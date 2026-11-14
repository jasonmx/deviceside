"""Zigbee2MQTT's device classes (docs/evidence.md, Device classes): which firmware family a target's device is.

On Zigbee the call and the firmware family decide the result together, so one call proves nothing across vendors
(research/2026-11-14-timed-on-call-patterns.md). A class is named by RULES: an ordered list of (class, conditions), the
first match winning, and `other`, the default class, for a device none places. A lab probe changes the rules' data,
never this structure.

A rule reads two sources, both sync and with no I/O:

- The registry device, which Z2M fills from its definition at discovery: the vendor as manufacturer, the definition's
  model as `model_id` (research/2026-11-14-lab-probe.md, §1). Enough for Hue, Sengled and Gledopto.
- The device's Zigbee strings, its own manufacturer and model, from the bridge cache's bridge/devices
  (bridge_cache.py, the one members.py lists groups from). Only they show a Tuya device under another vendor's
  definition (`_TZ3000_…`, `_TZE…`), which the registry doesn't (plan Decision 18): one that fell into `other` would
  take turn_on_for's call with Experimental features on, and Zigbee2MQTT would leave it on.

The rule order, the one home for it (internals.md points here). It matters in three places; elsewhere no device can
match two rules (`hue`'s vendor is Z2M's own Philips string, which no Tuya rule matches):

1. Among the Tuya rules on the Zigbee strings, the models go first: a TS011F's manufacturer (`_TZ3000_…`) matches
   `tuya_zcl`'s, and a TS0601's model matches `tuya_zcl`'s `TS` and four digits.
2. Every Tuya rule on the Zigbee strings goes before the vendor rules (`sengled`, `gledopto`), so a Tuya device sold
   under another vendor's definition (Gledopto's GL-SPI-206P runs Tuya datapoints) takes its Tuya class.
3. Among the Tuya rules on the registry, the models go before the vendor `Tuya` and the catch-all `TS.*`, which a
   `TS011F_…` or `TS0601_…` definition also matches; all of them go last, as they only stand in for the Zigbee strings.

The registry fallback's limit: until bridge/devices arrives after a connect, for a device it doesn't list yet, or for
one it lists without its strings (mid-interview), the Tuya rules on the Zigbee strings can't match, so the registry
rules place what the registry can show: vendor `Tuya`, or a definition model that starts `TS`. A Tuya device under
another vendor's definition shows neither: Lidl's `HG06337` lands in `other`, and Gledopto's GL-SPI-206P in
`gledopto`, until the strings arrive. `unplaced` names that case, which turn_on_for refuses (plan Decision 23), by one
rule: a device is placed once bridge/devices lists it with `interview_completed` true and both Zigbee strings
(Z2M's bridge/devices, https://www.zigbee2mqtt.io/guide/usage/mqtt_topics_and_messages.html), or when the registry
puts it in `hue`, which no Zigbee-string rule can override. So a registry Tuya class waits too (refused either way),
a rule added before the Zigbee-string rules can't become final by accident, and a class a later call serves (part
3's countdown) still waits for the full strings. With hass None (the rig's stand-in target, until its second change
reads the class from the result) only the registry is read. A group is no device of its own: the dispatcher classes
its members (members.expand_group).

Each adapter's scope holds the levels below; why each class sits where it does is in index.md (Device classes) and
the step 5 PR body (`other`'s Documented actions).
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from . import bridge_cache
from .addressing import z2m_address
from ...core.models import Target
from ...core.scope import DoesntWork, documented, experimental, verified

# Z2M's vendor string for Hue, which HA's device registry shows as the manufacturer; native_control.py reads it too.
HUE_MANUFACTURER = "Philips"

HUE = "hue"
# A Tuya device on the Zigbee On/Off cluster (TS011F, TS000x and white labels): its timer is Tuya's countdown.
TUYA_ZCL = "tuya_zcl"
# The TS011F plug, a Tuya ZCL device whose failure of turn_on_for's call is measured (Decision 16), matched by its
# model so every firmware inherits the refusal.
TUYA_TS011F = "tuya_ts011f"
# A Tuya datapoint device (TS0601): no Zigbee On/Off cluster, so no raw ZCL read or write reaches it (Decision 12).
TUYA_MCU = "tuya_mcu"
SENGLED = "sengled"
# Gledopto's controllers and bulbs: its converter departs from the standard calls (research/2026-11-14-timed-on-call-
# patterns.md, §3), so `other`'s Documented actions don't cover it (plan Decision 24).
GLEDOPTO = "gledopto"
OTHER = "other"  # the default class, never Verified (IntegrationInfo.default_class)
CLASSES = frozenset({HUE, TUYA_ZCL, TUYA_TS011F, TUYA_MCU, SENGLED, GLEDOPTO, OTHER})

# The fields a rule may test: the registry device's, and the Zigbee manufacturer and model bridge/devices holds.
REGISTRY = frozenset({"manufacturer", "model", "model_id"})
ZIGBEE = {"zigbee_manufacturer": "manufacturer", "zigbee_model": "model_id"}  # rule field: bridge/devices key


@dataclass(frozen=True, slots=True)
class Rule:
    """A class and the fields that place a device in it, each a regex the field must fully match; every condition
    must hold, and there is at least one."""

    capability: str
    conditions: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.capability not in CLASSES - {OTHER}:
            raise ValueError(f"a rule names {self.capability!r}, no class but the default's")
        if not self.conditions:
            raise ValueError(f"a rule for {self.capability!r} tests nothing")
        if unknown := self.conditions.keys() - REGISTRY - ZIGBEE.keys():
            raise ValueError(f"a rule tests {sorted(unknown)}, which neither the registry nor bridge/devices holds")

    def matches(self, strings: Mapping[str, str | None]) -> bool:
        return all(
            isinstance(value := strings.get(name), str) and re.fullmatch(pattern, value)
            for name, pattern in self.conditions.items()
        )


# Tuya's Zigbee manufacturer strings (research/2026-11-14-timed-on-call-patterns.md, §4): `_TZE…` for datapoint
# devices, `_TZ3000_…`, `_TZ3210_…` and `_TYZB01_…` for ZCL ones. Only the TS011F's are measured; the rest are Tuya's
# naming, from the converters and quirks.
_TUYA_MCU_MANUFACTURER = r"_TZE\d{3}_.*"
_TUYA_ZCL_MANUFACTURER = r"_TZ\d{4}_.*|_TYZB\d{2}_.*"

RULES: tuple[Rule, ...] = (
    Rule(HUE, {"manufacturer": re.escape(HUE_MANUFACTURER)}),
    # The Zigbee strings, from bridge/devices.
    Rule(TUYA_TS011F, {"zigbee_model": "TS011F"}),
    Rule(TUYA_MCU, {"zigbee_model": "TS0601"}),
    Rule(TUYA_MCU, {"zigbee_manufacturer": _TUYA_MCU_MANUFACTURER}),
    Rule(TUYA_ZCL, {"zigbee_manufacturer": _TUYA_ZCL_MANUFACTURER}),
    Rule(TUYA_ZCL, {"zigbee_model": r"TS\d{4}.*"}),
    Rule(SENGLED, {"manufacturer": "Sengled"}),
    # The registry manufacturer: Z2M's vendor `Gledopto`, in any case, as the devices' own Zigbee string is
    # `GLEDOPTO` (zigbee-herdsman-converters src/devices/gledopto.ts, at the research file's commit).
    Rule(GLEDOPTO, {"manufacturer": "(?i:gledopto)"}),
    # The registry, for a Tuya device bridge/devices doesn't show yet: Z2M's definition (`TS011F_plug_1`) and vendor.
    Rule(TUYA_TS011F, {"model_id": "TS011F(_.*)?"}),
    Rule(TUYA_MCU, {"model_id": "TS0601(_.*)?"}),
    Rule(TUYA_ZCL, {"manufacturer": "Tuya"}),
    Rule(TUYA_ZCL, {"model_id": "TS.*"}),
)

# The levels each adapter's scope holds (index.md, Device classes): Verified on Hue, whose bulbs every record ran on;
# `other` Documented where the call is the Verified one and no known class departs, else untested; the Tuya, Sengled
# and Gledopto classes untested, or left out where they're not yet supported or don't work.
_UNTESTED = experimental(TUYA_ZCL, TUYA_TS011F, TUYA_MCU, SENGLED, GLEDOPTO)
# Brightness fades and stop: the ZCL Level and Color commands Hue's are (native_control turns Philips' own off for a
# brightness fade), and no class known to depart from them.
FADE_BRIGHTNESS = STOP = verified(HUE) | documented(OTHER) | _UNTESTED
# Hue and saturation fades: a Hue bulb may get Philips' own command, as the user's option has it, and holds its hue
# fading saturation in xy, which nothing shows other firmware does.
FADE_COLOR = verified(HUE) | experimental(OTHER) | _UNTESTED
# Raw ZCL reads and writes, which a datapoint device can't take. Other brands depart: a bulb without `onTime` can fail
# the read, and some come on, or at full, when written while off.
READ_WRITE = verified(HUE) | experimental(OTHER, TUYA_ZCL, TUYA_TS011F, SENGLED, GLEDOPTO)
# turn_on_for: the Sengled needs a host-side off, which isn't built, so it is not yet supported; every other brand may
# depart (some never switch off), so `other` and `gledopto` are untested. The Tuya classes are listed as doesn't work.
TURN_ON_FOR = verified(HUE) | experimental(OTHER, GLEDOPTO)
# The Tuya classes are harmed by today's call (plan Decision 20): Z2M has no `on_time` converter for a definition built
# with `onOffCountdown`, or for a datapoint device, so it sends a plain On and the device stays on.
_RESEARCH = "https://github.com/DeviceSide/DeviceSide/blob/main/docs/integrations/zigbee2mqtt/research/"
_STAYS_ON = "Zigbee2MQTT turns it on with no timer, so it stays on"
TURN_ON_FOR_DOESNT_WORK = {
    # Measured (Decision 16), and matched by its model so every firmware inherits the refusal.
    TUYA_TS011F: DoesntWork(what="Tuya TS011F", reason=_STAYS_ON, source=_RESEARCH + "2026-11-14-lab-probe.md"),
    TUYA_ZCL: DoesntWork(
        what="Tuya plugs and switches", reason=_STAYS_ON, source=_RESEARCH + "2026-11-14-timed-on-call-patterns.md"
    ),
    TUYA_MCU: DoesntWork(
        what="Tuya datapoint devices", reason=_STAYS_ON, source=_RESEARCH + "2026-11-14-timed-on-call-patterns.md"
    ),
}


def _listed(hass: HomeAssistant | None, device: dr.DeviceEntry) -> dict | None:
    """The device's entry in the held bridge/devices; None with hass None, while none is held, or for a device it
    doesn't list."""
    if hass is None or (ieee := z2m_address(device)) is None or (cache := bridge_cache.get(hass)) is None:
        return None
    return cache.device(ieee)


