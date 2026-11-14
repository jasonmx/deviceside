"""Zigbee2MQTT's bridge: its groups, device features and entity options from retained topics, and options requests."""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Literal
from uuid import uuid4

from homeassistant.components import mqtt
from homeassistant.core import HomeAssistant, callback

from . import bridge_cache, set_check
from .addressing import bridge_topic, options_address, state_topic, topic
from ...core.models import Target

_LOGGER = logging.getLogger(__name__)

# Retained topics arrive once HA's debounced subscribe reaches the broker; the reply takes one settings write.
OPTION = "hue_native_control"  # the entity option that changes the Zigbee commands a Hue light gets
_RETAINED_TIMEOUT_S = 2.0
_REPLY_TIMEOUT_S = 5.0


async def group_members(hass: HomeAssistant, group: str) -> list[str]:
    """The IEEE addresses of a Z2M group's members, from bridge/groups; none for a group it doesn't list."""
    return members_of(await retained(hass, "groups"), group)


def members_of(groups: list[dict], group: str) -> list[str]:
    """The IEEE addresses of a group's members in a parsed bridge/groups; none for a group it doesn't list."""
    members = next((g.get("members", []) for g in groups if str(g.get("id")) == group), [])
    return [member["ieee_address"] for member in members if member.get("ieee_address")]


async def has_feature(hass: HomeAssistant, ieees: list[str], name: str) -> bool:
    """Whether every one of these devices' Z2M definitions exposes a feature by this name, from bridge/devices: Z2M
    gives a group only the features its members share."""
    devices = await retained(hass, "devices")
    exposing = {
        device.get("ieee_address")
        for device in devices
        if _exposes(name, (device.get("definition") or {}).get("exposes", []))
    }
    return bool(ieees) and set(ieees) <= exposing


def _exposes(name: str, features: list[dict]) -> bool:
    """Whether the feature is in this exposes list, or nested in one of its composites (a light's features)."""
    return any(feature.get("name") == name or _exposes(name, feature.get("features", [])) for feature in features)


async def retained(hass: HomeAssistant, suffix: str) -> Any:
    """The parsed retained message on bridge/<suffix>; raises TimeoutError when Z2M hasn't published one, or published
    one that doesn't parse.

    bridge/devices, bridge/groups and bridge/info come from the bridge cache when it holds them (bridge_cache.py), else
    from a fresh subscription. A cached result is shared by every caller: read it, never change it. A held bridge/info
    doesn't yet show options confirmed since Z2M last published it; `ensure_options` reads those too.
    """
    if (
        suffix in bridge_cache.HELD
        and (cache := bridge_cache.get(hass)) is not None
        and (held := cache.parsed(suffix)) is not None
    ):
        return held
    return parse(await first_retained(hass, suffix), f"bridge/{suffix}")


def parse(payload: str, what: str) -> Any:
    """A retained payload, parsed; TimeoutError when it doesn't parse, as a malformed message counts as none."""
    try:
        return json.loads(payload)
    except ValueError:
        raise TimeoutError(f"no parseable {what} from Zigbee2MQTT") from None


async def first_retained(hass: HomeAssistant, suffix: str) -> str:
    """The raw retained message on bridge/<suffix>, from a fresh subscription; raises TimeoutError when none comes.

    Its arrival shows that subscriptions queued before it have reached the broker too.
    """
    payload: asyncio.Future[str] = hass.loop.create_future()
    unsubscribe = await mqtt.async_subscribe(hass, bridge_topic(suffix), first_payload(payload))
    try:
        return await wait(payload, _RETAINED_TIMEOUT_S, f"no bridge/{suffix} from Zigbee2MQTT")
    finally:
        unsubscribe()


async def ensure_options(
    hass: HomeAssistant,
    kind: Literal["device", "group"],
    z2m_ids: list[str],
    wanted: Callable[[dict], dict],
    changed: Callable[[str], None],
    ahead: Callable[[str, dict], Awaitable[None]] | None = None,
) -> None:
    """Set each entity's options to what `wanted` returns from its current ones, where that is anything.

    With the bridge cache's reply topics live and bridge/info held, the current options come from the cache, with this
    and earlier calls' confirmed writes over them, and the call subscribes to nothing: a user's change reaches the
    cache through Z2M's republish of bridge/info. Otherwise it reads bridge/info fresh. Calls `changed` with each ID
    as Z2M confirms its options, so one set before a later failure is still reported; raises when bridge/info or a
    reply doesn't come, or a reply is an error. Awaits `ahead` with each ID and its options before its request goes out.
    """
    section = f"{kind}s"
    cache = bridge_cache.get(hass)
    shared = cache is not None and cache.replies_live
    pending: dict[str, asyncio.Future[dict]] = cache.pending if shared else {}
    transactions: list[str] = []
    unsubscribes: list[Callable[[], None]] = []
    try:
        current = await _options_source(hass, cache, kind, pending, unsubscribes)
        for z2m_id in z2m_ids:
            before = current(z2m_id)
            if not (options := wanted(before)):
                continue
            if ahead is not None:
                await ahead(z2m_id, options)
            transaction = uuid4().hex
            transactions.append(transaction)
            pending[transaction] = hass.loop.create_future()
            await publish(
                hass,
                bridge_topic(f"request/{kind}/options"),
                {"id": z2m_id, "options": options, "transaction": transaction},
            )
            answer = await wait(pending[transaction], _REPLY_TIMEOUT_S, "no reply from Zigbee2MQTT")
            if answer.get("status") != "ok":
                raise RuntimeError(answer.get("error") or f"status {answer.get('status')}")
            if cache is not None:
                cache.confirmed[(section, z2m_id)] = _after(answer, before, options)
            changed(z2m_id)
    finally:
        for unsubscribe in unsubscribes:
            unsubscribe()
        for transaction in transactions:
            pending.pop(transaction, None)


