# Matter internals

How the Matter adapters address a target, what they send and read back, and what was measured on real hardware.
What a user sees is in [index.md](index.md) and isn't repeated here; what every integration shares is in
[internals.md](../../internals.md). Result and detail codes are in the code. Code comments cite the sections here.

## Addressing

Through the core integration's python-matter-server connection. The unique ID is
`{fabric_hex16}-{node_hex16}-{postfix}-{endpoint_id}-{key}-{cluster_id}-{attr_id}`. The Lighting feature check reads
FeatureMap bit 0 from the node data (`node_data.attributes["{endpoint}/6/65532"]`), avoiding a chip.clusters import
that `MatterEndpoint.get_attribute_value` would need.

Lights take the same command and reads as plugs, behind the same Lighting feature check.

## Modules

One section per adapter module, named after it.

### `turn_on_for`

`OnOff.Commands.OnWithTimedOff(onOffControl=0, onTime=<tenths>, offWaitTime=0)`, then
reads of `OnOff.OnTime` and `OnOff.OnOff`, one path per `read_attribute`: `confirmed` when OnOff is on and OnTime
is at most the time sent and no more than 3 s under it, as on ZHA. OnTime alone is not proof: a device can hold a
stale OnTime while off.

### `get_attributes`

Live reads of `OnTime` and `OnOff`, as the plug tested never reports OnTime on its
subscription, so the node cache reads 0 mid-timer (Tested behavior). A timer is reported only while OnOff is on.
`turns` is always `off`.

## Tested behavior

On a WiZ Smart Plug (firmware v1.4), 2026-10-28: the node cache's `OnTime` read 0 before, during and after a 20 s
timer, as the plug never reports it on its subscription. A plain `switch.turn_on` mid-timer left it running.

Its device strings (2026-11-14, Home Assistant 2026.10.4): manufacturer `Signify`, model `WiZ Smart Plug`, `model_id`
`8979`, `sw_version` and `hw_version` `v1.4`; Basic Information VendorID 4107, ProductID 8979, OnOff FeatureMap 1.
Probe: [lab-probe](../zigbee2mqtt/research/2026-11-14-lab-probe.md), §1.

## Upstream defects

Workarounds the adapters carry for behavior upstream that could be fixed there. Only the issues our code already
cites have been checked upstream: search each tracker for duplicates before filing.

None open.

Not worth filing:

- **Firmware:** a WiZ plug never reporting OnTime on its subscription. A report to WiZ at most.
