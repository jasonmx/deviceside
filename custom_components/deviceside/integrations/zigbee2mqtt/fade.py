"""Zigbee2MQTT raise and lower: brightness and saturation published as an end point with a transition, hue as a rate
move. Each action's adapter is a Z2MRamp."""

import logging
from collections.abc import Coroutine
from dataclasses import dataclass
from statistics import mean
from typing import Any, ClassVar

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .addressing import not_addressable, topic, z2m_address
from .bridge import has_feature, publish, publish_set
from .classes import FADE_BRIGHTNESS, FADE_COLOR
from .color_refresh import refresh_color
from .members import member_lights
from .native_control import NEED, native_control
from .set_check import checked
from ...actions.ramp import RampRequest
from ...core import transition
from ...core.adapter import Adapter
from ...core.errors import Reason
from ...core.ha_watch import ALL, Call, Entries
from ...core.models import EntityResult, Status, Target
from ...core.resolver import resolve
from ...core.runtime import Job, get_runtime
from ...core.scope import AdapterScope, verified
from ...core.transition import BRIGHTNESS, SATURATION, TransitionCache, hs_color, light_brightness, lit

_LOGGER = logging.getLogger(__name__)

# Z2M reads a light back within 0.7 s of a stop, and once a fade has run its published end value is right.
SETTLE_SECONDS = 2.0
# The smallest brightness or saturation step a bulb makes (1/254), in percent.
MIN_STEP = 100 / 254
# A hue move's rate is in steps of 1/254 of the cycle per second, 1 to 254, so a cycle takes 1 to 254 s.
MIN_CYCLE_S, MAX_CYCLE_S = 1.0, 254.0
# The end read-back, after the fade has run: a live read mid-fade may have overwritten Z2M's published end value.
END_READ_S = 1.0
COLOR_HS = "color_hs"  # the Z2M feature a hue or saturation move needs
# An off light keeps its level, and a fade from off starts there: it is turned on at the bottom first.
AT_THE_BOTTOM = {"state": "ON", "brightness": 1, "transition": 0}


def transition_cache(hass: HomeAssistant) -> TransitionCache:
    return transition.transition_cache(hass, "zigbee2mqtt", lambda: TransitionCache(SETTLE_SECONDS))


def hue_moves(hass: HomeAssistant) -> set[str]:
    """The lights a hue move of ours is cycling: it runs until a stop, so it has no transition to predict."""
    return get_runtime(hass).integration_state.setdefault("zigbee2mqtt_hue_moves", set())


class Z2MCallResolver:
    """Forget the Z2M lights a light call from HA targets: the bulb then goes where the call sends it, and its next move
    or stop starts from HA's state. A call that sets a color or turns the light off ends the bulb's hue cycle. The end
    read-back stays: it reads the bulb.

    A group's members come only from Z2M's retained bridge/groups message, so a call to a group resolves in a task;
    the dispatcher waits for it (ha_watch.settled) before a move plans.
    """

    def busy(self, hass: HomeAssistant) -> bool:
        return bool(transition_cache(hass).resource_ids() or hue_moves(hass))

    def forget(self, hass: HomeAssistant, call: Call, entries: Entries) -> Coroutine[Any, Any, None] | None:
        if entries is ALL:
            transition_cache(hass).clear()
            if call.ends_hue_cycle:
                hue_moves(hass).clear()
            return None
        targets = _z2m_lights(hass, entries)
        _drop(hass, call, [target.entity_id for target in targets])
        if not (groups := [target for target in targets if target.is_group]):
            return None

        async def members() -> None:
            for group in groups:
                _drop(hass, call, [light.entity_id for light in await member_lights(hass, group)])

        return members()


def _drop(hass: HomeAssistant, call: Call, light_ids: list[str]) -> None:
    cache, cycling = transition_cache(hass), hue_moves(hass)
    for light_id in light_ids:
        cache.forget(light_id)
        if call.ends_hue_cycle:
            cycling.discard(light_id)


def _z2m_lights(hass: HomeAssistant, entries: list[er.RegistryEntry]) -> list[Target]:
    """The Z2M light and group targets among the entries, which may hold any MQTT entity."""
    registry = get_runtime(hass).registry
    targets = [resolve(hass, registry, entry.entity_id) for entry in entries if entry.domain == "light"]
    return [t for t in targets if not isinstance(t, str) and t.integration == "zigbee2mqtt"]


def read_back_key(target: Target, attribute: str) -> str:
    """The key an attribute's end read-back is scheduled under, which the attribute's next move, or a stop, replaces."""
    return f"zigbee2mqtt:ramp:{target.entity_id}:{attribute}"


