# Hue internals

How the Hue adapters address a target, what they send and read back, and what was measured on real hardware.
What a user sees is in [index.md](index.md) and isn't repeated here; what every integration shares is in
[internals.md](../../internals.md). Result and detail codes are in the code. Code comments cite the sections here.

## Addressing

Through the core integration's aiohue connection, CLIP v2 only. The unique ID is the bridge resource ID: a light, a
scene, or a room's or zone's `grouped_light` (`is_hue_group`). A room's lights are those the bridge lists that have
a light entity. Reads come from aiohue's copy of the bridge's state, kept current by the event stream.

- **No timer adapter.** The bridge's timers survive a plain on or off and can only be removed on the bridge, and a
  bulb without power when one fires misses it, with no retry.
- **No auto-off or auto-on adapter.** CLIP v2 stores neither on a light or `grouped_light`, and lacks the v1 rule
  engine that could build one, which Signify is retiring.

## Modules

One section per adapter module, named after it.

### `set_attributes`

`set_attributes` writes each light without `on`; a `grouped_light` write misses lights that are off. Colors
convert to xy; `rgb_color` wins over `hs_color` over `xy_color`. `min_brightness`/`max_brightness` move the
stored brightness. A `0` leaves `min_brightness`, `max_brightness` or `color_temp_kelvin` unset, as hue_dimmer did.

### `set_scene`

`set_scene` has no off-preserving recall in the Hue API, so it reads the scene from aiohue and writes each
action as a light write: `on: true` dropped, `on: false` kept, gradients, effects and dynamics skipped. The bridge
takes 3 requests at once and aiohue retries the rest.

### `get_attributes`

`get_attributes` leaves out `color_temp_kelvin` in color mode, as the bridge's value is stale. A room with a
light on uses the bridge's brightness and averages color over lit lights; with all off, over all of them.

### `fade`

One write of the end point with a duration; a room is one `grouped_light` write. Saturation uses the room's hue
(the mean xy of its lit color lights), each light clamped to its gamut by the bridge. `lower_brightness` to 0 % is
`on: false` with the duration, which keeps the stored brightness.

### `stop`

`stop` sends the dimming stop. The bridge has no xy stop, so a saturation fade is halted by writing its predicted xy
with no duration, in the same write (per light for a room). The bridge refuses a dimming stop during a soft off, so
DeviceSide's own soft off is cut with `on: true` at the predicted brightness and no duration.

### `report_filter`

Reports during a remembered move can't be told apart by value (Tested behavior), so every report of what the move
changed (brightness and on, or color) is taken as DeviceSide's, except a switch to off while fading to or halted at
a lit level, and a report of anything the move didn't change. Those end the prediction of what they report, and
only that: brightness and color fade independently, and a bulb can correct a color mid-fade (Tested behavior), so a
color report leaves a brightness fade's prediction, and the stale brightness the bulb reported with it, in place.
A bridge scene recall (`status.active`) and a Home Assistant call end the light's whole prediction. With
`predicted_state` on, the reports taken as DeviceSide's are hidden from aiohue and Home Assistant is sent the
prediction instead; a raise from off shows 0.4 %, where the fade starts.

## Tested behavior

Measured 2026-10-26 on a BSB002 bridge (software 6.1.1978418030) and two LTA009 bulbs (firmware 1.163.1), Home
Assistant 2026.10.2, aiohue 4.9.0, unless dated otherwise. Home Assistant shows each event as it arrives.

- **A transition reports its end value at once,** about 70 ms after the write (80 % reads 79.84), and nothing when it
  ends. A transition to the value the bridge holds sends nothing.
- **Where a stopped light halted is reported late or never:** 0.1 to 27 s after the stop, varying as if from the
  bulb; after 4 of 16 stops, nothing within 30 s. Reports that came were within 0.3 % brightness and 1 mirek of a
  straight line. Once (2026-10-27, a zone) a bulb reported the brightness from before the move: 56 % against a
  40 % halt.
- **A mid-transition report can come,** and be the last: about 1 s stale but on the path. Every stop report was
  within 0.4 % of the straight line.
