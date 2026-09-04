#!/usr/bin/env python3
"""Small portable controller for Exakt's in-harness workflow and report UI."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPORT_VERSION = "exakt-report-v1"
MODES = ("task", "product")
PHASES = (
    "intake",
    "recon",
    "requirements",
    "design",
    "plan",
    "execute",
    "verify",
    "handoff",
)
STATUSES = ("draft", "active", "blocked", "failed", "unverified", "verified")
RENDERER = Path(__file__).resolve().with_name("render_report.py")


class ExaktCliError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def title_from_request(request: str) -> str:
    first = " ".join(request.strip().split())
    if not first:
        raise ExaktCliError("request must not be empty")
    return first if len(first) <= 72 else first[:69].rstrip() + "…"


def initial_state(request: str, mode: str, title: str | None = None) -> dict[str, Any]:
    if mode not in MODES:
        raise ExaktCliError(f"mode must be one of: {', '.join(MODES)}")
    request = request.strip()
    if not request:
        raise ExaktCliError("request must not be empty")
    return {
        "schema_version": REPORT_VERSION,
        "title": title or title_from_request(request),
        "mode": mode,
        "summary": "Exakt has captured the request. Reconnaissance and requirements are next.",
        "status": "draft",
        "phase": "intake",
        "updated_at": utc_now(),
        "brief": {"outcome": request, "users": [], "constraints": []},
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


def _is_list(value: Any) -> bool:
    return isinstance(value, list)


def validate_state(state: Any) -> dict[str, Any]:
    if not isinstance(state, dict):
        raise ExaktCliError("report state must be a JSON object")
    required = {
        "schema_version",
        "title",
        "mode",
        "summary",
        "status",
        "phase",
        "updated_at",
        "brief",
        "requirements",
        "architecture",
        "acceptance_criteria",
        "tasks",
        "critiques",
        "decisions",
        "verification",
        "files",
        "evidence",
        "gaps",
    }
    missing = sorted(required - set(state))
    if missing:
        raise ExaktCliError("report state is missing: " + ", ".join(missing))
    if state["schema_version"] != REPORT_VERSION:
        raise ExaktCliError("unsupported report schema version")
    if state["mode"] not in MODES or state["phase"] not in PHASES:
        raise ExaktCliError("report mode or phase is invalid")
    if state["status"] not in STATUSES:
        raise ExaktCliError("report status is invalid")
    for name in ("title", "summary", "updated_at"):
        if not isinstance(state[name], str):
            raise ExaktCliError(f"report {name} must be text")
    if not isinstance(state["brief"], dict) or not isinstance(
        state["architecture"], dict
    ):
        raise ExaktCliError("brief and architecture must be objects")
    for name in (
        "requirements",
        "acceptance_criteria",
        "tasks",
        "critiques",
        "decisions",
        "verification",
        "files",
        "evidence",
        "gaps",
    ):
        if not _is_list(state[name]):
            raise ExaktCliError(f"report {name} must be a list")
    return state


def load_state(path: Path) -> dict[str, Any]:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ExaktCliError(f"cannot read report state: {error}") from error
    return validate_state(state)


def write_state(path: Path, state: dict[str, Any], *, force: bool = False) -> None:
    if path.exists() and not force:
        raise ExaktCliError(f"refusing to overwrite existing state: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        state, ensure_ascii=False, sort_keys=True, indent=2
    ).encode("utf-8") + b"\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.tmp-", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def report_path_for(state_path: Path) -> Path:
    if state_path.name == "exakt-state.json":
        return state_path.with_name("exakt-report.html")
    return state_path.with_suffix(".html")


def render(state_path: Path, output: Path, *, force: bool = False) -> None:
    load_state(state_path)
    if output.exists() and not force:
        raise ExaktCliError(f"refusing to overwrite existing report: {output}")
    if not RENDERER.is_file():
        raise ExaktCliError("HTML renderer is not installed")
    output.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [sys.executable, str(RENDERER), str(state_path), "--output", str(output)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown renderer error"
        raise ExaktCliError(f"renderer failed: {detail}")


def verification_gaps(state: dict[str, Any]) -> list[str]:
    gaps: list[str] = []
    if state["status"] != "verified":
        gaps.append(f"status is {state['status']!r}, not 'verified'")
    if state["phase"] != "handoff":
        gaps.append(f"phase is {state['phase']!r}, not 'handoff'")
    criteria = state["acceptance_criteria"]
    if not criteria:
        gaps.append("no acceptance criteria were recorded")
    elif any(not isinstance(item, dict) or item.get("status") != "verified" for item in criteria):
        gaps.append("pending acceptance criteria remain")
    checks = state["verification"]
    if not checks:
        gaps.append("no verification evidence was recorded")
    elif any(not isinstance(item, dict) or item.get("status") != "verified" for item in checks):
        gaps.append("verification contains non-verified results")
    if state["gaps"]:
        gaps.append("declared gaps remain")
    return gaps


def command_init(args: argparse.Namespace) -> int:
    output = Path(args.output).resolve()
    state = initial_state(args.request, args.mode, args.title)
    write_state(output, state, force=args.force)
    report = report_path_for(output)
    if not args.no_render:
        render(output, report, force=args.force)
    print("EXAKT  •  " + args.mode.upper() + "  •  INTAKE")
    print(f"State   {output}")
    if not args.no_render:
        print(f"Report  {report}")
    print("Next    Inspect the real project, then define requirements and acceptance criteria.")
    return 0


def command_status(args: argparse.Namespace) -> int:
    path = Path(args.state).resolve()
    state = load_state(path)
    verified = sum(
        1
        for item in state["acceptance_criteria"]
        if isinstance(item, dict) and item.get("status") == "verified"
    )
    total = len(state["acceptance_criteria"])
    print(
        f"EXAKT  •  {state['mode'].upper()}  •  {state['phase'].upper()}  •  {state['status'].upper()}"
    )
    print(f"Project {state['title']}")
    print(f"Proof   {verified}/{total} acceptance criteria verified")
    print(f"State   {path}")
    return 0


def command_verify(args: argparse.Namespace) -> int:
    state = load_state(Path(args.state).resolve())
    gaps = verification_gaps(state)
    if gaps:
        print("EXAKT  •  NOT VERIFIED")
        for gap in gaps:
            print(f"- {gap}")
        return 2
    print("EXAKT  •  VERIFIED")
    print("All acceptance criteria and recorded checks are verified against this state.")
    return 0


def command_render(args: argparse.Namespace) -> int:
    source = Path(args.state).resolve()
    output = Path(args.output).resolve() if args.output else report_path_for(source)
    render(source, output, force=args.force)
    print("EXAKT  •  REPORT READY")
    print(f"Report  {output}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="exakt",
        description="Exakt: spec, build, inspect, verify, and explain engineering work.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    init = subparsers.add_parser("init", help="create a task or product workspace")
    init.add_argument("request", help="task, outcome, or complete product brief")
    init.add_argument("--mode", choices=MODES, default="task")
    init.add_argument("--title")
    init.add_argument("--output", default=".exakt/exakt-state.json")
    init.add_argument("--no-render", action="store_true")
    init.add_argument("--force", action="store_true")
    init.set_defaults(handler=command_init)

    status = subparsers.add_parser("status", help="show a compact truthful status")
    status.add_argument("state")
    status.set_defaults(handler=command_status)

    verify = subparsers.add_parser("verify", help="apply the minimal completion gate")
    verify.add_argument("state")
    verify.set_defaults(handler=command_verify)

    report = subparsers.add_parser("render", help="render the local interactive report")
    report.add_argument("state")
    report.add_argument("--output")
    report.add_argument("--force", action="store_true")
    report.set_defaults(handler=command_render)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        return args.handler(args)
    except ExaktCliError as error:
        print(f"exakt: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
