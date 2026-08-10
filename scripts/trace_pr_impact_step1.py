#!/usr/bin/env python3
"""CLI for the read-only PR impact Step 1 tracer."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from catalog.pr_impact_step1 import Step1Error, analyze_fixture, blocked_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", required=True)
    parser.add_argument(
        "--manifest", default="config/workspace_repos.yaml"
    )
    parser.add_argument("--active-db", required=True)
    parser.add_argument("--repo-key", required=True)
    parser.add_argument("--metadata", help="optional normalized PR metadata JSON artifact")
    parser.add_argument("--json", action="store_true", help="emit JSON (the default)")
    args = parser.parse_args(argv)
    try:
        report = analyze_fixture(args.fixture, args.manifest, args.active_db, args.repo_key, args.metadata)
    except Step1Error as exc:
        report = blocked_report(exc)
    except Exception as exc:  # keep operator output a stable report envelope
        report = blocked_report(Step1Error("step1_failure", str(exc)))
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0 if report["status"] != "blocked" else 2


if __name__ == "__main__":
    raise SystemExit(main())
