"""Zigbee2MQTT set_attributes: raw MoveTo commands with the override, then a live read of what was written."""

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util.color import color_RGB_to_xy, color_xy_to_hs

from custom_components.deviceside.integrations.zigbee2mqtt import set_attributes

from .conftest import call
from ...conftest import refusal, result
from ...fixtures.zigbee2mqtt import GROUPS, IEEE, INFO, add_z2m_entity, add_z2m_group, z2m_entry

pytestmark = pytest.mark.usefixtures("setup_production")

KEY = "deviceside_read"
SET = f"zigbee2mqtt/{IEEE}/set"
CONFIRMED = {"status": "confirmed", "integration": "zigbee2mqtt"}
OVERRIDE = {"transtime": 4, "optionsMask": 1, "optionsOverride": 1}
ONOFF = ["onoff"]
DIMMABLE = ["brightness"]
CT = ["color_temp"]
COLOR = ["color_temp", "xy"]
RANGE = {"min_color_temp_kelvin": 2202, "max_color_temp_kelvin": 6535}
# A bulb at 127 of 254 (50 %), at 250 mireds.
BULB = {
    "genOnOff": {"onOff": 1},
    "genLevelCtrl": {"currentLevel": 127},
    "lightingColorCtrl": {"colorMode": 2, "colorTemperature": 250, "currentX": 0, "currentY": 0},
}


def bulb(
    hass, broker, entity_id: str = "light.lamp", ieee: str = IEEE, modes: list | None = None, on: bool = True
) -> str:
    """A Z2M light that answers reads with BULB and stores the MoveTo commands it is sent."""
    name = entity_id.split(".")[1]
    entries = hass.config_entries.async_entries("mqtt")
    entry = entries[0] if entries else z2m_entry(hass)
    attributes = {"supported_color_modes": COLOR if modes is None else modes, **RANGE}
    state = "on" if on else "off"
    add_z2m_entity(hass, entry, entity_id, ieee=ieee, state=state, attributes=attributes, name=name)
    clusters = {cluster: dict(values) for cluster, values in BULB.items()}
    clusters["genOnOff"]["onOff"] = int(on)
    broker.devices[ieee] = (name, clusters)
    broker.retained[INFO]["config"]["devices"].setdefault(ieee, {"filtered_cache": [KEY]})
    return entity_id


def raw(cluster: str, command: str, **params) -> dict:
    return {"command": {"cluster": cluster, "command": command, "payload": {**params, **OVERRIDE}}}


def level(value: int) -> dict:
    return raw("genLevelCtrl", "moveToLevel", level=value)


def mireds(value: int) -> dict:
    return raw("lightingColorCtrl", "moveToColorTemp", colortemp=value)


def xy(x: float, y: float) -> dict:
    return raw("lightingColorCtrl", "moveToColor", colorx=round(x * 65535), colory=round(y * 65535))


def sent(broker) -> list[tuple[str, dict]]:
    return [(p["topic"], p["payload"]) for p in broker.published if "command" in p["payload"]]


def kept(broker) -> list[tuple[str, dict]]:
    """Each brightness sent through Z2M's own converter, to replace the level it keeps."""
    return [(p["topic"], p["payload"]) for p in broker.published if "brightness" in p["payload"]]


def keep(value: int) -> dict:
    return {"state": None, "brightness": value, "transition": 0.4}


def reads(broker) -> list[tuple[str, str, list]]:
    return [(topic, read["cluster"], read["attributes"]) for topic, read in broker.reads()]


