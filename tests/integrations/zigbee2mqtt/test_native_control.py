"""Zigbee2MQTT hue_native_control: set as an action needs it for its own publish, then the user's value put back."""

import asyncio
import json
import logging

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.deviceside.core.models import Target
from custom_components.deviceside.core.runtime import DATA
from custom_components.deviceside.integrations.zigbee2mqtt import bridge, bridge_cache, turn_on_for
from custom_components.deviceside.integrations.zigbee2mqtt import native_control as nc
from custom_components.deviceside.integrations.zigbee2mqtt.restore_journal import journal, locks

from .conftest import DEVICES, SET, call, light
from ...conftest import allow_experimental, refusal
from ...fixtures.zigbee2mqtt import GROUPS, IEEE, INFO, add_z2m_entity, add_z2m_group, z2m_entry

SENT = {"status": "sent", "integration": "zigbee2mqtt"}
DEVICE_REQUEST = "zigbee2mqtt/bridge/request/device/options"
DEVICE_RESPONSE = "zigbee2mqtt/bridge/response/device/options"
GROUP_REQUEST = "zigbee2mqtt/bridge/request/group/options"
GROUP_RESPONSE = "zigbee2mqtt/bridge/response/group/options"
MEMBER = "0x00124b00aaaaaaaa"
LOGGER = "custom_components.deviceside.integrations.zigbee2mqtt"
RED = {"entity_id": "light.lamp", "duration": 6, "rgb_color": [255, 0, 0]}


@pytest.fixture
def turn_ons(hass):
    return async_mock_service(hass, "light", "turn_on")


def hue_bulb(hass, entity_id: str = "light.lamp", manufacturer: str = "Philips") -> str:
    return add_z2m_entity(hass, z2m_entry(hass), entity_id, manufacturer=manufacturer)


def options(broker) -> list[bool]:
    """The hue_native_control value of each options request, in order."""
    return [request["payload"]["options"][nc.OPTION] for request in broker.requests()]


def timers(broker) -> list[dict]:
    return [p["payload"] for p in broker.published if p["topic"] == SET and "on_time" in p["payload"]]


def target(hass, entity_id: str) -> Target:
    entry = er.async_get(hass).async_get(entity_id)
    device = dr.async_get(hass).async_get(entry.device_id)
    return Target(entity_id, "light", "zigbee2mqtt", entry, device, None, hass.states.get(entity_id))


# turn_on_for: on for a color, then put back


async def test_hue_bulb_gets_it_on_for_the_turn_on_then_back_before_the_timer(
    hass, setup_production, broker, turn_ons, caplog
):
    hue_bulb(hass)

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        response = await call(hass, "turn_on_for", RED)

    assert response == {"light.lamp": SENT}
    on, back = broker.requests()
    assert (on["topic"], on["payload"]["id"], on["payload"]["options"]) == (DEVICE_REQUEST, IEEE, {nc.OPTION: True})
    assert back["payload"]["options"] == {nc.OPTION: False}
    assert broker.published.index(back) < broker.published.index(broker.timers()[0])
    assert len(turn_ons) == 1
    assert "Set Zigbee2MQTT's hue_native_control to True for light.lamp" in caplog.text
    assert "Set Zigbee2MQTT's hue_native_control to False for light.lamp" in caplog.text
    assert not any(r.levelno >= logging.INFO for r in caplog.records if r.name.startswith(LOGGER))
    assert not broker.listening(DEVICE_RESPONSE) and not broker.listening(INFO)


@pytest.mark.parametrize("field", [{"color_temp_kelvin": 3000}, {"hs_color": [120, 100]}, {"xy_color": [0.2, 0.3]}])
async def test_every_color_field_triggers_it(hass, setup_production, broker, turn_ons, field):
    hue_bulb(hass)

    await call(hass, "turn_on_for", {"entity_id": "light.lamp", "duration": 6, **field})

    assert options(broker) == [True, False]


