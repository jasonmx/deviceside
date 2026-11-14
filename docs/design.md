# Design

How DeviceSide is built and why. What each integration does for users is in its bundle's `index.md`, under
`docs/integrations/<integration>/`, with what they share in [integrations.md](integrations.md); what goes on the wire,
and what was measured on real devices, is in the bundle's `internals.md`, with what they share in
[internals.md](internals.md).

## Glossary

Code and docs use these terms.

- **Action**: what the user calls, e.g. `deviceside.turn_on_for`.
- **Feature**: the user experience one or more actions deliver (turn on with timer, smooth dimming). Prose only.
- **Integration**: the HA integration that is the first hop to the device (Shelly, ZHA, MQTT, Hue).
- **Bridge**: a separate system between the integration and the device, named only when the integration alone
  doesn't say how the device is reached (Zigbee2MQTT behind MQTT). The Hue Bridge is not counted.
- **Adapter**: one action on one integration (or bridge).
- **Capability**: the code's name for a Class: `IntegrationInfo.capability` names a target's, `IntegrationInfo.classes`
  declares them, `AdapterScope.capabilities` holds a level per class, and a result reports it as `capability`.
- **Class**: the set of devices that get the same call and run the same firmware family, as the integration names it
  (Shelly: the RPC component; Zigbee2MQTT: `hue`, `tuya_zcl`, `tuya_ts011f`, `tuya_mcu`, `sengled`, `gledopto`,
  `other`). Each class stands on its own evidence, and a device the integration can't place is in its default class,
  which is never Verified: a record on another maker's device adds that maker's class. Where untested, it is
  Experimental (except where a call is known to harm: there a target whose class rests on information DeviceSide
  doesn't have yet is refused). Rules: [evidence.md](evidence.md#device-classes).
- **Doesn't work**: a class that fails, or is harmed by, the call DeviceSide sends for an action, even when an unbuilt
  call would work. Listed in the adapter's
  `AdapterScope.doesnt_work` with a reason and source, refused (`doesnt_work`) and named on the card.
- **Not yet supported**: a class that needs a call DeviceSide hasn't built for an action, and that today's call
  doesn't harm. The integration names it,
  the adapter's scope doesn't hold it, and it is refused (`not_supported`) and named on the card.
- **Field**: an action parameter, e.g. `brightness`.
- **Entity domain**: light, switch. "Domain" alone is fine where unambiguous.
- **Scope**: the entity domains, classes and fields an adapter is built for, each Verified, Documented or untested,
  and flagged or not, plus the classes it knows don't work. Outside it, calls are refused (`domain_not_tested`,
  `fields_not_tested`, or `not_supported` for a class).
