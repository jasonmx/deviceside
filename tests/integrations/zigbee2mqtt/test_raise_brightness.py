"""Zigbee2MQTT raise_brightness: the end point with the time left, turning the light on, and its read-back."""

import pytest

from custom_components.deviceside.core.transition import BRIGHTNESS
from custom_components.deviceside.integrations.zigbee2mqtt.fade import transition_cache

from .conftest import BOTTOM, GET, SENT, SET, call, commands, group, light
from ...conftest import advance, result, wall_clock_now

pytestmark = pytest.mark.usefixtures("setup_production", "clock")


@pytest.mark.parametrize(
    ("data", "payload"),
    [
        # From 60 %, the top of a 5 s sweep is 2 s away: the speed is the same wherever the move starts.
        pytest.param({}, {"state": "ON", "brightness": 254, "transition": 2.0}, id="turns_on"),
        pytest.param({"limit": 80}, {"state": "ON", "brightness": 203, "transition": 1.0}, id="to_a_limit"),
    ],
)
async def test_publishes_the_end_point_with_the_time_left(hass, broker, data, payload):
    entity_id = light(hass, broker)

    response = await call(hass, "raise_brightness", {"entity_id": entity_id, **data})

    assert response == {entity_id: SENT}
    assert commands(broker) == [(SET, payload)]


async def test_at_the_top_is_unchanged(hass, broker):
    entity_id = light(hass, broker, brightness=255)

    response = await call(hass, "raise_brightness", {"entity_id": entity_id})

    assert response == {entity_id: result("zigbee2mqtt", "unchanged", detail="at_limit")}
    assert commands(broker) == []


async def test_from_off_starts_at_the_bottom(hass, broker):
    # An off light keeps its level, which a fade from off would start at: it is turned on at the bottom first.
    entity_id = light(hass, broker, state="off")

    await call(hass, "raise_brightness", {"entity_id": entity_id})

    assert commands(broker) == [(SET, BOTTOM), (SET, {"state": "ON", "brightness": 254, "transition": 5.0})]


async def test_a_group_with_an_off_member_moves_it_up_from_the_bottom(hass, broker, clock):
    room = group(hass, broker, one={"brightness": 204}, two={"state": "off"})

    await call(hass, "raise_brightness", {"entity_id": room})

    # From the lit member's 80 %: 1 s. Only the off member is put at the bottom first.
    assert commands(broker) == [
        ("zigbee2mqtt/0x02/set", BOTTOM),
        ("zigbee2mqtt/7/set", {"state": "ON", "brightness": 254, "transition": 1.0}),
    ]
    clock.now += 0.5
    assert transition_cache(hass).value("light.two", BRIGHTNESS, -1) == pytest.approx(50.0)


async def test_a_group_all_off_is_put_at_the_bottom_with_one_publish(hass, broker):
    room = group(hass, broker, one={"state": "off"}, two={"state": "off"})

    await call(hass, "raise_brightness", {"entity_id": room})

    assert commands(broker) == [
        ("zigbee2mqtt/7/set", BOTTOM),
        ("zigbee2mqtt/7/set", {"state": "ON", "brightness": 254, "transition": 5.0}),
    ]


async def test_brightness_is_read_back_once_the_fade_has_run(hass, broker):
    entity_id = light(hass, broker)
    start = wall_clock_now()

    await call(hass, "raise_brightness", {"entity_id": entity_id})

    await advance(hass, start, 2.5)
    assert commands(broker)[1:] == []
    await advance(hass, start, 3.5)
    assert commands(broker)[1:] == [(GET, {"state": "", "brightness": ""})]


async def test_a_group_is_read_back_member_by_member(hass, broker):
    room = group(hass, broker, one={}, two={})
    start = wall_clock_now()

    await call(hass, "raise_brightness", {"entity_id": room})

    await advance(hass, start, 3.5)
    assert commands(broker)[1:] == [
        ("zigbee2mqtt/0x01/get", {"state": "", "brightness": ""}),
        ("zigbee2mqtt/0x02/get", {"state": "", "brightness": ""}),
    ]
