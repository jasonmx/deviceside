"""Zigbee2MQTT turn_on_for: optional light.turn_on then mqtt.publish with on_time."""

import json
import logging
from types import SimpleNamespace

import pytest
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.deviceside.integrations.zigbee2mqtt import bridge, turn_on_for

from .conftest import call
from ...conftest import advance, allow_experimental, refusal, sent, wall_clock_now
from ...fixtures.zigbee2mqtt import IEEE, add_z2m_entity, add_z2m_group, z2m_entry

SENT = {"status": "sent", "integration": "zigbee2mqtt"}
TOPIC = f"zigbee2mqtt/{IEEE}/set"
GET_TOPIC = f"zigbee2mqtt/{IEEE}/get"


@pytest.fixture
def publishes(hass):
    return async_mock_service(hass, "mqtt", "publish")


@pytest.fixture
def turn_ons(hass):
    return async_mock_service(hass, "light", "turn_on")


@pytest.fixture
def listed(monkeypatch):
    """A group whose membership turn_on_for takes as known, for a test of its call alone: unknown membership is
    refused (test_classes.py), and the dispatcher still counts the group as Experimental, as bridge/groups isn't
    held."""
    monkeypatch.setattr(turn_on_for, "expand_group", lambda hass, target: [])


async def test_light_without_fields_publishes_on_time(hass, setup_production, publishes, turn_ons):
    entry = z2m_entry(hass)
    add_z2m_entity(hass, entry, "light.kitchen")

    response = await call(hass, "turn_on_for", {"entity_id": "light.kitchen", "duration": 5})

    assert response == {"light.kitchen": SENT}
    assert not turn_ons
    assert len(publishes) == 1
    assert publishes[0].data["topic"] == TOPIC
    assert json.loads(publishes[0].data["payload"]) == {"state": "ON", "on_time": 5.0}


async def test_light_with_fields_calls_turn_on_with_them_all_then_publishes_on_time(
    hass, setup_production, publishes, turn_ons
):
    entry = z2m_entry(hass)
    add_z2m_entity(hass, entry, "light.kitchen")
    data = {"entity_id": "light.kitchen", "duration": 10, "brightness": 80, "color_temp_kelvin": 3000}

    response = await call(hass, "turn_on_for", data)

    assert response == {"light.kitchen": SENT}
    assert len(turn_ons) == 1
    assert turn_ons[0].data == {"entity_id": "light.kitchen", "brightness": 204, "color_temp_kelvin": 3000}
    assert len(publishes) == 1
    assert publishes[0].data["topic"] == TOPIC
    assert json.loads(publishes[0].data["payload"]) == {"state": "ON", "on_time": 10.0}


async def test_switch_is_experimental(hass, setup_production, publishes, turn_ons):
    add_z2m_entity(hass, z2m_entry(hass), "switch.plug")
    data = {"entity_id": "switch.plug", "duration": 5}

    assert await refusal(hass, "turn_on_for", data) == "experimental_off"
    assert not publishes

    allow_experimental(hass)
    assert await call(hass, "turn_on_for", data) == {"switch.plug": SENT}
    assert not turn_ons
    assert publishes[0].data["topic"] == TOPIC
    assert json.loads(publishes[0].data["payload"]) == {"state": "ON", "on_time": 5.0}


@pytest.mark.parametrize(
    ("duration", "on_time", "clamped_to"),
    [
        pytest.param({"milliseconds": 1500}, 1.5, None, id="rounded_to_one_decimal_place"),
        pytest.param({"hours": 2}, 6553.4, 6553.4, id="clamped_to_z2m_max"),
        pytest.param({"milliseconds": 20}, 0.1, 0.05, id="below_50ms_clamped"),
    ],
)
async def test_duration_is_rounded_and_clamped(
    hass, setup_production, publishes, turn_ons, duration, on_time, clamped_to
):
    add_z2m_entity(hass, z2m_entry(hass), "light.kitchen")

    response = await call(hass, "turn_on_for", {"entity_id": "light.kitchen", "duration": duration})

    assert response == {"light.kitchen": SENT if clamped_to is None else {**SENT, "clamped_to": clamped_to}}
    assert json.loads(publishes[0].data["payload"]) == {"state": "ON", "on_time": on_time}


