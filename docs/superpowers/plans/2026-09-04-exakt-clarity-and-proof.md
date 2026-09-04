# Exakt Clarity and Proof Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make one Exakt invocation produce a proportional living spec, native
milestone plan, legitimate TDD/proof loop, evidence-bound closeouts, and a
truthful report without depending on another skill pack.

**Architecture:** Keep Exakt prompt-first. Add one canonical clarity/proof
reference, a small v2 project-view model, and a deterministic Markdown renderer;
then project the same v2 state into the existing offline HTML report. Preserve
v1 reading/rendering and label it legacy rather than silently inventing v2
guarantees.

**Tech Stack:** Markdown Agent Skill instructions, Python 3 standard library,
`unittest`, deterministic JSON/HTML, native Codex/Claude plan primitives.

---

## File map

- `skills/exakt/SKILL.md`: short constitutional workflow and routing.
- `skills/exakt/references/clarity-and-proof.md`: canonical clarity ledger,
  traceability, TDD legitimacy, milestone, and commit rules.
- `skills/exakt/references/workflows.md`: phase exits and proportional task flow.
- `skills/exakt/references/product-mode.md`: product decomposition and living
  contract behavior.
- `skills/exakt/references/harness-adapters.md`: collision-safe native plan/TODO
  mirroring.
- `skills/exakt/references/verification.md`: falsification, staleness, and
  anti-test-gaming checks.
- `skills/exakt/references/report-interface.md`: v1/v2 authority and projection
  contract.
- `skills/exakt/scripts/report_state.py`: focused v1/v2 construction,
  validation, traceability, and completion gates.
- `skills/exakt/schemas/exakt-report-v2.json`: closed structural contract for
  the v2 project view.
- `skills/exakt/scripts/render_spec.py`: deterministic Markdown projection and
  contract digest.
- `skills/exakt/scripts/exakt.py`: CLI orchestration for init, status, spec,
  render, and verify.
- `skills/exakt/scripts/render_report.py`: v2 clarity, milestones, provenance,
  and truth views while preserving v1 rendering.
- `tests/evals/clarity-proof-baseline.md`: reproducible RED observations for the
  seven pressure cases.
- `tests/test_prompt_contract.py`: package-level prompt/reference invariants.
- `tests/test_report_state.py`: v2 shape, traceability, TDD/proof, legacy, and
  completion-gate tests.
- `tests/test_spec_renderer.py`: deterministic Markdown, digest, escaping, and
  proportional-output tests.
- `tests/test_cli.py`: end-to-end CLI behavior for v2 init/spec/verify and v1.
- `tests/test_renderer.py` and `tests/fixtures/report-state-v2.json`: v2 HTML
  projection tests.
- `README.md`, `docs/design.md`, and `examples/*`: public behavior and verified
  example output.

## Milestone M1: Put clarity and proof in the prompt

### Task 1: Preserve the RED baseline

**Files:**
- Create: `tests/evals/clarity-proof-baseline.md`

- [ ] **Step 1: Record the seven pre-change failures**

Write the observed baseline with one section per case and direct pointers to the
current instruction that is absent or too weak. Start with:

```markdown
# Exakt clarity/proof RED baseline

Captured: 2026-09-04
Skill revision: 0574438^ (prompt behavior before M1)
Result: 0/7 cases fully satisfy the approved design.

## Case 1 — underspecified feature
Observed: partial
Failure: no intent hypothesis, clarity classification, or blocking-ambiguity
rule; a consequential unknown can be mislabeled reversible.
```

- [ ] **Step 2: Confirm the baseline is genuinely RED**

Run:

```bash
rg -n "intent hypothesis|clarity ledger|test-legitimacy|milestone closeout|Spec-Digest" \
  skills/exakt/SKILL.md skills/exakt/references
```

Expected: required concepts are absent or incomplete, matching the recorded
failures; do not reinterpret unrelated prose as a pass.

### Task 2: Add the constitutional prompt and canonical detailed contract

**Files:**
- Modify: `skills/exakt/SKILL.md`
- Create: `skills/exakt/references/clarity-and-proof.md`
- Modify: `skills/exakt/references/workflows.md`
- Modify: `skills/exakt/references/product-mode.md`
- Modify: `skills/exakt/references/harness-adapters.md`
- Modify: `skills/exakt/references/verification.md`
- Modify: `skills/exakt/references/report-interface.md`
- Test: `tests/test_prompt_contract.py`