- **Level**: how a scope item is proven, Verified or Documented. Rules: [evidence.md](evidence.md#levels). User
  wording: [device-compatibility.md](device-compatibility.md#levels-of-proof).
- **Experimental**: the flag on a scope item (`experimental(...)`) or a whole adapter (`experimental=True`) not yet
  judged ready for everyone, which keeps it off until the user turns on Experimental features; not a level. An
  integration maintainer lifts it when they judge it ready; a passing run is evidence for that, not a trigger. Rules:
  [evidence.md](evidence.md#the-experimental-flag).
- **Maintainer**: a GitHub handle in an integration's `IntegrationInfo.maintainers`. Maintainers look after that
  integration's package: they get its bug reports and approve changes to it. Rules:
  [CLAUDE.md](../CLAUDE.md#maintainers). User wording: [device-compatibility.md](device-compatibility.md#maintainers).
- **Repo maintainer**: a handle in `REPO_MAINTAINERS` (`scripts/gen_codeowners.py`), with admin on the repo. Repo
  maintainers own shared code, merge every PR and set caveats. Distinct from an
  integration's Maintainer.
- **Caveat**: a risk mark on an adapter (`AdapterScope.caveat`, an issue URL), ⚠️ Untested fix or ⚠️ Unmaintained,
  set by a repo maintainer. It ships live; setup warns, its card shows the mark and the release notes say why. Rules:
  [evidence.md](evidence.md#caveats).
- **Sign-off**: a whole passing rig run (every test record at one package tree) or a `_manual.md` line from one of an
  integration's Maintainers (or a Repo maintainer when none responds), holding the package as the release does, which a
  release needs for each integration changed since the last tag. Rules: [evidence.md](evidence.md#release-gate).
- **Experimental features**: the option (`experimental_features`, off by default) that lets calls touching a flagged
  item through. Only the dispatcher reads it.
- **Rig**: a tool that tests DeviceSide's features automatically with real hardware on a live Home Assistant instance,
  and writes up the results as test records.
- **Test record**: the evidence that an adapter works on real hardware, from a rig run:
  `docs/integrations/<integration>/test-records/<action>*.md`. A maintainer's hand test goes in the same folder's
  `_manual.md`, one line per run; either can be a sign-off ([release gate](evidence.md#release-gate)).
- **Device report**: a user's "works" or "doesn't work" for an action on a model, filed with the device report form;
  the GitHub issue is its record. A maintainer who trusts a "works" report may cite it in a `_manual.md` line as a
  sign-off, which for an item no maintainer can test verifies it; a "doesn't work" is evidence for
  a fix or for leaving the item out.
- **Profile**: one test setup on the rig's HA, the entities a run may move by integration and role; a record names the
  one it ran on (`lab`, `house`).

## Principles

What gets built, and the evidence it needs: [CONTRIBUTING.md](../CONTRIBUTING.md#development-principles). How it is
built:

- **On the device.** Once the command is sent, the behavior completes (or degrades to plain device behavior) with
  HA offline.
- **One action per user intent, not per protocol primitive.** Zigbee has move, step and move-to; DeviceSide has one
  raise and one lower per attribute, and the adapter picks the command.
- **Human terms.** Fields describe what the light should do, never how the device does it: no rates, steps, mireds
  or cluster vocabulary. The adapter converts.
- **Behave as core does.** When to raise, what to skip and what a response holds follow HA core's own actions:
  [action-behavior.md](action-behavior.md).
- **Drop-in for Hue Smooth Dimming:** [action-behavior.md](action-behavior.md#replacing-hue_dimmer).
- **Highest aggregation the device accepts.** A group goes to the device as one command where the integration has
  a native group, to cut radio traffic and popcorning.

### Naming

1. `verb_object`, in HA vocabulary only: objects are the names HA's own services and state attributes use
   (`brightness`, `attributes`, `scene`). Protocol vocabulary (`level`, `OnLevel`, `toggle_after`) never appears in an
   action or field name.
2. Commands use HA's command verbs: `turn_on_for`, `raise_brightness`, `stop`, `set_attributes`.
3. A persistent setting (how the device behaves from now on) is `set_<behavior>` with the trigger in plain English:
   `set_auto_off`, `set_auto_on`. It is undone by `clear_<behavior>`, as core does (`sonos.clear_sleep_timer`), never
   by a zero value: in core a zero duration means "now". `clear_`, not `cancel_`: core's `cancel` stops something
   already running (`timer.cancel`).
4. Field names are `light.turn_on`'s wherever one exists; a feature-specific field gets plain words (`duration`,
   `sweep_time`, `limit`).

## Scope

Integrations: Zigbee2MQTT (via `mqtt`), ZHA, Matter, Shelly Gen2+ and Hue (CLIP v2). Each adapter's `AdapterScope`
says what it covers, and the README feature table (`scripts/gen_features.py`) lists every adapter. Each integration's
docs, data and records are its bundle, `docs/integrations/<integration>/`, which
[CLAUDE.md](../CLAUDE.md#where-facts-go) lays out. What can't be built where, and why: `NO_PATH` in
`scripts/gen_features.py`, the README's prohibited signs, explained to users in
[integrations.md](integrations.md#what-cant-be-done-and-why).

- **`turn_on_for`, `turn_off_for`**: on, then off after `duration` (or the reverse), timed on the device.
  `turn_on_for` takes the `light.turn_on` fields.
- **`set_auto_off`, `set_auto_on`, `clear_auto_off`, `clear_auto_on`**: a setting stored on the device, applied after
  every turn-on (or turn-off) from any source. A call that changes nothing sends nothing.
- **`raise_*`, `lower_*`, `stop`**: one action per attribute (brightness, saturation, hue) and direction, as HA names
  its direction actions (`cover.open_cover`). `sweep_time` is how long a full range takes, `limit` ends the move
  early, and `stop` halts every move on the target. No color temperature move: every stop of a Hue fade jumps visibly.
- **`set_attributes`, `get_attributes`**: write brightness, color or color temperature without turning on, and read
  what HA doesn't show. Every timed or set-while-off adapter needs a matching `get_attributes` on its integration.
- **`set_scene`**: a scene's settings, written to each light while it's off. A Zigbee scene recall turns lights on, so
  Zigbee would follow the same pattern.

Not built yet: turn-on defaults (`OnLevel`), Shelly Gen1, Hue bridge v1, other domains (cover, valve, lock) and other
integrations (Z-Wave JS, Tuya, ESPHome, Tasmota, TP-Link Kasa). Each is weighed against the broad-appeal principle
in [CONTRIBUTING.md](../CONTRIBUTING.md#development-principles). Per device class:
[device-compatibility.md](device-compatibility.md).

## Architecture

Facts from HA core this rests on:

- `helpers/group.py:expand_entity_ids` expands only `group.*`. A light or switch group helper is not expanded by
  target extraction, so `core/groups.py` expands it through `get_entity_ids`.
- `zha.issue_zigbee_cluster_command` is an admin service, so adapters forward the caller's `call.context`.
- HA auto-imports top-level modules named after entity platforms and after `group`, `diagnostics`, `repairs` and the
  like, so adapters live under `integrations/` and nothing at the top level is called `light.py`.
- Schemas use `probatio` and reuse `light.const.LIGHT_TURN_ON_SCHEMA` entries, so ranges never drift from core,
  bar brightness: it is a percent in every action, as in Hue Smooth Dimming. `turn_on_for` converts it to 1–255 where
  it calls `light.turn_on`, never to 0, and never as `brightness_pct`, which core (`light/helper.py`) rounds to 0
  below 0.2%.

### core/

```
models.py        Status, ActionKind, Request, Target, EntityResult, LightFields
scope.py         AdapterScope, IntegrationInfo, DurationLimits
action.py        Action base class
adapter.py       Adapter base class; gather() runs sends under the concurrency limits
registry.py      AdapterRegistry: discovery, guarded import, lookup, matrix
resolver.py      entity_id -> Target, or the reason it can't be served
groups.py        HA group expansion, any domain
dispatcher.py    parse, extract, expand, resolve, plan, run, raise rule, response
ha_watch.py      drops predictions a light or scene call from HA leaves stale
transition.py    a move's predicted value, per integration
level_fade.py    LevelFade: raise and lower from the level the device reports, in one timed set
readback.py      read_back: the read after an accepted write; a failed read reports sent
runtime.py       DeviceSideData in hass.data: registry, dispatcher, semaphores, integration state
```

### Core contract

- **Registry.** `pkgutil` discovery over `actions/` and `integrations/`. Modules export data (`ACTION`,
  `INTEGRATION`, `ADAPTERS`), not decorators, so import has no side effects, order is sorted and deterministic, and
  `check_release.py` and the rig, from outside the repo, import the registry without `hass`, so a change to
  `AdapterRegistry`'s interface needs the rig's matching change. A failing integration import (its core
  integration isn't installed) records it unavailable and setup carries on.
- **Classes.** `IntegrationInfo.capability(hass, target) -> str | None` names the target's class: sync and cheap, no
  I/O, reading only the registry device, the config entry or the integration's data (Zigbee2MQTT's sources and when
  a device counts as placed: [its page](integrations/zigbee2mqtt/index.md#device-classes)). A device it can't place
  gets the default class; `None` is only for a target it can't class at all (a Shelly entity with no RPC component),
  which counts as Experimental. Where a call is known to harm, the adapter refuses a target whose class rests on
  information DeviceSide doesn't have yet, even with Experimental features on; one placed in the default class from
  full information stays Experimental ([evidence.md](evidence.md#doesnt-work-not-yet-supported-untested)). It takes `hass=None` on a target with no config
  entry or state, so the rig can class a stand-in (Zigbee2MQTT then reads the registry alone), until the rig reads the
  class from the result. Every adapter of the integration sees the same class, and maps the classes it serves to its own
  branch in `execute`; a class is a device class, not a call pattern, as patterns are per action.
  `IntegrationInfo.classes` declares every class it can name. The registry refuses an integration with `capability`
  unless every adapter's scope has `capabilities`, one without unless none has, a scope class the integration doesn't
  declare, and a scope holding `IntegrationInfo.default_class` (the class a device it can't place gets; none on Shelly)
  as Verified. `AdapterScope.doesnt_work` maps a class to a `DoesntWork` (what, reason, source); a class in it is never
  in `capabilities`.
- **Resolver.** Candidates are the available integrations whose `ha_domain` is the entity's registry platform;
  one with a `detect` hook wins first (Z2M over plain MQTT). A `switch_as_x` light resolves to the switch it wraps,
  and results report the light.
- **Groups.** HA groups are always expanded, in one place, because no device can address them; members carry
  `via_group`. Native groups (Hue `grouped_light`, Z2M and ZHA groups) pass through as one target. An adapter with
  `native_groups` gets the group; for one without, the dispatcher calls the integration's `expand_group`, or refuses
  with `group_not_supported` when there is none. A native group an adapter addresses as one takes its members'
  classes, from `expand_group`, its weakest member's level for the gate. An empty one (`expand_group` lists no
  members) is skipped silently, named or not, as core skips an empty group. One whose members can't be listed (no
  `expand_group`, it answered `None`, or it raised, logged) counts as Experimental: refused while Experimental
  features is off, and, where a call is known to harm, by that adapter's `supports()` with them on
  ([evidence.md](evidence.md#doesnt-work-not-yet-supported-untested)). When a member's class doesn't work or isn't supported, or, while Experimental features is off, is
  Experimental (the adapter, domain and fields not flagged), the group falls back to its members: each available one
  not already a target is planned on its own, indirect, so the good ones run and a refused or flagged one is skipped
  and logged (a member also named raises, as its own target). While Experimental features is off, a classifier that
  raised (logged) leaves the group's class unknown, so it falls back too, and each member is classed again. With
  Experimental features on, a group whose weakest member is Experimental, or whose class is unknown, runs whole, as
  one command; a member named beside it then gets its own command as well as the group's. A group that falls back
  gets no result of its own: its planned members answer for it, including one already a target, so naming a member
  beside a group that falls back changes nothing. Only when none of its members is planned or left nothing to do
  (each refused, gated or unavailable) is the group itself refused, with its reason (the refused member's, or
  `experimental_off`): raised if named, skipped and logged if not. For an adapter without `native_groups`, an
  `expand_group` that answers `None` or raises (logged) leaves membership unknowable, so the group is refused as
  `group_not_supported`, as with none.
- **Dispatcher.** Buckets targets by adapter, drops fields a target lacks, checks `supports()` (domain and fields in
  scope, and the adapter's own reasons), then the class (`AdapterScope.refusal`: a class in `doesnt_work` is refused as
  `doesnt_work`, one in neither it nor `capabilities` as `not_supported`; a call left nothing to send is first), clamps
  durations to `AdapterScope.duration`, then runs every adapter concurrently under
  `ADAPTER_TIMEOUT_S`: `Adapter.slot` (which `gather` runs each target in) rejects with `timeout` only the targets
  not done by then, so those that finished keep their results. An adapter exception rejects its targets. A named
  target that can never take the action raises before anything is sent; an indirect one is skipped and logged; a
  device failure raises once every target has run.
- **Experimental items** are gated here, at runtime. After `supports()` passes a target and fields are dropped,
  `Dispatcher.run_adapters` takes the call's level from its domain, the class `IntegrationInfo.capability` names (one
  it can't name counts as Experimental; a native group takes its members' weakest) and the fields it still carries. An
  Experimental one is refused as `experimental_off` while the option is off, raising its own message for a named
  target. A call left nothing to send is unchanged, not refused.
- **Results.** Commands answer optionally, per entity: `{status, integration, capability?, clamped_to?, detail?}`
  (a native group's `capabilities?` in place of `capability?`), where status is
  `sent`, `confirmed` (read back from the device) or `unchanged` (nothing to send). Queries answer only, in core's
  shape: each target's data under its entity_id, as `weather.get_forecasts` does, the class among it. The class is
  the one the dispatcher used, for an integration that names classes, so a record holds the class a call reached.
- **Actions** register once in `async_setup` and refuse calls until the config entry loads.

### Concurrency

Each command runs under two semaphores, applied in `Adapter.gather()`: the integration's
(`IntegrationInfo.max_concurrency`: Hue, ZHA and Matter 2; Shelly and Z2M 4) and the adapter's
(`AdapterScope.max_concurrency`). Hue is 2 because aiohue's cap of 3 is shared with core. `execute` gets all of an
adapter's targets at once, so native group commands stay possible.

A per-device lock (`LevelFade.guard`, and the stop that shares it) is `gather`'s `hold`, taken before the slot under
the same deadline, so a call waiting on its device holds no slot; a section under a hold never takes a slot itself.
Not built, for when more integrations need per-device ordering: a lock-free per-device stop counter (a move that sees
a stop land during it sends one more halt), or a per-device command queue.

### services.yaml, strings, icons

- `services.yaml` holds only target filters and field selectors; names and descriptions are in `strings.json`.
  Target filters list exactly the integrations with an adapter for the action. They are a UI convenience: adapters
  enforce scope through `supports()`.
- Field filters can only test `supported_features` and attributes, not domain, so light fields copy
  `light/services.yaml`'s `supported_color_modes` filters; a switch-only selection shows just `duration`.
- Adding an action means a block each in `services.yaml`, `strings.json` and `icons.json`;
  `tests/core/test_actions_setup.py` checks all three.

## Release gates

The rules are in [evidence.md](evidence.md#release-gate); each gate's mechanics are in the header of the script or
workflow named.

- **Coverage:** `scripts/check_release.py --coverage`, in CI.
- **Sign-offs:** `check_release.py --signoffs`, in `cut-release`: a sign-off, or ⚠️ Untested fix on every changed
  adapter, for each integration whose package differs from the last release tag. `--notes` lists the unsigned
  integrations and Untested fix caveats in both cut workflows.
- **Tags:** `scripts/check_tag.py`, in `.github/workflows/cut-release-candidate.yml` and `cut-release.yml`, the only
  way a tag is made.
- **RC audit:** `scripts/check_audit.py`, in `cut-release-candidate`.
- **Testing:** unit tests mock each integration at its boundary (MQTT's publish and subscribe, the ZHA service, the
  Shelly RPC, the Matter client, aiohue) against a real `hass`. The rig runs against a live HA and writes the test
  records.

**Hardware the rig lacks:** a maintainer verifies by hand and signs off with a `_manual.md` line. After v0.1.0, a
published rig kit lets maintainers run the rig on their own devices and commit attributed test records under
`docs/integrations/<integration>/test-records/`.
