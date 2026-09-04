# Exakt clarity and proof design

Date: 2026-09-04
Status: approved for implementation

## Summary

Exakt will remain a prompt-first, portable engineering skill rather than grow
into a second agent runtime. One invocation must turn a task or product brief
into a clear living specification, a native host plan, proof-driven execution,
milestone closeouts, and a truthful final report.

The design adds four missing guarantees to the existing workflow:

1. ambiguity is resolved iteratively and proportionally before it becomes code;
2. intended behavior is expressed as primitives, invariants, acceptance
   criteria, and counterexamples before tests are written;
3. every implementation claim traces to fresh evidence rather than activity;
4. the user can see the current brief, plan, milestone progress, and gaps in the
   existing harness as well as the optional HTML report.

The implementation should add these guarantees to the current Exakt state and
renderer. It must not introduce a second workflow engine, a mandatory external
service, or a runtime dependency on another skill pack.

## Product principle

The smallest process that prevents the wrong software is the right process.

Exakt should help an agent think clearly, ask better questions, and falsify its
own claims. It should not reward lengthy documents, ceremony, speculative
abstractions, or tests that merely ratify the implementation.

Depth is proportional:

- a clear single-file task may need a five-line contract and one milestone;
- a cross-cutting feature needs explicit interfaces, invariants, and rollout;
- a product brief may need a capability map, architecture, multiple milestones,
  and parallel specialist review.

The truth standard does not change with task size.

## Goals

- Make the user's intended outcome, constraints, and unresolved decisions
  inspectable before implementation.
- Ask only consequential questions, one at a time, with an explicit hypothesis.
- Create one living Markdown specification and show a concise brief in the
  terminal or chat before execution.
- Mirror approved milestones and tasks into the host's native plan/TODO surface.
- Make every executable behavior change follow a legitimate RED, GREEN,
  REFACTOR loop.
- Prevent common test-gaming paths such as weakening assertions, deleting or
  skipping tests, hard-coding fixtures, or changing the quality bar.
- Close each milestone with user-visible outcomes, covered acceptance criteria,
  changed artifacts, fresh evidence, gaps, and an evidence-calibrated status.
- When commits are authorized, create one verified commit per milestone with a
  body that records the contract and proof represented by that commit.
- Keep the deterministic HTML report useful as a projection of the same state,
  not a separate source of truth.

## Non-goals

- Replacing the host harness's plan mode, permissions, sandbox, or approval UX.
- Requiring Addy Osmani's agent-skills or invoking them at runtime.
- Creating a general project-management system or issue tracker.
- Forcing a long interview or a large specification for routine work.
- Requiring unit tests for prose, documentation, or assets where another proof
  method is more honest.
- Treating a passing test suite, successful command, code diff, commit, or agent
  report as sufficient proof on its own.
- Automatically committing when the user has not authorized commits.

## Ideas adopted, independently

The design was informed by publicly documented patterns in
`addyosmani/agent-skills`: inspect before asking, one-question-at-a-time intent
discovery, living specs, vertical slices, RED/GREEN/REFACTOR, constraint floors,
and adversarial review. Exakt will implement its own self-contained workflow and
language. There is no package dependency or delegation to those skills.

## Core contract

Every material result follows this trace:

```text
Intent
  -> Requirement
  -> Primitive
  -> Invariant
  -> Acceptance criterion
  -> Test or proof oracle
  -> Task
  -> Evidence
  -> Milestone
  -> Commit, when authorized
```

This is a reasoning contract, not a demand for ten separate documents. The
Markdown spec presents it compactly, while structured state keeps identifiers
and relationships machine-checkable.

### Contract primitives

Six concepts are sufficient:

- **Behavior**: an externally observable result for a user, caller, operator, or
  dependent system.
- **Invariant**: a condition that must remain true across relevant states and
  transitions, including failure and recovery paths.
- **Oracle**: an observation or command capable of distinguishing compliant
  behavior from non-compliant behavior.
- **Counterexample**: a concrete input, state, or perturbation that would expose
  a false implementation or weak test.