async def test_non_z2m_mqtt_entity_is_refused(hass, setup_production, publishes, turn_ons):
    """A named MQTT entity whose device lacks the zigbee2mqtt_ identifier raises TargetNotSupported."""
    entry = z2m_entry(hass)
    other_device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("mqtt", "some_other_bridge_0x1234")},
    )
    er.async_get(hass).async_get_or_create(
        "light",
        "mqtt",
        "other_ieee",
        suggested_object_id="other_light",
        config_entry=entry,
        device_id=other_device.id,
    )
    hass.states.async_set("light.other_light", "off")
    add_z2m_entity(hass, entry, "light.kitchen")

    reason = await refusal(hass, "turn_on_for", {"entity_id": "light.other_light", "duration": 5})
    assert reason == "integration_not_supported"
    response = await call(hass, "turn_on_for", {"entity_id": "light.kitchen", "duration": 5})
    assert response["light.kitchen"] == SENT


async def test_publish_error_rejects_that_target(hass, setup_production, turn_ons):
    entry = z2m_entry(hass)
    add_z2m_entity(hass, entry, "light.kitchen")
    async_mock_service(hass, "mqtt", "publish", raise_exception=Exception("broker unreachable"))

    with pytest.raises(HomeAssistantError) as raised:
        await call(hass, "turn_on_for", {"entity_id": "light.kitchen", "duration": 5})

    assert raised.value.translation_key == "targets_failed"


async def test_turn_on_error_rejects_that_target(hass, setup_production, publishes):
    """light.turn_on failure before mqtt.publish propagates as REJECTED."""
    entry = z2m_entry(hass)
    add_z2m_entity(hass, entry, "light.kitchen")
    async_mock_service(hass, "light", "turn_on", raise_exception=Exception("entity unavailable"))

    with pytest.raises(HomeAssistantError) as raised:
        await call(hass, "turn_on_for", {"entity_id": "light.kitchen", "duration": 5, "brightness": 50})

    assert raised.value.translation_key == "targets_failed"
    assert not publishes  # publish was never reached


async def test_group_with_fields_calls_turn_on_then_publishes(hass, setup_production, publishes, turn_ons, listed):
    add_z2m_group(hass, z2m_entry(hass), "light.downstairs", group_id=7)
    allow_experimental(hass)

    response = await call(hass, "turn_on_for", {"entity_id": "light.downstairs", "duration": 5, "brightness": 50})

    assert response == {"light.downstairs": SENT}
    assert turn_ons[0].data == {"entity_id": "light.downstairs", "brightness": 128}
    assert publishes[0].data["topic"] == "zigbee2mqtt/7/set"


async def test_group_on_custom_base_topic_is_not_addressable(hass, setup_production, publishes, turn_ons):
    add_z2m_group(hass, z2m_entry(hass), "light.downstairs", group_id=7, base_topic="z2m")

    assert await refusal(hass, "turn_on_for", {"entity_id": "light.downstairs", "duration": 5}) == "not_addressable"
    assert not publishes


async def test_device_and_group_each_get_their_own_topic(hass, setup_production, publishes, turn_ons, listed):
    entry = z2m_entry(hass)
    add_z2m_entity(hass, entry, "light.kitchen")
    add_z2m_group(hass, entry, "light.downstairs", group_id=7)
    allow_experimental(hass)

    response = await call(hass, "turn_on_for", {"entity_id": ["light.kitchen", "light.downstairs"], "duration": 5})

    assert response == {"light.kitchen": SENT, "light.downstairs": SENT}
    assert {p.data["topic"] for p in publishes} == {TOPIC, "zigbee2mqtt/7/set"}


# --- Waiting for Z2M's state before the timer ---

STATE_TOPIC = "zigbee2mqtt/Kitchen"


class StateTopic:
    """Z2M's state topic as the adapter sees it: light.turn_on answered by a state publish, or by nothing."""

    def __init__(self, hass) -> None:
        self.hass = hass
        self.subscribers: dict[str, list] = {}
        self.events: list[str] = []  # "turn_on", "state" and "timer", in the order they happened
        self.answer: bool = True
        self.retained: str | None = None  # delivered on subscribe, as a broker resends a retained message

    async def subscribe(self, hass, topic, msg_callback):
        self.subscribers.setdefault(topic, []).append(msg_callback)
        if self.retained is not None:
            msg_callback(SimpleNamespace(topic=topic, payload=self.retained, retain=True))
        return lambda: self.subscribers[topic].remove(msg_callback)

    @callback
    def turn_on(self, call) -> None:
        self.events.append("turn_on")
        if self.answer:
            self.hass.loop.call_later(0.01, self.publish_state)

    def publish_state(self) -> None:
        self.events.append("state")
        for msg_callback in list(self.subscribers.get(STATE_TOPIC, [])):
            msg_callback(SimpleNamespace(topic=STATE_TOPIC, payload='{"state":"ON"}', retain=False))

    @callback
    def publish(self, call) -> None:
        if json.loads(call.data["payload"]).get("on_time") is not None:
            self.events.append("timer")


