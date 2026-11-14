"""Zigbee2MQTT's device classes: the rules that name a device's class, the group members its classes come from, and
what each class's level does to a call."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.deviceside.core.errors import TargetNotSupported
from custom_components.deviceside.core.models import Target
from custom_components.deviceside.core.resolver import resolve
from custom_components.deviceside.core.runtime import get_runtime
from custom_components.deviceside.core.scope import Level
from custom_components.deviceside.integrations.zigbee2mqtt import (
    ADAPTERS,
    INTEGRATION,
    bridge_cache,
    classes,
    members,
    native_control,
)
from custom_components.deviceside.integrations.zigbee2mqtt.classes import RULES, Rule

from .conftest import DEVICES, SENT, SET, call, commands, group, light
from ...conftest import allow_experimental, refusal
from ...conftest import call as call_service
from ...fixtures.zigbee2mqtt import GROUPS, IEEE, add_z2m_entity, add_z2m_group, z2m_entry

STRINGS = Path(__file__).parents[3] / "custom_components" / "deviceside" / "strings.json"
# Strings of the shapes the lab probe saw (research/2026-11-14-lab-probe.md, §1): the Zigbee manufacturer a Tuya
# device reports in bridge/devices, and the vendor and definition Z2M registers for a TS011F.
TUYA = "_TZ3000_abcdefgh"
TUYA_MCU = "_TZE200_abcdefgh"
PLUG = ("Tuya", "TS011F_plug_1")
# Gledopto's GL-SPI-206P: Gledopto's definition, Tuya datapoint firmware (classes.py, rule order 2).
GLEDOPTO_TUYA = {"manufacturer": "Gledopto", "model_id": "GL-SPI-206P", "zigbee": (TUYA_MCU, "TS0601")}


def interviewed(ieee: str, manufacturer: str, model_id: str) -> dict:
    """A device's bridge/devices entry once Zigbee2MQTT has interviewed it."""
    return {"ieee_address": ieee, "manufacturer": manufacturer, "model_id": model_id, "interview_completed": True}


def stand_in(hass, manufacturer: str | None, model_id: str | None = None) -> Target:
    """A target with a registry device alone, as the rig classes one: no config entry or state."""
    config = z2m_entry(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=config.entry_id,
        identifiers={("mqtt", f"zigbee2mqtt_{IEEE}")},
        manufacturer=manufacturer,
        model_id=model_id,
    )
    entry = er.async_get(hass).async_get_or_create("light", "mqtt", IEEE, device_id=device.id)
    return Target(entry.entity_id, "light", "zigbee2mqtt", entry, device, None, None)


@pytest.mark.parametrize(
    ("manufacturer", "model_id", "expected"),
    [
        ("Philips", "9290018217", "hue"),
        ("Philips Lighting", None, "other"),  # the whole string, not a prefix
        ("IKEA of Sweden", "LED1836G9", "other"),
        ("Sengled", "E11-G13", "sengled"),
        ("Gledopto", "GL-C-008", "gledopto"),  # Z2M's vendor string
        ("GLEDOPTO", "GL-S-007ZS", "gledopto"),  # the Zigbee string, in any case
        ("Gledopto Lighting", "GL-C-009", "other"),  # the whole string, not a prefix
        (*PLUG, "tuya_ts011f"),
        ("Tuya", "TS011F", "tuya_ts011f"),
        ("Tuya", "TS0601_switch", "tuya_mcu"),
        ("Tuya", "TS0001_switch_module", "tuya_zcl"),
        ("Tuya", "ZG-101Z", "tuya_zcl"),  # Tuya's own vendor string
        ("Moes", "TS0002", "tuya_zcl"),  # a Tuya definition model under another vendor
        ("Lidl", "HG06337", "other"),  # a rebadged Tuya plug the registry can't show: bridge/devices does
        (None, None, "other"),
    ],
)
def test_on_the_registry_alone_a_device_s_class_is_its_first_matching_rule(hass, manufacturer, model_id, expected):
    assert INTEGRATION.capability(None, stand_in(hass, manufacturer, model_id)) == expected