async def test_option_already_on_sends_no_request(hass, setup_production, broker, turn_ons):
    hue_bulb(hass)
    broker.retained[INFO]["config"]["devices"][IEEE]["hue_native_control"] = True

    await call(hass, "turn_on_for", RED)

    assert broker.requests() == []
    assert len(broker.timers()) == 1


async def test_device_missing_from_settings_gets_it(hass, setup_production, broker, turn_ons):
    hue_bulb(hass)
    broker.retained[INFO] = {"config": {}}

    await call(hass, "turn_on_for", RED)

    assert options(broker) == [True, False]


async def test_other_brands_are_left_alone(hass, setup_production, broker, turn_ons):
    hue_bulb(hass, manufacturer="IKEA of Sweden")
    # Placed by its Zigbee strings, so turn_on_for takes it as `other`.
    broker.retained[DEVICES] = [
        {"ieee_address": IEEE, "manufacturer": "IKEA of Sweden", "model_id": "TRADFRI", "interview_completed": True}
    ]
    allow_experimental(hass)  # class `other`

    await call(hass, "turn_on_for", RED)

    assert broker.requests() == []
    assert broker.topics == []
    assert len(broker.timers()) == 1


@pytest.mark.parametrize("fields", [{}, {"brightness": 50}, {"transition": 1}])
async def test_without_a_color_nothing_is_read(hass, setup_production, broker, turn_ons, fields):
    hue_bulb(hass)

    await call(hass, "turn_on_for", {"entity_id": "light.lamp", "duration": 6, **fields})

    assert broker.requests() == []
    assert broker.topics == []


# A failed set: a warning, and the command goes out as the option stands, with nothing to put back


@pytest.mark.parametrize(
    ("break_it", "reason", "sent"),
    [
        pytest.param(lambda broker: broker.retained.pop(INFO), "no bridge/info from Zigbee2MQTT", [], id="no_info"),
        # Z2M may have applied a write it never answered, so it is put back.
        pytest.param(
            lambda broker: setattr(broker, "reply", None), "no reply from Zigbee2MQTT", [True, False], id="no_reply"
        ),
        pytest.param(
            lambda broker: setattr(broker, "reply", {"status": "error", "error": "Device 'x' does not exist"}),
            "Device 'x' does not exist",
            [True],
            id="error_reply",
        ),
        pytest.param(lambda broker: setattr(broker, "reply", {}), "status None", [True], id="no_status"),
        pytest.param(
            lambda broker: setattr(broker, "reply", "not json"),
            "no reply from Zigbee2MQTT",
            [True, False],
            id="unreadable",
        ),
    ],
)
async def test_failed_set_warns_and_still_sends(
    hass, setup_production, broker, turn_ons, caplog, break_it, reason, sent
):
    hue_bulb(hass)
    break_it(broker)

    response = await call(hass, "turn_on_for", RED)

    assert response == {"light.lamp": SENT}
    assert len(turn_ons) == 1 and len(broker.timers()) == 1
    assert options(broker) == sent
    assert (
        "Couldn't set Zigbee2MQTT's hue_native_control to True for light.lamp, so the command goes out as the option "
        "stands: "
    ) in caplog.text
    assert reason in caplog.text
    assert not broker.listening(DEVICE_RESPONSE)


async def test_a_failed_restore_warns_and_stays_owed(hass, setup_production, broker, turn_ons, caplog):
    hue_bulb(hass)
    publish = broker.publish

    def refuse_after_the_first(service):
        if broker.requests():
            broker.reply = {"status": "error", "error": "refused"}
        publish(service)

    hass.services.async_register("mqtt", "publish", refuse_after_the_first)

    response = await call(hass, "turn_on_for", RED)

    assert response == {"light.lamp": SENT}
    assert options(broker) == [True, False]
    assert len(broker.timers()) == 1
    assert "Couldn't put Zigbee2MQTT's hue_native_control back to False for light.lamp, so it stays True" in caplog.text
    assert (await journal(hass)).owed == {f"device/{IEEE}": False}


