# How actions behave

When an action stops an automation, what it skips and what its response holds all match Home Assistant core,
since users, blueprints and templates already expect core's behavior. For a case not covered here, read core's code
and match it; DeviceSide's own habits are not precedents.

The facts below were read from HA 2026.10.2: `helpers/service.py` (`entity_service_call` and the functions it
calls), `helpers/target.py` (`async_extract_referenced_entity_ids`), `components/light/helper.py`
(`filter_turn_on_params`) and `helpers/template/extensions/functional.py` (`merge_response`). Re-read them when HA
changes; `.venv` holds the version CI runs.

## When to stop an automation

An exception from an action stops the automation unless the step sets `continue_on_error`. Core raises only
when the call is wrong or a device failed, never because some targets didn't apply:

| Situation | Core | DeviceSide |
|---|---|---|
| Invalid input (out of range, wrong type) | Raises at the schema | Raise at the schema |
| A target is unavailable or missing | Skips it, logs "Referenced entities ... are missing or not currently available" | Skip and log alike |
| No target given (an empty selection) | Returns, nothing raised | Return |
| No targets left after skipping | Returns, nothing raised | Return |
| The same, when the caller asked for a response | Raises: no entity matched | Raise `no_entities_matched` |
| `entity_id: all` | Targets every entity of the domain, none named, so none raises or logs as missing | Same: every light and switch is indirect, so one that can't take the action is skipped with the warning |
| A target the caller named can never support the action | Raises `ServiceNotSupported` | Raise `target_not_supported`, before anything is sent |
| The same target reached indirectly | Skips it silently | Skip it, with one warning per call listing each target and why |
| A light can't take one field (color on a white bulb) | Drops the field silently and acts on the rest | Drop it, act on the rest, `detail: fields_dropped` |
| The device or its integration fails | The error propagates | Run every target, then raise `targets_failed` listing the failures |
| DeviceSide's entry isn't loaded (disabled, or its setup failed) | Raises `service_config_entry_not_loaded` | Raise `not_loaded`, before anything is resolved |

Why: a mixed room must not abort an automation over the lights that can't take part, but a call that names
the wrong entity, or a device that failed, should stop it, because carrying on would hide the mistake.

**Named or indirect.** A target is named when the caller listed its entity ID. Everything else is indirect:
an area, device or label, a member of an HA group (`group.*` or a group helper), or a member of a native group
the dispatcher expanded. Core treats a `group.*` member as named; DeviceSide doesn't, because a group is a
deliberately mixed set. An indirect entity outside the action's domains is never a target, as in core.

**Can never support** is any reason that holds whatever the device's state: no adapter, a domain or option nobody
has tested, a class that doesn't work or isn't supported (`doesnt_work`, `not_supported`), an entity the integration
can't address. A field the adapter has never been
tested with is in this class too: it is refused, never dropped. **Lacks a field** comes from the target's state, read
as `filter_turn_on_params` reads it (a color is lacked only without any color mode, color temperature only without
that or a color mode, since core converts between them first), and from the adapter where only it knows: Hue's
gamut, `AdapterScope.drops`, and a Shelly component's fields. A field tested on one Shelly component but not yet
ported or recorded for another is dropped there, not refused (`color_temp_kelvin` on a `CCT` light). A `switch`, or a `switch_as_x` light, lacks every
light field, so a timed call still runs its timer. A call left nothing to do by dropping is `unchanged` with
`fields_dropped`. An empty group is skipped silently, as core's group expansion yields nothing for one.

