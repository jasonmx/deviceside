"""Registry building: guarded integration import, adapter validation, matrix iteration."""

import importlib
import logging
from dataclasses import replace
from itertools import product
from types import SimpleNamespace

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.deviceside import actions
from custom_components.deviceside.core.models import Target
from custom_components.deviceside.core.registry import AdapterRegistry
from custom_components.deviceside.core.scope import DoesntWork, Level, experimental, verified

from ..conftest import FAKE_INTEGRATIONS
from ..fakes.fake_integration import ADAPTERS, INTEGRATION, FakeTurnOffFor, FakeTurnOnFor


def _registry(adapters=ADAPTERS, info=INTEGRATION) -> AdapterRegistry:
    """A registry of the fake integration alone, with these adapters and this info."""
    return AdapterRegistry(actions.discover(), [SimpleNamespace(INTEGRATION=info, ADAPTERS=adapters)])


def test_broken_integration_is_recorded_unavailable_and_others_load():
    registry = AdapterRegistry(actions.discover(), FAKE_INTEGRATIONS)

    broken = registry.integrations["broken_integration"]
    assert not broken.available
    assert "fakelib" in broken.unavailable_reason
    assert [info.key for info in registry.available_integrations()] == ["fake", "fake_special"]
    assert registry.adapter("turn_on_for", "fake").integration == "fake"


@pytest.mark.parametrize(
    ("missing", "level"),
    [("fakelib", logging.DEBUG), ("homeassistant.components.fake.gone", logging.WARNING), (None, logging.DEBUG)],
)
def test_import_failure_warns_only_when_our_code_is_out_of_step(monkeypatch, caplog, missing, level):
    def fail(module):
        raise ImportError(f"no {missing}", name=missing)

    discovered = actions.discover()
    monkeypatch.setattr(importlib, "import_module", fail)
    with caplog.at_level(logging.DEBUG, logger="custom_components.deviceside.core.registry"):
        registry = AdapterRegistry(discovered, ["custom_components.deviceside.integrations.fake"])

    assert not registry.integrations["fake"].available
    assert [record.levelno for record in caplog.records] == [level]


def test_matrix_lists_every_pair_sorted():
    discovered = actions.discover()
    registry = AdapterRegistry(discovered, FAKE_INTEGRATIONS)
    matrix = list(registry.matrix())

    keys = sorted(action.key for action in discovered)
    integrations = ["broken_integration", "fake", "fake_special"]
    assert [(action, integration) for action, integration, _ in matrix] == list(product(keys, integrations))
    assert [(action, integration) for action, integration, adapter in matrix if adapter is not None] == [
        ("set_attributes", "fake"),
        ("turn_off_for", "fake"),
        ("turn_on_for", "fake"),
        ("turn_on_for", "fake_special"),
    ]


def test_adapter_for_unknown_action_is_refused():
    class Stray(FakeTurnOnFor):
        action = "no_such_action"

    with pytest.raises(ValueError, match="unknown action"):
        _registry([Stray])


CLASSED = replace(INTEGRATION, capability=lambda hass, target: "hue", classes=frozenset({"hue", "tuya", "other"}))
BROKEN = DoesntWork("Tuya plug", "It never switches on", "https://example.com/source")


class Classed(FakeTurnOnFor):
    scope = replace(FakeTurnOnFor.scope, capabilities=verified("hue") | experimental("other"))


def test_an_integration_naming_classes_needs_them_in_every_scope():
    _registry([Classed], CLASSED)
    with pytest.raises(ValueError, match="names classes, but its scope holds none"):
        _registry([Classed, FakeTurnOffFor], CLASSED)


@pytest.mark.parametrize(
    "scope",
    [
        pytest.param(replace(FakeTurnOnFor.scope, capabilities=verified("hue")), id="capabilities"),
        pytest.param(replace(FakeTurnOnFor.scope, doesnt_work={"tuya": BROKEN}), id="doesnt_work"),
    ],
)
def test_an_integration_naming_no_classes_needs_them_in_no_scope(scope):
    class Stray(FakeTurnOnFor):
        pass

    Stray.scope = scope
    with pytest.raises(ValueError, match="holds classes, but fake names none"):
        _registry([Stray])


def test_a_scope_holds_only_classes_its_integration_declares():
    class Typo(FakeTurnOnFor):
        scope = replace(Classed.scope, doesnt_work={"tuya_zcl": BROKEN})

    with pytest.raises(ValueError, match=r"doesn't declare \(IntegrationInfo.classes\): \['tuya_zcl'\]"):
        _registry([Typo], CLASSED)


def test_the_default_class_is_never_verified():
    """docs/evidence.md, Device classes: it holds every device not yet placed, so no record proves it."""
    defaulted = replace(CLASSED, default_class="other")
    _registry([Classed], defaulted)

    class Lifted(FakeTurnOnFor):
        scope = replace(Classed.scope, capabilities=verified("hue", "other"))

    with pytest.raises(ValueError, match="holds fake's default class 'other' as Verified"):
        _registry([Lifted], defaulted)
    _registry([Lifted], CLASSED)  # with no default class, any class may be Verified