# Groups: the group's own option, and only with a Hue member

BRIDGE_GROUPS = [{"id": 3, "members": []}, {"id": 7, "members": [{"ieee_address": MEMBER, "endpoint": 11}]}]
DOWNSTAIRS = {"entity_id": "light.downstairs", "duration": 6, "rgb_color": [255, 0, 0]}


def group_with_member(hass, manufacturer: str) -> None:
    entry = z2m_entry(hass)
    add_z2m_entity(hass, entry, "light.member", ieee=MEMBER, manufacturer=manufacturer)
    add_z2m_group(hass, entry, "light.downstairs", group_id=7)


async def test_group_with_a_hue_member_gets_the_group_option(hass, setup_production, broker, turn_ons):
    group_with_member(hass, "Philips")
    broker.retained[GROUPS] = BRIDGE_GROUPS
    broker.retained[INFO]["config"]["devices"][MEMBER] = {"hue_native_control": True}  # a member's own doesn't count

    response = await call(hass, "turn_on_for", DOWNSTAIRS)

    assert response == {"light.downstairs": SENT}
    assert [(r["topic"], r["payload"]["id"]) for r in broker.requests()] == [(GROUP_REQUEST, "7")] * 2
    assert options(broker) == [True, False]
    assert not broker.listening(GROUP_RESPONSE)


async def test_group_with_option_on_sends_no_request(hass, setup_production, broker, turn_ons):
    group_with_member(hass, "Philips")
    broker.retained[GROUPS] = BRIDGE_GROUPS
    broker.retained[INFO]["config"]["groups"]["7"] = {"hue_native_control": True}

    await call(hass, "turn_on_for", DOWNSTAIRS)

    assert broker.requests() == []


async def test_a_group_bridge_groups_doesn_t_list_is_sent_whole(hass, setup_production, broker, turn_ons, monkeypatch):
    """turn_on_for refuses a group it can't list (test_classes.py), so its check is patched out: native control
    leaves the group alone, as a call that isn't turn_on_for's would find it."""
    group_with_member(hass, "Philips")
    broker.retained[GROUPS] = [{"id": 3, "members": []}]
    monkeypatch.setattr(turn_on_for, "expand_group", lambda hass, target: [])

    assert await refusal(hass, "turn_on_for", DOWNSTAIRS) == "experimental_off"
    allow_experimental(hass)
    await call(hass, "turn_on_for", DOWNSTAIRS)

    assert broker.requests() == []
    assert len(broker.timers()) == 1


async def test_a_group_with_no_hue_member_is_left_alone(hass, setup_production, broker, turn_ons):
    group_with_member(hass, "IKEA of Sweden")
    broker.retained[GROUPS] = BRIDGE_GROUPS
    # Placed by its Zigbee strings, so turn_on_for takes it as `other`.
    broker.retained[DEVICES] = [
        {"ieee_address": MEMBER, "manufacturer": "IKEA of Sweden", "model_id": "TRADFRI", "interview_completed": True}
    ]
    allow_experimental(hass)  # an `other` member

    await call(hass, "turn_on_for", DOWNSTAIRS)

    assert broker.requests() == []
    assert len(broker.timers()) == 1


async def test_no_bridge_groups_warns_and_still_sends(hass, setup_production, broker, turn_ons, caplog, monkeypatch):
    group_with_member(hass, "Philips")
    # bridge/groups isn't held, so the group's members can't be listed: it counts as Experimental, and turn_on_for's
    # refusal of it (test_classes.py) is patched out, to reach native control.
    monkeypatch.setattr(turn_on_for, "expand_group", lambda hass, target: [])
    allow_experimental(hass)

    response = await call(hass, "turn_on_for", DOWNSTAIRS)

    assert response == {"light.downstairs": SENT}
    assert len(broker.timers()) == 1
    assert "so the command goes out as the option stands: " in caplog.text
    assert "no bridge/groups from Zigbee2MQTT" in caplog.text