@pytest.mark.parametrize(
    ("registry", "zigbee", "expected"),
    [
        (("Lidl", "HG06337"), (TUYA, "TS011F"), "tuya_ts011f"),
        (("Lidl", "HG06337"), (TUYA, "TS0001"), "tuya_zcl"),
        (("Lidl", "HG06337"), ("_TYZB01_abcdefgh", "HG06337"), "tuya_zcl"),
        (("Moes", "ZM-105"), (TUYA_MCU, "TS0601"), "tuya_mcu"),
        (("Moes", "ZM-105"), (TUYA_MCU, "ZM-105"), "tuya_mcu"),
        (("Moes", "ZM-105"), ("Moes", "TS0601"), "tuya_mcu"),
        (("Philips", "9290018217"), ("Signify Netherlands B.V.", "LWA012"), "hue"),
        (("Sengled", "E11-G13"), ("sengled", "E11-G13"), "sengled"),
        (("IKEA of Sweden", "LED1836G9"), ("IKEA of Sweden", "TRADFRI bulb E27 WW 806lm"), "other"),
        # Tuya's model is `TS` and four digits; another `TS` model under another vendor isn't Tuya's.
        (("Lidl", "HG06337"), ("Lidl", "TSV-1"), "other"),
        (("Lidl", "HG06337"), ("Lidl", "TS011"), "other"),
    ],
)
async def test_the_zigbee_strings_in_bridge_devices_place_a_rebadged_tuya_device(
    hass, setup_production, broker, registry, zigbee, expected
):
    entity_id = light(hass, broker, manufacturer=registry[0], model_id=registry[1], zigbee=zigbee)

    assert INTEGRATION.capability(hass, resolve(hass, get_runtime(hass).registry, entity_id)) == expected


async def test_until_bridge_devices_arrives_the_registry_places_the_device(hass, setup_production, broker):
    entity_id = light(hass, broker, manufacturer="Lidl", model_id="HG06337", zigbee=(TUYA, "TS011F"))
    target = resolve(hass, get_runtime(hass).registry, entity_id)
    del broker.retained[DEVICES]

    assert INTEGRATION.capability(hass, target) == "other"


async def test_a_device_bridge_devices_does_not_list_yet_is_placed_by_the_registry(hass, setup_production, broker):
    entity_id = light(hass, broker, manufacturer=PLUG[0], model_id=PLUG[1])
    broker.retained[DEVICES] = [{"ieee_address": "0x09", "manufacturer": TUYA_MCU, "model_id": "TS0601"}]

    assert INTEGRATION.capability(hass, resolve(hass, get_runtime(hass).registry, entity_id)) == "tuya_ts011f"


def test_the_bridge_cache_indexes_bridge_devices_once_per_payload():
    cache = bridge_cache.BridgeCache()
    cache._payloads["devices"] = (
        '[{"ieee_address": "0x01", "model_id": "TS011F"}, "junk", {"model_id": "x"}, {"ieee_address": 5}, '
        '{"ieee_address": null}]'
    )

    first = cache.device("0x01")
    assert first == {"ieee_address": "0x01", "model_id": "TS011F"}
    assert cache.device("0x01") is first
    assert cache.device("0x02") is None
    assert set(cache._devices[1]) == {"0x01"}  # only a string address is indexed
    # A new payload re-indexes: a device it adds is found, one it drops is gone.
    cache._payloads["devices"] = '[{"ieee_address": "0x02", "model_id": "TS0601"}]'
    assert cache.device("0x02") == {"ieee_address": "0x02", "model_id": "TS0601"}
    assert cache.device("0x01") is None
    cache._payloads["devices"] = '{"not": "a list"}'
    assert cache.device("0x02") is None


def test_a_target_with_no_device_has_no_class(hass):
    target = stand_in(hass, "Philips")
    assert (
        INTEGRATION.capability(None, Target(target.entity_id, "light", "zigbee2mqtt", target.entry, None, None, None))
        is None
    )


def test_the_hue_rule_and_native_control_read_one_string():
    assert native_control.HUE_MANUFACTURER is classes.HUE_MANUFACTURER
    assert RULES[0] == Rule("hue", {"manufacturer": "Philips"})


