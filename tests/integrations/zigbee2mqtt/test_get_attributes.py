"""Zigbee2MQTT get_attributes: live ZCL reads through Z2M's generic read, one cluster at a time."""

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from custom_components.deviceside.integrations.zigbee2mqtt import read

from .conftest import call
from ...conftest import allow_experimental, refusal, sent
from ...fixtures.zigbee2mqtt import GROUPS, IEEE, INFO, add_z2m_entity, add_z2m_group, z2m_entry

pytestmark = pytest.mark.usefixtures("setup_production")

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
KEY = "deviceside_read"
SET = f"zigbee2mqtt/{IEEE}/set"
DEVICE_REQUEST = "zigbee2mqtt/bridge/request/device/options"
LOGGER = "custom_components.deviceside.integrations.zigbee2mqtt"
CT_AND_XY = ["color_temp", "xy"]
# A bulb at 152 of 254 in color temperature mode, at 250 mireds.
BULB = {
    "genOnOff": {"onOff": 1, "onTime": 0},
    "genLevelCtrl": {"currentLevel": 152},
    "lightingColorCtrl": {
        "colorMode": 2,
        "colorTemperature": 250,
        "currentX": 19667,
        "currentY": 32722,
        "currentHue": 127,
        "currentSaturation": 254,
    },
}
ROW = {"brightness": 59.8, "color_temp_kelvin": 4000}


@pytest.fixture(autouse=True)
def frozen_now(monkeypatch):
    monkeypatch.setattr(dt_util, "utcnow", lambda: NOW)


def timer(remaining_s: float) -> dict:
    return {"turns": "off", "ends_at": (NOW + timedelta(seconds=remaining_s)).isoformat(), "remaining_s": remaining_s}


def bulb(
    hass, broker, entity_id: str = "light.lamp", ieee: str = IEEE, modes: list | None = None, **clusters: dict
) -> str:
    """A Z2M light whose device answers reads with BULB, each cluster updated by `clusters`."""
    name = entity_id.split(".")[1]
    entries = hass.config_entries.async_entries("mqtt")
    entry = entries[0] if entries else z2m_entry(hass)
    attributes = {"supported_color_modes": CT_AND_XY if modes is None else modes}
    add_z2m_entity(hass, entry, entity_id, ieee=ieee, state="on", attributes=attributes, name=name)
    answers = {cluster: {**values, **clusters.get(cluster, {})} for cluster, values in BULB.items()}
    broker.devices[ieee] = (name, answers)
    broker.retained[INFO]["config"]["devices"].setdefault(ieee, {})
    return entity_id


async def test_reads_each_cluster_and_returns_the_row(hass, broker):
    bulb(hass, broker)

    response = await call(hass, "get_attributes", {"entity_id": "light.lamp"})

    assert response == {"light.lamp": ROW}
    assert broker.reads() == [
        (SET, {"cluster": "genOnOff", "attributes": ["onOff", "onTime"], "state_property": KEY}),
        (SET, {"cluster": "genLevelCtrl", "attributes": ["currentLevel"], "state_property": KEY}),
        (
            SET,
            {
                "cluster": "lightingColorCtrl",
                "attributes": ["colorMode", "colorTemperature", "currentX", "currentY"],
                "state_property": KEY,
            },
        ),
    ]
    assert broker.subscribers == {
        topic: []
        for topic in (
            "zigbee2mqtt/bridge/logging",
            "zigbee2mqtt/bridge/state",
            "zigbee2mqtt/bridge/response/device/options",
            INFO,
            "zigbee2mqtt/lamp",
        )
    }


@pytest.mark.parametrize(
    ("modes", "reads"),
    [
        (["onoff"], {"genOnOff": ["onOff", "onTime"]}),
        (["brightness"], {"genOnOff": ["onOff", "onTime"], "genLevelCtrl": ["currentLevel"]}),
        (
            ["color_temp"],
            {
                "genOnOff": ["onOff", "onTime"],
                "genLevelCtrl": ["currentLevel"],
                "lightingColorCtrl": ["colorMode", "colorTemperature"],
            },
        ),
        (
            ["hs"],
            {
                "genOnOff": ["onOff", "onTime"],
                "genLevelCtrl": ["currentLevel"],
                "lightingColorCtrl": ["colorMode", "currentHue", "currentSaturation"],
            },
        ),
    ],
)
async def test_reads_only_the_attributes_its_color_modes_have(hass, broker, modes, reads):
    bulb(hass, broker, modes=modes)

    await call(hass, "get_attributes", {"entity_id": "light.lamp"})

    assert {read["cluster"]: read["attributes"] for _, read in broker.reads()} == reads


