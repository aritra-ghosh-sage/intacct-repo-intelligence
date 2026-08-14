#!/usr/bin/env python3
"""Generate a complete PR-review LLM prompt from a PR number and request."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from catalog.github_pr_metadata import GitHubPrMetadataError
from catalog.pr_review_catalog import PrReviewCatalogError
from catalog.pr_review_prompt import PromptBuildError, generate_prompt


def _pr_number(value: str) -> int:
    match = re.fullmatch(
        r"\s*(?:https?://github\.com/([^/]+)/([^/]+)/pull/)?(\d+)\s*/?\s*",
        value,
    )
    if not match:
        raise argparse.ArgumentTypeError(
            "PR must be a positive number or GitHub pull-request URL"
        )
    if match.group(1) and (match.group(1), match.group(2)) != ("intacct", "ia-app"):
        raise argparse.ArgumentTypeError("PR URL must target intacct/ia-app")
    number = int(match.group(3))
    if number <= 0:
        raise argparse.ArgumentTypeError("PR number must be positive")
    return number


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pr", required=True, type=_pr_number, help="PR number or GitHub PR URL"
    )
    parser.add_argument("--request", required=True, help="User request to analyze")
    parser.add_argument(
        "--manifest", type=Path, default=Path("config/workspace_repos.yaml")
    )
    parser.add_argument("--repo-key", default="ia-main")
    parser.add_argument("--max-hops", type=int, choices=(1, 2), default=2)
    parser.add_argument("--min-confidence", type=float, default=0.7)
    parser.add_argument(
        "--prompt-only",
        action="store_true",
        help="Print only prompt_text instead of the JSON envelope",
    )
    args = parser.parse_args(argv)
    try:
        envelope = generate_prompt(
            pr_number=args.pr,
            request=args.request,
            manifest=args.manifest,
            repo_key=args.repo_key,
            max_hops=args.max_hops,
            min_confidence=args.min_confidence,
        )
    except GitHubPrMetadataError as exc:
        print(
            "ERROR [github_metadata_unavailable]: "
            f"{exc}. Fix: verify gh authentication and access to the PR, then retry.",
            file=sys.stderr,
        )
        return 1
    except (PrReviewCatalogError, PromptBuildError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(
            f"ERROR [local_file_unavailable]: {exc}. "
            "Fix: verify the workspace files and permissions, then retry.",
            file=sys.stderr,
        )
        return 1
    if args.prompt_only:
        print(envelope["prompt_text"])
    else:
        print(json.dumps(envelope, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
