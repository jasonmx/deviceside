from types import SimpleNamespace

import pytest

from custom_components.deviceside.core.resolver import resolve
from custom_components.deviceside.core.runtime import DATA, get_runtime
from custom_components.deviceside.integrations.zigbee2mqtt import (
    INTEGRATION,
    bridge,
    bridge_cache,
    classes,
    members,
    native_control,
    read,
    set_attributes,
)
from custom_components.deviceside.integrations.zigbee2mqtt.fade import transition_cache

from ...conftest import Clock, unload_mock_entries
from ...conftest import call as call_service
from ...fixtures.zigbee2mqtt import GROUPS, IEEE, INFO, Broker, add_z2m_entity, add_z2m_group, z2m_entry

unload_mock_mqtt_entries = unload_mock_entries("mqtt")


@pytest.fixture(autouse=True)
def publish_via_service(monkeypatch):
    """Route mqtt.async_publish to an mqtt.publish service, which the tests register as the broker (or a failure).

    The real mqtt integration isn't set up in unit tests, so its async_publish would refuse every publish.
    """

    async def async_publish(hass, topic: str, payload: str, *args, **kwargs) -> None:
        await hass.services.async_call("mqtt", "publish", {"topic": topic, "payload": payload}, blocking=True)

    monkeypatch.setattr(bridge.mqtt, "async_publish", async_publish)


@pytest.fixture
def broker(hass, monkeypatch):
    fake = Broker()
    fake.retained[INFO] = {"config": {"devices": {IEEE: {"friendly_name": "bulb"}}, "groups": {}}}
    fake.retained["zigbee2mqtt/bridge/state"] = {"state": "online"}
    monkeypatch.setattr(bridge.mqtt, "async_subscribe", fake.subscribe)
    monkeypatch.setattr(bridge, "_RETAINED_TIMEOUT_S", 0.1)
    monkeypatch.setattr(bridge, "_REPLY_TIMEOUT_S", 0.1)
    monkeypatch.setattr(read, "_REPLY_TIMEOUT_S", 0.1)
    monkeypatch.setattr(native_control, "_REPLY_CAP_S", 0.05)
    monkeypatch.setattr(set_attributes, "_FADE_WAIT_S", 0.0)
    monkeypatch.setattr(set_attributes, "_RETRY_S", 0.0)
    hass.services.async_register("mqtt", "publish", fake.publish)

    # The dispatcher lists a group's members from bridge/groups as the running cache would hold it, once the entry is
    # set up; a test's own cache stays, once it holds bridge/groups.
    def held(hass):
        if (cache := bridge_cache.get(hass)) is not None and cache.parsed("groups") is not None:
            return cache
        return HeldGroups(fake) if DATA in hass.data else None

    monkeypatch.setattr(members, "bridge_cache", SimpleNamespace(get=held))
    monkeypatch.setattr(classes, "bridge_cache", SimpleNamespace(get=held))
    return fake


async def call(hass, service: str, data: dict, return_response: bool = True):
    """The response, each entry's class checked and left out, so a test states only what its action decides: every
    Zigbee2MQTT result names the class its device is, or a group's members' (docs/action-behavior.md)."""
    response = await call_service(hass, service, data, return_response)
    for entity_id, entry in (response or {}).items():
        target = resolve(hass, get_runtime(hass).registry, entity_id)
        if target.is_group:
            listed = INTEGRATION.expand_group(hass, target) or []
            classes = sorted({INTEGRATION.capability(hass, member) for member in listed}) or None
            assert entry.pop("capabilities", None) == classes, entity_id
        else:
            assert entry.pop("capability", None) == INTEGRATION.capability(hass, target), entity_id
    return response


class HeldGroups(bridge_cache.BridgeCache):
    """A bridge cache holding bridge/groups and bridge/devices, as the broker has them now, for members.expand_group
    and the classifier."""

    def __init__(self, broker) -> None:
        super().__init__()
        self.broker = broker

    def parsed(self, suffix: str):
        return self.broker.retained.get({"groups": GROUPS, "devices": DEVICES}.get(suffix))


DEVICES = "zigbee2mqtt/bridge/devices"
XY = ["color_temp", "xy"]
# A light's color cluster mid-hue-cycle: in hs mode (0), at x 0.3 and y 0.4.
COLOR = {"colorMode": 0, "currentX": 19661, "currentY": 26214}


