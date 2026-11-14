"""End-to-end through registered services, against the fake integrations."""

import asyncio
import logging
from dataclasses import replace
from unittest.mock import patch

import pytest
from homeassistant.components.light import ColorMode, LightEntityFeature
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError, ServiceNotSupported, ServiceValidationError, Unauthorized
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import floor_registry as fr
from homeassistant.helpers import label_registry as lr

from custom_components.deviceside.core.errors import TargetNotSupported
from custom_components.deviceside.core.models import EntityResult, Status, TimedRequest
from custom_components.deviceside.core.runtime import get_runtime
from custom_components.deviceside.core.scope import (
    AdapterScope,
    DoesntWork,
    documented,
    experimental,
    verified,
)

from ..conftest import allow_experimental, call, refusal
from ..fakes.fake_integration import LIMITS, FakeSetAttributes, FakeTurnOffFor, FakeTurnOnFor
from ..fixtures.fake import add_fake_entity, add_fake_native_group, add_ha_group, add_switch_as_x, fake_config_entry

pytestmark = pytest.mark.usefixtures("setup_integration")

DISPATCHER = "custom_components.deviceside.core.dispatcher"


@pytest.fixture(params=[False, True], ids=["experimental_off", "experimental_on"])
def experimental_option(request, hass, setup_integration):
    """The Experimental features option off and on. The refusal and skip-log tests take it: no fake adapter they
    reach has an Experimental item, so the option must change nothing there. The tests of the gate set it
    themselves, or take its value."""
    allow_experimental(hass, request.param)
    return request.param


@pytest.fixture
def fake(hass):
    return fake_config_entry(hass)


SWITCH_EXPERIMENTAL = AdapterScope(domains=verified("light") | experimental("switch"), duration=LIMITS)


async def test_a_named_target_on_an_experimental_domain_raises_unless_the_option_is_on(
    hass, fake, reset_script, experimental_option
):
    on = experimental_option
    add_fake_entity(hass, fake, "light.a")
    add_fake_entity(hass, fake, "switch.s")
    data = {"entity_id": ["light.a", "switch.s"], "duration": 5}

    with patch.object(FakeTurnOnFor, "scope", SWITCH_EXPERIMENTAL):
        if on:
            assert await call(hass, "turn_on_for", data) == {
                "light.a": {"status": "sent", "integration": "fake"},
                "switch.s": {"status": "sent", "integration": "fake"},
            }
            return
        with pytest.raises(ServiceNotSupported) as err:
            await call(hass, "turn_on_for", data)

    assert err.value.translation_key == "experimental_off"
    assert err.value.translation_placeholders == {"entity_id": "switch.s", "reason": "experimental_off"}
    assert reset_script.calls == []


@pytest.mark.parametrize(
    "domains",
    [
        pytest.param(verified("light") | documented("switch"), id="documented"),
        pytest.param(verified("light", "switch"), id="verified"),
    ],
)
async def test_every_level_above_experimental_passes_with_the_option_off(hass, fake, reset_script, domains):
    add_fake_entity(hass, fake, "switch.s")
    allow_experimental(hass, False)

    with patch.object(FakeTurnOnFor, "scope", AdapterScope(domains=domains, duration=LIMITS)):
        assert await call(hass, "turn_on_for", {"entity_id": "switch.s", "duration": 5}) == {
            "switch.s": {"status": "sent", "integration": "fake"}
        }


async def test_an_indirect_target_on_an_experimental_domain_is_skipped_and_logged(hass, fake, reset_script, caplog):
    add_fake_entity(hass, fake, "light.a")
    add_fake_entity(hass, fake, "switch.s")
    area_id = in_area(hass, "Den", "light.a", "switch.s")
    allow_experimental(hass, False)

    with patch.object(FakeTurnOnFor, "scope", SWITCH_EXPERIMENTAL):
        response = await call(hass, "turn_on_for", {"area_id": area_id, "duration": 5})

    assert response == {"light.a": {"status": "sent", "integration": "fake"}}
    assert "turn_on_for skipped targets that can't take it: switch.s (experimental_off)" in caplog.text


async def test_an_experimental_field_is_gated_only_when_the_call_carries_it(hass, fake, reset_script):
    add_fake_entity(hass, fake, "light.full", attributes=FULL)
    add_fake_entity(hass, fake, "light.no_fade", attributes=FULL | NO_FEATURES)
    scope = AdapterScope(duration=LIMITS, fields=verified("brightness") | experimental("transition"))
    allow_experimental(hass, False)
    data = {"duration": 5, "brightness": 9, "transition": 1}

    with patch.object(FakeTurnOnFor, "scope", scope):
        assert await refusal(hass, "turn_on_for", {"entity_id": "light.full", **data}) == "experimental_off"
        # A light that lacks transition has it dropped first, so its call touches nothing Experimental.
        assert await call(hass, "turn_on_for", {"entity_id": "light.no_fade", **data}) == {
            "light.no_fade": {"status": "sent", "integration": "fake", "detail": "fields_dropped"}
        }
        assert await call(hass, "turn_on_for", {"entity_id": "light.full", "duration": 5, "brightness": 9}) == {
            "light.full": {"status": "sent", "integration": "fake"}
        }
        allow_experimental(hass, True)
        assert await call(hass, "turn_on_for", {"entity_id": "light.full", **data}) == {
            "light.full": {"status": "sent", "integration": "fake"}
        }


def by_name(hass, target):
    """A class per light, from its object id: light.a is class "A"; light.unknown is unnamed."""
    name = target.entity_id.partition(".")[2]
    return None if name in ("unknown", "fake_group") else name.upper()


@pytest.fixture
def classed(hass, setup_integration):
    """The fake integration naming classes A to E by_name, as IntegrationInfo.capability does: its info swapped in
    the loaded registry for the test."""
    registry = get_runtime(hass).registry
    info = registry.integrations["fake"]
    registry.integrations["fake"] = replace(info, capability=by_name, classes=frozenset("ABCDE"))
    yield
    registry.integrations["fake"] = info


CLASSED = AdapterScope(
    domains=verified("light", "switch"),
    duration=LIMITS,
    capabilities=verified("A") | experimental("B"),
    doesnt_work={"D": DoesntWork("Plug D", "It never switches on", "https://example.com/d")},
    native_groups=True,
)


@pytest.mark.usefixtures("classed")
async def test_an_experimental_class_is_gated_by_the_one_the_integration_names(hass, fake, reset_script):
    add_fake_entity(hass, fake, "light.a")
    add_fake_entity(hass, fake, "light.b")
    allow_experimental(hass, False)

    with patch.object(FakeTurnOnFor, "scope", CLASSED):
        assert await call(hass, "turn_on_for", {"entity_id": "light.a", "duration": 5}) == {
            "light.a": {"status": "sent", "integration": "fake", "capability": "A"}
        }
        assert await refusal(hass, "turn_on_for", {"entity_id": "light.b", "duration": 5}) == "experimental_off"
        allow_experimental(hass, True)
        assert await call(hass, "turn_on_for", {"entity_id": "light.b", "duration": 5}) == {
            "light.b": {"status": "sent", "integration": "fake", "capability": "B"}
        }


