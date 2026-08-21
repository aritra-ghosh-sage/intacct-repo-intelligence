"""Bounded, read-only related pull-request evidence production."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from greenfield.artifact_io import artifact_sha256


class RelatedPrError(ValueError):
    """Related PR evidence could not be produced safely."""


Provider = Callable[[str], Any]
SHA = re.compile(r"^[0-9a-f]{40}$")


def _repository_from_issue(issue: Mapping[str, Any]) -> str | None:
    repository_url = issue.get("repository_url")
    if isinstance(repository_url, str) and "/repos/" in repository_url:
        return repository_url.split("/repos/", 1)[1].strip("/") or None
    html_url = issue.get("html_url")
    if isinstance(html_url, str) and "/issues/" in html_url:
        return html_url.split("github.com/", 1)[-1].split("/issues/", 1)[0]
    return None


def _related_issue(event: Mapping[str, Any]) -> tuple[str, int] | None:
    if event.get("event") != "cross-referenced":
        return None
    source = event.get("source")
    issue = source.get("issue") if isinstance(source, Mapping) else None
    if not isinstance(issue, Mapping):
        return None
    repository = _repository_from_issue(issue)
    number = issue.get("number")
    pull_request = issue.get("pull_request")
    if not repository or not isinstance(number, int) or not isinstance(pull_request, Mapping):
        return None
    return repository, number


def build_related_pr_evidence(
    *,
    source_repository: str,
    canonical_repository: str,
    source_pr_number: int,
    source_revision: str,
    candidate_repositories: Iterable[str],
    timeline_events: Iterable[Mapping[str, Any]],
    pull_requests: Mapping[tuple[str, int], Mapping[str, Any]],
    evidence_path: str,
) -> dict[str, Any]:
    if not isinstance(source_repository, str) or not source_repository.strip():
        raise RelatedPrError("source_repository is required")
    if not isinstance(canonical_repository, str) or not canonical_repository.strip():
        raise RelatedPrError("canonical_repository is required")
    if not isinstance(source_pr_number, int) or source_pr_number < 1:
        raise RelatedPrError("source_pr_number must be positive")
    if not isinstance(source_revision, str) or not SHA.fullmatch(source_revision.lower()):
        raise RelatedPrError("source_revision must be a lowercase 40-character SHA")
    allowed = {str(value) for value in candidate_repositories}
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for event in timeline_events:
        related = _related_issue(event)
        if related is None:
            continue
        repository, number = related
        if repository not in allowed or related in seen:
            continue
        pull_request = pull_requests.get(related)
        if not isinstance(pull_request, Mapping):
            continue
        state = pull_request.get("state")
        if state == "open":
            normalized_state = "open"
        elif state == "closed" and pull_request.get("merged_at"):
            normalized_state = "merged"
        else:
            continue
        head = pull_request.get("head")
        base = pull_request.get("base")
        head_sha = head.get("sha") if isinstance(head, Mapping) else None
        base_sha = base.get("sha") if isinstance(base, Mapping) else None
        if (
            not isinstance(head_sha, str)
            or not isinstance(base_sha, str)
            or not SHA.fullmatch(head_sha.lower())
            or not SHA.fullmatch(base_sha.lower())
        ):
            continue
        event_id = event.get("id")
        evidence_id = f"github_timeline:{event_id}" if event_id is not None else f"github_cross_reference:{repository}:{number}"
        evidence_payload = {
            "timeline_event": dict(event),
            "pull_request": dict(pull_request),
        }
        rows.append(
            {
                "repository": repository,
                "number": number,
                "state": normalized_state,
                "head_sha": head_sha.lower(),
                "base_sha": base_sha.lower(),
                "relation_type": "github_cross_reference",
                "evidence": {
                    "id": evidence_id,
                    "payload": evidence_payload,
                    "sha256": artifact_sha256(evidence_payload),
                },
            }
        )
        seen.add(related)
    rows.sort(key=lambda row: (row["repository"], row["number"], row["state"]))
    body: dict[str, Any] = {
        "schema_version": "0.1",
        "evidence_type": "related_pull_requests",
        "status": "available" if rows else "empty",
        "source_repository": source_repository,
        "canonical_source_repository": canonical_repository,
        "source_revision": source_revision.lower(),
        "source_pr_number": source_pr_number,
        "pull_requests": rows,
        "evidence_path": evidence_path,
    }
    body["artifact_sha256"] = artifact_sha256(body)
    return body