**Not yet classed** (`class_unknown`) is refused the same way, raised if named and skipped if not, but it is
transient: it holds only until the integration has reported the device or group
([evidence.md](evidence.md#doesnt-work-not-yet-supported-untested)). That usually takes seconds, so an automation
firing at startup can get it; if the integration never reports it (DeviceSide couldn't connect to it), it holds
until DeviceSide reloads.

**Unlisted fields depart from core.** An adapter may name, in `AdapterScope.unlisted`, fields its devices take
although the light's state says they can't; a light never lacks those for that adapter, where
`filter_turn_on_params` would drop them. Why: DeviceSide fills design gaps between an upstream integration and HA,
as it makes Zigbee2MQTT announce the HS mode its full-color lights have ([integrations.md](integrations.md)), and
an integration that leaves a mode out of `supported_color_modes` shouldn't stop a device that has it. HA's own UI still shows the light without the mode,
and `light.turn_on` still drops the field. An unlisted field must be in the adapter's `fields`, at its own level:
fields are never Documented, so it starts Experimental. A `switch` still lacks every light field.

**A value the device can't reach** passes the schema but lies outside what the device can do: a timer longer
than its counter, or a `raise_hue` or `lower_hue` `sweep_time` outside a native hue cycle's 1–254 s (Zigbee2MQTT). It is clamped to
that range and the result carries `clamped_to`, the value used. It isn't refused, as the call can still do the
nearest thing, and it isn't `fields_dropped`, which is for a field the light lacks. Rounding inside the range
isn't reported.

## What a response looks like

Core's entity actions return `{entity_id: <that entity's data>}`, one entry per entity that ran, for example
`{"weather.home": {"forecast": [...]}}` from `weather.get_forecasts`. There is no status per entity: a failure
raises or the entity is skipped.

- **Queries** (`get_attributes`) return that shape: each target's data under its entity ID, nothing else, and
  a target that couldn't be read raises `targets_failed` once every target has run, as any device failure does.
  Templates read `response['light.desk'].brightness`.
- **Timers.** `get_attributes` takes lights and switches. A target with an on/off timer running (from
  `turn_on_for`, `turn_off_for` or the device's own) carries `timer: {turns, ends_at, remaining_s}`: `turns`
  is `on` or `off`, the state the timer leaves it in; `ends_at` is UTC ISO 8601; `remaining_s` is seconds, to
  one decimal. With no timer running the key is absent. The time left is always the device's: an integration
  that can't read it reports no timer, and DeviceSide never tracks one itself.
- **Auto-off and auto-on.** A Shelly target carries `auto_off` and `auto_on`: `{duration}` (seconds) while set,
  `null` while clear. A key is absent when the setting couldn't be read, so absent never means clear.
- **`merge_response`** turns that shape into a list of the entities' dicts with `entity_id` added to each, so
  a blueprint can loop without knowing the IDs. It requires every value to be a dict and raises if one already
  has an `entity_id` key, so query data never carries `entity_id`.
- **One entry per target.** A room returns one entry, not one per light as well, so `merge_response` lists each
  target once.
- **Commands and settings** return nothing in core. DeviceSide keeps an optional status response, one entry per target
  that ran: `confirmed`, `sent` or `unchanged`, with `integration` and, where they apply, `detail` and
  `clamped_to`. Skipped targets are left out, as core leaves them out, and `unsupported` or `rejected` never
  reach a response: the call skips or raises instead. A call that has nothing to do succeeds quietly as
  `unchanged` with a `detail`, checked before the target's class, so nothing-to-do wins over doesn't-work or not
  supported. A native group with a refused member, or an Experimental one while Experimental features is off, falls
  back to its members where the integration can list them. A group that falls back has no entry of its own: its
  members' entries answer for it, and it raises only when none of its members runs
  ([design.md](design.md#core-contract), Groups). A named target whose class doesn't work raises with the reason why.
- **The class.** On an integration that names [classes](evidence.md#device-classes), each entry carries the
  class the call reached, as an attribute would: a command's beside `status` and `integration`, a query's among its
  data. A target's is `capability` (a string: `"RGBCCT"`); a native group's is `capabilities`, its members' classes
  once each, sorted (a list: `["hue", "other"]`). The key is absent when the integration couldn't name one, for a
  group when it couldn't for any member, so a list always stands for the whole group.

## Replacing hue_dimmer

DeviceSide's actions are drop-in replacements for those of Hue Smooth Dimming (`hue_dimmer`,
[jasonmx/hue-smooth-dimming](https://github.com/jasonmx/hue-smooth-dimming)): a user changes only the domain, and
renames `raise`/`lower` to `raise_brightness`/`lower_brightness`, which take the same fields. That means the same
field names and units (brightness in percent), names and descriptions in the UI, the same response shape, and
hue_dimmer's input habits: `0` leaves `min_brightness`, `max_brightness` or `color_temp_kelvin` unset, and given
several colors, `rgb_color` wins, then `hs_color`, then `xy_color`. Before changing any of these, check hue_dimmer
and say what breaks for its users. README's migration box is the user-facing version of this section.
