"""Zigbee2MQTT bridge cache: bridge/devices, groups and info held, and options replies routed, for the entry's life."""

import asyncio
import json

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.deviceside.const import CONF_Z2M_COLOR_MODES, DOMAIN
from custom_components.deviceside.integrations.zigbee2mqtt import bridge, bridge_cache

from ...fixtures.zigbee2mqtt import GROUPS, INFO

DEVICES = "zigbee2mqtt/bridge/devices"
STATE = "zigbee2mqtt/bridge/state"
DEVICE_RESPONSE = "zigbee2mqtt/bridge/response/device/options"
GROUP_RESPONSE = "zigbee2mqtt/bridge/response/group/options"
LOGGING = "zigbee2mqtt/bridge/logging"
HELD_TOPICS = [DEVICE_RESPONSE, GROUP_RESPONSE, LOGGING, DEVICES, GROUPS, INFO, STATE]
ROOM = [{"id": 7, "members": [{"ieee_address": "0x01"}]}]


class Connection:
    """HA's MQTT connection status, as the cache subscribes to it."""

    def __init__(self) -> None:
        self.callbacks: list = []

    def subscribe(self, hass, status_callback):
        self.callbacks.append(status_callback)
        return lambda: self.callbacks.remove(status_callback)

    def lost(self) -> None:
        for status_callback in list(self.callbacks):
            status_callback(False)

    def back(self) -> None:
        for status_callback in list(self.callbacks):
            status_callback(True)


@pytest.fixture
def connection(monkeypatch) -> Connection:
    fake = Connection()
    monkeypatch.setattr(bridge_cache.mqtt, "async_subscribe_connection_status", fake.subscribe)
    return fake


@pytest.fixture
def mqtt_up(monkeypatch):
    async def ready(hass):
        return True

    monkeypatch.setattr(bridge_cache.mqtt, "async_wait_for_mqtt_client", ready)


async def settle() -> None:
    for _ in range(3):
        await asyncio.sleep(0)


async def start(hass, entry) -> bridge_cache.BridgeCache:
    await bridge_cache.async_setup(hass, entry)
    await settle()  # the retained messages
    cache = bridge_cache.get(hass)
    assert cache is not None
    return cache


@pytest.fixture
def online(broker):
    broker.retained[STATE] = {"state": "online"}
    broker.retained[GROUPS] = ROOM
    broker.retained[DEVICES] = [{"ieee_address": "0x01"}]
    return broker


def wanted_on(current: dict) -> dict:
    return {} if current.get("x") else {"x": True}


# Holding devices, groups and info


async def test_subscribes_the_response_topics_before_the_barrier(hass, setup_production, online, connection, mqtt_up):
    await start(hass, setup_production)

    assert online.topics == HELD_TOPICS
    assert len(connection.callbacks) == 1


async def test_groups_devices_and_info_come_from_the_cache(hass, setup_production, online, connection, mqtt_up):
    await start(hass, setup_production)
    online.topics.clear()

    assert await bridge.group_members(hass, "7") == ["0x01"]
    assert await bridge.has_feature(hass, ["0x01"], "color_hs") is False
    first = await bridge.retained(hass, "info")
    assert await bridge.retained(hass, "info") is first
    assert online.topics == []


async def test_each_payload_is_parsed_once_and_an_empty_one_clears_the_topic(
    hass, setup_production, online, connection, mqtt_up
):
    cache = await start(hass, setup_production)

    first = await bridge.retained(hass, "groups")
    assert await bridge.retained(hass, "groups") is first

    online.deliver(GROUPS, json.dumps([{"id": 8, "members": []}]))
    second = await bridge.retained(hass, "groups")
    assert second is not first
    assert second == [{"id": 8, "members": []}]
    assert await bridge.retained(hass, "groups") is second

    online.deliver(GROUPS, "")
    assert cache.parsed("groups") is None


async def test_an_empty_cache_reads_fresh(hass, setup_production, broker, connection, mqtt_up):
    cache = await start(hass, setup_production)  # Z2M has published no groups yet
    assert cache.parsed("groups") is None
    broker.topics.clear()

    with pytest.raises(TimeoutError, match="no bridge/groups from Zigbee2MQTT"):
        await bridge.retained(hass, "groups")
    assert broker.topics == [GROUPS]


async def test_without_a_cache_everything_reads_fresh(hass, broker):
    broker.retained[GROUPS] = ROOM

    assert bridge_cache.get(hass) is None
    assert await bridge.group_members(hass, "7") == ["0x01"]
    assert broker.topics == [GROUPS]


