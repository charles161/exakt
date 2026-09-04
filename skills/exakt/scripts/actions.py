"""Approval-bound external actions with crash-safe ordering and recovery.

This module never performs provider I/O by itself.  The controller supplies
small persistence and provider adapters, making the ordering testable: intent
and ``send_started`` are durable before transmission, and ambiguous outcomes
are reconciled rather than blindly retried.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


def _load_reducer_module():
    path = Path(__file__).resolve().with_name("reducer.py")
    for module in tuple(sys.modules.values()):
        loaded = getattr(module, "__file__", None)
        if loaded is None:
            continue
        try:
            if Path(loaded).resolve() == path:
                return module
        except (OSError, RuntimeError, TypeError, ValueError):
            continue
    spec = importlib.util.spec_from_file_location("_exakt_actions_reducer", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import Exakt reducer from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_reducer = _load_reducer_module()

SIDE_EFFECT_CLASSES = (
    "local_read",
    "network_read",
    "local_reversible_write",
    "external_reversible_write",
    "external_irreversible_write",
    "publication",
    "production_deploy",
)
EXTERNAL_ACTION_STATES = (
    "planned",
    "approved",
    "intent_persisted",
    "send_started",
    "confirmed",
    "unknown",
    "failed",
)
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_TRANSITIONS = {
    "planned": frozenset({"approved"}),
    "approved": frozenset({"intent_persisted"}),
    "intent_persisted": frozenset({"send_started"}),
    "send_started": frozenset({"confirmed", "unknown", "failed"}),
    "unknown": frozenset({"confirmed", "failed"}),
    "confirmed": frozenset(),
    "failed": frozenset(),
}


class ActionError(ValueError):
    """Base deterministic external-action error."""


class ActionPolicyError(ActionError):
    """The action class or requested operation is forbidden."""


class ActionApprovalError(ActionError):
    """The action is not bound to a valid exact approval."""


class ActionStateError(ActionError):
    """An external-action transition is illegal."""


class ActionOutcomeUnknown(ActionError):
    def __init__(self, message: str, action_state: "ActionState"):
        super().__init__(message)
        self.action_state = action_state


@dataclass(frozen=True)
class ActionIntent:
    action_id: str
    work_item_id: str
    action_digest: str
    action_class: str
    target: str
    approval_id: str
    idempotency_key: str
    provider_id: str | None
    expires_at: str
    nonce: str


@dataclass(frozen=True)
class ActionTransition:
    state: str
    timestamp: str
    evidence_id: str | None = None


@dataclass(frozen=True)
class ActionState:
    intent: ActionIntent
    state: str
    transitions: tuple[ActionTransition, ...]
    provider_receipt: str | None = None


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ActionPolicyError(f"{label} must be non-empty")
    return value


def _validate_intent(intent: Any) -> ActionIntent:
    if not isinstance(intent, ActionIntent):
        raise ActionPolicyError("action intent has the wrong type")
    for name in (
        "action_id",
        "work_item_id",
        "target",
        "approval_id",
        "idempotency_key",
        "expires_at",
        "nonce",
    ):
        _nonempty(getattr(intent, name), f"action {name}")
    if intent.provider_id is not None:
        _nonempty(intent.provider_id, "provider ID")
    if _DIGEST.fullmatch(intent.action_digest) is None:
        raise ActionPolicyError("action digest must be a SHA-256 digest")
    if intent.action_class not in SIDE_EFFECT_CLASSES:
        raise ActionPolicyError("unknown side-effect class")
    if intent.action_class == "production_deploy":
        raise ActionPolicyError("Exakt v1 cannot initiate a production deployment")
    return intent


def _coerce_budget(budget: Any) -> Any:
    """Accept an equivalent reducer instance loaded by an isolated host."""
    if isinstance(budget, _reducer.BudgetState):
        return budget
    try:
        source_limits = budget.limits
        limits = _reducer.BudgetLimits(
            **{
                name: getattr(source_limits, name)
                for name in (
                    "agent_invocations",
                    "controller_commands",
                    "wall_clock_seconds",
                    "external_writes",
                    "monetary_minor",
                    "currency",
                )
            }
        )
        return _reducer.BudgetState(
            limits,
            dict(budget.spent),
            {key: dict(value) for key, value in budget.reservations.items()},
        )
    except (AttributeError, TypeError, ValueError, _reducer.BudgetError) as error:
        raise ActionPolicyError("budget state has the wrong type") from error


def transition_action(
    current: ActionState,
    target: str,
    timestamp: str,
    *,
    evidence_id: str | None = None,
    provider_receipt: str | None = None,
    idempotent_retry: bool = False,
) -> ActionState:
    if not isinstance(current, ActionState):
        raise ActionStateError("action state has the wrong type")
    _nonempty(timestamp, "action transition timestamp")
    if target not in EXTERNAL_ACTION_STATES:
        raise ActionStateError("unknown external-action state")
    allowed = target in _TRANSITIONS[current.state]
    if current.state == "unknown" and target == "send_started":
        allowed = idempotent_retry is True
    if not allowed:
        raise ActionStateError(
            f"illegal external-action transition: {current.state!r} -> {target!r}"
        )
    if target == "confirmed":
        _nonempty(provider_receipt, "confirmed provider receipt")
    elif provider_receipt is not None:
        raise ActionStateError("provider receipt is valid only for confirmation")
    transition = ActionTransition(target, timestamp, evidence_id)
    return ActionState(
        current.intent,
        target,
        current.transitions + (transition,),
        provider_receipt if target == "confirmed" else current.provider_receipt,
    )


def authorize_action(
    intent: ActionIntent,
    approval: Any,
    *,
    expected_state_root: str,
    clock_epoch: int,
    authority_source: str,
    now: datetime | str,
    budget: Any,
    used_nonces: frozenset[str] = frozenset(),
    persist: Callable[[ActionState], None] | None = None,
) -> tuple[ActionState, Any]:
    intent = _validate_intent(intent)
    if not isinstance(approval, dict):
        raise ActionApprovalError("action approval must be an object")
    if approval.get("approval_id") != intent.approval_id:
        raise ActionApprovalError("action approval ID does not match")
    if approval.get("nonce") != intent.nonce:
        raise ActionApprovalError("action approval nonce does not match")
    expectation = _reducer.ApprovalExpectation(
        work_item_id=intent.work_item_id,
        expected_state_root=expected_state_root,
        subject_digest=intent.action_digest,
        targets=(intent.target,),
        action_class=intent.action_class,
        clock_epoch=clock_epoch,
        require_single_use=intent.action_class
        in {"external_irreversible_write", "publication"},
    )
    try:
        _reducer.validate_approval(
            approval,
            expectation,
            authority_source=authority_source,
            now=now,
            used_nonces=used_nonces,
        )
    except _reducer.ApprovalValidationError as error:
        raise ActionApprovalError(str(error)) from error
    units: dict[str, int] = {}
    if intent.action_class in {
        "external_reversible_write",
        "external_irreversible_write",
        "publication",
    }:
        units["external_writes"] = 1
    if units:
        budget = _coerce_budget(budget)
        try:
            budget = _reducer.reserve_budget(
                budget,
                intent.action_id,
                units,
                currency=getattr(budget.limits, "currency", None),
            )
        except _reducer.BudgetError as error:
            raise ActionPolicyError(str(error)) from error
    timestamp = now if isinstance(now, str) else now.isoformat().replace("+00:00", "Z")
    planned = ActionState(
        intent,
        "planned",
        (ActionTransition("planned", timestamp),),
    )
    approved = transition_action(planned, "approved", timestamp)
    if persist is not None:
        persist(planned)
        persist(approved)
    return approved, budget


def _persisted_transition(
    state: ActionState,
    target: str,
    timestamp: str,
    persist: Callable[[ActionState], None],
    **kwargs: Any,
) -> ActionState:
    next_state = transition_action(state, target, timestamp, **kwargs)
    persist(next_state)
    return next_state


def execute_action(
    state: ActionState,
    *,
    persist: Callable[[ActionState], None],
    provider_send: Callable[[ActionIntent], str],
    timestamp: str,
) -> ActionState:
    if state.state == "approved":
        state = _persisted_transition(state, "intent_persisted", timestamp, persist)
    if state.state != "intent_persisted":
        raise ActionStateError("execution requires approved or persisted intent")
    state = _persisted_transition(state, "send_started", timestamp, persist)
    try:
        receipt = provider_send(state.intent)
        _nonempty(receipt, "provider receipt")
    except Exception as error:
        unknown = _persisted_transition(state, "unknown", timestamp, persist)
        raise ActionOutcomeUnknown(
            "provider outcome is ambiguous; reconciliation is required", unknown
        ) from error
    return _persisted_transition(
        state,
        "confirmed",
        timestamp,
        persist,
        provider_receipt=receipt,
    )


def recover_action(
    state: ActionState,
    *,
    persist: Callable[[ActionState], None],
    provider_lookup: Callable[[ActionIntent], Any],
    provider_send: Callable[[ActionIntent], str],
    provider_guarantees_idempotency: bool,
    timestamp: str,
) -> ActionState:
    if state.state == "intent_persisted":
        return execute_action(
            state,
            persist=persist,
            provider_send=provider_send,
            timestamp=timestamp,
        )
    if state.state == "send_started":
        state = _persisted_transition(state, "unknown", timestamp, persist)
    if state.state != "unknown":
        raise ActionStateError("only persisted or ambiguous actions can be recovered")
    try:
        result = provider_lookup(state.intent)
    except Exception:
        return state
    if isinstance(result, tuple) and len(result) == 2:
        status, receipt = result
        if status == "confirmed":
            return _persisted_transition(
                state,
                "confirmed",
                timestamp,
                persist,
                provider_receipt=receipt,
            )
        if status == "failed":
            return _persisted_transition(
                state, "failed", timestamp, persist, evidence_id=receipt
            )
        return state
    if result != "not_found" or provider_guarantees_idempotency is not True:
        return state
    retrying = _persisted_transition(
        state,
        "send_started",
        timestamp,
        persist,
        idempotent_retry=True,
    )
    try:
        receipt = provider_send(retrying.intent)
        _nonempty(receipt, "provider receipt")
    except Exception:
        return _persisted_transition(retrying, "unknown", timestamp, persist)
    return _persisted_transition(
        retrying,
        "confirmed",
        timestamp,
        persist,
        provider_receipt=receipt,
    )


def authorize_compensation(
    original: ActionState,
    compensation: ActionIntent,
    approval: Any,
    **authorization: Any,
) -> tuple[ActionState, Any]:
    if not isinstance(original, ActionState) or original.state != "confirmed":
        raise ActionStateError("compensation requires a confirmed original action")
    compensation = _validate_intent(compensation)
    if (
        compensation.action_id == original.intent.action_id
        or compensation.action_digest == original.intent.action_digest
        or compensation.approval_id == original.intent.approval_id
        or compensation.idempotency_key == original.intent.idempotency_key
        or compensation.nonce == original.intent.nonce
    ):
        raise ActionApprovalError(
            "compensation requires a new ID, digest, approval, key, and nonce"
        )
    return authorize_action(compensation, approval, **authorization)


__all__ = [
    "ActionApprovalError",
    "ActionError",
    "ActionIntent",
    "ActionOutcomeUnknown",
    "ActionPolicyError",
    "ActionState",
    "ActionStateError",
    "ActionTransition",
    "EXTERNAL_ACTION_STATES",
    "SIDE_EFFECT_CLASSES",
    "authorize_action",
    "authorize_compensation",
    "execute_action",
    "recover_action",
    "transition_action",
]
