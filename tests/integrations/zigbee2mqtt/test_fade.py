"""Zigbee2MQTT raise and lower share their fade: color_hs, addressing, predictions and the end read-backs."""

import asyncio

import pytest
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.deviceside.core import ha_watch
from custom_components.deviceside.integrations.zigbee2mqtt import bridge, members
from custom_components.deviceside.integrations.zigbee2mqtt.fade import hue_moves, transition_cache

from .conftest import GET, SENT, SET, call, commands, group, light
from ...conftest import advance, refusal, result, wall_clock_now
from ...fixtures.zigbee2mqtt import add_z2m_group, z2m_entry

pytestmark = pytest.mark.usefixtures("setup_production", "clock")

COLOR_ACTIONS = ["raise_saturation", "lower_saturation", "raise_hue", "lower_hue"]


@pytest.mark.parametrize("action", COLOR_ACTIONS)
async def test_color_is_dropped_without_color_hs(hass, broker, action):
    entity_id = light(hass, broker, color_hs=False)

    response = await call(hass, action, {"entity_id": entity_id})

    assert response == {entity_id: result("zigbee2mqtt", "unchanged", detail="fields_dropped")}
    assert commands(broker) == []


@pytest.mark.parametrize("action", ["raise_hue", "raise_saturation"])
async def test_an_off_light_is_unchanged(hass, broker, action):
    """One row per branch: each lower shares its raise's."""
    entity_id = light(hass, broker, state="off")

    response = await call(hass, action, {"entity_id": entity_id})

    assert response == {entity_id: result("zigbee2mqtt", "unchanged", detail="off")}
    assert commands(broker) == []


async def test_brightness_moves_without_color_hs(hass, broker):
    entity_id = light(hass, broker, color_hs=False)

    response = await call(hass, "raise_brightness", {"entity_id": entity_id})

    assert response == {entity_id: SENT}
    assert commands(broker) == [(SET, {"state": "ON", "brightness": 254, "transition": 2.0})]


async def test_a_group_with_a_member_without_color_hs_drops_hue(hass, broker):
    # Z2M gives a group only the features every member shares.
    room = group(hass, broker, one={}, two={"color_hs": False})

    response = await call(hass, "raise_hue", {"entity_id": room})

    assert response == {room: result("zigbee2mqtt", "unchanged", detail="fields_dropped")}
    assert commands(broker) == []


@pytest.mark.parametrize("action", ["raise_brightness", "lower_brightness", *COLOR_ACTIONS, "stop"])
async def test_a_group_on_another_base_topic_is_refused(hass, broker, action):
    entry = z2m_entry(hass)
    add_z2m_group(hass, entry, "light.room", group_id=7, base_topic="z2m", state="on")

    assert await refusal(hass, action, {"entity_id": "light.room"}) == "not_addressable"


async def test_a_move_after_a_stop_starts_where_it_halted(hass, broker, clock):
    entity_id = light(hass, broker)
    await call(hass, "raise_brightness", {"entity_id": entity_id})
    hass.states.async_set(entity_id, "on", {"brightness": 255, "hs_color": (200.0, 60.0)})  # Z2M's end value
    clock.now += 1  # halfway from 60 % to 100 %

    await call(hass, "stop", {"entity_id": entity_id})
    await call(hass, "lower_brightness", {"entity_id": entity_id})

    assert commands(broker)[-1] == (SET, {"state": "OFF", "transition": 4.0})


async def test_a_saturation_move_starts_from_its_prediction(hass, broker, clock):
    entity_id = light(hass, broker)
    await call(hass, "raise_saturation", {"entity_id": entity_id})
    clock.now += 1  # halfway from 60 % to 100 %

    await call(hass, "lower_saturation", {"entity_id": entity_id})

    assert commands(broker)[-1] == (SET, {"color": {"hue": 200.0, "saturation": 0.0}, "transition": 4.0})


async def test_an_empty_group_is_skipped(hass, broker):
    room = group(hass, broker, one={}, two={})
    light(hass, broker)
    hass.states.async_set("light.one", "unavailable")
    hass.states.async_set("light.two", "unavailable")

    response = await call(hass, "raise_brightness", {"entity_id": [room, "light.lamp"]})

    assert response == {"light.lamp": SENT}
    assert [topic for topic, _ in commands(broker)] == [SET]


async def test_a_failed_read_back_is_logged(hass, broker, caplog):
    entity_id = light(hass, broker)
    start = wall_clock_now()
    await call(hass, "raise_brightness", {"entity_id": entity_id})

    def refuse(service):
        raise RuntimeError("broker gone")

    hass.services.async_register("mqtt", "publish", refuse)
    with caplog.at_level("DEBUG"):
        await advance(hass, start, 3.5)

    assert "End read-back for light.lamp failed: RuntimeError('broker gone')" in caplog.text


async def test_a_new_move_replaces_the_pending_read_back(hass, broker):
    entity_id = light(hass, broker)
    start = wall_clock_now()
    await call(hass, "raise_brightness", {"entity_id": entity_id})

    await call(hass, "lower_brightness", {"entity_id": entity_id, "limit": 40})

    # The second fade, from the first's start at the same instant, runs 1 s.
    await advance(hass, start, 2.5)
    assert commands(broker)[2:] == [(GET, {"state": "", "brightness": ""})]
    await advance(hass, start, 3.5)
    assert commands(broker)[2:] == [(GET, {"state": "", "brightness": ""})]