async def _options_source(
    hass: HomeAssistant,
    cache: bridge_cache.BridgeCache | None,
    kind: str,
    pending: dict[str, asyncio.Future[dict]],
    unsubscribes: list[Callable[[], None]],
) -> Callable[[str], dict]:
    """Each entity's current options: from the cache when its reply topics are live and it holds bridge/info, else
    from a fresh bridge/info, subscribing for the replies too when the cache's aren't live (into `unsubscribes`)."""
    section = f"{kind}s"
    shared = cache is not None and cache.replies_live
    if shared and cache.parsed("info") is not None:
        return lambda z2m_id: cache.settings(section, z2m_id) or {}
    info: asyncio.Future[str] = hass.loop.create_future()
    # HA debounces subscribes, so async_subscribe returns before the broker has them. Queued together, the two reach
    # the broker in one batch: the retained bridge/info arriving shows the reply topic is subscribed too, before a
    # request goes out. With the cache's reply topics live, bridge/info alone is read.
    if not shared:
        reply_topic = bridge_topic(f"response/{kind}/options")
        unsubscribes.append(await mqtt.async_subscribe(hass, reply_topic, bridge_cache.reply_router(pending)))
    unsubscribes.append(await mqtt.async_subscribe(hass, bridge_topic("info"), first_payload(info)))
    fresh = parse(await wait(info, _RETAINED_TIMEOUT_S, "no bridge/info from Zigbee2MQTT"), "bridge/info")
    return lambda z2m_id: fresh.get("config", {}).get(section, {}).get(z2m_id, {})


def _after(answer: dict, before: dict, sent: dict) -> dict:
    """The entity's options after a confirmed write: Z2M's own (its reply's data.to), else what was sent over before."""
    data = answer.get("data")
    if isinstance(data, dict) and isinstance(to := data.get("to"), dict):
        return to
    return {**before, **sent}


def logged_options(hass: HomeAssistant, target: Target) -> dict:
    """The option that changes how Z2M encodes a set, for its debug line: hue_native_control as of this publish, since
    the same payload sends different Zigbee commands with it on and off.

    Read from the bridge cache, which lays confirmed writes over bridge/info (an absent option is Z2M's default, off).
    None when it can't be known: no cache, no bridge/info held, or no address. Stamped for every target, Hue or not,
    so no Hue lookup sits on the publish path; Z2M ignores the option on other devices, so their stamp stays false.
    Call only while debug logging is on.

    Known limit: a set whose write timed out is not in the cache, so the stamp shows the prior value (that path logs
    a warning).
    """
    cache = bridge_cache.get(hass)
    address = options_address(target)
    settings = cache.settings(f"{address[0]}s", address[1]) if cache is not None and address is not None else None
    return {OPTION: None if settings is None else bool(settings.get(OPTION))}


async def publish_set(hass: HomeAssistant, target: Target, payload: dict) -> None:
    """Publish a command to the target's set topic, logged (never the topic) and noted for the call's set check
    (set_check.py). A read, sent on the same topic, goes through publish()."""
    await set_check.note(hass, target, payload)
    if _LOGGER.isEnabledFor(logging.DEBUG):
        _LOGGER.debug("%s: sending mqtt set %s with %s", target.reported_id, payload, logged_options(hass, target))
    await publish(hass, topic(target, "set"), payload)


async def publish(hass: HomeAssistant, mqtt_topic: str, payload: dict) -> None:
    await mqtt.async_publish(hass, mqtt_topic, json.dumps(payload))


def first_payload(future: asyncio.Future[str]) -> Callable[[mqtt.ReceiveMessage], None]:
    """A subscription callback that resolves future with the first message's payload."""

    @callback
    def received(msg: mqtt.ReceiveMessage) -> None:
        if not future.done():
            future.set_result(msg.payload)

    return received


async def wait(future: asyncio.Future[Any], timeout: float, what: str) -> Any:
    """The future's result, or TimeoutError(what) when it takes longer than timeout."""
    try:
        async with asyncio.timeout(timeout):
            return await future
    except TimeoutError:
        raise TimeoutError(what) from None


async def state_reply(hass: HomeAssistant, target: Target) -> tuple[asyncio.Future[None] | None, Callable[[], None]]:
    """A future for Z2M's next state publish for the target, subscribed before the caller publishes, and its
    unsubscribe; no future when the target has no state topic or the subscribe failed."""
    if (light_topic := state_topic(target)) is None:
        return None, lambda: None
    published: asyncio.Future[None] = hass.loop.create_future()

    @callback
    def received(msg: mqtt.ReceiveMessage) -> None:
        # A retained message is old state the broker resends on subscribe, not Z2M's answer.
        if not msg.retain and not published.done():
            published.set_result(None)

    try:
        # HA's own light entity subscribes to this topic, so the subscription is live at once.
        return published, await mqtt.async_subscribe(hass, light_topic, received)
    except Exception as err:  # noqa: BLE001 - each caller goes on without the reply
        _LOGGER.debug(
            "Couldn't subscribe to %s's state, so Zigbee2MQTT's answer isn't awaited: %r", target.entity_id, err
        )
        return None, lambda: None
