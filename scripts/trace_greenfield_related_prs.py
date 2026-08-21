#!/usr/bin/env python3
"""Capture bounded related pull-request evidence without GitHub writes."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from catalog.github_pr_metadata import GitHubPrMetadataError, _provider_call
from greenfield.artifact_io import artifact_sha256, read_json_object, write_json_atomic
from greenfield.related_prs import build_related_pr_evidence
from greenfield.source_identity import source_identity
from scripts.validate_greenfield_step1 import validate as validate_step1


def _call(endpoint: str) -> Any:
    value, _provider = _provider_call(endpoint, collection=False)
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step1-report", required=True, type=Path)
    parser.add_argument("--candidate-repository", action="append", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    source_repo_key: str | None = None
    canonical_repository: str | None = None
    source_pr_number: int | None = None
    source_revision: str | None = None
    try:
        step1 = read_json_object(args.step1_report)
        errors = validate_step1(step1)
        if errors:
            raise ValueError("invalid Greenfield Step 1 report: " + "; ".join(errors))
        data = step1["input"]
        canonical_repository, source_repo_key = source_identity(data)
        source_pr_number = data["pr_number"]
        source_revision = data["target_revision"]
        timeline_endpoint = (
            f"repos/{canonical_repository}/issues/{source_pr_number}/timeline?per_page=100"
        )
        timeline = _provider_call(
            timeline_endpoint, collection=True
        )[0]
        pull_requests: dict[tuple[str, int], dict[str, Any]] = {}
        for event in timeline:
            source = event.get("source") if isinstance(event, dict) else None
            issue = source.get("issue") if isinstance(source, dict) else None
            repository_url = issue.get("repository_url") if isinstance(issue, dict) else None
            number = issue.get("number") if isinstance(issue, dict) else None
            if (
                event.get("event") != "cross-referenced"
                or not isinstance(repository_url, str)
                or not isinstance(number, int)
            ):
                continue
            repository = repository_url.split("/repos/", 1)[-1].strip("/")
            if repository not in set(args.candidate_repository):
                continue
            pull_requests[(repository, number)] = _call(
                f"repos/{repository}/pulls/{number}"
            )
        report = build_related_pr_evidence(
            source_repository=source_repo_key,
            canonical_repository=canonical_repository,
            source_pr_number=source_pr_number,
            source_revision=source_revision,
            candidate_repositories=args.candidate_repository,
            timeline_events=timeline,
            pull_requests=pull_requests,
            evidence_path=args.output.as_posix(),
        )
    except (OSError, ValueError, GitHubPrMetadataError) as exc:
        report = {
            "schema_version": "0.1",
            "evidence_type": "related_pull_requests",
            "status": "unavailable",
            "source_repository": source_repo_key,
            "canonical_source_repository": canonical_repository,
            "source_revision": source_revision,
            "source_pr_number": source_pr_number,
            "pull_requests": [],
            "evidence_path": args.output.as_posix(),
            "gaps": [f"related_pr_evidence_unavailable:{exc}"],
        }
        report["artifact_sha256"] = artifact_sha256(report)
    try:
        write_json_atomic(args.output, report)
    except OSError as exc:
        print(f"greenfield_related_prs_failed: {exc}", file=sys.stderr)
        return 2
    print(f"greenfield related PR evidence written: {args.output}")
    return 0 if report.get("status") != "unavailable" else 2


if __name__ == "__main__":
    raise SystemExit(main())
