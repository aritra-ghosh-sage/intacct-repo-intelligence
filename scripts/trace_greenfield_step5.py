"""Recommend deterministic greenfield Step 5 actions."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from greenfield.artifact_io import write_json_atomic
from greenfield.step5_actions import (
    Step5Error,
    recommend_actions,
    validate_step5_report,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step3-report", required=True)
    parser.add_argument("--step4-report", required=True)
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    try:
        step3 = json.loads(Path(args.step3_report).read_text(encoding="utf-8"))
        step4 = json.loads(Path(args.step4_report).read_text(encoding="utf-8"))
        report = recommend_actions(step3, step4)
        report_errors = validate_step5_report(report)
        if report_errors:
            raise Step5Error(
                "generated invalid Greenfield Step 5 report: " + "; ".join(report_errors)
            )
    except (OSError, json.JSONDecodeError, Step5Error, ValueError) as exc:
        print(f"greenfield Step 5 failed: {exc}", file=sys.stderr)
        return 2
    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output:
        write_json_atomic(args.output, report)
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