- [ ] **Step 1: Write prompt-contract tests first**

Create tests that assert the installed prompt:

```python
class PromptContractTests(unittest.TestCase):
    def test_core_prompt_routes_to_clarity_and_proof_contract(self):
        skill = SKILL.read_text(encoding="utf-8")
        self.assertIn("references/clarity-and-proof.md", skill)
        self.assertIn("Intent → Requirement", skill)
        self.assertIn("RED", skill)
        self.assertIn("milestone", skill.casefold())

    def test_no_runtime_dependency_on_addys_skill_pack(self):
        package = "\n".join(
            path.read_text(encoding="utf-8")
            for path in SKILL_ROOT.rglob("*.md")
            if "docs/superpowers" not in str(path)
        )
        self.assertNotIn("agent-skills:", package)
```

Also assert that the canonical reference contains the five clarity classes,
one-question rule, contradiction stop, invariant/oracle/counterexample gate,
RED failure-reason requirement, anti-gaming diff checks, milestone closeout,
commit authorization fallback, and contract-change invalidation.

- [ ] **Step 2: Run the focused tests and observe RED**

Run:

```bash
python3 -m unittest tests.test_prompt_contract -v
```

Expected: FAIL because the reference and mandatory prompt language do not exist.

- [ ] **Step 3: Write the canonical reference**

Implement these compact sections in `clarity-and-proof.md`:

```markdown
# Clarity and proof contract

## Clarity loop
Inspect first. Maintain known / assumed / decided / unknown / conflicted facts.
Ask one consequential question with a guess. Show the decision delta. Stop when
remaining assumptions are reversible and observable.

## Trace
Intent → Requirement → Behavior → Invariant → Acceptance criterion → Oracle →
Task → Evidence → Milestone → Commit when authorized.

## Legitimate TDD
For behavior: define invariant, oracle, and counterexample; observe RED fail for
the intended reason; implement GREEN; refactor; audit the diff; run an
independent falsification. For non-executable work, use before-state,
counterexample, and fresh proof.

## Milestones and commits
Use stable M/T IDs, close vertical outcomes, stage declared files only, bind
evidence to the staged subject, and commit only with user authority.
```

Include exact anti-rationalization rules, host-plan merge rules, closeout shape,
and commit-body shape from the approved design.

- [ ] **Step 4: Tighten the core prompt without duplicating the reference**

Replace optional “test-first when practical” language with mandatory routing:

```markdown
Read [references/clarity-and-proof.md](references/clarity-and-proof.md) before
asking requirements questions, planning, implementing, or closing work. Its
clarity, traceability, TDD/proof, milestone, and commit gates are non-negotiable.
```

Keep the core behavior explicit: inspect before asking; show the brief/spec path;
mirror the approved milestones into native plan/TODOs; reopen the contract when
evidence contradicts it; never claim completion above fresh evidence.

- [ ] **Step 5: Update direct references and keep one source per rule**

Make each existing reference point to `clarity-and-proof.md` for normative
details. Keep only phase-specific adaptations in the old files. Do not copy the
full rules into every reference.

- [ ] **Step 6: Run prompt tests and full package tests**

Run:

```bash
python3 -m unittest tests.test_prompt_contract -v
python3 -m unittest discover -s tests -v
```

Expected: prompt tests PASS; existing 177 tests plus new tests PASS.

- [ ] **Step 7: Run fresh-context GREEN evaluations**

Give fresh read-only agents the same seven cases, the changed skill, and no
implementer conclusion. Require per-case pass/fail and quoted governing rule.
Expected: all seven satisfy the explicit prompt contract; any false pass is a
prompt defect to repair before committing.

- [ ] **Step 8: Commit M1**

Stage only the prompt/reference/eval/test files and commit with:

```text
feat(skill): enforce clarity and proof contract

Milestone: M1
Implements: AC2, AC3, AC4, AC5, AC6, AC7, AC8, AC9, AC11, AC12
Protects: clarity-before-code, legitimate-tests, evidence-before-claims
Evidence: prompt contract tests; full unittest suite; seven fresh-context evals
Spec-Digest: ba49e36c2bcbb48d5cfc4e5dd634beb95a5aa4451db259e178e54b3c630ccc50
Gaps: v2 generated artifacts arrive in M2/M3
```