@pytest.mark.usefixtures("classed")
async def test_a_class_that_doesnt_work_or_isnt_served_is_refused_and_none_is_gated(hass, fake, reset_script, caplog):
    for entity_id in ("light.a", "light.c", "light.d", "light.unknown"):
        add_fake_entity(hass, fake, entity_id)
    area_id = in_area(hass, "Den", "light.a", "light.c", "light.d")
    allow_experimental(hass, False)

    with patch.object(FakeTurnOnFor, "scope", CLASSED):
        assert await refusal(hass, "turn_on_for", {"entity_id": "light.c", "duration": 5}) == "not_supported"
        assert await refusal(hass, "turn_on_for", {"entity_id": "light.d", "duration": 5}) == "doesnt_work"
        # One the integration can't name is no refusal, but fails safe at the gate.
        assert await refusal(hass, "turn_on_for", {"entity_id": "light.unknown", "duration": 5}) == "experimental_off"
        # Reached indirectly, both are skipped, as any target that can never take the action is.
        response = await call(hass, "turn_on_for", {"area_id": area_id, "duration": 5})

    assert response == {"light.a": {"status": "sent", "integration": "fake", "capability": "A"}}
    assert "skipped targets that can't take it: light.c (not_supported), light.d (doesnt_work)" in caplog.text
    assert "turn_on_for on fake: light.c is class ('C',)" in caplog.text
    assert reset_script.calls == [("fake", "turn_on_for", reset_script.calls[0][2], ["light.a"])]


@pytest.mark.usefixtures("classed")
async def test_an_adapters_own_refusal_and_nothing_to_do_come_before_the_class(hass, fake, reset_script):
    add_fake_entity(hass, fake, "light.d", attributes={"supported_color_modes": [ColorMode.ONOFF]})
    add_fake_entity(hass, fake, "switch.c")
    scope = replace(CLASSED, domains=verified("light"), fields=verified("brightness"))

    with patch.object(FakeTurnOnFor, "scope", scope):
        assert await refusal(hass, "turn_on_for", {"entity_id": "switch.c", "duration": 5}) == "domain_not_tested"
    with patch.object(FakeSetAttributes, "scope", scope):
        # Nothing left to send, so nothing to refuse: unchanged, with the class it would have reached.
        assert await call(hass, "set_attributes", {"entity_id": "light.d", "brightness": 50}) == {
            "light.d": {"status": "unchanged", "integration": "fake", "capability": "D", "detail": "fields_dropped"}
        }


@pytest.mark.usefixtures("classed")
async def test_a_native_group_takes_its_members_classes(hass, fake, reset_script):
    members = ["switch.e", "switch.a", "light.a"]
    for member in members:
        add_fake_entity(hass, fake, member)
    add_fake_native_group(hass, fake, "switch.fake_group", members)
    allow_experimental(hass, False)
    scope = replace(CLASSED, capabilities=verified("A", "E") | experimental("B"))

    with patch.object(FakeTurnOffFor, "scope", scope):
        response = await call(hass, "turn_off_for", {"entity_id": "switch.fake_group", "duration": 5})

    # Sorted, each once.
    assert response == {"switch.fake_group": {"status": "sent", "integration": "fake", "capabilities": ["A", "E"]}}


@pytest.mark.usefixtures("classed")
@pytest.mark.parametrize("flagged", ["switch.b", "switch.unknown"])  # an Experimental class, and none
async def test_a_native_group_with_a_flagged_member_falls_back_while_experimental_is_off(
    hass, fake, reset_script, caplog, flagged
):
    for member in ("switch.a", flagged):
        add_fake_entity(hass, fake, member)
    add_fake_native_group(hass, fake, "switch.fake_group", ["switch.a", flagged])
    allow_experimental(hass, False)
    data = {"entity_id": "switch.fake_group", "duration": 5}

    with patch.object(FakeTurnOffFor, "scope", CLASSED):
        off = await call(hass, "turn_off_for", data)
        allow_experimental(hass)
        on = await call(hass, "turn_off_for", data)

    # Off: the member of a Verified class runs on its own, the flagged one is skipped as indirect.
    assert off == {"switch.a": {"status": "sent", "integration": "fake", "capability": "A"}}
    assert f"turn_off_for skipped targets that can't take it: {flagged} (experimental_off)" in caplog.text
    # On: the group runs whole, as one command.
    assert list(on) == ["switch.fake_group"]
    assert [ids for *_, ids in reset_script.calls] == [["switch.a"], ["switch.fake_group"]]


@pytest.mark.usefixtures("classed")
async def test_a_flagged_member_named_too_raises(hass, fake, reset_script):
    for member in ("switch.a", "switch.b"):
        add_fake_entity(hass, fake, member)
    add_fake_native_group(hass, fake, "switch.fake_group", ["switch.a", "switch.b"])
    allow_experimental(hass, False)

    with patch.object(FakeTurnOffFor, "scope", CLASSED):
        data = {"entity_id": ["switch.fake_group", "switch.b"], "duration": 5}
        assert await refusal(hass, "turn_off_for", data) == "experimental_off"
    assert reset_script.calls == []


@pytest.mark.usefixtures("classed")
@pytest.mark.parametrize(("bad", "reason"), [("switch.c", "not_supported"), ("switch.d", "doesnt_work")])
async def test_a_native_group_with_a_refused_member_falls_back_to_its_members(
    hass, fake, reset_script, caplog, bad, reason
):
    for member in ("switch.a", "switch.e", bad):
        add_fake_entity(hass, fake, member)
    add_fake_native_group(hass, fake, "switch.fake_group", ["switch.a", bad, "switch.e"])
    scope = replace(CLASSED, capabilities=verified("A", "E"))

    with patch.object(FakeTurnOffFor, "scope", scope):
        # Named, the group doesn't raise: its members are indirect, so the refused one is skipped and logged.
        response = await call(hass, "turn_off_for", {"entity_id": ["switch.fake_group", "switch.a"], "duration": 5})

    assert response == {
        "switch.a": {"status": "sent", "integration": "fake", "capability": "A"},
        "switch.e": {"status": "sent", "integration": "fake", "capability": "E"},
    }
    assert reset_script.calls[0][3] == ["switch.a", "switch.e"]  # switch.a once, though named and a member
    assert f"turn_off_for skipped targets that can't take it: {bad} ({reason})" in caplog.text


@pytest.mark.usefixtures("classed")
@pytest.mark.parametrize("flagged", ["switch.c", "switch.b"])  # not supported, Experimental
async def test_a_group_whose_member_is_already_a_target_is_answered_by_it(hass, fake, reset_script, caplog, flagged):
    """Naming a member beside its group changes nothing: switch.a, already a target, answers for the group, and
    the flagged member is unavailable."""
    add_fake_entity(hass, fake, "switch.a")
    add_fake_entity(hass, fake, flagged, state="unavailable")
    add_fake_native_group(hass, fake, "switch.fake_group", ["switch.a", flagged])
    area_id = in_area(hass, "Den", "switch.fake_group")
    allow_experimental(hass, False)
    expected = {"switch.a": {"status": "sent", "integration": "fake", "capability": "A"}}

    with patch.object(FakeTurnOffFor, "scope", CLASSED):
        alone = await call(hass, "turn_off_for", {"entity_id": "switch.fake_group", "duration": 5})
        both = await call(hass, "turn_off_for", {"entity_id": ["switch.a", "switch.fake_group"], "duration": 5})
        indirect = await call(hass, "turn_off_for", {"area_id": area_id, "entity_id": "switch.a", "duration": 5})

    assert alone == both == indirect == expected
    assert "skipped" not in caplog.text


