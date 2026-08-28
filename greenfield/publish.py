"""Canonical GitHub Check and idempotent PR-comment publication."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from greenfield.analysis_report import validate_analysis_report
from greenfield.artifact_io import artifact_sha256
from greenfield.pr_review import validate_review

CHECK_NAME = "Greenfield impact analysis"
COMMENT_MARKER = "<!-- greenfield-impact-analysis -->"


class GitHubPublisher(Protocol):
    def request(
        self, method: str, endpoint: str, body: Mapping[str, Any] | None = None
    ) -> Any: ...


def build_publication(
    analysis: Mapping[str, Any],
    *,
    artifact_bundle: str,
    draft_pr: Mapping[str, Any] | None = None,
    validation: Mapping[str, Any] | None = None,
    review: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    errors = validate_analysis_report(analysis)
    if errors:
        raise ValueError("invalid analysis report: " + "; ".join(errors))
    if isinstance(review, Mapping):
        review_errors = validate_review(review)
        if review_errors:
            raise ValueError("invalid PR review: " + "; ".join(review_errors))
    source = analysis["source"]
    impacts = analysis["repository_impacts"]
    actions = analysis["actions"]
    blocked = any(row.get("action_type") == "block_automation" for row in actions)
    conclusion = "failure" if blocked else "neutral" if analysis["gaps"] else "success"
    draft_url = None
    if isinstance(draft_pr, Mapping):
        pull = draft_pr.get("pull_request")
        if isinstance(pull, Mapping):
            draft_url = pull.get("url")
    summary = (
        f"{len(impacts)} impacted repository candidate(s), "
        f"{len(actions)} recommended action(s), {len(analysis['gaps'])} gap(s)."
    )
    lines = [
        COMMENT_MARKER,
        "## Greenfield impact analysis",
        "",
        summary,
        "",
        "### Ranked impact",
    ]
    lines.extend(
        f"- {row.get('rank')}. `{row.get('repository')}`: "
        f"**{row.get('evidence_state')}** - {row.get('rationale')}"
        for row in impacts
    )
    lines.extend(["", "### Recommended actions"])
    lines.extend(
        f"- `{row.get('action_type')}` in `{row.get('target_repository')}`: "
        f"{row.get('rationale')}"
        for row in actions
    )
    if not actions:
        lines.append("- No automated remediation was justified.")
    lines.extend(["", "### Remaining uncertainty"])
    lines.extend(f"- {gap}" for gap in analysis["gaps"])
    if not analysis["gaps"]:
        lines.append("- None recorded.")
    lines.extend(["", f"Artifact bundle: `{artifact_bundle}`"])
    if draft_url:
        lines.append(f"Draft test PR: {draft_url}")
    comment = "\n".join(lines) + "\n"
    if isinstance(review, Mapping) and isinstance(review.get("markdown"), str):
        comment = COMMENT_MARKER + "\n" + str(review["markdown"])
    publication: dict[str, Any] = {
        "schema_version": "0.1",
        "analysis_kind": "greenfield_github_publication",
        "source": {
            "repository": source["repository"],
            "pr_number": source["pr_number"],
            "head_revision": source["head_revision"],
        },
        "check": {
            "name": CHECK_NAME,
            "external_id": analysis["run_context_sha256"],
            "head_sha": source["head_revision"],
            "status": "completed",
            "conclusion": conclusion,
            "output": {
                "title": "Greenfield impact analysis",
                "summary": summary,
                "text": comment,
            },
        },
        "comment": {"marker": COMMENT_MARKER, "body": comment},
        "draft_pr_url": draft_url,
        "validation_status": validation.get("status")
        if isinstance(validation, Mapping)
        else "not_run",
        "provenance": {
            "analysis_report_sha256": analysis["report_sha256"],
            "artifact_bundle": artifact_bundle,
            "pr_review_sha256": review.get("review_sha256")
            if isinstance(review, Mapping)
            else None,
        },
    }
    publication["publication_sha256"] = artifact_sha256(publication)
    return publication


def publish_github(
    publication: Mapping[str, Any], publisher: GitHubPublisher
) -> dict[str, Any]:
    """Create/update one check and one marker-bound PR comment idempotently."""

    source = publication["source"]
    repository = source["repository"]
    head = source["head_revision"]
    check = dict(publication["check"])
    existing_checks = publisher.request(
        "GET", f"repos/{repository}/commits/{head}/check-runs"
    )
    rows = (
        existing_checks.get("check_runs", [])
        if isinstance(existing_checks, Mapping)
        else []
    )
    matches = [
        row
        for row in rows
        if isinstance(row, Mapping)
        and row.get("name") == CHECK_NAME
        and row.get("external_id") == check["external_id"]
    ]
    if len(matches) > 1:
        raise ValueError("multiple Greenfield checks match the run context")
    if matches:
        update = {
            key: check[key]
            for key in ("name", "status", "conclusion", "output")
            if key in check
        }
        check_result = publisher.request(
            "PATCH", f"repos/{repository}/check-runs/{matches[0]['id']}", update
        )
        check_status = "updated"
    else:
        check_result = publisher.request(
            "POST", f"repos/{repository}/check-runs", check
        )
        check_status = "created"

    comments = publisher.request(
        "GET", f"repos/{repository}/issues/{source['pr_number']}/comments?per_page=100"
    )
    comment_matches = [
        row
        for row in comments
        if isinstance(row, Mapping)
        and publication["comment"]["marker"] in str(row.get("body", ""))
    ]
    if len(comment_matches) > 1:
        raise ValueError("multiple Greenfield PR comments contain the canonical marker")
    body = {"body": publication["comment"]["body"]}
    if comment_matches:
        comment_result = publisher.request(
            "PATCH",
            f"repos/{repository}/issues/comments/{comment_matches[0]['id']}",
            body,
        )
        comment_status = "updated"
    else:
        comment_result = publisher.request(
            "POST", f"repos/{repository}/issues/{source['pr_number']}/comments", body
        )
        comment_status = "created"
    return {
        "status": "published",
        "check": {"status": check_status, "id": check_result.get("id")},
        "comment": {"status": comment_status, "id": comment_result.get("id")},
        "publication_sha256": publication["publication_sha256"],
    }


__all__ = ["CHECK_NAME", "COMMENT_MARKER", "build_publication", "publish_github"]