async def test_xy_mode_reports_the_color(hass, broker):
    bulb(hass, broker, lightingColorCtrl={"colorMode": 1})

    response = await call(hass, "get_attributes", {"entity_id": "light.lamp"})

    assert response["light.lamp"]["xy_color"] == [0.3001, 0.4993]
    assert "color_temp_kelvin" not in response["light.lamp"]


async def test_reports_a_running_timer(hass, broker):
    bulb(hass, broker, genOnOff={"onTime": 270})

    response = await call(hass, "get_attributes", {"entity_id": "light.lamp"})

    assert response == {"light.lamp": {**ROW, "timer": timer(27.0)}}


async def test_an_off_bulbs_brightness_is_z2ms_and_its_level_is_not_read(hass, broker):
    # Off with a transition: the bulb holds the minimum level, and Z2M the one it restores on the next on.
    bulb(hass, broker, genOnOff={"onOff": 0}, genLevelCtrl={"currentLevel": 1})
    broker.states[IEEE] = {"state": "OFF", "brightness": 152}

    response = await call(hass, "get_attributes", {"entity_id": "light.lamp"})

    assert response == {"light.lamp": ROW}
    assert [read["cluster"] for _, read in broker.reads()] == ["genOnOff", "lightingColorCtrl"]
    assert broker.states[IEEE]["brightness"] == 152


async def test_an_off_bulb_z2m_has_no_brightness_for_reports_none(hass, broker):
    bulb(hass, broker, genOnOff={"onOff": 0})
    broker.states[IEEE] = {"state": "OFF"}

    response = await call(hass, "get_attributes", {"entity_id": "light.lamp"})

    assert response == {"light.lamp": {"color_temp_kelvin": 4000}}


async def test_adds_the_read_key_to_filtered_cache_first_keeping_the_users(hass, broker, caplog):
    bulb(hass, broker)
    broker.retained[INFO]["config"]["devices"][IEEE] = {"filtered_cache": ["linkquality"]}

    with caplog.at_level(logging.INFO, logger=LOGGER):
        await call(hass, "get_attributes", {"entity_id": "light.lamp"})

    [request] = broker.requests()
    assert request["topic"] == DEVICE_REQUEST
    assert request["payload"]["id"] == IEEE
    assert request["payload"]["options"] == {"filtered_cache": ["linkquality", KEY]}
    assert broker.published.index(request) < next(i for i, p in enumerate(broker.published) if "read" in p["payload"])
    assert f"Added {KEY} to Zigbee2MQTT's filtered_cache for light.lamp" in caplog.text


async def test_reads_and_the_options_request_are_not_logged_as_sent(hass, broker, wire):
    bulb(hass, broker)
    broker.retained[INFO]["config"]["devices"][IEEE] = {"filtered_cache": ["linkquality"]}

    await call(hass, "get_attributes", {"entity_id": "light.lamp"})

    assert broker.requests() and broker.reads()
    assert sent(wire) == []


async def test_read_key_already_filtered_sends_no_request(hass, broker):
    bulb(hass, broker)
    broker.retained[INFO]["config"]["devices"][IEEE] = {"filtered_cache": [KEY]}

    await call(hass, "get_attributes", {"entity_id": "light.lamp"})

    assert broker.requests() == []


async def test_waits_for_bridge_state_after_subscribing_to_the_log(hass, broker):
    bulb(hass, broker)

    await call(hass, "get_attributes", {"entity_id": "light.lamp"})

    assert broker.topics[:2] == ["zigbee2mqtt/bridge/logging", "zigbee2mqtt/bridge/state"]


async def test_no_bridge_state_raises_without_reading(hass, broker, caplog):
    bulb(hass, broker)
    del broker.retained["zigbee2mqtt/bridge/state"]

    with pytest.raises(HomeAssistantError) as raised:
        await call(hass, "get_attributes", {"entity_id": "light.lamp"})

    assert raised.value.translation_key == "targets_failed"
    assert "no bridge/state from Zigbee2MQTT" in caplog.text
    assert broker.requests() == [] and broker.reads() == []
    assert not broker.listening("zigbee2mqtt/bridge/state") and not broker.listening("zigbee2mqtt/bridge/logging")