@pytest.mark.parametrize(
    ("rule", "error"),
    [
        (lambda: Rule("other", {"manufacturer": "x"}), "no class but the default's"),
        (lambda: Rule("ikea", {"manufacturer": "x"}), "no class but the default's"),
        (lambda: Rule("hue", {"vendor": "x"}), r"tests \['vendor'\]"),
        (lambda: Rule("hue", {}), "tests nothing"),
    ],
)
def test_a_rule_names_a_declared_class_and_known_fields(rule, error):
    with pytest.raises(ValueError, match=error):
        rule()


def test_the_default_class_is_other_and_held_below_verified():
    assert INTEGRATION.default_class == "other"
    assert INTEGRATION.classes == {"hue", "tuya_zcl", "tuya_ts011f", "tuya_mcu", "sengled", "gledopto", "other"}


def _scopes() -> dict:
    return {adapter.action: adapter.scope for adapter in ADAPTERS}


def test_other_is_documented_on_brightness_fades_and_stop_alone():
    documented = {action for action, scope in _scopes().items() if scope.capabilities["other"] is Level.DOCUMENTED}
    assert documented == {"raise_brightness", "lower_brightness", "stop"}


@pytest.mark.parametrize(
    ("capability", "doesnt_work", "not_supported"),
    [
        ("tuya_zcl", {"turn_on_for"}, set()),
        ("tuya_ts011f", {"turn_on_for"}, set()),
        ("tuya_mcu", {"turn_on_for"}, {"get_attributes", "set_attributes"}),
        ("sengled", set(), {"turn_on_for"}),
        # Its converter departs from the brightness commands `other` is Documented on (Decision 24).
        ("gledopto", set(), set()),
    ],
)
def test_where_each_class_is_refused_and_elsewhere_untested(capability, doesnt_work, not_supported):
    scopes = _scopes()
    assert {action for action, scope in scopes.items() if capability in scope.doesnt_work} == doesnt_work
    refused = {action for action, scope in scopes.items() if capability not in scope.capabilities}
    assert refused - doesnt_work == not_supported
    assert {scope.capabilities[capability] for action, scope in scopes.items() if action not in refused} == {
        Level.EXPERIMENTAL
    }


# Group members, from the bridge cache.


async def test_a_group_lists_its_members_from_the_held_bridge_groups(hass, setup_production, broker):
    entity_id = group(hass, broker, one={}, two={"manufacturer": "IKEA of Sweden"})
    target = resolve(hass, get_runtime(hass).registry, entity_id)

    members = INTEGRATION.expand_group(hass, target)

    assert [member.entity_id for member in members] == ["light.one", "light.two"]
    assert {member.via_group for member in members} == {entity_id}
    assert [INTEGRATION.capability(hass, member) for member in members] == ["hue", "other"]


async def test_membership_is_unknowable_without_bridge_groups(hass, setup_production, broker):
    entity_id = group(hass, broker, one={}, two={})
    del broker.retained[GROUPS]

    assert INTEGRATION.expand_group(hass, resolve(hass, get_runtime(hass).registry, entity_id)) is None


async def test_without_the_bridge_cache_membership_is_unknowable(hass, setup_production):
    add_z2m_group(hass, z2m_entry(hass), "light.room", group_id=7)

    assert INTEGRATION.expand_group(hass, resolve(hass, get_runtime(hass).registry, "light.room")) is None


@pytest.mark.parametrize(
    ("groups", "expected"),
    [
        pytest.param([{"id": 3, "members": [{"ieee_address": "0x01"}]}], None, id="unlisted"),
        pytest.param([{"id": 7, "members": [{"ieee_address": "0x09"}]}], None, id="no_member_in_ha"),
        pytest.param([{"id": 7, "members": []}], [], id="listed_empty"),
    ],
)
async def test_only_a_group_listed_with_no_members_is_empty(hass, setup_production, broker, groups, expected):
    entity_id = group(hass, broker, one={}, two={})
    broker.retained[GROUPS] = groups

    assert INTEGRATION.expand_group(hass, resolve(hass, get_runtime(hass).registry, entity_id)) == expected