@pytest.mark.usefixtures("classed")
@pytest.mark.parametrize(
    ("members", "reason"),
    [
        (["switch.b", "switch.unknown"], "experimental_off"),  # an Experimental class, and none
        (["switch.d"], "doesnt_work"),
        (["switch.c", "switch.b"], "not_supported"),  # refused first, then gated
        (["switch.d", "switch.unknown"], "doesnt_work"),
    ],
)
async def test_a_group_whose_members_all_fail_is_refused_with_its_reason(
    hass, fake, reset_script, caplog, members, reason
):
    for member in members:
        add_fake_entity(hass, fake, member)
    add_fake_native_group(hass, fake, "switch.fake_group", members)
    area_id = in_area(hass, "Den", "switch.fake_group")
    allow_experimental(hass, False)

    with patch.object(FakeTurnOffFor, "scope", CLASSED):
        with pytest.raises(TargetNotSupported) as raised:
            await call(hass, "turn_off_for", {"entity_id": "switch.fake_group", "duration": 5})
        assert raised.value.reason == reason
        if members == ["switch.d"]:
            # The refused member's own why, as a named target's.
            assert raised.value.translation_key == "doesnt_work"
            assert raised.value.translation_placeholders["why"] == "It never switches on"
        # Indirect, the group and its members are skipped and logged.
        assert await call(hass, "turn_off_for", {"area_id": area_id, "duration": 5}, return_response=False) is None

    assert reset_script.calls == []
    assert f"switch.fake_group ({reason})" in caplog.text


@pytest.mark.usefixtures("classed")
async def test_a_fallen_group_whose_member_has_nothing_to_do_is_answered_by_it(hass, fake, reset_script):
    """light.a lacks brightness, so it is unchanged, and that answers for the group beside the refused light.d."""
    modes = {"supported_color_modes": [ColorMode.BRIGHTNESS], "brightness": 100}
    add_fake_entity(hass, fake, "light.a", attributes={"supported_color_modes": [ColorMode.ONOFF]})
    add_fake_entity(hass, fake, "light.d", attributes=modes)
    add_fake_entity(
        hass,
        fake,
        "light.fake_group",
        attributes={"fake_members": ["light.a", "light.d"], **modes},
        unique_id="group:light.fake_group",
    )
    scope = replace(FakeSetAttributes.scope, capabilities=CLASSED.capabilities, doesnt_work=CLASSED.doesnt_work)
    scope = replace(scope, native_groups=True)

    with patch.object(FakeSetAttributes, "scope", scope):
        response = await call(hass, "set_attributes", {"entity_id": "light.fake_group", "brightness": 50})

    assert response == {
        "light.a": {"status": "unchanged", "integration": "fake", "capability": "A", "detail": "fields_dropped"}
    }
    assert reset_script.calls == []


async def test_an_expand_group_that_raises_is_logged_and_the_group_not_supported(hass, fake, reset_script, caplog):
    add_fake_entity(hass, fake, "light.a")
    add_fake_native_group(hass, fake, "light.fake_group", ["light.a"])
    registry = get_runtime(hass).registry

    def fail(hass, target):
        raise RuntimeError("listing bug")

    registry.integrations["fake"] = replace(registry.integrations["fake"], expand_group=fail)
    data = {"entity_id": "light.fake_group", "duration": 5}

    assert await refusal(hass, "turn_on_for", data) == "group_not_supported"
    assert [record.getMessage() for record in caplog.records if record.exc_info] == [
        "turn_on_for on fake: listing light.fake_group failed"
    ]
    assert reset_script.calls == []


async def test_an_expand_group_that_can_t_list_the_group_leaves_it_not_supported(hass, fake, reset_script):
    """None is its answer for membership unknowable now (Zigbee2MQTT's, before bridge/groups arrives)."""
    add_fake_entity(hass, fake, "light.a")
    add_fake_native_group(hass, fake, "light.fake_group", ["light.a"])
    registry = get_runtime(hass).registry
    registry.integrations["fake"] = replace(registry.integrations["fake"], expand_group=lambda hass, target: None)

    assert await refusal(hass, "turn_on_for", {"entity_id": "light.fake_group", "duration": 5}) == "group_not_supported"
    assert reset_script.calls == []


@pytest.mark.usefixtures("classed")
async def test_a_named_target_that_doesnt_work_raises_with_why(hass, fake, reset_script):
    add_fake_entity(hass, fake, "light.d")

    with patch.object(FakeTurnOnFor, "scope", CLASSED), pytest.raises(ServiceNotSupported) as err:
        await call(hass, "turn_on_for", {"entity_id": "light.d", "duration": 5})

    assert err.value.translation_key == "doesnt_work"
    assert err.value.translation_placeholders == {
        "entity_id": "light.d",
        "reason": "doesnt_work",
        "why": "It never switches on",
    }


@pytest.mark.usefixtures("classed")
async def test_an_empty_native_group_is_skipped_silently(hass, fake, reset_script, caplog):
    """docs/action-behavior.md: as core's group expansion yields nothing for one, named or not."""
    add_fake_entity(hass, fake, "switch.a")
    add_fake_native_group(hass, fake, "switch.fake_group", [])
    allow_experimental(hass, False)

    with patch.object(FakeTurnOffFor, "scope", CLASSED):
        response = await call(hass, "turn_off_for", {"entity_id": ["switch.fake_group", "switch.a"], "duration": 5})

    assert response == {"switch.a": {"status": "sent", "integration": "fake", "capability": "A"}}
    assert "skipped" not in caplog.text


@pytest.mark.usefixtures("classed")
@pytest.mark.parametrize("broken", ["capability", "expand_group"])
async def test_a_classifier_that_raises_is_logged_and_counts_as_unknown(hass, fake, reset_script, caplog, broken):
    add_fake_entity(hass, fake, "switch.a")
    add_fake_native_group(hass, fake, "switch.fake_group", ["switch.a"])
    registry = get_runtime(hass).registry

    def fail(hass, target):
        raise RuntimeError("classifier bug")

    registry.integrations["fake"] = replace(registry.integrations["fake"], **{broken: fail})
    allow_experimental(hass, False)
    data = {"entity_id": "switch.fake_group", "duration": 5}

    with patch.object(FakeTurnOffFor, "scope", CLASSED):
        # Listed, the group falls back to its member, classed again, whose class is unknown too: with no member
        # planned, the named group raises. Unlisted, it is gated as Experimental.
        assert await refusal(hass, "turn_off_for", data) == "experimental_off"
    assert reset_script.calls == []

    failed = [record.getMessage() for record in caplog.records if record.exc_info]
    if broken == "expand_group":
        assert failed == ["turn_off_for on fake: listing switch.fake_group failed"]
    else:
        assert failed == [
            f"turn_off_for on fake: classing {entity_id} failed" for entity_id in ("switch.fake_group", "switch.a")
        ]


@pytest.mark.usefixtures("classed")
async def test_an_expand_group_answering_none_gates_a_classed_group(hass, fake, reset_script):
    add_fake_entity(hass, fake, "switch.a")
    add_fake_native_group(hass, fake, "switch.fake_group", ["switch.a"])
    registry = get_runtime(hass).registry
    registry.integrations["fake"] = replace(registry.integrations["fake"], expand_group=lambda hass, target: None)
    allow_experimental(hass, False)
    data = {"entity_id": "switch.fake_group", "duration": 5}

    with patch.object(FakeTurnOffFor, "scope", CLASSED):
        assert await refusal(hass, "turn_off_for", data) == "experimental_off"
        allow_experimental(hass)
        assert await call(hass, "turn_off_for", data) == {
            "switch.fake_group": {"status": "sent", "integration": "fake"}
        }