async def test_no_filtered_cache_raises_without_reading(hass, broker):
    bulb(hass, broker)
    broker.reply = {"status": "error", "error": "Device 'x' does not exist"}

    with pytest.raises(HomeAssistantError) as raised:
        await call(hass, "get_attributes", {"entity_id": "light.lamp"})

    assert raised.value.translation_key == "targets_failed"
    assert broker.reads() == []


async def test_a_publish_without_the_clusters_attributes_is_not_the_reply(hass, broker):
    bulb(hass, broker)
    # State from before the read, with an older, deep-merged key; then state that isn't JSON, or not an object.
    broker.before_reply = [{"state": "OFF"}, {KEY: {"onOff": 0, "onTime": 0, "currentLevel": 3}}, {KEY: 1}]

    response = await call(hass, "get_attributes", {"entity_id": "light.lamp"})

    assert response == {"light.lamp": ROW}


async def test_unreadable_state_is_not_the_reply(hass, broker):
    bulb(hass, broker)
    original = broker._read

    def read_after_junk(ieee, payload):
        for junk in ("not json", "[1]"):
            broker.deliver("zigbee2mqtt/lamp", junk)
        original(ieee, payload)

    broker._read = read_after_junk

    assert await call(hass, "get_attributes", {"entity_id": "light.lamp"}) == {"light.lamp": ROW}


async def test_a_retained_state_is_not_the_reply(hass, broker):
    bulb(hass, broker)
    broker.retained["zigbee2mqtt/lamp"] = {KEY: {"onOff": 0, "onTime": 0}}
    broker.devices.clear()  # Z2M never answers, so only the retained message could

    with pytest.raises(HomeAssistantError) as raised:
        await call(hass, "get_attributes", {"entity_id": "light.lamp"})

    assert raised.value.translation_key == "targets_failed"


async def test_no_reply_raises_and_unsubscribes(hass, broker, caplog):
    bulb(hass, broker)
    broker.devices.clear()

    with pytest.raises(HomeAssistantError) as raised:
        await call(hass, "get_attributes", {"entity_id": "light.lamp"})

    assert raised.value.translation_key == "targets_failed"
    assert "no genOnOff reply from Zigbee2MQTT" in caplog.text
    assert not broker.listening("zigbee2mqtt/lamp") and not broker.listening("zigbee2mqtt/bridge/logging")


async def test_a_logged_read_error_raises_at_once(hass, broker, monkeypatch, caplog):
    bulb(hass, broker)
    broker.read_errors[(IEEE, "lightingColorCtrl")] = "UNSUPPORTED_ATTRIBUTE"
    monkeypatch.setattr(read, "_REPLY_TIMEOUT_S", 30)

    with pytest.raises(HomeAssistantError) as raised:
        async with asyncio.timeout(5):
            await call(hass, "get_attributes", {"entity_id": "light.lamp"})

    assert raised.value.translation_key == "targets_failed"
    assert "lightingColorCtrl.read() failed (UNSUPPORTED_ATTRIBUTE)" in caplog.text


async def test_the_fake_broker_logs_a_read_of_an_attribute_the_bulb_lacks_as_failed(hass, broker, caplog):
    """The fixture's own reply (tests/fixtures/zigbee2mqtt.py, Broker._read), as Zigbee2MQTT answers for a bulb
    without the attribute: a failed read on bridge/logging, not a reply."""
    bulb(hass, broker)
    del broker.devices[IEEE][1]["genOnOff"]["onTime"]

    with pytest.raises(HomeAssistantError) as raised:
        await call(hass, "get_attributes", {"entity_id": "light.lamp"})

    assert raised.value.translation_key == "targets_failed"
    assert "genOnOff.read() failed (UNSUPPORTED_ATTRIBUTE)" in caplog.text


async def test_a_delivery_failure_is_read_again(hass, broker, caplog):
    bulb(hass, broker)
    broker.read_errors_then_reply[(IEEE, "genOnOff")] = ["Delivery failed for '1'"]

    await call(hass, "get_attributes", {"entity_id": "light.lamp"})

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


async def test_two_delivery_failures_fail_the_read(hass, broker, caplog):
    bulb(hass, broker)
    broker.read_errors_then_reply[(IEEE, "genOnOff")] = ["Delivery failed for '1'", "Delivery failed for '2'"]

    with pytest.raises(HomeAssistantError) as raised:
        await call(hass, "get_attributes", {"entity_id": "light.lamp"})

    assert raised.value.translation_key == "targets_failed"
    assert "genOnOff.read() failed (Delivery failed for '2')" in caplog.text


