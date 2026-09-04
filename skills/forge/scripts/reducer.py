"""Pure Forge workflow replay, transition gates, approvals, clocks, and budgets.

All functions in this module are deterministic and side-effect free.  Trusted
I/O observations are supplied by the controller as explicit inputs and become
authoritative only after the controller persists them in the journal.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


def _load_contracts_module():
    path = Path(__file__).resolve().with_name("contracts.py")
    spec = importlib.util.spec_from_file_location("_forge_reducer_contracts", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import Forge contracts from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ContractError, module.ContractRegistry, module._ensure_json_domain


_ContractError, _ContractRegistry, _ensure_json_domain = _load_contracts_module()
_CONTRACTS = _ContractRegistry()

WORKFLOW_PHASES = (
    "intake",
    "recon",
    "requirements",
    "design",
    "plan",
    "execute",
    "verify",
    "handoff",
)
TASK_STATUSES = (
    "ready",
    "implementing",
    "observing",
    "verifying",
    "repairing",
    "verified",
    "blocked",
    "unverified",
    "failed",
    "cancelled",
)
RUN_STATUSES = ("active", "suspended", "cancelled")
CLOSURE_STATUSES = (
    "open",
    "verified_complete",
    "independently_verified_complete",
    "closed_with_unverified_items",
    "blocked",
    "failed",
    "cancelled",
)
VERIFICATION_TIERS = ("none", "standard", "independent")
TRUSTED_APPROVAL_AUTHORITIES = ("live_user", "authenticated_host_receipt")

_FORWARD_PHASE_EDGES = tuple(zip(WORKFLOW_PHASES, WORKFLOW_PHASES[1:]))
_BACKWARD_PHASE_EDGES = (
    ("execute", "design"),
    ("verify", "design"),
    ("plan", "requirements"),
    ("execute", "requirements"),
    ("verify", "requirements"),
)
_PHASE_GUARDS: dict[tuple[str, str], tuple[str, ...]] = {
    ("intake", "recon"): (
        "run_active",
        "freshness_checkpoint",
        "private_source_captured",
        "identities_valid",
    ),
    ("recon", "requirements"): (
        "run_active",
        "freshness_checkpoint",
        "project_instructions_read",
        "scale_and_class_recorded",
        "capabilities_recorded",
        "unknowns_recorded",
    ),
    ("requirements", "design"): (
        "run_active",
        "freshness_checkpoint",
        "source_intent_coverage_complete",
        "requirements_valid",
        "draft_oracle_valid",
        "consequential_decisions_resolved",
    ),
    ("design", "plan"): (
        "run_active",
        "freshness_checkpoint",
        "alternatives_reviewed",
        "interfaces_reviewed",
        "risks_reviewed",
        "rollback_reviewed",
        "proof_strategy_reviewed",
        "semantic_diff_approved",
    ),
    ("plan", "execute"): (
        "run_active",
        "freshness_checkpoint",
        "task_graph_acyclic",
        "dependency_ready_tasks_valid",
        "leases_valid",
        "budgets_valid",
        "oracle_immutable",
        "external_action_policy_valid",
        "execution_scope_approved",
    ),
    ("execute", "verify"): (
        "run_active",
        "freshness_checkpoint",
        "no_required_task_in_progress",
        "merged_subject_captured",
        "nonverified_work_visible",
    ),
    ("verify", "handoff"): (
        "run_active",
        "freshness_checkpoint",
        "completion_predicate_evaluated",
        "closure_language_ledger_derived",
        "waivers_or_gaps_approved",
    ),
    ("execute", "design"): (
        "run_active",
        "freshness_checkpoint",
        "falsified_assumption_recorded",
        "superseding_oracle_proposed",
        "dependent_evidence_invalidated",
    ),
    ("verify", "design"): (
        "run_active",
        "freshness_checkpoint",
        "falsified_assumption_recorded",
        "superseding_oracle_proposed",
        "dependent_evidence_invalidated",
    ),
    ("plan", "requirements"): (
        "run_active",
        "freshness_checkpoint",
        "scope_change_approved",
        "superseding_oracle_proposed",
        "dependent_evidence_invalidated",
    ),
    ("execute", "requirements"): (
        "run_active",
        "freshness_checkpoint",
        "scope_change_approved",
        "superseding_oracle_proposed",
        "dependent_evidence_invalidated",
    ),
    ("verify", "requirements"): (
        "run_active",
        "freshness_checkpoint",
        "scope_change_approved",
        "superseding_oracle_proposed",
        "dependent_evidence_invalidated",
    ),
}
_APPROVAL_PHASE_EDGES = {("design", "plan"), ("plan", "execute")}
_TASK_TRANSITIONS = {
    "ready": frozenset({"implementing", "blocked", "cancelled"}),
    "implementing": frozenset(
        {"observing", "repairing", "blocked", "failed", "cancelled"}
    ),
    "observing": frozenset(
        {"verifying", "repairing", "blocked", "failed", "cancelled"}
    ),
    "verifying": frozenset(
        {"verified", "repairing", "blocked", "unverified", "failed", "cancelled"}
    ),
    "repairing": frozenset({"implementing", "blocked", "failed", "cancelled"}),
    "blocked": frozenset({"cancelled"}),
    "unverified": frozenset({"cancelled"}),
    "failed": frozenset({"cancelled"}),
    "verified": frozenset(),
    "cancelled": frozenset(),
}


class ReducerError(ValueError):
    """Base deterministic workflow-state error."""


class ReplayError(ReducerError):
    """An event stream cannot be replayed without guessing."""


class IllegalTransitionError(ReducerError):
    """A state edge is absent from the closed transition table."""


class PhaseGuardError(ReducerError):
    def __init__(self, message: str, missing_guards: Sequence[str] = ()):
        super().__init__(message)
        self.missing_guards = tuple(missing_guards)


class ApprovalValidationError(ReducerError):
    """An approval is unauthenticated, stale, expired, or incorrectly bound."""


class ClockError(ReducerError):
    """A clock observation is malformed."""


class BudgetError(ReducerError):
    """An action cannot reserve a known bounded budget."""


def required_phase_guards(source: str, target: str) -> tuple[str, ...]:
    try:
        return _PHASE_GUARDS[(source, target)]
    except KeyError as error:
        raise IllegalTransitionError(
            f"illegal workflow phase transition: {source!r} -> {target!r}"
        ) from error


def transition_phase(
    source: str,
    target: str,
    guard_facts: Mapping[str, Any],
    *,
    approval_authority: str | None = None,
    reason: str | None = None,
) -> str:
    required = required_phase_guards(source, target)
    if not isinstance(guard_facts, Mapping):
        raise PhaseGuardError("phase guard facts must be a mapping", required)
    missing = tuple(name for name in required if guard_facts.get(name) is not True)
    if missing:
        raise PhaseGuardError(
            "phase exit guard is not satisfied: " + ", ".join(missing), missing
        )
    if (source, target) in _APPROVAL_PHASE_EDGES and (
        approval_authority not in TRUSTED_APPROVAL_AUTHORITIES
    ):
        raise PhaseGuardError("phase transition lacks authenticated approval")
    if (source, target) in _BACKWARD_PHASE_EDGES and (
        not isinstance(reason, str) or not reason.strip()
    ):
        raise PhaseGuardError("backward phase transition requires a recorded reason")
    return target


def transition_task(
    source: str,
    target: str,
    *,
    resolution_recorded: bool = False,
    evidence_invalidated: bool = False,
) -> str:
    if source not in _TASK_TRANSITIONS or target not in _TASK_TRANSITIONS:
        raise IllegalTransitionError("unknown task status")
    if source in {"blocked", "unverified", "failed"} and target == "repairing":
        if resolution_recorded is not True:
            raise IllegalTransitionError(
                "repairing a non-success state requires a journaled resolution or retry"
            )
        return target
    if source == "verified" and target == "ready":
        if evidence_invalidated is not True:
            raise IllegalTransitionError(
                "verified work returns to ready only after evidence invalidation"
            )
        return target
    if target not in _TASK_TRANSITIONS[source]:
        raise IllegalTransitionError(
            f"illegal task transition: {source!r} -> {target!r}"
        )
    return target


def transition_run(source: str, target: str) -> str:
    allowed = {
        "active": {"suspended", "cancelled"},
        "suspended": {"active", "cancelled"},
        "cancelled": set(),
    }
    if source not in allowed or target not in allowed or target not in allowed[source]:
        raise IllegalTransitionError(f"illegal run transition: {source!r} -> {target!r}")
    return target


def transition_closure(
    source: str,
    target: str,
    *,
    all_required_verified: bool = False,
    verification_tier: str = "none",
    waiver_approved: bool = False,
    run_status: str = "active",
) -> str:
    if source not in CLOSURE_STATUSES or target not in CLOSURE_STATUSES:
        raise IllegalTransitionError("unknown closure status")
    if source != "open" or target == "open":
        raise IllegalTransitionError(
            f"illegal closure transition: {source!r} -> {target!r}"
        )
    if verification_tier not in VERIFICATION_TIERS:
        raise PhaseGuardError("unknown verification tier")
    if target in {"verified_complete", "independently_verified_complete"}:
        if all_required_verified is not True:
            raise PhaseGuardError("completion requires every required claim to be verified")
        if target == "verified_complete" and verification_tier not in {
            "standard",
            "independent",
        }:
            raise PhaseGuardError("verified completion requires a verification tier")
        if (
            target == "independently_verified_complete"
            and verification_tier != "independent"
        ):
            raise PhaseGuardError("independent completion requires independent proof")
    if target == "closed_with_unverified_items" and waiver_approved is not True:
        raise PhaseGuardError("non-complete closure requires an approved visible waiver")
    if target == "cancelled" and run_status != "cancelled":
        raise PhaseGuardError("cancelled closure requires cancelled run status")
    return target


def _parse_utc(value: str | datetime, *, label: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.endswith("Z"):
        try:
            parsed = datetime.fromisoformat(value[:-1] + "+00:00")
        except ValueError as error:
            raise ClockError(f"{label} is not a valid UTC timestamp") from error
    else:
        raise ClockError(f"{label} must be an RFC3339 UTC timestamp ending in Z")
    if parsed.tzinfo is None:
        raise ClockError(f"{label} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _render_utc(value: datetime) -> str:
    rendered = value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return rendered


@dataclass(frozen=True)
class ClockState:
    epoch: int
    greatest_wall_time: str
    monotonic_ns: int | None
    source: str
    invalidated_prior_epoch: bool = False
    invalidation_reason: str | None = None


def observe_clock(
    previous: ClockState | None,
    *,
    wall_time: str,
    monotonic_ns: int | None,
    source: str,
    resumed: bool = False,
) -> ClockState:
    current_wall = _parse_utc(wall_time, label="wall time")
    if not isinstance(source, str) or not source.strip():
        raise ClockError("clock source must be non-empty")
    if monotonic_ns is not None and (
        not isinstance(monotonic_ns, int)
        or isinstance(monotonic_ns, bool)
        or monotonic_ns < 0
    ):
        raise ClockError("monotonic observation must be a non-negative integer")
    if previous is None:
        return ClockState(1, _render_utc(current_wall), monotonic_ns, source)
    if not isinstance(previous, ClockState):
        raise ClockError("previous clock observation has the wrong type")
    prior_wall = _parse_utc(previous.greatest_wall_time, label="prior wall time")
    reason: str | None = None
    if source != previous.source:
        reason = "clock_source_changed"
    elif resumed and (previous.monotonic_ns is None or monotonic_ns is None):
        reason = "resume_elapsed_time_unreconciled"
    elif (
        previous.monotonic_ns is not None
        and monotonic_ns is not None
        and monotonic_ns < previous.monotonic_ns
    ):
        reason = "monotonic_clock_rollback"
    elif (prior_wall - current_wall).total_seconds() > 1:
        reason = "wall_clock_rollback"
    if reason is not None:
        return ClockState(
            previous.epoch + 1,
            _render_utc(current_wall),
            monotonic_ns,
            source,
            True,
            reason,
        )
    greatest = max(prior_wall, current_wall)
    return ClockState(
        previous.epoch,
        _render_utc(greatest),
        monotonic_ns,
        source,
    )


@dataclass(frozen=True)
class ApprovalExpectation:
    work_item_id: str
    expected_state_root: str
    subject_digest: str
    targets: tuple[str, ...]
    action_class: str
    clock_epoch: int
    require_single_use: bool = False
    slice_id: str | None = None
    oracle_digest: str | None = None
    external_action_policy_digest: str | None = None
    action_budget_digest: str | None = None


@dataclass(frozen=True)
class ApprovalGrant:
    approval_id: str
    nonce: str
    single_use: bool
    expires_at: str
    clock_epoch: int


def validate_approval(
    approval: Any,
    expectation: ApprovalExpectation,
    *,
    authority_source: str,
    now: datetime | str,
    used_nonces: frozenset[str] = frozenset(),
) -> ApprovalGrant:
    try:
        _CONTRACTS.validate(approval, "approval-v1")
    except _ContractError as error:
        raise ApprovalValidationError("approval violates its closed contract") from error
    if not isinstance(expectation, ApprovalExpectation):
        raise ApprovalValidationError("approval expectation has the wrong type")
    if authority_source not in TRUSTED_APPROVAL_AUTHORITIES:
        raise ApprovalValidationError("approval source is not a trusted live authority")
    authority = approval["authority"]
    if authority["authority_kind"] != authority_source:
        raise ApprovalValidationError("serialized authority does not match trusted source")
    if (
        authority_source == "authenticated_host_receipt"
        and not authority["receipt_id"]
    ):
        raise ApprovalValidationError("authenticated host authority lacks a receipt")
    exact = {
        "work_item_id": expectation.work_item_id,
        "expected_state_root": expectation.expected_state_root,
        "subject_digest": expectation.subject_digest,
        "clock_epoch": expectation.clock_epoch,
    }
    for field_name, expected in exact.items():
        if approval[field_name] != expected:
            raise ApprovalValidationError(f"approval {field_name} binding does not match")
    scope = approval["scope"]
    if set(scope["targets"]) != set(expectation.targets) or len(
        scope["targets"]
    ) != len(expectation.targets):
        raise ApprovalValidationError("approval target set does not match")
    scope_exact = {
        "action_class": expectation.action_class,
        "slice_id": expectation.slice_id,
        "oracle_digest": expectation.oracle_digest,
        "external_action_policy_digest": expectation.external_action_policy_digest,
        "action_budget_digest": expectation.action_budget_digest,
    }
    for field_name, expected in scope_exact.items():
        if scope[field_name] != expected:
            raise ApprovalValidationError(f"approval scope {field_name} does not match")
    if expectation.require_single_use and approval["single_use"] is not True:
        raise ApprovalValidationError("this action requires single-use approval")
    if approval["nonce"] in used_nonces:
        raise ApprovalValidationError("approval nonce was already consumed")
    try:
        expiration = _parse_utc(approval["expires_at"], label="approval expiry")
        observed_now = _parse_utc(now, label="current time")
    except ClockError as error:
        raise ApprovalValidationError(str(error)) from error
    if observed_now >= expiration:
        raise ApprovalValidationError("approval is expired")
    return ApprovalGrant(
        approval["approval_id"],
        approval["nonce"],
        approval["single_use"],
        approval["expires_at"],
        approval["clock_epoch"],
    )


_BUDGET_FIELDS = (
    "agent_invocations",
    "controller_commands",
    "wall_clock_seconds",
    "external_writes",
    "monetary_minor",
)


def _budget_number(value: Any, label: str, *, positive: bool = False) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < (1 if positive else 0):
        qualifier = "positive" if positive else "non-negative"
        raise BudgetError(f"{label} must be a {qualifier} integer or null")
    return value


@dataclass(frozen=True)
class BudgetLimits:
    agent_invocations: int | None
    controller_commands: int | None
    wall_clock_seconds: int | None
    external_writes: int | None
    monetary_minor: int | None
    currency: str | None

    def __post_init__(self):
        for name in _BUDGET_FIELDS:
            _budget_number(getattr(self, name), f"budget limit {name}")
        if self.monetary_minor is not None and (
            not isinstance(self.currency, str) or not self.currency.strip()
        ):
            raise BudgetError("a monetary limit requires a currency")


@dataclass(frozen=True)
class BudgetState:
    limits: BudgetLimits
    spent: dict[str, int] = field(default_factory=dict)
    reservations: dict[str, dict[str, int]] = field(default_factory=dict)

    @classmethod
    def empty(cls, limits: BudgetLimits) -> "BudgetState":
        if not isinstance(limits, BudgetLimits):
            raise BudgetError("budget limits have the wrong type")
        return cls(limits, {name: 0 for name in _BUDGET_FIELDS}, {})


def reserve_budget(
    state: BudgetState,
    reservation_id: str,
    units: Mapping[str, Any],
    *,
    currency: str | None = None,
) -> BudgetState:
    if not isinstance(state, BudgetState):
        raise BudgetError("budget state has the wrong type")
    if not isinstance(reservation_id, str) or not reservation_id.strip():
        raise BudgetError("reservation ID must be non-empty")
    if not isinstance(units, Mapping) or not units:
        raise BudgetError("reservation units must be a non-empty mapping")
    unknown = sorted(set(units) - set(_BUDGET_FIELDS))
    if unknown:
        raise BudgetError(f"unknown budget counter: {unknown[0]}")
    normalized: dict[str, int] = {}
    for name, value in units.items():
        parsed = _budget_number(value, f"reservation {name}", positive=True)
        assert parsed is not None
        normalized[name] = parsed
    existing = state.reservations.get(reservation_id)
    if existing is not None:
        if existing == normalized:
            return state
        raise BudgetError("reservation ID is already bound to different units")
    if normalized.get("monetary_minor", 0) > 0 and currency != state.limits.currency:
        raise BudgetError("reservation currency does not match the budget")
    for name, requested in normalized.items():
        limit = getattr(state.limits, name)
        if limit is None:
            raise BudgetError(f"budget counter {name} is unavailable or unknowable")
        reserved = sum(item.get(name, 0) for item in state.reservations.values())
        if state.spent.get(name, 0) + reserved + requested > limit:
            raise BudgetError(f"budget counter {name} would be exceeded")
    reservations = {key: dict(value) for key, value in state.reservations.items()}
    reservations[reservation_id] = normalized
    return BudgetState(state.limits, dict(state.spent), reservations)


def release_reservation(state: BudgetState, reservation_id: str) -> BudgetState:
    if reservation_id not in state.reservations:
        return state
    reservations = {key: dict(value) for key, value in state.reservations.items()}
    reservations.pop(reservation_id)
    return BudgetState(state.limits, dict(state.spent), reservations)


def commit_reservation(state: BudgetState, reservation_id: str) -> BudgetState:
    if reservation_id not in state.reservations:
        raise BudgetError("cannot commit a missing reservation")
    reservations = {key: dict(value) for key, value in state.reservations.items()}
    units = reservations.pop(reservation_id)
    spent = dict(state.spent)
    for name, value in units.items():
        spent[name] = spent.get(name, 0) + value
    return BudgetState(state.limits, spent, reservations)


@dataclass(frozen=True)
class ReplayState:
    work_item_id: str
    workflow_phase: str = "intake"
    run_status: str = "active"
    closure_status: str = "open"
    tasks: dict[str, str] = field(default_factory=dict)
    seen_event_ids: dict[str, str] = field(default_factory=dict)
    idempotency_keys: dict[str, str] = field(default_factory=dict)


_REPLAY_EVENT_KEYS = {
    "event_id",
    "event_type",
    "idempotency_key",
    "actor",
    "timestamp",
    "data",
}


def _event_digest(event: Mapping[str, Any]) -> str:
    try:
        _ensure_json_domain(event)
        payload = json.dumps(
            event, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise ReplayError("event cannot be canonically fingerprinted") from error
    return hashlib.sha256(payload).hexdigest()


def _validate_replay_event(event: Any) -> dict[str, Any]:
    if not isinstance(event, dict) or set(event) != _REPLAY_EVENT_KEYS:
        raise ReplayError("replay event violates its closed envelope")
    for name in ("event_id", "event_type", "idempotency_key", "actor", "timestamp"):
        if not isinstance(event[name], str) or not event[name]:
            raise ReplayError(f"replay event {name} must be non-empty")
    if not isinstance(event["data"], dict):
        raise ReplayError("replay event data must be an object")
    _event_digest(event)
    return event


def replay_events(work_item_id: str, events: Sequence[Any]) -> ReplayState:
    if not isinstance(work_item_id, str) or not work_item_id:
        raise ReplayError("work-item ID must be non-empty")
    if isinstance(events, (str, bytes)) or not isinstance(events, Sequence):
        raise ReplayError("events must be an ordered sequence")
    phase = "intake"
    run = "active"
    closure = "open"
    tasks: dict[str, str] = {}
    seen: dict[str, str] = {}
    idempotency: dict[str, str] = {}
    for raw_event in events:
        event = _validate_replay_event(raw_event)
        digest = _event_digest(event)
        event_id = event["event_id"]
        key = event["idempotency_key"]
        if event_id in seen:
            if seen[event_id] == digest and idempotency.get(key) == digest:
                continue
            raise ReplayError("event ID was reused with different content")
        if key in idempotency:
            raise ReplayError("idempotency key was reused with different content")
        kind = event["event_type"]
        data = event["data"]
        try:
            if kind == "task_registered":
                if set(data) != {"task_id"} or not isinstance(data["task_id"], str) or not data["task_id"]:
                    raise ReplayError("task registration violates its closed contract")
                if data["task_id"] in tasks:
                    raise ReplayError("task was registered more than once")
                tasks[data["task_id"]] = "ready"
            elif kind == "task_transitioned":
                allowed = {"task_id", "from", "to", "resolution_recorded", "evidence_invalidated"}
                if not {"task_id", "from", "to"}.issubset(data) or set(data) - allowed:
                    raise ReplayError("task transition violates its closed contract")
                task_id = data["task_id"]
                if task_id not in tasks or tasks[task_id] != data["from"]:
                    raise ReplayError("task transition does not match replayed state")
                tasks[task_id] = transition_task(
                    data["from"],
                    data["to"],
                    resolution_recorded=data.get("resolution_recorded") is True,
                    evidence_invalidated=data.get("evidence_invalidated") is True,
                )
            elif kind == "phase_transitioned":
                allowed = {"from", "to", "guards", "approval_authority", "reason"}
                if not {"from", "to", "guards"}.issubset(data) or set(data) - allowed:
                    raise ReplayError("phase transition violates its closed contract")
                if data["from"] != phase:
                    raise ReplayError("phase transition does not match replayed state")
                phase = transition_phase(
                    data["from"],
                    data["to"],
                    data["guards"],
                    approval_authority=data.get("approval_authority"),
                    reason=data.get("reason"),
                )
            elif kind == "run_transitioned":
                if set(data) != {"from", "to"} or data["from"] != run:
                    raise ReplayError("run transition does not match replayed state")
                run = transition_run(data["from"], data["to"])
                if run == "cancelled":
                    for task_id, status in tuple(tasks.items()):
                        if status != "cancelled":
                            tasks[task_id] = "cancelled"
                    closure = "cancelled"
            elif kind == "closure_transitioned":
                allowed = {
                    "from",
                    "to",
                    "all_required_verified",
                    "verification_tier",
                    "waiver_approved",
                }
                if not {"from", "to"}.issubset(data) or set(data) - allowed or data["from"] != closure:
                    raise ReplayError("closure transition does not match replayed state")
                closure = transition_closure(
                    data["from"],
                    data["to"],
                    all_required_verified=data.get("all_required_verified") is True,
                    verification_tier=data.get("verification_tier", "none"),
                    waiver_approved=data.get("waiver_approved") is True,
                    run_status=run,
                )
            else:
                raise ReplayError(f"unknown replay event type: {kind}")
        except ReducerError as error:
            if isinstance(error, ReplayError):
                raise
            raise ReplayError(f"event {event_id} is invalid: {error}") from error
        seen[event_id] = digest
        idempotency[key] = digest
    return ReplayState(work_item_id, phase, run, closure, tasks, seen, idempotency)


__all__ = [
    "ApprovalExpectation",
    "ApprovalGrant",
    "ApprovalValidationError",
    "BudgetError",
    "BudgetLimits",
    "BudgetState",
    "CLOSURE_STATUSES",
    "ClockError",
    "ClockState",
    "IllegalTransitionError",
    "PhaseGuardError",
    "RUN_STATUSES",
    "ReducerError",
    "ReplayError",
    "ReplayState",
    "TASK_STATUSES",
    "TRUSTED_APPROVAL_AUTHORITIES",
    "VERIFICATION_TIERS",
    "WORKFLOW_PHASES",
    "commit_reservation",
    "observe_clock",
    "release_reservation",
    "replay_events",
    "required_phase_guards",
    "reserve_budget",
    "transition_closure",
    "transition_phase",
    "transition_run",
    "transition_task",
    "validate_approval",
]