- **Milestone**: a coherent vertical outcome that can be independently
  demonstrated, verified, and reverted.
- **Proof**: fresh evidence bound to the exact source, build, configuration,
  artifact, runtime, or external-state subject being claimed.

Primitives should describe the domain and interfaces already required by the
brief. They are not permission to invent framework abstractions.

## Iterative clarity engine

Clarity is a loop, not a questionnaire that runs only at intake.

### 1. Inspect before asking

Read the source brief, repository instructions, relevant code, tests, build
commands, open plans, and current worktree. Do not ask the user for facts that
are available in the repository or supplied material.

### 2. Form a falsifiable intent hypothesis

State the current interpretation in one sentence and attach:

- confidence;
- the reason when confidence is low;
- the most consequential unresolved point.

Confidence is not proof and is never shown as completion status. It is a prompt
for the agent to surface uncertainty.

### 3. Maintain a clarity ledger

Classify material statements as:

- `known`: supported by the user, repository, or cited source;
- `assumed`: a reversible working assumption with a stated confidence;
- `decided`: explicitly approved by the user;
- `unknown`: unresolved, with blocking or non-blocking impact;
- `conflicted`: two sources or constraints cannot both be satisfied.

Each entry records its source and the requirements or milestones it affects.
The brief shown to the user contains only the consequential entries, not the
full internal ledger.

### 4. Ask by information gain

Rank ambiguity using three factors:

- how much the answer can change user-visible behavior;
- blast radius across architecture, data, security, cost, or migration;
- reversibility after implementation.

Ask one question at a time only when its answer can materially change scope,
behavior, architecture, risk, or approval. Include the agent's best guess and
why. Never batch a generic discovery interview.

After an answer, show a compact delta:

```text
Changed: <the decision now fixed>
Still open: <next consequential uncertainty, or none>
```

### 5. Stop at sufficient clarity

Stop asking when:

- intended behavior and boundaries are predictable;
- no unresolved item can materially change the selected architecture or safety;
- remaining assumptions are reversible and covered by observable acceptance
  criteria; and
- the user has approved the concise brief or already delegated execution of
  the clearly stated scope.

An arbitrary question count or confidence percentage must not hold simple work
hostage.

### 6. Reopen when reality disagrees

During implementation, a repository discovery, failed test, runtime observation,
or reviewer finding may falsify the contract. Exakt must then:

1. record the contradiction instead of explaining it away;
2. mark dependent tasks and evidence stale;
3. revise the assumption, requirement, invariant, or architecture first;
4. request a decision only if the revision changes approved behavior, risk, or
   irreversible scope; and
5. re-plan affected work before continuing.

The spec is living, but history is not rewritten to hide why it changed.

## Living specification

### Storage and authority policy

Exakt declares one authority mode when a run starts and records it in state:

- In the stronger runtime, the external contract plus hash-chained journal is
  authoritative. Repository-local state, Markdown, and HTML are projections.
- In the portable skill-only runtime, `.exakt/exakt-state.json` is the local
  working authority. It is explicitly self-attested: structural verification
  can validate it, but it cannot be presented as independently verified or
  tamper-evident.

A deterministic Markdown projection is written to `.exakt/spec.md` after
initialization and every material contract change. The Markdown and HTML are
never competing sources of truth. This avoids littering small target
repositories while guaranteeing a durable local artifact.

For a material feature or product, Exakt also places the approved specification
in the repository's existing specification/documentation convention when one
exists. If no convention exists, it asks before adding a tracked spec file. A
commit may include only files inside the user's approved scope.

### Human-facing shape

The Markdown spec is concise and contains:

1. source brief and approved intent;
2. known facts, consequential assumptions, decisions, conflicts, and open
   questions;
3. requirements and non-goals;
4. architecture and rejected alternatives with short reasons;
5. behaviors, invariants, oracles, and counterexamples;
6. acceptance criteria and their trace links;
7. milestones, tasks, dependencies, and proof commands;
8. change log and current status.

Task mode may collapse sections with no material content. Product mode expands
them rather than using a different truth model.

### Brief shown before execution

The conversational brief should normally fit on one screen:

