import copy
import importlib.util
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REDUCER_PATH = PACKAGE_ROOT / "skills/forge/scripts/reducer.py"


def load_module():
    spec = importlib.util.spec_from_file_location("forge_reducer", REDUCER_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot import reducer module: {REDUCER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class TransitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reducer = load_module()

    def complete_guards(self, source, target):
        return {
            name: True
            for name in self.reducer.required_phase_guards(source, target)
        }

    def test_every_forward_phase_exit_and_only_declared_backtracks(self):
        phases = self.reducer.WORKFLOW_PHASES
        forward = list(zip(phases, phases[1:]))
        backward = [
            ("execute", "design"),
            ("verify", "design"),
            ("plan", "requirements"),
            ("execute", "requirements"),
            ("verify", "requirements"),
        ]
        legal = set(forward + backward)

        for source, target in legal:
            with self.subTest(source=source, target=target):
                authority = None
                if (source, target) in {
                    ("design", "plan"),
                    ("plan", "execute"),
                }:
                    authority = "live_user"
                self.assertEqual(
                    target,
                    self.reducer.transition_phase(
                        source,
                        target,
                        self.complete_guards(source, target),
                        approval_authority=authority,
                        reason="new evidence invalidated an assumption",
                    ),
                )

        for source in phases:
            for target in phases:
                if source == target or (source, target) in legal:
                    continue
                with self.subTest(illegal=(source, target)):
                    with self.assertRaises(self.reducer.IllegalTransitionError):
                        self.reducer.transition_phase(source, target, {})

    def test_each_phase_guard_is_individually_required(self):
        for source, target in zip(
            self.reducer.WORKFLOW_PHASES, self.reducer.WORKFLOW_PHASES[1:]
        ):
            guards = self.complete_guards(source, target)
            for omitted in guards:
                with self.subTest(edge=(source, target), omitted=omitted):
                    facts = dict(guards)
                    facts.pop(omitted)
                    with self.assertRaises(self.reducer.PhaseGuardError) as raised:
                        self.reducer.transition_phase(
                            source,
                            target,
                            facts,
                            approval_authority="live_user",
                        )
                    self.assertIn(omitted, raised.exception.missing_guards)

    def test_agents_cannot_approve_design_or_execution(self):
        for edge in (("design", "plan"), ("plan", "execute")):
            for bad in (None, "agent", "repository_text", "imported_file"):
                with self.subTest(edge=edge, authority=bad):
                    with self.assertRaises(self.reducer.PhaseGuardError):
                        self.reducer.transition_phase(
                            *edge,
                            self.complete_guards(*edge),
                            approval_authority=bad,
                        )
            for valid in ("live_user", "authenticated_host_receipt"):
                self.assertEqual(
                    edge[1],
                    self.reducer.transition_phase(
                        *edge,
                        self.complete_guards(*edge),
                        approval_authority=valid,
                    ),
                )

    def test_task_transition_table_and_exception_guards(self):
        normal = {
            "ready": {"implementing", "blocked", "cancelled"},
            "implementing": {"observing", "repairing", "blocked", "failed", "cancelled"},
            "observing": {"verifying", "repairing", "blocked", "failed", "cancelled"},
            "verifying": {"verified", "repairing", "blocked", "unverified", "failed", "cancelled"},
            "repairing": {"implementing", "blocked", "failed", "cancelled"},
            "blocked": {"cancelled"},
            "unverified": {"cancelled"},
            "failed": {"cancelled"},
            "verified": set(),
            "cancelled": set(),
        }
        statuses = tuple(normal)
        for source in statuses:
            for target in statuses:
                if target in normal[source]:
                    self.assertEqual(target, self.reducer.transition_task(source, target))
                elif source == target:
                    continue
                else:
                    with self.assertRaises(self.reducer.IllegalTransitionError):
                        self.reducer.transition_task(source, target)

        for source in ("blocked", "unverified", "failed"):
            with self.assertRaises(self.reducer.IllegalTransitionError):
                self.reducer.transition_task(source, "repairing")
            self.assertEqual(
                "repairing",
                self.reducer.transition_task(
                    source, "repairing", resolution_recorded=True
                ),
            )
            self.assertEqual(
                "cancelled", self.reducer.transition_task(source, "cancelled")
            )
        with self.assertRaises(self.reducer.IllegalTransitionError):
            self.reducer.transition_task("verified", "ready")
        self.assertEqual(
            "ready",
            self.reducer.transition_task(
                "verified", "ready", evidence_invalidated=True
            ),
        )

    def test_run_and_closure_states_are_orthogonal_and_terminal(self):
        self.assertEqual("suspended", self.reducer.transition_run("active", "suspended"))
        self.assertEqual("active", self.reducer.transition_run("suspended", "active"))
        self.assertEqual("cancelled", self.reducer.transition_run("active", "cancelled"))
        with self.assertRaises(self.reducer.IllegalTransitionError):
            self.reducer.transition_run("cancelled", "active")

        self.assertEqual(
            "verified_complete",
            self.reducer.transition_closure(
                "open",
                "verified_complete",
                all_required_verified=True,
                verification_tier="standard",
            ),
        )
        self.assertEqual(
            "independently_verified_complete",
            self.reducer.transition_closure(
                "open",
                "independently_verified_complete",
                all_required_verified=True,
                verification_tier="independent",
            ),
        )
        with self.assertRaises(self.reducer.PhaseGuardError):
            self.reducer.transition_closure(
                "open", "verified_complete", all_required_verified=False
            )
        with self.assertRaises(self.reducer.PhaseGuardError):
            self.reducer.transition_closure(
                "open",
                "independently_verified_complete",
                all_required_verified=True,
                verification_tier="standard",
            )
        with self.assertRaises(self.reducer.PhaseGuardError):
            self.reducer.transition_closure(
                "open", "closed_with_unverified_items", waiver_approved=False
            )
        self.assertEqual(
            "cancelled",
            self.reducer.transition_closure(
                "open", "cancelled", run_status="cancelled"
            ),
        )
        with self.assertRaises(self.reducer.IllegalTransitionError):
            self.reducer.transition_closure("failed", "open")


class ReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reducer = load_module()

    def event(self, event_id, event_type, data, key=None, actor="controller"):
        return {
            "event_id": event_id,
            "event_type": event_type,
            "idempotency_key": key or f"key-{event_id}",
            "actor": actor,
            "timestamp": "2026-09-03T12:00:00Z",
            "data": data,
        }

    def test_replay_is_pure_deterministic_and_exact_duplicates_are_idempotent(self):
        phase_guards = {
            name: True
            for name in self.reducer.required_phase_guards("intake", "recon")
        }
        events = [
            self.event("1", "task_registered", {"task_id": "t1"}),
            self.event(
                "2",
                "task_transitioned",
                {"task_id": "t1", "from": "ready", "to": "implementing"},
            ),
            self.event(
                "3",
                "phase_transitioned",
                {"from": "intake", "to": "recon", "guards": phase_guards},
            ),
        ]
        original = copy.deepcopy(events)
        first = self.reducer.replay_events("work-1", events)
        second = self.reducer.replay_events("work-1", [*events, copy.deepcopy(events[-1])])
        self.assertEqual(first, second)
        self.assertEqual(original, events)
        self.assertEqual("recon", first.workflow_phase)
        self.assertEqual("implementing", first.tasks["t1"])

    def test_unknown_duplicate_or_illegal_events_fail_closed(self):
        with self.assertRaises(self.reducer.ReplayError):
            self.reducer.replay_events(
                "work-1", [self.event("1", "future_magic", {})]
            )
        same_key = "same-key"
        with self.assertRaises(self.reducer.ReplayError):
            self.reducer.replay_events(
                "work-1",
                [
                    self.event("1", "task_registered", {"task_id": "t1"}, same_key),
                    self.event("2", "task_registered", {"task_id": "t2"}, same_key),
                ],
            )
        with self.assertRaises(self.reducer.ReplayError):
            self.reducer.replay_events(
                "work-1",
                [self.event("1", "task_transitioned", {"task_id": "missing", "from": "ready", "to": "implementing"})],
            )
        with self.assertRaises(self.reducer.ReplayError):
            self.reducer.replay_events(
                "work-1",
                [
                    self.event("1", "task_registered", {"task_id": "t1"}),
                    self.event("1", "task_registered", {"task_id": "t2"}),
                ],
            )


class ApprovalClockBudgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reducer = load_module()
        cls.digest_a = "a" * 64
        cls.digest_b = "b" * 64
        cls.root = "c" * 64

    def approval(self):
        return {
            "schema_version": "approval-v1",
            "approval_id": "approval-1",
            "work_item_id": "work-1",
            "expected_state_root": self.root,
            "subject_digest": self.digest_a,
            "authority": {
                "authority_kind": "live_user",
                "identity": "charles",
                "channel": "telegram",
                "receipt_id": None,
            },
            "scope": {
                "targets": ["repo-1"],
                "action_class": "external_irreversible_write",
                "slice_id": None,
                "oracle_digest": None,
                "external_action_policy_digest": None,
                "action_budget_digest": None,
            },
            "expires_at": "2026-09-03T13:00:00Z",
            "nonce": "nonce-1",
            "clock_epoch": 3,
            "single_use": True,
        }

    def expectation(self):
        return self.reducer.ApprovalExpectation(
            work_item_id="work-1",
            expected_state_root=self.root,
            subject_digest=self.digest_a,
            targets=("repo-1",),
            action_class="external_irreversible_write",
            clock_epoch=3,
            require_single_use=True,
        )

    def test_approval_is_bound_to_every_security_dimension(self):
        now = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
        grant = self.reducer.validate_approval(
            self.approval(),
            self.expectation(),
            authority_source="live_user",
            now=now,
            used_nonces=frozenset(),
        )
        self.assertEqual("approval-1", grant.approval_id)

        mutations = {
            "wrong_work_item": ("work_item_id", "work-2"),
            "stale_root": ("expected_state_root", self.digest_b),
            "changed_digest": ("subject_digest", self.digest_b),
            "wrong_epoch": ("clock_epoch", 2),
            "not_single_use": ("single_use", False),
        }
        for name, (field, value) in mutations.items():
            with self.subTest(name=name):
                approval = self.approval()
                approval[field] = value
                with self.assertRaises(self.reducer.ApprovalValidationError):
                    self.reducer.validate_approval(
                        approval,
                        self.expectation(),
                        authority_source="live_user",
                        now=now,
                    )

        approval = self.approval()
        approval["scope"]["targets"] = ["repo-2"]
        with self.assertRaises(self.reducer.ApprovalValidationError):
            self.reducer.validate_approval(
                approval, self.expectation(), authority_source="live_user", now=now
            )
        approval = self.approval()
        approval["scope"]["action_class"] = "publication"
        with self.assertRaises(self.reducer.ApprovalValidationError):
            self.reducer.validate_approval(
                approval, self.expectation(), authority_source="live_user", now=now
            )

    def test_expired_replayed_or_agent_authored_approval_is_rejected(self):
        now = datetime(2026, 9, 3, 13, 0, tzinfo=timezone.utc)
        for source, used in (
            ("agent_text", frozenset()),
            ("repository_text", frozenset()),
            ("live_user", frozenset({"nonce-1"})),
        ):
            with self.subTest(source=source, used=used):
                with self.assertRaises(self.reducer.ApprovalValidationError):
                    self.reducer.validate_approval(
                        self.approval(),
                        self.expectation(),
                        authority_source=source,
                        now=now,
                        used_nonces=used,
                    )

    def test_authenticated_host_receipt_requires_a_real_receipt(self):
        approval = self.approval()
        approval["authority"]["authority_kind"] = "authenticated_host_receipt"
        now = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
        with self.assertRaises(self.reducer.ApprovalValidationError):
            self.reducer.validate_approval(
                approval,
                self.expectation(),
                authority_source="authenticated_host_receipt",
                now=now,
            )
        approval["authority"]["receipt_id"] = "host-receipt-1"
        self.reducer.validate_approval(
            approval,
            self.expectation(),
            authority_source="authenticated_host_receipt",
            now=now,
        )

    def test_clock_rollback_source_change_and_unreconciled_resume_open_new_epoch(self):
        state = self.reducer.observe_clock(
            None,
            wall_time="2026-09-03T12:00:00Z",
            monotonic_ns=100,
            source="system-utc",
        )
        self.assertEqual(1, state.epoch)
        self.assertFalse(state.invalidated_prior_epoch)
        tolerant = self.reducer.observe_clock(
            state,
            wall_time="2026-09-03T11:59:59.500000Z",
            monotonic_ns=101,
            source="system-utc",
        )
        self.assertEqual(1, tolerant.epoch)
        self.assertEqual(state.greatest_wall_time, tolerant.greatest_wall_time)

        cases = [
            dict(wall_time="2026-09-03T11:59:58Z", monotonic_ns=102, source="system-utc"),
            dict(wall_time="2026-09-03T12:00:01Z", monotonic_ns=102, source="ntp-utc"),
            dict(wall_time="2026-09-03T12:00:01Z", monotonic_ns=99, source="system-utc"),
            dict(wall_time="2026-09-03T12:00:01Z", monotonic_ns=None, source="system-utc", resumed=True),
        ]
        for case in cases:
            with self.subTest(case=case):
                changed = self.reducer.observe_clock(state, **case)
                self.assertEqual(2, changed.epoch)
                self.assertTrue(changed.invalidated_prior_epoch)
                self.assertTrue(changed.invalidation_reason)

    def test_budget_reservations_are_atomic_bounded_and_releasable(self):
        limits = self.reducer.BudgetLimits(
            agent_invocations=2,
            controller_commands=4,
            wall_clock_seconds=60,
            external_writes=1,
            monetary_minor=500,
            currency="INR",
        )
        empty = self.reducer.BudgetState.empty(limits)
        reserved = self.reducer.reserve_budget(
            empty,
            "reservation-1",
            {"agent_invocations": 1, "external_writes": 1, "monetary_minor": 200},
            currency="INR",
        )
        self.assertEqual({}, empty.reservations)
        self.assertEqual(1, reserved.reservations["reservation-1"]["external_writes"])
        for units, currency in (
            ({"external_writes": 1}, "INR"),
            ({"unknown_counter": 1}, "INR"),
            ({"monetary_minor": 1}, "USD"),
        ):
            with self.subTest(units=units, currency=currency):
                with self.assertRaises(self.reducer.BudgetError):
                    self.reducer.reserve_budget(
                        reserved, "reservation-2", units, currency=currency
                    )
        released = self.reducer.release_reservation(reserved, "reservation-1")
        self.assertEqual({}, released.reservations)
        committed = self.reducer.commit_reservation(reserved, "reservation-1")
        self.assertEqual(1, committed.spent["external_writes"])
        self.assertEqual({}, committed.reservations)


if __name__ == "__main__":
    unittest.main()
