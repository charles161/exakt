# Exakt design

Exakt is a portable workflow skill for Codex, Claude Code, and compatible
engineering harnesses. It turns a bounded task or full product brief into a
repository-grounded contract, a native progress plan, proof-driven execution,
and an evidence-calibrated handoff.

## Product boundary

Exakt is a prompt-first skill plus deterministic local helpers. It is not an
IDE, hosted service, project tracker, or replacement agent runtime. The host
remains responsible for tools, process isolation, permissions, user approval,
and the availability of plans, TODOs, or subagents.

There is no mandatory service or second control plane. The local files remain
useful when the host exposes only a terminal and filesystem.

## Proportional workflow

1. Preserve the source brief and inspect repository instructions, code, tests,
   build commands, open plans, and the current worktree.
2. Choose Task mode for bounded work or Product mode for a broad outcome,
   product brief, or multiple independently deliverable systems.
3. Form one falsifiable intent hypothesis and classify consequential context as
   known, assumed, decided, unknown, or conflicted.
4. Ask at most one consequential question at a time, with a best guess, only
   when the answer can change behavior, scope, architecture, risk, or authority.
5. Define requirements, boundaries, architecture, observable acceptance
   criteria, invariants, counterexamples, and task-specific proof.
6. After approval, map stable milestone IDs to the host's native plan and
   optional task TODOs without replacing unrelated work.
7. Implement the approved scope using the correct executable or artifact proof
   loop, then inspect the actual result.
8. Compare every material claim with fresh evidence. Contradiction reopens the
   contract, invalidates dependent proof, and triggers repair or re-planning.
9. Close each milestone and refresh the Markdown and HTML projections before
   applying the final verification gate.

A routine task should normally use one milestone and a specification below 80
non-blank lines. Product mode expands the contract without changing the truth
standard.

## Three project artifacts

- `.exakt/exakt-state.json` is the structured source of truth. It stores stable
  IDs, clarity state, requirements, architecture, primitives, acceptance
  criteria, tasks, milestones, trace edges, evidence, invalidations, and gaps.
- `.exakt/spec.md` is a deterministic living projection of the proposed or
  approved contract. It is optimized for human review and repository history.
- `.exakt/exakt-report.html` is a deterministic, escaped, responsive, offline
  projection for reviewing specification, architecture, plan, decisions,
  progress, verification, and evidence.

The Markdown and HTML files are projections of the same state. Editing or
replacing a contract subject changes its digest and makes dependent proof stale;
the projections cannot independently upgrade a status.

## Runtime pieces

- `skills/exakt/SKILL.md` contains the portable reasoning and handoff workflow.
- `skills/exakt/references/` contains the detailed clarity, workflow, harness,
  report, and verification contracts loaded when relevant.
- `skills/exakt/agents/` defines bounded, read-only specialist roles.
- `skills/exakt/schemas/exakt-report-v2.json` is the closed project-state
  contract; valid legacy v1 reports remain readable but cannot silently gain v2
  guarantees.
- `skills/exakt/scripts/exakt.py` initializes workspaces, shows compact status,
  migrates legacy state, regenerates projections, and applies the completion
  gate.
- `skills/exakt/scripts/report_state.py` validates references, traceability,
  proof stages, invalidation, authority, and completion gaps.
- `skills/exakt/scripts/render_spec.py` produces the living Markdown contract.
- `skills/exakt/scripts/render_report.py` produces self-contained offline HTML.
- The journal, reducer, contract, and action helpers add canonical replay,
  crash-aware state, approval binding, and guarded external-action recovery for
  hosts that need stronger authority than local self-attestation.

## Contract and traceability

Material work follows one trace:

```text
Intent → Requirement → Behavior → Invariant → Acceptance criterion
       → Oracle → Task → Evidence → Milestone → Commit, when authorized
```

This is a relationship model, not a demand for ten documents. Stable IDs make
it possible to answer why a task exists, what observable behavior it protects,
which counterexample could expose a weak implementation, and what fresh proof
supports its status. Orphaned requirements, criteria, tasks, proof, or milestone
coverage remain visible gaps and block a verified handoff.

## Proof model

Executable behavior uses RED, GREEN, REFACTOR plus a legitimacy and
falsification gate:

- RED must fail against the pre-change subject for the intended missing
  behavior, not because of setup, syntax, fixtures, or an unrelated error.
- GREEN and regression evidence must exercise the approved behavior without
  test-only branches, hard-coded fixtures, weakened assertions, skipped tests,
  or bypassed real boundaries.
- After the final relevant change, a fresh negative, boundary, runtime, or
  separated review attempts to disprove the claim.

Non-executable work uses an artifact before-state, an explicit defect or
counterexample, and fresh artifact-appropriate proof. Passing tests are useful
evidence when applicable, never proof of every claim by themselves.

Each evidence record binds its stage and result to both a subject digest and a
contract digest. Provenance distinguishes self-attested, separated,
independent, and external-journal observations; provenance does not change the
observed result. The final claim cannot exceed the weakest required linked
evidence.

## Invalidation and recovery

When intent, repository facts, configuration, build, artifact, or runtime
changes, Exakt records the changed IDs and marks affected tasks, criteria,
milestones, and evidence stale. The old record is preserved. Work resumes from
the first valid unfinished milestone by stable ID rather than renumbering or
recreating the plan.

A blocking conflict stops affected work. Failed or unavailable proof becomes a
gap; it is never converted into success from confidence, code presence, an
agent summary, a commit, or a successful but unrelated command.

## Milestones and commits

A milestone is a coherent vertical outcome that can be demonstrated, verified,
and, where practical, reverted. Its closeout records completed behavior,
covered contract IDs, changed paths, evidence, gaps, commit state, and the
strongest supported status.

When the user authorizes commits, Exakt stages only declared milestone paths,
checks for unrelated staged work, binds proof to the staged subject, and creates
one verified commit per milestone. The commit body records milestone and
contract IDs, evidence, the approved spec digest, and remaining gaps. Without
commit authority, Exakt prepares that message but does not commit.

## Completion gate

V2 may report `verified` only at handoff when all of the following are true:

- no blocking unknown, conflict, orphan, open invalidation, or declared gap
  remains;
- every task, acceptance criterion, verification check, and milestone is
  verified;
- every required proof stage is fresh, successful, linked to the current
  subject and contract, and valid for the declared authority mode; and
- each milestone closeout covers its declared work and evidence.

Valid v1 state remains readable, renderable, and verifiable under its existing
rules, but it is labelled legacy and cannot satisfy the v2 traceability or
milestone guarantees without explicit migration and fresh proof.

### 4.9 `verification-ledger.json`

The stronger journal runtime records claims, immutable subjects, observations,
supporting and contradicting evidence, freshness, gaps, and claim results.
Independence is expressed as `verification_tier=independent`; it is not a claim
status.

### 4.10 Evidence freshness and invalidation

Evidence binds to source, build, configuration, artifact, runtime, or external
state fingerprints. Changed dependencies stale their proof; unavailable proof
remains an explicit gap.

## Resource limits

Portable contract parsing is bounded to:

- 100,000 digits per integer;
- 256 levels of JSON nesting;
- 100,000 JSON nodes per document; and
- 256 local-reference hops.

Inputs beyond these limits fail as controlled contract errors.

## Safety boundary

External, destructive, costly, security-sensitive, and production actions
still require the host to obtain explicit approval for the exact action. The
action helper persists intent before provider I/O and reconciles ambiguous
outcomes before any retry. Exakt does not initiate production deployment merely
because a plan or milestone is verified.
