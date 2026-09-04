#!/usr/bin/env python3
"""Install the same self-contained Exakt skill into supported harness homes."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = SKILL_ROOT.parents[1]


class InstallError(ValueError):
    pass


def default_root(host: str) -> Path:
    home = Path.home()
    if host == "codex":
        return Path(os.environ.get("CODEX_HOME", home / ".codex"))
    if host == "claude":
        return Path(os.environ.get("CLAUDE_CONFIG_DIR", home / ".claude"))
    raise InstallError("generic installs require --root")


def install(host: str, root: Path, *, dry_run: bool = False) -> tuple[Path, str]:
    root = root.expanduser().resolve()
    if host not in {"codex", "claude", "generic"}:
        raise InstallError("host must be codex, claude, or generic")
    legacy_paths = (
        [root / "forge"]
        if host == "generic"
        else [root / "skills" / "forge", root / "commands" / "forge.md"]
    )
    legacy = next(
        (path for path in legacy_paths if path.exists() or path.is_symlink()),
        None,
    )
    if legacy is not None:
        raise InstallError(
            f"remove the legacy forge installation before installing Exakt: {legacy}"
        )
    destination = root / "skills" / "exakt" if host != "generic" else root / "exakt"
    if destination.exists() or destination.is_symlink():
        raise InstallError(f"destination already exists: {destination}")
    command = root / "commands" / "exakt.md" if host == "claude" else None
    if command is not None and (command.exists() or command.is_symlink()):
        raise InstallError(f"command already exists: {command}")
    if dry_run:
        invocation = "/exakt <task>" if host == "claude" else "$exakt <task>"
        return destination, invocation

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=".exakt-install-", dir=destination.parent)
    )
    try:
        shutil.copytree(
            SKILL_ROOT,
            temporary / "exakt",
            symlinks=False,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        copied = temporary / "exakt"
        if not (copied / "SKILL.md").is_file():
            raise InstallError("source package has no SKILL.md")
        copied.replace(destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)

    if host == "claude":
        source_command = PACKAGE_ROOT / ".claude" / "commands" / "exakt.md"
        assert command is not None
        command.parent.mkdir(parents=True, exist_ok=True)
        command_text = source_command.read_text(encoding="utf-8").replace(
            "skills/exakt/SKILL.md", str(destination / "SKILL.md")
        )
        command.write_text(command_text, encoding="utf-8")
        invocation = "/exakt <task>"
    elif host == "codex":
        invocation = "$exakt <task>"
    else:
        invocation = "Ask your harness to use exakt/SKILL.md for <task>"
    return destination, invocation


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install the portable Exakt skill")
    parser.add_argument("--host", choices=("codex", "claude", "generic"), required=True)
    parser.add_argument("--root")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        root = Path(args.root) if args.root else default_root(args.host)
        destination, invocation = install(args.host, root, dry_run=args.dry_run)
    except (InstallError, OSError) as error:
        print(f"exakt install: {error}", file=sys.stderr)
        return 2
    label = "Would install" if args.dry_run else "Installed"
    print(f"EXAKT  •  {label.upper()}")
    print(f"Skill   {destination}")
    print(f"Use     {invocation}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