```text
Building: <one-sentence intended outcome>
Why: <user or product value>
Boundary: <important non-goal or safety limit>
Architecture: <one-sentence approach>
Proof: <strongest user-visible verification>
Plan: <N milestones / N tasks>
Open: <blocking decision, or none>
Spec: <path>
```

The user is asked to approve execution only after this brief is coherent.

## Native plan and TODO integration

After approval, Exakt maps milestones and tasks into the host's native plan or
TODO surface when available. The Markdown spec and structured state remain the
source of contract detail; the host plan is the live, concise progress view.

Rules:

- assign stable `M<N>` milestone IDs and `T<N>` task IDs when the plan is
  approved; do not renumber them during the run;
- one host plan item per milestone, prefixed with its stable ID;
- task-level TODOs only when the harness supports them without clutter;
- at most one milestone is `in_progress` unless independent work was explicitly
  parallelized;
- each TODO carries its requirement/acceptance-criterion IDs and verification;
- statuses are updated only after the corresponding evidence is recorded;
- when resuming the same Exakt run, merge status by stable ID rather than
  recreating items;
- if an unrelated active plan or TODO set exists and the host update operation
  would replace it, stop and ask before mapping Exakt work;
- if the host has no native plan surface, render a compact terminal checklist.

Specialists may research or critique in parallel, but the lead agent owns the
contract, user questions, plan state, approvals, and completion claims.

## TDD legitimacy contract

All executable behavior changes use RED, GREEN, REFACTOR. A task without an
executable behavior, such as documentation or visual content, uses the
equivalent before-state/counterexample/proof loop rather than a meaningless
synthetic test. Milestone closure applies the correct loop per task type.

### Before RED

For the behavior being implemented, the spec must identify:

- the requirement and acceptance criterion;
- the invariant being protected;
- the observable oracle;
- at least one counterexample or failure mode.

If none can be stated, the behavior is not clear enough to implement.

### RED

- Write or select a test that asserts externally meaningful behavior.
- Run it against the pre-change implementation.
- Confirm it fails for the intended missing behavior, not setup, syntax, fixture,
  environment, or unrelated failures.
- Record the focused command and failure result.

A test first written after the behavior already passes is not RED evidence.

### GREEN

- Implement the smallest coherent behavior satisfying the approved criterion.
- Do not branch on test environment, hard-code test fixtures, weaken production
  validation, or bypass real boundaries merely to obtain green.
- Run the focused test, then the relevant regression/build checks.

### REFACTOR

- Improve structure without changing the protected behavior.
- Re-run affected checks after the final relevant change.
- Do not create abstractions justified only by possible future requirements.

### Test-legitimacy gate

Before a task can close, inspect the diff for:

- skipped or deleted tests;
- removed/weakened assertions or thresholds;
- new suppression comments or ignored coverage/mutation regions;
- fixture-specific hard-coding and test-environment branches;
- mocks that bypass the behavior under claim;
- empty catches, placeholders, or unimplemented stubs;
- updates to constraints or snapshots whose only effect is making red turn green.

Then attempt at least one independent falsification appropriate to risk:

- a negative or boundary test;
- a counterexample from the spec;
- mutation testing scoped to changed behavior when supported;
- runtime inspection through the real interface;
- a fresh-context reviewer asked to disprove the claim.

The agent that wrote a test cannot treat that test alone as independent proof.

## Traceability and orphan gate

The following must be machine-checkable before execution and again at handoff:

- every requirement has at least one acceptance criterion;
- every material invariant is protected by an acceptance criterion and oracle;
- every acceptance criterion maps to at least one task and planned proof;
- every implementation task maps back to approved scope;
- every completed task has fresh evidence;
- every completed milestone covers its declared acceptance criteria;
- no final claim exceeds the weakest linked evidence status.

An orphan is not silently ignored. Before execution it blocks the affected task;
at handoff it becomes an explicit gap unless the contract is revised with the
user's approval where required.

## Milestone execution and closeout

A milestone is a thin vertical outcome, not a horizontal layer. It should leave
the repository usable and, where practical, demonstrate something through a
real caller or user path.