## Milestone M2: Generate the living v2 spec and gate traceability

### Task 3: Define v2 state, migration, and completion semantics

**Files:**
- Create: `skills/exakt/scripts/report_state.py`
- Create: `skills/exakt/schemas/exakt-report-v2.json`
- Test: `tests/test_report_state.py`
- Modify: `tests/test_contracts.py`
- Modify: `tests/fixtures/contracts/valid-examples.json`
- Modify: `skills/exakt/scripts/exakt.py`

- [ ] **Step 1: Write failing v2 state tests**

Cover seven named cases: v2 initialization declares portable/self-attested
authority; unknown top-level and nested fields fail; duplicate IDs and dangling
trace edges fail; verified behavior tasks require RED, GREEN, regression,
legitimacy, and falsification evidence; non-executable tasks require before,
proof, and falsification evidence; changed contract digests stale linked
evidence; and v1 remains readable but explicitly legacy.

The minimal v2 additions alongside existing report fields are:

```json
{
  "authority_mode": "local-self-attested",
  "clarity": {
    "intent": {"text": "Observable outcome", "confidence": "low", "reason": "Recon pending", "open_item_id": null},
    "ledger": []
  },
  "primitives": {"behaviors": [], "invariants": [], "oracles": [], "counterexamples": []},
  "traceability": {"edges": [], "invalidations": []},
  "milestones": [],
  "spec": {"path": ".exakt/spec.md", "revision": 1, "digest": "", "updated_at": "2026-09-04T00:00:00Z", "changes": []}
}
```

Use `external-journal` or `local-self-attested` authority; the five clarity
statuses from the design; stable IDs; and trace edges with closed relations
`defines`, `protects`, `accepted_by`, `observed_by`, `challenged_by`,
`implemented_by`, `proved_by`, and `delivered_in`. Add `work_type` to v2 tasks
and `id`, `stage`, `provenance`, `subject_digest`, and `contract_digest` to v2
evidence. Evidence stages distinguish RED/GREEN/regression/legitimacy/
falsification from before/proof; do not add a second task graph.

- [ ] **Step 2: Run focused tests and observe RED**

Run:

```bash
python3 -m unittest tests.test_report_state -v
```

Expected: FAIL because `report_state.py` and v2 behavior do not exist.

- [ ] **Step 3: Implement the smallest closed v2 validator**

In `report_state.py`, expose constants `REPORT_V1` and `REPORT_V2`, plus focused
functions named `initial_state`, `validate_state`, `traceability_gaps`,
`verification_gaps`, and `legacy_state`. Keep construction and semantic checks
out of the CLI command handlers.

Validate exact allowed keys for v2 records. Collect all declared IDs, reject
duplicates/dangling edges, and enforce required edge classes only when work is
marked verified. Preserve current v1 field/verification behavior and return a
legacy marker to callers; never populate v2 fields on v1 automatically.

Define the closed v2 structure in `exakt-report-v2.json`, register one valid
example in the contract fixture, and make the ordinary contract tests prove
unknown fields and versions fail closed.

- [ ] **Step 4: Route the CLI through the state module**

Keep `exakt.py`'s public commands stable. Default new `init` calls to v2. Make
`status` print `V1 LEGACY` for v1 and authority/provenance for v2. Make `verify`
include traceability, task-proof, milestone, gaps, and status gates.

Add an explicit `migrate STATE --output NEW_STATE` command. It copies v1 content,
creates unresolved v2 records, sets overall status to `unverified`, and refuses
in-place or silent overwrite. Migration never converts old `verified` labels
into v2 proof.

- [ ] **Step 5: Run focused and regression tests**

Run:

```bash
python3 -m unittest tests.test_report_state tests.test_cli -v
python3 -m unittest discover -s tests -v
```

Expected: PASS; original v1 fixture and commands remain readable.

### Task 4: Render and synchronize `.exakt/spec.md`

**Files:**
- Create: `skills/exakt/scripts/render_spec.py`
- Test: `tests/test_spec_renderer.py`
- Modify: `skills/exakt/scripts/exakt.py`

- [ ] **Step 1: Write failing Markdown renderer tests**

Write five named tests: identical state produces byte-identical Markdown and
digest; a contract change changes the digest while progress-only state does not;
untrusted multiline text cannot create unintended Markdown structure; the
simple-task fixture stays below 80 nonblank lines; and `init` plus `spec` write
the projection beside state atomically.

