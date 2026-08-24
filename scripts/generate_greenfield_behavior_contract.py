"""Generate a Greenfield behavioral contract from exact PR trace evidence."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from greenfield.artifact_io import read_json_object
from greenfield.behavior_contract import (
    BehaviorContractError,
    generate_behavior_contract,
    load_source_trace,
    write_behavior_contract,
)
from scripts.validate_greenfield_step1 import validate as validate_step1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step1-report", required=True, type=Path)
    parser.add_argument("--source-trace", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        step1 = read_json_object(args.step1_report)
        errors = validate_step1(step1)
        if errors:
            raise BehaviorContractError("invalid Step 1 report: " + "; ".join(errors))
        contract = generate_behavior_contract(
            step1,
            load_source_trace(args.source_trace),
            source_trace_path=args.source_trace.as_posix(),
        )
        write_behavior_contract(contract, args.output)
    except (OSError, TypeError, ValueError) as exc:
        print(f"behavior contract generation failed: {exc}", file=sys.stderr)
        return 2
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