@pytest.fixture
def z2m(hass, monkeypatch):
    fake = StateTopic(hass)
    monkeypatch.setattr(bridge.mqtt, "async_subscribe", fake.subscribe)
    monkeypatch.setattr(turn_on_for, "_STATE_TIMEOUT_S", 0.05)
    hass.services.async_register("light", "turn_on", fake.turn_on)
    hass.services.async_register("mqtt", "publish", fake.publish)
    return fake


async def test_timer_waits_for_the_state_z2m_publishes_after_turn_on(hass, setup_production, z2m):
    add_z2m_entity(hass, z2m_entry(hass), "light.kitchen", name="Kitchen")

    response = await call(hass, "turn_on_for", {"entity_id": "light.kitchen", "duration": 5, "xy_color": [0.15, 0.06]})

    assert response == {"light.kitchen": SENT}
    assert z2m.events == ["turn_on", "state", "timer"]
    assert z2m.subscribers[STATE_TOPIC] == []


async def test_a_retained_state_is_not_the_answer(hass, setup_production, z2m):
    add_z2m_entity(hass, z2m_entry(hass), "light.kitchen", name="Kitchen")
    z2m.retained = '{"state":"OFF"}'

    await call(hass, "turn_on_for", {"entity_id": "light.kitchen", "duration": 5, "brightness": 50})

    assert z2m.events == ["turn_on", "state", "timer"]


async def test_no_state_by_the_timeout_still_sends_the_timer(hass, setup_production, z2m, caplog):
    add_z2m_entity(hass, z2m_entry(hass), "light.kitchen", name="Kitchen")
    z2m.answer = False

    response = await call(hass, "turn_on_for", {"entity_id": "light.kitchen", "duration": 5, "brightness": 50})

    assert response == {"light.kitchen": SENT}
    assert z2m.events == ["turn_on", "timer"]
    assert (
        "No state from Zigbee2MQTT for light.kitchen in 0.05 s, "
        "so its timer starts late and it stays on that much longer" in caplog.text
    )
    assert z2m.subscribers[STATE_TOPIC] == []


async def test_a_failed_turn_on_unsubscribes_and_rejects(hass, setup_production, z2m):
    add_z2m_entity(hass, z2m_entry(hass), "light.kitchen", name="Kitchen")
    async_mock_service(hass, "light", "turn_on", raise_exception=Exception("entity unavailable"))

    with pytest.raises(HomeAssistantError) as raised:
        await call(hass, "turn_on_for", {"entity_id": "light.kitchen", "duration": 5, "brightness": 50})

    assert raised.value.translation_key == "targets_failed"
    assert "timer" not in z2m.events
    assert z2m.subscribers[STATE_TOPIC] == []


async def test_a_group_waits_on_its_own_state_topic(hass, setup_production, z2m, listed):
    add_z2m_group(hass, z2m_entry(hass), "light.downstairs", group_id=7, name="Kitchen")
    allow_experimental(hass)

    await call(hass, "turn_on_for", {"entity_id": "light.downstairs", "duration": 5, "brightness": 50})

    assert z2m.events == ["turn_on", "state", "timer"]


async def test_without_fields_nothing_is_subscribed(hass, setup_production, z2m):
    add_z2m_entity(hass, z2m_entry(hass), "light.kitchen", name="Kitchen")

    await call(hass, "turn_on_for", {"entity_id": "light.kitchen", "duration": 5})

    assert z2m.events == ["timer"]
    assert z2m.subscribers == {}


# --- State read-backs ---


def _reads(publishes) -> list[str]:
    """Topics of the read-backs published so far, each checked to carry the read payload."""
    reads = [p for p in publishes if p.data["topic"].endswith("/get")]
    assert all(json.loads(p.data["payload"]) == {"state": ""} for p in reads)
    return [p.data["topic"] for p in reads]


async def test_without_fields_reads_on_then_twice_after_the_timer(hass, setup_production, publishes, turn_ons):
    add_z2m_entity(hass, z2m_entry(hass), "light.kitchen")
    start = wall_clock_now()

    await call(hass, "turn_on_for", {"entity_id": "light.kitchen", "duration": 10})
    assert _reads(publishes) == []

    await advance(hass, start, 1.5)
    assert _reads(publishes) == [GET_TOPIC]
    await advance(hass, start, 10.5)
    assert _reads(publishes) == [GET_TOPIC]
    await advance(hass, start, 11.5)
    assert _reads(publishes) == [GET_TOPIC] * 2
    await advance(hass, start, 15.5)
    assert _reads(publishes) == [GET_TOPIC] * 3


