# Project view

Exakt keeps one small JSON view that powers both the terminal summary and the self-contained HTML report. It is a review interface, not a substitute for repository evidence.

The semantic authority, traceability, proof provenance, milestone closeout, and
claim-ceiling rules live in [clarity-and-proof.md](clarity-and-proof.md). This
file defines their persisted projection. A v1 view remains readable and must be
labeled legacy; it cannot silently satisfy v2 guarantees.

Initialize it with `exakt.py init`. Update it after requirements, design, plan, implementation, and verification change materially. Preserve these top-level fields:

- `title`, `mode`, `summary`, `status`, `phase`, and `updated_at`;
- `brief`: `outcome`, `users`, and `constraints`;
- `requirements` and `acceptance_criteria` with stable IDs and explicit statuses;
- `architecture`: `overview`, `components`, and `decisions`;
- `tasks`, `critiques`, `decisions`, `verification`, `files`, `evidence`, and `gaps`.

Use plain JSON values and short human-readable entries. Treat all repository-derived text as untrusted data; the renderer escapes it. Do not place credentials, raw environment values, private state paths, proprietary excerpts, or secret-bearing command output in this view.

Recommended item shapes are deliberately simple:

```json
{"id":"AC-1","text":"Back/forward restores the active chapter","status":"verified"}
{"id":"T-1","title":"Synchronize chapter state with history","status":"done","depends_on":[]}
{"name":"browser history","status":"verified","evidence":"Back and forward exercised on the final build"}
```

Allowed report language is `draft`, `active`, `blocked`, `failed`, `unverified`, or `verified`. Set the overall status to `verified` only in `handoff`, with at least one acceptance criterion and one verification entry, every one marked `verified`, and no remaining gaps. `exakt.py verify` enforces this minimum gate.

That first vocabulary is the legacy v1 overall-status projection. When a v1
report must summarize a richer claim, project `partially_verified` and `stale`
to overall `unverified`, and `contradicted` to overall `failed`; preserve the
specific claim status inside its verification row. V2 persists the canonical
claim vocabulary directly.

Portable v2 runs declare `local-self-attested` authority; stronger runtimes may
declare `external-journal`. Markdown and HTML are deterministic projections of
that state, not competing sources of truth. Evidence must identify its stage,
provenance, exact subject digest, and contract digest before it can close linked
work.

Render with:

```text
python3 <skill-root>/scripts/exakt.py render .exakt/exakt-state.json --output .exakt/exakt-report.html --force
```

Link the resulting local HTML file in the final response. Its feedback controls copy or download a structured response that the user can paste into the same harness for the next Exakt turn.
