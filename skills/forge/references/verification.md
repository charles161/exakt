# Verification and repair

Verification compares approved intent with the actual result. Plans, summaries, code presence, screenshots, and successful tool calls are inputs—not proof by themselves.

## Compact truth loop

For each acceptance criterion:

1. Inspect the current diff, file, built artifact, database state, provider receipt, or runtime that the claim concerns.
2. Run claim-appropriate proof: a test for behavior, build for buildability, interaction for UX, migration inspection for data state, or live probe for deployed behavior.
3. Exercise relevant negative, boundary, compatibility, failure, and recovery cases.
4. Record what was intended, what was observed, the evidence, and any gap.
5. Repair a defect, strengthen weak proof, or revise a falsified design assumption; then inspect and test again.

After any relevant source, configuration, artifact, or runtime change, treat dependent evidence as stale until rerun.

## Status language

- `verified`: fresh evidence directly proves the criterion against the inspected subject.
- `partially_verified`: only part of the criterion is proved.
- `failed`: observed behavior contradicts the criterion.
- `blocked`: required authority, environment, or observable state is unavailable.
- `unverified`: proof was not run or cannot support the claim.
- `stale`: the proved subject changed afterward.
- `contradicted`: credible evidence disagrees; investigate instead of selecting the favorable result.

Use `independently verified` only when a fresh isolated agent or process received the approved contract and actual subject, discovered evidence itself, and reran the required checks without inheriting the implementer's conclusion.

## False-claim traps

Do not claim:

- a bug is fixed from a new passing test that was never observed failing or from code inspection alone;
- a build works from unit tests alone;
- a UI works from a screenshot without interaction;
- a deployment is live from a deploy command's exit code;
- an external action occurred once without a provider receipt or idempotency record; or
- the whole task is complete while required evidence is missing, stale, failed, or contradicted.

Stop repeated identical failures. Diagnose the cause, change the implementation or plan, or report the blocker. Before handoff, rerun checks affected by the final edits and inspect the final diff or artifact.