# Ramps: off for brightness; saturation and hue leave it alone


async def test_brightness_ramp_turns_it_off_for_its_publish(hass, setup_production, clock, broker):
    entity_id = light(hass, broker, manufacturer="Philips")
    broker.retained[INFO]["config"]["devices"][IEEE]["hue_native_control"] = True

    response = await call(hass, "raise_brightness", {"entity_id": entity_id})
    await hass.async_block_till_done()  # the restore, which runs after the call returns

    assert response == {entity_id: SENT}
    off, back = broker.requests()
    fade = next(p for p in broker.published if p["topic"] == SET)
    assert broker.published.index(off) < broker.published.index(fade) < broker.published.index(back)
    assert options(broker) == [False, True]


@pytest.mark.parametrize("action", ["raise_saturation", "raise_hue"])
async def test_a_color_ramp_leaves_it_alone(hass, setup_production, clock, broker, action):
    entity_id = light(hass, broker, manufacturer="Philips")

    await call(hass, action, {"entity_id": entity_id})

    assert broker.requests() == []


# The context manager itself


async def test_restore_runs_when_the_body_raises(hass, setup_production, broker):
    hue_bulb(hass)

    with pytest.raises(RuntimeError, match="publish failed"):
        async with nc.native_control(hass, target(hass, "light.lamp"), True):
            assert options(broker) == [True]
            raise RuntimeError("publish failed")
    await nc.settled(hass, target(hass, "light.lamp"))

    assert options(broker) == [True, False]


async def test_cancel_during_the_set_still_restores(hass, setup_production, broker, monkeypatch):
    hue_bulb(hass)
    answered = asyncio.Event()
    ensure = nc.ensure_options

    async def slow_answer(*args, **kwargs):
        await ensure(*args, **kwargs)
        await answered.wait()  # the request is out; Z2M's answer is late

    monkeypatch.setattr(nc, "ensure_options", slow_answer)

    async def body() -> None:
        async with nc.native_control(hass, target(hass, "light.lamp"), True):
            pytest.fail("a cancelled set runs no body")

    task = asyncio.create_task(body())
    while options(broker) != [True]:
        await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    answered.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert options(broker) == [True, False]


async def test_lock_keeps_opposite_needs_apart(hass, setup_production, broker):
    hue_bulb(hass)
    lamp = target(hass, "light.lamp")
    order: list[str] = []
    entered = asyncio.Event()
    release = asyncio.Event()

    async def hold(need: bool, name: str) -> None:
        async with nc.native_control(hass, lamp, need):
            order.append(f"{name} in")
            entered.set()
            if need:
                await release.wait()
            order.append(f"{name} out")

    first = asyncio.create_task(hold(True, "on"))
    await entered.wait()
    # The option stays absent in bridge/info, so a need of False writes nothing: the lock is all that holds it back.
    second = asyncio.create_task(hold(False, "off"))
    for _ in range(10):
        await asyncio.sleep(0)
    assert order == ["on in"]

    release.set()
    await asyncio.gather(first, second)
    assert order == ["on in", "on out", "off in", "off out"]


async def test_lock_is_per_device(hass, setup_production, broker):
    entry = z2m_entry(hass)
    add_z2m_entity(hass, entry, "light.one", ieee=IEEE, manufacturer="Philips")
    add_z2m_entity(hass, entry, "light.two", ieee=MEMBER, manufacturer="Philips")
    order: list[str] = []
    release = asyncio.Event()

    async def hold(entity_id: str) -> None:
        async with nc.native_control(hass, target(hass, entity_id), True):
            order.append(entity_id)
            await release.wait()

    tasks = [asyncio.create_task(hold("light.one")), asyncio.create_task(hold("light.two"))]
    for _ in range(1000):  # capped: a hold that never starts fails here, not hangs
        if len(order) == 2:
            break
        await asyncio.sleep(0)
    assert len(order) == 2
    release.set()
    await asyncio.gather(*tasks)

    assert sorted(order) == ["light.one", "light.two"]