@pytest.mark.parametrize(
    ("data", "modes", "expected", "read_back"),
    [
        pytest.param({"brightness": 40}, DIMMABLE, [level(102)], [("genLevelCtrl", ["currentLevel"])], id="brightness"),
        pytest.param(
            {"brightness": 0}, DIMMABLE, [level(1)], [("genLevelCtrl", ["currentLevel"])], id="brightness_0_is_level_1"
        ),
        pytest.param(
            {"color_temp_kelvin": 1000},
            CT,
            [mireds(454)],
            [("lightingColorCtrl", ["colorMode", "colorTemperature"])],
            id="ct_clamped",
        ),
        pytest.param(
            {"brightness": 40, "color_temp_kelvin": 2700},
            CT,
            [level(102), mireds(370)],
            [("genLevelCtrl", ["currentLevel"]), ("lightingColorCtrl", ["colorMode", "colorTemperature"])],
            id="brightness_and_ct",
        ),
        pytest.param(
            {"rgb_color": [255, 0, 0]},
            COLOR,
            [xy(*color_RGB_to_xy(255, 0, 0))],
            [("lightingColorCtrl", ["colorMode", "currentX", "currentY"])],
            id="rgb",
        ),
        pytest.param(
            {"xy_color": [0.369, 0.445], "color_temp_kelvin": 2700},
            COLOR,
            [xy(0.369, 0.445)],
            [("lightingColorCtrl", ["colorMode", "currentX", "currentY"])],
            id="color_wins_over_ct",
        ),
    ],
)
async def test_writes_with_the_override_and_reads_it_back(hass, broker, data, modes, expected, read_back):
    bulb(hass, broker, modes=modes, on=False)

    response = await call(hass, "set_attributes", {"entity_id": "light.lamp", **data})

    assert response == {"light.lamp": CONFIRMED}
    assert sent(broker) == [(SET, payload) for payload in expected]
    assert reads(broker) == [(SET, cluster, names) for cluster, names in read_back]


async def test_a_level_also_replaces_the_level_z2m_keeps(hass, broker):
    bulb(hass, broker, modes=CT, on=False)

    await call(hass, "set_attributes", {"entity_id": "light.lamp", "brightness": 40, "color_temp_kelvin": 2700})

    published = [p["payload"] for p in broker.published if p["topic"] == SET and "read" not in p["payload"]]
    assert published == [level(102), mireds(370), keep(102)]


async def test_no_level_keeps_nothing(hass, broker):
    bulb(hass, broker, modes=CT, on=False)

    await call(hass, "set_attributes", {"entity_id": "light.lamp", "color_temp_kelvin": 2700})

    assert kept(broker) == []


async def test_the_read_back_puts_the_stored_values_into_z2ms_state(hass, broker):
    bulb(hass, broker, modes=DIMMABLE, on=False)
    broker.states[IEEE] = {"state": "OFF", "brightness": 200}

    await call(hass, "set_attributes", {"entity_id": "light.lamp", "brightness": 40})

    assert broker.states[IEEE]["brightness"] == 102


async def test_color_on_an_hs_only_light_sends_hue_and_saturation(hass, broker):
    bulb(hass, broker, modes=["hs"])

    response = await call(hass, "set_attributes", {"entity_id": "light.lamp", "xy_color": [0.369, 0.445]})

    hue, saturation = color_xy_to_hs(0.369, 0.445)
    payload = raw(
        "lightingColorCtrl",
        "moveToHueAndSaturation",
        hue=round(hue * 254 / 360),
        saturation=round(saturation * 254 / 100),
    )
    assert response == {"light.lamp": CONFIRMED}
    assert sent(broker) == [(SET, payload)]
    assert reads(broker) == [(SET, "lightingColorCtrl", ["colorMode", "currentHue", "currentSaturation"])]


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        pytest.param({"min_brightness": 60}, level(152), id="min_raises"),
        pytest.param({"max_brightness": 30}, level(76), id="max_lowers"),
    ],
)
async def test_limits_clamp_the_level_read_live_while_on(hass, broker, data, expected):
    bulb(hass, broker, modes=DIMMABLE)

    response = await call(hass, "set_attributes", {"entity_id": "light.lamp", **data})

    assert response == {"light.lamp": CONFIRMED}
    assert sent(broker) == [(SET, expected)]
    assert reads(broker)[:2] == [(SET, "genOnOff", ["onOff"]), (SET, "genLevelCtrl", ["currentLevel"])]


async def test_limits_clamp_z2ms_brightness_while_off(hass, broker):
    bulb(hass, broker, modes=DIMMABLE, on=False)
    broker.states[IEEE] = {"state": "OFF", "brightness": 254}

    await call(hass, "set_attributes", {"entity_id": "light.lamp", "max_brightness": 30})

    assert sent(broker) == [(SET, level(76))]
    assert reads(broker) == [(SET, "genOnOff", ["onOff"]), (SET, "genLevelCtrl", ["currentLevel"])]


