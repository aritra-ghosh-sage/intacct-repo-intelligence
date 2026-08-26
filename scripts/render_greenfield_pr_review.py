"""Render a canonical Greenfield PR review from retained evidence artifacts."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from greenfield.artifact_io import read_json_object, write_json_atomic
from greenfield.pr_review import render_review, validate_review


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--discovery", required=True, type=Path)
    parser.add_argument("--assessment", required=True, type=Path)
    parser.add_argument("--context", action="append", default=[])
    parser.add_argument("--ci-evidence", action="append", default=[])
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    report = render_review(request=read_json_object(args.request), discovery=read_json_object(args.discovery), assessment=read_json_object(args.assessment), ci_evidence=[read_json_object(path) for path in args.ci_evidence], contexts=[read_json_object(path) for path in args.context])
    errors = validate_review(report)
    if errors:
        print("; ".join(errors), file=sys.stderr)
        return 2
    write_json_atomic(args.output, report)
    args.output.with_suffix(".md").write_text(report["markdown"], encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