@pytest.mark.usefixtures("classed")
async def test_a_named_empty_group_alone_is_skipped_silently(hass, fake, reset_script, caplog):
    add_fake_native_group(hass, fake, "switch.fake_group", [])

    with patch.object(FakeTurnOffFor, "scope", CLASSED):
        data = {"entity_id": "switch.fake_group", "duration": 5}
        assert await call(hass, "turn_off_for", data, return_response=False) is None

    assert reset_script.calls == []
    assert "skipped" not in caplog.text


FALLS_BACK = "turn_off_for: switch.fake_group falls back to its members"


@pytest.mark.usefixtures("classed")
@pytest.mark.parametrize(
    "scope",
    [
        pytest.param(replace(CLASSED, domains=experimental("switch")), id="domain"),
        pytest.param(replace(CLASSED, experimental=True), id="adapter"),
    ],
)
async def test_a_group_flagged_by_more_than_its_classes_does_not_fall_back(hass, fake, reset_script, caplog, scope):
    caplog.set_level(logging.DEBUG, logger="custom_components.deviceside")
    for member in ("switch.a", "switch.b"):
        add_fake_entity(hass, fake, member)
    add_fake_native_group(hass, fake, "switch.fake_group", ["switch.a", "switch.b"])
    allow_experimental(hass, False)

    with patch.object(FakeTurnOffFor, "scope", scope):
        assert await refusal(hass, "turn_off_for", {"entity_id": "switch.fake_group", "duration": 5}) == (
            "experimental_off"
        )

    assert FALLS_BACK not in caplog.text


@pytest.mark.usefixtures("classed")
async def test_a_group_flagged_by_a_field_does_not_fall_back(hass, fake, reset_script, caplog):
    caplog.set_level(logging.DEBUG, logger="custom_components.deviceside")
    modes = {"supported_color_modes": [ColorMode.BRIGHTNESS], "brightness": 100}
    add_fake_entity(hass, fake, "light.a", attributes=modes)
    add_fake_entity(
        hass,
        fake,
        "light.fake_group",
        attributes={"fake_members": ["light.a"], **modes},
        unique_id="group:light.fake_group",
    )
    allow_experimental(hass, False)
    scope = replace(CLASSED, fields=experimental("brightness"), domains=verified("light"))

    with patch.object(FakeTurnOnFor, "scope", scope):
        data = {"entity_id": "light.fake_group", "duration": 5, "brightness": 50}
        assert await refusal(hass, "turn_on_for", data) == "experimental_off"

    assert "turn_on_for: light.fake_group falls back" not in caplog.text


@pytest.mark.usefixtures("classed")
async def test_two_groups_sharing_a_member_send_it_once(hass, fake, reset_script):
    for member in ("switch.a", "switch.b", "switch.c"):
        add_fake_entity(hass, fake, member)
    add_fake_native_group(hass, fake, "switch.one", ["switch.a", "switch.b"])
    add_fake_native_group(hass, fake, "switch.two", ["switch.a", "switch.c"])
    allow_experimental(hass, False)

    with patch.object(FakeTurnOffFor, "scope", CLASSED):
        response = await call(hass, "turn_off_for", {"entity_id": ["switch.one", "switch.two"], "duration": 5})

    assert response == {"switch.a": {"status": "sent", "integration": "fake", "capability": "A"}}
    assert [ids for *_, ids in reset_script.calls] == [["switch.a"]]


@pytest.mark.usefixtures("classed")
async def test_a_group_with_refused_and_experimental_members_falls_back_with_experimental_on(hass, fake, reset_script):
    for member in ("switch.a", "switch.b", "switch.c"):
        add_fake_entity(hass, fake, member)
    add_fake_native_group(hass, fake, "switch.fake_group", ["switch.a", "switch.b", "switch.c"])
    allow_experimental(hass)

    with patch.object(FakeTurnOffFor, "scope", CLASSED):
        response = await call(hass, "turn_off_for", {"entity_id": "switch.fake_group", "duration": 5})

    # switch.c isn't supported, so the group falls back; with the option on, switch.b runs beside switch.a.
    assert sorted(response) == ["switch.a", "switch.b"]


@pytest.mark.usefixtures("classed")
async def test_a_native_group_whose_members_cant_be_listed_is_gated_and_names_no_class(hass, fake, reset_script):
    add_fake_native_group(hass, fake, "switch.fake_group", [])
    registry = get_runtime(hass).registry
    registry.integrations["fake"] = replace(registry.integrations["fake"], expand_group=None)
    allow_experimental(hass, False)
    data = {"entity_id": "switch.fake_group", "duration": 5}

    with patch.object(FakeTurnOffFor, "scope", CLASSED):
        assert await refusal(hass, "turn_off_for", data) == "experimental_off"
        allow_experimental(hass, True)
        # No list, which would pass for the whole group's.
        assert await call(hass, "turn_off_for", data) == {
            "switch.fake_group": {"status": "sent", "integration": "fake"}
        }


async def test_an_experimental_call_left_nothing_to_send_is_unchanged_not_refused(hass, fake, reset_script):
    add_fake_entity(hass, fake, "light.onoff", attributes={"supported_color_modes": [ColorMode.ONOFF]})
    allow_experimental(hass, False)

    with patch.object(
        FakeSetAttributes, "scope", AdapterScope(domains=experimental("light"), fields=verified("brightness"))
    ):
        response = await call(hass, "set_attributes", {"entity_id": "light.onoff", "brightness": 50})

    assert response == {"light.onoff": {"status": "unchanged", "integration": "fake", "detail": "fields_dropped"}}
    assert reset_script.calls == []


async def test_a_flagged_adapter_is_refused_unless_the_option_is_on(hass, fake, reset_script, caplog):
    """experimental=True flags every item of the adapter, Verified ones included: a named target raises and an
    indirect one is skipped while the option is off, and both run once it is on."""
    add_fake_entity(hass, fake, "light.named")
    add_fake_entity(hass, fake, "light.member")
    add_ha_group(hass, "light.room", ["light.member"])
    flagged = AdapterScope(experimental=True, duration=LIMITS)
    allow_experimental(hass, False)

    with patch.object(FakeTurnOnFor, "scope", flagged):
        assert await refusal(hass, "turn_on_for", {"entity_id": "light.named", "duration": 5}) == "experimental_off"
        await call(hass, "turn_on_for", {"entity_id": "light.room", "duration": 5}, return_response=False)
        assert "light.member (experimental_off)" in caplog.text
        assert reset_script.calls == []

        allow_experimental(hass, True)
        response = await call(hass, "turn_on_for", {"entity_id": ["light.named", "light.room"], "duration": 5})

    assert set(response) == {"light.named", "light.member"}
    assert {result["status"] for result in response.values()} == {"sent"}


async def test_response_shape(hass, fake, reset_script):
    add_fake_entity(hass, fake, "light.desk")
    reset_script.outcomes["light.desk"] = Status.CONFIRMED

    response = await call(hass, "turn_on_for", {"entity_id": "light.desk", "duration": 5})

    assert response == {"light.desk": {"status": "confirmed", "integration": "fake"}}
    [(integration, action, request, entity_ids)] = reset_script.calls
    assert (integration, action, entity_ids) == ("fake", "turn_on_for", ["light.desk"])
    assert request.duration_s == 5


