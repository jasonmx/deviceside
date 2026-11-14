"""Fabricate Zigbee2MQTT entities the way Z2M registers them via MQTT discovery, and a broker with Z2M behind it."""

import asyncio
import json
from types import SimpleNamespace

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

IEEE = "0x00124b0012345678"
INFO = "zigbee2mqtt/bridge/info"
GROUPS = "zigbee2mqtt/bridge/groups"


def z2m_entry(hass: HomeAssistant) -> MockConfigEntry:
    """One loaded MQTT config entry for Z2M (all Z2M devices share a single entry)."""
    # Untitled, as HA names a device without one after its entry; Z2M names every device after its friendly name.
    entry = MockConfigEntry(domain="mqtt", title="")
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.LOADED)
    return entry


def add_z2m_entity(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    entity_id: str,
    ieee: str = IEEE,
    state: str = "off",
    attributes: dict | None = None,
    manufacturer: str | None = "Philips",
    name: str | None = None,
    model_id: str | None = None,
) -> str:
    """Register one Z2M device entity with the identifier Z2M writes via MQTT discovery.

    Each device has its own device-registry entry (keyed by ieee); all share one config entry. It is a Hue device
    unless the manufacturer says otherwise, as every record's is, so its class is Verified (integrations/zigbee2mqtt/
    classes.py).
    """
    domain, object_id = entity_id.split(".")
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("mqtt", f"zigbee2mqtt_{ieee}")},
        manufacturer=manufacturer,
        model_id=model_id,
        name=name,
    )
    er.async_get(hass).async_get_or_create(
        domain,
        "mqtt",
        ieee,
        suggested_object_id=object_id,
        config_entry=entry,
        device_id=device.id,
    )
    hass.states.async_set(entity_id, state, attributes or {})
    return entity_id


def add_z2m_group(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    entity_id: str,
    group_id: int,
    base_topic: str = "zigbee2mqtt",
    state: str = "off",
    name: str | None = None,
) -> str:
    """Register one Z2M group entity with the identifier Z2M writes via MQTT discovery.

    Z2M encodes the base topic as character codes joined with no separator, then appends the
    group ID (getDevicePayload and getEncodedBaseTopic in lib/extension/homeassistant.ts).
    """
    encoded = "".join(str(ord(c)) for c in base_topic)
    return add_z2m_entity(
        hass, entry, entity_id, ieee=f"{encoded}_{group_id}", state=state, manufacturer="Zigbee2MQTT", name=name
    )