- **Rooms and zones:** each light reports its end at once, the `grouped_light` the average about 1 s later; after a
  stop the lights report separately, up to 16 s apart.
- **A change from elsewhere is reported promptly:** a Home Assistant `light.turn_on` within 0.25 s, a Hue app
  slider about once a second.
- **Dimming to off** reports `on: false` (and brightness 0, if written) at once; in 1 of 2 runs a mid-fade report
  then showed it on at 17 %, with nothing after. A dimming stop is refused during it, but `on: true` with a
  brightness and no duration ends it there without a jump. `on: false` with a duration keeps the stored brightness;
  with brightness 0 in the write, the next on comes back at the minimum.
- **Brightness 0 left on** (`on: true`, 0, with a duration) ends on at `min_dim_level` (0.2 % on the LTA009);
  Home Assistant shows it on at 0. A dimming stop halts it.
- **Color temperature** reports only `color_temperature`, and its transitions and stop (`color_temperature_delta`)
  behave as brightness, on a light and a zone. Brightness and color temperature fade independently, in either
  order; one write of both reports two events; one write takes both stops (2026-10-27).
- **xy** (a gamut C bulb, 2026-11-03) behaves as brightness: the end at once, nothing after. An xy with no duration
  mid-fade halts it, reported at once. One write takes a dimming stop and an xy. An xy fade and a brightness fade
  0.5 s apart both run. A zone xy write changes only its color bulbs.
- **A brightness write never reports a color:** across the full Hue rig suite (2026-10-27), only `on` and `dimming`.
  But a bulb can report the color it really holds mid-fade, correcting the bridge (rig run 2026-11-11 08:58, a room
  of color and color temperature bulbs): a room was set to one color temperature, then one member to another,
  each reported at once; 25 s later, mid-way through a brightness-only room fade, that member reported a stale
  mid-fade brightness and, in the next event, the room's color temperature. It happened on both bulbs, in two cases.
- **A room member can be left stale** (2026-11-11, LTA009s): after a room fade, one light's last report can be a
  mid-fade brightness, with nothing after it for 40 s, while the bulb reaches the end. It happened in 2 of 6 fades, a
  different light each time. A Home Assistant `light.turn_on` of the room can leave one too: set to 60 %, a light
  last reported 47.43 %.
- **A bridge scene recall** sets `status.active` to `static` within about 1 s of its lights' reports. A light whose
  color the scene leaves alone reports only `on` and `dimming`, so only the scene's report shows the change came
  from elsewhere.
- **Moving a bulb between networks** (2026-10-31, the A60 on firmware 1.164.2): a CLIP v2 `DELETE device/<id>`
  sends it a Leave, as Z2M's and ZHA's removes do, and it joins the next network in about 10 s. On older firmware,
  a v1 light delete left it on no network, until a delete in the Hue app released it.

- **Device strings** (2026-11-14, Home Assistant 2026.10.4): Hue bulbs register as manufacturer `Signify
  Netherlands B.V.`, a product-name model (`Hue ambiance lamp`), the model ID in `model_id` (`LTA009`), and the
  firmware as `sw_version`. Their `product_data` has `certified` true and `product_archetype` `sultan_bulb` or
  `classic_bulb`. A room's or zone's light entity has its own device, `Signify Netherlands B.V.` / `Room` (or
  `Zone`), and a room's member devices each have theirs. A Sengled E11-G13 didn't pair with a `BSB002` on
  software `6.3.1978545030` in one attempt (2026-11-14). Probes:
  [lab-probe](../zigbee2mqtt/research/2026-11-14-lab-probe.md), §1; [rooms and the
  Sengled](research/2026-11-14-lab-probe-hue.md).

## Upstream defects

Workarounds the adapters carry for behavior upstream that could be fixed there. Only the issues our code already
cites have been checked upstream: search each tracker for duplicates before filing.

None open.

Not worth filing:

- **Hue bridge behavior:** a `grouped_light` write misses lights that are off, there is no xy stop, a dimming stop
  is refused during a soft off, and timers survive on and off. Signify firmware and API, not HA or aiohue.
- **The `predicted_state` report filter** edits aiohue's event dicts before its controllers run. A supported hook
  would be fair to ask aiohue for, but few others need one.
