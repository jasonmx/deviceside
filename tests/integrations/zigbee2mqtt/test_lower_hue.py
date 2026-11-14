"""Zigbee2MQTT lower_hue: Z2M's hue_move, a cycle red to magenta to blue at a rate the bulb runs until a stop."""

import pytest

from .conftest import SENT, SET, call, commands, light

pytestmark = pytest.mark.usefixtures("setup_production", "clock")


async def test_a_cycle_in_the_sweep_time(hass, broker):
    entity_id = light(hass, broker)

    response = await call(hass, "lower_hue", {"entity_id": entity_id})

    assert response == {entity_id: SENT}
    assert commands(broker) == [(SET, {"hue_move": -51})]