@pytest.fixture
def clock(hass, setup_production) -> Clock:
    clock = Clock()
    transition_cache(hass).clock = clock
    return clock


class REGISTRY:
    """light()'s default Zigbee strings: the registry's vendor, and ZIGBEE_MODEL."""


# A Zigbee model no rule matches.
ZIGBEE_MODEL = "bulb"


def light(
    hass,
    broker,
    entity_id: str = "light.lamp",
    ieee: str = IEEE,
    *,
    state: str = "on",
    brightness: int | None = 153,  # 60 %
    hs: tuple[float, float] | None = (200.0, 60.0),
    modes: list[str] | None = None,
    color_hs: bool = True,
    color: dict | None = None,
    manufacturer: str | None = "Philips",
    model_id: str | None = None,
    zigbee: tuple[str | None, str | None] | None | type[REGISTRY] = REGISTRY,
    interviewed: bool | None = None,
) -> str:
    """A Z2M color light HA lists with xy, whose definition exposes color_hs, answering color reads with COLOR.

    Its bridge/devices entry holds the Zigbee manufacturer and model `zigbee` names. By default, REGISTRY, the
    registry's manufacturer and ZIGBEE_MODEL: an interviewed device of that vendor that shows no Tuya string. None
    writes an entry with neither, as Zigbee2MQTT lists a device mid-interview, which the classifier can't place by
    them. `interview_completed` is `interviewed`, by default whether `zigbee` names any string.
    """
    name = entity_id.split(".")[1]
    entries = hass.config_entries.async_entries("mqtt")
    entry = entries[0] if entries else z2m_entry(hass)
    attributes: dict = {"supported_color_modes": XY if modes is None else modes}
    if state == "on":
        attributes |= {"brightness": brightness, "hs_color": hs}
    add_z2m_entity(
        hass,
        entry,
        entity_id,
        ieee=ieee,
        state=state,
        attributes=attributes,
        name=name,
        manufacturer=manufacturer,
        model_id=model_id,
    )
    broker.devices[ieee] = (name, {"lightingColorCtrl": {**COLOR, **(color or {})}})
    broker.retained[INFO]["config"]["devices"].setdefault(ieee, {})
    features = [{"name": "state"}, {"name": "brightness"}] + ([{"name": "color_hs"}] if color_hs else [])
    device = {"ieee_address": ieee, "definition": {"exposes": [{"type": "light", "features": features}]}}
    if zigbee is REGISTRY:
        zigbee = (manufacturer, ZIGBEE_MODEL)
    # Its own Zigbee manufacturer and model, which the classifier reads; Z2M holds null for one it hasn't read.
    device |= dict(zip(("manufacturer", "model_id"), zigbee or (None, None), strict=True))
    device["interview_completed"] = zigbee is not None if interviewed is None else interviewed
    broker.retained.setdefault(DEVICES, []).append(device)
    return entity_id


def group(hass, broker, **members: dict) -> str:
    """Z2M group 7, light.room, of light.one (0x01) and light.two (0x02), each set up by light() with its options."""
    for ieee, (name, options) in zip(("0x01", "0x02"), members.items(), strict=True):
        light(hass, broker, f"light.{name}", ieee, **options)
    add_z2m_group(hass, hass.config_entries.async_entries("mqtt")[0], "light.room", group_id=7, name="room")
    hass.states.async_set("light.room", "on", {"supported_color_modes": XY, "hs_color": (200.0, 60.0)})
    broker.retained[GROUPS] = [{"id": 7, "members": [{"ieee_address": "0x01"}, {"ieee_address": "0x02"}]}]
    return "light.room"


def commands(broker) -> list[tuple[str, dict]]:
    """Every command and get published to a light or group, leaving out bridge requests and reads."""
    return [
        (p["topic"], p["payload"])
        for p in broker.published
        if not p["topic"].startswith("zigbee2mqtt/bridge/") and "read" not in p["payload"]
    ]


SENT = {"status": "sent", "integration": "zigbee2mqtt"}
SET = f"zigbee2mqtt/{IEEE}/set"
GET = f"zigbee2mqtt/{IEEE}/get"
# An off light is put here first, so a fade from off starts at the bottom, not the level it kept.
BOTTOM = {"state": "ON", "brightness": 1, "transition": 0}
