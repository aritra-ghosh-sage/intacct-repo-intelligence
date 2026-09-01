"""Canonical GitHub Check and idempotent PR-comment publication."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Protocol

from greenfield.analysis_report import (
    canonical_analysis_projection,
    validate_analysis_report,
)
from greenfield.artifact_io import artifact_sha256
from greenfield.planning_contract import validate_planning_report
from greenfield.pr_review import validate_review
from greenfield.step8_contract import validate_step8_report

CHECK_NAME = "Greenfield impact analysis"
COMMENT_MARKER = "<!-- greenfield-impact-analysis -->"
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
PLANNING_STATUSES = frozenset({"complete", "partial", "blocked", "unavailable", "not_run"})
VALIDATION_STATUSES = frozenset({"validated", "not_run", "blocked", "unavailable", "failed"})
CHECK_CONCLUSIONS = frozenset({"success", "failure", "neutral", "cancelled", "skipped", "timed_out", "action_required"})


def _publication_unsigned(value: Mapping[str, Any]) -> dict[str, Any]:
    unsigned = dict(value)
    unsigned.pop("publication_sha256", None)
    # These are audit projections of the digest, excluded to avoid a circular
    # self-hash while retaining the digest in both persisted projections.
    check = unsigned.get("check")
    if isinstance(check, Mapping):
        check = dict(check)
        check.pop("publication_sha256", None)
        unsigned["check"] = check
    comment = unsigned.get("comment")
    if isinstance(comment, Mapping):
        comment = dict(comment)
        comment.pop("publication_sha256", None)
        unsigned["comment"] = comment
    return unsigned


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
    planning: Mapping[str, Any] | None = None,
    handbook_resynchronization: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    errors = validate_analysis_report(analysis)
    if errors:
        raise ValueError("invalid analysis report: " + "; ".join(errors))
    if isinstance(review, Mapping):
        review_errors = validate_review(review)
        if review_errors:
            raise ValueError("invalid PR review: " + "; ".join(review_errors))
    if isinstance(planning, Mapping):
        planning_errors = validate_planning_report(planning)
        if planning_errors:
            raise ValueError("invalid planning report: " + "; ".join(planning_errors))
        analysis_planning = analysis.get("provenance", {}).get("planning", {})
        if isinstance(analysis_planning, Mapping) and analysis_planning.get("planning_sha256") != planning.get("planning_sha256"):
            raise ValueError("planning hash does not match analysis provenance")
    source = analysis["source"]
    impacts = analysis["repository_impacts"]
    actions = analysis["actions"]
    blocked = any(row.get("action_type") == "block_automation" for row in actions)
    conclusion = "failure" if blocked else "neutral" if analysis["gaps"] else "success"
    draft_url = None
    draft_result: dict[str, Any] = {"status": "not_run"}
    if isinstance(draft_pr, Mapping):
        pull = draft_pr.get("pull_request")
        if isinstance(pull, Mapping):
            draft_url = pull.get("url")
        draft_result = {
            "status": draft_pr.get("status"),
            **(
                {
                    "number": pull.get("number"),
                    "url": pull.get("url"),
                    "head_sha": pull.get("head_sha"),
                }
                if isinstance(pull, Mapping)
                else {}
            ),
        }
        if draft_pr.get("status") in {"created", "reused"}:
            step8_errors = validate_step8_report(draft_pr)
            if step8_errors:
                raise ValueError("invalid Step 8 draft result: " + "; ".join(step8_errors))
            if planning is None or planning.get("status") != "complete":
                raise ValueError("draft publication requires complete planning")
            if validation is None or validation.get("status") != "validated":
                raise ValueError("draft publication requires validated Step 7")
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
        "draft_pr": draft_result,
        "canonical_analysis": canonical_analysis_projection(analysis),
        "validation_status": validation.get("status")
        if isinstance(validation, Mapping)
        else "not_run",
        "planning_status": planning.get("status")
        if isinstance(planning, Mapping)
        else "not_run",
        "handbook_resynchronization": dict(
            handbook_resynchronization or {"status": "not_run"}
        ),
        "provenance": {
            "analysis_report_sha256": analysis["report_sha256"],
            "planning_sha256": planning.get("planning_sha256")
            if isinstance(planning, Mapping)
            else None,
            "artifact_bundle": artifact_bundle,
            "pr_review_sha256": review.get("review_sha256")
            if isinstance(review, Mapping)
            else None,
        },
    }
    publication["publication_sha256"] = artifact_sha256(_publication_unsigned(publication))
    publication["check"]["publication_sha256"] = publication["publication_sha256"]
    publication["comment"]["publication_sha256"] = publication["publication_sha256"]
    return publication


def validate_publication(value: Any, *, allow_legacy_replay: bool = False) -> list[str]:
    """Validate the immutable publication envelope before any GitHub request."""

    if not isinstance(value, Mapping):
        return ["publication must be an object"]
    errors: list[str] = []
    if value.get("schema_version") != "0.1":
        errors.append("schema_version must be 0.1")
    if value.get("analysis_kind") != "greenfield_github_publication":
        errors.append("analysis_kind is invalid")
    source = value.get("source")
    if not isinstance(source, Mapping):
        errors.append("source must be an object")
        source = {}
    if not isinstance(source.get("repository"), str) or not REPOSITORY.fullmatch(source["repository"]):
        errors.append("source.repository must be owner/repository")
    if isinstance(source.get("pr_number"), bool) or not isinstance(source.get("pr_number"), int) or source["pr_number"] <= 0:
        errors.append("source.pr_number must be a positive integer")
    if not isinstance(source.get("head_revision"), str) or not SHA40.fullmatch(source["head_revision"]):
        errors.append("source.head_revision must be a lowercase SHA")
    check = value.get("check")
    if not isinstance(check, Mapping):
        errors.append("check must be an object")
        check = {}
    for field in ("name", "external_id", "head_sha", "status", "conclusion"):
        if not isinstance(check.get(field), str) or not check[field].strip():
            errors.append(f"check.{field} is required")
    if check.get("name") != CHECK_NAME:
        errors.append("check.name is invalid")
    if not isinstance(check.get("external_id"), str) or not SHA256.fullmatch(check["external_id"]):
        errors.append("check.external_id must be a SHA-256")
    if check.get("head_sha") != source.get("head_revision"):
        errors.append("check.head_sha must match source.head_revision")
    if check.get("status") != "completed":
        errors.append("check.status must be completed")
    if check.get("conclusion") not in CHECK_CONCLUSIONS:
        errors.append("check.conclusion is invalid")
    output = check.get("output")
    if not isinstance(output, Mapping) or not isinstance(output.get("summary"), str) or not isinstance(output.get("text"), str):
        errors.append("check.output must contain summary and text")
    comment = value.get("comment")
    if not isinstance(comment, Mapping):
        errors.append("comment must be an object")
        comment = {}
    if comment.get("marker") != COMMENT_MARKER or not isinstance(comment.get("body"), str) or COMMENT_MARKER not in comment.get("body", ""):
        errors.append("comment marker and body are required")
    planning_status = value.get("planning_status")
    if planning_status not in PLANNING_STATUSES:
        errors.append("planning_status is invalid")
    if planning_status == "complete" and not isinstance(value.get("provenance"), Mapping):
        errors.append("complete publication requires provenance")
    validation_status = value.get("validation_status")
    if validation_status not in VALIDATION_STATUSES:
        errors.append("validation_status is invalid")
    draft = value.get("draft_pr")
    if not isinstance(draft, Mapping):
        if not allow_legacy_replay:
            errors.append("draft_pr must be an object")
        draft = {"status": "not_run"}
    draft_status = draft.get("status")
    if draft_status not in {"not_run", "blocked", "failed", "created", "reused"}:
        errors.append("draft_pr.status is invalid")
    if draft_status in {"created", "reused"}:
        number = draft.get("number")
        if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
            errors.append("draft_pr.number must be a positive integer")
        expected_url = f"https://github.com/{source.get('repository')}/pull/{number}"
        if draft.get("url") != expected_url:
            errors.append("draft_pr.url must be the canonical pull request URL")
        if draft.get("head_sha") != source.get("head_revision"):
            errors.append("draft_pr.head_sha must match source.head_revision")
        if planning_status != "complete":
            errors.append("draft publication requires complete planning")
        if validation_status != "validated":
            errors.append("draft publication requires validated Step 7")
    if not allow_legacy_replay and value.get("draft_pr_url") != draft.get("url"):
        errors.append("draft_pr_url must match draft_pr.url")
    provenance = value.get("provenance")
    if not isinstance(provenance, Mapping):
        errors.append("provenance must be an object")
        provenance = {}
    canonical = value.get("canonical_analysis")
    if not isinstance(canonical, Mapping):
        if not allow_legacy_replay:
            errors.append("canonical_analysis must be an object")
    else:
        if canonical.get("analysis_report_sha256") != provenance.get("analysis_report_sha256"):
            errors.append("canonical_analysis.analysis_report_sha256 must match provenance")
        for field in ("repository_impacts", "actions", "gaps"):
            if not isinstance(canonical.get(field), list):
                errors.append(f"canonical_analysis.{field} must be a list")
        if not isinstance(canonical.get("coverage"), Mapping):
            errors.append("canonical_analysis.coverage must be an object")
    for field in ("analysis_report_sha256",):
        if not isinstance(provenance.get(field), str) or not SHA256.fullmatch(provenance[field]):
            errors.append(f"provenance.{field} must be SHA-256")
    if provenance.get("planning_sha256") is not None and not SHA256.fullmatch(str(provenance["planning_sha256"])):
        errors.append("provenance.planning_sha256 must be SHA-256")
    digest = value.get("publication_sha256")
    if not isinstance(digest, str) or not SHA256.fullmatch(digest) or artifact_sha256(_publication_unsigned(value)) != digest:
        errors.append("publication_sha256 does not match publication")
    if not allow_legacy_replay and check.get("publication_sha256") != digest:
        errors.append("check.publication_sha256 must match publication_sha256")
    if not allow_legacy_replay and comment.get("publication_sha256") != digest:
        errors.append("comment.publication_sha256 must match publication_sha256")
    return errors


def _paged(publisher: GitHubPublisher, endpoint: str, *, key: str | None = None) -> list[Mapping[str, Any]]:
    rows: list[Mapping[str, Any]] = []
    previous_page_digest: str | None = None
    for page in range(1, 1_001):
        separator = "&" if "?" in endpoint else "?"
        response = publisher.request("GET", f"{endpoint}{separator}per_page=100&page={page}")
        if key:
            if not isinstance(response, Mapping) or key not in response:
                raise ValueError("GitHub pagination response is malformed")
            value: Any = response[key]
        else:
            value = response
        if not isinstance(value, list) or any(not isinstance(row, Mapping) for row in value):
            raise ValueError("GitHub pagination response is malformed")
        page_digest = artifact_sha256(value)
        if previous_page_digest == page_digest and value:
            raise ValueError("GitHub pagination made no progress")
        previous_page_digest = page_digest
        rows.extend(value)
        if len(value) < 100:
            return rows
    raise ValueError("GitHub pagination exceeded the safety bound")


def _response_id(value: Any, label: str) -> Any:
    if not isinstance(value, Mapping):
        raise TypeError(f"GitHub {label} response is malformed")
    identifier = value.get("id")
    if not ((isinstance(identifier, int) and not isinstance(identifier, bool) and identifier > 0) or (isinstance(identifier, str) and identifier.strip())):
        raise ValueError(f"GitHub {label} response has no valid id")
    return identifier


def publish_github(
    publication: Mapping[str, Any], publisher: GitHubPublisher
) -> dict[str, Any]:
    """Create/update one check and one marker-bound PR comment idempotently."""

    publication_errors = validate_publication(publication)
    if publication_errors:
        raise ValueError("invalid publication: " + "; ".join(publication_errors))

    source = publication["source"]
    repository = source["repository"]
    head = source["head_revision"]
    check = dict(publication["check"])
    check.pop("publication_sha256", None)
    # Revalidate immediately before the first external request.
    publication_errors = validate_publication(publication)
    if publication_errors:
        raise ValueError("invalid publication: " + "; ".join(publication_errors))
    rows = _paged(
        publisher, f"repos/{repository}/commits/{head}/check-runs", key="check_runs"
    )
    comments = _paged(
        publisher, f"repos/{repository}/issues/{source['pr_number']}/comments"
    )
    comment_matches = [
        row
        for row in comments
        if isinstance(row, Mapping)
        and publication["comment"]["marker"] in str(row.get("body", ""))
    ]
    if len(comment_matches) > 1:
        raise ValueError("multiple Greenfield PR comments contain the canonical marker")
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
        _response_id(matches[0], "existing check")
        update = {
            key: check[key]
            for key in ("name", "status", "conclusion", "output")
            if key in check
        }
        check_result = publisher.request(
            "PATCH", f"repos/{repository}/check-runs/{matches[0]['id']}", update
        )
        _response_id(check_result, "check update")
        check_status = "updated"
    else:
        check_result = publisher.request(
            "POST", f"repos/{repository}/check-runs", check
        )
        _response_id(check_result, "check create")
        check_status = "created"

    body = {"body": publication["comment"]["body"]}
    if comment_matches:
        _response_id(comment_matches[0], "existing comment")
        comment_result = publisher.request(
            "PATCH",
            f"repos/{repository}/issues/comments/{comment_matches[0]['id']}",
            body,
        )
        _response_id(comment_result, "comment update")
        comment_status = "updated"
    else:
        comment_result = publisher.request(
            "POST", f"repos/{repository}/issues/{source['pr_number']}/comments", body
        )
        _response_id(comment_result, "comment create")
        comment_status = "created"
    return {
        "status": "published",
        "check": {"status": check_status, "id": _response_id(check_result, "check")},
        "comment": {"status": comment_status, "id": _response_id(comment_result, "comment")},
        "publication_sha256": publication["publication_sha256"],
    }


__all__ = [
    "CHECK_NAME",
    "COMMENT_MARKER",
    "build_publication",
    "publish_github",
    "validate_publication",
]
