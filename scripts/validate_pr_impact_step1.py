#!/usr/bin/env python3
"""Validate a separately materialized Step 1 JSON report."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

STATUSES = {"complete", "partial", "blocked"}
SURFACE_STATUSES = {"available", "empty", "unavailable", "unresolved", "ambiguous", "stale", "deferred"}


def validate(report: object) -> list[str]:
    errors: list[str] = []
    if not isinstance(report, dict): return ["report must be a JSON object"]
    if report.get("schema_version") != "0.1": errors.append("schema_version must be 0.1")
    if report.get("analysis_kind") != "pr_impact_step_1": errors.append("invalid analysis_kind")
    if report.get("status") not in STATUSES: errors.append("invalid status")
    for key in ("input", "preflight", "changed_files", "direct_traces", "onboarding_feasibility", "impact_ranking", "gaps", "warnings", "provenance"):
        if key not in report: errors.append(f"missing section: {key}")
    if isinstance(report.get("input"), dict):
        for key in ("manifest", "repo_key", "repo_root", "base_revision", "target_revision"):
            if key not in report["input"] and report.get("status") != "blocked":
                errors.append(f"missing input field: {key}")
    for trace in report.get("direct_traces", []):
        if not isinstance(trace, dict) or trace.get("status") not in SURFACE_STATUSES:
            errors.append("direct trace has invalid status")
        if isinstance(trace, dict) and trace.get("status") == "empty" and not trace.get("warning"):
            errors.append("empty trace must include a warning")
        if isinstance(trace, dict) and trace.get("status") in {"unresolved", "ambiguous", "stale"} and not trace.get("warning"):
            errors.append("classified trace must include a warning")
    if report.get("status") == "complete":
        for trace in report.get("direct_traces", []):
            if isinstance(trace, dict) and trace.get("status") not in {"available", "unavailable", "deferred"}:
                errors.append("complete report contains a direct-trace gap")
    if report.get("status") == "blocked":
        if not isinstance(report.get("error"), dict) or not report["error"].get("code"):
            errors.append("blocked report requires error.code")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True)
    args = parser.parse_args(argv)
    try:
        report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"invalid report: {exc}", file=sys.stderr)
        return 2
    errors = validate(report)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 2
    print("valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