def plug_in_group(hass, broker) -> str:
    """Group 7 of two Hue bulbs and a TS011F plug, 0x03, which Z2M exposes as a switch, so HA has no light for it."""
    entity_id = group(hass, broker, one={}, two={})
    entry = hass.config_entries.async_entries("mqtt")[0]
    add_z2m_entity(hass, entry, "switch.plug", ieee="0x03", manufacturer=PLUG[0], model_id=PLUG[1])
    broker.retained[DEVICES].append(interviewed("0x03", TUYA, "TS011F"))
    broker.retained[GROUPS][0]["members"].append({"ieee_address": "0x03"})
    return entity_id


async def test_a_member_ha_has_no_light_for_is_classed_by_its_device(hass, setup_production, broker):
    """Left out, the plug would leave the group Verified on the Hue bulbs alone, and get its light command."""
    entity_id = plug_in_group(hass, broker)

    members = INTEGRATION.expand_group(hass, resolve(hass, get_runtime(hass).registry, entity_id))

    classed = {member.entity_id: INTEGRATION.capability(hass, member) for member in members}
    # The plug's stand-in is the group's own entity, with its device and no state, so no action reaches it.
    assert classed == {"light.one": "hue", "light.two": "hue", entity_id: "tuya_ts011f"}
    assert [member.state for member in members if member.entity_id == entity_id] == [None]


async def test_a_stand_in_is_left_out_of_the_member_lights_and_logged(hass, setup_production, broker, caplog):
    """The plug's stand-in has no state, so no action reads or commands it; the debug log says why it is missing."""
    caplog.set_level("DEBUG", logger=members.__name__)
    entity_id = plug_in_group(hass, broker)
    group_target = resolve(hass, get_runtime(hass).registry, entity_id)

    lights = await members.member_lights(hass, group_target)

    assert [member.entity_id for member in lights] == ["light.one", "light.two"]
    assert f"{entity_id}: a member device (switch.plug) has no light entity" in caplog.text


async def test_a_group_with_a_member_that_doesnt_work_falls_back_with_experimental_on(hass, setup_production, broker):
    """The audit's H1: the plug once left the membership unknown, so with Experimental features on the group ran
    whole, and the plug took the group's timer and stayed on."""
    entity_id = plug_in_group(hass, broker)
    allow_experimental(hass)

    response = await call(hass, "turn_on_for", {"entity_id": entity_id, "duration": 5})

    assert response == {"light.one": SENT, "light.two": SENT}
    assert {topic for topic, _ in commands(broker)} == {SET.replace(IEEE, "0x01"), SET.replace(IEEE, "0x02")}


async def test_a_member_with_no_device_leaves_membership_unknowable(hass, setup_production, broker):
    entity_id = group(hass, broker, one={}, two={})
    broker.retained[GROUPS][0]["members"].append({"ieee_address": "0x09"})

    assert INTEGRATION.expand_group(hass, resolve(hass, get_runtime(hass).registry, entity_id)) is None


async def test_a_member_on_two_endpoints_counts_once(hass, setup_production, broker):
    entity_id = group(hass, broker, one={}, two={})
    broker.retained[GROUPS][0]["members"].append({"ieee_address": "0x01", "endpoint": 2})

    members = INTEGRATION.expand_group(hass, resolve(hass, get_runtime(hass).registry, entity_id))

    assert [member.entity_id for member in members] == ["light.one", "light.two"]


# What a class's level does to a call.


async def test_a_hue_bulb_is_class_hue(hass, setup_production, broker):
    entity_id = light(hass, broker, manufacturer="Philips")

    response = await call_service(hass, "turn_on_for", {"entity_id": entity_id, "duration": 5})

    assert response == {entity_id: SENT | {"capability": "hue"}}


async def test_a_non_hue_light_is_experimental(hass, setup_production, broker):
    entity_id = light(hass, broker, manufacturer="IKEA of Sweden")
    data = {"entity_id": entity_id, "duration": 5}

    assert await refusal(hass, "turn_on_for", data) == "experimental_off"
    allow_experimental(hass)
    assert await call_service(hass, "turn_on_for", data) == {entity_id: SENT | {"capability": "other"}}