async def lights_of(hass: HomeAssistant, target: Target) -> list[Target]:
    return await member_lights(hass, target) if target.is_group else [target]


@dataclass(frozen=True, slots=True)
class Fade:
    """One attribute's fade: the payload published, each moving light's start and end, and how long it runs; first, any
    publishes that put a light at the fade's start."""

    attribute: str
    payload: dict
    starts: dict[str, float]
    ends: dict[str, float]
    duration: float
    before: tuple[tuple[Target, dict], ...] = ()


def ramp_scope(attribute: str) -> AdapterScope:
    """A ramp's scope: a brightness fade's classes are held as stop's are, a hue or saturation fade's apart
    (classes.py)."""
    capabilities = FADE_BRIGHTNESS if attribute == BRIGHTNESS else FADE_COLOR
    return AdapterScope(
        domains=verified("light"), capabilities=capabilities, fields=verified(attribute), native_groups=True
    )


class Z2MRamp(Adapter[RampRequest]):
    """Publish the limit (or range end) with a transition for the distance left; the bulb runs the fade.

    Brightness and saturation fades are separate calls, each with its own transition: the bulb runs them
    independently. Predictions live per light and attribute (the core transition cache), so a move after a stop
    continues from where the light halted. A group gets one publish to its group topic, so its members fade together;
    each member's transition is recorded from its own start, and the group's start is the mean of its lit members.

    Raising brightness turns the light on. Lowering it to 0 % is Z2M's soft off, state OFF with the transition, which
    keeps the level for the next plain on. Saturation is written as a color, the light's current hue with the end
    saturation, and moves only lit lights. Hue is Z2M's hue_move, a cycle at a rate the bulb runs until a stop; a
    sweep_time outside 1-254 s is clamped, with clamped_to. A light whose Z2M definition lacks color_hs is unchanged
    with fields_dropped for hue and saturation. A move with nothing left to make is unchanged with at_limit; one with
    only lights that are off to move is unchanged with off.

    Z2M publishes the end value at once. Once a fade has run, its attribute is read back, and a saturation fade's color
    refreshed (color_refresh), so HA ends right even after a live read mid-fade.
    """

    integration: ClassVar[str] = "zigbee2mqtt"

    def supports(self, hass: HomeAssistant, request: RampRequest, target: Target) -> str | None:
        if (reason := super().supports(hass, request, target)) is not None:
            return reason
        if (reason := not_addressable(target)) is not None:
            return reason
        return None

    async def execute(self, hass: HomeAssistant, request: RampRequest, targets: list[Target]) -> list[EntityResult]:
        cache = transition_cache(hass)

        async def send(target: Target) -> EntityResult:
            if not (lights := await lights_of(hass, target)):
                return self.result(target, Status.UNSUPPORTED, detail=Reason.EMPTY_GROUP)
            if await lacks_color(hass, request, lights):
                return self.result(target, Status.UNCHANGED, detail="fields_dropped")
            if isinstance(outcome := plan(cache, request, target, lights), str):
                return self.result(target, Status.UNCHANGED, detail=outcome)
            clamped_to = await start(hass, cache, target, lights, outcome, NEED.get(self.action))
            return self.result(target, Status.SENT, clamped_to=clamped_to)

        return await checked(self, hass, targets, send)


type HueMove = tuple[int, float | None]  # hue_move's signed rate, and the sweep_time used where it was clamped


def plan(cache: TransitionCache, request: RampRequest, target: Target, lights: list[Target]) -> Fade | HueMove | str:
    """The attribute's fade or hue move, or why it has none to make."""
    if request.attribute == BRIGHTNESS:
        return brightness_fade(cache, request, target, lights)
    if request.attribute == SATURATION:
        return saturation_fade(cache, request, target, lights)
    return hue_rate(request) if lit(cache, lights) else "off"


async def start(
    hass: HomeAssistant,
    cache: TransitionCache,
    target: Target,
    lights: list[Target],
    outcome: Fade | HueMove,
    need: bool | None,
) -> float | None:
    """Publish the fade, recording it, and schedule its end read-back, or publish the hue move; the clamped
    sweep_time. Z2M's hue_native_control is held at need for the publishes (native_control.py)."""
    async with native_control(hass, target, need):
        if not isinstance(outcome, Fade):
            rate, clamped_to = outcome
            await publish_set(hass, target, {"hue_move": rate})
            hue_moves(hass).update(light.entity_id for light in lights)
            return clamped_to
        # Inside the option's window, so the prediction starts at the publish, not before the settings writes.
        async with cache.recording((light_id, outcome.attribute) for light_id in outcome.starts):
            for light_id, begin in outcome.starts.items():
                cache.moving(light_id, outcome.attribute, begin, outcome.ends[light_id], outcome.duration)
            for light, payload in outcome.before:
                await publish_set(hass, light, payload)
            await publish_set(hass, target, outcome.payload)
        if outcome.attribute == SATURATION:
            hue_moves(hass).difference_update(outcome.starts)  # a color write ends the bulb's hue cycle
    read = end_read(hass, target, lights, outcome.attribute)
    get_runtime(hass).schedule(hass, read_back_key(target, outcome.attribute), [(outcome.duration + END_READ_S, read)])
    return None


