# Zigbee2MQTT

What DeviceSide's features do on Zigbee2MQTT, their limits, and what DeviceSide changes in your setup. What applies
to every integration is in [integrations.md](../../integrations.md); which devices each feature is proven on is
on the [device compatibility page](../../device-compatibility.md); how the calls work, and what was measured, is
in [internals.md](internals.md).

Lights that Zigbee2MQTT announced to Home Assistant through MQTT discovery.

## Device classes

Zigbee devices given the same command can answer it differently, by brand and firmware
([research](research/2026-11-14-timed-on-call-patterns.md)), so a test on one brand proves nothing about another.
DeviceSide tells them apart by the manufacturer and model Zigbee2MQTT registers in Home Assistant and, for Tuya
devices sold under other brands, by the device's own Zigbee manufacturer and model, which Zigbee2MQTT publishes as
it connects. Every feature says how well it is proven for each
([levels of proof](../../device-compatibility.md#levels-of-proof)).

- **Hue bulbs** (manufacturer `Philips`) are tested: every feature on this page has passed on them.
- A **group** is proven as far as its weakest light. One with a light a feature refuses, or, with Experimental
  features off, one of another brand, falls back to its lights, each on its own; with them on, it runs as one group.
  A member Home Assistant has no light for (a plug Zigbee2MQTT shows as a switch, say) counts by its device, so a Tuya
  plug in a group makes it fall back too. Right after Home Assistant connects to MQTT, until Zigbee2MQTT republishes
  its groups, which it does as the connection comes up, DeviceSide can't tell a group's members: the group counts as
  Experimental, and turn on with timer refuses it (`class_unknown`), as a Tuya plug in it would stay on. If DeviceSide
  can't subscribe to Zigbee2MQTT's bridge at all, it logs a warning, groups run only with Experimental features on,
  and, until the integration reloads, turn on with timer refuses every group and every light, plug or switch but
  Hue's (IKEA's included), as it can't tell which is a rebadged Tuya device.

### Tuya

Tuya plugs, switches and lights, including those sold under other brands (Lidl, Moes, BlitzWolf and more). Most run
Tuya's own countdown instead of the Zigbee timer, and Zigbee2MQTT turns DeviceSide's timer into a plain on, so turn
on with timer doesn't work on them: the device would turn on and stay on
([research](research/2026-11-14-timed-on-call-patterns.md)). DeviceSide refuses it, even with Experimental features
on, until it builds Tuya's countdown. Their other features are untested, so they run only with Experimental features
on.

#### Tuya plugs and switches

Tuya devices on the standard Zigbee on/off commands: plugs, switches and relays (models `TS0001` to `TS0004`, and white
labels). Turn on with timer doesn't work, as above; on the [Tuya TS011F](../../device-compatibility.md#tuya-ts011f)
plug it is tested.

#### Tuya datapoint devices

Tuya devices that speak only Tuya's own protocol (model `TS0601`). Turn on with timer doesn't work, as above, and they
take no standard Zigbee reads or writes, so get attributes and set attributes aren't supported on them either.

Until Zigbee2MQTT publishes its devices after Home Assistant connects to MQTT, or while it is still pairing one,
DeviceSide knows a rebadged Tuya device only by what Home Assistant shows: the vendor `Tuya`, or a model starting
`TS`. One shown under another brand, as another brand's device (Lidl's, or Gledopto's GL-SPI-206P, which runs Tuya's
own protocol), can't be told apart from that brand's own devices for those first moments. So turn on with timer
refuses every light, plug or switch but a Hue one (`class_unknown`), even with Experimental features on, until
Zigbee2MQTT lists it as fully paired (interviewed), with its own Zigbee manufacturer and model.

### Sengled

Sengled bulbs ignore the Zigbee timer command, so turn on with timer is not yet supported on them: the bulb would stay
off ([probe](research/2026-11-14-lab-probe.md)). Their other features are untested, so they run only with
Experimental features on. A Sengled E11-G13 written while off jumps to full brightness on ZHA
([ZHA internals](../zha/internals.md)); set attributes on Zigbee2MQTT waits for a test on one.

### Gledopto

Gledopto's LED controllers and bulbs. Zigbee2MQTT sends some of them a plain on, a pause, then the brightness, rather
than the standard command a Hue bulb gets ([research](research/2026-11-14-timed-on-call-patterns.md)), so every
feature on them is untested and runs only with Experimental features on.

### Other brands

