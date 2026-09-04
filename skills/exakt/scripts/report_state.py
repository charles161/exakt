"""Construction and semantic validation for Exakt report state."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterable

from contracts import ContractError, validate_document


REPORT_V1 = "exakt-report-v1"
REPORT_V2 = "exakt-report-v2"
MODES = ("task", "product")
PHASES = (
    "intake",
    "recon",
    "requirements",
    "design",
    "plan",
    "execute",
    "verify",
    "handoff",
)
STATUSES = (
    "draft",
    "active",
    "blocked",
    "failed",
    "contradicted",
    "unverified",
    "verified",
)
_V1_STATUSES = ("draft", "active", "blocked", "failed", "unverified", "verified")

_V1_REQUIRED = {
    "schema_version",
    "title",
    "mode",
    "summary",
    "status",
    "phase",
    "updated_at",
    "brief",
    "requirements",
    "architecture",
    "acceptance_criteria",
    "tasks",
    "critiques",
    "decisions",
    "verification",
    "files",
    "evidence",
    "gaps",
}
_COLLECTIONS_WITH_IDS = (
    "requirements",
    "acceptance_criteria",
    "tasks",
    "critiques",
    "decisions",
    "verification",
    "files",
    "evidence",
    "milestones",
)
_PRIMITIVE_COLLECTIONS = ("behaviors", "invariants", "oracles", "counterexamples")
_EXECUTABLE_STAGES = {"red", "green", "regression", "legitimacy", "falsification"}
_ARTIFACT_STAGES = {"before", "proof", "falsification"}
_COMPLETED_TASK_STATUSES = {"done", "verified"}
_EDGE_ENDPOINTS = {
    "defines": ("requirements", "primitives.behaviors"),
    "accepted_by": ("primitives.behaviors", "acceptance_criteria"),
    "protects": ("primitives.behaviors", "primitives.invariants"),
    "observed_by": ("primitives.invariants", "primitives.oracles"),
    "challenged_by": ("primitives.oracles", "primitives.counterexamples"),
    "implemented_by": ("acceptance_criteria", "tasks"),
    "proved_by": ("tasks", "evidence"),
    "delivered_in": ("tasks", "milestones"),
}


class ReportStateError(ValueError):
    """Report state is malformed or makes an internally false claim."""


def _fields(record: dict[str, Any], names: Iterable[str]) -> dict[str, Any]:
    return {name: record[name] for name in names}


def _contract_items(
    records: list[dict[str, Any]], names: Iterable[str]
) -> list[dict[str, Any]]:
    return [_fields(record, names) for record in records]


def contract_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    """Return contract-only v2 data without progress, timestamps, or proof results."""
    if not isinstance(state, dict) or state.get("schema_version") != REPORT_V2:
        raise ReportStateError("contract snapshots require exakt-report-v2 state")
    primitives = state["primitives"]
    return {
        "schema_version": state["schema_version"],
        "authority_mode": state["authority_mode"],
        "title": state["title"],
        "mode": state["mode"],
        "brief": state["brief"],
        "clarity": {
            "intent": state["clarity"]["intent"],
            "ledger": _contract_items(
                state["clarity"]["ledger"],
                ("id", "text", "status", "source", "affects", "blocking"),
            ),
        },
        "requirements": _contract_items(state["requirements"], ("id", "text")),
        "architecture": state["architecture"],
        "primitives": {
            "behaviors": _contract_items(primitives["behaviors"], ("id", "text")),
            "invariants": _contract_items(primitives["invariants"], ("id", "text")),
            "oracles": _contract_items(
                primitives["oracles"], ("id", "text", "method")
            ),
            "counterexamples": _contract_items(
                primitives["counterexamples"], ("id", "text", "targets")
            ),
        },
        "acceptance_criteria": _contract_items(
            state["acceptance_criteria"], ("id", "text")
        ),
        "tasks": _contract_items(
            state["tasks"],
            (
                "id",
                "title",
                "depends_on",
                "work_type",
                "requirement_ids",
                "acceptance_criterion_ids",
                "verification",
                "milestone_id",
            ),
        ),
        "milestones": _contract_items(
            state["milestones"],
            ("id", "title", "task_ids", "acceptance_criterion_ids"),
        ),
        "traceability": {"edges": state["traceability"]["edges"]},
        "decisions": _contract_items(
            state["decisions"], ("id", "title", "rationale", "impact", "owner")
        ),
    }


def contract_digest(state: dict[str, Any]) -> str:
    payload = json.dumps(
        contract_snapshot(state),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def synchronize_contract_digest(
    state: dict[str, Any], *, now: str | None = None
) -> str:
    """Refresh the contract digest and invalidate proof bound to an older contract."""
    digest = contract_digest(state)
    previous = state["spec"]["digest"]
    if previous and previous != digest:
        for item in state["evidence"]:
            if item["contract_digest"] != digest and item["status"] == "verified":
                item["status"] = "stale"
        for item in state["tasks"]:
            if item["status"] in _COMPLETED_TASK_STATUSES:
                item["status"] = "unverified"
        for item in state["acceptance_criteria"]:
            if item["status"] == "verified":
                item["status"] = "unverified"
        for item in state["verification"]:
            if item["status"] in {"verified", "partially_verified"}:
                item["status"] = "stale"
            item["freshness"] = "stale"
        for item in state["milestones"]:
            if item["status"] in _COMPLETED_TASK_STATUSES:
                item["status"] = "unverified"
            closeout = item["closeout"]
            if closeout is not None:
                if closeout["status"] in {"verified", "partially_verified"}:
                    closeout["status"] = "stale"
                message = "Contract changed after recorded proof; re-verification required."
                if message not in closeout["gaps"]:
                    closeout["gaps"].append(message)
        if state["status"] in {"verified", "active"}:
            state["status"] = "unverified"
        timestamp = now or utc_now()
        state["spec"]["revision"] += 1
        state["spec"]["updated_at"] = timestamp
        changed_ids = [
            identifier
            for identifier, (collection, _record) in _id_index(state).items()
            if collection
            in {
                "requirements",
                "architecture.components",
                "primitives.behaviors",
                "primitives.invariants",
                "primitives.oracles",
                "primitives.counterexamples",
                "acceptance_criteria",
                "tasks",
                "milestones",
                "decisions",
            }
        ]
        state["spec"]["changes"].append(
            {
                "revision": state["spec"]["revision"],
                "summary": "Contract projection changed",
                "changed_ids": changed_ids,
                "reason": "Existing proof was bound to a different contract digest.",
                "updated_at": timestamp,
            }
        )
    state["spec"]["digest"] = digest
    return digest


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def title_from_request(request: str) -> str:
    first = " ".join(request.strip().split())
    if not first:
        raise ReportStateError("request must not be empty")
    return first if len(first) <= 72 else first[:69].rstrip() + "…"


def initial_state(
    request: str,
    mode: str,
    title: str | None = None,
    *,
    now: str | None = None,
) -> dict[str, Any]:
    if mode not in MODES:
        raise ReportStateError(f"mode must be one of: {', '.join(MODES)}")
    request = request.strip()
    if not request:
        raise ReportStateError("request must not be empty")
    timestamp = now or utc_now()
    return {
        "schema_version": REPORT_V2,
        "authority_mode": "local-self-attested",
        "title": title or title_from_request(request),
        "mode": mode,
        "summary": "Exakt captured the request. Reconnaissance and clarity are next.",
        "status": "draft",
        "phase": "intake",
        "updated_at": timestamp,
        "brief": {"outcome": request, "users": [], "constraints": []},
        "clarity": {
            "intent": {
                "text": request,
                "confidence": "low",
                "reason": "Repository reconnaissance is pending.",
                "open_item_id": None,
            },
            "ledger": [],
        },
        "requirements": [],
        "architecture": {"overview": "", "components": [], "decisions": []},
        "primitives": {
            "behaviors": [],
            "invariants": [],
            "oracles": [],
            "counterexamples": [],
        },
        "acceptance_criteria": [],
        "tasks": [],
        "milestones": [],
        "traceability": {"edges": [], "invalidations": []},
        "critiques": [],
        "decisions": [],
        "verification": [],
        "files": [],
        "evidence": [],
        "gaps": [],
        "spec": {
            "path": ".exakt/spec.md",
            "revision": 1,
            "digest": "",
            "updated_at": timestamp,
            "changes": [],
        },
    }


def legacy_state(state: Any) -> bool:
    return isinstance(state, dict) and state.get("schema_version") == REPORT_V1


def _validate_v1(state: Any) -> dict[str, Any]:
    if not isinstance(state, dict):
        raise ReportStateError("report state must be a JSON object")
    missing = sorted(_V1_REQUIRED - set(state))
    if missing:
        raise ReportStateError("report state is missing: " + ", ".join(missing))
    if state.get("schema_version") != REPORT_V1:
        raise ReportStateError("unsupported report schema version")
    if state.get("mode") not in MODES or state.get("phase") not in PHASES:
        raise ReportStateError("report mode or phase is invalid")
    if state.get("status") not in _V1_STATUSES:
        raise ReportStateError("report status is invalid")
    for name in ("title", "summary", "updated_at"):
        if not isinstance(state.get(name), str):
            raise ReportStateError(f"report {name} must be text")
    if not isinstance(state.get("brief"), dict) or not isinstance(
        state.get("architecture"), dict
    ):
        raise ReportStateError("brief and architecture must be objects")
    for name in _V1_REQUIRED - {
        "schema_version",
        "title",
        "mode",
        "summary",
        "status",
        "phase",
        "updated_at",
        "brief",
        "architecture",
    }:
        if not isinstance(state.get(name), list):
            raise ReportStateError(f"report {name} must be a list")
    return state


def _record_collections(state: dict[str, Any]) -> Iterable[tuple[str, dict[str, Any]]]:
    for name in _COLLECTIONS_WITH_IDS:
        for record in state[name]:
            yield name, record
    for name in _PRIMITIVE_COLLECTIONS:
        for record in state["primitives"][name]:
            yield f"primitives.{name}", record
    for record in state["clarity"]["ledger"]:
        yield "clarity.ledger", record
    for record in state["architecture"]["components"]:
        yield "architecture.components", record
    for record in state["traceability"]["invalidations"]:
        yield "traceability.invalidations", record


def _id_index(state: dict[str, Any]) -> dict[str, tuple[str, dict[str, Any]]]:
    index: dict[str, tuple[str, dict[str, Any]]] = {}
    for collection, record in _record_collections(state):
        identifier = record["id"]
        if identifier in index:
            raise ReportStateError(f"duplicate id {identifier!r} in report state")
        index[identifier] = (collection, record)
    return index


def _trace_set(state: dict[str, Any]) -> set[tuple[str, str, str]]:
    return {
        (edge["from"], edge["to"], edge["kind"])
        for edge in state["traceability"]["edges"]
    }


def _proof_records_for_task(
    task: dict[str, Any], evidence: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    return [evidence[item_id] for item_id in task["evidence_ids"] if item_id in evidence]


def _validate_v2_semantics(state: dict[str, Any]) -> None:
    index = _id_index(state)
    all_ids = set(index)
    ledger_ids = {entry["id"] for entry in state["clarity"]["ledger"]}
    open_item_id = state["clarity"]["intent"]["open_item_id"]
    if open_item_id is not None and open_item_id not in ledger_ids:
        raise ReportStateError(f"intent open_item_id {open_item_id!r} is dangling")

    for edge in state["traceability"]["edges"]:
        if edge["from"] not in index or edge["to"] not in index:
            raise ReportStateError(
                "dangling trace edge "
                f"{edge['from']!r} -> {edge['to']!r} ({edge['kind']})"
            )
        expected_source, expected_target = _EDGE_ENDPOINTS[edge["kind"]]
        actual_source = index[edge["from"]][0]
        actual_target = index[edge["to"]][0]
        if (actual_source, actual_target) != (expected_source, expected_target):
            raise ReportStateError(
                f"invalid {edge['kind']} edge {edge['from']!r} -> {edge['to']!r}: "
                f"expected {expected_source} -> {expected_target}"
            )
    for entry in state["clarity"]["ledger"]:
        dangling = sorted(set(entry["affects"]) - all_ids)
        if dangling:
            raise ReportStateError(
                f"clarity entry {entry['id']} references dangling id {dangling[0]!r}"
            )
    for counterexample in state["primitives"]["counterexamples"]:
        dangling = sorted(set(counterexample["targets"]) - all_ids)
        if dangling:
            raise ReportStateError(
                f"counterexample {counterexample['id']} references dangling id {dangling[0]!r}"
            )
    for invalidation in state["traceability"]["invalidations"]:
        if invalidation["changed_id"] not in index:
            raise ReportStateError(
                f"invalidation {invalidation['id']!r} has dangling changed_id "
                f"{invalidation['changed_id']!r}"
            )
        for identifier in invalidation["affected_ids"]:
            if identifier not in index:
                raise ReportStateError(
                    f"invalidation {invalidation['id']!r} references dangling id {identifier!r}"
                )
        if invalidation["status"] == "resolved" and not invalidation["resolved_at"]:
            raise ReportStateError(
                f"resolved invalidation {invalidation['id']} requires resolved_at"
            )
        if invalidation["status"] == "open":
            for identifier in invalidation["affected_ids"]:
                affected = index[identifier][1]
                if affected.get("status") in _COMPLETED_TASK_STATUSES:
                    raise ReportStateError(
                        f"open invalidation {invalidation['id']} leaves affected "
                        f"record {identifier} verified"
                    )

    for change in state["spec"]["changes"]:
        dangling = sorted(set(change["changed_ids"]) - all_ids)
        if dangling:
            raise ReportStateError(
                f"spec change r{change['revision']} references dangling id {dangling[0]!r}"
            )

    evidence = {item["id"]: item for item in state["evidence"]}
    milestones = {item["id"]: item for item in state["milestones"]}
    criteria = {item["id"] for item in state["acceptance_criteria"]}
    requirements = {item["id"] for item in state["requirements"]}
    trace = _trace_set(state)
    expected_results = {
        "red": "failed-as-expected",
        "before": "observed",
        "green": "passed",
        "regression": "passed",
        "legitimacy": "passed",
        "falsification": "passed",
        "proof": "passed",
    }
    for item in state["evidence"]:
        if item["status"] != "verified":
            continue
        expected = expected_results[item["stage"]]
        if item["result"] != expected:
            label = "RED" if item["stage"] == "red" else item["stage"]
            raise ReportStateError(
                f"{label} evidence {item['id']} requires result {expected}"
            )

    for criterion in state["acceptance_criteria"]:
        dangling = sorted(set(criterion["evidence_ids"]) - set(evidence))
        if dangling:
            raise ReportStateError(
                f"acceptance criterion {criterion['id']} references dangling evidence {dangling[0]!r}"
            )
    for check in state["verification"]:
        dangling = sorted(set(check["evidence_ids"]) - set(evidence))
        if dangling:
            raise ReportStateError(
                f"verification {check['id']} references dangling evidence {dangling[0]!r}"
            )
        if check["counterexample"]:
            if (
                check["counterexample"] not in index
                or index[check["counterexample"]][0] != "primitives.counterexamples"
            ):
                raise ReportStateError(
                    f"verification {check['id']} references dangling counterexample "
                    f"{check['counterexample']!r}"
                )
        if check["status"] == "verified":
            # Freshness/evidence sufficiency are completion gates, not parse errors.
            # This keeps stale work inspectable while preventing a verified handoff.
            pass

    for task in state["tasks"]:
        for dependency in task["depends_on"]:
            if dependency not in {item["id"] for item in state["tasks"]}:
                raise ReportStateError(
                    f"task {task['id']} has dangling dependency {dependency!r}"
                )
        for requirement_id in task["requirement_ids"]:
            if requirement_id not in requirements:
                raise ReportStateError(
                    f"task {task['id']} references dangling requirement {requirement_id!r}"
                )
        for criterion_id in task["acceptance_criterion_ids"]:
            if criterion_id not in criteria:
                raise ReportStateError(
                    f"task {task['id']} references dangling acceptance criterion {criterion_id!r}"
                )
        if task["milestone_id"] not in milestones:
            raise ReportStateError(
                f"task {task['id']} references dangling milestone {task['milestone_id']!r}"
            )
        unknown_evidence = sorted(set(task["evidence_ids"]) - set(evidence))
        if unknown_evidence:
            raise ReportStateError(
                f"task {task['id']} references dangling evidence {unknown_evidence[0]!r}"
            )
        if task["status"] not in _COMPLETED_TASK_STATUSES:
            continue

        linked = _proof_records_for_task(task, evidence)
        required_stages = (
            _EXECUTABLE_STAGES if task["work_type"] == "executable" else _ARTIFACT_STAGES
        )
        observed_stages = {
            item["stage"] for item in linked if item["status"] == "verified"
        }
        missing_stages = sorted(required_stages - observed_stages)
        if missing_stages:
            raise ReportStateError(
                f"task {task['id']} is missing proof stage {missing_stages[0]}"
            )
        subject_digests = {item["subject_digest"] for item in linked}
        if len(subject_digests) != 1:
            raise ReportStateError(
                f"task {task['id']} proof stages must bind one subject digest"
            )
        for item in linked:
            if item["status"] != "verified":
                raise ReportStateError(
                    f"task {task['id']} links non-verified evidence {item['id']}"
                )
            if (task["id"], item["id"], "proved_by") not in trace:
                raise ReportStateError(
                    f"task {task['id']} evidence {item['id']} lacks proved_by trace"
                )
            if state["authority_mode"] == "external-journal" and item["provenance"] not in {
                "independent", "external-journal"
            }:
                raise ReportStateError(
                    f"task {task['id']} evidence {item['id']} cannot satisfy external-journal authority"
                )

    for milestone in state["milestones"]:
        task_ids = {item["id"] for item in state["tasks"]}
        for task_id in milestone["task_ids"]:
            if task_id not in task_ids:
                raise ReportStateError(
                    f"milestone {milestone['id']} references dangling task {task_id!r}"
                )
        for criterion_id in milestone["acceptance_criterion_ids"]:
            if criterion_id not in criteria:
                raise ReportStateError(
                    f"milestone {milestone['id']} references dangling acceptance criterion {criterion_id!r}"
                )
        closeout = milestone["closeout"]
        if closeout is not None:
            dangling_evidence = sorted(set(closeout["evidence_ids"]) - set(evidence))
            if dangling_evidence:
                raise ReportStateError(
                    f"milestone {milestone['id']} closeout references dangling evidence "
                    f"{dangling_evidence[0]!r}"
                )
            dangling_covered = sorted(set(closeout["covered_ids"]) - all_ids)
            if dangling_covered:
                raise ReportStateError(
                    f"milestone {milestone['id']} closeout references dangling coverage "
                    f"{dangling_covered[0]!r}"
                )
        if milestone["status"] == "verified":
            if closeout is None or closeout["status"] != "verified":
                raise ReportStateError(
                    f"verified milestone {milestone['id']} requires a verified closeout"
                )
            if closeout["gaps"]:
                raise ReportStateError(
                    f"verified milestone {milestone['id']} cannot retain closeout gaps"
                )
            tasks_by_id = {item["id"]: item for item in state["tasks"]}
            criteria_by_id = {
                item["id"]: item for item in state["acceptance_criteria"]
            }
            for task_id in milestone["task_ids"]:
                if tasks_by_id[task_id]["status"] != "verified":
                    raise ReportStateError(
                        f"verified milestone {milestone['id']} contains non-verified task {task_id}"
                    )
            for criterion_id in milestone["acceptance_criterion_ids"]:
                if criteria_by_id[criterion_id]["status"] != "verified":
                    raise ReportStateError(
                        f"verified milestone {milestone['id']} contains non-verified criterion {criterion_id}"
                    )
                if criterion_id not in closeout["covered_ids"]:
                    raise ReportStateError(
                        f"verified milestone {milestone['id']} closeout omits criterion {criterion_id}"
                    )
            if not closeout["evidence_ids"] or any(
                evidence[item_id]["status"] != "verified"
                for item_id in closeout["evidence_ids"]
            ):
                raise ReportStateError(
                    f"verified milestone {milestone['id']} requires verified closeout evidence"
                )


def validate_state(state: Any) -> dict[str, Any]:
    if legacy_state(state):
        return _validate_v1(state)
    if not isinstance(state, dict):
        raise ReportStateError("report state must be a JSON object")
    if state.get("schema_version") != REPORT_V2:
        raise ReportStateError("unsupported report schema version")
    try:
        validate_document(state, REPORT_V2)
    except ContractError as error:
        raise ReportStateError(str(error)) from error
    _validate_v2_semantics(state)
    return state


def traceability_gaps(state: dict[str, Any]) -> list[str]:
    if legacy_state(state):
        return ["legacy v1 state has no v2 traceability guarantees"]
    trace = _trace_set(state)
    gaps: list[str] = []
    for requirement in state["requirements"]:
        if not any(
            source == requirement["id"] and kind == "defines"
            for source, _target, kind in trace
        ):
            gaps.append(f"requirement {requirement['id']} has no defines edge")
    for behavior in state["primitives"]["behaviors"]:
        if not any(
            target == behavior["id"] and kind == "defines"
            for _source, target, kind in trace
        ):
            gaps.append(f"behavior {behavior['id']} has no incoming defines edge")
        if not any(
            source == behavior["id"] and kind == "accepted_by"
            for source, _target, kind in trace
        ):
            gaps.append(f"behavior {behavior['id']} has no accepted_by edge")
        if not any(
            source == behavior["id"] and kind == "protects"
            for source, _target, kind in trace
        ):
            gaps.append(f"behavior {behavior['id']} has no protects edge")
    for invariant in state["primitives"]["invariants"]:
        if not any(
            target == invariant["id"] and kind == "protects"
            for _source, target, kind in trace
        ):
            gaps.append(f"invariant {invariant['id']} has no protects edge")
        if not any(
            source == invariant["id"] and kind == "observed_by"
            for source, _target, kind in trace
        ):
            gaps.append(f"invariant {invariant['id']} has no observed_by edge")
    for oracle in state["primitives"]["oracles"]:
        if not any(
            target == oracle["id"] and kind == "observed_by"
            for _source, target, kind in trace
        ):
            gaps.append(f"oracle {oracle['id']} has no incoming observed_by edge")
        if not any(
            source == oracle["id"] and kind == "challenged_by"
            for source, _target, kind in trace
        ):
            gaps.append(f"oracle {oracle['id']} has no challenged_by edge")
    for counterexample in state["primitives"]["counterexamples"]:
        if not any(
            target == counterexample["id"] and kind == "challenged_by"
            for _source, target, kind in trace
        ):
            gaps.append(f"counterexample {counterexample['id']} is not linked to an oracle")
    for criterion in state["acceptance_criteria"]:
        if not any(
            target == criterion["id"] and kind == "accepted_by"
            for _source, target, kind in trace
        ):
            gaps.append(
                f"acceptance criterion {criterion['id']} has no incoming accepted_by edge"
            )
        if not any(
            source == criterion["id"] and kind == "implemented_by"
            for source, _target, kind in trace
        ):
            gaps.append(
                f"acceptance criterion {criterion['id']} has no implemented_by edge"
            )
    for item in state["evidence"]:
        if not any(
            target == item["id"] and kind == "proved_by"
            for _source, target, kind in trace
        ):
            gaps.append(f"evidence {item['id']} has no incoming proved_by edge")
    for task in state["tasks"]:
        traced_criteria = {
            source
            for source, target, kind in trace
            if target == task["id"] and kind == "implemented_by"
        }
        if not traced_criteria:
            gaps.append(f"task {task['id']} is outside approved acceptance scope")
        elif traced_criteria != set(task["acceptance_criterion_ids"]):
            gaps.append(f"task {task['id']} acceptance IDs disagree with trace")
        if not task["verification"].strip():
            gaps.append(f"task {task['id']} has no planned verification")
        traced_milestones = {
            target
            for source, target, kind in trace
            if source == task["id"] and kind == "delivered_in"
        }
        if not traced_milestones:
            gaps.append(f"task {task['id']} has no delivered_in edge")
        elif traced_milestones != {task["milestone_id"]}:
            gaps.append(f"task {task['id']} milestone disagrees with trace")
        traced_evidence = {
            target
            for source, target, kind in trace
            if source == task["id"] and kind == "proved_by"
        }
        if traced_evidence != set(task["evidence_ids"]):
            gaps.append(f"task {task['id']} evidence IDs disagree with trace")
        for requirement_id in task["requirement_ids"]:
            connected = any(
                source == requirement_id
                and kind == "defines"
                and any(
                    b_source == target
                    and b_kind == "accepted_by"
                    and b_target in traced_criteria
                    for b_source, b_target, b_kind in trace
                )
                for source, target, kind in trace
            )
            if not connected:
                gaps.append(
                    f"task {task['id']} requirement {requirement_id} has no connected behavior/criterion path"
                )
    for milestone in state["milestones"]:
        traced_tasks = {
            source
            for source, target, kind in trace
            if target == milestone["id"] and kind == "delivered_in"
        }
        if traced_tasks != set(milestone["task_ids"]):
            gaps.append(f"milestone {milestone['id']} task IDs disagree with trace")
        tasks = {item["id"]: item for item in state["tasks"]}
        expected_criteria = {
            criterion_id
            for task_id in milestone["task_ids"]
            if task_id in tasks
            for criterion_id in tasks[task_id]["acceptance_criterion_ids"]
        }
        if expected_criteria != set(milestone["acceptance_criterion_ids"]):
            gaps.append(f"milestone {milestone['id']} acceptance IDs disagree with tasks")
    return gaps


def verification_gaps(state: dict[str, Any]) -> list[str]:
    if legacy_state(state):
        gaps: list[str] = []
        if state["status"] != "verified":
            gaps.append(f"status is {state['status']!r}, not 'verified'")
        if state["phase"] != "handoff":
            gaps.append(f"phase is {state['phase']!r}, not 'handoff'")
        criteria = state["acceptance_criteria"]
        if not criteria:
            gaps.append("no acceptance criteria were recorded")
        elif any(
            not isinstance(item, dict) or item.get("status") != "verified"
            for item in criteria
        ):
            gaps.append("pending acceptance criteria remain")
        checks = state["verification"]
        if not checks:
            gaps.append("no verification evidence was recorded")
        elif any(
            not isinstance(item, dict) or item.get("status") != "verified"
            for item in checks
        ):
            gaps.append("verification contains non-verified results")
        if state["gaps"]:
            gaps.append("declared gaps remain")
        return gaps

    gaps: list[str] = []
    recomputed_digest = contract_digest(state)
    evidence_by_id = {item["id"]: item for item in state["evidence"]}
    trace = _trace_set(state)
    proof_sources: dict[str, set[str]] = {}
    for source, target, kind in trace:
        if kind == "proved_by":
            proof_sources.setdefault(target, set()).add(source)

    def evidence_gaps(
        label: str,
        evidence_ids: list[str],
        *,
        allowed_tasks: set[str] | None = None,
        require_one_subject: bool = False,
    ) -> list[str]:
        problems: list[str] = []
        if not evidence_ids:
            return [f"{label} has no evidence"]
        subjects: set[str] = set()
        for evidence_id in evidence_ids:
            item = evidence_by_id[evidence_id]
            subjects.add(item["subject_digest"])
            if item["status"] != "verified":
                problems.append(f"{label} uses {item['status']} evidence {evidence_id}")
            if item["contract_digest"] != recomputed_digest:
                problems.append(f"{label} uses stale-contract evidence {evidence_id}")
            if (
                state["authority_mode"] == "external-journal"
                and item["provenance"] not in {"independent", "external-journal"}
            ):
                problems.append(
                    f"{label} evidence {evidence_id} does not meet external-journal authority"
                )
            owners = proof_sources.get(evidence_id, set())
            if not owners:
                problems.append(f"{label} evidence {evidence_id} is not bound to a task")
            elif allowed_tasks is not None and not owners.intersection(allowed_tasks):
                problems.append(
                    f"{label} evidence {evidence_id} is bound outside its approved task scope"
                )
        if require_one_subject and len(subjects) > 1:
            problems.append(f"{label} evidence does not bind one subject digest")
        return problems
    if not state["spec"]["digest"] or state["spec"]["digest"] != recomputed_digest:
        gaps.append("contract digest is stale; refresh the living specification and proof")
    if state["status"] != "verified":
        gaps.append(f"status is {state['status']!r}, not 'verified'")
    if state["phase"] != "handoff":
        gaps.append(f"phase is {state['phase']!r}, not 'handoff'")
    if not state["acceptance_criteria"]:
        gaps.append("no acceptance criteria were recorded")
    elif any(item["status"] != "verified" for item in state["acceptance_criteria"]):
        gaps.append("pending acceptance criteria remain")
    for criterion in state["acceptance_criteria"]:
        if criterion["status"] == "verified":
            implementing_tasks = {
                target
                for source, target, kind in trace
                if source == criterion["id"] and kind == "implemented_by"
            }
            gaps.extend(
                evidence_gaps(
                    f"acceptance criterion {criterion['id']}",
                    criterion["evidence_ids"],
                    allowed_tasks=implementing_tasks,
                    require_one_subject=True,
                )
            )
    if not state["verification"]:
        gaps.append("no verification evidence was recorded")
    elif any(item["status"] != "verified" for item in state["verification"]):
        gaps.append("verification contains non-verified results")
    for check in state["verification"]:
        if check["status"] == "verified":
            gaps.extend(
                evidence_gaps(
                    f"verification {check['id']}",
                    check["evidence_ids"],
                    require_one_subject=True,
                )
            )
    if any(item["freshness"] != "fresh" for item in state["verification"]):
        gaps.append("stale verification remains")
    if not state["tasks"]:
        gaps.append("no implementation tasks were recorded")
    elif any(item["status"] != "verified" for item in state["tasks"]):
        gaps.append("non-verified tasks remain")
    if not state["milestones"]:
        gaps.append("no milestones were recorded")
    elif any(item["status"] != "verified" for item in state["milestones"]):
        gaps.append("non-verified milestones remain")
    for milestone in state["milestones"]:
        closeout = milestone["closeout"]
        if milestone["status"] == "verified" and closeout is not None:
            gaps.extend(
                evidence_gaps(
                    f"milestone {milestone['id']} closeout",
                    closeout["evidence_ids"],
                    allowed_tasks=set(milestone["task_ids"]),
                )
            )
    if state["gaps"]:
        gaps.append("declared gaps remain")
    blocking_clarity = [
        item["id"]
        for item in state["clarity"]["ledger"]
        if item["blocking"] and item["status"] in {"unknown", "conflicted"}
    ]
    if blocking_clarity:
        gaps.append("blocking clarity remains: " + ", ".join(blocking_clarity))
    for invalidation in state["traceability"]["invalidations"]:
        if invalidation["status"] == "open":
            gaps.append(f"open invalidation {invalidation['id']} remains")
    linked_evidence = {
        evidence_id
        for task in state["tasks"]
        for evidence_id in task["evidence_ids"]
    }
    for evidence_id in sorted(linked_evidence):
        item = evidence_by_id.get(evidence_id)
        if item is None:
            continue
        if item["status"] != "verified":
            gaps.append(f"evidence {evidence_id} is {item['status']}")
        elif item["contract_digest"] != recomputed_digest:
            gaps.append(f"evidence {evidence_id} is stale for the current contract")
        elif state["authority_mode"] == "external-journal" and item["provenance"] not in {
            "independent", "external-journal"
        }:
            gaps.append(f"evidence {evidence_id} does not meet external-journal authority")
    gaps.extend(traceability_gaps(state))
    return list(dict.fromkeys(gaps))


def _text(record: Any, *keys: str, fallback: str) -> str:
    if isinstance(record, dict):
        for key in keys:
            value = record.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    if isinstance(record, str) and record.strip():
        return record.strip()
    return fallback


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _digest_or_empty(value: Any) -> str:
    if (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    ):
        return value
    return ""


def migrate_v1_state(state: dict[str, Any], *, now: str | None = None) -> dict[str, Any]:
    _validate_v1(state)
    migrated = initial_state(
        state["brief"].get("outcome", state["title"]),
        state["mode"],
        state["title"],
        now=now or utc_now(),
    )
    migrated["status"] = "unverified"
    migrated["summary"] = state["summary"]
    migrated["brief"] = {
        "outcome": _text(state["brief"].get("outcome"), fallback=state["title"]),
        "users": _strings(state["brief"].get("users")),
        "constraints": _strings(state["brief"].get("constraints")),
    }
    migrated["clarity"]["intent"]["text"] = migrated["brief"]["outcome"]
    migrated["requirements"] = [
        {
            "id": f"R{index + 1}",
            "text": _text(item, "text", "title", "name", fallback=f"Legacy requirement {index + 1}"),
            "status": "unverified",
        }
        for index, item in enumerate(state["requirements"])
    ]
    migrated["acceptance_criteria"] = [
        {
            "id": f"AC{index + 1}",
            "text": _text(item, "text", "title", "name", fallback=f"Legacy criterion {index + 1}"),
            "status": "unverified",
            "evidence_ids": [],
        }
        for index, item in enumerate(state["acceptance_criteria"])
    ]
    migrated["architecture"]["overview"] = _text(
        state["architecture"].get("overview"), fallback=""
    )
    migrated["architecture"]["components"] = [
        {
            "id": f"COMP{index + 1}",
            "name": _text(item, "name", "title", "id", fallback=f"Legacy component {index + 1}"),
            "responsibility": _text(item, "responsibility", "description", fallback=""),
            "interfaces": _strings(item.get("interfaces")) if isinstance(item, dict) else [],
            "failure_boundary": _text(item, "failure_boundary", fallback="Not recorded in v1"),
        }
        for index, item in enumerate(state["architecture"].get("components", []))
    ]
    migrated["architecture"]["decisions"] = [
        _text(item, "title", "decision", "text", fallback=f"Legacy architecture decision {index + 1}")
        for index, item in enumerate(state["architecture"].get("decisions", []))
    ]

    old_task_ids = {
        _text(item, "id", fallback=f"legacy-task-{index + 1}"): f"T{index + 1}"
        for index, item in enumerate(state["tasks"])
    }
    migrated["tasks"] = []
    for index, item in enumerate(state["tasks"]):
        old_dependencies = item.get("depends_on", []) if isinstance(item, dict) else []
        migrated["tasks"].append(
            {
                "id": f"T{index + 1}",
                "title": _text(item, "title", "text", "name", fallback=f"Legacy task {index + 1}"),
                "status": "pending",
                "depends_on": [
                    old_task_ids[dependency]
                    for dependency in _strings(old_dependencies)
                    if dependency in old_task_ids
                ],
                "work_type": "executable",
                "requirement_ids": [],
                "acceptance_criterion_ids": [],
                "verification": "Legacy task requires a new v2 proof plan.",
                "evidence_ids": [],
                "milestone_id": "M1",
            }
        )
    if migrated["tasks"] or migrated["acceptance_criteria"]:
        migrated["milestones"] = [
            {
                "id": "M1",
                "title": "Re-establish the migrated contract",
                "status": "pending",
                "task_ids": [item["id"] for item in migrated["tasks"]],
                "acceptance_criterion_ids": [
                    item["id"] for item in migrated["acceptance_criteria"]
                ],
                "closeout": None,
            }
        ]

    migrated["critiques"] = [
        {
            "id": f"CR{index + 1}",
            "source": _text(item, "source", fallback="legacy-v1"),
            "finding": _text(item, "finding", "title", "text", fallback=f"Legacy critique {index + 1}"),
            "status": "unverified",
            "disposition": _text(item, "disposition", fallback=""),
            "rationale": _text(item, "rationale", fallback=""),
        }
        for index, item in enumerate(state["critiques"])
    ]
    migrated["decisions"] = [
        {
            "id": f"D{index + 1}",
            "title": _text(item, "title", "decision", "text", fallback=f"Legacy decision {index + 1}"),
            "status": "unverified",
            "rationale": _text(item, "rationale", fallback=""),
            "impact": _text(item, "impact", fallback=""),
            "owner": _text(item, "owner", fallback=""),
        }
        for index, item in enumerate(state["decisions"])
    ]
    migrated["files"] = [
        {
            "id": f"F{index + 1}",
            "path": _text(item, "path", "name", fallback=f"legacy-file-{index + 1}"),
            "change": _text(item, "change", fallback="Legacy file record"),
            "digest": _digest_or_empty(item.get("digest") if isinstance(item, dict) else None),
            "note": _text(item, "note", fallback="Imported from v1; proof is not carried forward."),
        }
        for index, item in enumerate(state["files"])
    ]

    legacy_observations = list(state["verification"]) + list(state["evidence"])
    migrated["verification"] = [
        {
            "id": f"V{index + 1}",
            "name": _text(item, "name", "claim", "title", fallback=f"Legacy check {index + 1}"),
            "status": "unverified",
            "evidence_ids": [],
            "proof_type": "legacy-unbound",
            "freshness": "stale",
            "counterexample": "",
            "detail": (
                item
                if isinstance(item, str)
                else json.dumps(item, ensure_ascii=False, sort_keys=True)
            ),
        }
        for index, item in enumerate(legacy_observations)
    ]

    # Preserve collision-free legacy IDs. Generated IDs remain deterministic when
    # a legacy document omitted an ID or reused one across record collections.
    used_ids = {"CL1"}
    if migrated["milestones"]:
        used_ids.add("M1")

    def preserve_ids(
        records: list[dict[str, Any]], originals: list[Any], prefix: str
    ) -> dict[str, str]:
        renames: dict[str, str] = {}
        for index, record in enumerate(records):
            current = record["id"]
            original = originals[index] if index < len(originals) else None
            preferred = _text(original, "id", fallback="")
            base = preferred if preferred and preferred not in used_ids else current
            candidate = base
            suffix = 2
            while candidate in used_ids:
                candidate = f"{prefix}{index + 1}-{suffix}"
                suffix += 1
            record["id"] = candidate
            used_ids.add(candidate)
            renames[current] = candidate
        return renames

    preserve_ids(migrated["requirements"], state["requirements"], "R")
    criterion_renames = preserve_ids(
        migrated["acceptance_criteria"], state["acceptance_criteria"], "AC"
    )
    preserve_ids(
        migrated["architecture"]["components"],
        state["architecture"].get("components", []),
        "COMP",
    )
    task_renames = preserve_ids(migrated["tasks"], state["tasks"], "T")
    for task in migrated["tasks"]:
        task["depends_on"] = [task_renames.get(item, item) for item in task["depends_on"]]
    for milestone in migrated["milestones"]:
        milestone["task_ids"] = [
            task_renames.get(item, item) for item in milestone["task_ids"]
        ]
        milestone["acceptance_criterion_ids"] = [
            criterion_renames.get(item, item)
            for item in milestone["acceptance_criterion_ids"]
        ]
    preserve_ids(migrated["critiques"], state["critiques"], "CR")
    preserve_ids(migrated["decisions"], state["decisions"], "D")
    preserve_ids(migrated["files"], state["files"], "F")
    preserve_ids(migrated["verification"], legacy_observations, "V")
    migrated["gaps"] = _strings(state.get("gaps")) + [
        "Migrated from v1: requirements, criteria, tasks, and evidence require fresh v2 trace and proof."
    ]
    changed_ids = [
        record["id"]
        for collection in (
            migrated["requirements"],
            migrated["acceptance_criteria"],
            migrated["tasks"],
            migrated["milestones"],
            migrated["critiques"],
            migrated["decisions"],
            migrated["verification"],
            migrated["files"],
        )
        for record in collection
    ]
    migrated["clarity"]["ledger"] = [
        {
            "id": "CL1",
            "text": "Legacy content was imported without carrying proof authority forward.",
            "status": "known",
            "source": "explicit v1 migration",
            "affects": changed_ids,
            "blocking": False,
        }
    ]
    migrated["spec"]["changes"] = [
        {
            "revision": 1,
            "summary": "Imported legacy v1 content without upgrading proof.",
            "changed_ids": changed_ids,
            "reason": "Explicit v1 migration",
            "updated_at": migrated["updated_at"],
        }
    ]
    return validate_state(migrated)