async def test_limits_on_an_off_light_z2m_has_no_brightness_for_fail_it(hass, broker):
    bulb(hass, broker, modes=DIMMABLE, on=False)
    broker.states[IEEE] = {"state": "OFF"}

    with pytest.raises(HomeAssistantError) as raised:
        await call(hass, "set_attributes", {"entity_id": "light.lamp", "min_brightness": 60})

    assert raised.value.translation_key == "targets_failed"
    assert sent(broker) == []


async def test_within_limits_sends_nothing(hass, broker):
    bulb(hass, broker, modes=DIMMABLE)

    response = await call(
        hass, "set_attributes", {"entity_id": "light.lamp", "min_brightness": 20, "max_brightness": 80}
    )

    assert response == {"light.lamp": result("zigbee2mqtt", "unchanged", detail="within_limits")}
    assert sent(broker) == []


async def test_a_field_the_light_cant_take_is_dropped(hass, broker):
    bulb(hass, broker, modes=CT)

    response = await call(hass, "set_attributes", {"entity_id": "light.lamp", "brightness": 40, "hs_color": [30, 80]})

    assert response == {"light.lamp": {**CONFIRMED, "detail": "fields_dropped"}}
    assert sent(broker) == [(SET, level(102))]


@pytest.mark.parametrize(
    ("modes", "data"),
    [
        pytest.param(ONOFF, {"brightness": 40}, id="onoff_light"),
        pytest.param(DIMMABLE, {"rgb_color": [255, 0, 0], "color_temp_kelvin": 2700}, id="every_field_dropped"),
    ],
)
async def test_a_light_that_takes_no_field_is_unchanged(hass, broker, modes, data):
    bulb(hass, broker, modes=modes)

    response = await call(hass, "set_attributes", {"entity_id": "light.lamp", **data})

    assert response == {"light.lamp": result("zigbee2mqtt", "unchanged", detail="fields_dropped")}
    assert sent(broker) == []
    assert reads(broker) == []


async def test_a_bulb_that_ignores_the_override_is_rejected(hass, broker):
    bulb(hass, broker, modes=CT, on=False)
    broker.ignores_override.add(IEEE)

    with pytest.raises(HomeAssistantError) as raised:
        await call(hass, "set_attributes", {"entity_id": "light.lamp", "color_temp_kelvin": 2700})

    assert raised.value.translation_key == "targets_failed"
    assert raised.value.translation_placeholders["summary"] == "light.lamp: not_stored"


async def test_the_read_waits_out_the_write_fade(hass, broker, monkeypatch):
    # A lit bulb answers the old level until the fade ends; read inside it, the write looks ignored.
    bulb(hass, broker, modes=DIMMABLE)
    broker.fade_s = 0.05
    monkeypatch.setattr(set_attributes, "_RETRIES", 0)

    with pytest.raises(HomeAssistantError) as raised:
        await call(hass, "set_attributes", {"entity_id": "light.lamp", "brightness": 40})
    assert raised.value.translation_placeholders["summary"] == "light.lamp: not_stored"

    monkeypatch.setattr(set_attributes, "_FADE_WAIT_S", 0.1)
    broker.fading.clear()
    broker.devices[IEEE][1]["genLevelCtrl"]["currentLevel"] = 127

    response = await call(hass, "set_attributes", {"entity_id": "light.lamp", "brightness": 40})

    assert response == {"light.lamp": CONFIRMED}


async def test_a_read_still_mid_fade_is_repeated_before_the_write_counts_as_ignored(hass, broker, monkeypatch):
    bulb(hass, broker, modes=DIMMABLE)
    broker.fade_reads = 1  # counted in reads, so no wait has to outlast a real-clock fade
    monkeypatch.setattr(set_attributes, "_RETRY_S", 0)

    response = await call(hass, "set_attributes", {"entity_id": "light.lamp", "brightness": 40})

    assert response == {"light.lamp": CONFIRMED}
    assert len([r for r in reads(broker) if r[1] == "genLevelCtrl"]) == 2


async def test_color_is_not_compared(hass, broker):
    # The bulb clamps a color to its gamut, so what reads back differs from what was sent.
    bulb(hass, broker, on=False)
    broker.ignores_override.add(IEEE)

    response = await call(hass, "set_attributes", {"entity_id": "light.lamp", "rgb_color": [255, 0, 0]})

    assert response == {"light.lamp": CONFIRMED}


