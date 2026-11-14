# Evidence

What DeviceSide must know about a device before a feature ships, and what a release needs. This page is the one home
for these rules; other docs point here. Settled in [the evidence-rules plan](plans/2026-11-12-evidence-rules.md);
device classes in [the device-classes plan](plans/2026-11-14-device-classes.md).

## Tenets

1. **User experience is the north star.** Actions make devices do what users expect, quickly. If that holds, nothing
   else matters.
2. **Probe before you build.** When unsure how a device responds to a call, run a cheap probe rather than assume.
   Record what it shows as a fact (the bundle's `internals.md`, dated). Where its source goes depends on whether a
   shipped call depends on the fact staying true:
   - **Yes: a fact test.** The rig re-runs it (`--facts`) when that call changes, and `test-records/_facts.md` keeps
     each dated result. A watched test covers what only a person can see.
   - **No: `research/`.** The fact decided scope or design, and no shipped call depends on it. A dated file, never
     edited; if evidence suggests it has changed, repeat the probe by hand in a new file.

   *Example:* a bulb set to red while off must come on red, with no flash of its old color. HA's state can't show the
   flash, and the call sequence DeviceSide sends decides it, so it's a watched fact test (`set_attributes`,
   `sets_color_while_off`). A Hue bulb's timer can't be stopped through the bridge, so Hue ships no `turn_on_for`. No
   call depends on that, so it lives in `research/`.
3. **Verify, then trust.** A verified fact stands across firmware, HA and code changes until evidence contradicts it.
   Nobody goes looking for things to re-prove.
4. **Surprises reopen facts.** A bug or unexplained behavior triggers a root-cause dive. The finding joins the fact
   base and is then trusted in turn. Log so that the first bug report already holds the diagnosis: what DeviceSide
   sent, what the device answered, and which way DeviceSide went because of it.
