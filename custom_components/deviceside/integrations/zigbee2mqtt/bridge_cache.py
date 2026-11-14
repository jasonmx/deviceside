"""Zigbee2MQTT's bridge topics, subscribed for the entry's life rather than per call.

Each fresh subscription costs a call HA's subscribe cooldown (0.1 s) plus a broker round trip, and a group action
read bridge/groups two or three times in series. Held here instead:

- bridge/devices, bridge/groups and bridge/info, the latest of each as Z2M publishes it, parsed once per payload. All
  three are retained; Z2M republishes devices and groups whenever a device or group changes (lib/extension/bridge.ts),
  and info on every settings change and on start, so a user's change of an option reaches the cache (probe in
  docs/integration-internals.md, Tested behavior).
- The options response topics, routed to whichever `ensure_options` call is waiting on the reply's transaction.
- bridge/logging, whose error lines go to whichever calls listen (`listen_log`): a set's failure is logged there and
  nowhere else (set_check.py). Each line is parsed only while a call listens.

The response topics and bridge/logging carry no retained message, so nothing shows they are live; the retained
bridge/state, subscribed after them, does. Queued after them, it reaches the broker in their batch or a later one
(HA sends a batch once the last is acknowledged), and on a reconnect HA resubscribes in topic order, which puts
bridge/state after them too: its arrival shows they are subscribed (PR #69's barrier, made once per connection rather
than per call). A disconnect clears everything until the resubscribe brings it back, so a cache whose subscriptions
died with the MQTT client reads fresh, as before.

Z2M republishes bridge/info just before it answers an options request (microseconds apart on the rig), so the held
bridge/info usually has the new value by the time the reply arrives. Nothing promises that order, so `ensure_options`
also writes each confirmed entity's options through to `confirmed` (the reply's `data.to`, else what it sent over the
options before), which `settings` reads over bridge/info's. Z2M writes the setting before it publishes either, so any
bridge/info arriving later holds it too: each new one drops the overlay.
"""

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from .addressing import bridge_topic
from ...core.runtime import DATA

_LOGGER = logging.getLogger(__name__)

_KEY = "zigbee2mqtt_bridge_cache"
HELD = ("devices", "groups", "info")
KINDS = ("device", "group")
# Retained by Z2M ("online"), and after the response topics in both subscribe orders.
_BARRIER = "state"
LOG = "logging"


class BridgeCache:
    """The held bridge topics, and the options replies awaited by transaction."""

    def __init__(self, on_online: Callable[[], None] = lambda: None) -> None:
        self._on_online = on_online
        self._payloads: dict[str, str] = {}
        self._parsed: dict[str, tuple[str, Any]] = {}
        # bridge/devices by IEEE address, for the parsed list it was built from.
        self._devices: tuple[Any, dict[str, dict]] | None = None
        self.pending: dict[str, asyncio.Future[dict]] = {}
        # Options Z2M confirmed since its last bridge/info, by (section, Z2M ID): Z2M's whole options after the write.
        self.confirmed: dict[tuple[str, str], dict] = {}
        # Whether the response topics and bridge/logging are subscribed, so a request's reply reaches `pending` and a
        # log line the listeners.
        self.replies_live = False
        self._log_listeners: list[Callable[[str], None]] = []
        self._unsubscribes: list[Callable[[], None]] = []

    def parsed(self, suffix: str) -> Any | None:
        """The latest bridge/<suffix>, parsed once per payload; None when none has arrived since the last connect, or
        the latest doesn't parse (the caller reads fresh, which raises TimeoutError on it as on no message)."""
        if (payload := self._payloads.get(suffix)) is None:
            return None
        memo = self._parsed.get(suffix)
        if memo is None or memo[0] is not payload:
            try:
                parsed = json.loads(payload)
            except ValueError:
                _LOGGER.debug("Zigbee2MQTT's bridge/%s doesn't parse: %.80s", suffix, payload)
                parsed = None
            memo = self._parsed[suffix] = (payload, parsed)
        return memo[1]

    def device(self, ieee: str) -> dict | None:
        """A device's entry in the held bridge/devices, by IEEE address, indexed once per payload: the classifier reads
        its Zigbee strings (classes.py). None while no bridge/devices is held, or when it doesn't list the device.
        Shared with every caller: read it, never change it."""
        if not isinstance(devices := self.parsed("devices"), list):
            return None
        if self._devices is None or self._devices[0] is not devices:
            index = {
                d["ieee_address"]: d for d in devices if isinstance(d, dict) and isinstance(d.get("ieee_address"), str)
            }
            self._devices = (devices, index)
        return self._devices[1].get(ieee)

    def settings(self, section: str, z2m_id: str) -> dict | None:
        """An entity's options: confirmed since the last bridge/info, else bridge/info's own; None when no bridge/info
        is held. Shared with every caller: read it, never change it."""
        if (info := self.parsed("info")) is None:
            return None
        if (options := self.confirmed.get((section, z2m_id))) is not None:
            return options
        return info.get("config", {}).get(section, {}).get(z2m_id, {})

    def _keeper(self, suffix: str) -> Callable[[mqtt.ReceiveMessage], None]:
        @callback
        def received(msg: mqtt.ReceiveMessage) -> None:
            # An empty payload clears a retained topic.
            if msg.payload:
                self._payloads[suffix] = msg.payload
            else:
                self._payloads.pop(suffix, None)
            if suffix == "info":
                self.confirmed.clear()

        return received

    def listen_log(self, listener: Callable[[str], None]) -> Callable[[], None]:
        """Call listener with the message of each error line Z2M logs, until the returned callable is called."""
        self._log_listeners.append(listener)
        return lambda: self._log_listeners.remove(listener)

    @callback
    def _on_log(self, msg: mqtt.ReceiveMessage) -> None:
        if self._log_listeners and (message := log_error(msg.payload)) is not None:
            for listener in list(self._log_listeners):
                listener(message)

    @callback
    def _on_barrier(self, msg: mqtt.ReceiveMessage) -> None:
        self.replies_live = True
        if _says_online(msg.payload):
            self._on_online()

    @callback
    def _on_connection(self, connected: bool) -> None:
        if not connected:
            self._clear()

    @callback
    def stop(self) -> None:
        for unsubscribe in self._unsubscribes:
            unsubscribe()
        self._unsubscribes.clear()
        self._clear()

    def _clear(self) -> None:
        self.replies_live = False
        self._payloads.clear()
        self._parsed.clear()
        self.confirmed.clear()

    async def subscribe(self, hass: HomeAssistant) -> None:
        # In this order, so the barrier follows the response topics and the log in HA's batch.
        for kind in KINDS:
            self._unsubscribes.append(
                await mqtt.async_subscribe(hass, bridge_topic(f"response/{kind}/options"), reply_router(self.pending))
            )
        self._unsubscribes.append(await mqtt.async_subscribe(hass, bridge_topic(LOG), self._on_log))
        for suffix in HELD:
            self._unsubscribes.append(await mqtt.async_subscribe(hass, bridge_topic(suffix), self._keeper(suffix)))
        self._unsubscribes.append(await mqtt.async_subscribe(hass, bridge_topic(_BARRIER), self._on_barrier))
        self._unsubscribes.append(mqtt.async_subscribe_connection_status(hass, self._on_connection))