async def test_a_brightness_fade_and_stop_run_on_another_brand_as_documented(hass, setup_production, broker):
    """The call a Hue bulb gets, pinned: the same publish, to an IKEA bulb's topic, with Experimental features off."""
    entity_id = light(hass, broker, manufacturer="IKEA of Sweden")

    response = await call_service(hass, "raise_brightness", {"entity_id": entity_id, "limit": 80, "sweep_time": 10})
    assert response == {entity_id: SENT | {"capability": "other"}}
    assert commands(broker) == [(SET, {"state": "ON", "brightness": 203, "transition": 2.0})]

    broker.published.clear()
    assert await call_service(hass, "stop", {"entity_id": entity_id}) == {entity_id: SENT | {"capability": "other"}}
    assert commands(broker) == [(SET, {"brightness_move": "stop"})]


async def test_a_hue_fade_runs_on_another_brand_with_experimental_on(hass, setup_production, broker):
    entity_id = light(hass, broker, manufacturer="IKEA of Sweden")
    data = {"entity_id": entity_id, "sweep_time": 10}

    assert await refusal(hass, "raise_hue", data) == "experimental_off"
    allow_experimental(hass)
    assert await call_service(hass, "raise_hue", data) == {entity_id: SENT | {"capability": "other"}}
    assert commands(broker)


async def test_a_hue_and_ikea_group_falls_back_while_experimental_is_off(hass, setup_production, broker, caplog):
    entity_id = group(hass, broker, one={}, two={"manufacturer": "IKEA of Sweden"})
    data = {"entity_id": entity_id, "duration": 5}

    # Off: the Hue bulb runs on its own, the IKEA one is skipped.
    assert await call_service(hass, "turn_on_for", data) == {"light.one": SENT | {"capability": "hue"}}
    assert commands(broker) == [(SET.replace(IEEE, "0x01"), {"state": "ON", "on_time": 5.0})]
    assert "light.two (experimental_off)" in caplog.text

    # On: the group runs whole, one command to its group topic, at its weakest member's class.
    broker.published.clear()
    allow_experimental(hass)
    assert await call_service(hass, "turn_on_for", data) == {entity_id: SENT | {"capabilities": ["hue", "other"]}}
    assert commands(broker) == [("zigbee2mqtt/7/set", {"state": "ON", "on_time": 5.0})]


async def test_a_hue_group_is_verified_on_its_members_class(hass, setup_production, broker):
    entity_id = group(hass, broker, one={}, two={})

    response = await call_service(hass, "turn_on_for", {"entity_id": entity_id, "duration": 5})

    assert response == {entity_id: SENT | {"capabilities": ["hue"]}}


# The Tuya and Sengled classes.


async def test_turn_on_for_refuses_a_ts011f_as_doesnt_work_with_experimental_on(hass, setup_production, broker):
    """Z2M has no on_time converter for it, so today's call would turn the plug on with no timer
    (research/2026-11-14-lab-probe.md, §2): refused, not flagged, so Experimental features can't send it."""
    allow_experimental(hass)
    add_z2m_entity(hass, z2m_entry(hass), "switch.plug", manufacturer=PLUG[0], model_id=PLUG[1])
    broker.retained[DEVICES] = [interviewed(IEEE, TUYA, "TS011F")]

    assert await refusal(hass, "turn_on_for", {"entity_id": "switch.plug", "duration": 5}) == "doesnt_work"
    assert commands(broker) == []


async def test_the_ts011f_s_refusal_says_what_the_user_sees(hass, setup_production, broker):
    allow_experimental(hass)
    add_z2m_entity(hass, z2m_entry(hass), "switch.plug", manufacturer=PLUG[0], model_id=PLUG[1])
    broker.retained[DEVICES] = [interviewed(IEEE, TUYA, "TS011F")]

    with pytest.raises(HomeAssistantError) as raised:
        await call_service(hass, "turn_on_for", {"entity_id": "switch.plug", "duration": 5})
    assert raised.value.translation_placeholders["why"] == classes.TURN_ON_FOR_DOESNT_WORK["tuya_ts011f"].reason
    assert "no timer, so it stays on" in raised.value.translation_placeholders["why"]