async def test_state_reply_ends_the_wait(hass, setup_production, clock, broker, monkeypatch):
    monkeypatch.setattr(nc, "_REPLY_CAP_S", 5.0)
    entity_id = light(hass, broker, manufacturer="Philips")
    loop = asyncio.get_running_loop()

    start = loop.time()
    async with nc.native_control(hass, target(hass, entity_id), True):
        broker.deliver("zigbee2mqtt/lamp", '{"state": "ON"}')
    await nc.settled(hass, target(hass, entity_id))

    assert loop.time() - start < 1
    assert options(broker) == [True, False]
    assert not broker.subscribers["zigbee2mqtt/lamp"]


async def test_failed_state_subscribe_still_restores_after_the_cap(
    hass, setup_production, clock, broker, monkeypatch, caplog
):
    entity_id = light(hass, broker, manufacturer="Philips")
    subscribe = bridge.mqtt.async_subscribe

    async def refuse_state(hass, topic, *args, **kwargs):
        if topic == "zigbee2mqtt/lamp":
            raise RuntimeError("MQTT is not connected")
        return await subscribe(hass, topic, *args, **kwargs)

    monkeypatch.setattr(bridge.mqtt, "async_subscribe", refuse_state)
    loop = asyncio.get_running_loop()

    start = loop.time()
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        async with nc.native_control(hass, target(hass, entity_id), True):
            pass
        await nc.settled(hass, target(hass, entity_id))

    assert loop.time() - start >= nc._REPLY_CAP_S
    assert options(broker) == [True, False]
    assert f"Couldn't subscribe to {entity_id}'s state, so Zigbee2MQTT's answer isn't awaited" in caplog.text
    assert "zigbee2mqtt/lamp" not in caplog.text


async def test_call_returns_before_the_reply_and_restores_after_it(hass, setup_production, clock, broker, monkeypatch):
    monkeypatch.setattr(nc, "_REPLY_CAP_S", 5.0)
    entity_id = light(hass, broker, manufacturer="Philips")
    broker.retained[INFO]["config"]["devices"][IEEE]["hue_native_control"] = True
    loop = asyncio.get_running_loop()

    start = loop.time()
    response = await call(hass, "raise_brightness", {"entity_id": entity_id})

    assert response == {entity_id: SENT}
    assert loop.time() - start < 1
    assert options(broker) == [False]  # still the action's value: Z2M hasn't answered the fade
    broker.deliver("zigbee2mqtt/lamp", '{"state": "ON"}')
    await hass.async_block_till_done()
    assert options(broker) == [False, True]


async def test_next_call_on_the_target_waits_for_the_restore(hass, setup_production, clock, broker, monkeypatch):
    monkeypatch.setattr(nc, "_REPLY_CAP_S", 5.0)
    entity_id = light(hass, broker, manufacturer="Philips")
    lamp = target(hass, entity_id)
    order: list[str] = []

    async with nc.native_control(hass, lamp, True):
        order.append("first")

    async def second() -> None:
        async with nc.native_control(hass, lamp, False):
            order.append(f"second, after {options(broker)}")

    task = asyncio.create_task(second())
    for _ in range(10):
        await asyncio.sleep(0)
    assert order == ["first"]  # held by the lock until Z2M answers the first

    broker.deliver("zigbee2mqtt/lamp", '{"state": "ON"}')
    async with asyncio.timeout(1):
        await task
    assert order == ["first", "second, after [True, False]"]