# Replies


async def test_live_replies_reach_the_call_without_its_own_subscription(
    hass, setup_production, online, connection, mqtt_up
):
    cache = await start(hass, setup_production)
    assert cache.replies_live
    online.topics.clear()
    confirmed: list[str] = []

    await bridge.ensure_options(hass, "device", ["0x01"], wanted_on, confirmed.append)
    await bridge.ensure_options(hass, "device", ["0x01"], wanted_on, confirmed.append)

    assert confirmed == ["0x01"]  # the second call sees the first's write, before Z2M republishes bridge/info
    assert online.topics == []  # neither bridge/info nor the reply topic
    assert len(online.requests()) == 1
    assert cache.pending == {}


async def test_a_refused_live_reply_still_raises_and_writes_nothing_through(
    hass, setup_production, online, connection, mqtt_up
):
    cache = await start(hass, setup_production)
    online.refused.add("0x01")

    with pytest.raises(RuntimeError, match="refused"):
        await bridge.ensure_options(hass, "device", ["0x01"], wanted_on, lambda _id: None)
    assert cache.pending == {}
    assert cache.confirmed == {}


async def test_without_the_barrier_each_call_subscribes_for_its_reply(
    hass, setup_production, broker, connection, mqtt_up
):
    del broker.retained[STATE]
    cache = await start(hass, setup_production)
    assert not cache.replies_live
    broker.topics.clear()
    confirmed: list[str] = []

    await bridge.ensure_options(hass, "group", ["7"], wanted_on, confirmed.append)

    assert confirmed == ["7"]
    assert broker.topics == [GROUP_RESPONSE, INFO]


async def test_a_disconnect_clears_it_until_the_resubscribe(hass, setup_production, online, connection, mqtt_up):
    cache = await start(hass, setup_production)

    connection.lost()

    assert not cache.replies_live
    assert cache.parsed("groups") is None and cache.parsed("devices") is None and cache.parsed("info") is None
    online.topics.clear()
    await bridge.ensure_options(hass, "device", ["0x01"], wanted_on, lambda _id: None)
    assert online.topics == [DEVICE_RESPONSE, INFO]

    connection.back()  # connected again, but nothing is resubscribed yet
    assert not cache.replies_live
    online.deliver(GROUPS, json.dumps(ROOM))  # HA's resubscribe, in topic order
    online.deliver(STATE, json.dumps({"state": "online"}))
    assert cache.replies_live
    assert await bridge.retained(hass, "groups") == ROOM


async def test_live_replies_without_info_read_it_fresh(hass, setup_production, online, connection, mqtt_up):
    cache = await start(hass, setup_production)
    online.deliver(INFO, "")  # bridge/info cleared
    assert cache.replies_live and cache.parsed("info") is None
    online.topics.clear()

    await bridge.ensure_options(hass, "device", ["0x01"], wanted_on, lambda _id: None)

    assert online.topics == [INFO]


# Confirmed options, written through


def info(devices: dict) -> str:
    return json.dumps({"config": {"devices": devices, "groups": {}}})


async def test_a_confirmed_write_shows_until_the_next_info(hass, setup_production, online, connection, mqtt_up):
    online.retained[INFO] = {"config": {"devices": {"0x01": {"y": 1}}, "groups": {}}}
    cache = await start(hass, setup_production)

    await bridge.ensure_options(hass, "device", ["0x01"], wanted_on, lambda _id: None)

    # No data.to in the reply: what was sent, over the options before.
    assert cache.settings("devices", "0x01") == {"y": 1, "x": True}
    assert (await bridge.retained(hass, "info"))["config"]["devices"]["0x01"] == {"y": 1}  # never changed in place

    online.deliver(INFO, info({"0x01": {"y": 2}}))  # Z2M's republish
    assert cache.confirmed == {}
    assert cache.settings("devices", "0x01") == {"y": 2}


async def test_a_confirmed_write_takes_z2m_s_own_options(hass, setup_production, online, connection, mqtt_up):
    cache = await start(hass, setup_production)
    online.reply = {"status": "ok", "data": {"from": {}, "to": {"x": True, "z": 3}}}

    await bridge.ensure_options(hass, "device", ["0x01"], wanted_on, lambda _id: None)

    assert cache.settings("devices", "0x01") == {"x": True, "z": 3}


