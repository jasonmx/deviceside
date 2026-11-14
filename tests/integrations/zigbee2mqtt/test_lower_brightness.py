"""Zigbee2MQTT lower_brightness: the end point with the time left, or a soft off at 0 %%."""

import pytest

from custom_components.deviceside.core.transition import BRIGHTNESS
from custom_components.deviceside.integrations.zigbee2mqtt.fade import transition_cache

from .conftest import SENT, SET, call, commands, group, light
from ...conftest import result

pytestmark = pytest.mark.usefixtures("setup_production", "clock")


@pytest.mark.parametrize(
    ("data", "payload"),
    [
        pytest.param({}, {"state": "OFF", "transition": 3.0}, id="to_zero_is_a_soft_off"),
        pytest.param({"limit": 20}, {"brightness": 51, "transition": 2.0}, id="to_a_limit"),
    ],
)
async def test_publishes_the_end_point_with_the_time_left(hass, broker, data, payload):
    entity_id = light(hass, broker)

    response = await call(hass, "lower_brightness", {"entity_id": entity_id, **data})

    assert response == {entity_id: SENT}
    assert commands(broker) == [(SET, payload)]


@pytest.mark.parametrize(
    ("options", "data", "detail"),
    [
        pytest.param({"brightness": 51}, {"limit": 20}, "at_limit", id="at_the_limit"),
        pytest.param({"state": "off"}, {}, "off", id="an_off_light"),
    ],
)
async def test_nothing_left_to_move_is_unchanged(hass, broker, options, data, detail):
    entity_id = light(hass, broker, **options)

    response = await call(hass, "lower_brightness", {"entity_id": entity_id, **data})

    assert response == {entity_id: result("zigbee2mqtt", "unchanged", detail=detail)}
    assert commands(broker) == []


async def test_a_group_gets_one_publish_and_a_prediction_per_member(hass, broker, clock):
    room = group(hass, broker, one={"brightness": 255}, two={"brightness": 51})

    response = await call(hass, "lower_brightness", {"entity_id": room})

    assert response == {room: SENT}
    # From the members' mean, 60 %, to off over a 5 s sweep.
    assert commands(broker) == [("zigbee2mqtt/7/set", {"state": "OFF", "transition": 3.0})]
    cache = transition_cache(hass)
    clock.now += 1.5
    assert cache.value("light.one", BRIGHTNESS, 0) == pytest.approx(50.0)
    assert cache.value("light.two", BRIGHTNESS, 0) == pytest.approx(10.0)
