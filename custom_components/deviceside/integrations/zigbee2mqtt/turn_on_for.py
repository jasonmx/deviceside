"""Zigbee2MQTT turn_on_for: optional light.turn_on, a publish with on_time, then state read-backs."""

import asyncio
import logging
from typing import ClassVar

from homeassistant.core import HomeAssistant

from .addressing import not_addressable, topic
from .bridge import logged_options, publish, publish_set, state_reply
from .classes import TURN_ON_FOR, TURN_ON_FOR_DOESNT_WORK, unplaced
from .members import expand_group
from .native_control import COLOR_FIELDS, NEED, native_control, settled
from .set_check import checked, note
from ...actions.turn_on_for import TurnOnForRequest
from ...core.adapter import Adapter
from ...core.errors import Reason
from ...core.models import EntityResult, Status, Target
from ...core.runtime import get_runtime
from ...core.scope import AdapterScope, DurationLimits, experimental, verified

_LOGGER = logging.getLogger(__name__)

# Zigbee OnWithTimedOff: on_time in tenths of a second (uint16), range 1-65534 tenths.
# 65534 tenths = 6553.4 s. The 50 ms floor from core/duration.py handles the effective minimum.
_TIMER_LIMITS = DurationLimits(min_s=0, max_s=6553.4)

# Read-back delays in seconds. The second end read allows for a bulb clock that runs slow on a long timer.
_ON_READ_S = 1.0
_END_READ_S = 1.0
_LATE_END_READ_MIN_S = 5.0
_LATE_END_READ_FRACTION = 0.02
# Z2M publishes a light's state once the command has reached it: under a second, or seconds on a busy mesh.
_STATE_TIMEOUT_S = 5.0


