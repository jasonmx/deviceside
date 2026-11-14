# Zigbee2MQTT internals

How the Zigbee2MQTT adapters address a target, what they send and read back, and what was measured on real hardware.
What a user sees is in [index.md](index.md) and isn't repeated here; what every integration shares is in
[internals.md](../../internals.md). Result and detail codes are in the code. Code comments cite the sections here.

## Addressing

Only devices Z2M registered through MQTT discovery are found: the device carries the identifier
`("mqtt", "zigbee2mqtt_<address>")`.

- **Devices** use the IEEE address: topic `zigbee2mqtt/<ieee>/set`. Z2M's `resolveEntity` matches the IEEE-keyed
  `devices` map before `friendly_name`, so a rename doesn't break it.
- **Groups** have address `<encoded base topic>_<group ID>`, the base topic as character codes with no separator
  (`1221051039810110150109113116116` for `zigbee2mqtt`; `getEncodedBaseTopic` in Z2M's
  `lib/extension/homeassistant.ts`), and topic `zigbee2mqtt/<group ID>/set`. `resolveEntity` falls back to the
  ID-keyed `groups` map, so a rename doesn't break it either, but a device whose friendly name is that number wins.
  Members come from `zigbee2mqtt/bridge/groups`, features from `zigbee2mqtt/bridge/devices`, and each entity's
  options from Z2M's settings (`bridge/info`). All three are held for the entry's life (`bridge_cache.py`):
  subscribed once MQTT is up, kept as Z2M republishes them (devices and groups on a device or group change, settings
  on every settings change and on start; Tested behavior), and read fresh only while nothing has arrived since the
  last connect. Options DeviceSide sets are written through to the held settings once Z2M confirms them, until its
  next `bridge/info`.
- **Both assume the default base topic.** A group's identifier carries it, so another is refused; a device's
  doesn't, so its publish goes nowhere.

## Modules

One section per adapter module, named after it.

### `classes`

