#!/usr/bin/env python3
"""CLI for the read-only PR impact Step 2 audit."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from catalog.pr_impact_step2 import analyze_fixture, render_review_markdown


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--manifest", default="config/workspace_repos.yaml")
    parser.add_argument("--active-db", required=True)
    parser.add_argument("--repo-key", required=True)
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true", help="emit JSON (the default)")
    output.add_argument("--markdown", action="store_true", help="emit Markdown")
    args = parser.parse_args(argv)
    report = analyze_fixture(args.fixture, args.manifest, args.active_db, args.repo_key)
    if args.markdown:
        print(render_review_markdown(report), end="")
    else:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if report["status"] != "blocked" else 2


if __name__ == "__main__":
    raise SystemExit(main())
