#!/usr/bin/env python3
"""Render a deterministic, self-contained Exakt report from local JSON state."""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "assets/report-template.html"
MAX_INPUT_BYTES = 16 * 1024 * 1024

VIEW_NAVIGATION = (
    ("spec", "Brief & spec"),
    ("architecture", "Architecture"),
    ("plan", "Acceptance & plan"),
    ("decisions", "Critique & decisions"),
    ("progress", "Progress"),
    ("truth", "Verification & truth"),
    ("evidence", "Files & evidence"),
)

STATUS_SYMBOLS = {
    "verified": "✓",
    "fresh": "✓",
    "accepted": "✓",
    "reviewed": "✓",
    "failed": "×",
    "blocked": "!",
    "contradicted": "↯",
    "stale": "⌛",
    "partial": "◐",
    "unverified": "?",
    "pending": "○",
    "implementing": "→",
    "active": "→",
    "open": "◇",
}

TRUTH_PRIORITY = {
    "contradicted": 0,
    "stale": 1,
    "failed": 2,
    "blocked": 3,
    "partial": 4,
    "unverified": 5,
    "pending": 6,
    "verified": 7,
    "fresh": 8,
}


class RenderError(ValueError):
    """The supplied report state cannot be rendered safely."""


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return []


def _text(value: Any, fallback: str = "Not recorded") -> str:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else fallback
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (int, float)):
        return str(value)
    return fallback


def _escape(value: Any, fallback: str = "Not recorded") -> str:
    return html.escape(_text(value, fallback), quote=True)


def _status_key(value: Any) -> str:
    raw = _text(value, "unverified").casefold().replace("_", " ")
    key = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    return key or "unverified"


def _status_badge(value: Any) -> str:
    key = _status_key(value)
    symbol = STATUS_SYMBOLS.get(key, "◇")
    label = _escape(key.replace("-", " ").title())
    return (
        f'<span class="status" data-status="{key}">'
        f'<span aria-hidden="true">{symbol}</span> {label}</span>'
    )


def _plain_list(values: Any, empty: str) -> str:
    items = _sequence(values)
    rendered = [f"<li>{_escape(item)}</li>" for item in items if isinstance(item, (str, int, float, bool))]
    if not rendered:
        return f'<p class="empty">{html.escape(empty)}</p>'
    return '<ul class="plain-list">' + "".join(rendered) + "</ul>"


def _display_value(value: Any) -> str:
    values = _sequence(value)
    if values:
        scalar_values = [
            _escape(item)
            for item in values
            if isinstance(item, (str, int, float, bool))
        ]
        return ", ".join(scalar_values) if scalar_values else "Not recorded"
    return _escape(value)


def _details_cards(
    values: Any,
    *,
    title_keys: tuple[str, ...],
    id_keys: tuple[str, ...] = ("id",),
    body_fields: tuple[tuple[str, str], ...] = (),
    empty: str,
) -> str:
    cards: list[str] = []
    for index, item in enumerate(_sequence(values)):
        record = _mapping(item)
        if record:
            title_value: Any = None
            for key in title_keys:
                if key in record:
                    title_value = record[key]
                    break
            title = _escape(title_value, f"Item {index + 1}")
            identifier_value: Any = None
            for key in id_keys:
                if key in record:
                    identifier_value = record[key]
                    break
            identifier = (
                f'<span class="record-id">{_escape(identifier_value)}</span>'
                if identifier_value is not None
                else ""
            )
            status = record.get("status", "unverified")
            paragraphs = []
            for field, label in body_fields:
                if field in record:
                    paragraphs.append(
                        f'<p><span class="micro-label">{html.escape(label)}</span><br>'
                        f"{_display_value(record[field])}</p>"
                    )
        elif isinstance(item, (str, int, float, bool)):
            title = _escape(item)
            identifier = ""
            status = "unverified"
            paragraphs = []
        else:
            continue

        status_key = _status_key(status)
        opened = " open" if index == 0 or status_key in {"blocked", "contradicted", "stale", "failed"} else ""
        body = "".join(paragraphs) or "<p>No additional detail recorded.</p>"
        cards.append(
            f'<details class="record"{opened}>'
            f"<summary><span class=\"record-title\">{identifier}{title}</span>"
            f"{_status_badge(status)}</summary>"
            f'<div class="record-body">{body}</div>'
            "</details>"
        )
    if not cards:
        return f'<p class="empty">{html.escape(empty)}</p>'
    return '<div class="record-stack">' + "".join(cards) + "</div>"


def _heading(index: int, title: str) -> str:
    return (
        '<div class="section-heading">'
        f'<p class="section-index">{index:02d}</p>'
        f"<h2>{html.escape(title)}</h2>"
        "</div>"
    )


