# Device classes: evidence, routing and refusals per class of device

## Coordination

Intent: Give every integration one way to say which class of device a target is, and make the class carry the
evidence. A class holds its own level (Verified, Documented, flagged), its own "doesn't work" entries, and its own
"not yet supported" state. The cards are generated from that data. A CI check fails when a Verified class has no
record behind it. The default call path runs for unknown devices behind `experimental`, and known exceptions are
refused in code and named on the cards.
Touches: docs/evidence.md, docs/design.md, CLAUDE.md (tenet 5's line), docs/device-compatibility.md, CONTRIBUTING.md,
TODO.md, README.md (regenerated), custom_components/deviceside/core/{scope.py, adapter.py, registry.py,
dispatcher.py, errors.py}, the five integration packages and their bundles, scripts/gen_compat.py, and their tests.
Tests-with: ha-rig #84 (step 2, merged) writes the Capability column. A second ha-rig change, after step 4, reads the
class from each result (Decision 6). It lands before any rig run on step 4's branch or later.
Depends-on: none. #350 (Control4) gets no comment and no change until step 8. It is `experimental=True` throughout, so
none of these steps blocks it.

## Why

- **The gap nobody flagged.** Every Zigbee2MQTT record is a Philips Hue bulb. `domains=verified("light")` still makes
  every Z2M light Verified, so an IKEA or Innr bulb runs unflagged and shows as tested on the cards. Nothing compares a
  scope's `verified(...)` with the models its records reached, so neither a maintainer nor Claude was told. ZHA, Hue
  and Matter have the same gap.
- **Zigbee breaks "same call, same result".** The timed-on research
  ([2026-11-14](../integrations/zigbee2mqtt/research/2026-11-14-timed-on-call-patterns.md)) found five distinct call
  patterns for one action. Even devices that get the identical standard call differ in firmware: Hue replaces a
  running timer where the spec says extend, and some IKEA and Hue models turn on and never off. Tenet 5 assumed the
  call decides the result. On multi-vendor protocols, the call and the firmware family decide it together.
- **"Doesn't work" isn't enforced.** `devices.yaml` `doesnt_work` reaches the cards but not the code, so a listed
  device is still sent the call. On Z2M, 49 Tuya plug definitions have no `on_time` converter. Our call becomes a
  permanent plain On, and we report `sent`.
- **Refusals are hand-rolled per adapter.** Hue repeats a `bridge_v1` check in five adapters and a `smart_scene` check
  in one. The card's "Bridge v2/Pro" is a typed `requires:` line. Each is a fact with two homes.
- **Shelly already does most of this.** `capability()`, per-capability levels, the experimental gate and the card
  tiers all exist. The plan generalizes them and moves them to the integration, rather than adding a new mechanism.

## Tenets behind the design (to settle with Jason)

These refine `docs/evidence.md`; they don't replace its tenets.

1. **Proof follows the call and the class** (refines tenet 5). Evidence is about how a class of devices responds to a
   call. A class is the set of devices that get the same call and run the same firmware family, as the integration's
   `capability()` names it. Code that sends the same call to the same class inherits the evidence.
2. **Unknown is flagged, never Verified.** A device the classifier can't place falls in the integration's default
   class, which no record reaches until one does. So it runs only behind `experimental`. A misclassification can only
   make something more cautious. The exception is tenet 8: where a call is known to harm, a target whose class rests
   on information DeviceSide doesn't have yet is refused, even with Experimental features on; one placed in the
   default class from full information stays Experimental.
3. **Three ways a class can't take an action, each shown differently.**
   - *Doesn't work:* it fails the call we send. Refused in code, listed with a reason and source.
   - *Not yet supported:* it needs a call we haven't built. Refused, listed as not yet supported.
   - *Untested:* the default call may work. Runs behind `experimental`.
4. **One fact, one home, in code.** Classes, levels and doesn't-work entries live in the package's code. The cards are
   generated from it. `devices.yaml` keeps only presentation: each class's phrase and section.
5. **The split rule.**
   - A different gateway or API is a separate integration, with a `detect` hook on the same `ha_domain`, as Z2M is
     beside MQTT. Example: a Hue v1 bridge.
   - A different device behind the same API is a class.
   - A class that needs a different payload is a branch inside the adapter.
   - An adapter splits only if a class needs a different result contract. Today that never happens. A different
     duration limit becomes a per-class setting first.
6. **Build only what Z2M needs.** Z2M is the most varied integration, so it sets the bar. Rules match on
   manufacturer and model until a real departure needs firmware. No matcher, setting or tier is added ahead of an
   integration that uses it.
7. **Split off the exception, not the class** (Decision 25): `docs/evidence.md`, Device classes.
8. **Where a call is known to harm, a class not yet known is refused** (Decisions 25 and 26): `docs/evidence.md`,
   Device classes.

## Design

### Core

- **`IntegrationInfo.capability(hass, target) -> str | None`** names the target's class. It's sync and cheap, with no
  I/O, and may read the registry device, the config entry, or integration data such as a Control4 Director item. It
  replaces `Adapter.capability()` and Shelly's `RpcComponent` mixin. Every adapter of the integration sees the same
  class.
- **Capability names a device class, not a call pattern.** Patterns are per action: a timed-on pattern means nothing
  to a fade. Each adapter maps the classes it serves to its own branch in `execute`. This replaces the
  `pattern:class` names suggested in discussion.
- **Registry rule.** An integration with `capability` needs `scope.capabilities` on every adapter. One without needs it
  on none. The check moves from per adapter to per integration, so an adapter can't silently skip the axis.
- **`AdapterScope.doesnt_work: Mapping[str, DoesntWork]`**, keyed by class, each entry with what, reason and source.
  It is per adapter because a TS011F fails `turn_on_for` but answers `get_attributes`. The dispatcher refuses with a
  new `Reason.DOESNT_WORK`, raised for a named target and skipped for an indirect one, as the existing raise rule
  does. Doesn't-work classes are deliberately left out of `capabilities`.
- **Not yet supported** is a class the integration names, in neither `capabilities` nor `doesnt_work`. The existing
  `capability_not_tested` refusal covers it. Rename it `not_supported` in the same PR, as the old name misdescribes
  it.
- **Native groups** take the weakest level among their members' classes. A member in `doesnt_work` or not supported
  refuses the group, unless the integration can expand it.

### Records and the coverage check

- The rig writes the class each test reached into the record's Tests table, as a Capability column, from
  `IntegrationInfo.capability`. `_manual.md` rows gain a Capabilities column.
- **The coverage check.** Every class an adapter's scope holds as Verified must appear in a passing record test, or a
  manual row, for that action. Each gap is named, for example: "zigbee2mqtt turn_on_for: `hue` is Verified but no
  record reaches it". It warns on every PR (`gen_compat --check`), so a developer sees the gap early, and fails only
  at `cut-release` (`check_release.py`), where the other release gates run (Decision 7).
- With the class known per test, the Works tier credits classes per record: a record still can't map a test to a
  model, so an adapter flagged on a class counts a record's models only when every passing test reached an
  unflagged class. That replaces the rule where any flagged capability added no models.

### Cards

Per integration, the tiers are: Works (models and classes with records), Should work (Documented classes),
Experimental (flagged classes), Not yet supported (classes the integration names with no adapter in scope), and
Doesn't work (generated from `doesnt_work`). `devices.yaml` keeps `column`, `column_order` and the class phrases.
`doesnt_work` and, eventually, `requires` move to code.

## Execution (Jason, 2026-11-14)

- **One implementer per step**, a subagent or session given this plan and the step, and **a separate reviewer
  subagent** per step. The reviewer gets the plan, the step number, the step's diff, CLAUDE.md and
  `docs/evidence.md`, never the implementer's conclusions. It judges the diff against this plan's intent and
  tenets. Its findings are fixed or answered before the next step starts, and a "Plan review" note goes in the step's
  last commit.
- Steps 1 and 4 are on the binding-rule surface, so they also get the Opus audit.
- **Branches:** stacked, one per step, `claude/device-classes-<n>-<name>`, each off the previous, so each can become
  its own PR in order.
- **Decisions the plan doesn't settle go to Jason**, asked directly, and are recorded below as they are made.
- **The rig:** the lab profile is available every step. It has the Hue bulbs on Hue, ZHA and Z2M, the TS011F plug and
  the Matter WiZ plug, and can take a Sengled bulb or the Shelly color bulb when needed. The house profile, which
  has the Shelly switch devices, is a day or two away. Each lab run is also that integration's v0.1.0 sign-off if its
  package doesn't change afterward. The A60, the Sengled E11-G13 (the "ZHA bulb", paired on ZHA) and the Shelly color
  bulb share one lab fixture and are swapped by hand, so each lab run names which bulb goes in the fixture and on which
  network.

## Steps

Each step is one PR, in order, with pytest, pre-commit and the regenerated files as usual.

1. **Rules and design (docs only).** Done: `claude/device-classes-1-rules`.
2. **Capability column.** Done: `claude/device-classes-2-capability-column`, ha-rig #84 merged.
3. **Lab probe, before any classifier code (1a).** The implementer first writes a probe spec, in this plan and to
   Jason. Jason runs it in the lab while step 4 (core) proceeds. Results go in each bundle's `internals.md`, dated.
   - **Registry strings:** what HA's device registry holds (manufacturer, model, `model_id`, firmware) for each lab
     device on Hue, ZHA, Z2M and Matter. Vendor and model strings only, never names, IDs or addresses.
   - **The TS011F probe** (Decision 3).
   - **The Tuya detection signal** on Z2M and ZHA.
   - **Sengled, once it's in the lab:** its strings on each network, and whether it honors 0x42. The research puts it
     in pattern E, no device timer.
   - **Probe spec:** [below](#step-3-probe-spec).
4. **Core** (the plan's former step 3).
   - `IntegrationInfo.capability`, the registry rule, `doesnt_work` and `Reason.DOESNT_WORK`, and the rename.
   - The class in each result (Decision 6).
   - Shelly moves from its mixin to `IntegrationInfo.capability`. Its scopes don't change.
   - `gen_compat`: the new tiers, `doesnt_work` read from code, and the coverage check, warning only (Decision 7).
     `check_release.py`: the same check, failing.
   - Decision 5's rule in `docs/evidence.md`.
   - Second rig change: instructions given to Jason directly. It lands before any rig run on step 4's branch or
     later.
   - **Accepted:** `check_release --signoffs` fails on Shelly's coverage gaps until its house rerun records the
     Capability column. No release is cut before that rerun.
   - `gen_compat`'s Capability-cell regex captures any cell, so a malformed cell fails loudly instead of dropping
     a pass (PR #423 audit, Low).
5. **Zigbee2MQTT (the driver).**
   - Classes from the probe's real strings: `hue`, `tuya_zcl`, `tuya_mcu`, `other`, and `sengled` if the Sengled needs
     its own rule. A one-off test bulb lifts its maker's class, never `other` (Decision 5).
   - Rules are data, so a later probe changes rule data, not structure.
   - Scopes as planned. `turn_on_for` refuses the Tuya classes as not supported. `sengled` on `turn_on_for` is not
     supported if the probe confirms it ignores 0x42. The TS011F's entry follows Decision 16.
   - Once the probe's outcome settles the TS011F's entry, update the docs that depend on it: `docs/evidence.md`'s
     tenets example, the `docs/integrations.md` Zigbee plugs line and the `docs/models.yaml` reason, then regenerate
     `docs/device-compatibility.md`. Their "never switches on" is disproven: a `_TZ3000_typdpbpg` plug switched on
     106 s after a 10 s request on ZHA.
   - **Answered (Decision 18):** what `capability()` returns for a Z2M device before the first `bridge/devices`
     after a connect. The probe answered the precondition: HA's device registry holds no Tuya strings for a Z2M
     device, only Z2M's definition (`TS011F_plug_1`) (`docs/integrations/zigbee2mqtt/internals.md`).
   - **Group members, sync:** Z2M has no `expand_group`. It gets one, listing a group's members from the bridge
     cache's `bridge/groups`, so a group takes its members' classes and falls back to them (Decision 10).
   - **The default class in code (Decision 5):** an `IntegrationInfo` field naming the default class, which the
     registry refuses at Verified in any scope.
   - **Lab run** on the branch tip, with the Sengled. It tests the classifier, the column, the coverage check, Works
     credited per class, and the refusals. Fixture: the A60 on Z2M, then the Sengled E11-G13 on Z2M in its place.
   - **Part 1, done** (`claude/device-classes-5-z2m`): everything that needs no probe string. The classifier
     (`integrations/zigbee2mqtt/classes.py`), rules as data, with the `hue` rule alone (manufacturer `Philips`, the
     constant `native_control` reads too) and classes `hue` and `other`; `expand_group` from the bridge cache's
     `bridge/groups` (every Z2M adapter has `native_groups`, so it serves classing and the fallback only);
     `IntegrationInfo.default_class`, `other` on Z2M, refused at Verified by the registry; scopes `hue` Verified and
     `other` `experimental(...)` on every adapter. Core, after the plan review: an empty group is skipped, and a group
     with an Experimental member falls back while the option is off (Decision 8). `models.yaml`'s TS011F entry stays
     until part 2, which moves it into code per Decision 16.
   - **Part 2, from the probe:**
     - **Classes and rules,** each class added with its rule, from §1's registry strings and §3's Tuya signal:
       `tuya_zcl`, `tuya_mcu`, and `sengled` if the Sengled needs its own. If a signal is only in `bridge/devices`
       (not the registry), that is a structure change, and goes to Jason with the open question above. Each class
       gets its `devices.yaml` phrase and section and its `index.md` entry, and the cards are regenerated.
     - **Scope entries:** `tuya_zcl` and `tuya_mcu` left out of `turn_on_for` (not supported: Z2M turns `on_time`
       into a plain On that never ends); `tuya_mcu` left out of `get_attributes` and `set_attributes` too (Decision
       12); the rest `experimental(...)`. The TS011F per Decision 16: a `doesnt_work` entry on `turn_on_for`, matched on the model, holding with
       Experimental on. `sengled` on `turn_on_for`: out of scope (not supported). The
       E11-G13 already refuses 0x42 with `UNSUP_CLUSTER_COMMAND` on ZHA (`zha/internals.md`, Groups), so §4 only
       confirms it on Z2M. `sengled` while off: the E11-G13 jumps to full on `MoveToLevel` while off (research §3),
       so check every adapter that sends it from off (the raise ramps, `turn_on_for` with brightness), not just
       `set_attributes`, whose state the Sengled lab run decides (Decision 13).
     - **Documented for `other`,** settled per adapter, not only `stop`, once the Tuya rules take those devices
       out of `other`.
     - **Group members with no light:** `expand_group` answers None (membership unknowable) when some member
       address resolves to no device or no entity in the group's domain, so a switch-exposed Tuya plug in a Hue group
       can't leave the group Verified on its Hue members alone. (When none resolves, it already answers None.)
     - **Docs:** `docs/evidence.md`'s tenets example (the TS011F), the `docs/integrations.md` Zigbee plugs and
       switches line, `docs/models.yaml`'s TS011F reason (or the entry's move into code, one per integration), the
       TS011F and Sengled model IDs in `docs/models.yaml`, and `index.md`'s Other brands paragraph on Tuya and
       Sengled.
     - **Restore the class names** in `docs/evidence.md` (Device classes, and the coverage check's example) and
       `docs/design.md` (glossary, Class) once the Tuya classes land: they name only `hue` and `other` until then.
     - **TODO and the rerun:** tick step 5 in `TODO.md`, and the lab run below, which restores the card's Works
       line and clears the ten coverage warnings.
   - **Part 2, built** (`claude/device-classes-5-z2m`), the lab run pending. The TS011F's `doesnt_work` is keyed by
     a class of its own, `tuya_ts011f`, as `tuya_zcl` stays not supported on `turn_on_for` and a scope entry is per
     class. `other` is Documented on `raise_brightness`, `lower_brightness` and `stop`, and `experimental(...)` on the
     rest (argued in the PR body). The `sengled` audit of Level commands sent from off is in the Z2M `internals.md`
     (`classes`); no behavior changed beyond the scopes. Fix-ups from its plan review and audit, and Decisions 19 to
     21, land before part 3.
   - **Part 2's fix-ups, built** (same branch): Decisions 20, 23, 24 and 25. The Tuya classes are doesn't work on
     `turn_on_for`; a `gledopto` class, `experimental(...)` everywhere; a group member with a device but no entity in
     the group's domain is classed by its device (a stand-in no action reaches), and only a member with no device
     leaves membership unknown; Z2M `turn_on_for` refuses, as `class_unknown` and with Experimental features on, a
     group of unknown membership and a device only the registry placed in `other` (or a group with one). The rule
     order's one home is the `classes.py` docstring.
   - **Part 2's fix-ups, rounds 2 and 3, built** (same branch), from each round's plan review and audit, and
     Decision 26. On Z2M `turn_on_for` a device is placed by one rule (round 3, replacing round 2's `classes.FINAL`):
     bridge/devices lists it with `interview_completed` true and both Zigbee strings non-empty, or the registry puts
     it in `hue`, the one class no Zigbee-string rule can override. Every other device waits, so a Gledopto-badged
     Tuya device in `gledopto`, a Sengled, and a registry-only Tuya device (now `class_unknown` rather than
     `doesnt_work`, a refusal either way) wait for the strings, and part 3's countdown class will too.
     `class_unknown` is core's `Reason`, with its own translation, saying it clears once the integration has
     reported the device (usually seconds; a reload if it never does), and always naming the integration's title;
     `docs/action-behavior.md` calls it transient. The harm rule's wording in
     `docs/evidence.md`, `docs/design.md` and the tenets says what it requires: a class resting on information
     DeviceSide doesn't have yet. The Tuya entries' reason says what the user sees.
   - **Part 3, the Tuya countdown on Z2M (Decision 22),** after part 2's fix-ups:
     1. **Read first:** in zigbee-herdsman-converters, whether every definition that exposes `countdown` uses one
        converter (the same attribute and units). If it does, one class covers them all; if not, a class per
        converter. Record it in the Z2M `internals.md`, citing the source.
     2. **Probe** ([spec below](#step-5-part-3-probe-spec)), on the lab TS011F on Z2M, before any code. Its answers
        settle the read-back and the cancel behavior.
     3. **Classes:** the class rule matches the `countdown` expose in `bridge/devices` (Z2M's own statement that the
        device takes the call), not a firmware string. A Tuya device without it keeps today's refusal. While the
        cache is empty, the registry fallback can't see exposes, so the device keeps today's refusal until it fills.
     4. **The call:** a branch in Z2M's `turn_on_for` for the class, `{"state": "ON", "countdown": N}` (or as the
        probe shows), with no `on_time`. Brightness with it follows the probe.
     5. **Duration limits per class** (from Deferred): whole seconds, 1 to 43,200 s unless the probe shows other
        bounds; out of range raises as core does, rounding is reported in `clamped_to`. Core change, so an Opus
        audit.
     6. **Read-back** per the probe: `confirmed` only if the device reports the countdown; otherwise `sent`.
        `get_attributes` time left likewise.
     7. **Level:** `experimental(...)` until the lab run, then Verified for the class the record covers. The cards
        and `index.md` move the TS011F from Doesn't work.
     8. **Lab run:** the TS011F on Z2M, added to step 5's lab run.
6. **Core fix-ups** from step 5's run, before ZHA and Hue copy the pattern. If a package changes, its lab run is
   repeated.
   - **The harm rule moves to core (Decision 26):** a `harms` flag on `DoesntWork`, and the classifier answering
     `None` for "can't place yet"; the dispatcher then refuses `class_unknown` for an adapter whose scope holds a
     `harms` entry, and Z2M's `supports()` check goes.
   - **"Not placed yet" gets its own sentinel** (a pending class, or a flag on the target's classes), not `None`.
     `None` already means a permanent "can't class": a Shelly entity with no RPC component, a classifier that raised,
     a group whose members can't be listed. Core then refuses a pending target only where the adapter's scope has a
     harm-based `doesnt_work` entry.
   - **A group with one unplaced member falls back to its members** in core, with only that member refused
     (`class_unknown`), instead of the whole group being refused as Z2M's `supports()` does today.
7. **ZHA.** The same shape. Lab run, with the Sengled.
8. **Hue.** Classes `hue`, `other`, `bridge_v1` (not supported) and `smart_scene` (doesn't work, on `set_scene`).
   `other` is decided per action (Decision 2). Lab run, with the Sengled on the bridge if it pairs. The `bridge_v1`
   refusal is unit-tested only.
9. **Matter.** Lab run on the WiZ plug.
10. **Close out.**
    - The house run: Shelly's switch devices, plus the color bulb unless it already ran in the lab.
    - Repeat any run on the list below.
    - Remove `models.yaml`'s shared `doesnt_work`, regenerate the README, and tick TODO.
    - Then, with Jason's go-ahead, comment on #350: the classes it will need to leave `experimental`, and what its
      manual records should note (the Director's driver or proxy type).

### Step 3: probe spec

A runbook for the lab, at a Home Assistant instance with Developer Tools, the Z2M frontend and ZHA's device pages.
N is 10 s throughout: short, and a 10× misreading (100 s) still lands inside a 125 s watch.

**The repo is public.** Record only vendor, model, firmware and cluster strings, Tuya's `_TZ3000_…`-style
manufacturer strings included (they name a firmware, not a unit). Never a device name, friendly name, entity ID,
IEEE or MAC address, network address, serial number or hostname. Z2M and ZHA logs carry the IEEE and friendly name on
every line: transcribe values into the tables, never paste a log line. `<device>`, `<ieee>` and `<entity>` below are
placeholders, typed at run time and never written down.

#### 0. Prepare

1. Note the versions for every table: Home Assistant (Settings > About), Z2M and zigbee-herdsman-converters (Z2M
   frontend > Settings > About).
2. Z2M debug log, in Developer Tools > Actions (YAML mode):
   ```yaml
   action: mqtt.publish
   data:
     topic: zigbee2mqtt/bridge/request/options
     payload: '{"options": {"advanced": {"log_level": "debug"}}}'
   ```
   Set it back to `info` the same way when done.
3. ZHA debug log:
   ```yaml
   action: logger.set_level
   data:
     homeassistant.components.zha: debug
   ```
4. Two lab-only scripts (Settings > Automations & scenes > Scripts > Add > Edit in YAML; save them as
   `script.probe_onoff_z2m` and `script.probe_onoff_zha`; delete them after). Each sends the pre-state, waits 5 s,
   sends the case's command, then polls once a second for `seconds`.
   ```yaml
   alias: probe_onoff_z2m
   mode: single
   fields:
     device: {description: "Z2M friendly name or IEEE"}
     pre: {description: "ON or OFF"}
     command: {description: "The case's payload, as a mapping"}
     seconds: {description: "Reads after the command", default: 125}
   sequence:
     - action: mqtt.publish
       data:
         topic: "zigbee2mqtt/{{ device }}/set"
         payload: '{"state": "{{ pre }}"}'
     - delay: 5
     - repeat:
         count: 3
         sequence:
           - action: mqtt.publish
             data:
               topic: "zigbee2mqtt/{{ device }}/set"
               payload: '{"read": {"cluster": "genOnOff", "attributes": ["onOff", "onTime", "offWaitTime"]}}'
           - delay: 1
     - action: mqtt.publish
       data:
         topic: "zigbee2mqtt/{{ device }}/set"
         payload: "{{ command | to_json }}"
     - repeat:
         count: "{{ seconds | int(125) }}"
         sequence:
           - action: mqtt.publish
             data:
               topic: "zigbee2mqtt/{{ device }}/set"
               payload: '{"read": {"cluster": "genOnOff", "attributes": ["onOff", "onTime", "offWaitTime"]}}'
           - delay: 1
   ```
   ```yaml
   alias: probe_onoff_zha
   mode: single
   fields:
     ieee: {description: "The device's IEEE, from its Zigbee info card"}
     endpoint: {description: "OnOff endpoint: 1 on the TS011F, 11 on Hue bulbs", default: 1}
     entity: {description: "Its switch or light entity"}
     pre: {description: "on or off"}
     params: {description: "on_off_control, on_time, off_wait_time, as a mapping"}
     seconds: {description: "State polls after the command", default: 125}
   sequence:
     - action: zha.issue_zigbee_cluster_command
       data:
         ieee: "{{ ieee }}"
         endpoint_id: "{{ endpoint | int(1) }}"
         cluster_id: 6
         cluster_type: in
         command_type: server
         command: "{{ 1 if pre == 'on' else 0 }}"
         params: {}
     - delay: 5
     - action: zha.issue_zigbee_cluster_command
       data:
         ieee: "{{ ieee }}"
         endpoint_id: "{{ endpoint | int(1) }}"
         cluster_id: 6
         cluster_type: in
         command_type: server
         command: 66
         params: "{{ params }}"
     - repeat:
         count: "{{ seconds | int(125) }}"
         sequence:
           - action: homeassistant.update_entity
             target: {entity_id: "{{ entity }}"}
           - delay: 1
   ```
   Run one with `script.turn_on`, so Developer Tools doesn't block for two minutes:
   ```yaml
   action: script.turn_on
   target: {entity_id: script.probe_onoff_z2m}
   data:
     variables:
       device: "<device>"
       pre: "OFF"
       command: {command: {cluster: genOnOff, command: onWithTimedOff, payload: {ctrlbits: 0, ontime: 100, offwaittime: 0}}}
   ```
5. Where each reading comes from:
   - **Z2M:** each read answers in the Z2M log (frontend > Logs, filtered on the device, or the add-on or container
     log) as `Received Zigbee message from '…', type 'readResponse', cluster 'genOnOff', data '{"offWaitTime":…,
     "onOff":…,"onTime":…}'`. t = 0 is the log's `Received MQTT message on 'zigbee2mqtt/…/set'` line carrying the
     case's command. A command the device refuses logs `Publish 'set' '<key>' to '…' failed: …` with the ZCL status.
     If the three-attribute read itself fails, read each attribute on its own.
   - **ZHA:** state from the entity's history (the script polls it each second). `on_time` and `off_wait_time` by
     hand: device page > ⋮ > Manage Zigbee device > Clusters > the OnOff endpoint, OnOff (0x0006) > Attributes >
     Read. The script makes no baseline reads, so read both there before starting it, then at t ≈ 3 s, t ≈ 15 s and after each state change. The command's status is in HA's log on the
     `homeassistant.components.zha` debug line for the issued command. t = 0 is that line.

#### 1. Registry strings

1. Developer Tools > Template, paste:
   ```jinja
   | integration | manufacturer | model | model_id | sw_version | hw_version | domains |
   |---|---|---|---|---|---|---|
   {%- for integ in ['hue', 'zha', 'mqtt', 'matter'] %}
     {%- set seen = namespace(ids=[]) %}
     {%- for e in integration_entities(integ) %}
       {%- set d = device_id(e) %}
       {%- if d and d not in seen.ids %}{% set seen.ids = seen.ids + [d] %}{% endif %}
     {%- endfor %}
     {%- for d in seen.ids %}
       {%- set z = namespace(z2m=false) %}
       {%- for i in device_attr(d, 'identifiers') %}
         {%- if i[0] == 'mqtt' and i[1].startswith('zigbee2mqtt_0x') %}{% set z.z2m = true %}{% endif %}
       {%- endfor %}
       {%- if integ != 'mqtt' or z.z2m %}
   | {{ 'zigbee2mqtt' if integ == 'mqtt' else integ }} | {{ device_attr(d, 'manufacturer') }} | {{ device_attr(d, 'model') }} | {{ device_attr(d, 'model_id') }} | {{ device_attr(d, 'sw_version') }} | {{ device_attr(d, 'hw_version') }} | {{ device_entities(d) | map('regex_replace', '[.].*', '') | unique | sort | join(', ') }} |
       {%- endif %}
     {%- endfor %}
   {%- endfor %}
   ```
   It prints one row per device: the five registry fields and its entity domains, never a name, ID or address.
   MQTT rows are kept only for Z2M devices (identifier `zigbee2mqtt_0x…`), which drops Z2M groups, the Z2M bridge
   and other MQTT devices. Bridges, rooms and coordinators that have entities show too: drop those rows.
2. Copy the rows for the lab devices into this table, adding a generic role (`Hue bulb`, `TS011F plug`, `WiZ plug`,
   `Sengled bulb`), never the device's name:

   | Date | Network | Role | manufacturer | model | model_id | sw_version | hw_version | Versions (HA, Z2M/ZHC) |
   |---|---|---|---|---|---|---|---|---|
   | | | | | | | | | |

3. For each Zigbee device, also note what the stack shows beyond the registry:
   - Z2M frontend > the device > About: *Zigbee model*, *Zigbee manufacturer*, the definition's model and vendor;
     Exposes tab: whether `countdown` is listed.
   - ZHA device page > Zigbee info: the *Quirk* line, and the `quirk_id` where shown; Manage Zigbee device >
     Clusters: endpoint 1's input clusters (ids only); the node descriptor's `manufacturer_code` (Zigbee info, or
     the device's ZHA diagnostics download, `node_descriptor`). All of these are readable sync via `zcl.gateway`
     (`zha/zcl.py`), with no I/O.

   | Network | Role | Zigbee model | Zigbee manufacturer | Definition model / vendor (Z2M) or quirk (ZHA) | `quirk_id` (ZHA) | `manufacturer_code` (ZHA) | Endpoint 1 in-clusters (ZHA) | `countdown` expose (Z2M) |
   |---|---|---|---|---|---|---|---|---|
   | | | | | | | | | |

4. For the Hue bulbs, and the Sengled if the bridge takes it: aiohue's `product_data` per light, `certified` and
   `product_archetype` (the bridge's CLIP v2 `device` resource, or the Hue integration's diagnostics download).
   For the WiZ plug: from the node cache (the Matter integration's diagnostics download for the device), the Basic
   Information VendorID and ProductID, and the OnOff FeatureMap (`<endpoint>/6/65532`, which `matter/addressing.py`
   already reads).

   | Network | Role | `certified` / `product_archetype` (Hue) | VendorID / ProductID / OnOff FeatureMap (Matter) |
   |---|---|---|---|
   | | | | |

5. Groups: one each of a Z2M group, a ZHA group and a Hue room. Decision 6 reports a group's class as its members'
   classes, so steps 5, 7 and 8 need to know what a group target looks like to `capability()`. In Developer Tools >
   Template, with the group's light entity typed in:
   ```jinja
   {%- set d = device_id('<entity>') %}
   {{ 'yes' if d else 'no' }} | {{ device_attr(d, 'manufacturer') if d else '' }} | {{ device_attr(d, 'model') if d else '' }}
   ```
   Members resolve when each member the group's own page lists (the Z2M frontend's group page, ZHA's group page, the
   Hue app's room) has a device entry in HA, found by the same template on the member's light entity. Record yes or no
   and the count, never the entities. DeviceSide's `get_attributes` can't show this: on Z2M a group answers as one
   averaged entry. Z2M registers its groups with manufacturer `Zigbee2MQTT` and model `Group`. No group
   IDs.

   | Network | Group | Has a device entry | manufacturer | model | Members resolve (count) |
   |---|---|---|---|---|---|
   | Z2M | group | | | | |
   | ZHA | group | | | | |
   | Hue | room | | | | |

#### 2. The TS011F probe (Decision 3)

**Networks.** Run the cases on Z2M, where the reads can be scripted once a second. ZHA C1 on the TS011F is already a
rig fact (`zha/internals.md`, `get_attributes`: it counts OnTime down while off), so re-pair the plug to ZHA (on most
TS011F, hold the button about 5 s until the LED flashes fast) only if a case works on Z2M, and run that case there.
ZHA's `TuyaZBOnOffAttributeCluster` passes 0x42 through raw (research, §2), so the plug gets the same frame.

**Which cases.** C1, C2, C5 and C6, plus C7 if the device exposes `countdown`. C3 and C4 only if C5 fails.

**Cases.** Each starts from a baseline: the script's three reads before the command must show `onTime` 0 and
`offWaitTime` 0. If not, wait until they do or 130 s pass, then power-cycle the plug (10 s unplugged) and rerun the
case. 0x42 payload fields are `(on_off_control, on_time, off_wait_time)`; ZCL units are tenths.

| Case | Pre | 0x42 fields | What it tests |
|---|---|---|---|
| C1 | off | `(0, 100, 0)` | DeviceSide's call: 10 s in tenths |
| C2 | off | `(0, 10, 0)` | 10 s in whole seconds |
| C3 | off | `(0, 0, 10)` | off wait only |
| C4 | on | `(0, 10, 0)` | on, then `on_time` in seconds |
| C5 | on | `(0, 10, 10)` | Tuya's recipe: plain On, then both fields in seconds |
| C6 (Z2M) | off | none: publishes `{"state": "ON", "on_time": 10}` | DeviceSide's actual Z2M publish |
| C7 (Z2M, only with a `countdown` expose) | off | none: publishes `{"state": "ON", "countdown": 10}` | Z2M's own Tuya converter |

Z2M `command` mapping per case (the script's `command` variable; `pre` is `"OFF"` or `"ON"`):

- C1: `{command: {cluster: genOnOff, command: onWithTimedOff, payload: {ctrlbits: 0, ontime: 100, offwaittime: 0}}}`
- C2: the same with `ontime: 10, offwaittime: 0`; C3: `ontime: 0, offwaittime: 10`; C4: `ontime: 10,
  offwaittime: 0` with `pre: "ON"`; C5: `ontime: 10, offwaittime: 10` with `pre: "ON"`.
- C6: `{state: "ON", on_time: 10}`; C7: `{state: "ON", countdown: 10}`.

ZHA `params` per case (the script's `params` variable; `pre` is `off` or `on`):
`{on_off_control: 0, on_time: 100, off_wait_time: 0}` for C1, and the table's fields for the rest.

**C6, did the converter drop `on_time`?** The research found TS011F_plug_1 has no `on_time` converter (§2, §3 Tuya
row). It did if the Z2M log shows `No converter available for 'on_time'` after the publish and the herdsman debug
lines that follow name only `genOnOff.on`, no `onWithTimedOff`. If no `zh:` lines show, set
`advanced.log_namespaced_levels` for `zh` to `debug`. For C7, note which ZCL commands those debug lines
name, in order, with their fields.

**Results**, one row per case and network. Outcome is one of: `works` (on within 2 s of the last call, off by itself
at 10 ± 2 s), `none`, `delayed on at t`, `delayed off at t`, `on, never off`, `rejected <status>`.

| Date | Network | Case | Status (log) | Baseline onTime / offWaitTime | First read after: onTime / offWaitTime | Counts down per s (by 10, by 1, no) | State changes (t s: state) | State at 125 s | Outcome |
|---|---|---|---|---|---|---|---|---|---|
| | | | | | | | | | |

For the research file, also each case's reads where any value changed, plus the first and last: `t | onOff | onTime |
offWaitTime`.

**Decision rule** (Decision 3):

1. **C1 works** on Z2M: this contradicts the rig's "never switches on". Repeat C1 twice; if it holds,
   stop and take it to Jason, as Decision 3 doesn't cover it.
2. **Else, any of C2–C5 or C7 works:** the TS011F is `tuya_zcl`, not yet supported on `turn_on_for` (pattern B,
   deferred), and its doesn't-work entry goes. Record which case worked and its unit: that is the recipe pattern B
   would build.
3. **Else, nothing works:** the doesn't-work entry stays, one code entry each for ZHA and Z2M. Its reason is C1's
   outcome on that network; on Z2M, C6's converter drop if the log shows it.
4. **Z2M and ZHA disagree on the same case:** record both and take it to Jason (tenet 4).

#### 3. The Tuya detection signal

What the research and the stacks' code suggest, to confirm or correct ([inferred] until a lab row shows it):

| Network | Tuya ZCL (TS011F, TS000x, white labels) | Tuya MCU (TS0601) | What the lab confirms |
|---|---|---|---|
| ZHA | registry manufacturer `_TZ3000_…` / `_TZ3210_…` / `_TYZB01_…`, model `TS011F` / `TS000x`; a `zhaquirks.tuya` quirk (quirk and clusters readable sync via `zcl.gateway`) | model `TS0601`, manufacturer `_TZE2xx_…`; cluster `0xEF00`; a `zhaquirks.tuya.mcu` or quirk-builder quirk | which fields hold `TS011F` and `_TZ3000_…` |
| Z2M | registry carries Z2M's definition (vendor, model e.g. `TS011F_plug_1`); `bridge/devices` holds the Zigbee `model_id` and `manufacturer` and the exposes (`countdown`), sync from `bridge_cache.py` | `bridge/devices` `model_id` `TS0601`, manufacturer `_TZE2xx_…`; DP exposes such as `countdown_l1` | which fields hold `TS011F` and `_TZ3000_…` |

The lab's TS011F confirms, from §1's tables: which registry field carries `TS011F` and the `_TZ3000_…` string on each
network; whether Z2M's registry lacks them (so a Z2M rule needs `bridge/devices`); whether its definition exposes
`countdown`; and its ZHA quirk. Inferred, staying so: every TS0601 signal, white-label vendor strings on Z2M,
TS000x and TS0726, and the `_TZ3000_cicwjqth` TS011F, whose definition has no `countdown` (research §3).

#### 4. The Sengled

1. Pair it on Z2M, then ZHA, then the Hue bridge (Hue app > add lights). A bridge that won't take it: record "doesn't
   pair" and the bridge's software version.
2. On each network it joins, run §1 and add its rows.
3. On Z2M: C1 and C6, with `<device>`. Skip ZHA: the E11-G13's ZHA C1 is already a rig fact (`UNSUP_CLUSTER_COMMAND`
   by unicast, `zha/internals.md`, Groups). The research expects the same rejection on Z2M and no state change. If
   any case is accepted or the bulb changes state, run C2 and C5 as well. Use §2's results table. Its ZHA step is
   only §1's template read, as it is already paired there.

#### 5. Where results go

Results land on step 3's branch, before any classifier reads them. Each is a dated fact under `## Tested behavior`,
citing a dated research file that holds the filled tables (`research/<date>-lab-probe.md`, never edited after). A
refusal sends no call, so these are research files, not rig fact tests (`docs/evidence.md`, tenet 2). Per CLAUDE.md's
"Where facts go", a firmware fact that spans ZHA and Z2M, such as the TS011F's response to 0x42, goes in the shared
`docs/internals.md`; each bundle's `internals.md` keeps only its own network's rows. The TS011F and Sengled model IDs
join `docs/models.yaml`, per its header.

| Result | Bundle(s) | Consumed by |
|---|---|---|
| Registry strings and groups | `hue`, `zha`, `zigbee2mqtt` | the `hue` class rules: step 5 (Z2M), 7 (ZHA), 8 (Hue) |
| Registry strings, WiZ plug | `matter` | step 9 |
| TS011F response to 0x42 | shared `docs/internals.md`; `zigbee2mqtt`, `zha` keep their own network's rows | step 4 (the `doesnt_work` entry shape), steps 5 and 7 (the TS011F's entry, per Decision 16) |
| C6 converter drop | `zigbee2mqtt`, under Upstream defects if confirmed | step 5 (the Z2M reason) |
| Tuya signal | `zigbee2mqtt`, `zha` | the `tuya_zcl` / `tuya_mcu` rules: steps 5 and 7 |
| Sengled strings and 0x42 | `zigbee2mqtt`, `zha`, `hue` | the `sengled` rule and its `turn_on_for` state: steps 5, 7, 8 |
| TS011F and Sengled model IDs | `docs/models.yaml` | steps 5, 7, 8 (the cards) |

### Step 5 part 3: probe spec

On Z2M, the lab TS011F (`_TZ3000_0zfrhq4i`), from Developer Tools, MQTT publish to `zigbee2mqtt/<plug>/set`; times
from Z2M's log. Known already (lab probe, C7): `{"state": "ON", "countdown": 10}` from off turns it on and off 10 s
later. One row each; record the Z2M state payload after each publish.

| Case | Pre-state | Publish | Answers |
|---|---|---|---|
| P1 | on | `{"countdown": 10}` alone | whether a countdown starts without `state` |
| P2 | off | `{"state": "ON", "countdown": 30}`, then at 5 s `{"countdown": 10}` | replace or extend: off at ~15 s or ~35 s |
| P3 | off | `{"state": "ON", "countdown": 30}`, then at 5 s `{"state": "OFF"}`, then `{"state": "ON"}` | whether off cancels it: still on after 35 s? |
| P4 | off | `{"state": "ON", "countdown": 30}`, then at 5 s `{"countdown": 0}` | whether 0 cancels it |
| P5 | off | `{"state": "ON", "countdown": 30}`, then at 5 s the plug's button twice | whether a local toggle cancels it |
| P6 | off | `{"state": "ON", "countdown": 30}`, then at 5 s `{"countdown": ""}` on `/get` | whether time left can be read, and whether state reports it unprompted |
| P7 | off | `countdown` 1, then 43200, then 43201 | the bounds: accepted, rejected or clamped (cancel each with P3 or P4's way) |

Results go in a dated research file, `zigbee2mqtt/research/<date>-tuya-countdown-probe.md`, and the facts in the Z2M
`internals.md`.

### Runs to repeat

A package changed after its lab run needs that run again before the release. None yet.

## Deferred until something needs it

- Firmware matching in the classifier: needed the first time a firmware boundary decides behavior, such as the Sonoff
  ZBMINIR2 at 1.1.0.
- Building Tuya pattern B on ZHA and pattern C, and a non-Hue bulb on the rig. The second lifts that maker's own class on Z2M and
  ZHA, never `other` (Decision 5).
- Tuya's legacy `_TYST11_…` manufacturer strings: the Tuya rules don't match them, so such a device with a model
  that isn't `TS…` lands in `other`. Check how many devices that covers before adding a rule.
- A query's own `capability` or `capabilities` data key: the class would overwrite it in the response. No query has
  one; a check is built when one would.

## Decisions (Jason, 2026-11-14)

1. **Lands before v0.1.0.** Features that non-Hue bulb owners on Z2M, ZHA and Hue use unflagged today move behind
   `experimental` in v0.1.0.
2. **Third-party lights on a Hue bridge get the `other` class.** Step 8 decides per action whether `other` is flagged or
   Documented, by an Opus model's judgment, and argues each choice in the PR body. The bar is `docs/evidence.md`'s
   Documented test: same capability, differing only in addressing, and no known class of devices departs.
3. **Probe the TS011F before calling it "doesn't work".** The research suggests it may run the timer, but read our
   tenths as whole seconds: a 15 s request toggled one after 150 s. Before steps 5 and 7 settle its entry, probe one on
   the rig. Try off then 0x42 `{on_time N, off_wait 0}` in tenths and in seconds, and the Tuya recipe: plain On, then
   `{N s, N s}`. Read state and `onTime` each second. Record the result in `internals.md`. If a recipe works, the
   TS011F is `tuya_zcl`, not yet supported (pattern B, deferred), not doesn't work. Its doesn't-work entry stays only
   if no recipe works. Whichever way it goes, the shared entry becomes one code entry per integration (ZHA and Z2M),
   which `gen_compat` merges on the cards.
4. No release-note line: DeviceSide isn't widely released yet.

## Decisions (Jason, 2026-11-14, during steps 1–2)

5. **The default class is never Verified.** `other` can be Documented per action where `docs/evidence.md`'s Documented
   test holds (no known class departs), and is otherwise `experimental(...)`. A record on a non-Hue device lifts a new
   named class for its maker (for example `ikea`), not `other`. Step 4 writes the rule into `docs/evidence.md`.
6. **The class a call used is reported in its response.** `IntegrationInfo.capability(hass, target)` stays as
   designed. Each target's result carries the class the dispatcher used: a command's beside `status` and
   `integration`, a query's in its entity's data, a native group's as its members' classes. The rig records that
   instead of classifying a stand-in target, so a record holds the class the call really reached. Step 4 adds the
   field; the rig then reads it (ha-rig, after step 4).
7. **The coverage check warns during development and fails at release.** `gen_compat --check` prints each gap on
   every PR, so a developer sees it early. `check_release.py` fails on it at `cut-release`, with the other release
   gates. No allowlist is needed while records catch up (Shelly's house rerun).

## Decisions (Jason, 2026-11-14, during step 4)

8. **A native group with a member that doesn't work or isn't supported falls back to its members** when the
   integration has `expand_group`: each member is judged alone, the good ones run, and the refused one is reported
   refused. With no `expand_group` the group can't be judged by member, so it counts as Experimental: refused while
   Experimental features is off. **Amended during step 5:** a member whose class is Experimental falls back the same
   way while Experimental features is off: the other members run one by one, and the flagged one is skipped with
   `experimental_off`, raised only if it was named itself. With the option on, the group runs whole, as one command.
   An empty group is skipped silently (docs/action-behavior.md), never gated. Amended (audit): a group that falls
   back has no result of its own; with no member planned it is refused with the fallback's reason. Mechanics:
   design.md (Groups).
9. **Shelly `set_attributes` on Light, CCT, RGB and RGBW** is Documented if Shelly's Gen2+ docs say, alike for those
   components, that a `Set` without `on` leaves the output as it is; otherwise `experimental(...)`. RGBCCT stays
   Verified.
10. **Z2M groups get sync member listing in step 5**, from the bridge cache's `bridge/groups`.
11. **The "ZHA bulb" is the Sengled E11-G13**, already paired on ZHA, sharing one lab fixture with the A60 and the
    Shelly color bulb.

## Decisions (Jason, 2026-11-14, during step 5)

12. **`tuya_mcu` is not supported on `get_attributes` and `set_attributes`:** they send raw ZCL reads and writes,
    which a TS0601 can't take.
13. **The Sengled's `set_attributes` state** is decided by the Sengled lab run.
14. **Parts 1 and 2 of step 5 ship as one PR.**
15. **The Z2M card's Works line is lost until the lab rerun** fills the Capability column: accepted.

## Decisions (Jason, 2026-11-14, after the lab probe)

16. **The TS011F: one fact, a `doesnt_work` entry for today's call, no firmware split.** This replaces Decision 3's
   "its doesn't-work entry goes".
   - The probe tables live in the dated research file (`zigbee2mqtt/research/2026-11-14-lab-probe.md`).
     `docs/internals.md` holds one cross-network fact listing both behaviors and citing it. It says the two firmwares
     were on different networks, so the cause is firmware or path, not proven firmware. The model ids go in
     `docs/models.yaml`.
   - The class rule matches the model (`TS011F`), never the `_TZ3000_…` string, so firmwares nobody has probed
     inherit the refusal. Add a firmware rule only if a later probe finds a different call that works.
   - Today's call is refused by a `doesnt_work` entry whose source is the research file: on ZHA, 0x42 in tenths from
     off; on Z2M, `{"state": "ON", "on_time": N}`. Marking it untested isn't enough, because an untested class runs
     the default call once Experimental features is on. That call switches a `_TZ3000_typdpbpg` plug on about 10×
     later than asked, and on Z2M it leaves the plug on with no timer. A unit test pins that the refusal holds with
     Experimental on.
   - The reason users see names no firmware; it says what they see, e.g. that its timer isn't supported yet. The
     firmware detail stays in `internals.md`.
   - There's no separate safety note: the `doesnt_work` entry is the safety mechanism, and the card shows "Doesn't
     work". The switch-on-later finding goes in the PR body as the rule's evidence, and in TODO as a v0.1.0 blocker.
   - Timing: the Z2M entry lands in step 5 part 2. The ZHA entry is the dangerous one; it is planned for step 7 and
     must land before v0.1.0 even if step 7 slips.
   - Building the countdown pattern (a plain On, then 0x42 with N seconds in both fields; Z2M's `countdown` expose on
     Z2M) is separate, later work. It would move the class from "Doesn't work" to Verified.
17. **No same-unit re-pair** of the Z2M TS011F to ZHA.
18. **The Tuya signal on Z2M: the bridge cache first, the registry as fallback.** Answers step 5's open question. The
    classifier reads the Zigbee manufacturer and model from the `bridge/devices` cache DeviceSide already keeps for
    group members, so rebadged Tuya devices are caught (`_TZ3000_…`, `_TZE…`). Until that cache fills after a
    connect, it falls back to the registry: manufacturer `Tuya`, or a `model_id` starting `TS`. Registry-only was
    rejected because a rebadged plug would land in `other` and, with Experimental features on, take today's call
    and stay on. This is a structure change confined to the Z2M package: the classifier reads one cache it already
    has.
19. **`other` stays Documented on `raise_brightness`, `lower_brightness` and `stop`** on Z2M, as part 2 built it:
    they send the same standard ZCL commands as the Verified Hue call, pinned by a unit test. The rest stays
    `experimental(...)`.
20. **`tuya_zcl` and `tuya_mcu` are "doesn't work" on Z2M `turn_on_for`, not "not supported":** today's call turns
    them on with no timer, so they stay on, which `docs/evidence.md` counts as harm. Each gets a `doesnt_work` entry
    with that reason, sourced to the Z2M converter research (`tuya_ts011f`'s, Decision 16, to the lab probe).
21. **The TS011F keeps its own class, `tuya_ts011f`.**
22. **The Tuya countdown on Z2M ships in v0.1.0, as step 5 part 3.** Tuya plugs are popular, the probe showed Z2M's
    `countdown` works, and the lab has the plug. Z2M only: ZHA keeps its refusal (Decision 16), as it would need
    Tuya's raw recipe.

## Decisions (Jason, 2026-11-14, part 2's fix-ups)

23. **On Z2M, `turn_on_for` refuses unknown, even with Experimental features on.** Audit H1, M1 and M2: a TS011F got
    today's call with Experimental on in a group where it had no light entity, in any group before `bridge/groups`
    arrived, and as a rebadged definition before `bridge/devices` arrived. A group member with a device is classed
    by it; `supports()` refuses a group of unknown membership and a device only the registry placed in `other`.
24. **A `gledopto` class.** Gledopto's converter sends GL-S-007ZS and GL-C-009 a plain On, a 1 s sleep, then the
    brightness command (timed-on research, §3), so `other`'s Documented actions (Decision 19) would cover a known
    departure. Registry manufacturer `Gledopto`, any case; `experimental(...)` on every adapter. `other` stays
    Documented on those three.
25. **Two rules in `docs/evidence.md`, Device classes:** split off the exception, not the class; and where a call is
    known to harm, unknown is refused (tenet 2's exception). Z2M applies the second in Decision 23; ZHA, Hue and
    Matter apply it in their own steps (7 to 9).
26. **The harm rule moves to core in step 6,** before ZHA copies the pattern: a `harms` flag on `DoesntWork` marks
    the entries whose call harms the class, and the classifier answers `None` for a target it can't place yet, which
    the dispatcher refuses as `class_unknown` on an adapter with a `harms` entry. Until then Z2M's `turn_on_for`
    applies it in `supports()`.

## Handoff

2026-11-14, step 3 done. Branch `wt/device-classes-3`, worktree `deviceside-wt/device-classes-3`, PR #423.

**Done:** the lab probe, written up in `docs/integrations/zigbee2mqtt/research/2026-11-14-lab-probe.md` and
`docs/integrations/hue/research/2026-11-14-lab-probe-hue.md`, with the facts in `docs/internals.md`, each bundle's
`internals.md` and `docs/models.yaml`; Decisions 16 and 17 (numbered 8 and 9 on that branch); the step's plan review (in its last commit).

**Next:** merged into step 5's branch after steps 4 and 5 part 1; step 5 part 2 follows.

**Open:**
- The Sengled is in the house profile, which is out of bounds until Monday 2026-11-16, so it can't join step 5's lab
  run until it's back in the lab. Its return to the lab ZHA group is unconfirmed: a group-add call timed out and
  wasn't checked. Jason checks the group's members before the next ZHA group case. Working notes: the gitignored
  `notes/lab-probe-2026-11-14/`.
- Step 5's open question (above) is Jason's call.
