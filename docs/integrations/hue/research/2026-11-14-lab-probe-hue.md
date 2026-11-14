# Lab probe, Hue: the Sengled and rooms (2026-11-14)

Probe date: 2026-11-14, the same run as
[the lab probe](../../zigbee2mqtt/research/2026-11-14-lab-probe.md) (spec: `docs/plans/2026-11-14-device-classes.md`,
step 3), on Home Assistant 2026.10.4 and a Hue Bridge `BSB002`, software `6.3.1978545030`.

## The Sengled E11-G13 on the bridge

It doesn't pair: adding it from the Hue app failed (how it failed wasn't recorded). So the Sengled has no Hue row, and the bridge's
`other` class has no Sengled evidence. It went back to ZHA, where its registry strings are unchanged (`sengled` /
`E11-G13`, `sw_version` `0x00000009`, no quirk, `manufacturer_code` 4448).

## Rooms and zones

| Group | Has a device entry | manufacturer | model | Members resolve |
|---|---|---|---|---|
| room | yes | `Signify Netherlands B.V.` | `Room` | yes: every device child of every room has a device entry |
| zone | yes | `Signify Netherlands B.V.` | `Zone` | not checked: a zone's children are lights, not devices |

Method: each `is_hue_group` light entity's device, by `device_id()`; each room's `children` (`rtype` `device`) from
the Hue config entry's diagnostics, matched to the registry identifier `("hue", <device id>)`. The entity's own
`lights` attribute lists Hue names, which don't always match Home Assistant's, so it can't count members.

This corrects the first file's §1 Groups row, which now points here: the first check named a room entity that no
longer exists, and `device_id()` of a missing entity answers nothing.