async def test_with_fields_reads_only_after_the_timer(hass, setup_production, publishes, turn_ons):
    add_z2m_entity(hass, z2m_entry(hass), "light.kitchen")
    start = wall_clock_now()

    await call(hass, "turn_on_for", {"entity_id": "light.kitchen", "duration": 10, "brightness": 50})

    await advance(hass, start, 1.5)
    assert _reads(publishes) == []
    await advance(hass, start, 15.5)
    assert _reads(publishes) == [GET_TOPIC] * 2


async def test_late_end_read_scales_with_a_long_timer(hass, setup_production, publishes, turn_ons):
    """2% of 1000 s is 20 s, past the 5 s floor."""
    add_z2m_entity(hass, z2m_entry(hass), "light.kitchen")
    start = wall_clock_now()

    await call(hass, "turn_on_for", {"entity_id": "light.kitchen", "duration": 1000})

    await advance(hass, start, 1001.5)
    assert len(_reads(publishes)) == 2  # the on read and the first end read
    await advance(hass, start, 1019.5)
    assert len(_reads(publishes)) == 2
    await advance(hass, start, 1020.5)
    assert len(_reads(publishes)) == 3


async def test_group_reads_its_group_topic(hass, setup_production, publishes, turn_ons, listed):
    add_z2m_group(hass, z2m_entry(hass), "light.downstairs", group_id=7)
    allow_experimental(hass)
    start = wall_clock_now()

    await call(hass, "turn_on_for", {"entity_id": "light.downstairs", "duration": 5})
    await advance(hass, start, 10.5)

    assert _reads(publishes) == ["zigbee2mqtt/7/get"] * 3


async def test_a_second_call_replaces_the_pending_reads_of_that_target_only(
    hass, setup_production, publishes, turn_ons
):
    other_get = "zigbee2mqtt/0x00124b0000000002/get"
    entry = z2m_entry(hass)
    add_z2m_entity(hass, entry, "light.kitchen")
    add_z2m_entity(hass, entry, "light.hall", ieee="0x00124b0000000002")
    start = wall_clock_now()

    await call(hass, "turn_on_for", {"entity_id": ["light.kitchen", "light.hall"], "duration": 10})
    await call(hass, "turn_on_for", {"entity_id": "light.kitchen", "duration": 100, "brightness": 50})

    await advance(hass, start, 15.5)
    assert _reads(publishes) == [other_get] * 3  # the kitchen's first schedule never fired
    await advance(hass, start, 105.5)
    assert _reads(publishes) == [other_get] * 3 + [GET_TOPIC] * 2


async def test_unload_cancels_pending_reads(hass, setup_production, publishes, turn_ons):
    add_z2m_entity(hass, z2m_entry(hass), "light.kitchen")
    start = wall_clock_now()

    await call(hass, "turn_on_for", {"entity_id": "light.kitchen", "duration": 5})
    assert await hass.config_entries.async_unload(setup_production.entry_id)
    await advance(hass, start, 10.5)

    assert _reads(publishes) == []


async def test_a_failed_read_is_logged_and_the_result_stays_sent(hass, setup_production, turn_ons, caplog):
    add_z2m_entity(hass, z2m_entry(hass), "light.kitchen")
    start = wall_clock_now()
    sets = []

    async def publish(service_call):
        if service_call.data["topic"].endswith("/get"):
            raise HomeAssistantError("broker unreachable")
        sets.append(service_call)

    hass.services.async_register("mqtt", "publish", publish)

    response = await call(hass, "turn_on_for", {"entity_id": "light.kitchen", "duration": 5})
    with caplog.at_level(logging.DEBUG, logger="custom_components.deviceside.integrations.zigbee2mqtt"):
        await advance(hass, start, 10.5)

    assert response == {"light.kitchen": SENT}
    assert len(sets) == 1
    assert caplog.text.count("State read for light.kitchen failed: HomeAssistantError('broker unreachable')") == 3


async def test_the_fields_and_the_timer_are_logged_without_the_topic(hass, setup_production, publishes, turn_ons, wire):
    add_z2m_entity(hass, z2m_entry(hass), "light.kitchen")

    await call(hass, "turn_on_for", {"entity_id": "light.kitchen", "duration": 10, "brightness": 50})

    assert [line.split(" with ")[0] for line in sent(wire)] == [
        "light.kitchen: sending ha light.turn_on {'brightness': 128}",
        "light.kitchen: sending mqtt set {'state': 'ON', 'on_time': 10.0}",
    ]