async def test_a_move_of_another_attribute_keeps_the_pending_read_back(hass, broker):
    # Brightness and saturation fades run side by side: each is read back at its own end.
    entity_id = light(hass, broker, color={"colorMode": 1})
    start = wall_clock_now()
    await call(hass, "raise_brightness", {"entity_id": entity_id})  # 2 s
    await call(hass, "lower_saturation", {"entity_id": entity_id, "limit": 20})  # 2 s

    await advance(hass, start, 3.5)

    assert sorted(commands(broker)[2:], key=str) == [
        (GET, {"color": ""}),
        (GET, {"state": "", "brightness": ""}),
    ]


async def test_a_light_call_from_ha_mid_fade_drops_the_prediction(hass, broker, clock):
    """The next move plans from HA's state, not from where our fade would have the light."""
    entity_id = light(hass, broker)  # 60 %
    await call(hass, "raise_brightness", {"entity_id": entity_id, "sweep_time": 10})
    clock.now += 2  # predicted at 80 %
    async_mock_service(hass, "light", "turn_on")

    await hass.services.async_call("light", "turn_on", {"entity_id": entity_id, "brightness_pct": 30}, blocking=True)
    light(hass, broker, brightness=round(0.3 * 255))
    await call(hass, "lower_brightness", {"entity_id": entity_id, "sweep_time": 10})

    assert commands(broker)[-1] == (SET, {"state": "OFF", "transition": 3.0})


async def test_a_stop_after_a_light_call_from_ha_halts_from_has_state(hass, broker, clock):
    entity_id = light(hass, broker)
    await call(hass, "raise_brightness", {"entity_id": entity_id})
    clock.now += 1
    async_mock_service(hass, "light", "turn_on")

    await hass.services.async_call("light", "turn_on", {"entity_id": entity_id}, blocking=True)
    await call(hass, "stop", {"entity_id": entity_id})

    assert not transition_cache(hass).follows(entity_id)  # no halt predicted: Z2M's read-back is right


async def test_a_group_call_from_ha_drops_its_members_once_settled(hass, broker):
    room = group(hass, broker, one={}, two={})
    await call(hass, "raise_brightness", {"entity_id": room})
    cache = transition_cache(hass)
    assert cache.follows("light.one") and cache.follows("light.two")
    async_mock_service(hass, "light", "turn_off")

    await hass.services.async_call("light", "turn_off", {"entity_id": room}, blocking=True)
    await ha_watch.settled(hass)

    assert not cache.follows("light.one")
    assert not cache.follows("light.two")


async def test_a_move_after_a_group_call_from_ha_plans_after_its_forget(hass, broker, clock, monkeypatch):
    """The members come from a retained message, so the forget is async: the dispatcher waits for it."""
    room = group(hass, broker, one={}, two={})
    await call(hass, "raise_brightness", {"entity_id": room, "sweep_time": 10})  # 60 to 100 % in 4 s
    clock.now += 2
    released = asyncio.Event()
    group_members = bridge.group_members

    async def slow_members(hass, group_id):
        await released.wait()
        return await group_members(hass, group_id)

    monkeypatch.setattr(members, "group_members", slow_members)
    async_mock_service(hass, "light", "turn_on")
    await hass.services.async_call("light", "turn_on", {"entity_id": room}, blocking=True)
    assert transition_cache(hass).follows("light.one")  # still resolving

    lowering = hass.async_create_task(call(hass, "lower_brightness", {"entity_id": room, "sweep_time": 10}))
    await asyncio.sleep(0)
    released.set()
    await lowering

    # From HA's 60 %, not the predicted 80 %.
    assert commands(broker)[-1] == ("zigbee2mqtt/7/set", {"state": "OFF", "transition": 6.0})


@pytest.mark.parametrize(
    ("service", "data", "cycling"),
    [
        pytest.param("turn_on", {"brightness_pct": 40}, {"light.lamp"}, id="brightness_only"),
        pytest.param("turn_on", {"xy_color": [0.3, 0.3]}, set(), id="color"),
        pytest.param("turn_off", {}, set(), id="off"),
    ],
)
async def test_a_light_call_from_ha_that_sets_a_color_ends_the_hue_cycle(hass, broker, service, data, cycling):
    entity_id = light(hass, broker)
    await call(hass, "raise_hue", {"entity_id": entity_id})
    async_mock_service(hass, "light", service)

    await hass.services.async_call("light", service, {"entity_id": entity_id, **data}, blocking=True)

    assert hue_moves(hass) == cycling


@pytest.mark.parametrize(("data", "cycling"), [({}, {"light.lamp"}), ({"hs_color": [30, 80]}, set())])
async def test_a_call_to_all_lights_drops_every_z2m_prediction(hass, broker, data, cycling):
    entity_id = light(hass, broker)
    await call(hass, "raise_hue", {"entity_id": entity_id})
    await call(hass, "raise_brightness", {"entity_id": entity_id})
    async_mock_service(hass, "light", "turn_on")

    await hass.services.async_call("light", "turn_on", {"entity_id": "all", **data}, blocking=True)

    assert not transition_cache(hass).follows(entity_id)
    assert hue_moves(hass) == cycling
