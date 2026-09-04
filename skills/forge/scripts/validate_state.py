#!/usr/bin/env python3
"""Validate one JSON document against an installed Forge contract."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from contracts import SCHEMA_BASE_ID, ContractError, ContractRegistry


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate a JSON document against a Forge v1 schema."
    )
    parser.add_argument(
        "--schema",
        help="Schema filename, version name, or stable $id; defaults to schema_version.",
    )
    parser.add_argument("document", type=Path, help="JSON document to validate")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        schema_id = ContractRegistry().validate_file(args.document, args.schema)
    except ContractError as error:
        print(f"invalid: {error}", file=sys.stderr)
        return 1
    schema_name = schema_id.removeprefix(SCHEMA_BASE_ID)
    print(f"valid: {schema_name}.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