async def test_unchanged_everywhere_is_not_a_failure(hass, fake, reset_script):
    # Nothing to do is a result, not an error: the call returns rather than stopping the automation.
    add_fake_entity(hass, fake, "light.desk")
    reset_script.outcomes["light.desk"] = Status.UNCHANGED

    response = await call(hass, "turn_on_for", {"entity_id": "light.desk", "duration": 5})

    assert response == {"light.desk": {"status": "unchanged", "integration": "fake"}}


async def test_no_response_requested_returns_none(hass, fake):
    add_fake_entity(hass, fake, "light.desk")

    assert await call(hass, "turn_on_for", {"entity_id": "light.desk", "duration": 5}, return_response=False) is None


async def test_duration_is_clamped_and_reported(hass, fake, reset_script):
    add_fake_entity(hass, fake, "light.desk")

    response = await call(hass, "turn_on_for", {"entity_id": "light.desk", "duration": 500})

    assert response["light.desk"]["clamped_to"] == 100
    assert reset_script.calls[0][2].duration_s == 100


def in_area(hass, area: str, *entity_ids: str) -> str:
    area_id = ar.async_get(hass).async_get_or_create(area).id
    for entity_id in entity_ids:
        dr.async_get(hass).async_update_device(er.async_get(hass).async_get(entity_id).device_id, area_id=area_id)
    return area_id


async def test_a_device_failure_raises_once_every_target_has_run(hass, fake, reset_script):
    for entity_id in ("light.a", "light.b", "light.c"):
        add_fake_entity(hass, fake, entity_id)
    reset_script.outcomes["light.b"] = RuntimeError("radio busy")
    reset_script.outcomes["light.c"] = Status.REJECTED

    with pytest.raises(HomeAssistantError) as err:
        await call(hass, "turn_on_for", {"entity_id": ["light.a", "light.b", "light.c"], "duration": 5})

    assert err.value.translation_key == "targets_failed"
    assert err.value.translation_placeholders == {"summary": "light.b: error, light.c: rejected"}
    assert reset_script.calls[0][3] == ["light.a", "light.b", "light.c"]


async def test_an_unauthorized_target_is_rejected_as_admin_required_and_the_rest_run(hass, fake, reset_script):
    for entity_id in ("light.a", "light.b"):
        add_fake_entity(hass, fake, entity_id)
    reset_script.outcomes["light.a"] = Unauthorized()

    with pytest.raises(HomeAssistantError) as err:
        await call(hass, "turn_on_for", {"entity_id": ["light.a", "light.b"], "duration": 5})

    assert err.value.translation_key == "targets_failed"
    assert err.value.translation_placeholders == {"summary": "light.a: admin_required"}
    assert reset_script.calls[0][3] == ["light.a", "light.b"]


@pytest.mark.usefixtures("experimental_option")
async def test_a_named_target_that_cant_take_the_action_raises_before_anything_is_sent(hass, fake, reset_script):
    add_fake_entity(hass, fake, "light.a")
    add_fake_entity(hass, fake, "light.b")
    add_fake_entity(hass, fake, "cover.blind")

    with pytest.raises(ServiceNotSupported) as err:  # core's own class, so callers can catch it as they do core's
        await call(hass, "turn_on_for", {"entity_id": ["light.a", "light.b", "cover.blind"], "duration": 5})

    assert err.value.translation_key == "target_not_supported"
    assert err.value.translation_placeholders == {"entity_id": "cover.blind", "reason": "domain_not_supported"}
    assert reset_script.calls == []
    # An adapter's own refusal raises before any adapter runs too, whichever runs first.
    assert await refusal(hass, "turn_off_for", {"entity_id": ["switch.ok", "light.a"], "duration": 5}) == (
        "domain_not_tested"
    )
    assert reset_script.calls == []


@pytest.mark.usefixtures("experimental_option")
async def test_an_indirect_target_that_cant_take_the_action_is_skipped_and_logged(hass, fake, reset_script, caplog):
    add_fake_entity(hass, fake, "light.a")
    add_fake_entity(hass, fake, "cover.blind")
    add_fake_entity(hass, fake, "light.lamp", identifier="special_1")
    add_fake_entity(hass, fake, "switch.s")
    area_id = in_area(hass, "Den", "light.a", "cover.blind")
    add_ha_group(hass, "light.room", ["light.lamp", "switch.s"])

    response = await call(hass, "turn_off_for", {"area_id": area_id, "entity_id": "light.room", "duration": 5})

    # A light group helper is a deliberately mixed set, so its members are indirect, unlike core's group.* rule.
    assert response == {"switch.s": {"status": "sent", "integration": "fake"}}
    assert (
        "turn_off_for skipped targets that can't take it: light.a (domain_not_tested), light.lamp (no_adapter)"
        in caplog.text
    )
    assert "cover.blind (" not in caplog.text  # another domain in an area is never the action's, as in core


async def test_unavailable_targets_are_skipped_and_the_named_ones_logged(hass, fake, reset_script, caplog):
    add_fake_entity(hass, fake, "light.a")
    add_fake_entity(hass, fake, "light.down", state="unavailable")
    add_fake_entity(hass, fake, "light.away", state="unavailable")
    unloaded = fake_config_entry(hass, ConfigEntryState.NOT_LOADED)
    add_fake_entity(hass, unloaded, "light.unloaded")
    area_id = in_area(hass, "Den", "light.away")

    response = await call(
        hass,
        "turn_on_for",
        {"entity_id": ["light.a", "light.down", "light.ghost", "light.unloaded"], "area_id": area_id, "duration": 5},
    )

    assert response == {"light.a": {"status": "sent", "integration": "fake"}}
    assert reset_script.calls[0][3] == ["light.a"]
    assert (
        "Referenced entities light.down, light.ghost, light.unloaded are missing or not currently available"
        in caplog.text
    )


async def test_no_targets_left_returns_or_raises_by_response_mode(hass, fake, reset_script):
    add_fake_entity(hass, fake, "light.a")  # an empty selection must not mean "all": nothing reaches it
    add_fake_entity(hass, fake, "light.down", state="unavailable")
    add_fake_entity(hass, fake, "cover.blind")
    area_id = in_area(hass, "Den", "cover.blind")

    for data in ({"entity_id": "light.down"}, {"area_id": area_id}, {"entity_id": []}):
        assert await call(hass, "turn_on_for", {**data, "duration": 5}, return_response=False) is None
        with pytest.raises(HomeAssistantError) as err:
            await call(hass, "turn_on_for", {**data, "duration": 5})
        assert err.value.translation_key == "no_entities_matched"
    assert reset_script.calls == []


async def test_invalid_input(hass, fake):
    add_fake_entity(hass, fake, "light.a")

    with pytest.raises(ServiceValidationError) as err:
        await call(hass, "turn_on_for", {"entity_id": "light.a", "duration": 0})
    assert err.value.translation_key == "invalid_duration"


