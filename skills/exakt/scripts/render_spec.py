#!/usr/bin/env python3
"""Render Exakt v2 state as a deterministic living Markdown specification."""

from __future__ import annotations

import html
import os
import tempfile
from pathlib import Path
from typing import Any

import report_state


class SpecRenderError(ValueError):
    """A living specification cannot be rendered or written safely."""


def contract_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    """Return only approved-contract data; exclude progress and evidence results."""
    report_state.validate_state(state)
    if report_state.legacy_state(state):
        raise SpecRenderError("living specifications require exakt-report-v2 state")
    return report_state.contract_snapshot(state)


def contract_digest(state: dict[str, Any]) -> str:
    report_state.validate_state(state)
    return report_state.contract_digest(state)


def _inline(value: Any) -> str:
    raw = value if isinstance(value, str) else str(value)
    collapsed = " ".join(raw.split())
    escaped = html.escape(collapsed, quote=True).replace("\\", "\\\\")
    for token in ("`", "*", "_", "[", "]", "#"):
        escaped = escaped.replace(token, f"\\{token}")
    return escaped


def _id_list(values: list[str]) -> str:
    return ", ".join(_inline(value) for value in values) or "none"


def _record_lines(records: list[dict[str, Any]], *, text_key: str = "text") -> list[str]:
    if not records:
        return ["_None recorded._"]
    return [
        f"- **{_inline(record['id'])}:** {_inline(record[text_key])}"
        for record in records
    ]


def render_spec(state: dict[str, Any]) -> str:
    report_state.validate_state(state)
    if report_state.legacy_state(state):
        raise SpecRenderError("living specifications require exakt-report-v2 state")
    digest = contract_digest(state)
    intent = state["clarity"]["intent"]
    lines = [
        f"# {_inline(state['title'])}",
        "",
        f"Mode: **{_inline(state['mode'])}** · Authority: **{_inline(state['authority_mode'])}**",
        f"Contract digest: `{digest}`",
        "",
        "## Intent",
        "",
        f"Source brief: {_inline(state['brief']['outcome'])}",
        "",
        f"Intent hypothesis: {_inline(intent['text'])}",
        "",
        f"Confidence: **{_inline(intent['confidence'])}** — {_inline(intent['reason']) or 'No reason recorded.'}",
        "",
        "## Boundaries",
        "",
        f"- **Users:** {_id_list(state['brief']['users'])}",
        f"- **Constraints:** {_id_list(state['brief']['constraints'])}",
    ]

    ledger = state["clarity"]["ledger"]
    if ledger:
        lines.extend(["", "## Clarity ledger", ""])
        for entry in ledger:
            blocking = " · blocking" if entry["blocking"] else ""
            lines.append(
                f"- **{_inline(entry['id'])} · {_inline(entry['status'])}{blocking}:** "
                f"{_inline(entry['text'])} _(source: {_inline(entry['source'])}; "
                f"affects: {_id_list(entry['affects'])})_"
            )

    if state["requirements"] or state["mode"] == "product":
        lines.extend(["", "## Requirements", ""])
        lines.extend(_record_lines(state["requirements"]))

    architecture = state["architecture"]
    if architecture["overview"] or architecture["components"] or architecture["decisions"]:
        lines.extend(["", "## Architecture", ""])
        if architecture["overview"]:
            lines.append(f"Overview: {_inline(architecture['overview'])}")
        for component in architecture["components"]:
            lines.append(
                f"- **{_inline(component['id'])} · {_inline(component['name'])}:** "
                f"{_inline(component['responsibility'])} · interfaces: "
                f"{_id_list(component['interfaces'])} · failure boundary: "
                f"{_inline(component['failure_boundary'])}"
            )
        for decision in architecture["decisions"]:
            lines.append(f"- **Decision:** {_inline(decision)}")

    if state["decisions"]:
        lines.extend(["", "## Decisions", ""])
        for decision in state["decisions"]:
            lines.append(
                f"- **{_inline(decision['id'])} · {_inline(decision['title'])}:** "
                f"{_inline(decision['rationale'])} · impact: {_inline(decision['impact'])} "
                f"· owner: {_inline(decision['owner']) or 'unassigned'}"
            )

    primitives = state["primitives"]
    if any(primitives.values()) or state["mode"] == "product":
        lines.extend(["", "## Behavior and proof primitives", ""])
        for label, name in (
            ("Behavior", "behaviors"),
            ("Invariant", "invariants"),
            ("Oracle", "oracles"),
            ("Counterexample", "counterexamples"),
        ):
            for record in primitives[name]:
                suffix = ""
                if name == "oracles":
                    suffix = f" _(method: {_inline(record['method'])})_"
                elif name == "counterexamples":
                    suffix = f" _(targets: {_id_list(record['targets'])})_"
                lines.append(
                    f"- **{label} {_inline(record['id'])}:** {_inline(record['text'])}{suffix}"
                )

    if state["acceptance_criteria"] or state["mode"] == "product":
        lines.extend(["", "## Acceptance criteria", ""])
        lines.extend(_record_lines(state["acceptance_criteria"]))

    lines.extend(["", "## Milestones and tasks", ""])
    if not state["milestones"]:
        lines.append("_No milestones approved yet._")
    for milestone in state["milestones"]:
        lines.append(
            f"- **{_inline(milestone['id'])}:** {_inline(milestone['title'])} "
            f"_(tasks: {_id_list(milestone['task_ids'])}; accepts: "
            f"{_id_list(milestone['acceptance_criterion_ids'])})_"
        )
        for task in state["tasks"]:
            if task["milestone_id"] == milestone["id"]:
                lines.append(
                    f"  - **{_inline(task['id'])}:** {_inline(task['title'])} "
                    f"_(type: {_inline(task['work_type'])}; depends on: "
                    f"{_id_list(task['depends_on'])}; requirements: "
                    f"{_id_list(task['requirement_ids'])}; accepts: "
                    f"{_id_list(task['acceptance_criterion_ids'])}; proof: "
                    f"{_inline(task['verification']) or 'not planned'})_"
                )

    if state["traceability"]["edges"]:
        lines.extend(["", "## Trace", ""])
        for edge in state["traceability"]["edges"]:
            lines.append(
                f"- {_inline(edge['from'])} —{_inline(edge['kind'])}→ {_inline(edge['to'])}"
            )

    lines.extend(
        [
            "",
            "## Current state",
            "",
            f"Phase: **{_inline(state['phase'])}** · Status: **{_inline(state['status'])}**",
        ]
    )
    if state["gaps"]:
        lines.append("")
        lines.append("Gaps:")
        lines.extend(f"- {_inline(gap)}" for gap in state["gaps"])
    if state["spec"]["changes"]:
        lines.extend(["", "Changes:"])
        for change in state["spec"]["changes"]:
            lines.append(
                f"- **r{change['revision']}:** {_inline(change['summary'])} — "
                f"{_inline(change['reason'])}"
            )
    return "\n".join(lines).rstrip() + "\n"


def write_spec(path: str | Path, state: dict[str, Any], *, force: bool = False) -> str:
    output = Path(path)
    if output.exists() and not force:
        raise SpecRenderError(f"refusing to overwrite existing specification: {output}")
    report_state.validate_state(state)
    digest = report_state.synchronize_contract_digest(state)
    report_state.validate_state(state)
    payload = render_spec(state).encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.tmp-", dir=output.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            temporary.unlink()
    return digest
