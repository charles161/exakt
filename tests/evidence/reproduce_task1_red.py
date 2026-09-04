#!/usr/bin/env python3
"""Reconstruct and verify Task 1's pre-scaffold structural-test red state."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path


BASE_COMMIT = "a9961fb35d27dbf55ad2a6ce398e7286ebca536b"
SCAFFOLD_COMMIT = "1c6c38caba843de9b95404aa892c6687cf7c9b50"
TEST_PATH = "projects/forge-skill/tests/test_package_structure.py"
TEST_BLOB = "6e9d71729ca10548a92c009f4459ff844edcdbd8"
INIT_PATH = "projects/forge-skill/tests/__init__.py"
INIT_BLOB = "6f530f41c0c63bb9e2a4cee80b84b3a672dcc43c"
ABSENT_PACKAGE_PATHS = (
    "projects/forge-skill/README.md",
    "projects/forge-skill/LICENSE",
    "projects/forge-skill/.codex-plugin/plugin.json",
    "projects/forge-skill/.claude-plugin/plugin.json",
    "projects/forge-skill/.claude/commands/forge.md",
    "projects/forge-skill/commands/forge.toml",
)


def git(repo_root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ("git", "--no-replace-objects", "-C", str(repo_root), *args),
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def require_git_object(repo_root: Path, revision: str, expected: str) -> None:
    actual = git(repo_root, "rev-parse", revision).stdout.decode().strip()
    if actual != expected:
        raise RuntimeError(f"{revision} resolved to {actual}, expected {expected}")


def main() -> int:
    repo_root = Path(
        git(Path.cwd(), "rev-parse", "--show-toplevel").stdout.decode().strip()
    )
    require_git_object(repo_root, f"{BASE_COMMIT}^{{commit}}", BASE_COMMIT)
    require_git_object(repo_root, f"{SCAFFOLD_COMMIT}^{{commit}}", SCAFFOLD_COMMIT)
    require_git_object(repo_root, f"{SCAFFOLD_COMMIT}:{TEST_PATH}", TEST_BLOB)
    require_git_object(repo_root, f"{SCAFFOLD_COMMIT}:{INIT_PATH}", INIT_BLOB)

    unexpectedly_present = []
    for path in ABSENT_PACKAGE_PATHS:
        result = git(repo_root, "cat-file", "-e", f"{BASE_COMMIT}:{path}", check=False)
        if result.returncode == 0:
            unexpectedly_present.append(path)
    if unexpectedly_present:
        raise RuntimeError(
            f"base commit unexpectedly contains package files: {unexpectedly_present}"
        )

    with tempfile.TemporaryDirectory(prefix="forge-task1-red-") as temp_dir:
        package_root = Path(temp_dir)
        tests_dir = package_root / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_package_structure.py").write_bytes(
            git(repo_root, "cat-file", "blob", TEST_BLOB).stdout
        )
        (tests_dir / "__init__.py").write_bytes(
            git(repo_root, "cat-file", "blob", INIT_BLOB).stdout
        )

        command = (sys.executable, "-m", "unittest", "tests.test_package_structure", "-v")
        child = subprocess.run(
            command,
            cwd=package_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        transcript = child.stdout.replace(str(package_root), "<TEMP_PACKAGE_ROOT>")

    expected_markers = (
        "Ran 5 tests",
        "FAILED (failures=1, errors=5)",
        "missing required plugin files",
    )
    if child.returncode != 1 or any(marker not in transcript for marker in expected_markers):
        print(transcript, end="")
        print(f"observed child exit: {child.returncode}")
        print("red-state signature did not match", file=sys.stderr)
        return 1
    for path in ABSENT_PACKAGE_PATHS:
        relative_path = path.removeprefix("projects/forge-skill/")
        if relative_path not in transcript:
            print(f"red-state transcript omitted missing path: {relative_path}", file=sys.stderr)
            return 1

    print(f"base commit: {BASE_COMMIT}")
    print(f"test blob: {TEST_BLOB}")
    print("command: python3 -m unittest tests.test_package_structure -v")
    print(transcript, end="")
    print(f"observed child exit: {child.returncode}")
    print("red-state signature verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