async def test_shutdown_during_the_reply_wait_still_restores(hass, setup_production, clock, broker, monkeypatch):
    monkeypatch.setattr(nc, "_REPLY_CAP_S", 5.0)
    entity_id = light(hass, broker, manufacturer="Philips")

    async with nc.native_control(hass, target(hass, entity_id), True):
        pass
    await asyncio.sleep(0)
    nc._restores(hass)[("device", IEEE)].cancel()
    await nc.settled(hass, target(hass, entity_id))  # ends the wait rather than raising into the caller

    assert options(broker) == [True, False]
    assert not locks(hass)[("device", IEEE)].locked()


async def test_caller_cancelled_in_the_body_still_restores(hass, setup_production, broker):
    hue_bulb(hass)
    inside = asyncio.Event()

    async def body() -> None:
        async with nc.native_control(hass, target(hass, "light.lamp"), True):
            inside.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(body())
    await inside.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await nc.settled(hass, target(hass, "light.lamp"))

    assert options(broker) == [True, False]


async def test_settled_is_immediate_without_a_change(hass, setup_production, broker):
    hue_bulb(hass)
    broker.retained[INFO]["config"]["devices"][IEEE]["hue_native_control"] = True

    async with nc.native_control(hass, target(hass, "light.lamp"), True):
        pass
    await nc.settled(hass, target(hass, "light.lamp"))

    assert broker.requests() == []
    assert not locks(hass)[("device", IEEE)].locked()


async def test_a_timer_after_a_brightness_fade_waits_for_its_restore(
    hass, setup_production, clock, broker, monkeypatch
):
    monkeypatch.setattr(nc, "_REPLY_CAP_S", 5.0)
    entity_id = light(hass, broker, manufacturer="Philips")
    broker.retained[INFO]["config"]["devices"][IEEE]["hue_native_control"] = True

    await call(hass, "raise_brightness", {"entity_id": entity_id})
    timer = hass.async_create_task(call(hass, "turn_on_for", {"entity_id": entity_id, "duration": 6}))
    for _ in range(10):
        await asyncio.sleep(0)
    assert timers(broker) == []  # the option is still the fade's

    broker.deliver("zigbee2mqtt/lamp", '{"state": "ON"}')
    async with asyncio.timeout(1):
        await timer
    assert options(broker) == [False, True]
    assert timers(broker) == [{"state": "ON", "on_time": 6}]


async def test_settled_without_an_address_returns(hass):
    await nc.settled(hass, Target("light.x", "light", "zigbee2mqtt", None, None, None, None))


async def test_reply_cap_is_one_second():
    assert nc._REPLY_CAP_S == 1.0


async def test_no_need_writes_nothing(hass, setup_production, broker):
    hue_bulb(hass)

    async with nc.native_control(hass, target(hass, "light.lamp"), None):
        pass

    assert broker.topics == []


# The debug line stamps the option's value on each set


def hold_info(hass, broker) -> None:
    """The bridge cache holding the broker's bridge/info as it stands, as it does once MQTT is up."""
    cache = bridge_cache.BridgeCache()
    cache._payloads["info"] = json.dumps(broker.retained[INFO])
    hass.data[DATA].integration_state[bridge_cache._KEY] = cache


def sets(wire) -> list[tuple]:
    """The args of each set's debug line, its options last."""
    return [r.args for r in wire.records if r.levelno == logging.DEBUG and r.msg.endswith(" with %s")]


def stamps(wire) -> list:
    return [args[-1][nc.OPTION] for args in sets(wire)]


@pytest.mark.parametrize(
    "info, stamp", [({"hue_native_control": True}, True), ({"hue_native_control": False}, False), ({}, False)]
)
async def test_a_set_is_stamped_with_the_option_from_bridge_info(hass, setup_production, broker, wire, info, stamp):
    entity_id = light(hass, broker)
    broker.retained[INFO]["config"]["devices"][IEEE] = info
    hold_info(hass, broker)

    await call(hass, "stop", {"entity_id": entity_id})

    assert stamps(wire) == [stamp]
    assert sets(wire)[0][1] == {"brightness_move": "stop"}  # the payload stays the published one


