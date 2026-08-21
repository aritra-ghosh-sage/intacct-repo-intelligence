"""Validate a Greenfield Step 6 patch handoff report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from greenfield.step6_contract import validate_step6_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--strict-target-evidence", action="store_true")
    parser.add_argument("--require-owner-approvals", action="store_true")
    parser.add_argument("--require-step7-eligibility", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"greenfield Step 6 validation failed: {exc}", file=sys.stderr)
        return 2
    errors = validate_step6_report(
        report,
        strict_target_evidence=args.strict_target_evidence,
        require_approvals=args.require_owner_approvals,
        require_step7_eligibility=args.require_step7_eligibility,
    )
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("greenfield Step 6 report is valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
