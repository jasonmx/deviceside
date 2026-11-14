"""Set Z2M's hue_native_control the way an action needs it, for that action's own publish only.

With the option on, Z2M sends on, brightness, color and color temperature to a Hue light as one Philips-cluster
command (zigbee-herdsman-converters src/lib/philips.ts); with it off, as ZCL commands. Some actions need one of the two:

- A timer with a color needs it on: a Hue bulb drops a color sent while it is off, so as ZCL it lights at its last
  color first.
- A brightness fade needs it off: sent native, a long raise runs about 2.3x faster than its transition, and a soft off
  reports `onOff: 0` at once while the bulb still fades.

The rest work either way and leave it alone: a saturation fade holds its hue, and a stop, a hue move and a read or
write of settings pass on the rig with the option on and off.

The option belongs to the user. An action that needs it the other way sets it, publishes, and puts the user's value
back once Z2M has published the target's state in reply (1 s cap), so nothing outlasts the publish. The user's value
is journaled as the set goes out (restore_journal.py) and written back from there, so a restore that failed earlier
is paid by the next action on the target, whatever its need. The call returns
once its command is out; the restore runs in a task of its own, still under the target's lock. An action that works
either way may publish before it lands; turn_on_for's timer waits for it (`settled`). Z2M reads an
entity's options as it takes a command (lib/extension/publish.ts), so a fade already sent runs on unchanged. A command
to a group reads the group's options, never its members', so a group with a Hue member is set itself.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from contextlib import asynccontextmanager
from typing import Any

from homeassistant.core import HomeAssistant

from .addressing import group_id, options_address, z2m_address, z2m_device
from .bridge import OPTION, ensure_options, group_members, state_reply
from .classes import HUE_MANUFACTURER
from .restore_journal import Address, journal, locks
from ...core.models import Target
from ...core.runtime import get_runtime

_LOGGER = logging.getLogger(__name__)

# The fields Z2M sends as a separate command after on; brightness rides moveToLevelWithOnOff with it.
COLOR_FIELDS = frozenset({"color_temp_kelvin", "hs_color", "rgb_color", "xy_color"})
# Each action's need: True on, False off. An action left out works either way and leaves the option alone.
NEED: dict[str, bool] = {
    "turn_on_for": True,  # with a color; without one it works either way
    "raise_brightness": False,
    "lower_brightness": False,
}
_REPLY_CAP_S = 1.0


def _restores(hass: HomeAssistant) -> dict[Address, asyncio.Task[None]]:
    return get_runtime(hass).integration_state.setdefault("zigbee2mqtt_native_control_restores", {})


async def settled(hass: HomeAssistant, target: Target) -> None:
    """Wait for a pending restore of the target's option, from this call or an earlier one.

    For a publish that must go out with the user's value, whatever action ran on the target just before. A cancel of
    the waiter leaves the restore running; a restore cancelled at shutdown ends the wait.
    """
    if (address := options_address(target)) is None or (restore := _restores(hass).get(address)) is None:
        return
    try:
        await asyncio.shield(restore)
    except asyncio.CancelledError:
        current = asyncio.current_task()
        if restore.cancelled() and (current is None or current.cancelling() == 0):
            return
        raise


@asynccontextmanager
async def native_control(hass: HomeAssistant, target: Target, need: bool | None) -> AsyncIterator[None]:
    """Hold the option at `need` around the body's publish to a Hue device, or a group with a Hue member.

    Reads the option on each call (from the bridge cache when it holds bridge/info, which Z2M republishes on every
    change), so a user who changes it is seen. A need of None, a non-Hue target or the option already right costs no
    writes, and a non-Hue device reads nothing, unless a restore is still owed for the target (an earlier one failed):
    then the body runs and the owed value is written back after, as for a set. One lock per device or group, held
    from the set to the restore, keeps actions with opposite needs apart, and a replay of the journal out.

    Returns once the body has, without waiting on Z2M's reply: the option is put back after Z2M answers (1 s cap), in a
    task that holds the lock until then, so the next call on the target waits for it instead. A publish that must go
    out with the user's value awaits `settled()` first.

    Never raises of its own: a failed check, set or restore logs a warning, and the body runs (or has run) anyway.
    """
    if need is None:
        yield
        return
    try:
        address = await _hue_address(hass, target)
    except Exception as err:  # noqa: BLE001 - the command still goes out, encoded as the user's option has it
        _warn_set(target, need, err)
        address = None
    if address is None:
        yield
        return
    lock = locks(hass).setdefault(address, asyncio.Lock())
    await lock.acquire()
    handed_off = False
    try:
        if not await _take(hass, target, address, need):
            yield
            return
        replied: asyncio.Future[None] | None = None
        unsubscribe: Callable[[], None] = lambda: None  # noqa: E731
        try:
            replied, unsubscribe = await state_reply(hass, target)
            yield
        except BaseException:
            # The body raised, or the call was cancelled: no reply to wait for, but the option still goes back.
            _restore_task(hass, address, _restore(hass, target, address, need, None, unsubscribe, lock))
            handed_off = True
            raise
        _restore_task(
            hass, address, _restore(hass, target, address, need, replied, unsubscribe, lock, wait_for_reply=True)
        )
        handed_off = True
    finally:
        if not handed_off:
            lock.release()


async def _take(hass: HomeAssistant, target: Target, address: Address, need: bool) -> bool:
    """Set the option to need, under the address's lock; whether a restore is due after the body: the option may
    have changed, or a restore is still owed from before."""
    journaled = False

    async def owe(_z2m_id: str, _options: dict) -> None:
        # As the request goes out, not after: a restart before Z2M answers still finds the user's value owed.
        nonlocal journaled
        journaled = await _owe(hass, address, not need, target.entity_id)

    # Shielded so that a cancel mid-set (HA stopping), the journal's save included, still learns whether the option
    # changed, and puts it back.
    setting = asyncio.ensure_future(_set(hass, target, address, need, ahead=owe))
    try:
        changed = await asyncio.shield(setting)
    except asyncio.CancelledError:
        if await setting:
            await _put_back(hass, target, address, not need)
        elif journaled:
            await _strike(hass, address, not need, target.entity_id)
        raise
    if changed:
        return True
    if journaled:
        await _strike(hass, address, not need, target.entity_id)  # refused: the option stands as it was
    return await _owed(hass, address, target.entity_id) is not None


def _restore_task(hass: HomeAssistant, address: Address, restore: Coroutine[Any, Any, None]) -> None:
    # Tracked, so HA's shutdown waits for the restore rather than cancelling it. The lock keeps one restore per address.
    task = hass.async_create_task(restore, "zigbee2mqtt hue_native_control restore")
    restores = _restores(hass)
    restores[address] = task
    task.add_done_callback(lambda done: restores.pop(address, None) if restores.get(address) is done else None)


async def _restore(
    hass: HomeAssistant,
    target: Target,
    address: Address,
    need: bool,
    replied: asyncio.Future[None] | None,
    unsubscribe: Callable[[], None],
    lock: asyncio.Lock,
    wait_for_reply: bool = False,
) -> None:
    """Put the user's value back, after Z2M's reply to the body's publish when wait_for_reply, then free the lock.

    The restore itself is shielded: cancelled while it runs, it still finishes before the lock is freed.
    """
    try:
        try:
            if wait_for_reply:
                await _reply(target, replied)
        finally:
            unsubscribe()
            # Only a value other than need was changed, and an absent option is Z2M's default, off.
            restoring = asyncio.ensure_future(_put_back(hass, target, address, not need))
            try:
                await asyncio.shield(restoring)
            except asyncio.CancelledError:
                await restoring
                raise
    finally:
        lock.release()


async def _owe(hass: HomeAssistant, address: Address, value: bool, entity_id: str) -> bool:
    """Journal the user's value as owed, so a restart before the restore doesn't leave the option flipped; whether
    this call added the entry (an earlier one still owed is kept)."""
    try:
        return await (await journal(hass)).add(address, value)
    except Exception as err:  # noqa: BLE001 - the journal is a safety net; the action goes on without it
        _LOGGER.warning("Couldn't journal the pending %s restore for %s: %r", OPTION, entity_id, err)
        return False


async def _owed(hass: HomeAssistant, address: Address, entity_id: str) -> bool | None:
    """The value the journal owes back at the address, or None (none owed, or the journal unreadable)."""
    try:
        return (await journal(hass)).owes(address)
    except Exception as err:  # noqa: BLE001 - as if none were owed
        _LOGGER.warning("Couldn't read the %s restore journal for %s: %r", OPTION, entity_id, err)
        return None


async def _strike(hass: HomeAssistant, address: Address, value: bool, entity_id: str) -> None:
    try:
        await (await journal(hass)).strike(address, value)
    except Exception as err:  # noqa: BLE001 - a stale entry is replayed harmlessly at the next online
        _LOGGER.warning("Couldn't strike the %s restore for %s off the journal: %r", OPTION, entity_id, err)


async def _put_back(hass: HomeAssistant, target: Target, address: Address, fallback: bool) -> None:
    """Restore the user's value, the one the journal owes (fallback when none is), and strike it off once Z2M
    confirmed it."""
    value = owed if (owed := await _owed(hass, address, target.entity_id)) is not None else fallback
    if await _set(hass, target, address, value, restoring=True):
        await _strike(hass, address, value, target.entity_id)


async def _reply(target: Target, replied: asyncio.Future[None] | None) -> None:
    if replied is None:
        # Without a reply to await, the cap is the margin for Z2M to take the command.
        await asyncio.sleep(_REPLY_CAP_S)
        return
    try:
        async with asyncio.timeout(_REPLY_CAP_S):
            await replied
    except TimeoutError:
        _LOGGER.debug("No state from Zigbee2MQTT for %s in %s s", target.entity_id, _REPLY_CAP_S)


async def _hue_address(hass: HomeAssistant, target: Target) -> Address | None:
    """Where the target's options live, for a Hue device or a group with a Hue member; None for anything else."""
    if (group := group_id(target)) is not None:
        return ("group", group) if await _has_hue_member(hass, target, group) else None
    if target.device is not None and target.device.manufacturer == HUE_MANUFACTURER:
        return ("device", ieee) if (ieee := z2m_address(target.device)) is not None else None
    return None