class Broker:
    """MQTT as the adapters see it, and Z2M answering options requests and reads.

    Like HA's client, a subscription goes live only when its debounced batch reaches the broker (here, on the next
    loop turn), and a retained message is delivered then. A reply published before that is lost.
    """

    def __init__(self) -> None:
        self.retained: dict[str, object] = {}
        self.pending: list[tuple[str, object]] = []
        self.subscribers: dict[str, list] = {}
        self.topics: list[str] = []  # every topic ever subscribed
        self.published: list[dict] = []
        self.reply: dict | str | None = {"status": "ok"}  # merged with the transaction; a str is sent as is
        self.refused: set[str] = set()  # IDs whose options requests Z2M answers with an error
        # Each device's attributes by cluster, keyed by IEEE, with the friendly name its state is published under.
        self.devices: dict[str, tuple[str, dict[str, dict]]] = {}
        self.read_errors: dict[tuple[str, str], str] = {}  # (IEEE, cluster) -> the status Z2M logs for a failed read
        # (IEEE, cluster) -> statuses Z2M logs for that many failed reads in a row, then it answers
        self.read_errors_then_reply: dict[tuple[str, str], list[str]] = {}
        self.before_reply: list[dict] = []  # state payloads published ahead of each read's reply
        self.logs_before_reply: list[str] = []  # bridge/logging payloads published ahead of each read's reply
        self.states: dict[str, dict] = {}  # the state Z2M keeps for each device, keyed by IEEE, in every publish
        self.ignores_override: set[str] = set()  # IEEEs whose bulbs drop a MoveTo command, as without the override
        self.fade_s = 0.0  # seconds a lit bulb takes to reach a stored level, answering the old one until then
        self.fading: dict[str, tuple[int, float]] = {}  # IEEE -> (level before the write, when the fade ends)
        self.fade_reads = 0  # reads, not seconds: a lit bulb answers the old level to this many reads after a write
        self.fading_reads: dict[str, list[int]] = {}  # IEEE -> [level before the write, reads left to answer it]
        # Z2M's answer to a set or light.turn_on publish, called with its topic and payload (state or a log line)
        self.on_set = None

    async def subscribe(self, hass, topic, msg_callback):
        self.topics.append(topic)
        if topic in {f"zigbee2mqtt/{name}" for name, _ in self.devices.values()}:
            # HA's light entity already subscribes to its state topic, so another subscription is live at once.
            self.subscribers.setdefault(topic, []).append(msg_callback)
            if topic in self.retained:
                message = SimpleNamespace(topic=topic, payload=json.dumps(self.retained[topic]), retain=True)
                asyncio.get_running_loop().call_soon(msg_callback, message)
            return lambda: self.subscribers[topic].remove(msg_callback)
        if not self.pending:
            asyncio.get_running_loop().call_soon(self.flush)
        self.pending.append((topic, msg_callback))

        def unsubscribe() -> None:
            if (topic, msg_callback) in self.pending:
                self.pending.remove((topic, msg_callback))
            else:
                self.subscribers[topic].remove(msg_callback)

        return unsubscribe

    def flush(self) -> None:
        batch, self.pending = self.pending, []
        for topic, msg_callback in batch:
            self.subscribers.setdefault(topic, []).append(msg_callback)
        for topic, msg_callback in batch:
            if topic in self.retained:
                msg_callback(SimpleNamespace(topic=topic, payload=json.dumps(self.retained[topic]), retain=True))

    def deliver(self, topic: str, payload: str) -> None:
        for msg_callback in list(self.subscribers.get(topic, [])):
            msg_callback(SimpleNamespace(topic=topic, payload=payload, retain=False))

    def listening(self, topic: str) -> bool:
        return bool(self.subscribers.get(topic)) or any(t == topic for t, _ in self.pending)

    @callback
    def publish(self, service: ServiceCall) -> None:
        topic, payload = service.data["topic"], json.loads(service.data["payload"])
        self.published.append({"topic": topic, "payload": payload})
        if "read" in payload:
            self._read(topic.split("/")[1], payload["read"])
        elif "command" in payload:
            self._command(topic.split("/")[1], payload["command"])
            if self.on_set is not None:
                self.on_set(topic, payload)
        elif self.on_set is not None and topic.endswith("/set"):
            self.on_set(topic, payload)
        elif topic.startswith("zigbee2mqtt/bridge/request/") and self.reply is not None:
            response = topic.replace("/request/", "/response/")
            self.deliver(response, json.dumps({"transaction": "someone else's", "status": "error"}))
            if isinstance(self.reply, str):
                self.deliver(response, self.reply)
            else:
                reply = {"status": "error", "error": "refused"} if payload.get("id") in self.refused else self.reply
                self.deliver(response, json.dumps({**reply, "transaction": payload["transaction"]}))

    def _read(self, ieee: str, read: dict) -> None:
        """Z2M's answer to a generic read: the reply in the device's state, or a line on its log."""
        if ieee not in self.devices:
            return
        name, clusters = self.devices[ieee]
        cluster = read["cluster"]
        failures = self.read_errors_then_reply.get((ieee, cluster))
        status = failures.pop(0) if failures else self.read_errors.get((ieee, cluster))
        # A bulb set up without the attribute answers as one lacking it would: Z2M logs the read as failed.
        if not status and not set(read["attributes"]) <= set(clusters.get(cluster, {})):
            status = "UNSUPPORTED_ATTRIBUTE"
        if status:
            message = f"Publish 'set' 'read' to '{name}' failed: ZCL command {ieee}/11 {cluster}.read() failed"
            message += f" ({status})"
            self.deliver("zigbee2mqtt/bridge/logging", json.dumps({"level": "error", "message": message}))
            return
        for line in self.logs_before_reply:
            self.deliver("zigbee2mqtt/bridge/logging", line)
        for payload in self.before_reply:
            self.deliver(f"zigbee2mqtt/{name}", json.dumps(payload))
        reply = {attribute: clusters[cluster][attribute] for attribute in read["attributes"]}
        if (
            (fade := self.fading.get(ieee))
            and cluster == "genLevelCtrl"
            and asyncio.get_running_loop().time() < fade[1]
        ):
            reply["currentLevel"] = fade[0]
        elif (counted := self.fading_reads.get(ieee)) and cluster == "genLevelCtrl" and counted[1] > 0:
            counted[1] -= 1
            reply["currentLevel"] = counted[0]
        state = self.states.setdefault(ieee, {"state": "ON"})
        if "currentLevel" in reply:
            state["brightness"] = reply["currentLevel"]
        self.deliver(f"zigbee2mqtt/{name}", json.dumps({**state, read["state_property"]: reply}))

    def _command(self, address: str, command: dict) -> None:
        """A bulb storing a raw MoveTo command, sent to it or to a group it is in (from the retained bridge/groups)."""
        if address in self.devices:
            ieees = [address]
        else:
            groups = {str(group["id"]): group["members"] for group in self.retained.get(GROUPS, [])}
            ieees = [member["ieee_address"] for member in groups.get(address, [])]
        params = command["payload"]
        stored = {
            "moveToLevel": ("genLevelCtrl", {"currentLevel": params.get("level")}),
            "moveToColorTemp": ("lightingColorCtrl", {"colorMode": 2, "colorTemperature": params.get("colortemp")}),
            "moveToColor": (
                "lightingColorCtrl",
                {"colorMode": 1, "currentX": params.get("colorx"), "currentY": params.get("colory")},
            ),
            "moveToHueAndSaturation": (
                "lightingColorCtrl",
                {"colorMode": 0, "currentHue": params.get("hue"), "currentSaturation": params.get("saturation")},
            ),
        }.get(command["command"])
        if stored is None:
            return
        cluster, values = stored
        for ieee in ieees:
            if ieee in self.devices and ieee not in self.ignores_override:
                clusters = self.devices[ieee][1]
                if cluster == "genLevelCtrl" and clusters.get("genOnOff", {}).get("onOff") and self.fade_s:
                    before = clusters[cluster]["currentLevel"]
                    self.fading[ieee] = (before, asyncio.get_running_loop().time() + self.fade_s)
                if cluster == "genLevelCtrl" and clusters.get("genOnOff", {}).get("onOff") and self.fade_reads:
                    self.fading_reads[ieee] = [clusters[cluster]["currentLevel"], self.fade_reads]
                self.devices[ieee][1].setdefault(cluster, {}).update(values)

    def reads(self) -> list[tuple[str, dict]]:
        """Each read sent, as (topic, read payload)."""
        return [(p["topic"], p["payload"]["read"]) for p in self.published if "read" in p["payload"]]

    def requests(self) -> list[dict]:
        return [p for p in self.published if p["topic"].startswith("zigbee2mqtt/bridge/request/")]

    def timers(self) -> list[dict]:
        return [p for p in self.published if p["payload"].get("on_time") is not None]
