"""Zigbee2MQTT set check: a set Z2M logs as failed makes its target rejected; the check never delays a publish."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from homeassistant.core import ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr

from custom_components.deviceside.core.runtime import DATA
from custom_components.deviceside.integrations.zigbee2mqtt import bridge_cache, set_check, turn_on_for

from .conftest import SENT, SET, call, commands, group, light
from .test_native_control import target
from .test_set_attributes import DIMMABLE, bulb
from ...fixtures.zigbee2mqtt import IEEE

pytestmark = pytest.mark.usefixtures("setup_production", "clock")

LOGGING = "zigbee2mqtt/bridge/logging"
GROUP_SET = "zigbee2mqtt/7/set"
# The house run's line (notes: rig-run-2026-11-11-155622), household names replaced: a groupcast the coordinator
# refused with BUSY, which herdsman's Ember adapter doesn't retry.
GROUP_BUSY = (
    "Publish 'set' 'brightness' to 'room' failed: 'Error: ZCL command 7 genLevelCtrl.moveToLevelWithOnOff("
    '{"level":76,"transtime":35,"optionsMask":0,"optionsOverride":0}) failed '
    "(~x~> [ZCL GROUP groupId=7] Failed to send with status=BUSY.)'"
)
# A unicast that failed after herdsman's retries, naming no status.
LAMP_UNDELIVERED = (
    f"Publish 'set' 'brightness' to 'lamp' failed: 'Error: ZCL command {IEEE}/11 genLevelCtrl.moveToLevelWithOnOff("
    "{\"level\":51,\"transtime\":20}) failed (Delivery failed for '12345')'"
)


@pytest.fixture
def live(hass, setup_production, broker, monkeypatch):
    """The bridge cache with its response topics and bridge/logging live, as once MQTT is up."""
    cache = bridge_cache.BridgeCache()
    cache.replies_live = True
    hass.data[DATA].integration_state[bridge_cache._KEY] = cache
    broker.subscribers.setdefault(LOGGING, []).append(cache._on_log)
    for kind in bridge_cache.KINDS:
        reply_topic = f"zigbee2mqtt/bridge/response/{kind}/options"
        broker.subscribers.setdefault(reply_topic, []).append(bridge_cache.reply_router(cache.pending))
    monkeypatch.setattr(set_check, "BOUND_S", 0.05)
    return cache


def log(broker, message: str, level: str = "error") -> None:
    broker.deliver(LOGGING, json.dumps({"level": level, "message": message}))


def answer(broker, on: str, *, line: str | None = None, state: str | None = None) -> None:
    """Have Z2M answer each set published to `on`: a log line, and or a state publish under the friendly name."""

    def on_set(topic: str, payload: dict) -> None:
        if topic != on:
            return
        loop = asyncio.get_running_loop()
        if line is not None:
            loop.call_soon(log, broker, line)
        if state is not None:
            loop.call_soon(broker.deliver, f"zigbee2mqtt/{state}", json.dumps({"state": "ON"}))

    broker.on_set = on_set


async def failure(hass, service: str, data: dict) -> str:
    with pytest.raises(HomeAssistantError) as raised:
        await call(hass, service, data)
    assert raised.value.translation_key == "targets_failed"
    return raised.value.translation_placeholders["summary"]


async def timed(coro) -> tuple[object, float]:
    loop = asyncio.get_running_loop()
    began = loop.time()
    outcome = await coro
    return outcome, loop.time() - began


@pytest.mark.usefixtures("live")
async def test_a_groupcast_z2m_logs_busy_fails_the_group_with_the_status(hass, broker):
    entity_id = group(hass, broker, one={}, two={})
    answer(broker, GROUP_SET, line=GROUP_BUSY)

    assert await failure(hass, "lower_brightness", {"entity_id": entity_id}) == "light.room: busy"


@pytest.mark.usefixtures("live")
async def test_a_unicast_z2m_couldnt_deliver_fails_the_light(hass, broker):
    entity_id = light(hass, broker)
    answer(broker, SET, line=LAMP_UNDELIVERED)

    assert await failure(hass, "lower_brightness", {"entity_id": entity_id, "limit": 20}) == "light.lamp: send_failed"


@pytest.mark.usefixtures("live")
async def test_only_the_target_whose_set_failed_fails(hass, broker):
    light(hass, broker)
    light(hass, broker, "light.desk", "0x02")
    answer(broker, "zigbee2mqtt/0x02/set", line=GROUP_BUSY.replace("'room'", "'desk'"))

    summary = await failure(hass, "lower_brightness", {"entity_id": ["light.lamp", "light.desk"], "limit": 20})

    assert summary == "light.desk: busy"


async def test_a_state_publish_answers_sent_without_waiting_for_the_bound(hass, broker, live, monkeypatch):
    monkeypatch.setattr(set_check, "BOUND_S", 5.0)
    entity_id = light(hass, broker)
    answer(broker, SET, state="lamp")

    response, elapsed = await timed(call(hass, "lower_brightness", {"entity_id": entity_id, "limit": 20}))

    assert response == {entity_id: SENT}
    assert elapsed < 1.0


@pytest.mark.parametrize(
    ("line", "level"),
    [
        pytest.param(GROUP_BUSY, "error", id="another_targets_set"),
        pytest.param(LAMP_UNDELIVERED.replace("'brightness'", "'read'"), "error", id="a_read_reader_counts"),
        pytest.param(LAMP_UNDELIVERED, "warning", id="not_an_error"),
        pytest.param("Failed to ping 'lamp'", "error", id="not_a_set"),
    ],
)
@pytest.mark.usefixtures("live")
async def test_other_lines_are_ignored_and_the_settle_runs_to_its_bound(hass, broker, line, level):
    entity_id = light(hass, broker)
    broker.on_set = lambda topic, payload: asyncio.get_running_loop().call_soon(log, broker, line, level)

    response, elapsed = await timed(call(hass, "lower_brightness", {"entity_id": entity_id, "limit": 20}))

    assert response == {entity_id: SENT}
    assert elapsed >= set_check.BOUND_S


@pytest.mark.usefixtures("live")
async def test_a_retained_state_doesnt_answer_a_set(hass, broker):
    entity_id = light(hass, broker)
    broker.retained["zigbee2mqtt/lamp"] = {"state": "ON"}

    response, elapsed = await timed(call(hass, "lower_brightness", {"entity_id": entity_id, "limit": 20}))

    assert response == {entity_id: SENT}
    assert elapsed >= set_check.BOUND_S


async def test_a_hue_move_waits_only_for_a_groupcasts_busy(hass, broker, live, monkeypatch):
    # hue_move draws no state, so a settle to BOUND_S would hold a raise_hue's answer, and a script's stop after it.
    monkeypatch.setattr(set_check, "BOUND_S", 30.0)
    entity_id = light(hass, broker)

    response, elapsed = await timed(call(hass, "raise_hue", {"entity_id": entity_id}))

    assert response == {entity_id: SENT}
    assert set_check.NO_STATE_BOUND_S <= elapsed < 1.0


@pytest.mark.usefixtures("live")
async def test_a_groupcast_hue_move_z2m_logs_busy_fails_the_group(hass, broker):
    entity_id = group(hass, broker, one={}, two={})
    answer(broker, GROUP_SET, line=GROUP_BUSY.replace("'brightness'", "'hue_move'"))

    assert await failure(hass, "raise_hue", {"entity_id": entity_id}) == "light.room: busy"


async def test_the_settle_ends_before_the_dispatchers_deadline(hass, broker, live, monkeypatch):
    monkeypatch.setattr(set_check, "BOUND_S", 30.0)
    monkeypatch.setattr(set_check, "DEADLINE_MARGIN_S", 0.05)
    monkeypatch.setattr("custom_components.deviceside.core.dispatcher.ADAPTER_TIMEOUT_S", 0.1)
    entity_id = light(hass, broker)

    response, elapsed = await timed(call(hass, "lower_brightness", {"entity_id": entity_id, "limit": 20}))

    assert response == {entity_id: SENT}
    assert 0.1 - set_check.DEADLINE_MARGIN_S <= elapsed < 0.1


async def test_without_a_live_bridge_cache_nothing_is_checked(hass, broker):
    entity_id = light(hass, broker)
    answer(broker, SET, line=LAMP_UNDELIVERED)

    response = await call(hass, "lower_brightness", {"entity_id": entity_id, "limit": 20})

    assert response == {entity_id: SENT}
    assert "zigbee2mqtt/lamp" not in broker.topics


async def test_a_stops_publishes_go_out_back_to_back(hass, broker, live, clock):
    # A hue cycle of ours, halted: the hue stop, then the brightness stop, with no settle between them.
    entity_id = light(hass, broker)
    await call(hass, "raise_hue", {"entity_id": entity_id})
    clock.now += 1.5
    loop = asyncio.get_running_loop()
    stamps: list[float] = []
    broker.on_set = lambda topic, payload: stamps.append(loop.time())
    before = len(commands(broker))

    response, elapsed = await timed(call(hass, "stop", {"entity_id": entity_id}))

    assert response == {entity_id: SENT}
    assert [payload for _topic, payload in commands(broker)[before:]] == [
        {"hue_move": "stop"},
        {"brightness_move": "stop"},
    ]
    assert stamps[1] - stamps[0] < 0.02 < set_check.BOUND_S <= elapsed


@pytest.mark.usefixtures("live")
async def test_turn_on_for_fails_when_its_light_turn_on_fails(hass, broker, monkeypatch):
    # HA publishes light.turn_on as a set, so Z2M logs its failure the same way.
    monkeypatch.setattr(turn_on_for, "_STATE_TIMEOUT_S", 0.05)
    entity_id = light(hass, broker)

    async def turn_on(service: ServiceCall) -> None:
        asyncio.get_running_loop().call_soon(log, broker, GROUP_BUSY.replace("'room'", "'lamp'"))

    hass.services.async_register("light", "turn_on", turn_on)

    summary = await failure(hass, "turn_on_for", {"entity_id": entity_id, "duration": 5, "brightness": 50})

    assert summary == "light.lamp: busy"


async def test_set_attributes_checks_without_a_settle(hass, broker, live, monkeypatch):
    monkeypatch.setattr(set_check, "BOUND_S", 30.0)
    bulb(hass, broker, modes=DIMMABLE)

    response, elapsed = await timed(call(hass, "set_attributes", {"entity_id": "light.lamp", "brightness": 40}))

    assert response == {"light.lamp": {"status": "confirmed", "integration": "zigbee2mqtt"}}
    assert elapsed < 1.0


@pytest.mark.usefixtures("live")
async def test_a_device_z2m_names_nothing_for_is_sent_unchecked(hass, broker):
    entity_id = light(hass, broker)
    device = target(hass, entity_id).device
    dr.async_get(hass).async_update_device(device.id, name=None)
    answer(broker, SET, line=LAMP_UNDELIVERED)

    response, elapsed = await timed(call(hass, "lower_brightness", {"entity_id": entity_id, "limit": 20}))

    assert response == {entity_id: SENT}
    assert elapsed < set_check.BOUND_S


@pytest.mark.usefixtures("live")
async def test_a_state_topic_that_wont_subscribe_is_sent_unchecked(hass, broker, monkeypatch):
    entity_id = light(hass, broker)
    subscribe = broker.subscribe

    async def refusing(hass, topic, msg_callback):
        if topic == "zigbee2mqtt/lamp":
            raise RuntimeError("refused")
        return await subscribe(hass, topic, msg_callback)

    monkeypatch.setattr(set_check.mqtt, "async_subscribe", refusing)
    answer(broker, SET, line=LAMP_UNDELIVERED)

    response = await call(hass, "lower_brightness", {"entity_id": entity_id, "limit": 20})

    assert response == {entity_id: SENT}


async def test_a_watch_closed_mid_subscribe_drops_the_subscription(hass, broker, live):
    entity_id = light(hass, broker)
    watch = set_check.Watch(hass, live)
    watch.close()

    await watch.note(target(hass, entity_id), entity_id, set_check.BOUND_S)

    assert watch.entities == {}
    assert broker.subscribers["zigbee2mqtt/lamp"] == []


async def test_a_no_state_publish_keeps_an_earlier_publishs_longer_bound(hass, broker, live):
    entity_id = light(hass, broker)
    watch = set_check.Watch(hass, live)
    lamp = target(hass, entity_id)
    start = hass.loop.time()

    await watch.note(lamp, entity_id, set_check.BOUND_S)
    await watch.note(lamp, entity_id, set_check.NO_STATE_BOUND_S)

    assert watch.entities[entity_id].bound_at >= start + set_check.BOUND_S
    watch.close()


async def test_two_sends_noting_one_entity_at_once_both_own_it(hass, broker, live, monkeypatch):
    # A group's send writing a member that is also a target: both subscribes are in flight together.
    entity_id = light(hass, broker)
    subscribe = broker.subscribe

    async def slow(hass, topic, msg_callback):
        unsubscribe = await subscribe(hass, topic, msg_callback)
        msg_callback(SimpleNamespace(topic=topic, payload="{}", retain=False))  # before the entity is noted
        await asyncio.sleep(0)
        return unsubscribe

    monkeypatch.setattr(set_check.mqtt, "async_subscribe", slow)
    watch = set_check.Watch(hass, live)
    lamp = target(hass, entity_id)

    await asyncio.gather(
        watch.note(lamp, "light.room", set_check.BOUND_S), watch.note(lamp, entity_id, set_check.BOUND_S)
    )
    log(broker, LAMP_UNDELIVERED)

    assert watch.failures() == {"light.room": LAMP_UNDELIVERED, entity_id: LAMP_UNDELIVERED}
    assert len(broker.subscribers["zigbee2mqtt/lamp"]) == 1
    watch.close()
    assert broker.subscribers["zigbee2mqtt/lamp"] == []
