"""Render a deterministic behavior impact report from Greenfield Steps 1.5-5."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from greenfield.artifact_io import read_json_object, write_json_atomic
from greenfield.behavior_impact_report import (
    BehaviorImpactReportError,
    build_behavior_impact_report,
    render_behavior_impact_report_markdown,
    validate_behavior_impact_report,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--step2", required=True, type=Path)
    parser.add_argument("--step3", required=True, type=Path)
    parser.add_argument("--step4", required=True, type=Path)
    parser.add_argument("--step5", required=True, type=Path)
    parser.add_argument("--output-json", required=True, type=Path)
    parser.add_argument("--output-markdown", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = build_behavior_impact_report(
            read_json_object(args.contract),
            read_json_object(args.step2),
            read_json_object(args.step3),
            read_json_object(args.step4),
            read_json_object(args.step5),
        )
        errors = validate_behavior_impact_report(report)
        if errors:
            raise BehaviorImpactReportError(
                "generated invalid behavior handbook: " + "; ".join(errors)
            )
        markdown = render_behavior_impact_report_markdown(report)
    except (OSError, TypeError, ValueError) as exc:
        print(f"greenfield behavior handbook failed: {exc}", file=sys.stderr)
        return 2

    write_json_atomic(args.output_json, report)
    args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
    args.output_markdown.write_text(markdown, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
