"""The color refresh after a color stop Z2M doesn't read back right: a color get, which Z2M answers by publishing it."""

import logging

import pytest

from custom_components.deviceside.integrations.zigbee2mqtt import color_refresh

from .conftest import call, commands, group, light
from ...conftest import advance, wall_clock_now
from ...fixtures.zigbee2mqtt import IEEE

pytestmark = pytest.mark.usefixtures("setup_production", "clock")

SENT = {"status": "sent", "integration": "zigbee2mqtt"}
COLOR_GET = (f"zigbee2mqtt/{IEEE}/get", {"color": ""})
LOGGER = "custom_components.deviceside.integrations.zigbee2mqtt.color_refresh"


async def stop_after(hass, broker, entity_id: str, action: str) -> list:
    """Raise or lower, stop, then let the refresh run: what it published after the stop."""
    start = wall_clock_now()
    await call(hass, action, {"entity_id": entity_id})
    response = await call(hass, "stop", {"entity_id": entity_id})
    assert response == {entity_id: SENT}
    stopped = len(commands(broker))
    await advance(hass, start, color_refresh.DELAY_S + 0.5)
    return commands(broker)[stopped:]


@pytest.mark.parametrize("modes", [["xy"], ["hs", "xy"]])
async def test_a_hue_stop_gets_nothing_as_z2m_reads_it_back(hass, broker, modes):
    entity_id = light(hass, broker, modes=modes)

    published = await stop_after(hass, broker, entity_id, "raise_hue")

    assert broker.reads() == []
    assert published == []


@pytest.mark.parametrize("modes", [["xy"], ["hs", "xy"]])
async def test_a_saturation_stop_gets_the_color(hass, broker, modes):
    # The raw stopMoveStep publishes nothing, and Z2M's cached x/y is the fade's end point.
    entity_id = light(hass, broker, modes=modes)

    published = await stop_after(hass, broker, entity_id, "lower_saturation")

    assert broker.reads() == []
    assert published == [COLOR_GET]


async def test_a_brightness_stop_gets_no_color(hass, broker):
    entity_id = light(hass, broker)

    published = await stop_after(hass, broker, entity_id, "raise_brightness")

    assert published == []


async def test_each_group_member_gets_the_color(hass, broker):
    # Z2M reads a group's stop back from its first member only.
    room = group(hass, broker, one={}, two={"modes": ["hs"]})

    published = await stop_after(hass, broker, room, "raise_hue")

    assert published == [("zigbee2mqtt/0x01/get", {"color": ""}), ("zigbee2mqtt/0x02/get", {"color": ""})]


async def test_a_failed_get_is_logged_and_the_group_carries_on(hass, broker, caplog, monkeypatch):
    room = group(hass, broker, one={}, two={})
    sent = []

    async def refuse_one(hass, mqtt_topic, payload):
        if "0x01" in mqtt_topic:
            raise RuntimeError("broker gone")
        sent.append(mqtt_topic)

    monkeypatch.setattr(color_refresh, "publish", refuse_one)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        await stop_after(hass, broker, room, "raise_hue")

    assert (
        "Couldn't refresh Home Assistant's color for light.one, so it may be stale: RuntimeError('broker gone')"
        in caplog.text
    )
    assert sent == ["zigbee2mqtt/0x02/get"]


async def test_no_bridge_groups_is_logged(hass, broker, caplog):
    room = group(hass, broker, one={}, two={})
    start = wall_clock_now()
    await call(hass, "raise_hue", {"entity_id": room})
    await call(hass, "stop", {"entity_id": room})
    del broker.retained["zigbee2mqtt/bridge/groups"]

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        await advance(hass, start, color_refresh.DELAY_S + 0.5)

    assert "Couldn't refresh Home Assistant's color for light.room" in caplog.text