async def test_a_confirmed_write_shows_in_the_stamp_then_the_users_value_returns(
    hass, setup_production, clock, broker, wire
):
    entity_id = light(hass, broker, manufacturer="Philips")
    broker.retained[INFO]["config"]["devices"][IEEE]["hue_native_control"] = True
    hold_info(hass, broker)

    await call(hass, "raise_brightness", {"entity_id": entity_id})
    await hass.async_block_till_done()  # the restore
    await call(hass, "stop", {"entity_id": entity_id})

    assert stamps(wire) == [False, True]


async def test_the_turn_on_inside_a_flip_is_stamped_on(hass, setup_production, broker, turn_ons, wire):
    hue_bulb(hass)
    hold_info(hass, broker)

    await call(hass, "turn_on_for", RED)

    # The HA light.turn_on inside the flip; the timer waits for the restore, so it carries the user's value.
    assert stamps(wire) == [True, False]


async def test_a_group_is_stamped_from_the_group_section(hass, setup_production, broker):
    group_with_member(hass, "Philips")
    broker.retained[GROUPS] = BRIDGE_GROUPS
    broker.retained[INFO]["config"]["groups"]["7"] = {"hue_native_control": True}
    broker.retained[INFO]["config"]["devices"][MEMBER] = {"hue_native_control": False}
    hold_info(hass, broker)

    assert bridge.logged_options(hass, target(hass, "light.downstairs")) == {nc.OPTION: True}


async def test_the_stamp_is_unknown_without_an_address(hass, setup_production):
    assert bridge.logged_options(hass, Target("light.x", "light", "zigbee2mqtt", None, None, None, None)) == {
        nc.OPTION: None
    }


async def test_nothing_is_computed_while_debug_logging_is_off(hass, setup_production, broker, monkeypatch):
    entity_id = light(hass, broker)
    monkeypatch.setattr(bridge, "logged_options", lambda *args: pytest.fail("looked up with debug logging off"))

    await call(hass, "stop", {"entity_id": entity_id})


# The restore journal: owed until Z2M confirms the user's value is back


async def test_a_confirmed_restore_leaves_nothing_owed(hass, setup_production, broker, turn_ons):
    hue_bulb(hass)

    await call(hass, "turn_on_for", RED)

    assert (await journal(hass)).owed == {}


async def test_a_later_owe_keeps_the_users_value_owed(hass, setup_production):
    held = await journal(hass)

    assert await held.add(("device", IEEE), False)
    assert not await held.add(("device", IEEE), True)
    await held.strike(("device", IEEE), True)

    assert held.owed == {f"device/{IEEE}": False}
    await held.strike(("device", IEEE), False)
    assert held.owed == {}


@pytest.mark.parametrize("failing", ["add", "strike"])
async def test_a_failing_journal_warns_and_the_restore_still_runs(
    hass, setup_production, broker, turn_ons, monkeypatch, caplog, failing
):
    """The journal is a safety net: a store that can't write is logged, and the call and the restore go on."""
    hue_bulb(hass)
    held = await journal(hass)

    async def broken(*args):
        raise OSError("disk full")

    monkeypatch.setattr(held, failing, broken)

    with caplog.at_level(logging.WARNING):
        await call(hass, "turn_on_for", RED)

    assert "disk full" in caplog.text
    assert options(broker) == [True, False]


