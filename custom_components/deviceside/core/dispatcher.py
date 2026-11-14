import asyncio
import logging
from collections import deque
from dataclasses import replace

from homeassistant.const import ATTR_ENTITY_ID, ENTITY_MATCH_ALL, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, State, split_entity_id
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import target as target_helpers

from . import duration, ha_watch
from .action import Action
from .adapter import Adapter
from .errors import EXPERIMENTAL_OFF, Reason, TargetNotSupported
from .fields import lacking
from .groups import expand_ha_groups
from .models import FAILED, ActionKind, EntityResult, Request, Status, Target, TimedRequest
from .registry import AdapterRegistry
from .resolver import resolve
from .runtime import DEADLINE, get_runtime
from .scope import Level
from ..const import ADAPTER_BACKSTOP_S, ADAPTER_TIMEOUT_S, DOMAIN

_LOGGER = logging.getLogger(__name__)


def _available(state: State | None) -> bool:
    return state is not None and state.state != STATE_UNAVAILABLE


# A target's class, or a native group's members' classes (core/scope.py, AdapterScope.level); None for one the
# integration couldn't name.
type Classes = tuple[str | None, ...]
# One execute: the adapter, the request its targets get, the targets, the fields dropped, and each target's classes.
type Run = tuple[Adapter, Request, list[Target], frozenset[str], dict[str, Classes]]


def _classed(result: EntityResult, target: Target, classes: Classes) -> EntityResult:
    """The result with the class the call reached (docs/action-behavior.md, What a response looks like): a
    target's, or a native group's members', sorted and each once. None when the integration couldn't name one, for a
    group any member's, so a group's list never passes for its whole membership."""
    if not classes or None in classes:
        return result
    if target.is_group:
        return replace(result, classes=tuple(sorted({name for name in classes if name is not None})))
    return replace(result, classes=classes[0])


def _skip(
    entity_id: str,
    named: bool,
    reason: str,
    integration: str | None = None,
    why: str | None = None,
    title: str | None = None,
) -> list[EntityResult]:
    """A target that can't take the action: raise if the caller named it (with `why`, a doesn't-work entry's reason,
    and `title`, the integration's, which a class_unknown message names), else the result that skips it."""
    if reason == Reason.EMPTY_GROUP:
        return []
    if named:
        raise TargetNotSupported(entity_id, reason, why, title)
    return [EntityResult(entity_id, Status.UNSUPPORTED, integration=integration, detail=reason)]


def _unanswered(
    adapter: Adapter, fallen: list[tuple[Target, list[Target], str, str | None]], answered: set[str]
) -> list[EntityResult]:
    """Each group that fell back with no member planned or left nothing to do, refused with its reason: raised if
    named, else skipped. A group a member answers for gets no result of its own."""
    results: list[EntityResult] = []
    for group, members, reason, why in fallen:
        if not any(member.reported_id in answered for member in members):
            results += _skip(group.reported_id, group.named, reason, adapter.integration, why)
    return results