async def _has_hue_member(hass: HomeAssistant, target: Target, group: str) -> bool:
    for ieee in await group_members(hass, group):
        device = z2m_device(hass, target, ieee)
        if device is not None and device.manufacturer == HUE_MANUFACTURER:
            return True
    return False


async def _set(
    hass: HomeAssistant,
    target: Target,
    address: Address,
    value: bool,
    restoring: bool = False,
    ahead: Callable[[str, dict], Awaitable[None]] | None = None,
) -> bool:
    """Set the option to value where it differs, or regardless for a restore; whether the option may now be value.
    Awaits ahead before the request goes out, only when one does.

    It may be when Z2M confirmed the write, or when the request went out and no answer came, as Z2M may have applied
    it: either way the caller restores. A refused write left the option as it was. A restore doesn't compare: the
    cache's write-through would show our own confirmed set, but a set that timed out left no confirmation, so writing
    regardless is the safe default.
    """
    written = False
    requested = False

    def confirmed(_z2m_id: str) -> None:
        nonlocal written
        written = True
        _LOGGER.debug("Set Zigbee2MQTT's %s to %s for %s", OPTION, value, target.entity_id)

    def wanted(current: dict) -> dict:
        nonlocal requested
        requested = restoring or bool(current.get(OPTION)) != value
        return {OPTION: value} if requested else {}

    kind, z2m_id = address
    try:
        await ensure_options(hass, kind, [z2m_id], wanted, confirmed, ahead)
    except Exception as err:  # noqa: BLE001 - the command goes out (or went out) as the option stood
        if not restoring:
            _warn_set(target, value, err)
            return requested and isinstance(err, TimeoutError)
        else:
            _LOGGER.warning(
                "Couldn't put Zigbee2MQTT's %s back to %s for %s, so it stays %s: %r",
                OPTION,
                value,
                target.entity_id,
                not value,
                err,
            )
    return written


def _warn_set(target: Target, value: bool, err: Exception) -> None:
    _LOGGER.warning(
        "Couldn't set Zigbee2MQTT's %s to %s for %s, so the command goes out as the option stands: %r",
        OPTION,
        value,
        target.entity_id,
        err,
    )
