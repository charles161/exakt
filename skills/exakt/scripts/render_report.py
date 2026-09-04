#!/usr/bin/env python3
"""Render a deterministic, self-contained Exakt report from local JSON state."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import tempfile
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
    "known": "●",
    "assumed": "≈",
    "decided": "✓",
    "unknown": "?",
    "conflicted": "↯",
    "ready": "◇",
    "done": "✓",
    "committed": "✓",
    "prepared": "→",
    "not-authorized": "○",
    "self-attested": "◇",
    "separated": "↗",
    "independent": "✓",
    "external-journal": "✓",
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


def _label(value: Any, fallback: str = "Not recorded") -> str:
    """Humanize a contract token without making it more authoritative."""
    return _text(value, fallback).replace("_", " ").capitalize()


def _is_v2(state: Mapping[str, Any]) -> bool:
    return state.get("schema_version") == "exakt-report-v2"


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
    data_fields: tuple[tuple[str, str], ...] = (),
    show_status: bool = True,
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
            data_attributes = "".join(
                f' data-{attribute}="{html.escape(_text(record.get(field), ""), quote=True)}"'
                for field, attribute in data_fields
                if field in record
            )
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
            data_attributes = ""
            paragraphs = []
        else:
            continue

        status_key = _status_key(status)
        opened = " open" if index == 0 or (
            show_status and status_key in {"blocked", "contradicted", "stale", "failed"}
        ) else ""
        body = "".join(paragraphs) or "<p>No additional detail recorded.</p>"
        status_html = _status_badge(status) if show_status else ""
        cards.append(
            f'<details class="record"{data_attributes}{opened}>'
            f"<summary><span class=\"record-title\">{identifier}{title}</span>"
            f"{status_html}</summary>"
            f'<div class="record-body">{body}</div>'
            "</details>"
        )
    if not cards:
        return f'<p class="empty">{html.escape(empty)}</p>'
    return '<div class="record-stack">' + "".join(cards) + "</div>"


def _contract_notice(state: Mapping[str, Any]) -> str:
    if not _is_v2(state):
        return (
            '<aside class="contract-notice legacy" data-contract="legacy-v1">'
            '<p class="micro-label">Legacy v1 contract</p>'
            '<p>V2 traceability and milestone guarantees are unavailable.</p>'
            "</aside>"
        )
    authority = _text(state.get("authority_mode"), "local-self-attested")
    if authority == "external-journal":
        label = "External journal"
        explanation = (
            "Authority is delegated to an external journal; individual proof rows "
            "still declare their own provenance and freshness."
        )
    else:
        label = "Local self-attested"
        explanation = "This report does not claim independent verification."
    return (
        f'<aside class="contract-notice" data-authority-mode="{html.escape(authority, quote=True)}">'
        '<p class="micro-label">Proof authority</p>'
        f'<p class="authority-name">{html.escape(label)}</p>'
        f"<p>{html.escape(explanation)}</p>"
        "</aside>"
    )


def _clarity_panel(state: Mapping[str, Any]) -> str:
    clarity = _mapping(state.get("clarity"))
    intent = _mapping(clarity.get("intent"))
    ledger = _details_cards(
        clarity.get("ledger"),
        title_keys=("text", "id"),
        body_fields=(
            ("source", "Source"),
            ("affects", "Affects"),
            ("blocking", "Blocking"),
        ),
        empty="No material uncertainty recorded.",
    )
    open_item = intent.get("open_item_id")
    open_html = (
        f'<p><span class="micro-label">Open item</span><br>{_escape(open_item)}</p>'
        if open_item
        else ""
    )
    return (
        '<div class="intent-card">'
        '<p class="micro-label">Current intent hypothesis</p>'
        f'<p class="intent-text">{_escape(intent.get("text"), "No intent hypothesis recorded.")}</p>'
        f'<p class="confidence">Confidence · {_escape(_label(intent.get("confidence"), "Unknown"))}</p>'
        f'<p>{_escape(intent.get("reason"), "No confidence rationale recorded.")}</p>'
        f"{open_html}</div>"
        '<h3 class="subhead spaced">Clarity ledger</h3>'
        f"{ledger}"
    )


def _primitive_panel(state: Mapping[str, Any]) -> str:
    primitives = _mapping(state.get("primitives"))
    groups = (
        ("behaviors", "Behaviors", (("text", "Behavior"),)),
        ("invariants", "Invariants", (("text", "Invariant"),)),
        ("oracles", "Oracles", (("method", "Method"),)),
        ("counterexamples", "Counterexamples", (("targets", "Targets"),)),
    )
    rendered: list[str] = []
    for key, title, fields in groups:
        cards = _details_cards(
            primitives.get(key),
            title_keys=("text", "id"),
            body_fields=fields,
            empty=f"No {title.casefold()} recorded.",
        )
        rendered.append(
            f'<div class="primitive-lane"><h3 class="subhead">{html.escape(title)}</h3>{cards}</div>'
        )
    return '<div class="primitive-grid">' + "".join(rendered) + "</div>"


def _spec_metadata(state: Mapping[str, Any]) -> str:
    spec = _mapping(state.get("spec"))
    changes = _sequence(spec.get("changes"))
    change_items = []
    for item in changes:
        record = _mapping(item)
        if not record:
            continue
        change_items.append(
            '<li><strong>Revision '
            + _escape(record.get("revision"))
            + "</strong> · "
            + _escape(record.get("summary"))
            + '<span class="trace-meta">Changed: '
            + _display_value(record.get("changed_ids"))
            + " · "
            + _escape(record.get("reason"))
            + "</span></li>"
        )
    history = (
        '<ol class="change-list">' + "".join(change_items) + "</ol>"
        if change_items
        else '<p class="empty">No contract revisions recorded.</p>'
    )
    return (
        '<div class="panel spec-meta"><h3>Living specification</h3><dl>'
        f'<dt>Path</dt><dd>{_escape(spec.get("path"))}</dd>'
        f'<dt>Spec revision</dt><dd>{_escape(spec.get("revision"))}</dd>'
        f'<dt>Digest</dt><dd class="digest">{_escape(spec.get("digest"), "Not generated")}</dd>'
        f'<dt>Updated</dt><dd>{_escape(spec.get("updated_at"))}</dd>'
        f"</dl>{history}</div>"
    )


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
    base = (
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
    )
    if not _is_v2(state):
        return base + "</section>"
    return (
        base
        + '<div class="v2-contract-block"><div>'
        + _clarity_panel(state)
        + "</div>"
        + _spec_metadata(state)
        + "</div>"
        + '<h3 class="subhead spaced">Behavioral contract</h3>'
        + _primitive_panel(state)
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
        show_status=not _is_v2(state),
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


def _milestone_cards(value: Any, *, closeout_detail: bool) -> str:
    cards: list[str] = []
    for index, item in enumerate(_sequence(value)):
        milestone = _mapping(item)
        if not milestone:
            continue
        identifier = _text(milestone.get("id"), f"M{index + 1}")
        title = _escape(milestone.get("title"), "Untitled milestone")
        status = milestone.get("status", "unverified")
        opened = " open" if index == 0 or _status_key(status) in {
            "blocked",
            "failed",
            "stale",
            "contradicted",
        } else ""
        scope = (
            '<div class="milestone-scope">'
            f'<p><span class="micro-label">Tasks</span><br>{_display_value(milestone.get("task_ids"))}</p>'
            f'<p><span class="micro-label">Acceptance criteria</span><br>{_display_value(milestone.get("acceptance_criterion_ids"))}</p>'
            "</div>"
        )
        closeout = _mapping(milestone.get("closeout"))
        closeout_html = ""
        if closeout_detail:
            if not closeout:
                closeout_html = '<p class="empty">No milestone closeout recorded.</p>'
            else:
                commit = _mapping(closeout.get("commit"))
                gaps = _gap_values(closeout.get("gaps"))
                gap_html = (
                    '<ul class="risk-list compact">'
                    + "".join(f"<li>{html.escape(gap)}</li>" for gap in gaps)
                    + "</ul>"
                    if gaps
                    else '<p class="no-gap"><span aria-hidden="true">✓</span> No closeout gaps recorded.</p>'
                )
                commit_state = commit.get("state", "not-authorized")
                commit_hash = commit.get("hash")
                closeout_html = (
                    '<div class="closeout">'
                    f'<p><span class="micro-label">Completed</span><br>{_escape(closeout.get("completed"))}</p>'
                    f'<p><span class="micro-label">Covered</span><br>{_display_value(closeout.get("covered_ids"))}</p>'
                    f'<p><span class="micro-label">Changed</span><br>{_display_value(closeout.get("changed_paths"))}</p>'
                    f'<p><span class="micro-label">Proved</span><br>{_display_value(closeout.get("evidence_ids"))}</p>'
                    f"{gap_html}"
                    '<div class="commit-line"><span class="micro-label">Commit</span>'
                    f'{_status_badge(commit_state)}'
                    f'<code>{_escape(commit_hash, "No commit hash")}</code>'
                    f'<span>{_escape(commit.get("message"), "No commit message recorded")}</span>'
                    "</div></div>"
                )
        cards.append(
            f'<details class="record milestone" data-milestone-id="{html.escape(identifier, quote=True)}"{opened}>'
            '<summary><span class="record-title">'
            f'<span class="record-id">Milestone {html.escape(identifier)}</span>{title}</span>'
            f"{_status_badge(status)}</summary>"
            f'<div class="record-body">{scope}{closeout_html}</div></details>'
        )
    if not cards:
        return '<p class="empty">No milestones recorded.</p>'
    return '<div class="record-stack milestone-stack">' + "".join(cards) + "</div>"


def _plan_section(state: Mapping[str, Any]) -> str:
    criteria = _details_cards(
        state.get("acceptance_criteria"),
        title_keys=("text", "title", "name"),
        body_fields=(
            ("evidence", "Evidence"),
            ("requirement", "Requirement"),
            ("evidence_ids", "Evidence IDs"),
        ),
        empty="No acceptance criteria recorded.",
    )
    tasks = _details_cards(
        state.get("tasks"),
        title_keys=("title", "text", "name", "id"),
        body_fields=(
            ("owner", "Owner"),
            ("depends_on", "Depends on"),
            ("work_type", "Work type"),
            ("requirement_ids", "Requirements"),
            ("acceptance_criterion_ids", "Acceptance criteria"),
            ("verification", "Verification"),
            ("evidence_ids", "Evidence IDs"),
            ("milestone_id", "Milestone"),
            ("attempts", "Attempts"),
        ),
        empty="No implementation tasks recorded.",
    )
    milestone_scope = ""
    if _is_v2(state):
        milestone_scope = (
            '<h3 class="subhead">Milestones</h3>'
            + _milestone_cards(state.get("milestones"), closeout_detail=False)
            + '<h3 class="subhead spaced">Acceptance and implementation</h3>'
        )
    return (
        '<section id="plan" data-report-view="plan" class="report-view">'
        + _heading(3, "Acceptance & plan")
        + milestone_scope
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
    milestone_closeout = ""
    if _is_v2(state):
        milestone_closeout = (
            '<h3 class="subhead spaced">Milestone closeouts</h3>'
            + _milestone_cards(state.get("milestones"), closeout_detail=True)
        )
    return (
        '<section id="progress" data-report-view="progress" class="report-view">'
        + _heading(5, "Progress")
        + '<p class="lede">A compact operating view of what moved, what waits, and what blocks the next proof.</p>'
        + f'<div class="progress-grid">{metrics}</div>'
        + '<h3 class="subhead" style="margin-top:2rem">Delivery runway</h3>'
        + runway
        + milestone_closeout
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


def _proof_stage_label(record: Mapping[str, Any]) -> str:
    stage = _text(record.get("stage"), "proof")
    result = _text(record.get("result"), "unverified")
    if stage == "red" and result == "failed-as-expected":
        return "RED observed"
    if stage in {"red", "green"}:
        return f"{stage.upper()} · {_label(result)}"
    return f"{_label(stage)} · {_label(result)}"


def _proof_stages(state: Mapping[str, Any]) -> str:
    rows = []
    for item in _sequence(state.get("evidence")):
        record = _mapping(item)
        if not record or "stage" not in record:
            continue
        provenance = _text(record.get("provenance"), "unrecorded")
        rows.append(
            f'<li data-proof-provenance="{html.escape(provenance, quote=True)}">'
            f'<span class="proof-stage">{html.escape(_proof_stage_label(record))}</span>'
            f'<span>{html.escape(_label(provenance))}</span>'
            f'{_status_badge(record.get("status", "unverified"))}'
            f'<code>{_escape(record.get("id"))}</code>'
            "</li>"
        )
    if not rows:
        return '<p class="empty">No TDD or falsification stages recorded.</p>'
    return '<ol class="proof-lane">' + "".join(rows) + "</ol>"


def _trace_orphans(state: Mapping[str, Any]) -> list[str]:
    traceability = _mapping(state.get("traceability"))
    edges = [record for record in map(_mapping, _sequence(traceability.get("edges"))) if record]
    required_outgoing: list[tuple[str, str]] = []
    required_incoming: list[tuple[str, str]] = []

    def add(records: Any, direction: str, kinds: tuple[str, ...]) -> None:
        target = required_outgoing if direction == "out" else required_incoming
        for item in _sequence(records):
            identifier = _mapping(item).get("id")
            if isinstance(identifier, str):
                target.extend((identifier, kind) for kind in kinds)

    add(state.get("requirements"), "out", ("defines",))
    primitives = _mapping(state.get("primitives"))
    add(primitives.get("behaviors"), "in", ("defines",))
    add(primitives.get("behaviors"), "out", ("accepted_by", "protects"))
    add(primitives.get("invariants"), "in", ("protects",))
    add(primitives.get("invariants"), "out", ("observed_by",))
    add(primitives.get("oracles"), "in", ("observed_by",))
    add(primitives.get("oracles"), "out", ("challenged_by",))
    add(primitives.get("counterexamples"), "in", ("challenged_by",))
    add(state.get("acceptance_criteria"), "in", ("accepted_by",))
    add(state.get("acceptance_criteria"), "out", ("implemented_by",))
    add(state.get("tasks"), "in", ("implemented_by",))
    add(state.get("tasks"), "out", ("proved_by", "delivered_in"))
    add(state.get("evidence"), "in", ("proved_by",))
    add(state.get("milestones"), "in", ("delivered_in",))

    gaps = []
    for identifier, kind in required_outgoing:
        if not any(edge.get("from") == identifier and edge.get("kind") == kind for edge in edges):
            gaps.append(f"Orphan trace · {identifier} has no {kind} edge")
    for identifier, kind in required_incoming:
        if not any(edge.get("to") == identifier and edge.get("kind") == kind for edge in edges):
            gaps.append(f"Orphan trace · {identifier} has no incoming {kind} edge")
    return gaps


def _trace_panel(state: Mapping[str, Any]) -> str:
    traceability = _mapping(state.get("traceability"))
    edge_rows = []
    for item in _sequence(traceability.get("edges")):
        edge = _mapping(item)
        if not edge:
            continue
        source = _escape(edge.get("from"))
        target = _escape(edge.get("to"))
        kind = _text(edge.get("kind"), "unknown")
        edge_rows.append(
            f'<li data-trace-kind="{html.escape(kind, quote=True)}">{source} '
            f'<span aria-hidden="true">→</span> {html.escape(kind)} '
            f'<span aria-hidden="true">→</span> {target}</li>'
        )
    edges_html = (
        '<ol class="trace-lane">' + "".join(edge_rows) + "</ol>"
        if edge_rows
        else '<p class="empty">No trace edges recorded.</p>'
    )
    orphan_rows = _trace_orphans(state)
    orphan_html = (
        '<ul class="orphan-list">'
        + "".join(f"<li>{html.escape(item)}</li>" for item in orphan_rows)
        + "</ul>"
        if orphan_rows
        else '<p class="no-gap"><span aria-hidden="true">✓</span> No orphan trace detected.</p>'
    )
    invalidations = _details_cards(
        traceability.get("invalidations"),
        title_keys=("reason", "id"),
        body_fields=(
            ("changed_id", "Changed ID"),
            ("affected_ids", "Affected IDs"),
            ("recorded_at", "Recorded"),
            ("resolved_at", "Resolved"),
        ),
        empty="No contract invalidations recorded.",
    )
    return (
        '<div class="trace-grid"><div><h3 class="subhead">Contract trace</h3>'
        + edges_html
        + orphan_html
        + '</div><div><h3 class="subhead">Invalidations</h3>'
        + invalidations
        + "</div></div>"
    )


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
            ("evidence_ids", "Evidence IDs"),
            ("proof_type", "Proof type"),
            ("freshness", "Freshness"),
            ("counterexample", "Counterexample"),
        ),
        empty="No verification observations recorded.",
    )
    authority_truth = _contract_notice(state)
    v2_truth = ""
    if _is_v2(state):
        v2_truth = (
            '<h3 class="subhead spaced">Proof stages and provenance</h3>'
            + _proof_stages(state)
            + '<h3 class="subhead spaced">Traceability</h3>'
            + _trace_panel(state)
        )
    return (
        '<section id="truth" data-report-view="truth" class="report-view">'
        + _heading(6, "Verification & truth")
        + '<p class="lede">Contradictions and stale proof lead this ledger; passing evidence follows.</p>'
        + '<h3 class="subhead">Unresolved gaps</h3>'
        + gap_html
        + '<h3 class="subhead" style="margin-top:2rem">Truth ledger</h3>'
        + verification
        + authority_truth
        + v2_truth
        + "</section>"
    )


def _evidence_section(state: Mapping[str, Any]) -> str:
    files = _details_cards(
        state.get("files"),
        title_keys=("path", "name", "title"),
        id_keys=("id",),
        body_fields=(("change", "Change"), ("digest", "Digest"), ("note", "Note")),
        show_status=not _is_v2(state),
        empty="No changed files recorded.",
    )
    evidence = _details_cards(
        state.get("evidence"),
        title_keys=("name", "title", "command", "id"),
        body_fields=(
            ("type", "Evidence type"),
            ("stage", "Stage"),
            ("provenance", "Provenance"),
            ("detail", "Detail"),
            ("result", "Result"),
            ("command", "Command"),
            ("subject_digest", "Subject digest"),
            ("contract_digest", "Contract digest"),
            ("digest", "Digest"),
        ),
        data_fields=(("provenance", "proof-provenance"),),
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


def _write_atomic(output: Path, rendered: str) -> None:
    """Replace an HTML projection only after its complete bytes reach disk."""
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        mode = output.stat().st_mode & 0o777 if output.exists() else 0o644
        temporary.chmod(mode)
        os.replace(temporary, output)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        rendered = render_report(_load_state(args.input))
        _write_atomic(args.output, rendered)
    except (OSError, RenderError) as error:
        print(f"render_report.py: error: {error}", file=sys.stderr)
        return 2
    print(f"Rendered local Exakt report: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
