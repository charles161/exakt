import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
CLI = PACKAGE_ROOT / "skills/forge/scripts/forge.py"


class ForgeCliTests(unittest.TestCase):
    def run_cli(self, *args, cwd=None):
        return subprocess.run(
            [sys.executable, str(CLI), *map(str, args)],
            cwd=cwd or PACKAGE_ROOT,
            capture_output=True,
            text=True,
            timeout=15,
        )

    def test_init_creates_a_clean_task_workspace_and_terminal_summary(self):
        with tempfile.TemporaryDirectory(prefix="forge-cli-") as temp_dir:
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
            self.assertIn("FORGE", result.stdout)
            self.assertIn("TASK", result.stdout)
            self.assertIn(str(output), result.stdout)
            state = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual("forge-report-v1", state["schema_version"])
            self.assertEqual("task", state["mode"])
            self.assertEqual("Make chapter navigation URL-shareable and accessible", state["brief"]["outcome"])
            self.assertEqual("intake", state["phase"])
            self.assertEqual("draft", state["status"])

    def test_init_refuses_silent_overwrite_and_product_mode_is_explicit(self):
        with tempfile.TemporaryDirectory(prefix="forge-cli-overwrite-") as temp_dir:
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
        with tempfile.TemporaryDirectory(prefix="forge-cli-verify-") as temp_dir:
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
            verify = self.run_cli("verify", output)
            self.assertEqual(2, verify.returncode)
            self.assertIn("NOT VERIFIED", verify.stdout)

            state = json.loads(output.read_text(encoding="utf-8"))
            state["status"] = "verified"
            state["phase"] = "handoff"
            state["acceptance_criteria"] = [
                {"id": "AC-1", "text": "Original symptom is gone", "status": "verified"}
            ]
            state["verification"] = [
                {"name": "regression", "status": "verified", "evidence": "test passed against current source"}
            ]
            output.write_text(json.dumps(state), encoding="utf-8")
            verify = self.run_cli("verify", output)
            self.assertEqual(0, verify.returncode, verify.stdout + verify.stderr)
            self.assertIn("VERIFIED", verify.stdout)

    def test_malformed_or_false_complete_state_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="forge-cli-invalid-") as temp_dir:
            malformed = Path(temp_dir) / "bad.json"
            malformed.write_text("{}", encoding="utf-8")
            self.assertNotEqual(0, self.run_cli("status", malformed).returncode)

            false_complete = Path(temp_dir) / "false.json"
            false_complete.write_text(
                json.dumps(
                    {
                        "schema_version": "forge-report-v1",
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


if __name__ == "__main__":
    unittest.main()
