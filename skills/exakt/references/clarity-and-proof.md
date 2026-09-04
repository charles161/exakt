# Clarity and proof contract

Read this contract before asking requirements questions, planning,
implementing, or closing work. Its gates are mandatory; their depth is
proportional to risk and scope.

## Clarity loop

1. Inspect the brief, repository instructions, relevant code, tests, build
   commands, open plans, and worktree before asking the user for discoverable
   facts.
2. State a falsifiable, one-sentence intent hypothesis. Record confidence, the
   reason for low confidence, and the most consequential unresolved point.
3. Maintain a clarity ledger using exactly these classes:
   **known / assumed / decided / unknown / conflicted**. Every consequential
   entry records its source, what it affects, and whether it is blocking.
4. Rank uncertainty by user-visible impact, blast radius, and reversibility.
   Ask **one consequential question** only when its answer can materially
   change behavior, scope, architecture, risk, or authority. Include your
   **best guess** and why. Never run a generic discovery interview.
5. After each answer, show a compact **decision delta**: what changed and the
   next consequential open item, if any.
6. Stop asking when behavior and boundaries are predictable, no unresolved
   item can materially change architecture or safety, and remaining
   assumptions are reversible and covered by observable criteria.

A **blocking conflict** stops the affected planning or implementation. Record
both sides and their sources, show the smallest decision required, and do not
silently choose the convenient interpretation.

For a clear, bounded task, use one milestone, ask zero discoverable questions
and at most one blocking question, keep the conversational brief to at most eight lines,
and keep the generated specification below 80 non-blank lines unless the actual
risk requires more.

## Living contract and trace

Use stable IDs and maintain this material chain:

**Intent → Requirement → Behavior → Invariant → Acceptance criterion → Oracle
→ Task → Evidence → Milestone → Commit**, when commits are authorized.

The working trace may be compact, but it must let a reviewer answer why a task
exists, which behavior it protects, how failure would be observed, and which
fresh evidence supports its status. The core proof chain is:

**Behavior → Invariant → Acceptance criterion → Oracle**.

Before executable behavior work, define:

- the observable behavior and important non-goals;
- invariants across success, boundary, failure, and recovery paths;
- an oracle that can distinguish compliant from non-compliant behavior; and
- at least one concrete counterexample capable of exposing a weak
  implementation or weak proof.

For non-executable work, define the intended artifact change, a before-state,
an artifact-specific criterion and oracle, and a counterexample or defect the
fresh proof must rule out. If the applicable chain cannot be stated honestly,
reopen clarity or design rather than manufacturing a test.

## Reopening and invalidation

When repository facts, a failing check, runtime evidence, or review contradicts
the contract:

1. record the contradiction and the changed contract IDs;
2. **mark dependent tasks and proof stale** by following trace links;
3. revise the assumption, requirement, behavior, invariant, criterion, or
   architecture before changing implementation;
4. ask the user only if approved behavior, risk, or irreversible scope changes;
5. re-plan the affected work and rerun proof against the new contract digest.

Preserve the change history. Never rewrite prior assumptions or evidence to
make a changed plan appear inevitable.

## Legitimate TDD and proof

Executable behavior uses RED, GREEN, REFACTOR:

1. **RED:** write or select an externally meaningful test, run it against the
   pre-change subject, and confirm it **fails for the intended reason** rather
   than setup, syntax, fixture, environment, or unrelated failure. Record the
   command, result, subject digest, and contract digest.
2. **GREEN:** make the smallest coherent production change that satisfies the
   approved criterion. Run the focused test and relevant regression/build
   checks.
3. **REFACTOR:** improve structure only while the same behavior remains green;
   inspect the final diff and rerun affected checks.
4. Run the **test-legitimacy gate** and a fresh falsification before marking the
   behavior verified. Use **independent falsification** when isolation exists;
   otherwise use a clearly labeled separated fresh pass.

Non-executable work such as prose or visual assets uses a **before-state**, a
counterexample or defect oracle, the changed artifact, and fresh proof. Do not
write meaningless tests merely to imitate TDD.