@pytest.mark.parametrize(
    "registry",
    [
        pytest.param(("Tuya", "TS0001_switch_module"), id="tuya_zcl"),
        pytest.param(("Tuya", "TS0601_switch"), id="tuya_mcu"),
    ],
)
async def test_turn_on_for_refuses_every_tuya_class_as_doesnt_work_with_experimental_on(
    hass, setup_production, broker, registry
):
    """Z2M turns `on_time` into a plain On on Tuya firmware (Decision 20), so the device would stay on."""
    allow_experimental(hass)
    entity_id = light(hass, broker, manufacturer=registry[0], model_id=registry[1])

    assert await refusal(hass, "turn_on_for", {"entity_id": entity_id, "duration": 5}) == "doesnt_work"
    assert commands(broker) == []


@pytest.mark.parametrize(
    ("registry", "action", "data"),
    [
        pytest.param(("Tuya", "TS0601_dimmer"), "get_attributes", {}, id="tuya_mcu_read"),
        pytest.param(("Tuya", "TS0601_dimmer"), "set_attributes", {"brightness": 50}, id="tuya_mcu_write"),
        pytest.param(("Sengled", "E11-G13"), "turn_on_for", {"duration": 5}, id="sengled"),
    ],
)
async def test_a_class_that_needs_an_unbuilt_call_is_not_supported(
    hass, setup_production, broker, registry, action, data
):
    """Rather than send `on_time`, which the Sengled ignores, or raw ZCL a datapoint device can't take."""
    allow_experimental(hass)
    entity_id = light(hass, broker, manufacturer=registry[0], model_id=registry[1])

    assert await refusal(hass, action, {"entity_id": entity_id, **data}) == "not_supported"
    assert commands(broker) == []


# Where a call is known to harm, a class not yet known is refused (docs/evidence.md, Doesn't work, not yet supported,
# untested; Decision 23): turn_on_for refuses a target it can't class, even with Experimental features on.


async def test_turn_on_for_refuses_a_group_of_unknown_membership_with_experimental_on(hass, setup_production, broker):
    """The audit's M1: before bridge/groups arrives, a group with a Tuya plug in it would run whole."""
    entity_id = group(hass, broker, one={}, two={})
    allow_experimental(hass)
    data = {"entity_id": entity_id, "duration": 5}

    retained = broker.retained.pop(GROUPS)
    assert await refusal(hass, "turn_on_for", data) == "class_unknown"
    retained[0]["members"].append({"ieee_address": "0x09"})  # a member with no device in HA
    broker.retained[GROUPS] = retained
    assert await refusal(hass, "turn_on_for", data) == "class_unknown"
    assert commands(broker) == []
    # Elsewhere unknown stays Experimental: stop runs the group whole.
    assert await call(hass, "stop", {"entity_id": entity_id}) == {entity_id: SENT}


async def test_turn_on_for_refuses_a_group_without_the_bridge_cache(hass, setup_production):
    allow_experimental(hass)
    add_z2m_group(hass, z2m_entry(hass), "light.room", group_id=7)

    assert await refusal(hass, "turn_on_for", {"entity_id": "light.room", "duration": 5}) == "class_unknown"


async def test_turn_on_for_refuses_a_device_only_the_registry_placed_in_other(hass, setup_production, broker):
    """The audit's M2: a rebadged TS011F (Lidl's definition) before bridge/devices arrives looks like another brand.
    Once it arrives, the plug is its own class, refused as doesn't work, and another brand runs flagged again."""
    plug = light(hass, broker, "light.plug", "0x03", manufacturer="Lidl", model_id="HG06337", zigbee=(TUYA, "TS011F"))
    ikea = light(hass, broker, "light.ikea", "0x04", manufacturer="IKEA of Sweden")
    allow_experimental(hass)

    devices = broker.retained.pop(DEVICES)
    for entity_id in (plug, ikea):
        assert await refusal(hass, "turn_on_for", {"entity_id": entity_id, "duration": 5}) == "class_unknown"
    assert commands(broker) == []

    broker.retained[DEVICES] = devices
    assert await refusal(hass, "turn_on_for", {"entity_id": plug, "duration": 5}) == "doesnt_work"
    assert await call(hass, "turn_on_for", {"entity_id": ikea, "duration": 5}) == {ikea: SENT}