async def test_entity_id_all_acts_on_every_supported_light_and_skips_the_rest_quietly(hass, fake, reset_script, caplog):
    add_fake_entity(hass, fake, "light.a")
    add_fake_entity(hass, fake, "light.b")
    hass.states.async_set("light.yaml_only", "off")  # no registry entry, so no integration to hand it to
    add_fake_entity(hass, fake, "light.down", state="unavailable")
    add_fake_entity(hass, fake, "cover.blind")

    response = await call(hass, "turn_on_for", {"entity_id": "all", "duration": 5})

    assert list(response) == ["light.a", "light.b"]
    assert reset_script.calls[0][3] == ["light.a", "light.b"]
    # Nothing is named: the unsupported light is skipped with the one warning rather than raised, and the
    # unavailable one is skipped without a word, as core does for "all".
    assert "turn_on_for skipped targets that can't take it: light.yaml_only (not_in_registry)" in caplog.text
    # Warnings only: caplog captures at DEBUG, which turns the per-call DEBUG lines on.
    ours = [
        r.getMessage()
        for r in caplog.records
        if r.name.startswith("custom_components.deviceside") and r.levelno >= logging.WARNING
    ]
    assert ours == ["turn_on_for skipped targets that can't take it: light.yaml_only (not_in_registry)"]


async def test_a_label_target_resolves_to_its_lights(hass, fake, reset_script):
    add_fake_entity(hass, fake, "light.a")
    add_fake_entity(hass, fake, "light.b")
    label = lr.async_get(hass).async_create("Evening")
    er.async_get(hass).async_update_entity("light.a", labels={label.label_id})

    response = await call(hass, "turn_on_for", {"label_id": label.label_id, "duration": 5})

    assert list(response) == ["light.a"]
    assert reset_script.calls[0][3] == ["light.a"]


async def test_a_floor_target_resolves_to_the_lights_in_its_areas(hass, fake, reset_script):
    add_fake_entity(hass, fake, "light.a")
    add_fake_entity(hass, fake, "light.b")
    floor = fr.async_get(hass).async_create("Upstairs")
    area_id = in_area(hass, "Landing", "light.a")
    ar.async_get(hass).async_update(area_id, floor_id=floor.floor_id)

    response = await call(hass, "turn_on_for", {"floor_id": floor.floor_id, "duration": 5})

    assert list(response) == ["light.a"]
    assert reset_script.calls[0][3] == ["light.a"]


async def test_ha_groups_expand_for_light_and_switch_and_an_empty_one_is_left_out(hass, fake, reset_script):
    for entity_id in ("light.a", "light.b", "switch.c"):
        add_fake_entity(hass, fake, entity_id)
    add_ha_group(hass, "light.room", ["light.a", "light.b"])
    add_ha_group(hass, "switch.plugs", ["switch.c"])
    add_ha_group(hass, "light.nobody", [])

    data = {"entity_id": ["light.room", "switch.plugs", "light.nobody"], "duration": 5}
    response = await call(hass, "turn_on_for", data)

    assert list(response) == ["light.a", "light.b", "switch.c"]
    assert reset_script.calls[0][3] == ["light.a", "light.b", "switch.c"]


async def test_native_group_passes_through_when_adapter_addresses_groups(hass, fake, reset_script):
    add_fake_entity(hass, fake, "switch.a")
    add_fake_native_group(hass, fake, "switch.fake_group", ["switch.a"])

    response = await call(hass, "turn_off_for", {"entity_id": "switch.fake_group", "duration": 5})

    assert list(response) == ["switch.fake_group"]
    assert reset_script.calls[0][3] == ["switch.fake_group"]


async def test_native_group_expands_when_adapter_cannot_address_it_and_an_empty_one_is_left_out(
    hass, fake, reset_script
):
    add_fake_entity(hass, fake, "light.a")
    add_fake_entity(hass, fake, "light.b")
    add_fake_native_group(hass, fake, "light.fake_group", ["light.a", "light.b"])
    add_fake_native_group(hass, fake, "light.empty_group", [])

    data = {"entity_id": ["light.fake_group", "light.empty_group", "light.a"], "duration": 5}
    response = await call(hass, "turn_on_for", data)

    assert set(response) == {"light.a", "light.b"}
    assert reset_script.calls[0][3] == ["light.a", "light.b"]


async def test_native_group_members_are_indirect(hass, fake, reset_script):
    add_fake_entity(hass, fake, "light.a")
    add_fake_entity(hass, fake, "light.dim", attributes={"supported_color_modes": [ColorMode.BRIGHTNESS]})
    add_fake_entity(hass, fake, "light.down", state="unavailable")
    add_fake_native_group(hass, fake, "light.fake_group", ["light.a", "light.dim", "light.down"])

    response = await call(hass, "turn_on_for", {"entity_id": "light.fake_group", "duration": 5, "rgb_color": [1, 2, 3]})

    # light.a would take the untested color, so it is skipped rather than raised; light.dim lacks it and drops it.
    assert response == {"light.dim": {"status": "sent", "integration": "fake", "detail": "fields_dropped"}}
    assert reset_script.calls[0][3] == ["light.dim"]


async def test_a_named_member_stays_named_after_its_group_expands_first(hass, fake, reset_script):
    add_fake_entity(hass, fake, "light.lamp")
    add_fake_entity(hass, fake, "light.dim", attributes={"supported_color_modes": [ColorMode.BRIGHTNESS]})
    add_fake_native_group(hass, fake, "light.fake_group", ["light.lamp", "light.dim"])

    # The group sorts first, so its indirect copy of light.lamp is met before the named one.
    with pytest.raises(ServiceNotSupported) as err:
        await call(
            hass,
            "turn_on_for",
            {"entity_id": ["light.fake_group", "light.lamp"], "duration": 5, "rgb_color": [1, 2, 3]},
        )

    assert err.value.translation_placeholders["entity_id"] == "light.lamp"
    assert reset_script.calls == []


async def test_native_group_without_expand_hook_is_refused(hass, fake, reset_script):
    add_fake_entity(hass, fake, "light.special_group", unique_id="group:special", identifier="special_g")

    assert await refusal(hass, "turn_on_for", {"entity_id": "light.special_group", "duration": 5}) == (
        "group_not_supported"
    )
    assert reset_script.calls == []


@pytest.mark.usefixtures("experimental_option")
async def test_refusal_reasons_from_resolution_and_adapters(hass, fake):
    add_fake_entity(hass, fake, "light.a")
    add_fake_entity(hass, fake, "light.lamp", identifier="special_1")
    add_ha_group(hass, "light.nested", ["light.ghost_member"])
    hass.states.async_set("light.yaml_only", "off")

    assert await refusal(hass, "turn_on_for", {"entity_id": "light.a", "duration": 5, "rgb_color": [255, 0, 0]}) == (
        "fields_not_tested"
    )
    assert await refusal(hass, "turn_off_for", {"entity_id": "light.a", "duration": 5}) == "domain_not_tested"
    assert await refusal(hass, "turn_off_for", {"entity_id": "light.lamp", "duration": 5}) == "no_adapter"
    assert await refusal(hass, "turn_on_for", {"entity_id": "light.yaml_only", "duration": 5}) == "not_in_registry"


async def test_an_untested_field_is_refused_after_lacked_ones_are_dropped(hass, fake, reset_script):
    # brightness_only lacks color, so rgb_color is dropped and nothing untested is left; light.a would take color,
    # and the fake adapter has never been tested with it.
    add_fake_entity(hass, fake, "light.a", attributes={"supported_color_modes": [ColorMode.HS]})
    add_fake_entity(hass, fake, "light.dim", attributes={"supported_color_modes": [ColorMode.BRIGHTNESS]})
    data = {"duration": 5, "rgb_color": [255, 0, 0], "brightness": 9}

    response = await call(hass, "turn_on_for", {"entity_id": "light.dim", **data})
    assert response == {"light.dim": {"status": "sent", "integration": "fake", "detail": "fields_dropped"}}
    reset_script.calls.clear()
    assert await refusal(hass, "turn_on_for", {"entity_id": ["light.dim", "light.a"], **data}) == "fields_not_tested"
    assert reset_script.calls == []  # light.dim is planned first, and still nothing is sent