async def test_another_read_error_is_not_read_again(hass, broker):
    bulb(hass, broker)
    broker.read_errors_then_reply[(IEEE, "genOnOff")] = ["UNSUPPORTED_ATTRIBUTE"]

    with pytest.raises(HomeAssistantError):
        await call(hass, "get_attributes", {"entity_id": "light.lamp"})

    assert not broker.read_errors_then_reply[(IEEE, "genOnOff")]


async def test_log_lines_about_anything_else_are_ignored(hass, broker):
    bulb(hass, broker)
    other = "ZCL command 0x00124b00ffffffff/11 genOnOff.read() failed (Delivery failed for '1')"
    broker.logs_before_reply = [
        "not json",
        json.dumps(["not", "an", "entry"]),
        json.dumps({"level": "info", "message": f"Read result of {IEEE}/11 genOnOff.read()"}),
        json.dumps({"level": "error", "message": other}),
    ]
    # Delivered on subscribe, before any read is in flight.
    broker.retained["zigbee2mqtt/bridge/logging"] = {"level": "error", "message": f"{IEEE}/11 genOnOff.read() failed"}

    assert await call(hass, "get_attributes", {"entity_id": "light.lamp"}) == {"light.lamp": ROW}


BRIDGE_GROUPS = [{"id": 3, "members": []}, {"id": 7, "members": [{"ieee_address": "0x01"}, {"ieee_address": "0x02"}]}]


def group(hass, broker, one: dict, two: dict, members: list | None = None) -> str:
    """Group 7 with two members, each answering with BULB updated by its own clusters."""
    bulb(hass, broker, "light.one", ieee="0x01", modes=["color_temp"], **one)
    bulb(hass, broker, "light.two", ieee="0x02", modes=["color_temp"], **two)
    add_z2m_group(hass, hass.config_entries.async_entries("mqtt")[0], "light.room", group_id=7, name="room")
    broker.retained[GROUPS] = BRIDGE_GROUPS if members is None else [{"id": 7, "members": members}]
    return "light.room"


async def test_group_averages_its_lit_members(hass, broker):
    group(
        hass,
        broker,
        one={"genLevelCtrl": {"currentLevel": 254}, "lightingColorCtrl": {"colorTemperature": 200}},
        two={"genOnOff": {"onOff": 0}, "genLevelCtrl": {"currentLevel": 1}},
    )
    broker.retained[INFO]["config"]["devices"]["0x02"] = {"filtered_cache": [KEY]}

    response = await call(hass, "get_attributes", {"entity_id": "light.room"})

    assert response == {"light.room": {"brightness": 100.0, "color_temp_kelvin": 5000}}
    assert [topic for topic, _ in broker.reads()] == ["zigbee2mqtt/0x01/set"] * 3 + ["zigbee2mqtt/0x02/set"] * 2
    [request] = broker.requests()  # one bridge/info read covers both members; the second already has the key
    assert request["payload"]["id"] == "0x01"


async def test_group_with_every_member_off_averages_them_all(hass, broker):
    group(
        hass,
        broker,
        one={"genOnOff": {"onOff": 0}, "genLevelCtrl": {"currentLevel": 1}},
        two={"genOnOff": {"onOff": 0}, "genLevelCtrl": {"currentLevel": 1}},
    )
    broker.states = {"0x01": {"state": "OFF", "brightness": 254}, "0x02": {"state": "OFF", "brightness": 127}}

    response = await call(hass, "get_attributes", {"entity_id": "light.room"})

    assert response == {"light.room": {"brightness": 75.0, "color_temp_kelvin": 4000}}


async def test_group_reports_the_longest_timer_when_every_lit_member_runs_one(hass, broker):
    group(hass, broker, one={"genOnOff": {"onTime": 150}}, two={"genOnOff": {"onTime": 200}})

    response = await call(hass, "get_attributes", {"entity_id": "light.room"})

    assert response["light.room"]["timer"] == timer(20.0)


async def test_group_reports_no_timer_when_a_lit_member_runs_none(hass, broker):
    group(hass, broker, one={"genOnOff": {"onTime": 150}}, two={})

    response = await call(hass, "get_attributes", {"entity_id": "light.room"})

    assert "timer" not in response["light.room"]