async def test_turn_on_for_refuses_a_group_with_a_member_only_the_registry_placed(hass, setup_production, broker):
    entity_id = group(hass, broker, one={}, two={"manufacturer": "Lidl", "model_id": "HG06337"})
    del broker.retained[DEVICES]
    allow_experimental(hass)

    assert await refusal(hass, "turn_on_for", {"entity_id": entity_id, "duration": 5}) == "class_unknown"


async def test_a_hue_device_and_group_run_before_bridge_devices_arrives(hass, setup_production, broker):
    """The registry alone places a Hue bulb, so nothing waits on the cache for one."""
    entity_id = group(hass, broker, one={}, two={})
    lamp = light(hass, broker, manufacturer="Philips")
    del broker.retained[DEVICES]

    response = await call(hass, "turn_on_for", {"entity_id": [entity_id, lamp], "duration": 5})

    assert response == {entity_id: SENT, lamp: SENT}


async def test_a_group_with_a_ts011f_member_falls_back_to_its_members(hass, setup_production, broker, caplog):
    entity_id = group(hass, broker, one={}, two={"manufacturer": "Lidl", "zigbee": (TUYA, "TS011F")})

    response = await call(hass, "turn_on_for", {"entity_id": entity_id, "duration": 5})

    assert response == {"light.one": SENT}
    assert commands(broker) == [(SET.replace(IEEE, "0x01"), {"state": "ON", "on_time": 5.0})]
    assert "light.two (doesnt_work)" in caplog.text


def test_hue_s_rule_comes_first_and_reads_the_registry_alone():
    """Why a registry `hue` is placed without the Zigbee strings (classes.unplaced): no Zigbee-string rule can come
    before it and override it."""
    assert RULES[0].capability == "hue"
    assert RULES[0].conditions.keys() <= classes.REGISTRY


async def test_turn_on_for_refuses_a_gledopto_badged_tuya_device_until_its_strings_arrive(
    hass, setup_production, broker
):
    """The audit's H1 (round 2): the registry alone put the GL-SPI-206P in `gledopto`, which with Experimental
    features on took today's call; its Zigbee strings make it `tuya_mcu`, which the call leaves on."""
    entity_id = light(hass, broker, "light.strip", "0x03", **GLEDOPTO_TUYA)
    allow_experimental(hass)
    data = {"entity_id": entity_id, "duration": 5}

    devices = broker.retained.pop(DEVICES)
    assert await refusal(hass, "turn_on_for", data) == "class_unknown"
    assert commands(broker) == []
    broker.retained[DEVICES] = devices
    assert await refusal(hass, "turn_on_for", data) == "doesnt_work"


async def test_turn_on_for_refuses_a_hue_group_with_a_gledopto_badged_tuya_member_until_its_strings_arrive(
    hass, setup_production, broker
):
    entity_id = group(hass, broker, one={}, two=GLEDOPTO_TUYA)
    del broker.retained[DEVICES]
    allow_experimental(hass)

    assert await refusal(hass, "turn_on_for", {"entity_id": entity_id, "duration": 5}) == "class_unknown"
    assert commands(broker) == []


@pytest.mark.parametrize(
    "registry",
    [
        pytest.param(("Tuya", "TS0001_switch_module"), id="tuya"),
        pytest.param(("Sengled", "E11-G13"), id="sengled"),
        pytest.param(("IKEA of Sweden", "LED1836G9"), id="other"),
    ],
)
async def test_without_the_strings_only_a_hue_device_runs(hass, setup_production, broker, registry):
    """A registry Tuya class waits as well: refused either way, and part 3's countdown class must wait for the
    strings that decide it."""
    entity_id = light(hass, broker, manufacturer=registry[0], model_id=registry[1])
    del broker.retained[DEVICES]
    allow_experimental(hass)

    assert await refusal(hass, "turn_on_for", {"entity_id": entity_id, "duration": 5}) == "class_unknown"


async def test_a_hue_device_runs_with_no_bridge_cache(hass, setup_production, broker, monkeypatch):
    lamp = light(hass, broker, manufacturer="Philips")
    monkeypatch.setattr(classes, "bridge_cache", SimpleNamespace(get=lambda hass: None))

    assert await call(hass, "turn_on_for", {"entity_id": lamp, "duration": 5}) == {lamp: SENT}


