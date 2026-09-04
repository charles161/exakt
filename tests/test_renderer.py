import copy
import html as html_module
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
RENDERER_PATH = PACKAGE_ROOT / "skills/exakt/scripts/render_report.py"
TEMPLATE_PATH = PACKAGE_ROOT / "skills/exakt/assets/report-template.html"
FIXTURE_PATH = PACKAGE_ROOT / "tests/fixtures/report-state.json"
V2_FIXTURE_PATH = PACKAGE_ROOT / "tests/fixtures/report-state-v2.json"


class RendererTests(unittest.TestCase):
    def load_renderer(self):
        self.assertTrue(RENDERER_PATH.is_file(), "renderer implementation is missing")
        spec = importlib.util.spec_from_file_location("exakt_report_renderer", RENDERER_PATH)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    def fixture(self):
        return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def v2_fixture(self):
        return json.loads(V2_FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_renderer_files_and_public_api_are_deterministic(self):
        renderer = self.load_renderer()
        self.assertTrue(TEMPLATE_PATH.is_file())
        state = self.fixture()
        first = renderer.render_report(state)
        second = renderer.render_report(copy.deepcopy(state))
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("<!doctype html>"))
        self.assertIn("Payment recovery studio", first)
        self.assertIn("2026-09-04T09:30:00Z", first)

        with tempfile.TemporaryDirectory(prefix="exakt-renderer-cli-") as temp_dir:
            output = Path(temp_dir) / "report.html"
            result = subprocess.run(
                [sys.executable, str(RENDERER_PATH), str(FIXTURE_PATH), "--output", str(output)],
                capture_output=True,
                text=True,
                timeout=15,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(first, output.read_text(encoding="utf-8"))
            self.assertIn(str(output), result.stdout)

    def test_seven_semantic_views_are_present_and_useful(self):
        html = self.load_renderer().render_report(self.fixture())
        expected = {
            "spec": "Brief & spec",
            "architecture": "Architecture",
            "plan": "Acceptance & plan",
            "decisions": "Critique & decisions",
            "progress": "Progress",
            "truth": "Verification & truth",
            "evidence": "Files & evidence",
        }
        self.assertEqual(7, len(re.findall(r'<section\b[^>]*data-report-view=', html)))
        for section_id, heading in expected.items():
            self.assertRegex(html, rf'<section\b[^>]*id="{section_id}"[^>]*data-report-view=')
            self.assertIn(html_module.escape(heading), html)
            self.assertIn(f'href="#{section_id}"', html)
        for project_text in (
            "Persist intent before provider I/O.",
            "Hash-chained source of truth",
            "Exercise provider reconciliation",
            "A timeout is not evidence",
            "Rollback drill has not been observed",
            "Crash-window transcript",
        ):
            self.assertIn(project_text, html)

    def test_project_text_is_escaped_and_unknown_fields_are_ignored(self):
        renderer = self.load_renderer()
        state = self.fixture()
        attack = '</script><script data-owned="yes">alert(1)</script><img src=x onerror=alert(2)>'
        state["title"] = attack
        state["summary"] = attack
        state["brief"]["outcome"] = attack
        state["requirements"][0]["text"] = attack
        state["files"][0]["path"] = 'javascript:alert(3)" autofocus onfocus="alert(4)'
        state["unknown_optional"] = attack
        state["architecture"]["unknown_optional"] = attack
        html = renderer.render_report(state)
        self.assertNotIn('<script data-owned="yes">', html)
        self.assertNotIn("<img src=x", html)
        self.assertNotIn('href="javascript:', html)
        self.assertNotIn("unknown_optional", html)
        self.assertIn("&lt;/script&gt;&lt;script", html)
        self.assertIn("javascript:alert(3)&quot; autofocus", html)
        self.assertEqual(1, len(re.findall(r"<script\b", html)))

    def test_report_has_no_remote_dependencies_and_feedback_stays_local(self):
        html = self.load_renderer().render_report(self.fixture())
        self.assertNotRegex(html, r"(?i)<(?:script|link|img)[^>]+(?:src|href)=[\"']https?://")
        self.assertNotRegex(html, r"(?i)@import\s")
        self.assertNotIn("fetch(", html)
        self.assertNotIn("XMLHttpRequest", html)
        self.assertIn('id="feedback-text"', html)
        self.assertIn('id="copy-feedback"', html)
        self.assertIn('id="download-feedback"', html)
        self.assertIn("navigator.clipboard.writeText", html)
        self.assertIn("new Blob", html)
        self.assertIn('download="exakt-feedback.json"', html)
        self.assertIn("Draft only", html)

    def test_statuses_are_not_color_only_and_truth_risks_sort_first(self):
        html = self.load_renderer().render_report(self.fixture())
        for status in ("partial", "verified", "blocked", "stale", "contradicted", "unverified"):
            self.assertRegex(
                html,
                rf'data-status="{status}"[^>]*>\s*<span[^>]*aria-hidden="true"[^>]*>[^<]+</span>\s*{status.replace("_", " ").title()}',
            )
        truth = html.split('<section id="truth"', 1)[1].split(
            '<section id="evidence"', 1
        )[0]
        contradicted = truth.index("Timeout behavior")
        stale = truth.index("Provider sandbox receipt")
        verified = truth.index("State-machine unit suite")
        self.assertLess(contradicted, stale)
        self.assertLess(stale, verified)
        self.assertIn('role="alert"', html)

    def test_mobile_accessibility_and_progressive_disclosure_contract(self):
        html = self.load_renderer().render_report(self.fixture())
        self.assertIn('<meta name="viewport" content="width=device-width, initial-scale=1">', html)
        self.assertIn('@media (max-width: 720px)', html)
        self.assertIn('@media (prefers-reduced-motion: reduce)', html)
        self.assertIn("overflow-wrap: anywhere", html)
        self.assertIn('class="skip-link" href="#report-main"', html)
        self.assertIn('<main id="report-main"', html)
        self.assertIn(":focus-visible", html)
        self.assertGreaterEqual(html.count("<details"), 7)
        self.assertEqual(html.count("<details"), html.count("<summary"))
        self.assertIn('<label for="feedback-text">', html)
        self.assertIn('<button type="button" id="copy-feedback"', html)
        self.assertIn('<button type="button" id="download-feedback"', html)
        self.assertIn('aria-live="polite"', html)

    def test_v2_surfaces_authority_clarity_primitives_and_trace(self):
        html = self.load_renderer().render_report(self.v2_fixture())
        self.assertIn('data-schema-version="exakt-report-v2"', html)
        self.assertIn('data-authority-mode="local-self-attested"', html)
        self.assertIn("Local self-attested", html)
        self.assertIn("This report does not claim independent verification.", html)
        for project_text in (
            "Known",
            "Conflicted",
            "Make chapter navigation URL-shareable.",
            "Confidence · High",
            "The existing router and history boundary were inspected.",
            "Chapter slugs are stable.",
            "Legacy hash URLs need a redirect decision.",
            "Blocking",
            "Opening a copied URL selects its chapter.",
            "Browser Back reverses chapter changes.",
            "Drive a real browser history sequence.",
            "Back leaves the reader on the new chapter.",
            "Invariants",
            "Counterexamples",
            "Orphan trace",
        ):
            self.assertIn(project_text, html)
        for edge in (
            ("R1", "defines", "B1"),
            ("O1", "challenged_by", "C1"),
            ("T1", "proved_by", "E1"),
        ):
            self.assertIn(
                f'data-trace-kind="{edge[1]}">{edge[0]} <span aria-hidden="true">→</span> {edge[1]} <span aria-hidden="true">→</span> {edge[2]}',
                html,
            )
        self.assertIn("A legacy redirect decision changed the contract.", html)

    def test_v2_milestones_and_evidence_reveal_scope_provenance_and_commit(self):
        html = self.load_renderer().render_report(self.v2_fixture())
        for project_text in (
            "Shareable navigation",
            "Milestone M1",
            "RED observed",
            "Self-attested",
            "Direct links and Back navigation were exercised.",
            "R1, B1, INV1, AC1",
            "src/chapter-navigation.ts",
            "Committed",
            "1234abc",
            "feat: make chapter URLs shareable",
            "Falsification",
            "Separated",
            "python -m pytest tests/test_navigation.py",
            "Contract digest",
            "Subject digest",
            "Spec revision",
            ".exakt/spec.md",
        ):
            self.assertIn(project_text, html)
        self.assertIn('data-proof-provenance="separated"', html)
        self.assertIn('data-milestone-id="M1"', html)

    def test_v1_is_visibly_labeled_as_a_legacy_contract(self):
        rendered = self.load_renderer().render_report(self.fixture())
        self.assertIn("Legacy v1 contract", rendered)
        self.assertIn("V2 traceability and milestone guarantees are unavailable.", rendered)

    def test_v2_project_text_remains_escaped_in_new_views(self):
        state = self.v2_fixture()
        attack = '<img src=x onerror="alert(1)">'
        state["clarity"]["ledger"][0]["text"] = attack
        state["traceability"]["invalidations"][0]["reason"] = attack
        state["milestones"][0]["closeout"]["completed"] = attack
        rendered = self.load_renderer().render_report(state)
        self.assertNotIn("<img src=x", rendered)
        self.assertGreaterEqual(rendered.count("&lt;img src=x"), 3)

    def test_v2_does_not_invent_statuses_for_unstatused_records(self):
        rendered = self.load_renderer().render_report(self.v2_fixture())
        component_summary = rendered.split("Chapter route adapter", 1)[1].split(
            "</summary>", 1
        )[0]
        file_summary = rendered.split("src/chapter-navigation.ts", 1)[1].split(
            "</summary>", 1
        )[0]
        self.assertNotIn("Unverified", component_summary)
        self.assertNotIn("Unverified", file_summary)

    def test_cli_rejects_malformed_or_non_object_json_without_output(self):
        self.load_renderer()
        with tempfile.TemporaryDirectory(prefix="exakt-renderer-invalid-") as temp_dir:
            root = Path(temp_dir)
            output = root / "report.html"
            for name, payload in (("broken.json", "{"), ("array.json", "[]")):
                source = root / name
                source.write_text(payload, encoding="utf-8")
                result = subprocess.run(
                    [sys.executable, str(RENDERER_PATH), str(source), "--output", str(output)],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                self.assertNotEqual(0, result.returncode)
                self.assertFalse(output.exists())
                self.assertIn("error", result.stderr.lower())

    def test_cli_replaces_output_atomically_and_preserves_it_on_replace_failure(self):
        renderer = self.load_renderer()
        with tempfile.TemporaryDirectory(prefix="exakt-renderer-atomic-") as temp_dir:
            root = Path(temp_dir)
            output = root / "report.html"
            output.write_text("existing-report", encoding="utf-8")
            with mock.patch("os.replace", side_effect=OSError("simulated replace failure")):
                result = renderer.main(
                    [str(V2_FIXTURE_PATH), "--output", str(output)]
                )
            self.assertEqual(2, result)
            self.assertEqual("existing-report", output.read_text(encoding="utf-8"))
            self.assertEqual([output], list(root.iterdir()))


if __name__ == "__main__":
    unittest.main()