async def test_group_leaves_out_unavailable_members_and_those_ha_lacks(hass, broker):
    group(
        hass,
        broker,
        one={},
        two={"genLevelCtrl": {"currentLevel": 1}},
        members=[{"ieee_address": "0x01"}, {"ieee_address": "0x02"}, {"ieee_address": "0x03"}, {}],
    )
    hass.states.async_set("light.two", "unavailable")
    # A member HA lacks leaves membership unknowable (members.expand_group), so the group runs only flagged.
    allow_experimental(hass)

    response = await call(hass, "get_attributes", {"entity_id": "light.room"})

    assert response == {"light.room": ROW | {"color_temp_kelvin": 4000}}
    assert {topic for topic, _ in broker.reads()} == {"zigbee2mqtt/0x01/set"}


async def test_a_group_whose_members_are_all_unavailable_is_skipped_as_empty(hass, broker):
    """The dispatcher plans it, as bridge/groups lists members, but there is no light to read: nothing answers."""
    group(hass, broker, one={}, two={})
    hass.states.async_set("light.one", "unavailable")
    hass.states.async_set("light.two", "unavailable")

    with pytest.raises(HomeAssistantError) as raised:
        await call(hass, "get_attributes", {"entity_id": "light.room"})

    assert raised.value.translation_key == "no_entities_matched"
    assert broker.reads() == []


async def test_a_member_given_the_key_is_logged_when_the_next_one_fails(hass, broker, caplog):
    group(hass, broker, one={}, two={})
    broker.refused = {"0x02"}

    with caplog.at_level(logging.INFO, logger=LOGGER), pytest.raises(HomeAssistantError):
        await call(hass, "get_attributes", {"entity_id": "light.room"})

    assert f"Added {KEY} to Zigbee2MQTT's filtered_cache for light.one" in caplog.text
    assert "filtered_cache for light.two" not in caplog.text
    assert broker.reads() == []


async def test_member_failure_fails_the_group(hass, broker):
    group(hass, broker, one={}, two={})
    broker.read_errors[("0x02", "genOnOff")] = "Delivery failed for '1'"

    with pytest.raises(HomeAssistantError) as raised:
        await call(hass, "get_attributes", {"entity_id": "light.room"})

    assert raised.value.translation_key == "targets_failed"


async def test_empty_group_is_skipped_silently(hass, broker, caplog):
    group(hass, broker, one={}, two={}, members=[])
    broker.retained[GROUPS] = [{"id": 7, "members": []}]

    response = await call(hass, "get_attributes", {"entity_id": ["light.room", "light.one"]})

    assert response == {"light.one": ROW}
    assert "skipped" not in caplog.text


async def test_a_group_bridge_groups_doesn_t_list_is_experimental(hass, broker):
    """Membership unknown, not empty: Z2M may not have published the group yet."""
    group(hass, broker, one={}, two={}, members=[])
    broker.retained[GROUPS] = [{"id": 3, "members": []}]

    assert await refusal(hass, "get_attributes", {"entity_id": "light.room"}) == "experimental_off"


async def test_no_bridge_groups_fails_the_group(hass, broker):
    group(hass, broker, one={}, two={})
    del broker.retained[GROUPS]
    assert await refusal(hass, "get_attributes", {"entity_id": "light.room"}) == "experimental_off"
    allow_experimental(hass)

    with pytest.raises(HomeAssistantError) as raised:
        await call(hass, "get_attributes", {"entity_id": "light.room"})

    assert raised.value.translation_key == "targets_failed"


async def test_group_on_another_base_topic_is_not_addressable(hass, broker):
    add_z2m_group(hass, z2m_entry(hass), "light.room", group_id=7, base_topic="z2m_upstairs", state="on")

    assert await refusal(hass, "get_attributes", {"entity_id": "light.room"}) == "not_addressable"


async def test_device_without_a_name_is_not_addressable(hass, broker):
    add_z2m_entity(hass, z2m_entry(hass), "light.lamp", state="on")

    assert await refusal(hass, "get_attributes", {"entity_id": "light.lamp"}) == "not_addressable"


async def test_switch_is_experimental_and_reads_only_on_off(hass, broker):
    bulb(hass, broker, "switch.plug", modes=[], genOnOff={"onTime": 50})

    assert await refusal(hass, "get_attributes", {"entity_id": "switch.plug"}) == "experimental_off"
    assert not broker.reads()

    allow_experimental(hass)
    assert await call(hass, "get_attributes", {"entity_id": "switch.plug"}) == {"switch.plug": {"timer": timer(5.0)}}
    assert broker.reads() == [
        (SET, {"cluster": "genOnOff", "attributes": ["onOff", "onTime"], "state_property": KEY}),
    ]
