"""Zigbee2MQTT stop: a stop per attribute we move, the halts recorded, the end read-backs cancelled."""

import pytest

from custom_components.deviceside.core.transition import BRIGHTNESS, SATURATION
from custom_components.deviceside.integrations.zigbee2mqtt.fade import hue_moves, transition_cache

from .conftest import GET, SENT, SET, call, commands, group, light
from ...conftest import advance, result, sent, wall_clock_now

pytestmark = pytest.mark.usefixtures("setup_production", "clock")

BRIGHTNESS_STOP = {"brightness_move": "stop"}
STOP_MOVE_STEP = {
    "command": {
        "cluster": "lightingColorCtrl",
        "command": "stopMoveStep",
        "payload": {"optionsMask": 0, "optionsOverride": 0},
    }
}


async def test_a_lit_light_gets_a_brightness_stop_and_no_prediction(hass, broker):
    # Nothing of ours is fading it, so Z2M's read-back is right at once.
    entity_id = light(hass, broker)

    response = await call(hass, "stop", {"entity_id": entity_id})

    assert response == {entity_id: SENT}
    assert commands(broker) == [(SET, BRIGHTNESS_STOP)]
    assert not transition_cache(hass).follows(entity_id)


async def test_an_off_light_is_unchanged(hass, broker):
    entity_id = light(hass, broker, state="off")

    response = await call(hass, "stop", {"entity_id": entity_id})

    assert response == {entity_id: result("zigbee2mqtt", "unchanged", detail="off")}
    assert commands(broker) == []


async def test_a_soft_off_of_ours_is_halted_where_it_is(hass, broker, clock):
    entity_id = light(hass, broker)
    await call(hass, "lower_brightness", {"entity_id": entity_id})
    hass.states.async_set(entity_id, "off", {})  # Z2M publishes off at the start of the fade
    clock.now += 1.5  # halfway from 60 % to off

    response = await call(hass, "stop", {"entity_id": entity_id})

    assert response == {entity_id: SENT}
    # The soft off ran as a ZCL transition (native control off), so the brightness stop alone halts it.
    assert commands(broker)[1:] == [(SET, BRIGHTNESS_STOP)]
    clock.now += 1
    assert transition_cache(hass).value(entity_id, BRIGHTNESS, 0) == pytest.approx(30.0)


@pytest.mark.parametrize(
    ("actions", "stops"),
    [
        pytest.param(["raise_hue"], [{"hue_move": "stop"}, BRIGHTNESS_STOP], id="hue"),
        pytest.param(["raise_saturation"], [STOP_MOVE_STEP, BRIGHTNESS_STOP], id="saturation"),
        pytest.param(
            ["raise_brightness", "lower_saturation"], [STOP_MOVE_STEP, BRIGHTNESS_STOP], id="two_moves_color_first"
        ),
        pytest.param(
            ["raise_brightness", "raise_hue"], [{"hue_move": "stop"}, BRIGHTNESS_STOP], id="brightness_and_hue"
        ),
    ],
)
async def test_each_attribute_a_move_of_ours_changes_gets_its_stop(hass, broker, actions, stops):
    entity_id = light(hass, broker)
    for action in actions:
        await call(hass, action, {"entity_id": entity_id})
    moved = len(commands(broker))

    await call(hass, "stop", {"entity_id": entity_id})

    assert commands(broker)[moved:] == [(SET, stop) for stop in stops]


async def test_a_stop_ends_the_hue_cycle(hass, broker):
    entity_id = light(hass, broker)
    await call(hass, "raise_hue", {"entity_id": entity_id})

    await call(hass, "stop", {"entity_id": entity_id})
    await call(hass, "stop", {"entity_id": entity_id})

    assert commands(broker)[-1] == (SET, BRIGHTNESS_STOP)
    assert commands(broker)[-2] == (SET, BRIGHTNESS_STOP)
    assert entity_id not in hue_moves(hass)