5. **Proof follows the call and the class.** Evidence is about how a class of devices responds to a call
   ([device classes](#device-classes)). Any code that sends the same call to the same class inherits it, and unit tests
   pin the call.
6. **Simplify relentlessly.** Prefer fewer calls, fewer round trips and less machinery. A guardrail stays until
   evidence shows the failure it guards against doesn't happen; meanwhile, look for ways to make it cheaper. A new
   rule or guardrail names the user-visible failure it prevents and what it replaces.
7. **Maintainers judge sufficiency.** Tooling records evidence, and maintainers decide what's enough. Gates check that a
   sign-off exists, not how thorough it was.

*Example (tenets 4 and 5):* the rig found that a Tuya TS011F plug doesn't run the Zigbee timed-on timer it is given.
That reopened "a Zigbee switch takes the light's timed-on call", so the `switch` domain stays untested on ZHA and
Zigbee2MQTT `turn_on_for`. A lab probe then root-caused it: the plug runs the timer only Tuya's way, and given the
light's call one plug stayed off while another switched on about ten times later than asked and stayed on
([internals](internals.md#tested-behavior)). Research also found
that even lights given the identical call differ by firmware: Hue replaces a running timer where the spec keeps the longer one,
and some IKEA and Hue models turn on and never off
([2026-11-14](integrations/zigbee2mqtt/research/2026-11-14-timed-on-call-patterns.md)). So a Hue bulb's record proves the
call on the `hue` class, not on every Zigbee light.

## Levels

A scope item (an entity domain, class or field of an adapter) is Verified, Documented, or untested; an untested
item ships only behind `experimental`.

- **Verified:** a maintainer has verified the call on a real device, by rig record or by manual test. How it was
  tested is the maintainer's choice; the rig is a tool, not a requirement.
- **Documented:** a call to another class that differs from a Verified class's call only in addressing, where no
  known class of devices departs. Unit tests pin the payload. A field is never Documented, because it changes the
  call. The PR that claims Documented argues the sibling case, and review checks it. A bug on the Verified sibling
  reopens its Documented siblings in the same fix.

Nothing ships unflagged unless it is Verified or Documented. Built and unit-tested code that the maintainer can't
exercise, and that isn't Documented, ships behind `experimental` as untested. A sign-off
covers only what was exercised; it never signs off untested code.

**Contrary evidence:** a report of misbehavior doesn't change the level. The maintainer root-causes it, and the fix,
the updated fact or a demotion lands with the finding.

*Example:* Shelly's `turn_on_for` and `turn_off_for` calls are Verified on the `Switch` and `RGBCCT` components and
Documented on `Light`, `CCT`, `RGB` and `RGBW`: the same call, addressed to another component, which Shelly's API
documents identically (`integrations/shelly/addressing.py`).

## Device classes

A **class** is the set of devices that get the same call and run the same firmware family. The integration declares
its classes (`IntegrationInfo.classes`) and names each target's (`IntegrationInfo.capability`; in code a scope's
`capabilities`), and every adapter of the integration sees the same one; an adapter maps the classes it serves to its
own branch. A scope holds a level per class, so each
class stands on its own evidence: a record on one class proves nothing about another. On a single-vendor API the class
is what the call addresses (Shelly: the RPC component). On a multi-vendor protocol the call and the firmware family
decide the result together, so the class follows the vendor (Zigbee2MQTT: `hue`, `tuya_zcl`, `tuya_ts011f`,
`tuya_mcu`, `sengled`, `gledopto`, `other`).

- **Unknown is flagged, never Verified.** A device the integration can't place falls in its default class
  (`IntegrationInfo.default_class`), which the registry refuses at Verified in any scope, so placing a device there
  can only make DeviceSide more cautious. Its one exception: where a call is known to harm, a class DeviceSide can't
  name yet is refused ([below](#doesnt-work-not-yet-supported-untested)). The default class may be Documented for an action where the
  [Documented](#levels) test holds (no known class departs), and is otherwise `experimental(...)`. Nothing lifts it: a record on a device outside the named classes adds a new class named for its
  maker (Zigbee2MQTT: `ikea` for an IKEA bulb), as the default class holds every device not yet placed, and one maker's
  record proves nothing about the rest.
- **One axis per integration.** An integration that names classes holds them in every adapter's scope, and only the
  classes it declares; one that names none holds them in none (the registry checks). A new integration names its
  classes, or its PR argues that it has one: that no known device behind it answers the same call differently.
- **One fact, one home, in code.** Classes, levels and doesn't-work entries live in the integration's package, and
  the cards are generated from them; where each piece goes is CLAUDE.md's
  [Where facts go](../CLAUDE.md#where-facts-go).
- **Groups.** A native group takes the weakest level among its members' classes. One with a member that doesn't work
  or isn't supported, or a member whose class is Experimental, while Experimental features is off (the adapter,
  domain and fields not flagged), falls back to its members, each judged on its own, when the integration can list
  them; one it can't list counts as Experimental: refused while Experimental features is off, and with them on where
  a call is known to harm, as its class rests on a membership DeviceSide doesn't have yet
  ([below](#doesnt-work-not-yet-supported-untested); [design.md](design.md#core-contract), Groups).

### Doesn't work, not yet supported, untested

Three ways a class can't take an action, each shown differently on the cards:

- **Doesn't work:** the class fails, or is harmed by, the call DeviceSide sends. The adapter's scope lists it in
  `doesnt_work`, with what, why and a source; the dispatcher refuses it (`doesnt_work`), and the card lists it under Doesn't work. It is per
  adapter, as one class can fail one action's call and answer another's.
- **Not yet supported:** the class needs a call that isn't built. The integration names it, and the adapter's scope
  holds it in neither `capabilities` nor `doesnt_work`. It is refused (`not_supported`) and listed as not yet supported.
- **Untested:** the default call may work. The class is in scope as `experimental(...)` and runs behind the flag.

A class that today's call harms (it acts wrong rather than refusing) is listed as doesn't work for that call, even when
a call that isn't built would work on it. Not yet supported is for a class today's call can't serve but doesn't harm.

- **Split off the exception, not the class.** When devices are known to depart from a class's call, a rule moves them
  into a class of their own, at the level their own evidence supports. The rest of the class keeps its level. The
  whole class drops a level only when no rule can tell the departing devices apart.
- **Where a call is known to harm, a class not yet known is refused.** An adapter whose scope lists a class as
  doesn't work because the call harms it refuses a target whose class rests on information DeviceSide doesn't have
  yet, even with Experimental features on: a group whose membership isn't listed, or a device whose identifying
  strings haven't been received while they could still move it into a harmed class, and a group with such a device
  among its members. A target placed in the default
  class from full information stays Experimental, as does every target of an action no class is harmed by (Unknown
  is flagged, never Verified, above). The refusal is `class_unknown`, and it clears once the integration has
  reported what it lacked. Zigbee2MQTT applies it today ([its page](integrations/zigbee2mqtt/index.md#device-classes));
  the rule moves to core in step 6 of the [device classes plan](plans/2026-11-14-device-classes.md), and ZHA, Hue
  and Matter apply it in steps 7 to 9.

A refused target raises when named and is skipped when it came indirectly, as `supports()` refusals do
([action-behavior.md](action-behavior.md)).

### The split rule

- A different gateway or API is a separate integration, with a `detect` hook on the same `ha_domain`, as Zigbee2MQTT is
  beside MQTT.
- A different device behind the same API is a class.
- A class that needs a different payload is a branch inside the adapter.
- An adapter splits only when a class needs a different result contract. A different duration limit is a per-class
  setting first.

No matcher, per-class setting or card tier is built before an integration needs it (tenet 6): Zigbee2MQTT, the most
varied integration, sets the bar. Classes match on manufacturer and model until a firmware boundary decides behavior.

### The coverage check

Every class an adapter's scope holds as Verified needs a passing record test, or a `_manual.md` row, for that action
that reached it. Records name the class each test reached, and manual rows the classes they cover. A native group's
test credits every class its cell lists, as the one call reached each member; a manual row credits every class it
lists to every action it passed, so a row whose actions reached different classes is split. A dash credits none.
`scripts/gen_compat.py` warns on each gap on every PR, so a developer sees it early, and names it, for example
"zigbee2mqtt turn_on_for: `hue` is Verified but no record reaches it"; `scripts/check_release.py --signoffs` fails on
it at `cut-release`, with the other release gates. Like every gate, it checks that evidence exists, not that it
suffices (tenet 7), and it applies to integrations that name classes.

*Example:* with Zigbee2MQTT's classes `hue`, `tuya_zcl`, `tuya_ts011f`, `tuya_mcu`, `sengled`, `gledopto` and `other`,
and only Hue bulbs in its records, `turn_on_for` can be Verified on `hue` alone; on `other` it is `experimental(...)`,
so an IKEA or Innr bulb runs only with Experimental features on. A record on an IKEA bulb adds an `ikea` class,
Verified on its own evidence, and `other` stays as it is. The Tuya classes, which Tuya's own countdown call drives but
today's call leaves on (Zigbee2MQTT turns it into a plain on), are listed as doesn't work for today's call until the
countdown call is built; the Sengled, which ignores today's call, needs a call that isn't built, so it is not yet
supported.

## The experimental flag

`experimental` is a flag, not a level: the item is not yet judged ready for everyone. In code it is the per-axis
marker `experimental(...)`, beside `verified(...)` and `documented(...)`; `experimental=True` on an `AdapterScope` flags
the whole adapter. A flagged item is refused unless the user turns on the Experimental features option. The dispatcher
is the only gate: adapters never read the option, and turning it on never changes how any other item behaves.

Every new feature (action, integration, domain, capability or field) starts flagged, and soaks behind the flag with
users who opt in. A passing rig record or `_manual.md` sign-off on a flagged item is evidence a maintainer can use to
lift its flag for the next release, skipping that soak; it is not a requirement to lift it. Whether to lift is the
maintainer's judgment, by how complex the feature is and how complete its tests are: a developer may run the first
few rig cases before the full suite exists, so a flagged item may have passing runs and stay flagged. Lifting makes
`experimental(...)` into `verified(...)`, or drops `experimental=True`; a flagged item that qualifies as Documented
becomes `documented(...)` in a PR that argues the sibling case ([Levels](#levels)). Lifting a flag is a change to the
integration's package, so one of its maintainers approves it, as for any change there
([CLAUDE.md](../CLAUDE.md#maintainers)).

For an item no maintainer can test, users' working reports, cited in a maintainer's `_manual.md` sign-off, can verify
it. The maintainer judges how many are enough (tenet 7); nothing counts them in code.

The cards never put a model under Works from a pass on a flagged item while its flag stands (`scripts/gen_compat.py`).

*Example:* Shelly dimming on `Light` is `experimental("Light")`: a user's report of a Gen3 Dimmer jumping on
`DimStop` keeps it from Documented. A passing `raise_brightness` run on a Gen3 Dimmer puts no model under Works and
lets a Shelly maintainer lift the flag, once they judge the tests complete: their PR makes it `verified("Light")`.

## Caveats

`AdapterScope.caveat` holds a reason (an issue URL), set and cleared by a repo maintainer. It is not a level: levels
are about evidence, caveats about risk and people. Each gives one setup warning, one card mark and one release-notes
line:

- **⚠️ Untested fix:** changed, and nobody could test it before the release. It ships live, with a call for testers
  in the release notes. A sign-off clears it, including a maintainer's `_manual.md` line citing a user's working
  report.
- **⚠️ Unmaintained:** nobody is fixing it. It ships live. Cleared when someone fixes the adapter or adopts the
  integration. An integration whose maintainers are all gone gets it on every adapter.

A deprecation gets a hand-written note in that release's notes, not a caveat.

*Example:* a low-risk fix to a broken adapter whose devices no maintainer has lands before the cut. It ships with
⚠️ Untested fix and the issue URL; a user's working report, cited in a maintainer's `_manual.md` line, clears it.

## Release gate

Each integration whose package (`custom_components/deviceside/integrations/<x>/`) differs from the last release tag
needs a sign-off that holds the package as the release does. Any change counts, flags included; a reverted one is
none.

- **A sign-off** is a whole passing rig run or a `_manual.md` line. A run is checked by the Package tree its records
  cite, so a run on a PR branch still counts once the PR squash-merges, and its records can land in the same PR. A
  manual line is checked by its commit, which must be in the release's checkout and hold the package as the release
  does: cite one on `main` (or a `release/` branch), as a PR branch's is gone once it squash-merges, so the line lands
  after the change merges. It may say "no call changed". It comes from one of the integration's maintainers, or a repo maintainer when
  none responds.
- **Prefer the rig.** Where the rig covers an integration, the sign-off is a rig run: it is repeatable and catches what
  a person doesn't. A manual line is the fallback for integrations the rig can't reach, or when the rig is broken; a
  manual sign-off on a rig-covered integration says why in its PR. This is guidance, not a gate check. Rig evidence is
  no separate level: the cards keep their ✔ for rig-tested models, and `--signoffs` prints each sign-off's kind (record
  or manual) for the RC audit.
- Changes outside an integration package (`core/`, `actions/`, the component root, `scripts/`) need no device
  sign-off: unit tests, the RC soak and tenet 5 cover them.
- **RCs need no sign-off.** The gate runs only at `cut-release`. An RC's notes list each integration changed without a
  sign-off, with a link to the device report form, so an RC can carry an unsigned fix for others to test. But
  `cut-release` checks the latest RC's commit, so land the sign-offs or Untested fix caveats before the last RC.
- **No sign-off at the cut:** `cut-release` fails unless every changed adapter of that integration carries ⚠️
  Untested fix; no other caveat, nor the `experimental` flag alone, stands in. A change to a package module that is no
  adapter's (a shared helper) changes every adapter of the package. A repo maintainer picks whichever is most likely
  to work for users (tenet 1): hold the fix back (revert it, or leave it out of the release branch) when the current
  code mostly works; ship it marked ⚠️ Untested fix when the current code is broken and the fix is low-risk; or, when
  it is risky, put it behind `experimental` and mark it ⚠️ Untested fix.
- Records keep a `Covers` row as information if the rig writes one. Nothing checks it.
- **Coverage:** every module under `core/` and `integrations/`, bar `__init__.py`, needs at least 95% line coverage;
  CI checks it.

*Example:* all five integrations changed since v0.0.1, so v0.1.0 needs five sign-offs. A passing Hue rig run whose
Package tree is the release's Hue package signs off Hue; an integration with no change would need none.

### Sign-off format

A rig sign-off is the bundle's test records, `docs/integrations/<x>/test-records/<action>*.md`, when every one cites
the same Package tree and passes: one whole run, whatever commit each record ran at. The rig writes the Package row,
the package's git tree at the run's commit:

    | Source | rig run (`scripts/rig.py`), commit `1a2b3c4` |
    | Package | `0123456789ab` |

*Example:* if `stop.md` fails, or cites a different Package tree than the rest, the records are no sign-off until every
action passes at one package tree; a rerun of `stop` on `main` completes a run made on a PR branch when the package
didn't change between them. A record with no Package row is no sign-off. A manual sign-off is a row of the bundle's
`test-records/_manual.md`, added in a PR: date, commit, tester, model ids (`docs/models.yaml`, never a household's
device names) and what passed.

An integration names classes when it has `IntegrationInfo.capability` (Shelly and Zigbee2MQTT, today). Its records' Tests table has a
Capability column between Target and Result: the class each test's target reached, in backticks, as the action's
result reports it ([action-behavior.md](action-behavior.md#what-a-response-looks-like)). A native group's cell lists
its members' classes once each, sorted, comma-separated. A dash means no class: the target, or a group member,
reached none the integration could name, or the test was skipped; a dash credits no class. Other integrations keep
the three columns. Its `_manual.md` rows likewise have a Capabilities column between Devices and Passed: the classes
the row's tests reached, or a dash.

    | Test | Target | Capability | Result |
    |---|---|---|---|
    | `on_then_off[switch_plug]` | `switch` (switch_plug) | `Switch` | pass |

    | Date | Commit | Tester | Devices | Capabilities | Passed |
    |---|---|---|---|---|---|
    | 2026-11-12 | `1a2b3c4` | @handle | `S3SW-001X8EU`, `S3BL-C010007AEU` | `Switch`, `RGBCCT` | `turn_on_for` |

A change that sends no new call signs off with a row whose Devices (and Capabilities) hold a dash and whose Passed
holds exactly `no call changed`. *Example:* a log-wording change in a Shelly adapter:

    | 2026-11-12 | `1a2b3c4` | @handle | - | - | no call changed |