async def test_a_read_back_that_times_out_fails_the_light(hass, broker):
    bulb(hass, broker, modes=DIMMABLE)
    broker.read_errors[(IEEE, "genLevelCtrl")] = "Timeout"

    with pytest.raises(HomeAssistantError) as raised:
        await call(hass, "set_attributes", {"entity_id": "light.lamp", "brightness": 40})

    assert raised.value.translation_key == "targets_failed"
    assert sent(broker) == [(SET, level(102))]


GROUP_SET = "zigbee2mqtt/7/set"


def group(hass, broker, members: list | None = None) -> str:
    """Group 7, light.room, of light.one (0x01, on at 127) and light.two (0x02, off, Z2M brightness 254)."""
    bulb(hass, broker, "light.one", ieee="0x01", modes=CT)
    bulb(hass, broker, "light.two", ieee="0x02", modes=DIMMABLE, on=False)
    broker.states["0x02"] = {"state": "OFF", "brightness": 254}
    add_z2m_group(hass, hass.config_entries.async_entries("mqtt")[0], "light.room", group_id=7, name="room")
    hass.states.async_set("light.room", "on", {"supported_color_modes": CT, **RANGE})
    ieees = ["0x01", "0x02"] if members is None else members
    broker.retained[GROUPS] = [{"id": 7, "members": [{"ieee_address": ieee} for ieee in ieees]}]
    return "light.room"


async def test_a_group_is_one_groupcast_read_back_from_every_member(hass, broker):
    group(hass, broker)

    response = await call(
        hass, "set_attributes", {"entity_id": "light.room", "brightness": 40, "color_temp_kelvin": 2700}
    )

    assert response == {"light.room": CONFIRMED}
    assert sent(broker) == [(GROUP_SET, level(102)), (GROUP_SET, mireds(370))]
    assert kept(broker) == [(GROUP_SET, keep(102))]
    assert reads(broker) == [
        ("zigbee2mqtt/0x01/set", "genLevelCtrl", ["currentLevel"]),
        ("zigbee2mqtt/0x01/set", "lightingColorCtrl", ["colorMode", "colorTemperature"]),
        ("zigbee2mqtt/0x02/set", "genLevelCtrl", ["currentLevel"]),  # no color temperature: not read
    ]


async def test_a_group_with_a_member_that_ignores_the_override_is_rejected(hass, broker):
    group(hass, broker)
    broker.ignores_override.add("0x02")

    with pytest.raises(HomeAssistantError) as raised:
        await call(hass, "set_attributes", {"entity_id": "light.room", "brightness": 40})

    assert raised.value.translation_placeholders["summary"] == "light.room: not_stored"


async def test_a_group_clamps_each_member_from_its_own_level(hass, broker):
    group(hass, broker)

    response = await call(hass, "set_attributes", {"entity_id": "light.room", "max_brightness": 60})

    assert response == {"light.room": CONFIRMED}
    # light.one at 127 is within the limit; light.two comes back at 254, so it is lowered.
    assert sent(broker) == [("zigbee2mqtt/0x02/set", level(152))]
    assert kept(broker) == [("zigbee2mqtt/0x02/set", keep(152))]


async def test_a_group_whose_members_are_all_within_limits_is_unchanged(hass, broker):
    group(hass, broker)

    response = await call(hass, "set_attributes", {"entity_id": "light.room", "min_brightness": 10})

    assert response == {"light.room": result("zigbee2mqtt", "unchanged", detail="within_limits")}
    assert sent(broker) == []


async def test_an_empty_group_is_skipped(hass, broker):
    group(hass, broker, members=[])

    response = await call(hass, "set_attributes", {"entity_id": ["light.room", "light.one"], "brightness": 40})

    assert response == {"light.one": CONFIRMED}


async def test_group_on_another_base_topic_is_not_addressable(hass, broker):
    add_z2m_group(hass, z2m_entry(hass), "light.room", group_id=7, base_topic="z2m_upstairs", state="on")

    assert await refusal(hass, "set_attributes", {"entity_id": "light.room", "brightness": 40}) == "not_addressable"


async def test_device_without_a_name_is_not_addressable(hass, broker):
    add_z2m_entity(hass, z2m_entry(hass), "light.lamp", state="on")

    assert await refusal(hass, "set_attributes", {"entity_id": "light.lamp", "brightness": 40}) == "not_addressable"