def _spec_section(state: Mapping[str, Any]) -> str:
    brief = _mapping(state.get("brief"))
    requirements = _details_cards(
        state.get("requirements"),
        title_keys=("text", "title", "name"),
        body_fields=(("rationale", "Rationale"), ("proof", "Proof")),
        empty="No requirements have been recorded.",
    )
    return (
        '<section id="spec" data-report-view="spec" class="report-view">'
        + _heading(1, "Brief & spec")
        + f'<p class="lede">{_escape(brief.get("outcome"), "No outcome has been recorded.")}</p>'
        + '<div class="split">'
        + '<div class="panel"><h3>People in the frame</h3>'
        + _plain_list(brief.get("users"), "No users named.")
        + "</div>"
        + '<div class="panel"><h3>Working constraints</h3>'
        + _plain_list(brief.get("constraints"), "No constraints recorded.")
        + "</div></div>"
        + '<h3 class="subhead" style="margin-top:2rem">Requirements ledger</h3>'
        + requirements
        + "</section>"
    )


def _architecture_section(state: Mapping[str, Any]) -> str:
    architecture = _mapping(state.get("architecture"))
    components = _details_cards(
        architecture.get("components"),
        title_keys=("name", "title", "id"),
        id_keys=("id", "kind"),
        body_fields=(
            ("responsibility", "Responsibility"),
            ("interface", "Interface"),
            ("interfaces", "Interfaces"),
            ("failure_boundary", "Failure boundary"),
        ),
        empty="No components recorded.",
    )
    return (
        '<section id="architecture" data-report-view="architecture" class="report-view">'
        + _heading(2, "Architecture")
        + f'<p class="lede">{_escape(architecture.get("overview"), "No architecture overview recorded.")}</p>'
        + '<div class="split"><div><h3 class="subhead">System components</h3>'
        + components
        + '</div><div class="panel"><h3>Architecture decisions</h3>'
        + _plain_list(architecture.get("decisions"), "No architecture decisions recorded.")
        + "</div></div></section>"
    )


def _plan_section(state: Mapping[str, Any]) -> str:
    criteria = _details_cards(
        state.get("acceptance_criteria"),
        title_keys=("text", "title", "name"),
        body_fields=(("evidence", "Evidence"), ("requirement", "Requirement")),
        empty="No acceptance criteria recorded.",
    )
    tasks = _details_cards(
        state.get("tasks"),
        title_keys=("title", "text", "name", "id"),
        body_fields=(
            ("owner", "Owner"),
            ("depends_on", "Depends on"),
            ("verification", "Verification"),
            ("attempts", "Attempts"),
        ),
        empty="No implementation tasks recorded.",
    )
    return (
        '<section id="plan" data-report-view="plan" class="report-view">'
        + _heading(3, "Acceptance & plan")
        + '<div class="split"><div><h3 class="subhead">Acceptance criteria</h3>'
        + criteria
        + '</div><div><h3 class="subhead">Task plan</h3>'
        + tasks
        + "</div></div></section>"
    )


def _decisions_section(state: Mapping[str, Any]) -> str:
    critiques = _details_cards(
        state.get("critiques"),
        title_keys=("finding", "title", "text"),
        id_keys=("source", "id"),
        body_fields=(("rationale", "Rationale"), ("disposition", "Disposition")),
        empty="No specialist critiques recorded.",
    )
    decisions = _details_cards(
        state.get("decisions"),
        title_keys=("title", "text", "decision", "id"),
        body_fields=(
            ("rationale", "Rationale"),
            ("owner", "Owner"),
            ("impact", "Impact"),
        ),
        empty="No material decisions recorded.",
    )
    return (
        '<section id="decisions" data-report-view="decisions" class="report-view">'
        + _heading(4, "Critique & decisions")
        + '<p class="lede">Challenges stay visible beside their dispositions; agreement is not proof.</p>'
        + '<div class="split"><div><h3 class="subhead">Critical review</h3>'
        + critiques
        + '</div><div><h3 class="subhead">Decision record</h3>'
        + decisions
        + "</div></div></section>"
    )


def _task_counts(tasks: list[Any]) -> tuple[int, int, int, int]:
    completed = active = blocked = queued = 0
    for item in tasks:
        status = _status_key(_mapping(item).get("status", "pending"))
        if status in {"verified", "complete", "completed", "done"}:
            completed += 1
        elif status in {"blocked", "failed"}:
            blocked += 1
        elif status in {"implementing", "observing", "verifying", "repairing", "active"}:
            active += 1
        else:
            queued += 1
    return completed, active, blocked, queued