async def lacks_color(hass: HomeAssistant, request: RampRequest, lights: list[Target]) -> bool:
    """Whether a hue or saturation move meets a light whose Z2M definition lacks color_hs."""
    if request.attribute == BRIGHTNESS:
        return False
    return not await has_feature(hass, [z2m_address(light.device) for light in lights], COLOR_HS)


def brightness_fade(cache: TransitionCache, request: RampRequest, target: Target, lights: list[Target]) -> Fade | str:
    """The brightness fade toward the limit; off lowering with nothing lit, at_limit when there is none left to make."""
    up, limit = request.up, request.end()
    starts = {light.entity_id: light_brightness(cache, light) for light in lights}
    lit_lights = lit(cache, lights)
    if not up and not lit_lights:
        return "off"
    # Raising turns every light on, so the off ones fade in from the bottom; lowering passes them by.
    moving = [light.entity_id for light in (lights if up else lit_lights)]
    current = mean(starts[light.entity_id] for light in lit_lights or lights)
    if (limit - current if up else current - limit) < MIN_STEP:
        return "at_limit"
    duration = abs(limit - current) / 100 * request.sweep_time
    transition_s = round(duration, 1)
    if limit == 0:
        payload = {"state": "OFF", "transition": transition_s}
    else:
        payload = {"brightness": max(1, round(limit / 100 * 254)), "transition": transition_s}
        payload = {"state": "ON", **payload} if up else payload
    off = [light for light in lights if light not in lit_lights] if up else []
    # One publish to the group when it is all off, so its members light together; else each off member's own.
    before = tuple((light, AT_THE_BOTTOM) for light in ([target] if off == lights else off))
    moving_starts = {light_id: starts[light_id] for light_id in moving}
    return Fade(BRIGHTNESS, payload, moving_starts, dict.fromkeys(moving, limit), duration, before)


def saturation_fade(cache: TransitionCache, request: RampRequest, target: Target, lights: list[Target]) -> Fade | str:
    """The saturation fade toward the limit at the current hue; off with no lit light in color, at_limit when there is
    none left to make.

    Written as a color: the bulb holds its hue as it fades, with Z2M's hue_native_control on (in xy) or off, and Z2M
    publishes the end, which HA takes.
    """
    if not (movers := [light for light in lit(cache, lights) if hs_color(light) is not None]):
        return "off"
    up, limit = request.up, request.end()
    starts = {light.entity_id: cache.value(light.entity_id, SATURATION, hs_color(light)[1]) for light in movers}
    current = mean(starts.values())
    if (limit - current if up else current - limit) < MIN_STEP:
        return "at_limit"
    hue = (hs_color(target) or hs_color(movers[0]))[0]
    duration = abs(limit - current) / 100 * request.sweep_time
    payload = {"color": {"hue": round(hue, 1), "saturation": round(limit, 1)}, "transition": round(duration, 1)}
    return Fade(SATURATION, payload, starts, dict.fromkeys(starts, limit), duration)


def hue_rate(request: RampRequest) -> HueMove:
    """hue_move's signed rate for the sweep_time, a full cycle, and the sweep_time used where it had to be clamped.

    Clamped, not refused: the call's value lies outside what the bulb can do, as an out-of-range duration does. Rounding
    the rate inside the range isn't reported.
    """
    cycle = min(max(request.sweep_time, MIN_CYCLE_S), MAX_CYCLE_S)
    rate = min(max(round(254 / cycle), 1), 254)
    return (rate if request.up else -rate), (cycle if cycle != request.sweep_time else None)


def end_read(hass: HomeAssistant, target: Target, lights: list[Target], attribute: str) -> Job:
    """The read-back once a fade has run: each light's state and brightness, or the target's color refreshed."""

    async def read() -> None:
        if attribute == SATURATION:
            await refresh_color(hass, target, silent=True)
            return
        for light in lights:
            try:
                await publish(hass, topic(light, "get"), {"state": "", "brightness": ""})
            except Exception as err:  # noqa: BLE001 - a failed read leaves HA's state stale, nothing more
                _LOGGER.debug("End read-back for %s failed: %r", light.entity_id, err)

    return read
