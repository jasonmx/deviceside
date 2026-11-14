from enum import StrEnum

from homeassistant.exceptions import ServiceNotSupported, ServiceValidationError

from ..const import DOMAIN

# The call touches an Experimental scope item and the Experimental features option is off.
EXPERIMENTAL_OFF = "experimental_off"


class Reason(StrEnum):
    """Reason keys that core and adapters both return or test for, defined once: the dispatcher's skip-vs-raise
    turns on the first two (docs/action-behavior.md), so a copy that drifted would change it. The class refusals
    (docs/evidence.md, Device classes) are defined here too, raised if named and skipped if not, as any other."""

    # The config entry behind the target isn't loaded: unavailable, as core treats an entity whose integration isn't up.
    INTEGRATION_NOT_LOADED = "integration_not_loaded"
    # A group with no members holds nothing to act on, which is not a reason it can't: it is skipped, never raised.
    EMPTY_GROUP = "empty_group"
    # The integration can't address the target (no device address, a nested wrapper): raised if named, else skipped.
    NOT_ADDRESSABLE = "not_addressable"
    # The target's class (a native group: a member's) fails the adapter's call: AdapterScope.doesnt_work.
    DOESNT_WORK = "doesnt_work"
    # The target's class (a native group: a member's) needs a call that isn't built: the integration names it, and the
    # adapter's scope holds it in neither capabilities nor doesnt_work.
    NOT_SUPPORTED = "not_supported"
    # The target's class rests on what DeviceSide doesn't know yet (a device's identifying strings, a native group's
    # membership), on an adapter whose call harms a class it could be: refused even with Experimental features on
    # (docs/evidence.md, Device classes). Transient: it clears once the integration has reported the device.
    CLASS_UNKNOWN = "class_unknown"


class TargetNotSupported(ServiceNotSupported):
    """A target the caller named can't take the action: core's ServiceNotSupported, with our reason in it. A target
    refused for experimental_off gets a message of its own, saying how to let it through, and one whose class doesn't
    work a message carrying why (`why`, the scope's DoesntWork.reason). One refused as class_unknown gets one saying
    it clears once the integration (`integration`, its title, which the dispatcher always passes) has reported the
    device; without the title it gets the plain refusal, never an untranslated name."""

    def __init__(self, entity_id: str, reason: str, why: str | None = None, integration: str | None = None) -> None:
        placeholders = {"entity_id": entity_id, "reason": reason}
        if reason == EXPERIMENTAL_OFF:
            key = EXPERIMENTAL_OFF
        elif reason == Reason.CLASS_UNKNOWN and integration is not None:
            key, placeholders["integration"] = Reason.CLASS_UNKNOWN.value, integration
        elif why is not None:
            key, placeholders["why"] = Reason.DOESNT_WORK.value, why
        else:
            key = "target_not_supported"
        # Skips ServiceNotSupported.__init__, which would set core's own translation key.
        ServiceValidationError.__init__(
            self, translation_domain=DOMAIN, translation_key=key, translation_placeholders=placeholders
        )
        self.reason = reason
