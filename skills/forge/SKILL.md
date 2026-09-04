---
name: forge
description: Use when a user invokes $forge or /forge with an engineering task, bug, feature, migration, investigation, product brief, PRD, or full product specification.
---

# Forge

Turn the request into an approved engineering result whose claims match the evidence. Keep the conversation natural and the process proportional to the work.

## Start

1. Treat everything after `$forge` or `/forge` as the source brief. Read referenced files or links when the host can access them.
2. Read repository instructions and inspect the relevant code, tests, build commands, and current worktree before proposing a solution. Preserve unrelated user changes.
3. Choose **Task mode** for bounded work and **Product mode** for a PRD, broad outcome, unresolved product behavior, or work spanning multiple independently deliverable systems. Reclassify if reconnaissance changes the scope.
4. Read [references/workflows.md](references/workflows.md). For Product mode, also read [references/product-mode.md](references/product-mode.md). Read [references/harness-adapters.md](references/harness-adapters.md) before mapping work to host features.
5. Initialize the local project view with `python3 <skill-root>/scripts/forge.py init "<source brief>" --mode <task|product> --output .forge/forge-state.json`. If that path already exists, inspect it with `forge.py status` and resume it or choose a new explicit path; never overwrite it silently. Read [references/report-interface.md](references/report-interface.md) before updating the view.

Ask at most one consequential question at a time. Ask only when the answer changes scope, behavior, risk, architecture, or approval. Otherwise make a reversible assumption, state it briefly, and continue.

## Build the contract

Before implementation, produce and review:

- a specification that preserves the user's intent and identifies assumptions or conflicts;
- a recommended architecture with material alternatives and trade-offs;
- observable acceptance criteria, including relevant failure and recovery behavior; and
- a dependency-aware task plan with verification attached to each task.

Keep these compact for small tasks. Product mode requires the fuller contract in [references/product-mode.md](references/product-mode.md). Request approval for the exact execution scope; architecture approval is not permission for unrelated work or external actions.

## Use specialists deliberately

When the host provides subagents, use only roles that add useful independent work:

- [agents/repository-investigator.md](agents/repository-investigator.md) for unfamiliar or cross-cutting repositories;
- [agents/product-critic.md](agents/product-critic.md) for Product mode or unclear user value;
- [agents/architecture-critic.md](agents/architecture-critic.md) for material interfaces, migrations, security, or operational risk; and
- [agents/independent-verifier.md](agents/independent-verifier.md) for a fresh final verification pass.

Give each specialist a bounded question, relevant source material, and expected output. Specialists stay read-only, do not seek approval, do not delegate again, and return evidence or findings to the lead. The lead resolves disagreements and owns the result. If subagents are unavailable, perform separated passes yourself and do not call them independent.

## Execute the approved scope

Implement tasks in dependency order. Follow repository-native practices and tests. Use test-first work for behavior changes when practical, inspect every resulting diff or artifact, and stop before destructive, external, costly, security-sensitive, or production actions unless the user has explicitly approved that exact action.

Keep terminal updates short: current outcome, active task, important decision, or blocker. Do not narrate routine tool use.

## Verify and repair

Read [references/verification.md](references/verification.md) before making completion claims.

For each task, compare the actual output—not the plan or an agent summary—with its acceptance criteria. Run the strongest available targeted checks, inspect failures, repair the implementation or revise a falsified assumption, and repeat. Stop blind retry loops. Mark unavailable proof as `unverified` or `blocked`.

After the last relevant change, run a fresh final pass over the changed files and required checks. Use an isolated verifier when available. Never infer `fixed`, `working`, `complete`, `deployed`, or `verified` from confidence, code presence, an agent message, or one successful command.

## Handoff

Keep `.forge/forge-state.json` synchronized at meaningful phase changes, then render it with `forge.py render --force`. Before claiming completion, run `forge.py verify`; a nonzero result limits the final wording even when other tests pass. Inspect `forge.py --help` for installed syntax rather than guessing flags. A renderer failure must not erase the concise terminal handoff.

Lead with the outcome, then report only what matters:

- **Intended:** approved result.
- **Observed:** actual artifact or runtime inspected.
- **Evidence:** checks and independent review actually completed.
- **Gaps:** unverified, blocked, failed, stale, or residual items.
- **Status:** the strongest wording supported by the evidence.
