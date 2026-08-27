"""Validate a persisted Strands-agent Greenfield Step 1.5 trace."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from greenfield.artifact_io import read_json_object
from greenfield.step1_5_trace import validate_trace
from scripts.validate_greenfield_step1 import validate as validate_step1


def validate(step1: dict[str, object], trace: dict[str, object]) -> list[str]:
    errors = validate_step1(step1)
    if errors:
        return ["invalid Step 1 report: " + "; ".join(errors)]
    return validate_trace(step1, trace)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step1-report", required=True, type=Path)
    parser.add_argument("--trace", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        errors = validate(read_json_object(args.step1_report), read_json_object(args.trace))
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"greenfield Step 1.5 validation failed: {exc}", file=sys.stderr)
        return 2
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(args.trace)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
