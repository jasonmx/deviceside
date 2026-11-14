# ZHA internals

How the ZHA adapters address a target, what they send and read back, and what was measured on real hardware.
What a user sees is in [index.md](index.md) and isn't repeated here; what every integration shares is in
[internals.md](../../internals.md). Result and detail codes are in the code. Code comments cite the sections here.

## Addressing

Commands go through `zha.issue_zigbee_cluster_command` and `zha.issue_zigbee_group_command` (admin-only; Device
Side passes the caller's context). The unique ID is `{ieee}-{endpoint_id}`; a longer one on the same endpoint (a
quirk's extra entity) is `not_addressable`, so a timer can't land on the wrong entity. A ZHA group entity
(`{platform}_zha_group_0x{group_id:04x}`) expands to its members for results; commands use the group where noted.

## Modules

One section per adapter module, named after it.

### `turn_on_for`

`OnWithTimedOff` (cluster `0x0006`, command `0x42`, `on_off_control: 0`, `off_wait_time: 0`), after
`light.turn_on` when there are fields. Then OnOff (`0x0000`) and OnTime (`0x4001`) are read in one uncached
request: `confirmed` when on and OnTime is above 0, at most the request and no more than 3 s under it.

A group gets one `light.turn_on` and one group `OnWithTimedOff`; per-member commands would start about 0.7 s apart
across 8 lights. A group command gets no reply, so every member is still read back. A slow end device (the rig's
Sengled) can read `not_on` before it takes the group's `light.turn_on` (Tested behavior). A member's read can also
overtake the group `OnWithTimedOff`: on the rig (2026-11-13, house run), the LTA004 read on with no timer straight
after a groupcast with brightness, then went off 6.57 s after coming on, where the cases before it went off at 6.0 to
6.1 s; the timer had landed about 0.5 s after the group's `light.turn_on`. So a member that reads `not_on` or no
timer is read again every 0.5 s, up to 3 times, before it is reported `sent`.

### `set_attributes`

Commands go one after another: `brightness` as a percent of level 254 (`0` sends level 1), color temperature as
mireds clamped to the light's range, color as `MoveToColor`, or `MoveToHueAndSaturation` on a light without xy. A
color light without color temperature takes one as a color, as core does. `min_brightness`/`max_brightness` read
CurrentLevel live. Then, once every light's commands are sent and outside the concurrency slots, each light at least
one command reached gets `homeassistant.update_entity`, since ZHA never applies a color temperature report; ZHA skips
it while the light is transitioning (1.5 s after a plain off), so DeviceSide waits up to 3 s, and after a longer
off fade refreshes again when it ends. Models in `zcl.DROPS_WHILE_OFF` are refused. The wait reads zha's private
`entity_data.entity` and its `is_transitioning`; a zha release without them is logged once, and the refresh then
goes ahead without waiting.

### `get_attributes`

Uncached reads, one cluster at a time: OnOff and OnTime always, Level when the light dims, Color when it has a color
mode. Color is the active mode (EnhancedColorMode where present, else ColorMode). The timer counts only while the
light reads on, as a TS011F counts OnTime down while off.

### `fade`

- `raise_brightness`: `MoveToLevelWithOnOff`. A light that is off first gets `MoveToLevel` to 1 with
  `ExecuteIfOff` and no transition. A Hue bulb ignores that and fades from its stored level, so it is turned on at
  level 1 by Signify's `multiColor` (cluster `0xFC03`, as Z2M's `hue_native_control` sends), straight through
  zigpy, as ZHA has no action for it; the same admin check applies.
- `lower_brightness`: `MoveToLevel` to a limit; to 0 %, `MoveToLevelWithOnOff`, which leaves the bulb at level 1.
  1 s after the fade should end, OnOff is read and an off light gets `MoveToLevel` back to its starting level with
  `ExecuteIfOff`. A stop or a new brightness call cancels that restore.
