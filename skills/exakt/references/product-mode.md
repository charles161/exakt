# Product mode

Use Product mode for a product brief, PRD, end-to-end product specification, broad outcome needing discovery, or work spanning multiple independently deliverable systems or rollout stages.

## Build a coherent product contract

Preserve the supplied brief as source intent, but challenge it rather than treating it as complete. Produce:

1. Problem, users, jobs, journeys, and measurable outcomes.
2. Repository and domain findings, sources, assumptions, and unresolved risks.
3. Functional and non-functional requirements, constraints, and non-goals.
4. Concrete acceptance scenarios for success, boundaries, failure, and recovery.
5. Architecture alternatives and a recommendation covering components, interfaces, data, state, trust boundaries, operations, migration, rollout, and rollback where relevant.
6. Staged delivery slices with dependencies and proof for every material claim.

Use the repository investigator for system facts, the product critic for missing value or contradictory requirements, and the architecture critic for technical failure modes when host subagents are available. Assign each one a narrow perspective; do not convene a ceremonial fixed crew.

## Ask for decisions sparingly

Ask one question only when the answer materially changes the product or architecture. Present two or three viable choices, recommend one, and state the trade-off. Resolve minor gaps with labeled reversible assumptions.

## Gate execution by slice

Stop at the plan by default. Architecture or product-contract approval does not authorize implementation of the whole product.

Before execution, name the delivery slice and show its:

- outcome and acceptance criteria;
- target repositories and boundaries;
- dependencies and task plan;
- verification approach;
- resource limits; and
- external-action policy.

Execute only the approved slice. Treat each later slice as a new bounded approval. If scope expands during implementation, return to the contract and seek approval for the changed slice.
