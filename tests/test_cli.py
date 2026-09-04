import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
CLI = PACKAGE_ROOT / "skills/exakt/scripts/exakt.py"


class ExaktCliTests(unittest.TestCase):
    def run_cli(self, *args, cwd=None):
        return subprocess.run(
            [sys.executable, str(CLI), *map(str, args)],
            cwd=cwd or PACKAGE_ROOT,
            capture_output=True,
            text=True,
            timeout=15,
        )

    def test_init_creates_a_clean_task_workspace_and_terminal_summary(self):
        with tempfile.TemporaryDirectory(prefix="exakt-cli-") as temp_dir:
            output = Path(temp_dir) / "state.json"
            result = self.run_cli(
                "init",
                "Make chapter navigation URL-shareable and accessible",
                "--mode",
                "task",
                "--output",
                output,
                "--no-render",
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("EXAKT", result.stdout)
            self.assertIn("TASK", result.stdout)
            self.assertIn(str(output), result.stdout)
            state = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual("exakt-report-v2", state["schema_version"])
            self.assertEqual("local-self-attested", state["authority_mode"])
            self.assertEqual("task", state["mode"])
            self.assertEqual("Make chapter navigation URL-shareable and accessible", state["brief"]["outcome"])
            self.assertEqual("intake", state["phase"])
            self.assertEqual("draft", state["status"])

    def test_default_workspace_uses_the_canonical_report_filename(self):
        with tempfile.TemporaryDirectory(prefix="exakt-cli-default-") as temp_dir:
            root = Path(temp_dir)
            result = self.run_cli(
                "init",
                "Verify the canonical Exakt workspace",
                "--mode",
                "task",
                cwd=root,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertTrue((root / ".exakt" / "exakt-state.json").is_file())
            self.assertTrue((root / ".exakt" / "spec.md").is_file())
            self.assertTrue((root / ".exakt" / "exakt-report.html").is_file())
            self.assertFalse((root / ".exakt" / "exakt-state.html").exists())
            self.assertIn(str(root / ".exakt" / "exakt-report.html"), result.stdout)
            self.assertIn(str(root / ".exakt" / "spec.md"), result.stdout)

    def test_spec_command_refreshes_projection_and_persists_digest(self):
        with tempfile.TemporaryDirectory(prefix="exakt-cli-spec-") as temp_dir:
            root = Path(temp_dir)
            state_path = root / "state.json"
            initialized = self.run_cli(
                "init", "Fix retry behavior", "--output", state_path, "--no-render"
            )
            self.assertEqual(0, initialized.returncode, initialized.stderr)
            spec_path = root / "spec.md"
            self.assertTrue(spec_path.is_file())

            collision = self.run_cli("spec", state_path)
            self.assertNotEqual(0, collision.returncode)
            self.assertIn("overwrite", collision.stderr.lower())

            refreshed = self.run_cli("spec", state_path, "--force")
            self.assertEqual(0, refreshed.returncode, refreshed.stderr)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertRegex(state["spec"]["digest"], r"^[0-9a-f]{64}$")
            self.assertIn(state["spec"]["digest"], spec_path.read_text(encoding="utf-8"))

    def test_init_refuses_silent_overwrite_and_product_mode_is_explicit(self):
        with tempfile.TemporaryDirectory(prefix="exakt-cli-overwrite-") as temp_dir:
            output = Path(temp_dir) / "state.json"
            first = self.run_cli(
                "init", "Build a complete learning platform", "--mode", "product", "--output", output, "--no-render"
            )
            self.assertEqual(0, first.returncode, first.stderr)
            second = self.run_cli(
                "init", "Replace it", "--mode", "task", "--output", output, "--no-render"
            )
            self.assertNotEqual(0, second.returncode)
            state = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual("product", state["mode"])
            self.assertEqual("Build a complete learning platform", state["brief"]["outcome"])

    def test_status_and_verify_never_call_draft_work_complete(self):
        with tempfile.TemporaryDirectory(prefix="exakt-cli-verify-") as temp_dir:
            output = Path(temp_dir) / "state.json"
            self.assertEqual(
                0,
                self.run_cli(
                    "init", "Fix the bug", "--output", output, "--no-render"
                ).returncode,
            )
            status = self.run_cli("status", output)
            self.assertEqual(0, status.returncode, status.stderr)
            self.assertIn("DRAFT", status.stdout)
            self.assertIn("no verified evidence recorded", status.stdout)
            verify = self.run_cli("verify", output)
            self.assertEqual(2, verify.returncode)
            self.assertIn("NOT VERIFIED", verify.stdout)

            state = json.loads(output.read_text(encoding="utf-8"))
            state["status"] = "verified"
            state["phase"] = "handoff"
            state["acceptance_criteria"] = [
                {
                    "id": "AC-1",
                    "text": "Original symptom is gone",
                    "status": "verified",
                    "evidence_ids": [],
                }
            ]
            state["verification"] = [
                {
                    "id": "V-1",
                    "name": "regression",
                    "status": "verified",
                    "evidence_ids": [],
                    "proof_type": "behavior",
                    "freshness": "fresh",
                    "counterexample": "",
                }
            ]
            output.write_text(json.dumps(state), encoding="utf-8")
            verify = self.run_cli("verify", output)
            self.assertEqual(2, verify.returncode, verify.stdout + verify.stderr)
            self.assertIn("no milestones", verify.stdout.lower())

    def test_malformed_or_false_complete_state_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="exakt-cli-invalid-") as temp_dir:
            malformed = Path(temp_dir) / "bad.json"
            malformed.write_text("{}", encoding="utf-8")
            self.assertNotEqual(0, self.run_cli("status", malformed).returncode)

            false_complete = Path(temp_dir) / "false.json"
            false_complete.write_text(
                json.dumps(
                    {
                        "schema_version": "exakt-report-v1",
                        "title": "False claim",
                        "mode": "task",
                        "summary": "",
                        "status": "verified",
                        "phase": "handoff",
                        "updated_at": "2026-09-04T00:00:00Z",
                        "brief": {"outcome": "x", "users": [], "constraints": []},
                        "requirements": [],
                        "architecture": {"overview": "", "components": [], "decisions": []},
                        "acceptance_criteria": [{"id": "AC-1", "text": "works", "status": "pending"}],
                        "tasks": [],
                        "critiques": [],
                        "decisions": [],
                        "verification": [],
                        "files": [],
                        "evidence": [],
                        "gaps": [],
                    }
                ),
                encoding="utf-8",
            )
            result = self.run_cli("verify", false_complete)
            self.assertEqual(2, result.returncode)
            self.assertIn("pending acceptance criteria", result.stdout.lower())

    def test_v1_status_is_legacy_and_migration_is_explicit_and_non_destructive(self):
        with tempfile.TemporaryDirectory(prefix="exakt-cli-migrate-") as temp_dir:
            root = Path(temp_dir)
            source = root / "legacy.json"
            destination = root / "migrated.json"
            source.write_text(
                json.dumps(
                    {
                        "schema_version": "exakt-report-v1",
                        "title": "Legacy",
                        "mode": "task",
                        "summary": "Old state",
                        "status": "verified",
                        "phase": "handoff",
                        "updated_at": "2026-09-04T00:00:00Z",
                        "brief": {"outcome": "Preserve this", "users": [], "constraints": []},
                        "requirements": [{"id": "R1", "text": "Old requirement", "status": "verified"}],
                        "architecture": {"overview": "Old design", "components": [], "decisions": []},
                        "acceptance_criteria": [{"id": "AC1", "text": "Old proof", "status": "verified"}],
                        "tasks": [{"id": "T1", "title": "Old task", "status": "done", "depends_on": []}],
                        "critiques": [],
                        "decisions": [],
                        "verification": [{"name": "old check", "status": "verified", "evidence": "old run"}],
                        "files": [],
                        "evidence": [],
                        "gaps": [],
                    }
                ),
                encoding="utf-8",
            )

            status = self.run_cli("status", source)
            self.assertEqual(0, status.returncode, status.stderr)
            self.assertIn("V1 LEGACY", status.stdout)

            in_place = self.run_cli("migrate", source, "--output", source)
            self.assertNotEqual(0, in_place.returncode)
            self.assertIn("in-place", in_place.stderr.lower())

            migrated = self.run_cli("migrate", source, "--output", destination)
            self.assertEqual(0, migrated.returncode, migrated.stderr)
            state = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual("exakt-report-v2", state["schema_version"])
            self.assertEqual("unverified", state["status"])
            self.assertTrue(state["gaps"])
            self.assertEqual("unverified", state["requirements"][0]["status"])
            self.assertEqual("Old task", state["tasks"][0]["title"])
            self.assertEqual("pending", state["tasks"][0]["status"])
            self.assertEqual(["T1"], state["milestones"][0]["task_ids"])
            self.assertTrue(
                all(item["status"] == "unverified" for item in state["verification"])
            )

            collision = self.run_cli("migrate", source, "--output", destination)
            self.assertNotEqual(0, collision.returncode)
            self.assertIn("overwrite", collision.stderr.lower())


if __name__ == "__main__":
    unittest.main()