- Saturation: `MoveToColor` to the xy of each light's current hue at the end saturation. Group members at different
  hues get one command each, so none takes another's hue; members at one hue share a groupcast. Hue instead fades
  a room at the room's hue, as its `grouped_light` write carries one color.
- Hue: `EnhancedMoveHue` at `65535 / sweep_time` steps per second; a light without enhanced hue in
  ColorCapabilities (zigpy's cache, else one live read) gets `fields_dropped`, and a read that fails rejects the
  move with `error`.
- A hue move marks its lights as cycling before the send, so a cancel mid-send still leaves them to a later stop; the
  lights it failed for are unmarked after.

A transition longer than ZCL's 6553.4 s runs at that. The bulb reports brightness during a fade, never color, so
DeviceSide calls `homeassistant.update_entity` 1 s after a fade should end and straight after a stop; raw commands
leave ZHA's transition guard down, so the read is the bulb's.

ZHA can't tell a change from a remote or app from the bulb's own reports, so the remembered position can go stale.
To keep hold-to-dim instant, the brightness fade is sent first and CurrentLevel read just after; the read, timed at
the middle of its round trip, lies on the bulb's ramp, so DeviceSide fits the ramp's real start to it. A brightness
call the remembered value says is `at_limit` or `off` is replanned from Home Assistant's state.

A group gets one command when every light in it moves; otherwise, such as a soft off with a member off, each moving
member gets its own, since a group soft off would drop an off member's stored level. The bottom level and the
restore are always per member.

A failed command rejects only the lights it was for, each keeping the remembered position and soft off restore it
had; the rest fade on. A groupcast that fails rejects every member it was for. A member whose bottom level failed
gets no fade, and the rest get one command each, as a groupcast would reach it.

### `stop`

`stop`: `StopMoveStep` where a color move runs, then Level `Stop`, each sent per light (one groupcast where the
lights are a whole group). A light either command fails for is rejected (`admin_required`, else `error`) and keeps
its move, hue cycle and soft-off restore; the lights that halted record the halt and drop theirs.

## Tested behavior

Color fades, on a Philips Hue A60 (LCA016, firmware 1.164.2): `MoveToColor` runs linearly in the bulb's own hue and
saturation, not along a straight xy line, so a saturation fade toward white swings the hue (136° to 100° in 2.5 s of
a 9 s fade). `StopMoveStep` halts on that path, and the reads hold after it; the same on Zigbee2MQTT. Probe:
[research/2026-11-13-a60-color-fade-path.md](research/2026-11-13-a60-color-fade-path.md). Watched, the stop of that
saturation fade still shows as a jump in saturation, on ZHA and Zigbee2MQTT, though the reads have none: the bulb
renders the fade unlike the state it reports. Cause not yet known:
[research/2026-11-13-a60-stop-jump-watched.md](research/2026-11-13-a60-stop-jump-watched.md).

Timers, on a Philips Hue LTA004 (firmware `0x01001416`, Home Assistant 2026.10.2). Firmware decides this, so other
devices may differ.

- OnTime counts down in tenths (a 30 s timer read 290, 250, 200 and 99 at 1, 5, 10 and 20 s; off at 30.2 s) and
  reads 0 with no timer.
- A new timer replaces the old one, longer or shorter.
- `light.turn_on`, plain (`On`) or with brightness (`move_to_level_with_on_off`), leaves the timer running.
  `light.turn_off` clears it.
- Nothing ends a timer with the light left on: writing OnTime 0 (status `SUCCESS`), `OnWithTimedOff` with
  `on_time: 0`, or a 0.1 s timer each turned the bulb off within 0.3 s. Writing OnTime resets the time left (100
  with 15.6 s left: off 10 s later). With no timer running, writing 0 changes nothing.

Writes while off: the LTA004, LWA012, LTA009 and LCA016 store `MoveToLevel` and `MoveToColorTemp` with `ExecuteIfOff`, at any delay
0 to 2 s after a plain off, and drop them without it. A Sengled E11-G13 (firmware `0x00000009`) takes `MoveToLevel`
while on, but while off, with the override or without, jumps CurrentLevel to 255 and comes on at full.

Moves, on the LTA004: a Level `Stop` 3 s into a 6 s soft off from 200
held at 100 with the bulb on, and Home Assistant showed 100 from the bulb's report. A 4 s fade read its end level by
3.9 s; a 4 s soft off read off, at level 1, by 4.3 s. Group `MoveToLevelWithOnOff` and `Stop` act as the unicast
ones.

Groups (2026-10-27, the LTA004, a Sengled E11-G13 and a plug): a unicast `turn_on_for` takes about 0.23 s per member
(0.34 s with brightness), so under `max_concurrency` 2 the last of 8 lights starts 0.7 to 1 s after the first. A
group `OnWithTimedOff` returns in 0.02 s, zigpy's own success with no delivery feedback, and a member that refuses
the command (the Sengled, `UNSUP_CLUSTER_COMMAND` by unicast) misses it silently, so each member is read back.

Device strings, 2026-11-14 on Home Assistant 2026.10.4. Probe:
[lab-probe](../zigbee2mqtt/research/2026-11-14-lab-probe.md), §1.

- **The registry holds the raw Zigbee strings,** manufacturer and model, with `model_id` empty: a TS011F plug is
  `_TZ3000_typdpbpg` / `TS011F`, a Sengled `sengled` / `E11-G13`, Hue bulbs `Signify Netherlands B.V.` and their
  model ID (`LTA009`). `sw_version` is the Zigbee firmware in hex.
- **Beyond it, readable through `zcl.gateway`:** the TS011F has quirk `zhaquirks.tuya.ts011f_plug:Plug` (no
  `quirk_id`), `manufacturer_code` 4417, and the Tuya clusters `0xe000` and `0xe001` on endpoint 1. The Sengled
  (4448) and the Hue bulbs (4107) have no quirk.
- **A group's light entity resolves to the coordinator's device** (here `SONOFF` / `ZBDongle-E`); its members each
  resolve to their own.
- The TS011F's timer: shared [internals.md](../../internals.md), Tested behavior.

## Upstream defects

Workarounds the adapters carry for behavior upstream that could be fixed there. Only the issues our code already
cites have been checked upstream: search each tracker for duplicates before filing.

- **ZHA (zigpy/zha): color temperature attribute reports are never applied to the entity.** HA shows a stale
  color temperature after any change it didn't send. Workaround: `homeassistant.update_entity` after every write
  (`integrations/zha/set_attributes.py`) and uncached live reads in `get_attributes`. A plain state-sync bug,
  easy to reproduce: change the color temperature from a remote. Already tracked as
  [zigpy/zha#855](https://github.com/zigpy/zha/issues/855) (open; a maintainer agrees it should be fixed as
  brightness was, and a fix sits on the `zigpy-bot/light-color-reports` branch, no PR yet). Don't file a new issue.
- **zha-quirks: define Signify's `0xFC03` `multiColor` command on the Hue cluster,** so
  `zha.issue_zigbee_cluster_command` can send it. Workaround: straight to zigpy, re-implementing ZHA's admin
  check (`zcl.hue_turn_on`, `require_admin`). Additive quirk work; Z2M's `philips.ts` is prior art.
- **ZHA: `update_entity` does nothing while the transition flag is up,** and "on" reports are ignored for 1.5 s
  after `turn_off`. Workaround: poll the flag up to 3 s and refresh again after long fades
  (`TRANSITION_WAIT_S`, the late refresh). The guard is deliberate; pitch it narrowly: an explicit
  `update_entity` bypasses it.

Not worth filing:

- **Firmware:** the Sengled E11-G13 jumping to full. A vendor problem.
- **ZHA group commands give no delivery feedback.** That is how a Zigbee groupcast works.