The class rules read the registry device (Z2M's vendor and definition) and, for a device the bridge cache's
`bridge/devices` lists, its Zigbee `manufacturer` and `model_id` there, indexed by IEEE once per payload (Tested
behavior: the registry holds no Zigbee strings). The rules, their order and the registry fallback's limit are in the
`classes.py` docstring.

Which adapters send a Level command to a light that is off, as a Sengled E11-G13 jumps to full on `MoveToLevel`
while off on ZHA ([ZHA internals](../zha/internals.md), Tested behavior). Read from the code, 2026-11-14; nothing
probed on Z2M:

- `set_attributes`: `MoveToLevel` with `ExecuteIfOff`, then its kept-level publish,
  `{"state": null, "brightness", "transition"}`, a `MoveToLevel` without it. The E11-G13 jumped on both on ZHA.
- `raise_brightness` from off: `{"state": "ON", "brightness": 1, "transition": 0}`, which Z2M sends as
  `moveToLevelWithOnOff`, then the fade, the same.
- `turn_on_for` with brightness: Home Assistant's `light.turn_on`, `{"state": "ON", "brightness"}`, also
  `moveToLevelWithOnOff`.
- `lower_brightness`, the hue and saturation ramps and `stop` send no Level command to an off light.

`moveToLevelWithOnOff` from off is untested on the Sengled. Every `sengled` scope entry is `experimental(...)`, or
left out on `turn_on_for`, until the Sengled lab run.

### `color_modes`

The Home Assistant override is set to `xy`, `hs` and, where present, `color_temp`, on lights
exposing both `color_xy` and `color_hs`, and on groups whose members all qualify. Z2M re-announces at once and
Home Assistant keeps the entity. Rerun at setup and on every device or group announcement, reading Z2M's settings
each time. Z2M can't delete an override (`null` strips the key from the announcement instead), so opting out
writes back the modes Z2M announces by itself, wherever the value is still DeviceSide's. Z2M declined to
announce both by default, as some lights handle mixed modes badly
([#33025](https://github.com/Koenkk/zigbee2mqtt/issues/33025)).

### `native_control`

`hue_native_control` makes Z2M send on, brightness, color and color temperature as one Philips command
rather than ZCL ones. Each action that needs it one way declares it (`NEED` in `native_control.py`): on for a
`turn_on_for` with a color or color temperature (a Hue bulb, manufacturer `Philips`, lights in the new color and
keeps the timer); off for a brightness fade (native, a long raise runs about 2.3x faster than its transition, and
a soft off reports `onOff: 0` at once). Other actions, a saturation fade included, leave it alone. Where the
setting differs, it's set (Z2M's settings read on each call, up to 5 s for Z2M), the command published, and the
user's value written back after Z2M's next state publish for the target (1 s cap). The action returns once its
command is out; the write-back runs after it, and a `turn_on_for` waits for any pending one on its target before
its timer, its own or an earlier action's. Z2M reads a command's options as it takes it
(`lib/extension/publish.ts`), so a fade already sent runs on unchanged. One lock per device or group holds from the
set to the restore, so the next action that sets it waits for the write-back. A group uses its own options, not its
members', so it's set on the group when any member is a Hue bulb. A failed set or restore logs a warning, and the
command goes out anyway.

### `restore_journal`

The journal (`restore_journal.py`, HA storage `deviceside.zigbee2mqtt_restore_journal`) keeps the
user's value owed, by device or group, from just before the set's request goes out until Z2M confirms it written
back, so a restart or a failed restore can't leave the option flipped for good. An earlier entry is never
replaced: it holds the user's own value, where a later set may start from a flipped one. A set Z2M refused strikes
its entry. Every write-back writes the owed value, not the opposite of the action's need, and an action on an
address with a value still owed writes it back after its own publish even when its need already matched.
Each time `bridge/state` says online (its retained message at startup included, and after a Z2M that was offline
comes back), the replay pays what's owed: for each entry, under the same per-address lock the actions take, it
writes the value only if the option still holds the flipped one (one the user set back in Z2M's UI is left), and
strikes it once Z2M has it. An entry an action paid while the replay waited for its lock is skipped. One replay
runs at a time; an online that arrives during it runs it once more after, since its failures may date from Z2M
going down. An entry that fails again stays owed, for the next online, the next start or the next action on the
address. A malformed entry on disk is dropped with a warning; the rest replay.

### `read`

`filtered_cache` gets `deviceside_read` appended. Z2M deep-merges a read's answer into the cached state that
every later message carries, so an unfiltered answer would look fresh forever. If it can't be set, the read
raises.

### `turn_on_for`

Publishes `{"state": "ON", "on_time": <s>}`; Z2M sends `OnWithTimedOff` (`on_time` to 0.1 s; max 6553.4 s). With
fields, `light.turn_on` goes first, and the timer waits for Z2M's state publish on `zigbee2mqtt/<friendly name>`
(up to 5 s). Sent straight after, Z2M would publish the light's state from before the fields landed, which Home
Assistant keeps, and the two would contend on the mesh, delaying the timer by seconds.

Z2M publishes no state for a timed command (the `on_time` branch of `on_off` in zigbee-herdsman-converters returns
none) and turns reporting off for Hue bulbs. So DeviceSide publishes `{"state": ""}` to the get topic 1 s after
the start (without fields only), and at the end + 1 s and end + max(5 s, 2 % of the duration), for a slow bulb
clock. A new call replaces pending reads. Attribute reporting isn't used instead: it would need setting up on every
bulb, and Z2M turned it off for Hue on purpose. A group gets one publish and one groupcast read; Z2M shows it off
once every member is.

### `set_attributes`

Z2M's generic `command` payload, one per field. A color goes as xy, or hue and saturation on a light without xy.
Z2M publishes no state after a raw command, so each written cluster is read back live from each light, which also
updates Z2M's and Home Assistant's state. Level and color temperature are compared (`not_stored` if they differ);
color isn't, as the bulb clamps it to its gamut. A group is one groupcast per field, then every member is read.
`min_brightness`/`max_brightness` alone clamp each member from its own level, so each gets its own commands; an off
light is clamped from the brightness Z2M holds.

### `get_attributes`

Z2M's generic read, `{"read": {"cluster", "attributes", "state_property": "deviceside_read"}}`, one cluster at a
time, answered on the state topic:

- `genOnOff` (`onOff`, `onTime` in tenths): the timer.
- `genLevelCtrl` (`currentLevel`), only when the light is on. An off light's brightness is Z2M's, from the state the
  `genOnOff` answer carries: after an off with a transition the bulb holds 0.4 % while Z2M keeps the level it
  restores, a Hue bulb's own fade dims it for about a second after a plain off, and reading it would overwrite
  Z2M's kept level.
- `lightingColorCtrl`: `colorMode`, then only the attributes of the light's own modes, since one missing attribute
  fails the whole read.

A failed read raises at once when Z2M logs it on `zigbee2mqtt/bridge/logging`, else after 5 s. A group reads its
members in turn: averages over the lit ones (all, when all are off), and a `timer` only when every lit member has
one, the longest.

### `fade`

- `raise_brightness`: `{"state": "ON", "brightness", "transition"}`. An off bulb keeps its level and would fade up
  from it, so it first gets `{"state": "ON", "brightness": 1, "transition": 0}` (one publish to an all-off group,
  else per off member).
- `lower_brightness` to 0 %: `{"state": "OFF", "transition"}`, Z2M's soft off.
- Saturation: `{"color": {"hue", "saturation"}, "transition"}`; the bulb holds its hue with `hue_native_control` on
  (it fades in xy) or off.
- Hue: `hue_move` at `254 / sweep_time` steps per second (ZCL allows 1 to 254).

### `stop`

`stop`: `brightness_move: stop`, `hue_move: stop` for a running hue cycle, and a raw `stopMoveStep` for a
saturation fade, since `saturation_move: stop` halts the attribute and lets the xy fade drift on. Each is its own
publish, color first: Z2M reads back about 0.7 s after a brightness stop before sending the next key.

A soft off of ours is halted by `brightness_move: stop` alone: `lower_brightness` turns `hue_native_control` off for
its publish (`native_control`), so the soft off runs as a ZCL transition, which a ZCL stop ends. Z2M reports `OFF`
from the start of a soft off, so HA shows the light off until Z2M's read-back after the stop. Lab probe, LTA009 on
Z2M 2.14.3, a 10 s soft off from full stopped 3.2 s in: the bulb held `currentLevel` 175 for 4 s on the stop alone,
where the linear prediction was 172; HA showed `on` 0.59 s after the stop, at Z2M's stale 254 for 40 ms, then 175.
The pin this replaced (on at the predicted brightness) made `on` immediate but jumped the bulb to the prediction, a
flicker seen by eye.

- **Rare case:** the `hue_native_control` set fails or times out (MQTT or Z2M slow or down; `native_control` logs
  "Couldn't set Zigbee2MQTT's hue_native_control to ..." and sends the command anyway). The soft off then goes out as
  Philips' own command, which `brightness_move: stop` doesn't end, so the light fades on to off after a stop.
- **How often:** no field data (no telemetry). 85 lab rig logs, 2026-11-08 to 13, on one Z2M 2.14.3, hold no
  set-failed and no restore-failed warning (172 sets visible, in the debug-logged runs only).
- **Fix if it bites:** restore the pin, `{"state": "ON", "brightness", "transition": 0}` at the predicted brightness
  after the stop, but only for a soft off whose native-control set failed (`native_control` reports it; the stop
  picks those lights out). `6710d79` pinned every soft off, from when the stop didn't end the Philips command;
  #302 made native control per action, and #407 dropped the pin.

### `set_check`

Z2M reports a set it couldn't send only on `bridge/logging`, at error level:
`Publish 'set' '<key>' to '<friendly name>' failed: '<error>'`, then goes on to the message's next key and publishes
the message's state after the last (`lib/extension/publish.ts`). So a key's failure line comes before its message's
state. raise, lower, stop and turn_on_for settle after their publishes until each entity published to has a state
publish or a failure line, else 2 s past its last publish; a failure line makes the target `rejected`, with the status
it names (`busy`) or `send_failed`. set_attributes checks without the settle. The settle never delays a publish.
`hue_move`, a raw `command` and `on_time` draw no state, so a publish of one waits only 0.25 s, a groupcast's BUSY:
waiting the 2 s answered raise_hue 2 s late, and a script that waited, then stopped, ran the hue 2 s on (rig run
2026-11-13: +92° on a 3 s raise, expected +54°). A unicast's BUSY on them answers `sent`.

### `color_refresh`

Z2M publishes a fade's end value at once and reads back within a second of a brightness or hue stop. Once a fade has
run, DeviceSide gets `{"state": "", "brightness": ""}`, in case a mid-fade `get_attributes` overwrote the end
value. A saturation stop is raw, and after an xy stop Z2M's read-back carries its cached end-point x/y, so Device
Side gets `{"color": ""}` 1 s after a saturation stop and at the end of a saturation fade. Z2M reads a group's stop
back from its first member only, so these reads go to each member. After a hue or saturation command the bulb is in
hs mode; Home Assistant accepts that only with the color-modes option on.

## Tested behavior

Measured 2026-10-28 on Z2M 2.14.2 with Hue bulbs (a CT bulb and the A60), Home Assistant 2026.10.2.

- **Color-mode override.** It deep-merges into the device's options, and Z2M republishes the discovery message at
  once, with no restart; Home Assistant's entity keeps its entity ID and shows `color_temp`, `hs` and `xy`. A
  group's options behave the same. A `null` can't remove it: `supported_color_modes: null` deletes the key from the
  announcement, `light: null` drops the light from discovery, and `homeassistant: null` the whole device.
- **Stops, by eye** (a 10 s fade halted 3 s in). Brightness and hue stops freeze cleanly. Every color temperature
  stop jumps, whichever command halts it, the Hue bridge's own included. With brightness and color temperature in
  one fade, a brightness stop leaves the color temperature fading on to its end. `saturation_move: stop` lets an xy
  fade drift on for about 3 s; a raw `stopMoveStep` holds it.
- **bridge/info is republished on every settings change** (measured 2026-11-10 on Z2M 2.14.3, Home Assistant
  2026.10.4), to a subscriber that stays subscribed: after an options request over MQTT, for a device and for a
  group; after a change in Z2M's frontend; and after a Z2M restart, 0.02 s after `bridge/state` goes online, with the
  values kept. On an options request it arrives 0.03 to 0.10 s after the request and microseconds before the reply
  (seven group flips). The reply's `data.to` holds the entity's options after the write, without `friendly_name`.
- **After an xy stop,** Z2M's read-back carries its cached end-point x/y, not where the bulb halted; a
  `{"color": ""}` get publishes the halted x/y.

Device strings and timers, 2026-11-14 on Z2M 2.14.3 (ZHC 26.125.0), Home Assistant 2026.10.4. Probe:
[research/2026-11-14-lab-probe.md](research/2026-11-14-lab-probe.md).

- **The registry holds Z2M's definition, not the Zigbee strings.** A TS011F plug registers as manufacturer `Tuya`,
  model `Smart plug (with power monitoring)`, `model_id` `TS011F_plug_1`; its Zigbee model `TS011F` and manufacturer
  `_TZ3000_…` are only in `bridge/devices`, with the `countdown` expose. A Sengled E11-G13 registers as `Sengled`,
  `Element classic (A19)`, `model_id` `E11-G13`, and `sw_version` is Z2M's own version. A group registers as
  `Zigbee2MQTT` / `Group`, and its members each resolve to a device.
- **`TS011F_plug_1` has no `on_time` converter:** `{"state": "ON", "on_time": 10}` logs `No converter available for
  'on_time'` and turns the plug on with no timer. Tuya's own recipe and `countdown` work: shared
  [internals.md](../../internals.md), Tested behavior.
- **A Sengled E11-G13 ignores `0x42` silently** (ZHA rejects it, [ZHA internals](../zha/internals.md)): in tenths
  or whole seconds, from off or after a plain On, Z2M logs it invoked and the bulb doesn't change state. It has no
  `onTime` or `offWaitTime` (`UNSUPPORTED_ATTRIBUTE`). So `{"state": "ON", "on_time": 10}`, which the light
  converter sends as `0x42` alone, leaves it off.

## Upstream defects

Workarounds the adapters carry for behavior upstream that could be fixed there. Only the issues our code already
cites have been checked upstream: search each tracker for duplicates before filing.

- **zigbee-herdsman-converters: `saturation_move: stop` doesn't halt a hue/saturation `color` fade; it drifts on
  for about 3 s.** The stop key doesn't match the command that started the fade (`hue_move: stop` does halt a
  hue cycle cleanly). The converter should send `stopMoveStep` (0x47), which halts every color move. Workaround:
  a raw `stopMoveStep` (`integrations/zigbee2mqtt/stop.py`). A one-line converter fix with a by-eye repro; the
  Hue bulbs honoring it is observed, not guaranteed by ZCL. Whether it needs `hue_native_control` is untested.
- **zigbee-herdsman-converters: `on_off` with `on_time` publishes no state.** The `on_time` branch returns
  nothing, unlike the others. Workaround: scheduled `{"state": ""}` reads at start + 1 s, end + 1 s and end +
  max(5 s, 2 %). Fix: return `{state: "ON"}` as the plain branch does.
- **Zigbee2MQTT: after an xy stop, the read-back publishes the cached end-point x/y,** not where the bulb halted.
  Workaround: a `{"color": ""}` get 1 s after a saturation stop. Fix: re-read `currentX`/`currentY` (or drop the
  optimistic value) on a color stop.
- **Zigbee2MQTT: a group stop is read back from its first member only;** the others keep the optimistic end
  value. Workaround: a read per member. Per-member reads on large groups cost mesh traffic, so expect discussion.
- **Zigbee2MQTT: a `read` answer with `state_property` is deep-merged into the cached state forever,** so every
  later state message carries it. Workaround: `deviceside_read` appended to each device's `filtered_cache`.
  Fix: a non-persisting read, or `state_property` keys kept out of the cache. They may answer "use
  `filtered_cache`".
- **zigbee-herdsman (Ember adapter): a groupcast the coordinator refuses with BUSY isn't retried.**
  `sendZclFrameToGroup` makes one `ezsp.send` (multicast) and throws on any status but success, while a unicast
  retries BUSY up to `QUEUE_MAX_SEND_ATTEMPTS` (3), 500 ms apart (`src/adapter/ember/adapter/emberAdapter.ts`).
  Seen on the house rig 2026-11-11: Z2M logged `[ZCL GROUP groupId=<id>] Failed to send with status=BUSY.` about
  20 ms after a group `brightness` set, and the group never moved. Workaround: none for the send; `set_check` reports
  the target failed. Fix: retry a group send on BUSY as a unicast is. Not yet filed.
- **Zigbee2MQTT: announce both xy and hs.** Declined in #33025, hence the `homeassistant.light` override
  (`integrations/zigbee2mqtt/color_modes.py`). The remaining angle is narrower: opt-in per device, or only for
  Signify bulbs known to handle mixed modes.
- **Zigbee2MQTT discovery identifiers.** A device's identifier lacks the base topic and a group's encodes it as
  unseparated character codes; a device whose friendly name is a bare number shadows the group with that ID in
  `resolveEntity`. Real but edge cases.
- **zigbee-herdsman-converters (candidate, not probed): `brightness_move: stop` leaves a color temperature fade
  running to its end** when brightness and color temperature fade together. Possibly the same gap as the `saturation_move: stop` one above,
  fixed by a `stopMoveStep`; untested. Probe it on a Hue CT bulb before filing, and file it separately: a color
  temperature stop that jumps whichever command halts it is a different problem.