async def test_a_saturation_move_ends_the_hue_cycle(hass, broker):
    # The color write halts the bulb's hue cycle, so the next stop has no hue_move to stop.
    entity_id = light(hass, broker)
    await call(hass, "raise_hue", {"entity_id": entity_id})
    await call(hass, "raise_saturation", {"entity_id": entity_id})

    await call(hass, "stop", {"entity_id": entity_id})

    assert (SET, {"hue_move": "stop"}) not in commands(broker)
    assert entity_id not in hue_moves(hass)


async def test_the_halts_are_recorded_at_their_predictions(hass, broker, clock):
    entity_id = light(hass, broker)
    await call(hass, "raise_brightness", {"entity_id": entity_id})
    await call(hass, "lower_saturation", {"entity_id": entity_id})
    clock.now += 1  # 80 % brightness, 40 % saturation

    await call(hass, "stop", {"entity_id": entity_id})

    clock.now += 1
    cache = transition_cache(hass)
    assert cache.value(entity_id, BRIGHTNESS, 0) == pytest.approx(80.0)
    assert cache.value(entity_id, SATURATION, 0) == pytest.approx(40.0)


async def test_a_group_is_stopped_with_one_publish_and_a_halt_per_member(hass, broker, clock):
    room = group(hass, broker, one={"brightness": 255}, two={"brightness": 51})
    await call(hass, "lower_brightness", {"entity_id": room})
    clock.now += 1

    response = await call(hass, "stop", {"entity_id": room})

    assert response == {room: SENT}
    assert commands(broker)[1:] == [("zigbee2mqtt/7/set", BRIGHTNESS_STOP)]
    cache = transition_cache(hass)
    assert cache.halted("light.one", BRIGHTNESS)
    assert cache.value("light.one", BRIGHTNESS, 0) == pytest.approx(100 - 100 / 3)
    assert cache.value("light.two", BRIGHTNESS, 0) == pytest.approx(20 - 20 / 3)


async def test_a_group_with_nothing_lit_is_unchanged(hass, broker):
    room = group(hass, broker, one={"state": "off"}, two={"state": "off"})

    response = await call(hass, "stop", {"entity_id": room})

    assert response == {room: result("zigbee2mqtt", "unchanged", detail="off")}


async def test_a_stop_cancels_the_moves_end_read_back(hass, broker):
    entity_id = light(hass, broker)
    start = wall_clock_now()
    await call(hass, "raise_brightness", {"entity_id": entity_id})

    await call(hass, "stop", {"entity_id": entity_id})

    await advance(hass, start, 5)
    assert commands(broker) == [
        (SET, {"state": "ON", "brightness": 254, "transition": 2.0}),
        (SET, BRIGHTNESS_STOP),
    ]


async def test_a_stop_of_two_moves_cancels_both_end_read_backs(hass, broker):
    # The saturation halt's color refresh replaces the saturation fade's read-back; the brightness one is dropped.
    entity_id = light(hass, broker)
    start = wall_clock_now()
    await call(hass, "raise_brightness", {"entity_id": entity_id})
    await call(hass, "lower_saturation", {"entity_id": entity_id})
    moved = len(commands(broker))

    await call(hass, "stop", {"entity_id": entity_id})

    await advance(hass, start, 5)
    assert (GET, {"state": "", "brightness": ""}) not in commands(broker)[moved:]


async def test_an_empty_group_is_skipped(hass, broker):
    room = group(hass, broker, one={}, two={})
    light(hass, broker)
    hass.states.async_set("light.one", "unavailable")
    hass.states.async_set("light.two", "unavailable")

    response = await call(hass, "stop", {"entity_id": [room, "light.lamp"]})

    assert response == {"light.lamp": SENT}


async def test_the_stop_is_logged_without_the_topic(hass, broker, wire):
    entity_id = light(hass, broker)

    await call(hass, "stop", {"entity_id": entity_id})

    assert sent(wire) == [f"{entity_id}: sending mqtt set {BRIGHTNESS_STOP} with {{'hue_native_control': None}}"]
