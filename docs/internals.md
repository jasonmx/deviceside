# Internals

What every adapter shares: how a target is resolved, how fades and writes while off work, and the levels Home
Assistant keeps. Each integration's addressing, calls and measurements are in its own `internals.md`:
[Shelly](integrations/shelly/internals.md), [Zigbee2MQTT](integrations/zigbee2mqtt/internals.md),
[ZHA](integrations/zha/internals.md), [Matter](integrations/matter/internals.md) and
[Hue](integrations/hue/internals.md). What a user sees is in [integrations.md](integrations.md) and isn't
repeated here; result and detail codes are in the code.

## Shared

- **Resolution.** An entity belongs to an integration by its entity registry platform (Z2M: by its device
  identifier, below). A `switch_as_x` light is served as the switch it wraps, keyed on the light's entity ID; one
  wrapping another helper is `not_addressable`.
- **Fades.** Brightness and saturation are one command to the end point (the limit, or the end of the range) with a
  transition of `sweep_time` × the fraction of the range left; the device runs it. A brightness and a saturation
  fade can overlap. Saturation is Home Assistant's `hs_color` saturation, written as the light's current hue at the
  end saturation, so one automation behaves alike everywhere. Each raise, lower and stop is remembered for its
  duration plus a settle time (Zigbee 2 s, Hue 15 s) and its position predicted meanwhile; a light or scene call
  from Home Assistant to the light, its group or `all` drops that at once (`call_resolver`).
- **Writes while off.** `set_attributes` never sends `on`. On Zigbee it sends the Level and Color `MoveTo…`
  commands (never `…WithOnOff`) with the ZCL `ExecuteIfOff` override (options mask and override 1) and a 0.4 s
  transition, Hue's default. Given a color and a color temperature, only the color is sent.
- **Kept levels.** After an off with a transition, Z2M and Home Assistant (for ZHA and Hue) keep the old level and
  resend it on the next plain on, undoing a level written while off. Each `set_attributes` level write also
  replaces that kept level: on Z2M by publishing `{"state": null, "brightness": <level>, "transition": 0.4}`, which
  the off light ignores; on ZHA and Hue on the light entity, and on the group or room entity for an explicit
  `brightness` there.

## Tested behavior

- **The Tuya TS011F and `OnWithTimedOff` (`0x42`)**, measured 2026-11-14 on two TS011F plugs, `_TZ3000_0zfrhq4i`
  on Zigbee2MQTT and `_TZ3000_typdpbpg` on ZHA (Home Assistant 2026.10.4, Z2M 2.14.3). Each firmware was probed on
  one network only, so where they differ the cause is the firmware or the network's path, not proven firmware; other
  `_TZ3000_…` plugs may differ again.
  - **Tuya's recipe works on both:** a plain On, then `0x42` with `on_time` and `off_wait_time` both the duration
    in whole seconds. The plug went off by itself 10 s later. Z2M's own `countdown` does the same.
  - **The ZCL call, tenths from off, fails on both, differently.** `_TZ3000_0zfrhq4i` stores `on_time` and stays
    off. `_TZ3000_typdpbpg` copies it into both fields, counts down in seconds while off and switches on at 0: a
    10 s request turned it on 106 s later and left it on.
  - Whole seconds from off (`_TZ3000_0zfrhq4i`) does nothing: the timer needs the plug on first.

  So the TS011F runs a timer, but only Tuya's way. Probe:
  [lab-probe](integrations/zigbee2mqtt/research/2026-11-14-lab-probe.md), §2.