- [ ] **Step 2: Observe RED**

Run:

```bash
python3 -m unittest tests.test_spec_renderer -v
```

Expected: FAIL because no Markdown renderer/spec command exists.

- [ ] **Step 3: Implement deterministic contract rendering**

Expose four focused functions: `contract_snapshot(state)` returns only contract
fields; `contract_digest(state)` returns lowercase SHA-256; `render_spec(state)`
returns newline-terminated UTF-8 Markdown; and `write_spec(path, state, force=)`
writes atomically and returns the digest.

Hash canonical JSON for contract fields only, excluding timestamps, progress,
evidence results, and the digest itself. Render task mode compactly by omitting
empty sections; include all consequential product sections when populated.

- [ ] **Step 4: Add `spec` and init synchronization**

Add:

```text
exakt.py spec STATE [--output PATH] [--force]
```

`init` writes state, `.exakt/spec.md`, and (unless disabled) HTML. `render`
refreshes the spec before HTML only when overwrite is explicit. Atomic writes
must use a same-directory temporary file plus `os.replace`.

- [ ] **Step 5: Verify CLI behavior and deterministic output**

Run:

```bash
python3 -m unittest tests.test_spec_renderer tests.test_cli -v
python3 -m unittest discover -s tests -v
```

Expected: PASS; two unchanged renders have identical SHA-256 digests.

- [ ] **Step 6: Commit M2**

```text
feat(runtime): add living v2 engineering spec

Milestone: M2
Implements: AC1, AC3, AC4, AC6, AC7, AC10, AC11
Protects: single-authority projection, traceability, evidence-calibrated closure
Evidence: report-state, spec-renderer, CLI, and full unittest suites
Spec-Digest: ba49e36c2bcbb48d5cfc4e5dd634beb95a5aa4451db259e178e54b3c630ccc50
Gaps: HTML v2 visualization arrives in M3
```

## Milestone M3: Make progress and proof legible in the report

### Task 5: Add v2 report projections without redesigning the runtime

**Files:**
- Modify: `skills/exakt/scripts/render_report.py`
- Modify if needed: `skills/exakt/assets/report-template.html`
- Create: `tests/fixtures/report-state-v2.json`
- Modify: `tests/test_renderer.py`

- [ ] **Step 1: Write failing v2 renderer tests**

Require the final HTML to show:

```python
for text in (
    "Known", "Conflicted", "Invariants", "Counterexamples",
    "Milestone M1", "RED observed", "Self-attested", "Orphan trace",
):
    self.assertIn(text, html)
```

Also require a visible `Legacy v1 contract` label for the old fixture, risk-first
ordering, fully escaped project text, no remote requests, deterministic output,
keyboard operability, reduced motion, and no mobile horizontal overflow.

- [ ] **Step 2: Observe RED**

Run:

```bash
python3 -m unittest tests.test_renderer -v
```

Expected: new v2 assertions FAIL while old renderer assertions still pass.

- [ ] **Step 3: Extend existing seven views**

Do not add a second UI or a generic dashboard. Extend:

- Brief & spec: intent hypothesis, clarity ledger, primitives;
- Acceptance & plan: milestones before task details;
- Progress: milestone closeout, coverage IDs, commit state;
- Verification & truth: authority provenance, test stages, trace orphans,
  contradictions, and stale evidence.

Use the existing editorial studio visual language and progressive disclosure.
Any new status must include visible text/symbols, not color alone.

- [ ] **Step 4: Run programmatic renderer checks**

Run:

```bash
python3 -m unittest tests.test_renderer -v
python3 -m unittest discover -s tests -v
```

Expected: PASS with deterministic HTML and no v1 regression.

- [ ] **Step 5: Inspect the real HTML with Playwright**

Use a native Python Playwright script against the static file. Check desktop
`1440x900` and mobile `390x844`, keyboard-expand details, console errors,
horizontal overflow, reduced motion, local feedback copy/download, and remote
requests. Save screenshots under `/tmp` first and visually inspect them before
replacing the public preview.

- [ ] **Step 6: Commit M3**