class Dispatcher:
    def __init__(self, hass: HomeAssistant, registry: AdapterRegistry):
        self.hass = hass
        self.registry = registry

    async def handle(self, action: type[Action], call: ServiceCall) -> ServiceResponse:
        """Act on every target that can take the action, as core's entity actions do (docs/action-behavior.md).

        A named target that can never take it raises before anything is sent; an indirect one is skipped and logged.
        Unavailable targets are skipped. A device failure raises once every target has run.
        """
        request = replace(action.parse(self.hass, call), context=call.context)
        if call.data.get(ATTR_ENTITY_ID) == ENTITY_MATCH_ALL:
            # Core passes referenced=None for "all": every entity of the domains is indirect, so none raises or logs.
            selected = target_helpers.SelectedEntities(
                indirectly_referenced=set(self.hass.states.async_entity_ids(action.domains))
            )
        else:
            selection = target_helpers.TargetSelection(call.data)
            if not selection.has_any_target:
                # Core returns here, and raises no_entities_matched only when the caller asked for a response.
                return self._respond(action, call, [])
            # expand_group=False: we expand group.* ourselves so members remember which group they came from.
            selected = target_helpers.async_extract_referenced_entity_ids(self.hass, selection, expand_group=False)
        members, _ = expand_ha_groups(self.hass, sorted(selected.referenced | selected.indirectly_referenced))

        results, targets = self._resolve(action, selected, members)
        if targets:
            # A light call from HA just before this one may still be dropping predictions this one would plan from.
            await ha_watch.settled(self.hass)
            results += await action.run(self.hass, request, targets, self)
        order = {entity_id: index for index, entity_id in enumerate(members)}
        results.sort(key=lambda result: order.get(result.entity_id, len(order)))
        return self._respond(action, call, results)

    def _resolve(
        self, action: type[Action], selected: target_helpers.SelectedEntities, members: dict[str, str | None]
    ) -> tuple[list[EntityResult], list[Target]]:
        """The targets to plan, and a skip for each indirect one that resolution already rules out."""
        results: list[EntityResult] = []
        missing: set[str] = set()
        targets: list[Target] = []
        for entity_id, via_group in members.items():
            named = entity_id in selected.referenced
            if split_entity_id(entity_id)[0] not in action.domains:
                # Core's entity actions never see another domain's entities in an area, so only a named one raises.
                if named:
                    raise TargetNotSupported(entity_id, "domain_not_supported")
                continue
            if not _available(self.hass.states.get(entity_id)):
                missing.add(entity_id)
                continue
            target = resolve(self.hass, self.registry, entity_id, via_group)
            if target == Reason.INTEGRATION_NOT_LOADED:
                missing.add(entity_id)
            elif isinstance(target, str):
                results += _skip(entity_id, named, target)
            elif target.domain not in action.domains:
                results += _skip(entity_id, named, "domain_not_supported", target.integration)
            else:
                targets.append(replace(target, named=named))
        selected.log_missing(missing & selected.referenced, _LOGGER)
        return results, targets

    @staticmethod
    def _respond(action: type[Action], call: ServiceCall, results: list[EntityResult]) -> ServiceResponse:
        """Log the skipped targets, raise on a device failure or an empty response, else answer for those that ran."""
        # An empty group is skipped silently, whether planning found it so or an adapter that reads membership did.
        if skipped := [r for r in results if r.status is Status.UNSUPPORTED and r.detail != Reason.EMPTY_GROUP]:
            _LOGGER.warning(
                "%s skipped targets that can't take it: %s",
                action.key,
                ", ".join(f"{result.entity_id} ({result.detail})" for result in skipped),
            )
        ran = [result for result in results if result.status is not Status.UNSUPPORTED]
        if failed := [result for result in ran if result.status in FAILED]:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="targets_failed",
                translation_placeholders={
                    "summary": ", ".join(f"{result.entity_id}: {result.detail or result.status}" for result in failed)
                },
            )
        if not call.return_response:
            return None
        if not ran:
            # Core raises here too: the caller asked for data and nothing could give it.
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="no_entities_matched")
        if action.kind is ActionKind.QUERY:
            # Core's shape for data, as weather.get_forecasts returns it: each target's own data under its entity_id.
            # The class rides the data, as an attribute would (docs/action-behavior.md).
            return {result.entity_id: (result.data or {}) | result.class_keys() for result in ran}
        return {result.entity_id: result.as_response() for result in ran}

    async def run_adapters(self, action_key: str, request: Request, targets: list[Target]) -> list[EntityResult]:
        """Plan every target, then run each adapter once per distinct request, every run concurrently.

        Planning buckets targets by adapter, expands native groups an adapter can't address, drops the fields each
        target lacks and checks supports() on what is left, then the target's class (a native group's: its members'):
        one the scope lists in doesnt_work, or the integration names but the scope doesn't serve, is refused; a
        native group so refused falls back to its members when the integration can list them (docs/design.md), and an
        empty one is skipped silently. A flagged call, from an `experimental=True` adapter or touching an Experimental
        scope item, is refused while the Experimental features option is off; a native group flagged by a member's
        class alone falls back to its members instead. A named target that can't take the action raises then, before
        anything is sent. Returns a result per target that ran or was left nothing to do, each with its class, and an
        unsupported one per indirect target skipped.
        """
        results: list[EntityResult] = []
        buckets: dict[str, tuple[Adapter, list[Target]]] = {}
        for target in targets:
            if (adapter := self.registry.adapter(action_key, target.integration)) is None:
                results += _skip(target.reported_id, target.named, "no_adapter", target.integration)
                continue
            buckets.setdefault(target.integration, (adapter, []))[1].append(target)

        runs: list[Run] = []
        experimental_on = get_runtime(self.hass).experimental_features
        for adapter, bucket in buckets.values():
            runs += self._plan(
                adapter, request, self._expand_native_groups(adapter, bucket, results), results, experimental_on
            )

        for adapter_results in await asyncio.gather(*(self._run_adapter(*run) for run in runs)):
            results.extend(adapter_results)
        return results

    def _plan(
        self,
        adapter: Adapter,
        request: Request,
        targets: list[Target],
        results: list[EntityResult],
        experimental_on: bool,
    ) -> list[Run]:
        """One adapter's runs, one per set of dropped fields so every target in a run gets the same request; each
        target skipped or left nothing to do goes in results instead.

        A native group that falls back to its members gets no result of its own: they answer for it. Only when none
        of its members was planned or left nothing to do (each refused, gated or unavailable) is the group itself
        refused, with its reason, raised if named, once every target is judged."""
        by_dropped: dict[frozenset[str], list[Target]] = {}
        classes: dict[str, Classes] = {}
        pending = deque(targets)
        seen = {target.reported_id for target in pending}
        # Each group that fell back, its members and the reason it would be refused with; the targets that answer.
        fallen: list[tuple[Target, list[Target], str, str | None]] = []
        answered: set[str] = set()
        while pending:
            target = pending.popleft()
            dropped = lacking(target, request, adapter.scope.unlisted) | (
                request.fields_present() & adapter.scope.drops
            )
            reduced = request.without(dropped)
            if reason := adapter.supports(self.hass, reduced, target):
                title = self.registry.integrations[adapter.integration].title
                results += _skip(target.reported_id, target.named, reason, adapter.integration, title=title)
                continue
            members = self._listed(adapter, target)
            if members == []:
                # Core's group expansion yields nothing for an empty group, so it is skipped silently.
                results += _skip(target.reported_id, target.named, Reason.EMPTY_GROUP)
                continue
            classes[target.reported_id] = found = self._classes(adapter, target, members)
            if reduced.nothing_to_do():
                answered.add(target.reported_id)
                results.append(
                    _classed(adapter.result(target, Status.UNCHANGED, detail="fields_dropped"), target, found)
                )
            elif refused := adapter.scope.refusal(found):
                # A native group falls back to its members, each judged alone: the good ones run, a refused one is
                # skipped as indirect.
                if members is not None:
                    fallen.append((target, members, *refused))
                    pending.extend(self._fallback(adapter, target, members, seen, refused[0]))
                else:
                    results += _skip(target.reported_id, target.named, refused[0], adapter.integration, refused[1])
            elif not experimental_on and self._experimental(adapter, reduced, target, found):
                # A native group flagged only by a member's class falls back the same way: the members of Verified
                # or Documented classes run, the flagged one is skipped (raised only if named on its own).
                if members is not None and self._flagged_by_class_alone(adapter, reduced, target):
                    fallen.append((target, members, EXPERIMENTAL_OFF, None))
                    pending.extend(self._fallback(adapter, target, members, seen, EXPERIMENTAL_OFF))
                else:
                    results += _skip(target.reported_id, target.named, EXPERIMENTAL_OFF, adapter.integration)
            else:
                answered.add(target.reported_id)
                by_dropped.setdefault(dropped, []).append(target)
        results += _unanswered(adapter, fallen, answered)
        return [(adapter, request.without(dropped), ready, dropped, classes) for dropped, ready in by_dropped.items()]

    def _experimental(self, adapter: Adapter, request: Request, target: Target, classes: Classes) -> bool:
        """Whether the call is flagged experimental: the whole adapter (`experimental=True`), or an Experimental item
        it touches, its domain, class (a native group's: its weakest member's) or a field it carries. The one place
        the option gates anything; adapters never read it (docs/evidence.md)."""
        if adapter.scope.experimental:
            return True
        return adapter.scope.level(target.domain, classes, request.fields_present()) is Level.EXPERIMENTAL

    def _flagged_by_class_alone(self, adapter: Adapter, request: Request, target: Target) -> bool:
        """Whether a flagged call would pass but for its classes: the adapter isn't flagged whole, nor its domain or
        a field it carries, so a group's members can run one by one."""
        if adapter.scope.experimental:
            return False
        return adapter.scope.level(target.domain, (), request.fields_present()) is not Level.EXPERIMENTAL

    def _listed(self, adapter: Adapter, target: Target) -> list[Target] | None:
        """A native group's members, for an integration that names classes: what its classes come from and what it
        falls back to. `[]` only for a group known to have no members; None when unknown, and for any other
        target."""
        if not target.is_group or self.registry.integrations[adapter.integration].capability is None:
            return None
        return self._members(adapter, target)

    def _classes(self, adapter: Adapter, target: Target, members: list[Target] | None) -> Classes:
        """The target's class, or a native group's members' classes (`members`, as _listed gives them), as
        IntegrationInfo.capability names them; empty for an integration that names none. A group whose members can't
        be listed, and a classifier that raised (logged), give (None,), which the gate weighs as Experimental, as an
        unknown device."""
        if (capability := self.registry.integrations[adapter.integration].capability) is None:
            return ()
        try:
            listed = (members or []) if target.is_group else [target]
            found: Classes = tuple(capability(self.hass, member) for member in listed) or (None,)
        except Exception:  # noqa: BLE001 - a classifier's bug must not abort the call: the class counts as unknown
            _LOGGER.exception("%s on %s: classing %s failed", adapter.action, adapter.integration, target.reported_id)
            found = (None,)
        _LOGGER.debug("%s on %s: %s is class %s", adapter.action, adapter.integration, target.reported_id, found)
        return found

    def _members(self, adapter: Adapter, target: Target) -> list[Target] | None:
        """A native group's members, as the integration's expand_group lists them; None, membership unknowable, when it
        has no expand_group, or it answered None or raised, which is logged."""
        if (expand := self.registry.integrations[adapter.integration].expand_group) is None:
            return None
        try:
            return expand(self.hass, target)
        except Exception:  # noqa: BLE001 - one group's listing must not abort the call
            _LOGGER.exception("%s on %s: listing %s failed", adapter.action, adapter.integration, target.reported_id)
            return None

    @staticmethod
    def _fallback(adapter: Adapter, group: Target, members: list[Target], seen: set[str], reason: str) -> list[Target]:
        """The members a refused or class-flagged native group falls back to, marked seen: its available ones not
        already targets, which a target already answers for."""
        fresh = [member for member in members if _available(member.state) and member.reported_id not in seen]
        _LOGGER.debug("%s: %s falls back to its members (%s)", adapter.action, group.reported_id, reason)
        seen.update(member.reported_id for member in fresh)
        return fresh

    def _expand_native_groups(
        self, adapter: Adapter, bucket: list[Target], results: list[EntityResult]
    ) -> list[Target]:
        if adapter.scope.native_groups or not any(target.is_group for target in bucket):
            return bucket
        expanded: list[Target] = []
        for target in bucket:
            if not target.is_group:
                expanded.append(target)
            elif (members := self._members(adapter, target)) is None:
                # No expand_group, or one that answered None or raised (logged): membership is unknowable here.
                results += _skip(target.reported_id, target.named, "group_not_supported", adapter.integration)
            else:
                # Members are indirect, so an unavailable one is skipped quietly, as core skips one in an area.
                expanded += [member for member in members if _available(member.state)]
        # One copy per entity. A member the caller also named stays named, whichever copy came first, so it raises
        # rather than skips, as a named target does.
        by_id: dict[str, Target] = {}
        for target in expanded:
            kept = by_id.setdefault(target.reported_id, target)
            if target.named and not kept.named:
                by_id[target.reported_id] = replace(kept, named=True)
        return list(by_id.values())

    async def _run_adapter(
        self,
        adapter: Adapter,
        request: Request,
        targets: list[Target],
        dropped: frozenset[str],
        classes: dict[str, Classes],
    ) -> list[EntityResult]:
        clamped_to = None
        if isinstance(request, TimedRequest):
            request, clamped_to = duration.clamp(request, adapter.scope.duration)
        # Each run is its own task (run_adapters gathers them), so the deadline set here is this run's alone.
        deadline = asyncio.get_running_loop().time() + ADAPTER_TIMEOUT_S
        DEADLINE.set(deadline)
        try:
            # Adapter.slot holds each command to the deadline; this catches an execute still busy outside it.
            async with asyncio.timeout_at(deadline + ADAPTER_BACKSTOP_S):
                raw = await adapter.execute(self.hass, request, targets)
        except TimeoutError:
            _LOGGER.warning(
                "%s on %s timed out after %ss",
                adapter.action,
                adapter.integration,
                ADAPTER_TIMEOUT_S + ADAPTER_BACKSTOP_S,
            )
            return [adapter.result(target, Status.REJECTED, detail="timeout") for target in targets]
        except Exception:
            _LOGGER.exception("%s on %s failed", adapter.action, adapter.integration)
            return [adapter.result(target, Status.REJECTED, detail="error") for target in targets]
        return self._reconcile(adapter, targets, raw, clamped_to, dropped, classes)

    def _reconcile(
        self,
        adapter: Adapter,
        targets: list[Target],
        raw: list[EntityResult],
        clamped_to: float | None,
        dropped: frozenset[str],
        classes: dict[str, Classes],
    ) -> list[EntityResult]:
        """One result per target: stray ones dropped, missing ones rejected. A run the dispatcher dropped fields
        from says so in detail, unless the adapter gave one. Each carries the class the call reached."""
        by_id: dict[str, EntityResult] = {}
        expected = {target.reported_id for target in targets}
        for result in raw:
            if result.entity_id in expected and result.entity_id not in by_id:
                by_id[result.entity_id] = result
            else:
                _LOGGER.debug(
                    "%s on %s: dropping stray result for %s", adapter.action, adapter.integration, result.entity_id
                )
        out = []
        for target in targets:
            result = by_id.get(target.reported_id) or adapter.result(target, Status.REJECTED, detail="no_result")
            if result.status not in FAILED:
                if dropped and result.detail is None:
                    result = replace(result, detail="fields_dropped")
                if clamped_to is not None and result.clamped_to is None:
                    result = replace(result, clamped_to=clamped_to)
                result = _classed(result, target, classes[target.reported_id])
            out.append(result)
        return out
