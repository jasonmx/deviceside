"""Zigbee2MQTT stop: halt the bulb's fades and hue cycle, remember where they halted, and refresh HA's color."""

from typing import ClassVar

from homeassistant.core import HomeAssistant

from .addressing import not_addressable
from .bridge import publish_set
from .classes import STOP
from .color_refresh import DELAY_S, refresh_color
from .fade import hue_moves, lights_of, read_back_key, transition_cache
from .set_check import checked
from ...core.adapter import Adapter
from ...core.errors import Reason
from ...core.models import EntityResult, Request, Status, Target
from ...core.runtime import get_runtime
from ...core.scope import AdapterScope, verified
from ...core.transition import BRIGHTNESS, SATURATION, hs_color, light_brightness, lit

# Z2M's saturation_move: stop halts only the saturation attribute, and the xy fade our saturation move runs carries on.
_STOP_MOVE_STEP = {
    "command": {
        "cluster": "lightingColorCtrl",
        "command": "stopMoveStep",
        "payload": {"optionsMask": 0, "optionsOverride": 0},
    }
}


class Z2MStop(Adapter[Request]):
    """Send brightness_move: stop to the light or group, with hue_move: stop where a hue move of ours cycles and a raw
    stopMoveStep where a saturation move of ours fades, and record the predicted values as where it halted.

    Each stop is its own publish, the color ones first: Z2M reads the light back after a brightness stop before it
    sends the next key of the same message. Z2M's read-back puts HA right for brightness; after a color stop, HA's color
    is refreshed where it needs it (color_refresh). A light or group with nothing lit (off, and no soft off of ours
    fading it) is unchanged with off and nothing is sent.
    """

    action: ClassVar[str] = "stop"
    integration: ClassVar[str] = "zigbee2mqtt"
    scope: ClassVar[AdapterScope] = AdapterScope(domains=verified("light"), capabilities=STOP, native_groups=True)

    def supports(self, hass: HomeAssistant, request: Request, target: Target) -> str | None:
        if (reason := super().supports(hass, request, target)) is not None:
            return reason
        if (reason := not_addressable(target)) is not None:
            return reason
        return None

    async def execute(self, hass: HomeAssistant, request: Request, targets: list[Target]) -> list[EntityResult]:
        cache = transition_cache(hass)
        cycling = hue_moves(hass)

        async def send(target: Target) -> EntityResult:
            if not (lights := await lights_of(hass, target)):
                return self.result(target, Status.UNSUPPORTED, detail=Reason.EMPTY_GROUP)
            if not (halting := lit(cache, lights)):
                return self.result(target, Status.UNCHANGED, detail="off")
            # A halt is predicted only where a move of ours ran: elsewhere Z2M's read-back is right at once.
            halts = {
                BRIGHTNESS: {
                    light.entity_id: light_brightness(cache, light)
                    for light in halting
                    if cache.tracks(light.entity_id, BRIGHTNESS)
                },
                SATURATION: {
                    light.entity_id: cache.value(light.entity_id, SATURATION, (hs_color(light) or (0, 0))[1])
                    for light in halting
                    if cache.tracks(light.entity_id, SATURATION)
                },
            }
            hue = [light.entity_id for light in halting if light.entity_id in cycling]
            async with cache.recording(
                (light_id, attribute) for attribute, values in halts.items() for light_id in values
            ):
                for attribute, values in halts.items():
                    for light_id, value in values.items():
                        cache.stopped(light_id, attribute, value)
                if hue:
                    await publish_set(hass, target, {"hue_move": "stop"})
                if halts[SATURATION]:
                    await publish_set(hass, target, _STOP_MOVE_STEP)
                await publish_set(hass, target, {"brightness_move": "stop"})
            cycling.difference_update(hue)

            async def refresh() -> None:
                # A raw stopMoveStep publishes nothing, so Z2M has no read-back of its own.
                await refresh_color(hass, target, silent=bool(halts[SATURATION]))

            # Replaces the end read-backs of the moves this halts.
            color_halted = hue or halts[SATURATION]
            runtime = get_runtime(hass)
            runtime.schedule(hass, read_back_key(target, BRIGHTNESS), [])
            runtime.schedule(hass, read_back_key(target, SATURATION), [(DELAY_S, refresh)] if color_halted else [])
            return self.result(target, Status.SENT)

        return await checked(self, hass, targets, send)
