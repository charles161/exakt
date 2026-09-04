import copy
import importlib.util
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
ACTIONS_PATH = PACKAGE_ROOT / "skills/exakt/scripts/actions.py"
REDUCER_PATH = PACKAGE_ROOT / "skills/exakt/scripts/reducer.py"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot import module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ExternalActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reducer = load(REDUCER_PATH, "exakt_reducer_for_actions_tests")
        cls.actions = load(ACTIONS_PATH, "exakt_actions")
        cls.now = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
        cls.root = "c" * 64
        cls.digest = "a" * 64

    def approval(self, approval_id="approval-1", nonce="nonce-1"):
        return {
            "schema_version": "approval-v1",
            "approval_id": approval_id,
            "work_item_id": "work-1",
            "expected_state_root": self.root,
            "subject_digest": self.digest,
            "authority": {
                "authority_kind": "live_user",
                "identity": "charles",
                "channel": "telegram",
                "receipt_id": None,
            },
            "scope": {
                "targets": ["telegram:8283681913"],
                "action_class": "external_irreversible_write",
                "slice_id": None,
                "oracle_digest": None,
                "external_action_policy_digest": None,
                "action_budget_digest": None,
            },
            "expires_at": "2026-09-03T13:00:00Z",
            "nonce": nonce,
            "clock_epoch": 1,
            "single_use": True,
        }

    def intent(self, **overrides):
        value = self.actions.ActionIntent(
            action_id="action-1",
            work_item_id="work-1",
            action_digest=self.digest,
            action_class="external_irreversible_write",
            target="telegram:8283681913",
            approval_id="approval-1",
            idempotency_key="provider-key-1",
            provider_id="telegram",
            expires_at="2026-09-03T13:00:00Z",
            nonce="nonce-1",
        )
        return self.actions.ActionIntent(**{**value.__dict__, **overrides})

    def budget(self):
        return self.reducer.BudgetState.empty(
            self.reducer.BudgetLimits(
                agent_invocations=1,
                controller_commands=10,
                wall_clock_seconds=60,
                external_writes=1,
                monetary_minor=0,
                currency="INR",
            )
        )

    def authorize(self, intent=None, approval=None, budget=None):
        return self.actions.authorize_action(
            intent or self.intent(),
            approval or self.approval(),
            expected_state_root=self.root,
            clock_epoch=1,
            authority_source="live_user",
            now=self.now,
            budget=budget or self.budget(),
        )

    def test_closed_side_effect_taxonomy_and_production_deploy_refusal(self):
        self.assertEqual(
            {
                "local_read",
                "network_read",
                "local_reversible_write",
                "external_reversible_write",
                "external_irreversible_write",
                "publication",
                "production_deploy",
            },
            set(self.actions.SIDE_EFFECT_CLASSES),
        )
        for action_class in ("production_deploy", "future_effect"):
            calls = []
            with self.subTest(action_class=action_class):
                with self.assertRaises(self.actions.ActionPolicyError):
                    self.actions.authorize_action(
                        self.intent(action_class=action_class),
                        self.approval(),
                        expected_state_root=self.root,
                        clock_epoch=1,
                        authority_source="live_user",
                        now=self.now,
                        budget=self.budget(),
                        persist=lambda *_: calls.append("persist"),
                    )
                self.assertEqual([], calls)

    def test_action_approval_binds_digest_target_nonce_and_identity(self):
        state, budget = self.authorize()
        self.assertEqual("approved", state.state)
        self.assertIn("action-1", budget.reservations)
        for override in (
            {"work_item_id": "work-2"},
            {"action_digest": "b" * 64},
            {"target": "telegram:other"},
            {"approval_id": "approval-other"},
            {"nonce": "nonce-other"},
        ):
            with self.subTest(override=override):
                with self.assertRaises(self.actions.ActionApprovalError):
                    self.authorize(intent=self.intent(**override))

    def test_intent_and_send_started_are_persisted_before_provider_io(self):
        state, _ = self.authorize()
        order = []

        def persist(updated):
            order.append(f"persist:{updated.state}")

        def send(intent):
            order.append(f"send:{intent.idempotency_key}")
            return "provider-receipt-1"

        completed = self.actions.execute_action(
            state, persist=persist, provider_send=send, timestamp="2026-09-03T12:01:00Z"
        )
        self.assertEqual("confirmed", completed.state)
        self.assertEqual(
            [
                "persist:intent_persisted",
                "persist:send_started",
                "send:provider-key-1",
                "persist:confirmed",
            ],
            order,
        )

    def test_crashes_after_intent_or_send_started_never_send_early(self):
        class Crash(BaseException):
            pass

        for crash_state in ("intent_persisted", "send_started"):
            with self.subTest(crash_state=crash_state):
                state, _ = self.authorize()
                persisted = []
                sent = []

                def persist(updated):
                    persisted.append(updated)
                    if updated.state == crash_state:
                        raise Crash(crash_state)

                with self.assertRaises(Crash):
                    self.actions.execute_action(
                        state,
                        persist=persist,
                        provider_send=lambda intent: sent.append(intent),
                        timestamp="2026-09-03T12:01:00Z",
                    )
                self.assertEqual([], sent)
                self.assertEqual(crash_state, persisted[-1].state)

    def test_provider_error_becomes_unknown_and_never_blindly_retries(self):
        state, _ = self.authorize()
        persisted = []
        sends = []

        def failing_send(intent):
            sends.append(intent.idempotency_key)
            raise TimeoutError("provider outcome unavailable")

        with self.assertRaises(self.actions.ActionOutcomeUnknown) as raised:
            self.actions.execute_action(
                state,
                persist=persisted.append,
                provider_send=failing_send,
                timestamp="2026-09-03T12:01:00Z",
            )
        self.assertEqual(["provider-key-1"], sends)
        self.assertEqual("unknown", raised.exception.action_state.state)
        self.assertEqual("unknown", persisted[-1].state)

    def test_ambiguous_resume_reconciles_before_same_key_retry(self):
        state, _ = self.authorize()
        started = self.actions.transition_action(
            self.actions.transition_action(
                state, "intent_persisted", "2026-09-03T12:00:01Z"
            ),
            "send_started",
            "2026-09-03T12:00:02Z",
        )
        order = []
        recovered = self.actions.recover_action(
            started,
            persist=lambda current: order.append(f"persist:{current.state}"),
            provider_lookup=lambda intent: order.append("lookup") or "not_found",
            provider_send=lambda intent: order.append(
                f"send:{intent.idempotency_key}"
            ) or "receipt-after-retry",
            provider_guarantees_idempotency=True,
            timestamp="2026-09-03T12:02:00Z",
        )
        self.assertEqual("confirmed", recovered.state)
        self.assertEqual(1, order.count("send:provider-key-1"))
        self.assertLess(order.index("lookup"), order.index("send:provider-key-1"))

    def test_non_idempotent_or_ambiguous_provider_is_never_retried(self):
        state, _ = self.authorize()
        started = self.actions.transition_action(
            self.actions.transition_action(
                state, "intent_persisted", "2026-09-03T12:00:01Z"
            ),
            "send_started",
            "2026-09-03T12:00:02Z",
        )
        for lookup_result, idempotent in (("not_found", False), ("ambiguous", True)):
            sent = []
            persisted = []
            with self.subTest(result=lookup_result, idempotent=idempotent):
                recovered = self.actions.recover_action(
                    started,
                    persist=persisted.append,
                    provider_lookup=lambda _intent, result=lookup_result: result,
                    provider_send=lambda intent: sent.append(intent),
                    provider_guarantees_idempotency=idempotent,
                    timestamp="2026-09-03T12:02:00Z",
                )
                self.assertEqual("unknown", recovered.state)
                self.assertEqual([], sent)

    def test_provider_receipt_reconciliation_confirms_without_resend(self):
        state, _ = self.authorize()
        started = self.actions.transition_action(
            self.actions.transition_action(
                state, "intent_persisted", "2026-09-03T12:00:01Z"
            ),
            "send_started",
            "2026-09-03T12:00:02Z",
        )
        sent = []
        recovered = self.actions.recover_action(
            started,
            persist=lambda _state: None,
            provider_lookup=lambda _intent: ("confirmed", "receipt-existing"),
            provider_send=lambda intent: sent.append(intent),
            provider_guarantees_idempotency=False,
            timestamp="2026-09-03T12:02:00Z",
        )
        self.assertEqual("confirmed", recovered.state)
        self.assertEqual("receipt-existing", recovered.provider_receipt)
        self.assertEqual([], sent)

    def test_compensation_is_a_fresh_approved_action_not_a_status_shortcut(self):
        original, _ = self.authorize()
        original = self.actions.transition_action(
            self.actions.transition_action(
                self.actions.transition_action(
                    original, "intent_persisted", "2026-09-03T12:00:01Z"
                ),
                "send_started",
                "2026-09-03T12:00:02Z",
            ),
            "confirmed",
            "2026-09-03T12:00:03Z",
            provider_receipt="receipt-1",
        )
        compensation = self.intent(
            action_id="action-2",
            action_digest="b" * 64,
            approval_id="approval-2",
            idempotency_key="provider-key-2",
            nonce="nonce-2",
        )
        with self.assertRaises(self.actions.ActionApprovalError):
            self.actions.authorize_compensation(
                original,
                compensation,
                self.approval(),
                expected_state_root=self.root,
                clock_epoch=1,
                authority_source="live_user",
                now=self.now,
                budget=self.budget(),
            )

        approval = self.approval("approval-2", "nonce-2")
        approval["subject_digest"] = "b" * 64
        compensated, _ = self.actions.authorize_compensation(
            original,
            compensation,
            approval,
            expected_state_root=self.root,
            clock_epoch=1,
            authority_source="live_user",
            now=self.now,
            budget=self.budget(),
        )
        self.assertEqual("approved", compensated.state)
        self.assertEqual("action-2", compensated.intent.action_id)
        self.assertEqual("confirmed", original.state)

    def test_transition_table_is_closed(self):
        state, _ = self.authorize()
        with self.assertRaises(self.actions.ActionStateError):
            self.actions.transition_action(
                state, "confirmed", "2026-09-03T12:01:00Z"
            )
        sent = copy.deepcopy(state)
        self.assertEqual(state, sent)


if __name__ == "__main__":
    unittest.main()