```text
feat(report): show clarity milestones and proof

Milestone: M3
Implements: AC3, AC5, AC6, AC7, AC8, AC10
Protects: truthful visual status, offline safety, accessible review
Evidence: renderer tests; full suite; desktop/mobile Playwright inspection
Spec-Digest: ba49e36c2bcbb48d5cfc4e5dd634beb95a5aa4451db259e178e54b3c630ccc50
Gaps: public docs and fresh-install trials remain in M4
```

## Milestone M4: Prove the shipped skill in clean harnesses

### Task 6: Update the public example and documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/design.md`
- Modify: `examples/gst-decoded-navigation.json`
- Modify: `examples/gst-decoded-navigation.html`
- Modify after visual approval: `docs/assets/exakt-report-preview.png`

- [ ] **Step 1: Update public behavior, not marketing volume**

Document three artifacts (`spec.md`, state, HTML), the one-command flow, the
clarity delta, native TODO mapping, legitimate TDD, milestone closeout, and an
example commit body. Do not mention the former product name or imply a runtime
dependency on Addy's skills.

- [ ] **Step 2: Regenerate the example from one v2 source state**

Run the installed CLI's `spec`, `render`, and `verify` commands. Preserve the
example's honest active/unverified status because it is a design example, not a
completed implementation.

- [ ] **Step 3: Validate links and artifacts**

Check every repository-relative README link exists, every JSON file parses, the
example HTML is self-contained, and screenshot dimensions/readability are sane.

### Task 7: Fresh-install and adversarial acceptance trials

**Files:**
- Modify only if verified defects are found in earlier files.
- Record final evaluation summary in: `tests/evals/clarity-proof-after.md`

- [ ] **Step 1: Run all static and unit gates**

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q skills/exakt/scripts
git diff --check
```

Expected: all pass with no warnings or whitespace errors.

- [ ] **Step 2: Install into clean temporary Codex and Claude roots**

Run:

```bash
exakt_install_root=$(mktemp -d /tmp/exakt-clean-install.XXXXXX)
python3 skills/exakt/scripts/install.py --host codex --root "$exakt_install_root/codex"
python3 skills/exakt/scripts/install.py --host claude --root "$exakt_install_root/claude"
```

Then validate the copied skill and commands. Do not disturb the user's live
installations.

- [ ] **Step 3: Run two fresh-session trials**

Trial A: a simple behavior change; require one milestone, no discoverable
questions, at most one blocking question, brief at most eight lines, and spec
under 80 nonblank lines.

Trial B: a conflicting multi-part product brief; require conflict detection,
one high-information question, stable milestones/TODOs, invariants and
counterexamples, and no implementation before resolution.

- [ ] **Step 4: Run adversarial reviewers**

Fresh agents independently attack prompt loopholes, v2 validation/traceability,
test gaming, UI truthfulness, portability, and overengineering. Reproduce every
important finding; repair only verified defects; rerun affected tests.

- [ ] **Step 5: Verify the exact release tree**

Require a clean worktree except intended M4 files, inspect the staged diff,
verify no unrelated file is included, rerun affected gates against the final
tree, and record the commit hash.

- [ ] **Step 6: Commit M4**

```text
docs: ship the Exakt clarity workflow

Milestone: M4
Implements: AC1–AC12
Protects: clean install, proportional workflow, truthful public claims
Evidence: full suite; clean Codex/Claude installs; small/product trials;
desktop/mobile report checks; independent adversarial review
Spec-Digest: ba49e36c2bcbb48d5cfc4e5dd634beb95a5aa4451db259e178e54b3c630ccc50
Gaps: none
```

- [ ] **Step 7: Push and verify public state**

Push `main`, compare local `HEAD` with `origin/main`, verify the public repo and
example assets return HTTP 200, and only then announce the release.

## Self-review

- Design coverage: AC1–AC12 map to M1–M4; no requirement is implementation-free.
- Ordering: prompt behavior is proved before runtime projections; HTML consumes
  v2 state rather than inventing a parallel model.
- Compatibility: v1 remains readable/renderable and explicitly legacy; v2
  claims require v2 fields and fresh proof.
- Proportionality: the simple-task fixture gives measurable ceremony limits.
- Authority: local portable state is visibly self-attested; stronger journal
  runs remain externally authoritative.
- Safety: commits are authority-gated and explicit-path staged; fresh installs
  use temporary roots and do not mutate live harness homes.
- Placeholders: angle-bracket values appear only in documented runtime/commit
  templates and must be replaced during execution; no implementation step is
  left unspecified.