async def test_settings_without_info(hass, setup_production, online, connection, mqtt_up):
    cache = await start(hass, setup_production)
    assert cache.settings("devices", "0x99") == {}  # held, but no options for it

    online.deliver(INFO, "")

    assert cache.settings("devices", "0x01") is None


async def test_a_disconnect_drops_confirmed_writes(hass, setup_production, online, connection, mqtt_up):
    cache = await start(hass, setup_production)
    await bridge.ensure_options(hass, "device", ["0x01"], wanted_on, lambda _id: None)
    assert cache.confirmed

    connection.lost()

    assert cache.confirmed == {}


# Lifecycle


async def test_unload_unsubscribes_everything(hass, setup_production, online, connection, mqtt_up):
    await start(hass, setup_production)

    assert await hass.config_entries.async_unload(setup_production.entry_id)

    assert bridge_cache.get(hass) is None
    assert not any(online.listening(topic) for topic in HELD_TOPICS)
    assert connection.callbacks == []


async def test_without_mqtt_nothing_is_held(hass, setup_production, broker, monkeypatch):
    async def no_client(hass):
        return False

    monkeypatch.setattr(bridge_cache.mqtt, "async_wait_for_mqtt_client", no_client)

    await bridge_cache.async_setup(hass, setup_production)

    assert bridge_cache.get(hass) is None
    assert broker.topics == []


async def test_a_failed_subscribe_warns_and_holds_nothing(
    hass, setup_production, broker, connection, mqtt_up, monkeypatch, caplog
):
    subscribe = bridge.mqtt.async_subscribe

    async def refuse_state(hass, topic, *args, **kwargs):
        if topic == STATE:
            raise RuntimeError("MQTT is not connected")
        return await subscribe(hass, topic, *args, **kwargs)

    monkeypatch.setattr(bridge.mqtt, "async_subscribe", refuse_state)

    await bridge_cache.async_setup(hass, setup_production)
    await settle()

    assert bridge_cache.get(hass) is None
    assert not any(broker.listening(topic) for topic in HELD_TOPICS)
    assert (
        "Couldn't subscribe to Zigbee2MQTT's bridge, so each action reads it fresh, Zigbee2MQTT groups run only "
        "with Experimental features on, and turn on with timer refuses them and every device but Hue's" in caplog.text
    )


async def test_setup_entry_starts_it_once_mqtt_is_up(hass, broker, connection, mqtt_up):
    entry = MockConfigEntry(domain=DOMAIN, options={CONF_Z2M_COLOR_MODES: False})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert bridge_cache.get(hass) is not None
    assert await hass.config_entries.async_unload(entry.entry_id)


# Z2M saying online, for the restore journal's replay


async def onlines(hass, entry) -> list[None]:
    seen: list[None] = []
    await bridge_cache.async_setup(hass, entry, lambda: seen.append(None))
    await settle()
    return seen


async def test_the_retained_online_calls_on_online(hass, setup_production, online, connection, mqtt_up):
    assert await onlines(hass, setup_production) == [None]


@pytest.mark.parametrize(("payload", "calls"), [("online", 1), ("offline", 0), ('{"state": "offline"}', 0), ("{", 0)])
async def test_each_online_calls_on_online(hass, setup_production, broker, connection, mqtt_up, payload, calls):
    """Z2M publishes JSON; older versions, and its last will, a bare string. Anything else isn't online."""
    broker.retained[STATE] = {"state": "offline"}
    seen = await onlines(hass, setup_production)

    broker.deliver(STATE, payload)

    assert len(seen) == calls
    assert bridge_cache.get(hass).replies_live


async def test_a_malformed_payload_counts_as_none(hass, setup_production, online, connection, mqtt_up):
    """A retained bridge/info that doesn't parse is no bridge/info: read fresh, which times out on it too."""
    cache = await start(hass, setup_production)
    online.retained.pop(INFO)
    online.deliver(INFO, "{not json")

    assert cache.parsed("info") is None
    with pytest.raises(TimeoutError, match="no bridge/info"):
        await bridge.ensure_options(hass, "device", ["0x01"], wanted_on, lambda _id: None)


async def test_a_malformed_fresh_payload_raises_timeout(hass, broker, monkeypatch):
    async def malformed(hass, suffix):
        return "{not json"

    monkeypatch.setattr(bridge, "first_retained", malformed)

    with pytest.raises(TimeoutError, match="no parseable bridge/groups"):
        await bridge.retained(hass, "groups")