Lights, plugs and switches of every other brand, such as IKEA, Innr or Ledvance. Dimming smoothly (raise and lower
brightness) and stop send them exactly the standard Zigbee commands a Hue bulb gets, which no known brand handles
differently, so they should work. Their other features are untested, so they run only with Experimental features on
([try it](../../device-compatibility.md#try-it-experimental-features)). Brands differ most on turn on with timer:
some turn on and never off, or ignore the timer. If you try one, please report it whether it works or not:
Integrations > [DeviceSide](https://my.home-assistant.io/redirect/integration/?domain=deviceside) > Configure > Report
a device test result or bug. A brand with a passing report gets its own entry here.

## What DeviceSide changes in Zigbee2MQTT

DeviceSide sets three Zigbee2MQTT options:

- **Fix colors of full-color Zigbee2MQTT lights** (on by default). Home Assistant ignores Zigbee2MQTT's color
  updates for full-color bulbs in hue-saturation (HS) mode: Zigbee2MQTT announces only one of the light's two color
  modes, usually XY, so Home Assistant keeps showing the old color after a hue or saturation change, a Hue gradient
  scene, a remote, or a power cut ([Koenkk/zigbee2mqtt#28757](https://github.com/Koenkk/zigbee2mqtt/issues/28757)).
  DeviceSide sets the Home Assistant override of every full-color light and group to announce both XY and HS, plus
  color temperature where the light has it. The bulb itself is untouched. Lights with one color model, lights on an
  endpoint (`light_l1`), and lights with an override of your own are left alone. Turning the option off in
  DeviceSide's options puts back the modes Zigbee2MQTT announced itself.
- **`hue_native_control`**, on a Hue bulb, or a group with one, for one command at a time, then put back as you had
  it. A `turn_on_for` with a color goes out with it on: without it, a Hue bulb that is off drops
  the color and lights at its last one first. A brightness move goes out with it off: with it, a Hue bulb runs a
  long fade faster than asked, and reports off at once during a fade to off. DeviceSide sets the option, sends the
  command, and puts your setting back once Zigbee2MQTT answers, within a second. Every other command, DeviceSide's
  or not, goes out as you set it. With the option on, a plain `light.turn_on` is the bulb's own short fade (about
  0.4 s), and a `stop` sent during it halts it there, as it halts any fade the bulb runs.
- **`filtered_cache`**, before the first read of a device, so a read's answer isn't resent in every later state
  message. Your own entries are kept. Removing DeviceSide's is harmless: it comes back at the next read.

## Failed sends

When Zigbee2MQTT logs that it couldn't send a command (a busy coordinator refusing a group command, say), raise,
lower, stop, turn on with timer and set attributes report that light or group failed, with the reason Zigbee2MQTT
gave (`busy`). These actions answer up to 2 s later than before while DeviceSide listens for that line; a command
Zigbee2MQTT answers with the light's state ends the wait at once.

## Turn on with timer

With light fields, DeviceSide calls `light.turn_on` first, then starts the timer once Zigbee2MQTT reports the
light's new state, usually within a fraction of a second. If that takes over 5 s, the light stays on up to 5 s
longer than asked.

The result is `sent`: Zigbee2MQTT doesn't confirm a timer, or report the light's state after one. Device
Side reads the state back after the light turns on and when the timer ends, so Home Assistant stays right. A
restart during a timer loses those reads, and the state can stay stale until the light next reports.

Color bulbs of other brands have no equivalent of Hue native control, so one given a color from off may show its
last color for a moment. With native control on, a Hue bulb may report its old color for the first seconds of a
timer.

**Cancelling.** `turn_off` cancels the timer.

## Smooth color changes

- **Hue moves** cycle the color wheel until `stop`, with a `sweep_time` of 1 to 254 s. Saturation and hue need a
  light that is on and supports hue and saturation, or a group whose lights all do.
- **Saturation on a group** publishes one color to the group, at the group's hue in Home Assistant, and every light
  that is on moves to it. A group whose lights sit at different hues ends at one hue.

## Flash-free turn-on

`set_attributes` reads each light back, so the result is `confirmed`. A light that ignored the write fails the call
(`not_stored`).

## Get attributes

`get_attributes` reads each bulb live, since Hue bulbs on Zigbee2MQTT don't report and its cached state can be stale.
A group returns one entry, averaged over the lights that are on, as Hue averages a room.

## Sharp edges

- **Custom base topic.** Groups on a base topic other than `zigbee2mqtt` are refused (`not_addressable`). Devices
  aren't, but their commands reach nothing while the result still says `sent`.
- **A device named with a bare number** that matches a group's ID gets that group's commands.
- **Large groups.** A call has 30 s for all its targets. Reading a large group on a slow mesh can run out, failing
  every target with `timeout`. Target the lights by area or label instead, which reads four at a time.