def test_the_default_class_is_a_declared_one():
    with pytest.raises(ValueError, match="default_class 'ikea' is none of its classes"):
        _registry([Classed], replace(CLASSED, default_class="ikea"))


def test_shelly_names_no_default_class_and_zigbee2mqtt_holds_its_below_verified():
    registry = AdapterRegistry.discover()
    assert registry.integrations["shelly"].default_class is None
    z2m = [adapter for _, key, adapter in registry.matrix() if key == "zigbee2mqtt" and adapter is not None]
    assert z2m
    assert all(adapter.scope.capabilities["other"] < Level.VERIFIED for adapter in z2m)


@pytest.mark.parametrize(
    "info",
    [replace(INTEGRATION, classes=frozenset({"hue"})), replace(CLASSED, classes=frozenset())],
    ids=["classes_alone", "capability_alone"],
)
def test_capability_and_classes_come_together(info):
    with pytest.raises(ValueError, match="capability and classes come together"):
        _registry([FakeTurnOnFor], info)


async def test_every_capability_classes_a_stand_in_target_with_no_hass(hass):
    """The rig classes a stand-in Target (hass None, no config entry or state) until it reads the class from each
    result (docs/plans/2026-11-14-device-classes.md, step 4): a capability() that read either would break its runs."""
    classed = [info for info in AdapterRegistry.discover().available_integrations() if info.capability is not None]
    found = {}
    for info in classed:
        config = MockConfigEntry(domain=info.ha_domain)
        config.add_to_hass(hass)
        device = dr.async_get(hass).async_get_or_create(
            config_entry_id=config.entry_id, identifiers={(info.ha_domain, info.key)}, manufacturer="Philips"
        )
        entry = er.async_get(hass).async_get_or_create("light", info.ha_domain, "aabbccddeeff-rgbcct:0")
        found[info.key] = info.capability(None, Target(entry.entity_id, "light", info.key, entry, device, None, None))
    assert found == {"shelly": "RGBCCT", "zigbee2mqtt": "hue"}


@pytest.mark.parametrize(
    ("maintainers", "error"),
    [
        ((), "non-empty tuple"),
        (["jasonmx"], "non-empty tuple"),
        (("@jasonmx",), "not GitHub handles"),
        (("jason mx",), "not GitHub handles"),
        (("-jasonmx",), "not GitHub handles"),
        (("a" * 40,), "not GitHub handles"),
        (("jasonmx", "jasonmx"), "twice"),
    ],
)
def test_an_integration_must_name_its_maintainers_by_handle(maintainers, error):
    with pytest.raises(ValueError, match=error):
        _registry(info=replace(INTEGRATION, maintainers=maintainers))


def test_several_maintainers_are_accepted():
    registry = _registry(info=replace(INTEGRATION, maintainers=("jasonmx", "a-b", "a" * 39)))
    assert registry.integrations["fake"].maintainers == ("jasonmx", "a-b", "a" * 39)


def test_production_discovery_imports_cleanly():
    registry = AdapterRegistry.discover()
    assert set(registry.actions) == {
        "clear_auto_off",
        "clear_auto_on",
        "get_attributes",
        "lower_brightness",
        "lower_hue",
        "lower_saturation",
        "raise_brightness",
        "raise_hue",
        "raise_saturation",
        "set_attributes",
        "set_auto_off",
        "set_auto_on",
        "set_scene",
        "stop",
        "turn_off_for",
        "turn_on_for",
    }
    assert registry.integrations["shelly"].available
    assert registry.integrations["hue"].available
    assert registry.adapter("turn_on_for", "shelly") is not None
    assert registry.adapter("turn_off_for", "shelly") is not None
    assert all(info.maintainers for info in registry.available_integrations())


def test_call_resolvers_are_keyed_by_ha_domain():
    registry = AdapterRegistry.discover()

    assert set(registry.call_resolvers()) == {"hue", "zha", "mqtt"}


def test_two_call_resolvers_for_one_ha_domain_are_refused():
    registry = AdapterRegistry(actions.discover(), FAKE_INTEGRATIONS)
    for info in registry.available_integrations():
        registry.integrations[info.key] = replace(info, ha_domain="shared", call_resolver=object())

    with pytest.raises(ValueError, match="ha_domain shared"):
        registry.call_resolvers()


def test_an_import_that_raises_anything_marks_the_integration_unavailable_and_logs_it(monkeypatch, caplog):
    def fail(module):
        raise AttributeError("module 'homeassistant.components.fake' has no attribute 'gone'")

    discovered = actions.discover()
    monkeypatch.setattr(importlib, "import_module", fail)
    with caplog.at_level(logging.DEBUG, logger="custom_components.deviceside.core.registry"):
        registry = AdapterRegistry(discovered, ["custom_components.deviceside.integrations.fake"])

    info = registry.integrations["fake"]
    assert not info.available
    assert "AttributeError" in info.unavailable_reason
    assert [record.levelno for record in caplog.records] == [logging.WARNING]
    assert caplog.records[0].exc_info is not None
