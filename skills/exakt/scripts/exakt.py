#!/usr/bin/env python3
"""Small portable controller for Exakt's in-harness workflow and report UI."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import report_state as state_contract
import render_spec as spec_renderer

REPORT_VERSION = state_contract.REPORT_V2
MODES = state_contract.MODES
RENDERER = Path(__file__).resolve().with_name("render_report.py")


class ExaktCliError(ValueError):
    pass


def initial_state(request: str, mode: str, title: str | None = None) -> dict[str, Any]:
    return state_contract.initial_state(request, mode, title)


def validate_state(state: Any) -> dict[str, Any]:
    return state_contract.validate_state(state)


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


def spec_path_for(state_path: Path) -> Path:
    return state_path.with_name("spec.md")


def display_path(path: Path) -> str:
    try:
        return path.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return str(path)


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
    return state_contract.verification_gaps(state)


def command_init(args: argparse.Namespace) -> int:
    output = Path(args.output).resolve()
    state = initial_state(args.request, args.mode, args.title)
    specification = spec_path_for(output)
    report = report_path_for(output)
    candidates = [output, specification]
    if not args.no_render:
        candidates.append(report)
    if not args.force:
        for candidate in candidates:
            if candidate.exists():
                raise ExaktCliError(f"refusing to overwrite existing artifact: {candidate}")
    state["spec"]["path"] = display_path(specification)
    spec_renderer.write_spec(specification, state, force=args.force)
    write_state(output, state, force=args.force)
    if not args.no_render:
        render(output, report, force=args.force)
    print("EXAKT  •  " + args.mode.upper() + "  •  INTAKE")
    print(f"State   {output}")
    print(f"Spec    {specification}")
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
    legacy = state_contract.legacy_state(state)
    suffix = "  •  V1 LEGACY" if legacy else ""
    print(
        f"EXAKT  •  {state['mode'].upper()}  •  {state['phase'].upper()}  •  "
        f"{state['status'].upper()}{suffix}"
    )
    print(f"Project {state['title']}")
    print(f"Proof   {verified}/{total} acceptance criteria verified")
    if legacy:
        print("Truth   Legacy v1 rules; no v2 trace or proof provenance")
    else:
        print(f"Truth   {state['authority_mode']}")
        provenances = sorted(
            {
                item["provenance"]
                for item in state["evidence"]
                if item.get("status") == "verified"
            }
        )
        print(
            "Source  "
            + (", ".join(provenances) if provenances else "no verified evidence recorded")
        )
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
    if state_contract.legacy_state(state):
        print("EXAKT  •  VERIFIED  •  V1 LEGACY")
        print("Verified under v1 rules; no v2 trace or proof provenance is claimed.")
    else:
        print("EXAKT  •  VERIFIED")
        print("All acceptance criteria, milestones, traces, and proof gates are verified.")
    return 0


def command_migrate(args: argparse.Namespace) -> int:
    source = Path(args.state).resolve()
    output = Path(args.output).resolve()
    if source == output:
        raise ExaktCliError("refusing in-place migration; choose a new output path")
    if output.exists():
        raise ExaktCliError(f"refusing to overwrite existing state: {output}")
    state = load_state(source)
    if not state_contract.legacy_state(state):
        raise ExaktCliError("migration source must be a legacy v1 state")
    migrated = state_contract.migrate_v1_state(state)
    specification = spec_path_for(output)
    if specification.exists():
        raise ExaktCliError(f"refusing to overwrite existing specification: {specification}")
    migrated["spec"]["path"] = display_path(specification)
    spec_renderer.write_spec(specification, migrated)
    write_state(output, migrated)
    print("EXAKT  •  V1 MIGRATED  •  UNVERIFIED")
    print(f"State   {output}")
    print(f"Spec    {specification}")
    print("Next    Rebuild traceability and gather fresh v2 proof.")
    return 0


def command_render(args: argparse.Namespace) -> int:
    source = Path(args.state).resolve()
    output = Path(args.output).resolve() if args.output else report_path_for(source)
    if args.force:
        state = load_state(source)
        if not state_contract.legacy_state(state):
            specification = spec_path_for(source)
            state["spec"]["path"] = display_path(specification)
            spec_renderer.write_spec(specification, state, force=True)
            write_state(source, state, force=True)
    render(source, output, force=args.force)
    print("EXAKT  •  REPORT READY")
    print(f"Report  {output}")
    return 0


def command_spec(args: argparse.Namespace) -> int:
    source = Path(args.state).resolve()
    state = load_state(source)
    output = Path(args.output).resolve() if args.output else spec_path_for(source)
    state["spec"]["path"] = display_path(output)
    digest = spec_renderer.write_spec(output, state, force=args.force)
    write_state(source, state, force=True)
    print("EXAKT  •  SPEC READY")
    print(f"Spec    {output}")
    print(f"Digest  sha256:{digest}")
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

    specification = subparsers.add_parser(
        "spec", help="render the living Markdown specification"
    )
    specification.add_argument("state")
    specification.add_argument("--output")
    specification.add_argument("--force", action="store_true")
    specification.set_defaults(handler=command_spec)

    migrate = subparsers.add_parser(
        "migrate", help="copy legacy v1 content into a new unverified v2 state"
    )
    migrate.add_argument("state")
    migrate.add_argument("--output", required=True)
    migrate.set_defaults(handler=command_migrate)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        return args.handler(args)
    except (
        ExaktCliError,
        state_contract.ReportStateError,
        spec_renderer.SpecRenderError,
    ) as error:
        print(f"exakt: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
