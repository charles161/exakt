# Exakt design

Exakt is a portable workflow skill for Codex, Claude Code, and compatible
engineering harnesses. It turns a bounded task or a full product brief into a
repository-grounded specification, architecture, acceptance criteria, task
plan, implementation loop, and evidence-calibrated handoff.

## Product boundary

Exakt is a skill plus deterministic local helpers. It is not an IDE, hosted
service, or replacement agent runtime. The host remains responsible for tool
permissions, process isolation, and user approval.

## Workflow

1. Preserve the source brief and inspect the real repository.
2. Choose a proportional task or product workflow.
3. Ask only questions that materially change behavior, scope, risk, or design.
4. Define requirements, architecture, observable acceptance criteria, and a
   dependency-aware plan before implementation.
5. Use bounded specialist agents only when they add independent evidence or
   useful critique.
6. Implement in the approved scope and inspect the actual result.
7. Compare each material claim with fresh evidence and repair or re-plan when
   the evidence contradicts the intended result.
8. Render the current state as a self-contained local HTML project view.

## Runtime pieces

- `skills/exakt/SKILL.md` contains the portable reasoning workflow.
- `skills/exakt/references/` contains task, product, harness, report, and
  verification guidance loaded only when relevant.
- `skills/exakt/agents/` defines read-only specialist roles.
- `skills/exakt/scripts/exakt.py` initializes, summarizes, renders, and applies
  the minimal completion gate to report state.
- `skills/exakt/scripts/render_report.py` converts state into deterministic,
  escaped, offline HTML.
- The contract, journal, reducer, and action helpers provide closed schemas,
  deterministic replay, crash-aware state, approval binding, and guarded
  external-action recovery for hosts that need the stronger runtime.

## State and truth

The ordinary project view uses `.exakt/exakt-state.json` and a regenerable HTML
projection. A report may say `verified` only when it is at handoff, every
acceptance criterion is verified, every recorded verification check is
verified, and no declared gap remains.

The stronger journal runtime stores canonical JSON objects and hash-chained
events outside an untrusted target repository. It fails closed on malformed,
divergent, truncated, or ambiguous authority and never treats an agent message
or a successful command as proof of completion.

### 4.9 `verification-ledger.json`

Each row records the approved claim, immutable subject, observed behavior,
supporting or contradicting evidence, freshness, gaps, and claim result. Claim
status is one of `verified`, `partially_verified`, `failed`, `blocked`,
`unverified`, `stale`, or `contradicted`. Independence is recorded separately
as `verification_tier=independent`; it is not a claim result.

Only proof gathered against the recorded subject can satisfy a claim. Changed
inputs make dependent evidence stale. Conflicting observations become
`contradicted` until a new subject or strengthened proof contract resolves the
conflict without erasing history.

### 4.10 Evidence freshness and invalidation

Evidence binds to the relevant source, build, configuration, artifact, runtime,
or external-state fingerprint. Exakt invalidates proof when those dependencies
change and records unavailable proof as a gap rather than inferring success.

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
outcomes before any retry. Exakt v1 does not initiate production deployments.