At the end of each milestone, Exakt reports:

```text
Milestone: M<N> — <outcome>
Completed: <user-visible and technical result>
Covered: <requirement / invariant / acceptance-criterion IDs>
Changed: <important files or artifacts>
Proved: <fresh commands, inspections, and outcomes>
Gaps: <none, or honest unresolved items>
Commit: <hash, prepared message, or not authorized>
```

The HTML report shows the same closeout with expandable evidence. It must never
display a stronger state than the canonical data supports.

## Commit contract

When the user has authorized commits, create one verified commit per milestone.
Do not commit a partially verified milestone merely to create a checkpoint.
Stage only milestone files and preserve unrelated work.

The sequence is fixed:

1. gather closeout evidence against the candidate milestone files;
2. fail or ask if unrelated changes are already staged;
3. stage only the milestone's declared files;
4. confirm the staged diff still matches the recorded contract and evidence
   subjects, run diff guards, and repeat affected checks if staging changed the
   verified subject;
5. commit with the contract body below; and
6. record the resulting commit hash in local Exakt state.

Commit-message prose is provenance, not proof. The linked evidence remains the
basis for milestone status.

Suggested format:

```text
feat(scope): deliver <milestone outcome>

Milestone: M2
Implements: R2, R4
Protects: INV1, INV3
Accepts: AC3, AC4, AC7
Evidence: <focused check>; <regression check>; <runtime observation>
Spec-Digest: sha256:<digest of approved contract projection>
Gaps: none
```

If commits are not authorized, prepare this exact message and report it without
running Git commit. If evidence is partial, the message and milestone status
must say so rather than claiming delivery.

## State and renderer changes

Introduce `exakt-report-v2` with required, closed records for:

- `clarity`: ledger entries plus current intent hypothesis;
- `primitives`: behaviors, invariants, oracles, and counterexamples;
- `milestones`: scope links, task IDs, closeout, and commit metadata;
- `traceability`: explicit edges between existing IDs;
- `spec`: output path, digest, and revision metadata.

Valid v1 state remains readable, renderable, and verifiable under its existing
rules, but the new renderer labels it as a legacy contract and it cannot satisfy
v2 milestone/traceability guarantees. Explicit migration copies known v1 data
into v2, creates the new records as unresolved, and requires fresh evidence
before any v2 `verified` closeout. It never infers new guarantees from absent
fields.

V2 schema validation rejects unknown statuses, malformed references, and
undeclared authority modes. The CLI gains a deterministic spec-rendering
operation or extends an existing render operation without introducing a daemon.

The report adds compact views for:

- current intent and material uncertainty;
- contract traceability and orphan warnings;
- milestone progress and closeouts;
- TDD/falsification evidence;
- changed or invalidated assumptions.

## Failure and recovery behavior

- If the user changes intent, revise the contract and invalidate affected work.
- If repository reality conflicts with the plan, stop the affected work and
  re-plan from evidence.
- If RED cannot be demonstrated, do not pretend TDD occurred; explain whether
  the behavior already existed, the test is invalid, or the environment blocks
  proof.
- If a verifier contradicts the implementation, status becomes `contradicted`
  until repaired and freshly rechecked.
- If the process resumes after compaction or interruption, load the spec, state,
  worktree, and evidence fingerprints; continue from the first valid unfinished
  milestone.
- If an external or irreversible action is needed, the host's exact-action
  approval boundary still applies.

## Evaluation strategy

The skill change must be developed with prompt/skill TDD:

### RED baseline

Run the current skill against pressure cases that expose the missing behavior:

1. an underspecified feature where premature coding is tempting;
2. a complete PRD containing one hidden contradiction;
3. a bug where a weak test can be made green without fixing the behavior;
4. a multi-milestone change interrupted after partial progress;
5. a dirty worktree where blind milestone commits would absorb user changes;
6. a scope change that should stale existing evidence;
7. a simple task where the workflow must stay lightweight.

Record concrete failures before editing the skill.

### GREEN evaluation

Rerun the same cases in fresh sessions with the revised skill. The revised skill
passes only if it:

- asks fewer, higher-value questions and does not ask discoverable facts;
- produces a proportional spec with explicit assumptions and invariants;
- refuses to plan through a blocking contradiction;
- demonstrates legitimate RED evidence and detects the weak-test shortcut;
- mirrors milestones into the available host plan;
- resumes without redoing completed or invalid work;
- avoids unrelated files in commit staging;
- marks stale or unavailable evidence honestly; and
- produces a concise milestone/final report grounded in actual output.

### Adversarial review

Use fresh-context agents to attack:

- prompt loopholes and rationalizations;
- schema and traceability consistency;
- TDD gaming and false-positive verification;
- harness portability and proportionality;
- report claims versus underlying state.

Findings are evidence, not automatic verdicts. The lead reproduces or checks
each material claim before changing the implementation.

## Acceptance criteria

- AC1: Every invocation writes a deterministic `.exakt/spec.md` representing
  the current approved or proposed contract.
- AC2: The skill inspects available repository facts before asking a user and
  asks at most one consequential question at a time with a stated hypothesis.
- AC3: The spec records consequential facts as known, assumed, decided,
  unknown, or conflicted, and implementation stops on blocking conflicts.
- AC4: Behavior work cannot start without a behavior, invariant, oracle,
  counterexample, acceptance criterion, task, and planned proof trace.
- AC5: Host-native plan/TODO items mirror milestone status without overwriting
  unrelated existing work.
- AC6: An executable-behavior task cannot close unless RED was observed for the
  expected reason, GREEN/regression checks are fresh, and the test-legitimacy
  gate has no unresolved failure. A non-executable task requires a recorded
  before-state or counterexample and fresh task-appropriate proof.
- AC7: Changed contract subjects make dependent tasks and proof stale and force
  affected work through clarification or re-planning.
- AC8: Every milestone closeout lists completed outcomes, covered contract IDs,
  changed artifacts, evidence, gaps, and commit status.
- AC9: Authorized milestone commits include milestone, contract coverage,
  evidence, spec digest, and gaps; unauthorized runs prepare but do not execute
  the commit.
- AC10: V2 final report status cannot exceed linked evidence in the declared
  authority mode and exposes orphans, contradictions, stale proof, unavailable
  verification, and self-attested versus independent provenance. V1 remains
  readable but cannot be silently upgraded to v2 verification.
- AC11: In the simple-task evaluation fixture, the workflow uses one milestone,
  asks no discoverable question and at most one blocking question, shows a brief
  of at most eight lines, and keeps the generated spec below 80 non-blank lines.
- AC12: Exakt installs and operates without Addy's repository, skills, services,
  or any other non-declared runtime dependency.

## Implementation boundaries

- Prefer extending existing schemas, CLI helpers, renderer, and prompt files.
- Avoid a new database, background service, network dependency, or tracker.
- Maintain backward compatibility for valid existing report states and commands.
- Keep generated HTML offline, escaped, deterministic, responsive, and free of
  remote requests.
- Keep prompt-critical rules in `skills/exakt/SKILL.md`; put templates and
  conditional detail in directly linked references.
- Do not duplicate the same normative rule in multiple files unless one is a
  concise routing statement and the other is its canonical detailed contract.

## Rollout

1. Capture RED prompt-evaluation transcripts from the current skill.
2. Update the core prompt and workflow references, then rerun the prompt cases
   to prove the intended behavior before adding richer projections.
3. Add the minimal v2 schema and traceability checks.
4. Implement deterministic Markdown spec generation.
5. Add milestone and TDD-legitimacy views to the existing report.
6. Run focused and full tests plus fresh-session skill evaluations.
7. Run adversarial review and repair verified findings.
8. Install from the public repository into a clean temporary Codex home and
   complete one small task and one complex product case.
9. Push only if tests, fresh install, actual outputs, links, and remote commit
   identity all pass.

## Expected memory after use

The user should remember three things:

1. Exakt made the intended behavior unusually clear without wasting time.
2. The implementation proceeded in visible, reversible milestones tied to the
   spec.
3. Every completion statement showed what was actually observed and what, if
   anything, remained unproved.
