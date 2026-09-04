import copy
import importlib.util
import sys
import unittest
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PACKAGE_ROOT / "skills/exakt/scripts"
MODULE_PATH = SCRIPTS_ROOT / "report_state.py"
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


def load_module():
    if not MODULE_PATH.is_file():
        raise AssertionError(f"missing report-state module: {MODULE_PATH}")
    sys.path.insert(0, str(SCRIPTS_ROOT))
    try:
        spec = importlib.util.spec_from_file_location("exakt_report_state", MODULE_PATH)
        if spec is None or spec.loader is None:
            raise AssertionError("cannot import report-state module")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


def legacy_v1_state():
    return {
        "schema_version": "exakt-report-v1",
        "title": "Legacy example",
        "mode": "task",
        "summary": "Old state",
        "status": "draft",
        "phase": "intake",
        "updated_at": "2026-09-04T00:00:00Z",
        "brief": {"outcome": "Fix it", "users": [], "constraints": []},
        "requirements": [],
        "architecture": {"overview": "", "components": [], "decisions": []},
        "acceptance_criteria": [],
        "tasks": [],
        "critiques": [],
        "decisions": [],
        "verification": [],
        "files": [],
        "evidence": [],
        "gaps": [],
    }


class ReportStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state_module = load_module()

    def initial(self):
        return self.state_module.initial_state(
            "Make chapter navigation URL-shareable",
            "task",
            now="2026-09-04T00:00:00Z",
        )

    def complete_state(self, work_type, stages):
        state = self.initial()
        state["status"] = "verified"
        state["phase"] = "handoff"
        state["requirements"] = [
            {"id": "R1", "text": "Navigation is shareable", "status": "verified"}
        ]
        state["primitives"] = {
            "behaviors": [
                {"id": "B1", "text": "URL restores a chapter", "status": "verified"}
            ],
            "invariants": [
                {"id": "INV1", "text": "Back remains reversible", "status": "verified"}
            ],
            "oracles": [
                {
                    "id": "O1",
                    "text": "Drive history in a browser",
                    "method": "browser",
                    "status": "verified",
                }
            ],
            "counterexamples": [
                {
                    "id": "C1",
                    "text": "Back leaves the chapter unchanged",
                    "targets": ["B1"],
                    "status": "verified",
                }
            ],
        }
        state["acceptance_criteria"] = [
            {
                "id": "AC1",
                "text": "Back restores the previous chapter",
                "status": "verified",
                "evidence_ids": [f"E{index + 1}" for index in range(len(stages))],
            }
        ]
        state["tasks"] = [
            {
                "id": "T1",
                "title": "Implement URL state",
                "status": "verified",
                "depends_on": [],
                "work_type": work_type,
                "requirement_ids": ["R1"],
                "acceptance_criterion_ids": ["AC1"],
                "verification": "Exercise the real history boundary",
                "evidence_ids": [f"E{index + 1}" for index in range(len(stages))],
                "milestone_id": "M1",
            }
        ]
        state["evidence"] = [
            {
                "id": f"E{index + 1}",
                "name": stage.title(),
                "type": "command",
                "stage": stage,
                "provenance": "separated",
                "subject_digest": DIGEST_B,
                "contract_digest": DIGEST_A,
                "status": "verified",
                "result": (
                    "failed-as-expected"
                    if stage == "red"
                    else "observed" if stage == "before" else "passed"
                ),
                "command": "python3 -m unittest",
                "detail": "Observed against the current source",
            }
            for index, stage in enumerate(stages)
        ]
        state["verification"] = [
            {
                "id": "V1",
                "name": "Acceptance proof",
                "status": "verified",
                "evidence_ids": [f"E{index + 1}" for index in range(len(stages))],
                "proof_type": "behavior",
                "freshness": "fresh",
                "counterexample": "C1",
            }
        ]
        state["milestones"] = [
            {
                "id": "M1",
                "title": "Shareable navigation",
                "status": "verified",
                "task_ids": ["T1"],
                "acceptance_criterion_ids": ["AC1"],
                "closeout": {
                    "completed": "Navigation works through history",
                    "covered_ids": ["R1", "B1", "INV1", "AC1"],
                    "changed_paths": ["src/navigation.ts"],
                    "evidence_ids": [f"E{index + 1}" for index in range(len(stages))],
                    "gaps": [],
                    "commit": {"state": "not-authorized", "hash": None, "message": ""},
                    "status": "verified",
                },
            }
        ]
        state["traceability"] = {
            "edges": [
                {"from": "R1", "to": "B1", "kind": "defines"},
                {"from": "B1", "to": "AC1", "kind": "accepted_by"},
                {"from": "B1", "to": "INV1", "kind": "protects"},
                {"from": "INV1", "to": "O1", "kind": "observed_by"},
                {"from": "O1", "to": "C1", "kind": "challenged_by"},
                {"from": "AC1", "to": "T1", "kind": "implemented_by"},
                *(
                    {"from": "T1", "to": f"E{index + 1}", "kind": "proved_by"}
                    for index in range(len(stages))
                ),
                {"from": "T1", "to": "M1", "kind": "delivered_in"},
            ],
            "invalidations": [],
        }
        digest = self.state_module.contract_digest(state)
        state["spec"]["digest"] = digest
        for item in state["evidence"]:
            item["contract_digest"] = digest
        return state

    def test_v2_initialization_declares_portable_authority(self):
        state = self.initial()
        self.assertEqual("exakt-report-v2", state["schema_version"])
        self.assertEqual("local-self-attested", state["authority_mode"])
        self.assertEqual(".exakt/spec.md", state["spec"]["path"])
        self.assertFalse(self.state_module.legacy_state(state))
        self.state_module.validate_state(state)

    def test_open_milestone_can_have_no_closeout_yet(self):
        state = self.initial()
        state["milestones"] = [
            {
                "id": "M1",
                "title": "First vertical outcome",
                "status": "pending",
                "task_ids": [],
                "acceptance_criterion_ids": [],
                "closeout": None,
            }
        ]
        self.state_module.validate_state(state)

    def test_unknown_nested_fields_duplicate_ids_and_dangling_edges_fail(self):
        unknown = self.initial()
        unknown["clarity"]["intent"]["future"] = True
        with self.assertRaisesRegex(ValueError, "unknown field"):
            self.state_module.validate_state(unknown)

        duplicate = self.initial()
        duplicate["requirements"] = [
            {"id": "R1", "text": "one", "status": "pending"},
            {"id": "R1", "text": "two", "status": "pending"},
        ]
        with self.assertRaisesRegex(ValueError, "duplicate id.*R1"):
            self.state_module.validate_state(duplicate)

        dangling = self.initial()
        dangling["traceability"]["edges"] = [
            {"from": "missing", "to": "also-missing", "kind": "defines"}
        ]
        with self.assertRaisesRegex(ValueError, "dangling trace edge"):
            self.state_module.validate_state(dangling)

    def test_trace_edges_and_cross_references_are_type_checked(self):
        wrong_edge = self.initial()
        wrong_edge["requirements"] = [
            {"id": "R1", "text": "Requirement", "status": "pending"}
        ]
        wrong_edge["traceability"]["edges"] = [
            {"from": "R1", "to": "R1", "kind": "defines"}
        ]
        with self.assertRaisesRegex(ValueError, "invalid defines edge"):
            self.state_module.validate_state(wrong_edge)

        dangling_affect = self.initial()
        dangling_affect["clarity"]["ledger"] = [
            {
                "id": "CL1",
                "text": "An assumption",
                "status": "assumed",
                "source": "agent",
                "affects": ["missing"],
                "blocking": False,
            }
        ]
        with self.assertRaisesRegex(ValueError, "clarity entry CL1.*dangling"):
            self.state_module.validate_state(dangling_affect)

    def test_verified_executable_tasks_require_all_behavior_evidence_stages(self):
        stages = ("red", "green", "regression", "legitimacy", "falsification")
        state = self.complete_state("executable", stages)
        self.state_module.validate_state(state)
        self.assertEqual([], self.state_module.verification_gaps(state))

        for missing in stages:
            with self.subTest(missing=missing):
                partial = self.complete_state(
                    "executable", tuple(stage for stage in stages if stage != missing)
                )
                with self.assertRaisesRegex(ValueError, f"missing proof stage.*{missing}"):
                    self.state_module.validate_state(partial)

    def test_verified_non_executable_tasks_require_artifact_proof_stages(self):
        stages = ("before", "proof", "falsification")
        state = self.complete_state("non-executable", stages)
        self.state_module.validate_state(state)

        partial = self.complete_state("non-executable", ("before", "proof"))
        with self.assertRaisesRegex(ValueError, "missing proof stage.*falsification"):
            self.state_module.validate_state(partial)

    def test_changed_contract_digest_blocks_verification_without_becoming_malformed(self):
        state = self.complete_state(
            "executable", ("red", "green", "regression", "legitimacy", "falsification")
        )
        state["requirements"][0]["text"] = "Changed after proof"
        self.state_module.validate_state(state)
        self.assertTrue(
            any("contract digest is stale" in gap for gap in self.state_module.verification_gaps(state))
        )

    def test_red_must_fail_for_the_expected_reason_and_proof_binds_one_subject(self):
        stages = ("red", "green", "regression", "legitimacy", "falsification")
        state = self.complete_state("executable", stages)
        state["evidence"][0]["result"] = "passed"
        with self.assertRaisesRegex(ValueError, "RED evidence.*failed-as-expected"):
            self.state_module.validate_state(state)

        state = self.complete_state("executable", stages)
        state["evidence"][1]["subject_digest"] = "c" * 64
        with self.assertRaisesRegex(ValueError, "one subject digest"):
            self.state_module.validate_state(state)

    def test_blocking_unknown_stale_check_and_open_invalidation_block_handoff(self):
        stages = ("before", "proof", "falsification")
        state = self.complete_state("non-executable", stages)
        state["clarity"]["ledger"] = [{
            "id": "CL1", "text": "Which compatibility target?", "status": "unknown",
            "source": "user brief", "affects": ["R1"], "blocking": True,
        }]
        state["verification"][0]["freshness"] = "stale"
        state["verification"][0]["status"] = "stale"
        state["traceability"]["invalidations"] = [{
            "id": "X1", "changed_id": "R1", "affected_ids": ["T1", "E1"],
            "reason": "Requirement changed", "recorded_at": "2026-09-04T01:00:00Z",
            "status": "open", "resolved_at": None,
        }]
        state["tasks"][0]["status"] = "unverified"
        state["evidence"][0]["status"] = "stale"
        state["acceptance_criteria"][0]["status"] = "unverified"
        state["milestones"][0]["status"] = "unverified"
        state["milestones"][0]["closeout"]["status"] = "stale"
        state["status"] = "unverified"
        self.state_module.validate_state(state)
        gaps = "\n".join(self.state_module.verification_gaps(state))
        self.assertIn("blocking clarity", gaps)
        self.assertIn("stale verification", gaps)
        self.assertIn("open invalidation X1", gaps)

    def test_verified_milestone_cannot_hide_incomplete_tasks_or_criteria(self):
        state = self.complete_state(
            "non-executable", ("before", "proof", "falsification")
        )
        state["tasks"][0]["status"] = "pending"
        with self.assertRaisesRegex(ValueError, "verified milestone M1.*task T1"):
            self.state_module.validate_state(state)

        state = self.complete_state(
            "non-executable", ("before", "proof", "falsification")
        )
        state["acceptance_criteria"][0]["status"] = "pending"
        with self.assertRaisesRegex(ValueError, "verified milestone M1.*criterion AC1"):
            self.state_module.validate_state(state)

    def test_traceability_gaps_name_orphans_without_corrupting_drafts(self):
        state = self.initial()
        state["requirements"] = [
            {"id": "R1", "text": "Orphan requirement", "status": "pending"}
        ]
        validated = self.state_module.validate_state(state)
        self.assertIn(
            "requirement R1 has no defines edge",
            self.state_module.traceability_gaps(validated),
        )

    def test_every_trace_node_needs_both_sides_of_its_chain(self):
        state = self.complete_state(
            "non-executable", ("before", "proof", "falsification")
        )
        state["primitives"]["behaviors"].append(
            {"id": "B2", "text": "Unscoped behavior", "status": "verified"}
        )
        state["traceability"]["edges"].extend(
            [
                {"from": "B2", "to": "AC1", "kind": "accepted_by"},
                {"from": "B2", "to": "INV1", "kind": "protects"},
            ]
        )
        self.assertIn(
            "behavior B2 has no incoming defines edge",
            self.state_module.traceability_gaps(state),
        )

    def test_claim_and_closeout_evidence_obey_authority_and_task_binding(self):
        state = self.complete_state(
            "non-executable", ("before", "proof", "falsification")
        )
        state["authority_mode"] = "external-journal"
        rogue = {
            "id": "E99", "name": "Rogue proof", "type": "command", "stage": "proof",
            "provenance": "self-attested", "subject_digest": "c" * 64,
            "contract_digest": "d" * 64, "status": "verified", "result": "passed",
            "command": "true", "detail": "Not independently bound to the work",
        }
        state["evidence"].append(rogue)
        state["acceptance_criteria"][0]["evidence_ids"] = ["E99"]
        state["verification"][0]["evidence_ids"] = ["E99"]
        state["milestones"][0]["closeout"]["evidence_ids"] = ["E99"]
        gaps = "\n".join(self.state_module.verification_gaps(state))
        self.assertIn("external-journal authority", gaps)
        self.assertIn("not bound to a task", gaps)

    def test_v1_remains_readable_but_is_explicitly_legacy(self):
        state = legacy_v1_state()
        self.assertIs(state, self.state_module.validate_state(state))
        self.assertTrue(self.state_module.legacy_state(state))
        self.assertTrue(self.state_module.verification_gaps(state))

    def test_v1_migration_preserves_ids_and_legacy_observation_detail(self):
        state = legacy_v1_state()
        state["requirements"] = [
            {"id": "REQ-old", "text": "Preserve me", "status": "verified"}
        ]
        state["verification"] = [
            {"id": "CHECK-old", "name": "Old check", "status": "verified", "evidence": "exact old output"}
        ]
        migrated = self.state_module.migrate_v1_state(
            state, now="2026-09-04T01:00:00Z"
        )
        self.assertEqual("REQ-old", migrated["requirements"][0]["id"])
        self.assertEqual("CHECK-old", migrated["verification"][0]["id"])
        self.assertIn("exact old output", migrated["verification"][0]["detail"])
        self.assertEqual("unverified", migrated["verification"][0]["status"])


if __name__ == "__main__":
    unittest.main()
