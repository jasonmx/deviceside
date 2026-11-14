"""Zigbee2MQTT get_attributes: on/off timer, brightness, color temperature and color, read live from each bulb.

An off bulb's brightness is Z2M's, the level it comes back at (Reader.read).
"""

from datetime import datetime
from typing import ClassVar

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .addressing import not_addressable
from .classes import READ_WRITE
from .members import member_lights
from .read import Reader, reader
from ...core.adapter import Adapter
from ...core.errors import Reason
from ...core.models import EntityResult, Request, Status, Target
from ...core.scope import AdapterScope, experimental, verified
from ...core.zcl_row import average, zcl_row


class Z2MGetAttributes(Adapter[Request]):
    """One row per target from live ZCL reads (read.py): Hue bulbs on Z2M don't report, so HA's state can be stale.

    A group's row follows Hue's room row: its members are read one after another and averaged over the lit ones,
    or over all of them when every member is off. It has a timer only when every lit member runs one, and then it
    is the longest. A read that fails raises, so the call raises targets_failed.
    """

    action: ClassVar[str] = "get_attributes"
    integration: ClassVar[str] = "zigbee2mqtt"
    scope: ClassVar[AdapterScope] = AdapterScope(
        # Switches are Experimental, as in turn_on_for.
        domains=verified("light") | experimental("switch"),
        capabilities=READ_WRITE,
        confirmable=True,
        native_groups=True,  # read through its members, which only Z2M's bridge/groups knows
    )

    def supports(self, hass: HomeAssistant, request: Request, target: Target) -> str | None:
        return super().supports(hass, request, target) or not_addressable(target, reads_state=True)

    async def execute(self, hass: HomeAssistant, request: Request, targets: list[Target]) -> list[EntityResult]:
        async def read(target: Target) -> EntityResult:
            if not target.is_group:
                async with reader(hass, [target]) as reading:
                    return self.result(target, Status.CONFIRMED, data=zcl_row(*await _read(reading, target)))
            members = await member_lights(hass, target)
            if not members:
                return self.result(target, Status.UNSUPPORTED, detail=Reason.EMPTY_GROUP)
            # One after another inside the group's own task: gathering them would nest the concurrency limits.
            async with reader(hass, members) as reading:
                reads = [await _read(reading, member) for member in members]
            lit = [(attributes, now) for attributes, now in reads if attributes.get("onOff")]
            return self.result(target, Status.CONFIRMED, data=average([zcl_row(*one) for one in lit or reads]))

        return await self.gather(hass, targets, read)


async def _read(reading: Reader, target: Target) -> tuple[dict, datetime]:
    """The target's attributes, and when they were read, which a timer's end counts from."""
    attributes = await reading.read(target)
    return attributes, dt_util.utcnow()