def _progress_section(state: Mapping[str, Any]) -> str:
    tasks = _sequence(state.get("tasks"))
    completed, active, blocked, queued = _task_counts(tasks)
    metrics = "".join(
        f'<div class="metric"><strong>{count}</strong><span>{label}</span></div>'
        for count, label in (
            (completed, "Complete"),
            (active, "In motion"),
            (blocked, "Blocked"),
            (queued, "Queued"),
        )
    )
    runway = _details_cards(
        tasks,
        title_keys=("title", "text", "name", "id"),
        body_fields=(("owner", "Owner"), ("depends_on", "Depends on")),
        empty="No task progress recorded.",
    )
    return (
        '<section id="progress" data-report-view="progress" class="report-view">'
        + _heading(5, "Progress")
        + '<p class="lede">A compact operating view of what moved, what waits, and what blocks the next proof.</p>'
        + f'<div class="progress-grid">{metrics}</div>'
        + '<h3 class="subhead" style="margin-top:2rem">Delivery runway</h3>'
        + runway
        + "</section>"
    )


def _gap_values(value: Any) -> list[str]:
    gaps: list[str] = []
    for item in _sequence(value):
        if isinstance(item, (str, int, float, bool)):
            gaps.append(_text(item))
        elif isinstance(item, Mapping):
            for key in ("text", "title", "gap", "description"):
                if key in item:
                    gaps.append(_text(item[key]))
                    break
    return gaps


def _sorted_truth(value: Any) -> list[Any]:
    indexed = list(enumerate(_sequence(value)))
    indexed.sort(
        key=lambda pair: (
            TRUTH_PRIORITY.get(_status_key(_mapping(pair[1]).get("status")), 5),
            pair[0],
        )
    )
    return [item for _index, item in indexed]


def _truth_section(state: Mapping[str, Any]) -> str:
    gaps = _gap_values(state.get("gaps"))
    gap_html = (
        '<ol class="risk-list">' + "".join(f"<li>{html.escape(gap)}</li>" for gap in gaps) + "</ol>"
        if gaps
        else '<p class="empty">No unresolved gaps recorded.</p>'
    )
    verification = _details_cards(
        _sorted_truth(state.get("verification")),
        title_keys=("name", "claim", "title", "text"),
        body_fields=(
            ("evidence", "Observation"),
            ("proof_type", "Proof type"),
            ("freshness", "Freshness"),
            ("counterexample", "Counterexample"),
        ),
        empty="No verification observations recorded.",
    )
    return (
        '<section id="truth" data-report-view="truth" class="report-view">'
        + _heading(6, "Verification & truth")
        + '<p class="lede">Contradictions and stale proof lead this ledger; passing evidence follows.</p>'
        + '<h3 class="subhead">Unresolved gaps</h3>'
        + gap_html
        + '<h3 class="subhead" style="margin-top:2rem">Truth ledger</h3>'
        + verification
        + "</section>"
    )


def _evidence_section(state: Mapping[str, Any]) -> str:
    files = _details_cards(
        state.get("files"),
        title_keys=("path", "name", "title"),
        id_keys=("id",),
        body_fields=(("change", "Change"), ("digest", "Digest"), ("note", "Note")),
        empty="No changed files recorded.",
    )
    evidence = _details_cards(
        state.get("evidence"),
        title_keys=("name", "title", "command", "id"),
        body_fields=(
            ("type", "Evidence type"),
            ("detail", "Detail"),
            ("result", "Result"),
            ("digest", "Digest"),
        ),
        empty="No evidence artifacts recorded.",
    )
    return (
        '<section id="evidence" data-report-view="evidence" class="report-view">'
        + _heading(7, "Files & evidence")
        + '<div class="split"><div><h3 class="subhead">Changed files</h3>'
        + files
        + '</div><div><h3 class="subhead">Evidence index</h3>'
        + evidence
        + "</div></div></section>"
    )


def _attention(state: Mapping[str, Any]) -> str:
    gaps = _gap_values(state.get("gaps"))
    risky = [
        item
        for item in _sequence(state.get("verification"))
        if _status_key(_mapping(item).get("status")) in {"contradicted", "stale", "failed", "blocked"}
    ]
    if not gaps and not risky:
        return '<div class="attention"><p class="micro-label">Attention queue</p><p>No blocking gap recorded.</p></div>'
    notes = []
    if gaps:
        notes.append(gaps[0])
    if risky:
        notes.append(
            _text(
                _mapping(risky[0]).get("name")
                or _mapping(risky[0]).get("title")
                or _mapping(risky[0]).get("claim"),
                "Verification risk",
            )
        )
    return (
        '<div class="attention" role="alert"><p class="micro-label">Attention queue</p><p>'
        + " · ".join(html.escape(note) for note in notes)
        + "</p></div>"
    )


