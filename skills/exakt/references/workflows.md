# Exakt workflows

Use the same compact lifecycle for every request; vary depth, not truthfulness.
The clarity classes, trace links, legitimate proof loop, stable milestone
closeout, and commit gate are defined once in
[clarity-and-proof.md](clarity-and-proof.md) and govern every phase below.

| Phase | Produce | Exit when |
|---|---|---|
| Intake | Source brief, target, constraints | The request and repository scope are identifiable |
| Recon | Relevant instructions, code paths, tests, risks, unknowns | The current system is understood well enough to specify change |
| Requirements | Intent hypothesis, clarity delta, behavior, constraints, non-goals | Material ambiguity is resolved or explicitly assumed under the clarity contract |
| Design | Recommended approach, alternatives, interfaces, failure/rollback concerns | Consequential trade-offs are reviewed |
| Plan | Dependency-ordered tasks and per-task proof | Exact execution scope is approved |
| Execute | Working changes inside approved scope using the correct behavior or artifact proof loop | No task remains silently in progress |
| Verify | Actual-output inspection, legitimacy audit, and fresh falsification | Every material claim is proved or classified honestly |
| Handoff | Milestone closeout, concise summary, and rendered report | Status language matches the evidence |

## Task mode

Keep the contract brief but explicit: problem, intended behavior, architecture impact, acceptance criteria, task list, and verification. Adapt emphasis:

- **Bug:** reproduce or capture the strongest evidence first; verify the original symptom after repair.
- **Feature:** define user-visible behavior, interfaces, edge cases, and rollout impact.
- **Migration/refactor:** define compatibility, ownership, rollback, and behavior-preservation checks.
- **Investigation:** define a falsifiable question, evidence policy, and decision; do not force code changes.

Apply the one-question and measurable lightweight-task rules from the canonical
contract. Prefer a sourced, stated reversible assumption for minor details.

## Approval and execution

Show the proposed specification, architecture, acceptance criteria, and plan before writing. Ask for approval of the exact scope if it has not already been given. Continue autonomously through ordinary reversible work inside that scope.

Request separate approval before destructive commands, external writes or messages, publication, purchases, access changes, irreversible migrations, or other material side effects. Never initiate a production deployment.

## Recovery

On resume, inspect the worktree and persisted Exakt status before acting. Merge
native progress by stable IDs, follow trace links to invalidate changed proof,
and continue from the first unfinished valid task. Do not repeat a confirmed
external effect. If state, authority, or an external outcome is ambiguous, stop
the affected action and explain the smallest decision or evidence needed.
