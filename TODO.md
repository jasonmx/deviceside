# TODO: road to v0.1.0

What is left before v0.1.0. The design is in [`docs/design.md`](docs/design.md). Tick items off in the PR that
finishes them. The v0.1.0 release PR deletes "To go"; "After v0.1.0" stays until its items are done or filed
as issues.

## Releases

`v0.0.1` is out, and HACS accepted the submission ([hacs/default#12611](https://github.com/hacs/default/pull/12611))
on 4 Nov 2026. v0.1.0 is next: everything lands on `main`, and release candidates are cut from it. It ships under the
[evidence rules](docs/evidence.md): one sign-off per integration changed since v0.0.1. The
[evidence-rules plan](docs/plans/2026-11-12-evidence-rules.md) lifted the freeze at `075895f`; with its step 5
landed, the old gates are gone and RCs can be cut again. The items below run in this order.

## To go

- [x] **RC audit**: `/rc-audit v0.1.0rc1` over `967bec0..HEAD` (rc1 was never tagged), fixes landed (#316). A second
  pass before the RC covers what lands after it.
- [x] **Docs sign-off** by the repo maintainers, before the freeze, as it may change the design: README, `CONTRIBUTING.md`,
  `docs/device-compatibility.md` and its "Help us test", `docs/integrations.md`, `docs/design.md`,
  `docs/action-behavior.md`; the GitHub issue flows end to end (`.github/ISSUE_TEMPLATE/`, and the options flow's
  "Report a device" step as a user meets it in HA); the forum draft (#319–#322). Each design change it raised
  landed before the freeze.
- [x] **Z2M `turn_on_for` late off**: `bulb_transition_then_off[light_dimmable]` was a step-file timing bug (the off
  window closed before DeviceSide's end read), not the bulb or DeviceSide; fixed in ha-rig #47.
- [x] **Freeze** at `075895f`; lifted by the evidence-rules plan.
- [x] **Evidence rules**, one PR per step of the [plan](docs/plans/2026-11-12-evidence-rules.md):
  - [x] 1. Rules, docs only (`docs/evidence.md`).
  - [x] 2. Runtime: two levels, the `experimental` gate, `caveat`; the seven release-flagged adapters lose the flag;
    the code comments citing CLAUDE.md's "Release flag" or "Default to Documented" point to `docs/evidence.md`.
  - [x] 3. Rig (ha-rig): drop the wire and scope taps, `Covers`, observed lending and the firmware-fact ledger.
  - [x] 3b. Step files move to DeviceSide (`tests/rig/steps/`, `tests/integrations/<x>/rig/`), the rig reading them
    from the checkout under test.
  - [x] 4. Release gate: `check_release.py --signoffs`; drop `--release` and the rest of `--records`; the
    docstring's CLAUDE.md citations and CLAUDE.md's Layout line on release checks.
  - [x] 5. Cards: `gen_compat.py` tiers for two levels, the flag and caveats, and the docstring's CLAUDE.md citations.
  - [x] 6. Sign-offs by the Package tree records cite, not commit ancestry, so a run on a PR branch counts (#408).
- [ ] **Device classes**, one PR per step of the [plan](docs/plans/2026-11-14-device-classes.md), before the
  sign-offs below, as it moves non-Hue bulbs on Zigbee2MQTT, ZHA and Hue behind `experimental`:
  - [x] 1. Rules and design, docs only (`docs/evidence.md`, `docs/design.md`).
  - [ ] 2. Rig (ha-rig): the records' Capability column (its format in `docs/evidence.md`, and `gen_compat`'s parser
    taking it, land in DeviceSide alongside); the rig reads the class from each result (the second rig change, given
    to Jason directly), before any rig run on step 4's branch or later; Shelly rerun on it.
  - [x] 3. Lab probe: registry strings, the TS011F, the Sengled
    ([results](docs/integrations/zigbee2mqtt/research/2026-11-14-lab-probe.md)).
  - [x] 4. Core: `IntegrationInfo.capability` and `classes`, `doesnt_work`, `not_supported` (and
    `docs/integrations.md`'s mention); Shelly moves to it; `gen_compat`'s tiers and coverage check (warning), failing
    in `check_release --signoffs`; `_manual.md`'s Capabilities column; "one fact, one home, in code" in
    `docs/evidence.md`, with CLAUDE.md's devices.yaml row; the Not yet supported tier on
    `docs/device-compatibility.md`; how a group test or a manual row credits the classes it lists; the Capability
    column's "until that lands" clause; the class in each result (plan Decision 6); `other` never Verified
    (Decision 5).
  - [ ] 5. Zigbee2MQTT classes, after the TS011F probe and the Tuya detection probe; the first default class, so
    `docs/device-compatibility.md` regains "and so does a device DeviceSide can't place in a known class"; rig rerun.
    Built on `claude/device-classes-5-z2m` (classes `hue`, `tuya_zcl`, `tuya_ts011f`, `tuya_mcu`, `sengled`,
    `gledopto`, `other`; the Tuya classes' `doesnt_work` on `turn_on_for`, which refuses unknown; `other` Documented
    on brightness fades and stop). Open until the lab run on
    the branch tip, with the Sengled, which restores the card's Works line and clears the coverage warnings.
  - [ ] 6. Core fix-ups from step 5's run.
  - [ ] 7. ZHA classes; rig rerun.
  - [ ] 8. Hue classes, the `bridge_v1` and `smart_scene` checks moved to them; rig rerun.
  - [ ] 9. Matter classes; manual or rig sign-off.
  - [ ] 10. Close out: `models.yaml`'s shared `doesnt_work` removed, README regenerated, then the #350 comment.
- [ ] **TS011F refusal on ZHA and Z2M** (device-classes plan, Decision 16): a `doesnt_work` entry for today's
  `turn_on_for` call, holding with Experimental on. Without it, ZHA's 0x42 in tenths switches a `_TZ3000_typdpbpg`
  plug on about 10× later than asked, and Z2M leaves the plug on with no timer
  ([probe](docs/integrations/zigbee2mqtt/research/2026-11-14-lab-probe.md)). Z2M in step 5 (built), ZHA in step 7;
  blocks v0.1.0 even if step 7 slips.
- [ ] **Sign-offs** for v0.1.0, one per integration changed since v0.0.1 (`check_release.py --signoffs`), each a rig
  run from ha-rig `main` (its records carry the Package row; older records count for none). The runs for the formerly release-flagged adapters need these `--profile lab` cases, run
  watched, the A60 visiting each network: Hue `saturation_down_stop`, `halts_brightness_and_saturation` and
  `sets_color_while_off`; ZHA `hue_cycle_stop`, `saturation_down_stop` and `sets_color_while_off`; Z2M
  `halts_a_lower_to_off`, `hue_cycle_stop`, `saturation_down_stop` and `sets_color_while_off`. Unattended alongside:
  Z2M `lower_hue` `cycles_down_the_wheel[group_color]` (#222).
  - [ ] Hue
  - [ ] Matter
  - [ ] Shelly. Accepted until then: `check_release --signoffs` fails on Shelly's class coverage gaps, as its
    records lack the Capability column; no release is cut before its house rerun.
  - [ ] ZHA
  - [ ] Zigbee2MQTT
- [ ] **Release notes**: hand-written v0.1.0 notes in `notes/`, signed off by the repo maintainers, after the RC installs cleanly
  and before `cut-release`, so they describe what the RC ships.
- [ ] **v0.1.0**: `/rc-audit` over the new range, the RC and its HACS install, then `cut-release` with every
  integration signed off (`check_release.py --signoffs`). The release steps are in the `release` skill; the release
  body gets the signed-off notes.
- [x] **Raise/lower rig run**: the six actions and `stop` on Hue and Z2M, rerun in the lab reruns (#297, #299).
- [x] **Lab rerun**: the first lab run's failures fixed (#160–#163, #172) and rerun (#297, #299); the Z2M failures
  it found fixed by #302.
- [x] **Persistent auto-off/auto-on** (PR #305): `set_`/`clear_auto_off`/`_on`, Shelly; records clear the flag.
- [x] **Shelly `set_attributes`** (#284, PR #287): RGBCCT, with `min_brightness`/`max_brightness`; the record clears
  the flag.
- [x] **Shelly smooth dimming** (PR #289): `raise_brightness`, `lower_brightness` and `stop` on Shelly lights,
  brightness only: each move one `<Component>.Set` with `transition_duration`, halted by `DimStop` (the
  `DimUp`/`DimDown` ramps are stepped and have no `sweep_time` mapping). Findings:
  [`docs/integrations/shelly/research/2026-11-08-dimming.md`](docs/integrations/shelly/research/2026-11-08-dimming.md).
  RGBCCT; the records clear the flag.
- [x] **Device support, part 1** (#262): support levels and contribution lanes in `CONTRIBUTING.md`, the README's "Will it
  work with my device?", and the device report, device request and bug issue forms.
- [x] **Device support, part 2**: `scripts/gen_compat.py` generates `docs/compatibility.md` (levels and tested
  models per action and integration) and CI checks it is current. The README table, its "Will it work with my
  device?", `CONTRIBUTING.md` and the bug and device report forms link to it instead of `docs/test-records/`.
- [x] **Device support, part 3**: a "Report a device" step in DeviceSide's options that opens a prefilled device
  report.
- [x] **Documented support level** (#267): a `Level` per scope item; Shelly Light, CCT, RGB and RGBW components and
  Matter lights ship Documented, and Matter confirms a timer only with OnOff on.
- [x] **Experimental features option and Reported level** (#307): Experimental scope items ship in stable releases,
  refused until the option is on; Zigbee switches for `turn_on_for` and `get_attributes`, and Shelly Light, CCT, RGB
  and RGBW dimming, are Experimental.
- [x] **Device compatibility cards** (#309): a generated card per integration in the README, from the records, scopes
  and `docs/devices.yaml`, backed by `docs/device-compatibility.md`, which replaces `docs/compatibility.md` and
  `docs/hardware-the-rig-lacks.md`.
- [x] **Single report form** (#313): one device report form for results and bugs, several devices per report; the Support
  field and the options flow's action step are gone.
- [x] **Maintainers per integration** (#359): `IntegrationInfo.maintainers` on the cards, `.github/CODEOWNERS` and
  issue routing; `Support` retired; ⚠️ Unmaintained.
- [x] **Docs bundles** (#362): each integration's user page, internals, device data, research and test records in
  `docs/integrations/<integration>/`, which its maintainers own; shared pages keep what spans integrations, and
  `docs/models.yaml` the models.
- [x] **Code-owner enforcement** (#370): outside maintainers hold Triage, repo maintainers merge, and CODEOWNERS keeps naming
  them; review requests go out by hand until that becomes a bottleneck.
- [x] **Fast track** (#370): a contributor who builds and tests on their own hardware opens a PR directly, with no
  issue (CONTRIBUTING, PR template); an Experimental item moves to Reported, on its maintainer's request, after five working reports from other users.
- [x] **Retests and Stale tests** (#374): every changed adapter is retested before release (a rig record, a maintainer's
  `_manual.md` line or an accepted report at a release); one left untested at the cut ships live, marked ⚠️ Stale tests,
  with a call for testers in the release notes.
- [x] **Shared code proof** (#377): superseded by the evidence rules, under which `core/` changes need no device
  sign-off.

## After v0.1.0

- [ ] **Code comments cite the bundles**: eight comments under `integrations/` (hue, matter, zha, zigbee2mqtt) still
  cite `docs/integration-internals.md` or `docs/integrations.md`; point each at its bundle's
  `internals.md#tested-behavior` (Z2M `addressing.py`: `index.md#sharp-edges`), which `tests/scripts/test_doc_anchors.py`
  then checks; widen that test to page-only citations (`docs/….md` with no anchor) too.

- [ ] **Forum announcement**, once v0.1.0 ships: refresh the draft's testers-wanted list from [Try it: Experimental
  features](docs/device-compatibility.md#try-it-experimental-features) and check every link resolves (the issue forms,
  `docs/device-compatibility.md`), then post it. After posting, point the "Getting involved" testers link at the
  heading's anchor, which Discourse names after the post id.
- [ ] **Forum thread links**: add the thread URL as the contact link in `.github/ISSUE_TEMPLATE/config.yml` and set
  `blank_issues_enabled: false` there, and link the thread from `CONTRIBUTING.md` and the README's Contributing
  section.
- [ ] **Shelly RGB and RGBW color**: port core's conversions (`light/helper.py` rgb to rgbw with the white split,
  kelvin to color) so RGB and RGBW take color instead of dropping it, and give `status_row` a branch per component
  so `get_attributes` reads Light, CCT, RGB and RGBW light state.
- **9b** Maintained adapters: the rig kit, so maintainers can commit records for hardware the rig lacks
  (`docs/design.md`, Release gates). Until then, they sign off by hand (`docs/evidence.md`).
