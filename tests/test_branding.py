import json
import unittest
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]


class ExaktBrandContractTests(unittest.TestCase):
    def test_package_exposes_only_the_exakt_skill_and_commands(self):
        required = (
            "skills/exakt/SKILL.md",
            "skills/exakt/scripts/exakt.py",
            ".claude/commands/exakt.md",
            "commands/exakt.toml",
        )
        removed = (
            "skills/forge",
            ".claude/commands/forge.md",
            "commands/forge.toml",
        )

        for relative_path in required:
            self.assertTrue(
                (PACKAGE_ROOT / relative_path).is_file(),
                f"missing Exakt package file: {relative_path}",
            )
        for relative_path in removed:
            self.assertFalse(
                (PACKAGE_ROOT / relative_path).exists(),
                f"stale Forge package path: {relative_path}",
            )

        skill = (PACKAGE_ROOT / "skills/exakt/SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("name: exakt", skill)
        self.assertIn("$exakt", skill)
        self.assertIn("/exakt", skill)

    def test_manifests_and_readme_publish_the_exakt_surface(self):
        codex = json.loads(
            (PACKAGE_ROOT / ".codex-plugin/plugin.json").read_text(
                encoding="utf-8"
            )
        )
        claude = json.loads(
            (PACKAGE_ROOT / ".claude-plugin/plugin.json").read_text(
                encoding="utf-8"
            )
        )
        readme = (PACKAGE_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertEqual("exakt", codex["name"])
        self.assertEqual("exakt", claude["name"])
        self.assertIn("From intent to evidence.", readme)
        self.assertIn("npx skills add charles161/exakt", readme)
        self.assertIn(
            "npx skills remove forge -g -y", readme
        )
        self.assertNotIn("charles161/forge-skill", readme)

    def test_machine_contract_uses_exakt_identifiers_and_state_paths(self):
        controller_path = PACKAGE_ROOT / "skills/exakt/scripts/exakt.py"
        state_store_path = PACKAGE_ROOT / "skills/exakt/scripts/state_store.py"
        schemas_path = PACKAGE_ROOT / "skills/exakt/schemas"
        self.assertTrue(controller_path.is_file(), "missing Exakt controller")
        self.assertTrue(state_store_path.is_file(), "missing Exakt state store")
        self.assertTrue(schemas_path.is_dir(), "missing Exakt schemas")

        controller = controller_path.read_text(encoding="utf-8")
        state_store = state_store_path.read_text(encoding="utf-8")
        schema_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(schemas_path.glob("*.json"))
        )

        self.assertIn('REPORT_VERSION = "exakt-report-v1"', controller)
        self.assertIn('.exakt/exakt-state.json', controller)
        self.assertIn('"exakt-canonical-json-v1"', state_store)
        self.assertIn("urn:exakt:schema:", schema_text)
        self.assertNotIn("urn:forge:schema:", schema_text)


if __name__ == "__main__":
    unittest.main()