class Z2MTurnOnFor(Adapter[TurnOnForRequest]):
    """Turn a Z2M light on for a duration using Zigbee2MQTT's on_time attribute.

    With fields: calls light.turn_on first so HA's MQTT light entity applies brightness, color and
    transition through its own protocol, waits for Z2M to publish the light's state, then publishes
    {"state":"ON","on_time":<seconds, 1dp>} to the device's command topic. Without fields: publishes only.
    With a color or color temperature, a Hue light (or a group with a Hue member) gets Z2M's hue_native_control on
    for that light.turn_on (native_control.py), so it lights in that color rather than its last one.

    Z2M publishes no state for a timed command, and Hue bulbs on Z2M don't report, so HA's state would go
    stale. The adapter then asks Z2M to read the state back ({"state":""} on the get topic): 1 s after the
    publish when no fields ran light.turn_on, and twice after the timer should end. A new call on the same
    target replaces its pending reads.

    Returns SENT (fire-and-forget via MQTT), or REJECTED when Zigbee2MQTT logs a set as failed (set_check.py). The
    reads never change the result.
    """

    action: ClassVar[str] = "turn_on_for"
    integration: ClassVar[str] = "zigbee2mqtt"
    scope: ClassVar[AdapterScope] = AdapterScope(
        # Zigbee plugs and switches rarely honor OnWithTimedOff, and Z2M has no read-back to show it, so switch fails
        # the Documented test (docs/evidence.md, Levels): it takes the same call, so it stays flagged experimental,
        # untested, until a maintainer judges it ready.
        domains=verified("light") | experimental("switch"),
        capabilities=TURN_ON_FOR,
        doesnt_work=TURN_ON_FOR_DOESNT_WORK,
        duration=_TIMER_LIMITS,
        fields=verified("brightness", "color_temp_kelvin", "hs_color", "rgb_color", "xy_color", "transition"),
        confirmable=False,
        native_groups=True,  # Z2M group entities receive commands on <base>/<group ID>/set.
    )

    def supports(self, hass: HomeAssistant, request: TurnOnForRequest, target: Target) -> str | None:
        return super().supports(hass, request, target) or not_addressable(target) or _unknown(hass, target)

    async def execute(
        self, hass: HomeAssistant, request: TurnOnForRequest, targets: list[Target]
    ) -> list[EntityResult]:
        async def send(target: Target) -> EntityResult:
            if request.light.present():
                need = NEED[self.action] if request.light.present() & COLOR_FIELDS else None
                async with native_control(hass, target, need):
                    await self._turn_on(hass, target, request.light.as_turn_on_data())

            # The timer goes out as the user's option has it, after this call's restore or an earlier action's.
            await settled(hass, target)

            on_time = round(request.duration_s, 1)
            # supports() refused the targets it can't address.
            await publish_set(hass, target, {"state": "ON", "on_time": on_time})
            self._schedule_reads(hass, target, on_time, read_on=not request.light.present())
            return self.result(target, Status.SENT)

        return await checked(self, hass, targets, send)

    async def _turn_on(self, hass: HomeAssistant, target: Target, data: dict) -> None:
        """light.turn_on, returning once Z2M publishes the light's state, so the timer follows the fields.

        HA returns once it has published the command, and Z2M handles two messages for one light at once. The
        timer's message would then publish the state from before the fields landed, which HA keeps, and contend
        with them on the mesh, starting the timer late. With no state by the timeout, the timer goes anyway.
        """
        service_data = {"entity_id": target.entity_id, **data}
        published, unsubscribe = await state_reply(hass, target)
        try:
            if _LOGGER.isEnabledFor(logging.DEBUG):
                _LOGGER.debug(
                    "%s: sending ha light.turn_on %s with %s", target.reported_id, data, logged_options(hass, target)
                )
            # HA publishes it as a set, whose failure Z2M logs as any other's.
            await note(hass, target)
            await hass.services.async_call("light", "turn_on", service_data, blocking=True)
            if published is None:
                return
            try:
                async with asyncio.timeout(_STATE_TIMEOUT_S):
                    await published
            except TimeoutError:
                _LOGGER.warning(
                    "No state from Zigbee2MQTT for %s in %s s, "
                    "so its timer starts late and it stays on that much longer",
                    target.entity_id,
                    _STATE_TIMEOUT_S,
                )
        finally:
            unsubscribe()

    def _schedule_reads(self, hass: HomeAssistant, target: Target, on_time: float, read_on: bool) -> None:
        get_topic = topic(target, "get")

        async def read() -> None:
            try:
                # A failure lands here rather than in HA's error log; the read runs in its own task.
                await publish(hass, get_topic, {"state": ""})
            except Exception as err:  # noqa: BLE001 - a failed read leaves HA's state stale, nothing more
                _LOGGER.debug("State read for %s failed: %r", target.entity_id, err)

        delays = [on_time + _END_READ_S, on_time + max(_LATE_END_READ_MIN_S, _LATE_END_READ_FRACTION * on_time)]
        if read_on:
            delays.insert(0, _ON_READ_S)
        get_runtime(hass).schedule(
            hass, f"{self.integration}:{self.action}:{target.entity_id}", ((delay, read) for delay in delays)
        )


def _unknown(hass: HomeAssistant, target: Target) -> str | None:
    """`class_unknown` for a target whose class rests on what DeviceSide doesn't know yet, even with Experimental
    features on (docs/evidence.md, Doesn't work, not yet supported, untested: where a call is known to harm, a class
    not yet known is refused); else None.

    The failure it prevents: a Tuya plug left on. Today's call turns the Tuya classes on with no timer
    (TURN_ON_FOR_DOESNT_WORK), so one that reached it in another class, or inside a group run as one, would stay on.
    That is a device bridge/devices doesn't yet list as interviewed with both Zigbee strings, unless the registry puts
    it in `hue` (classes.unplaced: a rebadged Tuya plug looks like another brand until then), and a group whose
    membership is unknown or that has such a member. Each runs once the bridge cache fills, as Zigbee2MQTT publishes
    right after a connect; with no cache (its subscribe failed, logged), they stay refused. A device placed in `other`
    from its strings stays Experimental.
    """
    if not target.is_group:
        return Reason.CLASS_UNKNOWN if unplaced(hass, target) else None
    members = expand_group(hass, target)
    if members is None or any(unplaced(hass, member) for member in members):
        return Reason.CLASS_UNKNOWN
    return None