def _says_online(payload: str) -> bool:
    """Whether a bridge/state payload says online: JSON `{"state": "online"}`, or the bare string of older Z2M."""
    if payload == "online":
        return True
    try:
        state = json.loads(payload)
    except ValueError:
        return False
    return isinstance(state, dict) and state.get("state") == "online"


def log_error(payload: str) -> str | None:
    """The message of a bridge/logging line at error level; None for any other level, or a line that doesn't parse."""
    try:
        entry = json.loads(payload)
        if entry.get("level") == "error" and isinstance(message := entry["message"], str):
            return message
    except ValueError, TypeError, KeyError, AttributeError:
        pass
    return None


def reply_router(pending: dict[str, asyncio.Future[dict]]) -> Callable[[mqtt.ReceiveMessage], None]:
    """A response-topic callback resolving the pending future of the reply's transaction; others are ignored."""

    @callback
    def received(msg: mqtt.ReceiveMessage) -> None:
        try:
            answer = json.loads(msg.payload)
        except ValueError:
            return
        if isinstance(answer, dict) and (reply := pending.get(answer.get("transaction"))) and not reply.done():
            reply.set_result(answer)

    return received


def get(hass: HomeAssistant) -> BridgeCache | None:
    """The running cache, or None before MQTT is up, after unload, or outside an entry (a test, a removal)."""
    if (runtime := hass.data.get(DATA)) is None:
        return None
    return runtime.integration_state.get(_KEY)


async def async_setup(hass: HomeAssistant, entry: ConfigEntry, on_online: Callable[[], None] = lambda: None) -> None:
    """Hold the bridge topics once MQTT is set up, until the entry unloads. Runs as the entry's background task.

    Calls on_online each time bridge/state says online, its retained message included, so at startup and whenever
    Z2M comes back.
    """
    if not await mqtt.async_wait_for_mqtt_client(hass):
        return
    cache = BridgeCache(on_online)
    state = hass.data[DATA].integration_state

    @callback
    def unload() -> None:
        cache.stop()
        if state.get(_KEY) is cache:
            del state[_KEY]

    # Before subscribing, so an unload mid-way still drops what was subscribed.
    entry.async_on_unload(unload)
    try:
        await cache.subscribe(hass)
    except Exception as err:  # noqa: BLE001 - each caller reads fresh, as without the cache
        # Without the cache, a group's members can't be listed sync (members.expand_group), so every group counts
        # as Experimental, and turn_on_for refuses it and every device but a Hue one for good (classes.unplaced).
        _LOGGER.warning(
            "Couldn't subscribe to Zigbee2MQTT's bridge, so each action reads it fresh, Zigbee2MQTT groups run "
            "only with Experimental features on, and turn on with timer refuses them and every device but Hue's: %r",
            err,
        )
        cache.stop()
        return
    state[_KEY] = cache