@pytest.mark.parametrize(
    ("zigbee", "interviewed", "expected"),
    [
        # Mid-interview: Z2M lists the device with null strings until it reads them.
        pytest.param(None, False, "class_unknown", id="no_strings"),
        pytest.param((None, "TS011F"), True, "class_unknown", id="model_only"),
        pytest.param((TUYA, "TS011F"), False, "class_unknown", id="mid_interview"),
        pytest.param((TUYA, "TS011F"), True, "doesnt_work", id="interviewed"),
    ],
)
async def test_a_device_listed_with_null_strings_is_not_placed(
    hass, setup_production, broker, zigbee, interviewed, expected
):
    """The audit's M (round 2): Lidl's HG06337 mid-interview, `manufacturer: null`, once counted as placed in
    `other`, so it took today's call with Experimental features on."""
    entity_id = light(hass, broker, manufacturer="Lidl", model_id="HG06337", zigbee=zigbee, interviewed=interviewed)
    allow_experimental(hass)

    assert await refusal(hass, "turn_on_for", {"entity_id": entity_id, "duration": 5}) == expected


IKEA = ("IKEA of Sweden", "TRADFRI bulb E27 WW 806lm")


@pytest.mark.parametrize(
    ("zigbee", "interviewed"),
    [
        pytest.param((IKEA[0], None), True, id="no_model"),
        pytest.param((None, IKEA[1]), True, id="no_manufacturer"),
        pytest.param((IKEA[0], ""), True, id="empty_model"),
        pytest.param(("", IKEA[1]), True, id="empty_manufacturer"),
        pytest.param(IKEA, False, id="mid_interview"),
    ],
)
async def test_another_brand_waits_until_interviewed_with_both_strings(
    hass, setup_production, broker, zigbee, interviewed
):
    """A Tuya device could still show its string in the one missing, or finish its interview as another."""
    ikea = light(hass, broker, manufacturer=IKEA[0], zigbee=zigbee, interviewed=interviewed)
    allow_experimental(hass)

    assert await refusal(hass, "turn_on_for", {"entity_id": ikea, "duration": 5}) == "class_unknown"


async def test_another_brand_listed_with_null_strings_waits_and_once_interviewed_runs(hass, setup_production, broker):
    ikea = light(hass, broker, manufacturer=IKEA[0], zigbee=None)
    allow_experimental(hass)
    data = {"entity_id": ikea, "duration": 5}

    assert await refusal(hass, "turn_on_for", data) == "class_unknown"
    broker.retained[DEVICES][0] |= {"manufacturer": IKEA[0], "model_id": IKEA[1], "interview_completed": True}
    assert await call(hass, "turn_on_for", data) == {ikea: SENT}


async def test_class_unknown_says_it_clears_once_zigbee2mqtt_reports_the_device(hass, setup_production, broker):
    entity_id = light(hass, broker, **GLEDOPTO_TUYA)
    del broker.retained[DEVICES]
    allow_experimental(hass)

    with pytest.raises(TargetNotSupported) as raised:
        await call_service(hass, "turn_on_for", {"entity_id": entity_id, "duration": 5})

    assert raised.value.translation_key == "class_unknown"
    assert raised.value.translation_placeholders == {
        "entity_id": entity_id,
        "reason": "class_unknown",
        "integration": "Zigbee2MQTT",
    }
    # Without the bridge it never clears in seconds, so the message doesn't promise them.
    message = json.loads(STRINGS.read_text())["exceptions"]["class_unknown"]["message"]
    assert "usually takes seconds; if it doesn't, reload DeviceSide" in message


async def test_an_indirect_target_of_unknown_class_is_skipped_and_the_rest_run(hass, setup_production, broker, caplog):
    """Refused as core refuses a target: skipped and logged when it came indirectly, here through `all`."""
    lamp = light(hass, broker, "light.lamp", "0x01")
    strip = light(hass, broker, "light.strip", "0x03", **GLEDOPTO_TUYA)
    del broker.retained[DEVICES]
    allow_experimental(hass)

    assert await call(hass, "turn_on_for", {"entity_id": "all", "duration": 5}) == {lamp: SENT}
    assert f"{strip} (class_unknown)" in caplog.text
