# Integrations

What the [README](../README.md) features do on every integration, and what none can do. Each integration has its own
page, with its limits and what DeviceSide changes in your setup: [Shelly](integrations/shelly/index.md),
[Zigbee2MQTT](integrations/zigbee2mqtt/index.md), [ZHA](integrations/zha/index.md),
[Matter](integrations/matter/index.md) and [Hue](integrations/hue/index.md). Which devices each feature is proven on
is on the [device compatibility page](device-compatibility.md). How the commands work on the wire, and what was
measured on real bulbs, is in [internals.md](internals.md) and each integration's own.

## All integrations

- **Refused targets.** A target that can't take an action fails the call if you named it, and is skipped if it
  came through an area, device, label or group ([action-behavior.md](action-behavior.md)). The error names the
  reason, such as `not_supported`.
- **Groups.** For timers and smooth moves, Zigbee2MQTT and ZHA groups and Hue rooms and zones get one
  command, so their lights move together. `set_attributes` writes a ZHA group's or Hue room's lights one by one.
  Any other group, such as a light group helper, is expanded and each light gets its own command.
- **Switches shown as lights.** A light made from a supported switch with Home Assistant's "Change device type of a
  switch" helper is treated as that switch: light fields are dropped and the timer still runs. An inverted one is
  refused (`inverted_wrapper`).
- **No color temperature moves.** Every stop of a color temperature fade on a Hue bulb, the Hue bridge's own
  included, makes a visible jump, so there is no raise or lower for it.
- **Fades pick up where the last one stopped.** DeviceSide remembers each raise, lower and `stop` for a few
  seconds after it ends (2 s on Zigbee, 15 s on Hue), so releasing and pressing again doesn't jump. A light
  command from Home Assistant clears it at once; a change from a remote or app in that window may be missed.
- **Dimming to 0 % is a soft off.** The light fades out and keeps its level, so a plain turn on brings it back
  there. Raising brightness on a light that is off fades in from the bottom.

## What can't be done, and why

The README's prohibited signs mark these. Each is a limit of the device protocol or the bridge, not a feature
waiting to be built.

- **Turn off with timer on ZHA, Zigbee2MQTT and Matter.** The Zigbee and Matter timer command (`OnWithTimedOff`)
  turns a device on, then off. There is no command for off, then on.
- **Turn on or off with timer on Hue.** The bridge's own timers keep running through a plain on or off, from Home
  Assistant, the Hue app or a dimmer switch, and the Hue app doesn't show them. To time Hue bulbs, pair them with
  ZHA, Zigbee2MQTT or Matter.
- **Smooth hue change on Hue.** A trip around the color wheel isn't a straight line the bridge can fade along.
  Smooth saturation changes work.

## Zigbee plugs and switches

`turn_on_for` and `get_attributes` on Zigbee2MQTT and ZHA plugs and switches are untested
([device compatibility](device-compatibility.md#zigbee-plugs-and-switches)). Few honor the Zigbee timer command:
IKEA's are moving to Thread, and Sonoff and Tuya run their own countdowns (the Tuya TS011F's runs only Tuya's way:
it [doesn't work](device-compatibility.md#tuya-ts011f) with DeviceSide's timer).
If you try one, please report it whether it works or not: Integrations >
[DeviceSide](https://my.home-assistant.io/redirect/integration/?domain=deviceside) > Configure > Report a device test
result or bug, which prefills the device's details.
