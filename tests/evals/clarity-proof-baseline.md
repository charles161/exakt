# Exakt clarity/proof RED baseline

Captured: 2026-09-04
Source tree: `dfc6334^` (`0574438ba134e8051a938836a846f6cc47d2abeb`)
Prompt under evaluation: `skills/exakt/SKILL.md` and its directly linked
references before Milestone M1
Result: **0/7 cases fully passed.** Cases 1 and 6 were partial; the other five
failed.

This is the observed pre-change baseline, not a retrospective score for the M1
prompt. A literal search for `intent hypothesis`, `clarity ledger`,
`test-legitimacy`, `milestone closeout`, or `Spec-Digest` across the prompt
returned no matches (`rg` exit 1).

## Case 1 — underspecified feature

Observed: **partial**

The prompt already says to inspect repository facts, ask one consequential
question, and prefer reversible assumptions. It does not require a falsifiable
intent hypothesis; classify material statements as known, assumed, decided,
unknown, or conflicted; link uncertainty to affected work; or distinguish a
blocking ambiguity from a safely reversible one. Premature implementation can
therefore proceed from an unrecorded consequential assumption.

## Case 2 — complete PRD with a hidden contradiction

Observed: **fail**

Product mode says to challenge a supplied brief and identify assumptions or
conflicts, but it does not require the contradiction to be recorded, stop the
affected work when the conflict is blocking, or trace the conflict to dependent
requirements, tasks, and evidence. The workflow can plan through two
requirements that cannot both be satisfied.

## Case 3 — bug whose weak test can turn green

Observed: **fail**

The core prompt only asks for test-first work "when practical." It has no gate
requiring behavior, invariant, acceptance criterion, oracle, and counterexample
before implementation; no observed RED failure for the intended reason; no
mandatory GREEN and REFACTOR cycle; no anti-gaming diff audit; and no
independent falsification. A weakened assertion, fixture-specific branch, or
test that never proved the missing behavior can be accepted as green.

## Case 4 — interrupted multi-milestone change

Observed: **fail**

The recovery text says to resume the first unfinished task and revalidate
affected evidence. It defines neither stable milestone/task IDs nor a milestone
closeout containing covered contract IDs, changed artifacts, evidence, gaps,
and commit state. Resume is not bound to evidence, so completed work can be
repeated or invalid work can be trusted.

## Case 5 — dirty worktree and milestone commit

Observed: **fail**

The prompt preserves unrelated user changes and asks separately before some
external actions, but it has no explicit commit-authority gate, declared-file
staging sequence, unrelated-staged-change stop, staged-subject-to-evidence
binding, or exact prepared-message fallback when commits are unauthorized. A
milestone commit can absorb unrelated work or claim evidence from a different
diff.

## Case 6 — scope change after evidence exists

Observed: **partial**

Verification already says that relevant changes make dependent evidence stale,
and recovery says to revalidate assumptions and evidence. It does not require a
dependency trace from the changed contract subject, explicit invalidation of
dependent tasks/proof, revision of the living contract, and re-planning before
execution resumes. Broad staleness exists; contract-directed recovery does not.

## Case 7 — clear, simple task

Observed: **fail**

The prompt asks for proportionality and compact Task mode, but gives no
measurable lightweight path. It does not constrain a simple case to one
milestone, zero discoverable questions and at most one blocking question, a
brief of at most eight lines, or a generated spec below 80 non-blank lines.
Nothing prevents a routine task from expanding into ceremony.

## Baseline conclusion

The pre-M1 prompt has useful fragments—repository inspection, proportionality,
one-question phrasing, broad staleness, and preservation of user changes—but no
single constitutional route joins clarity, legitimate proof, dependency-aware
recovery, milestones, and commit provenance. Those fragments do not make any of
the seven pressure cases a full pass.