def _report_body(state: Mapping[str, Any]) -> str:
    title = _escape(state.get("title"), "Untitled Exakt report")
    summary = _escape(state.get("summary"), "No summary has been recorded.")
    mode = _text(state.get("mode"), "task")
    status = _text(state.get("status"), "unverified")
    phase = _text(state.get("phase"), "intake")
    updated_at = _text(state.get("updated_at"), "Not recorded")
    schema_version = _text(state.get("schema_version"), "unknown")
    tasks = _sequence(state.get("tasks"))
    verification = _sequence(state.get("verification"))
    navigation = "".join(
        f'<li><a href="#{section_id}">{html.escape(label)}</a></li>'
        for section_id, label in VIEW_NAVIGATION
    )
    sections = "".join(
        section(state)
        for section in (
            _spec_section,
            _architecture_section,
            _plan_section,
            _decisions_section,
            _progress_section,
            _truth_section,
            _evidence_section,
        )
    )
    return f'''<header class="masthead" data-schema-version="{html.escape(schema_version, quote=True)}">
    <div class="masthead-grid">
      <div>
        <p class="kicker">Exakt / {html.escape(mode)} studio report</p>
        <h1>{title}</h1>
      </div>
      <div>
        <p class="masthead-summary">{summary}</p>
        <p class="meta-strip"><span>Phase · {html.escape(phase)}</span><span>Updated · {html.escape(updated_at)}</span></p>
      </div>
    </div>
  </header>
  <div class="studio">
    <aside class="rail" aria-label="Report summary">
      <div class="rail-inner">
        <div class="state-card">
          <p class="micro-label">Current state</p>
          <h2>{html.escape(phase.title())}</h2>
          {_status_badge(status)}
          <dl>
            <dt>Mode</dt><dd>{html.escape(mode.title())}</dd>
            <dt>Tasks</dt><dd>{len(tasks)}</dd>
            <dt>Proof rows</dt><dd>{len(verification)}</dd>
          </dl>
          {_attention(state)}
        </div>
        <nav class="phase-nav" aria-label="Seven report views">
          <p class="nav-label">Report index</p>
          <ol>{navigation}</ol>
        </nav>
      </div>
    </aside>
    <main id="report-main" class="report" tabindex="-1">
      {sections}
      <aside class="feedback" aria-labelledby="feedback-title">
        <p class="micro-label">Local review loop</p>
        <h2 id="feedback-title">Leave the work sharper.</h2>
        <p>Draft only — this feedback grants no approval. Copy or download a local JSON proposal, then review it through Exakt.</p>
        <label for="feedback-text">Feedback for this report</label>
        <textarea id="feedback-text" placeholder="Name the claim, decision, or task you want changed."></textarea>
        <div class="button-row">
          <button type="button" id="copy-feedback" class="primary">Copy JSON</button>
          <button type="button" id="download-feedback">Download JSON</button>
          <button type="button" id="expand-all">Expand details</button>
          <button type="button" id="collapse-all">Collapse details</button>
          <a id="feedback-download-anchor" hidden download="exakt-feedback.json"></a>
        </div>
        <p id="feedback-status" class="feedback-status" aria-live="polite"></p>
      </aside>
      <p class="footer-note">Local artifact · no remote dependencies · project text escaped</p>
    </main>
  </div>'''


def render_report(state: dict[str, Any]) -> str:
    """Return deterministic HTML for one Exakt report-state mapping."""
    if not isinstance(state, dict):
        raise RenderError("report state must be a JSON object")
    try:
        template = TEMPLATE_PATH.read_text(encoding="utf-8")
    except OSError as error:
        raise RenderError("report template is unavailable") from error
    if template.count("{{DOCUMENT_TITLE}}") != 1 or template.count("{{REPORT_BODY}}") != 1:
        raise RenderError("report template markers are invalid")
    title = _text(state.get("title"), "Untitled Exakt report")
    document_title = html.escape(f"{title} — Exakt report", quote=True)
    return template.replace("{{DOCUMENT_TITLE}}", document_title).replace(
        "{{REPORT_BODY}}", _report_body(state)
    )


def _load_state(path: Path) -> dict[str, Any]:
    try:
        payload = path.read_bytes()
    except OSError as error:
        raise RenderError(f"cannot read input: {error}") from error
    if len(payload) > MAX_INPUT_BYTES:
        raise RenderError(f"input exceeds {MAX_INPUT_BYTES} bytes")
    try:
        state = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RenderError(f"input is not valid UTF-8 JSON: {error}") from error
    if not isinstance(state, dict):
        raise RenderError("report state must be a JSON object")
    return state


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render a self-contained local Exakt HTML report."
    )
    parser.add_argument("input", type=Path, help="Exakt report-state JSON")
    parser.add_argument("--output", type=Path, required=True, help="HTML output path")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        rendered = render_report(_load_state(args.input))
        args.output.write_text(rendered, encoding="utf-8", newline="\n")
    except (OSError, RenderError) as error:
        print(f"render_report.py: error: {error}", file=sys.stderr)
        return 2
    print(f"Rendered local Exakt report: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