def zigbee_strings(hass: HomeAssistant | None, device: dr.DeviceEntry) -> dict[str, str]:
    """The device's Zigbee manufacturer and model from the held bridge/devices, as rule fields, each only when it is a
    non-empty string (Z2M holds null until the interview reads it); empty with hass None, while no bridge/devices is
    held, or for a device it doesn't list."""
    if (entry := _listed(hass, device)) is None:
        return {}
    return {name: value for name, key in ZIGBEE.items() if isinstance(value := entry.get(key), str) and value}


def unplaced(hass: HomeAssistant, target: Target) -> bool:
    """Whether the target's device isn't placed yet: bridge/devices doesn't list it as interviewed
    (`interview_completed`) with both Zigbee strings, and its registry class isn't `hue`. The caller has refused a
    target with no device (not_addressable)."""
    # `hue` is the one class no Zigbee-string rule can override: its rule comes first and reads the registry alone.
    if device_class(target.device) == HUE:
        return False
    if (entry := _listed(hass, target.device)) is None or entry.get("interview_completed") is not True:
        return True
    return zigbee_strings(hass, target.device).keys() != ZIGBEE.keys()


def device_class(device: dr.DeviceEntry, zigbee: Mapping[str, str | None] | None = None) -> str:
    """The class of a device Z2M registered: the first rule its registry and Zigbee strings match, else the default."""
    strings = {name: getattr(device, name) for name in REGISTRY} | dict(zigbee or {})
    return next((rule.capability for rule in RULES if rule.matches(strings)), OTHER)


def capability(hass: HomeAssistant | None, target: Target) -> str | None:
    """Zigbee2MQTT's IntegrationInfo.capability: the target's device's class; None for a target with no device,
    which no rule can place and no adapter can address."""
    if target.device is None:
        return None
    return device_class(target.device, zigbee_strings(hass, target.device))