### Test-legitimacy gate

Inspect the production and test diff together. Each item below invalidates the proof and blocks milestone closure:

- deleted, skipped, quarantined, or renamed tests that reduce coverage;
- weakened assertions, thresholds, validation, types, or quality gates;
- fixture hard-coding, test-environment branches, fake stubs, or bypassed real
  boundaries used only to obtain green;
- snapshots or golden files changed without inspecting and approving the
  semantic delta;
- new tests written after the behavior already passed and presented as RED;
- evidence for a different source, build, configuration, artifact, or runtime.

These acts cannot be excused merely because the suite passes. If an approved
contract change legitimately changes a test, assertion, threshold, fixture, or
snapshot, record the change, stale the old proof, capture the new intended RED
or artifact before-state, and gather fresh evidence for the revised claim.

Fresh falsification means a fresh agent or separated fresh pass receives the
approved contract and actual subject—not the implementer's conclusion—and
tries a counterexample, negative/boundary path, or alternate oracle. A separated
pass satisfies the falsification gate when isolation is unavailable, but only a
genuinely isolated provenance may be called independently verified.

Passing tests are necessary evidence when applicable, never sufficient proof
of the whole claim.

## Native plan and recovery

After approval, assign **stable M<N>** milestone IDs and stable task IDs. Mirror
one host-plan item per milestone and task-level TODOs only when the harness can
show them without clutter. Keep at most one milestone in progress unless the
user explicitly approves independent parallel work.

When resuming, **merge status by stable ID** rather than recreating or
renumbering items. Update status only from recorded fresh evidence. If a host
plan operation would replace an unrelated active plan, stop and ask before
mapping Exakt work.

## Milestone closeout

A milestone is a coherent vertical outcome that can be demonstrated, verified,
and reverted. Close it only after inspecting the actual result. Use this exact
shape, replacing every placeholder:

```text
Milestone: M<N> — <outcome>
Completed: <user-visible and technical result>
Covered: <requirement / behavior / invariant / acceptance-criterion IDs>
Changed: <important files or artifacts>
Proved: <fresh evidence IDs, commands, inspections, and outcomes>
Gaps: <none, or honest unresolved items>
Commit: <hash, prepared message, not authorized, or blocked>
Status: <verified | partially_verified | failed | contradicted | blocked | unverified | stale>
```

The recorded proof also binds subject and contract digests and includes the
test-legitimacy and falsification results.

Never mark a milestone verified while required evidence is absent, stale,
failed, contradicted, or bound to another subject.

## Commit gate

Commit only when the user has authorized commits for the exact scope. For each
milestone:

1. inspect the worktree and declare the exact files belonging to it;
2. stop if **unrelated changes are already staged**;
3. stage declared paths only—never broad or unresolved globs;
4. inspect the staged diff, bind verification to that staged subject, and rerun
   checks affected by any staging-time change;
5. create one verified commit for the milestone.

Use this body shape with real IDs and values:

```text
Milestone: M<N>
Implements: <contract IDs>
Protects: <invariant IDs>
Accepts: <acceptance-criterion IDs>
Evidence: <evidence IDs or concise commands>
Spec-Digest: sha256:<digest of approved contract projection>
Gaps: <none or explicit gaps>
```

If commit authority is absent, **prepare this exact message** for the user but
do not commit. A commit hash proves history was written; it does not prove the
feature works.

## Claim ceiling

Final language cannot be stronger than fresh evidence for the exact subject:

- `verified`: all required direct proof is fresh and no material gap remains;
- `partially_verified`: only part of the claim is proved;
- `failed` or `contradicted`: observation disagrees with intent;
- `blocked`: necessary authority, environment, or observable state is absent;
- `unverified`: proof was not run or is insufficient;
- `stale`: the proved subject changed afterward.

Report intended outcome, observed result, evidence, gaps, milestone coverage,
and status. Never upgrade a claim from confidence, code presence, a successful
tool call, an agent summary, or a commit alone.