async def test_lacked_fields_are_dropped_and_each_request_runs_once(hass, fake, reset_script):
    add_fake_entity(hass, fake, "light.full", attributes=FULL)
    add_fake_entity(hass, fake, "light.full_2", attributes=FULL)
    add_fake_entity(hass, fake, "light.onoff", attributes={"supported_color_modes": [ColorMode.ONOFF]} | NO_FEATURES)
    add_fake_entity(hass, fake, "light.no_fade", attributes=FULL | NO_FEATURES)
    add_fake_entity(hass, fake, "switch.s")
    entity_ids = ["light.full", "light.full_2", "light.no_fade", "light.onoff", "switch.s"]

    data = {"entity_id": entity_ids, "duration": 5, "brightness": 9, "transition": 1}

    response = await call(hass, "turn_on_for", data)

    dropped = {"status": "sent", "integration": "fake", "detail": "fields_dropped"}
    assert response == {
        "light.full": {"status": "sent", "integration": "fake"},
        "light.full_2": {"status": "sent", "integration": "fake"},
        "light.no_fade": dropped,
        "light.onoff": dropped,
        "switch.s": dropped,  # a switch lacks every light field, and the timer still runs
    }
    sent = sorted((tuple(sorted(request.fields_present())), ids) for _, _, request, ids in reset_script.calls)
    assert sent == [
        ((), ["light.onoff", "switch.s"]),
        (("brightness",), ["light.no_fade"]),
        (("brightness", "transition"), ["light.full", "light.full_2"]),
    ]


async def test_the_adapter_detail_wins_over_fields_dropped(hass, fake, reset_script):
    add_fake_entity(hass, fake, "switch.s")

    async def execute(self, hass, request, targets):
        return [self.result(targets[0], Status.SENT, detail="readback_failed")]

    with patch.object(FakeTurnOnFor, "execute", execute):
        response = await call(hass, "turn_on_for", {"entity_id": "switch.s", "duration": 5, "brightness": 9})

    assert response["switch.s"]["detail"] == "readback_failed"


async def test_scope_drops_are_dropped_before_the_scope_check(hass, fake, reset_script):
    add_fake_entity(hass, fake, "light.a", attributes=FULL)
    scope = AdapterScope(domains=verified("light"), duration=LIMITS, drops=frozenset({"rgb_color"}))

    with patch.object(FakeTurnOnFor, "scope", scope):
        response = await call(hass, "turn_on_for", {"entity_id": "light.a", "duration": 5, "rgb_color": [255, 0, 0]})

    assert response == {"light.a": {"status": "sent", "integration": "fake", "detail": "fields_dropped"}}
    assert reset_script.calls[0][2].fields_present() == frozenset()


async def test_an_unlisted_field_reaches_the_adapter_on_a_light_whose_state_lacks_it(hass, fake, reset_script):
    # A brightness-only light, as core shows a light whose integration leaves out its color temperature.
    add_fake_entity(hass, fake, "light.dim", attributes={"supported_color_modes": [ColorMode.BRIGHTNESS]} | NO_FEATURES)
    scope = AdapterScope(
        duration=LIMITS,
        fields=verified("brightness", "transition") | experimental("color_temp_kelvin"),
        unlisted=frozenset({"color_temp_kelvin"}),
    )
    data = {"entity_id": "light.dim", "duration": 5, "color_temp_kelvin": 3000, "transition": 1}

    with patch.object(FakeTurnOnFor, "scope", scope):
        allow_experimental(hass, False)
        assert await refusal(hass, "turn_on_for", data) == "experimental_off"
        assert reset_script.calls == []
        allow_experimental(hass, True)
        response = await call(hass, "turn_on_for", data)

    # transition is still dropped as lacked: only the unlisted field passes.
    assert response == {"light.dim": {"status": "sent", "integration": "fake", "detail": "fields_dropped"}}
    assert reset_script.calls[0][2].fields_present() == {"color_temp_kelvin"}


async def test_wrapper_reports_under_the_light_and_drops_light_fields(hass, fake, reset_script):
    add_fake_entity(hass, fake, "switch.relay")
    add_switch_as_x(hass, "light.relay", "switch.relay")
    reset_script.outcomes["switch.relay"] = Status.CONFIRMED

    response = await call(hass, "turn_on_for", {"entity_id": "light.relay", "duration": 5})

    assert response == {"light.relay": {"status": "confirmed", "integration": "fake"}}
    assert reset_script.calls[0][3] == ["switch.relay"]

    reset_script.calls.clear()
    response = await call(hass, "turn_on_for", {"entity_id": "light.relay", "duration": 5, "brightness": 50})
    assert response == {"light.relay": {"status": "confirmed", "integration": "fake", "detail": "fields_dropped"}}
    assert reset_script.calls[0][2].fields_present() == frozenset()


async def test_wrapper_and_its_switch_each_get_a_result(hass, fake, reset_script):
    add_fake_entity(hass, fake, "switch.relay")
    add_switch_as_x(hass, "light.relay", "switch.relay")

    response = await call(hass, "turn_on_for", {"entity_id": ["light.relay", "switch.relay"], "duration": 5})

    assert response == {
        "light.relay": {"status": "sent", "integration": "fake"},
        "switch.relay": {"status": "sent", "integration": "fake"},
    }
    assert reset_script.calls[0][3] == ["switch.relay", "switch.relay"]  # the same Set twice: idempotent


async def test_a_request_left_nothing_to_do_is_unchanged_and_sends_nothing(hass, fake, reset_script):
    add_fake_entity(hass, fake, "light.onoff", attributes={"supported_color_modes": [ColorMode.ONOFF]})
    add_fake_entity(hass, fake, "light.dim", attributes={"supported_color_modes": [ColorMode.BRIGHTNESS]})

    response = await call(hass, "set_attributes", {"entity_id": ["light.onoff", "light.dim"], "brightness": 50})

    assert response == {
        "light.onoff": {"status": "unchanged", "integration": "fake", "detail": "fields_dropped"},
        "light.dim": {"status": "sent", "integration": "fake"},
    }
    assert reset_script.calls[0][3] == ["light.dim"]


async def test_adapter_exception_and_timeout_reject_every_target(hass, fake, reset_script):
    add_fake_entity(hass, fake, "light.a")
    add_fake_entity(hass, fake, "switch.b")
    data = {"entity_id": ["light.a", "switch.b"], "duration": 5}
    reset_script.execute_error = RuntimeError("bridge gone")

    with pytest.raises(HomeAssistantError) as err:
        await call(hass, "turn_on_for", data)
    assert err.value.translation_placeholders == {"summary": "light.a: error, switch.b: error"}

    reset_script.execute_error = None
    reset_script.delay = 1
    with patch(f"{DISPATCHER}.ADAPTER_TIMEOUT_S", 0.01), pytest.raises(HomeAssistantError) as err:
        await call(hass, "turn_on_for", data)
    assert err.value.translation_placeholders == {"summary": "light.a: timeout, switch.b: timeout"}


