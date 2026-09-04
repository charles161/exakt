import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = PACKAGE_ROOT / "skills/forge/scripts/install.py"


class InstallerTests(unittest.TestCase):
    def run_installer(self, *args):
        return subprocess.run(
            [sys.executable, str(INSTALLER), *map(str, args)],
            capture_output=True,
            text=True,
            timeout=20,
        )

    def test_codex_and_claude_clean_installs_have_truthful_invocations(self):
        for host, invocation in (("codex", "$forge <task>"), ("claude", "/forge <task>")):
            with self.subTest(host=host):
                with tempfile.TemporaryDirectory(prefix=f"forge-{host}-") as temp_dir:
                    root = Path(temp_dir)
                    result = self.run_installer("--host", host, "--root", root)
                    self.assertEqual(0, result.returncode, result.stderr)
                    skill = root / "skills" / "forge"
                    self.assertTrue((skill / "SKILL.md").is_file())
                    self.assertTrue((skill / "scripts" / "forge.py").is_file())
                    self.assertIn(invocation, result.stdout)
                    if host == "claude":
                        command = root / "commands" / "forge.md"
                        self.assertTrue(command.is_file())
                        self.assertIn(str(skill / "SKILL.md"), command.read_text())

    def test_existing_destination_is_never_overwritten(self):
        with tempfile.TemporaryDirectory(prefix="forge-collision-") as temp_dir:
            root = Path(temp_dir)
            destination = root / "skills" / "forge"
            destination.mkdir(parents=True)
            marker = destination / "user-file.txt"
            marker.write_text("keep", encoding="utf-8")
            result = self.run_installer("--host", "codex", "--root", root)
            self.assertNotEqual(0, result.returncode)
            self.assertEqual("keep", marker.read_text(encoding="utf-8"))

    def test_dry_run_changes_nothing(self):
        with tempfile.TemporaryDirectory(prefix="forge-dry-run-") as temp_dir:
            root = Path(temp_dir)
            result = self.run_installer(
                "--host", "generic", "--root", root, "--dry-run"
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertFalse((root / "forge").exists())
            self.assertIn("WOULD INSTALL", result.stdout)


if __name__ == "__main__":
    unittest.main()
