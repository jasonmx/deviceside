# Device compatibility

Will DeviceSide work with your device? The [README's cards](../README.md#will-it-work-with-my-device) give the short
answer per integration. This page has the detail: what is known about each device and class of device, where that
comes from, and the evidence behind every claim.

## Levels of proof

Claims follow evidence, per class of device: devices that get the same command and run the same family of firmware,
such as Hue bulbs on Zigbee2MQTT. A test on one class says nothing about another, because makers' firmware answers the
same command differently. Each claim is at one of two levels of proof:

- **Verified:** a maintainer tested it on a real device, on the automated rig or by hand.
- **Documented:** no device of the class has been tested, but DeviceSide sends it the same call as a Verified device,
  differing only in addressing, and no known class of devices handles it differently.

New features ship as **Experimental** until a maintainer judges them ready for everyone: they stay off until you
turn on **Experimental features** in **Configure > Settings**, and so, for most features, does a device DeviceSide
can't place in a known class. Whether they work or not, please tell us with **Report a device test result or bug** in the same menu: your
reports help ship them to everyone. How we decide: [evidence rules](evidence.md).

## Maintainers

DeviceSide is run by volunteers. Each integration's maintainers, named on its card, look after it: they review changes
to it and fix bugs in it as they can. Please
[report a bug](https://github.com/DeviceSide/DeviceSide/issues/new?template=device_report.yml); the report goes to the
maintainers of the integration you choose. A report that a feature doesn't work is still useful: it is how features
get fixed or left out.

**⚠️ Unmaintained** marks a feature no one is fixing. It still works as it did, but a bug in it stays unfixed until
someone fixes it or takes over the integration, so use it at your own risk. **⚠️ Untested fix** marks a feature
changed in a release before anyone could test it on a device: please report how it works for you.

On the cards, ✔ marks what the automated rig verified; a model without it was verified by a maintainer by hand.

## Tested devices

A model works on the integrations and features its records and maintainers' hand tests (`_manual.md`) cover; the
[evidence](#evidence) lists them per action, with the firmware tested. Other models may work too, but makers implement
these features inconsistently: if yours isn't here, please try it and [report the
result](https://github.com/DeviceSide/DeviceSide/issues/new?template=device_report.yml), whether it works or not.

### Hue White Ambiance E27 1100lm

A color temperature bulb, rig-tested on Hue, ZHA and Zigbee2MQTT. It is `LTA009` on Hue and ZHA, and `9290024684` on
Zigbee2MQTT, which maps one to the other
([zigbee-herdsman-converters](https://github.com/Koenkk/zigbee-herdsman-converters/blob/master/src/devices/philips.ts)).
Smooth dimming is tested on all three, and turn on with timer on ZHA and Zigbee2MQTT (the Hue bridge has no usable
timer: see [integrations](integrations/hue/index.md)).

On a Hue bridge, a room fade can leave one of these bulbs last reported at a mid-fade brightness, with nothing after,
while the bulb reaches the end; and mid-fade, a bulb can report the color temperature it really holds, correcting the
bridge's earlier report (rig runs, 2026-11-11: Hue [Tested behavior](integrations/hue/internals.md#tested-behavior)).

### Hue Essential White and Color Ambiance A60

A color bulb, rig-tested on Hue, ZHA and Zigbee2MQTT: `LCA016` on Hue and ZHA, `8720169392182` on Zigbee2MQTT
([zigbee-herdsman-converters](https://github.com/Koenkk/zigbee-herdsman-converters/blob/master/src/devices/philips.ts)).
It carries the smooth saturation and hue tests, and Zigbee2MQTT's color mode override
([record](integrations/zigbee2mqtt/test-records/color_modes.md)). Turning that option off is unit-tested only: the rig can't
cleanly remove the override the option writes.

### Hue White A60 B22

A white (dimmable) bulb, rig-tested with turn on with timer and get attributes on ZHA and Zigbee2MQTT: `LWA012` on
ZHA, `9290018217` on Zigbee2MQTT
([zigbee-herdsman-converters](https://github.com/Koenkk/zigbee-herdsman-converters/blob/master/src/devices/philips.ts)).

### Native groups

A Hue room or zone, a ZHA group or a Zigbee2MQTT group, commanded as one, as the integration does it. The features a
group takes are in the README's feature table. A ZHA group command gives no delivery feedback, as is the way of a
Zigbee groupcast, and a Hue room write misses lights that are off, so DeviceSide writes a Hue room light by light when
it sets attributes ([integrations](integrations/hue/index.md)).

## Should work

Documented ([levels of proof](#levels-of-proof)).

## Try it: Experimental features

These are built and unit-tested, but not yet ready for everyone ([levels of proof](#levels-of-proof)); some may
have passed a maintainer's first tests. Turn on Experimental features to try them, then report the result, whether
they work or not: your report helps get them fixed or shipped to everyone.

### Zigbee plugs and switches

Turn on with timer and get attributes on a ZHA or Zigbee2MQTT `switch`. A plug works only if its firmware honors the
Zigbee timed-on command, and few do: Sonoff and Tuya plugs often run their own countdown instead. On Zigbee2MQTT,
DeviceSide refuses it on Tuya plugs, which would turn on and stay on; the [Tuya TS011F](#tuya-ts011f) runs only Tuya's
own timer. ZHA and Zigbee2MQTT each need their own report.

## Not yet supported

A class of device that needs a command DeviceSide hasn't built yet. DeviceSide refuses it, rather than send a command
that would leave it doing something you didn't ask for, and each card lists them. If you'd like one built,
[propose it](../CONTRIBUTING.md#how-to-help).

## Doesn't work

### Tuya TS011F

A Zigbee plug whose timer runs only Tuya's way, so DeviceSide's timer can leave it off, or turn it on long after the
time asked and leave it on. On Zigbee2MQTT, DeviceSide refuses turn on with timer on it until it builds Tuya's call.
ZHA doesn't refuse it yet, so don't give one a timer there. What was measured:
[internals](internals.md#tested-behavior).

## Evidence

Per integration, a row per action and level of proof its entities, classes and fields hold, with the models and
records behind the action (a rig record, or a maintainer's test by hand); ⚠️ Unmaintained and ⚠️ Untested fix mark an
action's caveat. A group is a native group of the integration (a Hue zone, a ZHA or Zigbee2MQTT group).

<!-- evidence:start -->
<!-- Generated by scripts/gen_compat.py from the bundles' records and devices.yaml, docs/models.yaml and the adapters' scopes. Do not edit; run the script. -->

### ZHA

| Action | Level | Items | Tested models | Records |
|---|---|---|---|---|
| `get_attributes` | Verified | light entities | `LTA009`, firmware `0x01002a00`<br>`LCA016`, firmware `0x01001200`<br>`LWA012`, firmware `0x01002502`<br>group | [record](integrations/zha/test-records/get_attributes.md) |
| `get_attributes` | Experimental | switch entities | | |
| `lower_brightness` | Verified | light entities; brightness fields | `LTA009`, firmware `0x01002a00`<br>group | [record](integrations/zha/test-records/lower_brightness.md) |
| `lower_hue` | Verified | light entities; hue fields | `LCA016`, firmware `0x01001200` | [record](integrations/zha/test-records/lower_hue.md) |
| `lower_saturation` | Verified | light entities; saturation fields | `LCA016`, firmware `0x01001200` | [record](integrations/zha/test-records/lower_saturation.md) |
| `raise_brightness` | Verified | light entities; brightness fields | `LTA009`, firmware `0x01002a00`<br>group | [record](integrations/zha/test-records/raise_brightness.md) |
| `raise_hue` | Verified | light entities; hue fields | `LCA016`, firmware `0x01001200` | [record](integrations/zha/test-records/raise_hue.md) |
| `raise_saturation` | Verified | light entities; saturation fields | `LCA016`, firmware `0x01001200` | [record](integrations/zha/test-records/raise_saturation.md) |
| `set_attributes` | Verified | light entities; brightness, color_temp_kelvin, hs_color, rgb_color, xy_color, min_brightness, max_brightness fields | `LCA016`, firmware `0x01001200` | [record](integrations/zha/test-records/set_attributes.md) |
| `stop` | Verified | light entities | `LTA009`, firmware `0x01002a00`<br>`LCA016`, firmware `0x01001200`<br>group | [record](integrations/zha/test-records/stop.md) |
| `turn_on_for` | Verified | light entities; brightness, color_temp_kelvin fields | `LWA012`, firmware `0x01002502`<br>`LTA009`, firmware `0x01002a00`<br>group | [record](integrations/zha/test-records/turn_on_for.md) |
| `turn_on_for` | Experimental | switch entities | | |

| Doesn't work | Why | Source |
|---|---|---|
| Tuya TS011F | Its timer isn't supported yet. DeviceSide's timer can leave it off, or turn it on long after the time asked and leave it on | [source](https://github.com/DeviceSide/DeviceSide/blob/main/docs/integrations/zigbee2mqtt/research/2026-11-14-lab-probe.md) |

### Zigbee2MQTT

| Action | Level | Items | Tested models | Records |
|---|---|---|---|---|
| `get_attributes` | Verified | light entities; hue classes | `9290024684`, firmware `1.163.1`<br>`8720169392182`, firmware `1.164.2`<br>`9290018217`, firmware `1.122.2`<br>group | [record](integrations/zigbee2mqtt/test-records/get_attributes.md) |
| `get_attributes` | Experimental | switch entities; other, tuya_zcl, tuya_ts011f, sengled, gledopto classes | | |
| `lower_brightness` | Verified | light entities; hue classes; brightness fields | `9290024684`, firmware `1.163.1`<br>group | [record](integrations/zigbee2mqtt/test-records/lower_brightness.md) |
| `lower_brightness` | Documented | other classes | | |
| `lower_brightness` | Experimental | tuya_zcl, tuya_ts011f, tuya_mcu, sengled, gledopto classes | | |
| `lower_hue` | Verified | light entities; hue classes; hue fields | `8720169392182`, firmware `1.164.2`<br>group | [record](integrations/zigbee2mqtt/test-records/lower_hue.md) |
| `lower_hue` | Experimental | other, tuya_zcl, tuya_ts011f, tuya_mcu, sengled, gledopto classes | | |
| `lower_saturation` | Verified | light entities; hue classes; saturation fields | `8720169392182`, firmware `1.164.2`<br>group | [record](integrations/zigbee2mqtt/test-records/lower_saturation.md) |
| `lower_saturation` | Experimental | other, tuya_zcl, tuya_ts011f, tuya_mcu, sengled, gledopto classes | | |
| `raise_brightness` | Verified | light entities; hue classes; brightness fields | `9290024684`, firmware `1.163.1`<br>group | [record](integrations/zigbee2mqtt/test-records/raise_brightness.md) |
| `raise_brightness` | Documented | other classes | | |
| `raise_brightness` | Experimental | tuya_zcl, tuya_ts011f, tuya_mcu, sengled, gledopto classes | | |
| `raise_hue` | Verified | light entities; hue classes; hue fields | `8720169392182`, firmware `1.164.2`<br>group | [record](integrations/zigbee2mqtt/test-records/raise_hue.md) |
| `raise_hue` | Experimental | other, tuya_zcl, tuya_ts011f, tuya_mcu, sengled, gledopto classes | | |
| `raise_saturation` | Verified | light entities; hue classes; saturation fields | `8720169392182`, firmware `1.164.2`<br>group | [record](integrations/zigbee2mqtt/test-records/raise_saturation.md) |
| `raise_saturation` | Experimental | other, tuya_zcl, tuya_ts011f, tuya_mcu, sengled, gledopto classes | | |
| `set_attributes` | Verified | light entities; hue classes; brightness, color_temp_kelvin, hs_color, rgb_color, xy_color, min_brightness, max_brightness fields | `9290024684`, firmware `1.163.1`<br>`8720169392182`, firmware `1.164.2`<br>group | [record](integrations/zigbee2mqtt/test-records/set_attributes.md) |
| `set_attributes` | Experimental | other, tuya_zcl, tuya_ts011f, sengled, gledopto classes | | |
| `stop` | Verified | light entities; hue classes | `9290024684`, firmware `1.163.1`<br>`8720169392182`, firmware `1.164.2`<br>group | [record](integrations/zigbee2mqtt/test-records/stop.md) |
| `stop` | Documented | other classes | | |
| `stop` | Experimental | tuya_zcl, tuya_ts011f, tuya_mcu, sengled, gledopto classes | | |
| `turn_on_for` | Verified | light entities; hue classes; brightness, color_temp_kelvin, hs_color, rgb_color, xy_color, transition fields | `LWA012`, firmware `1.104.2`<br>`LTA009`, firmware `1.163.1`<br>`LCA016`, firmware `1.164.2` | [record](integrations/zigbee2mqtt/test-records/turn_on_for.md) |
| `turn_on_for` | Experimental | switch entities; other, gledopto classes | | |

| Doesn't work | Why | Source |
|---|---|---|
| Tuya TS011F (`turn_on_for`) | Zigbee2MQTT turns it on with no timer, so it stays on | [source](https://github.com/DeviceSide/DeviceSide/blob/main/docs/integrations/zigbee2mqtt/research/2026-11-14-lab-probe.md) |
| Tuya plugs and switches (`turn_on_for`) | Zigbee2MQTT turns it on with no timer, so it stays on | [source](https://github.com/DeviceSide/DeviceSide/blob/main/docs/integrations/zigbee2mqtt/research/2026-11-14-timed-on-call-patterns.md) |
| Tuya datapoint devices (`turn_on_for`) | Zigbee2MQTT turns it on with no timer, so it stays on | [source](https://github.com/DeviceSide/DeviceSide/blob/main/docs/integrations/zigbee2mqtt/research/2026-11-14-timed-on-call-patterns.md) |

### Shelly

| Action | Level | Items | Tested models | Records |
|---|---|---|---|---|
| `clear_auto_off` | Verified | light, switch entities; Switch classes | `S3SW-001X8EU`, firmware `20261026-122213/2.0.3-g4601943` | [record](integrations/shelly/test-records/clear_auto_off.md) |
| `clear_auto_off` | Documented | Light, CCT, RGB, RGBW, RGBCCT classes | | |
| `clear_auto_on` | Verified | light, switch entities; Switch classes | `S3SW-001X8EU`, firmware `20261026-122213/2.0.3-g4601943` | [record](integrations/shelly/test-records/clear_auto_on.md) |
| `clear_auto_on` | Documented | Light, CCT, RGB, RGBW, RGBCCT classes | | |
| `get_attributes` | Verified | light, switch entities; Switch, RGBCCT classes | `S3BL-C010007AEU`, firmware `20261026-122229/2.0.3-g4601943`<br>`S3SW-001X8EU`, firmware `20261026-122213/2.0.3-g4601943` | [record](integrations/shelly/test-records/get_attributes.md) |
| `get_attributes` | Documented | Light, CCT, RGB, RGBW classes | | |
| `lower_brightness` | Verified | light entities; RGBCCT classes; brightness fields | `S3BL-C010007AEU`, firmware `20261026-122229/2.0.3-g4601943` | [record](integrations/shelly/test-records/lower_brightness.md) |
| `lower_brightness` | Experimental | Light, CCT, RGB, RGBW classes | | |
| `raise_brightness` | Verified | light entities; RGBCCT classes; brightness fields | `S3BL-C010007AEU`, firmware `20261026-122229/2.0.3-g4601943` | [record](integrations/shelly/test-records/raise_brightness.md) |
| `raise_brightness` | Experimental | Light, CCT, RGB, RGBW classes | | |
| `set_attributes` | Verified | light entities; RGBCCT classes; brightness, color_temp_kelvin, hs_color, rgb_color, xy_color, min_brightness, max_brightness fields | `S3BL-C010007AEU`, firmware `20261026-122229/2.0.3-g4601943` | [record](integrations/shelly/test-records/set_attributes.md) |
| `set_attributes` | Experimental | Light, CCT, RGB, RGBW classes | | |
| `set_auto_off` | Verified | light, switch entities; Switch classes | `S3SW-001X8EU`, firmware `20261026-122213/2.0.3-g4601943` | [record](integrations/shelly/test-records/set_auto_off.md) |
| `set_auto_off` | Documented | Light, CCT, RGB, RGBW, RGBCCT classes | | |
| `set_auto_on` | Verified | light, switch entities; Switch classes | `S3SW-001X8EU`, firmware `20261026-122213/2.0.3-g4601943` | [record](integrations/shelly/test-records/set_auto_on.md) |
| `set_auto_on` | Documented | Light, CCT, RGB, RGBW, RGBCCT classes | | |
| `stop` | Verified | light entities; RGBCCT classes | `S3BL-C010007AEU`, firmware `20261026-122229/2.0.3-g4601943` | [record](integrations/shelly/test-records/stop.md) |
| `stop` | Experimental | Light, CCT, RGB, RGBW classes | | |
| `turn_off_for` | Verified | light, switch entities; Switch, RGBCCT classes | `S3SW-001X8EU`, firmware `20261026-122213/2.0.3-g4601943`<br>`S3BL-C010007AEU`, firmware `20261026-122229/2.0.3-g4601943` | [record](integrations/shelly/test-records/turn_off_for.md) |
| `turn_off_for` | Documented | Light, CCT, RGB, RGBW classes | | |
| `turn_on_for` | Verified | light, switch entities; Switch, RGBCCT classes; brightness, color_temp_kelvin, hs_color, rgb_color, transition, xy_color fields | `S3SW-001X8EU`, firmware `20261026-122213/2.0.3-g4601943`<br>`S4SW-001X8EU`, firmware `20261026-122156/2.0.3-g4601943`<br>`S3BL-C010007AEU`, firmware `20261026-122229/2.0.3-g4601943` | [record](integrations/shelly/test-records/turn_on_for.md) |
| `turn_on_for` | Documented | Light, CCT, RGB, RGBW classes | | |

### Hue

| Action | Level | Items | Tested models | Records |
|---|---|---|---|---|
| `get_attributes` | Verified | light entities | `LTA009`, firmware `1.163.1`<br>`LCA016`, firmware `1.164.2`<br>group | [record](integrations/hue/test-records/get_attributes.md) |
| `lower_brightness` | Verified | light entities; brightness fields | `LTA009`, firmware `1.163.1`<br>group | [predicted state](integrations/hue/test-records/lower_brightness-predicted_state.md), [record](integrations/hue/test-records/lower_brightness.md) |
| `lower_saturation` | Verified | light entities; saturation fields | `LCA016`, firmware `1.164.2`<br>group | [record](integrations/hue/test-records/lower_saturation.md) |
| `raise_brightness` | Verified | light entities; brightness fields | `LTA009`, firmware `1.163.1`<br>group | [predicted state](integrations/hue/test-records/raise_brightness-predicted_state.md), [record](integrations/hue/test-records/raise_brightness.md) |
| `raise_saturation` | Verified | light entities; saturation fields | `LCA016`, firmware `1.164.2`<br>group | [record](integrations/hue/test-records/raise_saturation.md) |
| `set_attributes` | Verified | light entities; brightness, color_temp_kelvin, hs_color, rgb_color, xy_color, min_brightness, max_brightness fields | `LTA009`, firmware `1.163.1`<br>`LCA016`, firmware `1.164.2`<br>group | [record](integrations/hue/test-records/set_attributes.md) |
| `set_scene` | Verified | scene entities | group | [record](integrations/hue/test-records/set_scene.md) |
| `stop` | Verified | light entities | `LTA009`, firmware `1.163.1`<br>`LCA016`, firmware `1.164.2`<br>group | [predicted state](integrations/hue/test-records/stop-predicted_state.md), [record](integrations/hue/test-records/stop.md) |

| Doesn't work | Why | Source |
|---|---|---|
| Hue smart scenes | A smart (All-Day) scene changes with the time of day, so it has no fixed settings to write | [source](https://github.com/DeviceSide/DeviceSide/blob/main/docs/integrations/hue/index.md#flash-free-turn-on) |

### Matter

| Action | Level | Items | Tested models | Records |
|---|---|---|---|---|
| `get_attributes` | Verified | switch entities | `8979`, firmware `v1.4` | [record](integrations/matter/test-records/get_attributes.md) |
| `get_attributes` | Documented | light entities | | |
| `turn_on_for` | Verified | switch entities | `8979`, firmware `v1.4` | [record](integrations/matter/test-records/turn_on_for.md) |
| `turn_on_for` | Documented | light entities | | |

<!-- evidence:end -->
