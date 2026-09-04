import copy
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PACKAGE_ROOT / "skills/exakt/scripts"
REPORT_STATE_PATH = SCRIPTS_ROOT / "report_state.py"
RENDERER_PATH = SCRIPTS_ROOT / "render_spec.py"


def load_module(name, path):
    if not path.is_file():
        raise AssertionError(f"missing module: {path}")
    sys.path.insert(0, str(SCRIPTS_ROOT))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise AssertionError(f"cannot import module: {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


class SpecRendererTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state_module = load_module("exakt_spec_test_state", REPORT_STATE_PATH)
        cls.renderer = load_module("exakt_spec_renderer", RENDERER_PATH)

    def initial(self):
        return self.state_module.initial_state(
            "Make chapter navigation URL-shareable",
            "task",
            now="2026-09-04T00:00:00Z",
        )

    def test_identical_state_produces_byte_identical_markdown_and_digest(self):
        state = self.initial()
        first = self.renderer.render_spec(copy.deepcopy(state))
        second = self.renderer.render_spec(copy.deepcopy(state))
        self.assertEqual(first.encode("utf-8"), second.encode("utf-8"))
        self.assertEqual(
            self.renderer.contract_digest(state),
            self.renderer.contract_digest(copy.deepcopy(state)),
        )
        self.assertTrue(first.endswith("\n"))

    def test_contract_change_changes_digest_but_progress_only_change_does_not(self):
        state = self.initial()
        baseline = self.renderer.contract_digest(state)

        progress = copy.deepcopy(state)
        progress["summary"] = "Implementation is underway."
        progress["status"] = "active"
        progress["phase"] = "execute"
        progress["updated_at"] = "2026-09-04T01:00:00Z"
        self.assertEqual(baseline, self.renderer.contract_digest(progress))

        changed = copy.deepcopy(state)
        changed["requirements"] = [
            {"id": "R1", "text": "Back restores the previous chapter", "status": "pending"}
        ]
        self.assertNotEqual(baseline, self.renderer.contract_digest(changed))

    def test_untrusted_multiline_text_cannot_create_markdown_structure(self):
        state = self.initial()
        state["title"] = "Safe title\n# Injected heading"
        state["brief"]["outcome"] = "Keep state\n- [ ] injected task\n<script>alert(1)</script>"
        markdown = self.renderer.render_spec(state)
        self.assertNotIn("\n# Injected heading", markdown)
        self.assertNotIn("\n- [ ] injected task", markdown)
        self.assertNotIn("<script>", markdown)
        self.assertIn("&lt;script&gt;", markdown)

        state = self.initial()
        state["architecture"]["overview"] = "- injected architecture item"
        markdown = self.renderer.render_spec(state)
        self.assertNotIn("\n- injected architecture item", markdown)

    def test_force_refresh_invalidates_proof_after_contract_change(self):
        with tempfile.TemporaryDirectory(prefix="exakt-stale-spec-") as temp_dir:
            output = Path(temp_dir) / "spec.md"
            state = self.initial()
            self.renderer.write_spec(output, state)
            old_digest = state["spec"]["digest"]
            state["requirements"] = [
                {"id": "R1", "text": "Changed contract", "status": "pending"}
            ]
            new_digest = self.renderer.write_spec(output, state, force=True)
            self.assertNotEqual(old_digest, new_digest)
            self.assertEqual(new_digest, state["spec"]["digest"])
            self.assertIn("Changed contract", output.read_text(encoding="utf-8"))

    def test_simple_task_spec_stays_below_eighty_non_blank_lines(self):
        markdown = self.renderer.render_spec(self.initial())
        non_blank = [line for line in markdown.splitlines() if line.strip()]
        self.assertLess(len(non_blank), 80)
        self.assertIn("## Intent", markdown)
        self.assertIn("## Milestones and tasks", markdown)

    def test_write_spec_is_atomic_refuses_overwrite_and_returns_digest(self):
        with tempfile.TemporaryDirectory(prefix="exakt-spec-") as temp_dir:
            output = Path(temp_dir) / "spec.md"
            state = self.initial()
            digest = self.renderer.write_spec(output, state)
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
            self.assertEqual(digest, state["spec"]["digest"])
            self.assertEqual(self.renderer.render_spec(state), output.read_text(encoding="utf-8"))
            with self.assertRaisesRegex(ValueError, "overwrite"):
                self.renderer.write_spec(output, state)
            self.assertEqual(digest, self.renderer.write_spec(output, state, force=True))
            self.assertFalse(list(output.parent.glob(".spec.md.tmp-*")))


if __name__ == "__main__":
    unittest.main()
