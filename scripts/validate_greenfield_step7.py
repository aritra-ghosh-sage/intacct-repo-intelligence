"""Validate a Greenfield Step 6 patch before any Step 8 PR creation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from greenfield.artifact_io import write_json_atomic
from greenfield.step7_contract import (
    Step7Error,
    load_json,
    validate_step7_report,
)
from greenfield.step7_profiles import Step7ProfileError, load_profile_registry
from greenfield.step7_runner import LocalSubprocessRunner
from greenfield.step7_validate import validate_step7


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step6-report", required=True, type=Path)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--profiles", required=True, type=Path)
    parser.add_argument("--runner", required=True, choices=("local",))
    parser.add_argument("--target-checkout", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        step6 = load_json(args.step6_report, "Step 6 report")
        request = load_json(args.request, "Step 7 request")
        registry = load_profile_registry(args.profiles)
        report = validate_step7(
            step6,
            request,
            args.target_checkout,
            profile_registry=registry,
            runner=LocalSubprocessRunner(),
        )
        errors = validate_step7_report(report)
        if errors:
            raise Step7Error("generated invalid Step 7 report: " + "; ".join(errors))
        write_json_atomic(args.output, report)
    except (OSError, Step7Error, Step7ProfileError) as exc:
        print(f"greenfield Step 7 failed: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": report["status"],
                "pr_eligible": report["pr_eligible"],
                "output": str(args.output),
            },
            sort_keys=True,
        )
    )
    return 0 if report["pr_eligible"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