async def test_a_cancel_during_the_journal_save_still_restores(hass, setup_production, broker, monkeypatch):
    """The save runs inside the shielded set, as its request goes out: a cancel there lets the set finish, then
    puts the option back and strikes the entry."""
    hue_bulb(hass)
    held = await journal(hass)
    saving, release = asyncio.Event(), asyncio.Event()
    save = held._store.async_save

    async def slow_save(data):
        saving.set()
        await release.wait()
        await save(data)

    monkeypatch.setattr(held._store, "async_save", slow_save)

    async def body() -> None:
        async with nc.native_control(hass, target(hass, "light.lamp"), True):
            pytest.fail("a cancelled set runs no body")

    task = asyncio.create_task(body())
    await saving.wait()
    assert broker.requests() == []  # journaled before the request goes out
    task.cancel()
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert options(broker) == [True, False]
    assert held.owed == {}


async def test_an_option_already_right_journals_nothing(hass, setup_production, broker, monkeypatch):
    hue_bulb(hass)
    held = await journal(hass)
    saves: list[dict] = []

    async def counted(data):
        saves.append(dict(data))

    monkeypatch.setattr(held._store, "async_save", counted)

    async with nc.native_control(hass, target(hass, "light.lamp"), False):
        pass

    assert (broker.requests(), saves) == ([], [])


async def test_a_refused_set_strikes_its_entry(hass, setup_production, broker):
    hue_bulb(hass)
    broker.reply = {"status": "error", "error": "refused"}

    async with nc.native_control(hass, target(hass, "light.lamp"), True):
        pass
    await nc.settled(hass, target(hass, "light.lamp"))

    assert options(broker) == [True]
    assert (await journal(hass)).owed == {}


async def test_a_same_need_call_pays_a_restore_still_owed(hass, setup_production, broker):
    """An earlier restore failed, leaving the option at this call's need: the call runs on it, then writes the
    user's value back."""
    hue_bulb(hass)
    broker.retained[INFO]["config"]["devices"][IEEE][nc.OPTION] = True
    await (await journal(hass)).add(("device", IEEE), False)

    async with nc.native_control(hass, target(hass, "light.lamp"), True):
        assert options(broker) == []
    await nc.settled(hass, target(hass, "light.lamp"))

    assert options(broker) == [False]
    assert (await journal(hass)).owed == {}


async def test_an_opposite_need_call_restores_the_owed_value(hass, setup_production, broker):
    """The user's value is off; an earlier restore failed and left it on. A call needing it off sets it off, and the
    restore writes the owed off, not the on it found."""
    hue_bulb(hass)
    broker.retained[INFO]["config"]["devices"][IEEE][nc.OPTION] = True
    await (await journal(hass)).add(("device", IEEE), False)

    async with nc.native_control(hass, target(hass, "light.lamp"), False):
        assert options(broker) == [False]
    await nc.settled(hass, target(hass, "light.lamp"))

    assert options(broker) == [False, False]
    assert (await journal(hass)).owed == {}


async def test_an_unreadable_journal_runs_as_if_none_were_owed(hass, setup_production, broker, monkeypatch, caplog):
    hue_bulb(hass)

    async def unreadable(hass):
        raise OSError("unreadable")

    monkeypatch.setattr(nc, "journal", unreadable)

    async with nc.native_control(hass, target(hass, "light.lamp"), True):
        pass
    await nc.settled(hass, target(hass, "light.lamp"))

    assert options(broker) == [True, False]
    assert "Couldn't read the hue_native_control restore journal" in caplog.text


async def test_a_cancelled_set_that_was_refused_strikes_its_entry(hass, setup_production, broker, monkeypatch):
    hue_bulb(hass)
    broker.reply = {"status": "error", "error": "refused"}
    answered = asyncio.Event()
    ensure = nc.ensure_options

    async def slow_refusal(*args, **kwargs):
        await answered.wait()
        await ensure(*args, **kwargs)

    monkeypatch.setattr(nc, "ensure_options", slow_refusal)

    async def body() -> None:
        async with nc.native_control(hass, target(hass, "light.lamp"), True):
            pytest.fail("a cancelled set runs no body")

    task = asyncio.create_task(body())
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    answered.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert options(broker) == [True]
    assert (await journal(hass)).owed == {}