async def test_a_timeout_keeps_the_results_of_the_targets_that_finished(hass, fake, reset_script):
    add_fake_entity(hass, fake, "light.fast")
    add_fake_entity(hass, fake, "light.slow")
    reset_script.delays = {"light.slow": 1}

    with patch(f"{DISPATCHER}.ADAPTER_TIMEOUT_S", 0.05), pytest.raises(HomeAssistantError) as err:
        await call(hass, "turn_on_for", {"entity_id": ["light.fast", "light.slow"], "duration": 5})

    # light.fast was sent and isn't reported failed: only the light still going when time ran out is.
    assert err.value.translation_placeholders == {"summary": "light.slow: timeout"}


async def test_an_execute_busy_outside_gather_past_the_backstop_times_out_every_target(hass, fake):
    add_fake_entity(hass, fake, "light.a")
    add_fake_entity(hass, fake, "light.b")

    async def execute(self, hass, request, targets):
        await asyncio.sleep(1)

    with (
        patch("tests.fakes.fake_integration.FakeTurnOnFor.execute", execute),
        patch(f"{DISPATCHER}.ADAPTER_TIMEOUT_S", 0.01),
        patch(f"{DISPATCHER}.ADAPTER_BACKSTOP_S", 0.01),
        pytest.raises(HomeAssistantError) as err,
    ):
        await call(hass, "turn_on_for", {"entity_id": ["light.a", "light.b"], "duration": 5})

    assert err.value.translation_placeholders == {"summary": "light.a: timeout, light.b: timeout"}


async def test_missing_stray_and_untargeted_results_are_reconciled(hass, fake):
    add_fake_entity(hass, fake, "light.a")
    add_fake_entity(hass, fake, "light.b")
    add_fake_entity(hass, fake, "switch.a")
    add_fake_entity(hass, fake, "switch.b")
    add_fake_native_group(hass, fake, "switch.fake_group", ["switch.a", "switch.b"])

    async def execute_lights(self, hass, request, targets):
        return [self.result(targets[0], Status.SENT), self.result(targets[0], Status.REJECTED)] + [
            self.result(targets[0], Status.SENT).__class__("light.elsewhere", Status.SENT)
        ]

    with (
        patch("tests.fakes.fake_integration.FakeTurnOnFor.execute", execute_lights),
        pytest.raises(HomeAssistantError) as err,
    ):
        await call(hass, "turn_on_for", {"entity_id": ["light.a", "light.b"], "duration": 5})

    assert err.value.translation_placeholders == {"summary": "light.b: no_result"}

    async def execute_switches(self, hass, request, targets):
        by_id = {target.entity_id: target for target in targets}
        return [
            self.result(by_id["switch.fake_group"], Status.CONFIRMED),
            self.result(by_id["switch.a"], Status.SENT),
            # A second row for a target, and a row for a member nobody targeted, are both stray.
            EntityResult("switch.a", Status.CONFIRMED),
            EntityResult("switch.b", Status.CONFIRMED),
        ]

    with patch("tests.fakes.fake_integration.FakeTurnOffFor.execute", execute_switches):
        response = await call(hass, "turn_off_for", {"entity_id": ["switch.fake_group", "switch.a"], "duration": 500})

    assert response == {
        "switch.a": {"status": "sent", "integration": "fake", "clamped_to": 100},
        "switch.fake_group": {"status": "confirmed", "integration": "fake", "clamped_to": 100},
    }
    assert list(response) == ["switch.a", "switch.fake_group"]


async def test_integration_semaphore_bounds_concurrency(hass, fake, reset_script):
    entity_ids = [add_fake_entity(hass, fake, f"light.l{n}") for n in range(6)]
    reset_script.delay = 0.01

    await call(hass, "turn_on_for", {"entity_id": entity_ids, "duration": 5})

    assert reset_script.max_active == 2  # IntegrationInfo.max_concurrency, below the adapter's 4


async def test_a_call_waiting_on_its_devices_hold_holds_no_slot(hass, fake, reset_script):
    """At the cap of 2, a second call to a locked device waits outside the slots, so another device's call runs."""
    entity_ids = [add_fake_entity(hass, fake, f"light.{name}") for name in ("a1", "a2", "b")]
    reset_script.holds = dict.fromkeys(["light.a1", "light.a2"], asyncio.Lock())
    reset_script.delay = 0.02

    await call(hass, "turn_on_for", {"entity_id": entity_ids, "duration": 5})

    assert reset_script.started == ["light.a1", "light.b", "light.a2"]


async def test_a_hold_not_taken_by_the_deadline_is_a_timeout(hass, fake, reset_script):
    """Waiting on the device counts against the run's deadline, as waiting on a slot does."""
    add_fake_entity(hass, fake, "light.a1")
    add_fake_entity(hass, fake, "light.b")
    held = asyncio.Lock()
    reset_script.holds = {"light.a1": held}

    async with held:
        with patch(f"{DISPATCHER}.ADAPTER_TIMEOUT_S", 0.05), pytest.raises(HomeAssistantError) as err:
            await call(hass, "turn_on_for", {"entity_id": ["light.a1", "light.b"], "duration": 5})

    assert err.value.translation_placeholders == {"summary": "light.a1: timeout"}


async def test_a_send_that_raises_releases_its_hold(hass, fake, reset_script):
    add_fake_entity(hass, fake, "light.a1")
    add_fake_entity(hass, fake, "light.a2")
    reset_script.holds = dict.fromkeys(["light.a1", "light.a2"], asyncio.Lock())
    reset_script.outcomes = {"light.a1": RuntimeError("device gone")}

    with pytest.raises(HomeAssistantError) as err:
        await call(hass, "turn_on_for", {"entity_id": ["light.a1", "light.a2"], "duration": 5})

    assert err.value.translation_placeholders == {"summary": "light.a1: error"}
    assert reset_script.started == ["light.a1", "light.a2"]


async def test_adapters_on_different_integrations_run_concurrently(hass, fake, reset_script):
    add_fake_entity(hass, fake, "light.a")
    add_fake_entity(hass, fake, "light.lamp", identifier="special_1")
    reset_script.delay = 0.05

    async with asyncio.timeout(0.09):
        await call(hass, "turn_on_for", {"entity_id": ["light.a", "light.lamp"], "duration": 5})

    assert reset_script.max_active == 2


async def test_calls_refused_until_entry_loads(hass, setup_integration, fake):
    add_fake_entity(hass, fake, "light.a")
    await hass.config_entries.async_unload(setup_integration.entry_id)

    with pytest.raises(ServiceValidationError) as err:
        await call(hass, "turn_on_for", {"entity_id": "light.a", "duration": 5})
    assert err.value.translation_key == "not_loaded"


FULL = {
    "supported_color_modes": [ColorMode.COLOR_TEMP, ColorMode.HS],
    "supported_features": LightEntityFeature.TRANSITION,
}

NO_FEATURES = {"supported_features": 0}


def test_timed_request_is_what_adapters_see():
    assert TimedRequest(duration_s=1).fields_present() == frozenset()


def test_class_unknown_names_the_integration_by_its_title_or_gets_the_plain_refusal():
    """Its message names the integration; with no title it never names an untranslated stand-in."""
    named = TargetNotSupported("light.a", "class_unknown", integration="Zigbee2MQTT")
    assert named.translation_key == "class_unknown"
    assert named.translation_placeholders["integration"] == "Zigbee2MQTT"

    untitled = TargetNotSupported("light.a", "class_unknown")
    assert untitled.translation_key == "target_not_supported"
    assert untitled.translation_placeholders == {"entity_id": "light.a", "reason": "class_unknown"}
