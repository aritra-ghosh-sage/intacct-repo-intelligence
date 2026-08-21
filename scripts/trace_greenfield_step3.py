"""Assemble a deterministic greenfield Step 3 blast-radius report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from greenfield.artifact_io import write_json_atomic
from greenfield.semantic_contract import load_index
from greenfield.step3_outcome import (
    OutcomeError,
    assemble_outcome,
    load_related_pr_evidence,
)
from scripts.validate_greenfield_step3 import validate as validate_step3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step2-report", required=True)
    parser.add_argument("--semantic-index")
    parser.add_argument("--related-pr-evidence")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    try:
        step2 = json.loads(Path(args.step2_report).read_text(encoding="utf-8"))
        semantic = load_index(args.semantic_index) if args.semantic_index else None
        related = load_related_pr_evidence(args.related_pr_evidence) if args.related_pr_evidence else None
        report = assemble_outcome(step2, semantic_index=semantic, related_pr_evidence=related)
        report_errors = validate_step3(report)
        if report_errors:
            raise OutcomeError(
                "generated invalid Greenfield Step 3 report: " + "; ".join(report_errors)
            )
    except (OSError, json.JSONDecodeError, OutcomeError, ValueError) as exc:
        print(f"greenfield Step 3 failed: {exc}", file=sys.stderr)
        return 2
    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output:
        write_json_atomic(args.output, report)
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
