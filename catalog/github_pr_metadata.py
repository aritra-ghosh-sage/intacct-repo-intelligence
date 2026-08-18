"""Read-only GitHub pull-request metadata intake."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from catalog.pr_impact_manifest import resolve_manifest_repo_root
from catalog.repository_lifecycle import normalized_github_identity

_GITHUB_OPERATION_TIMEOUT_SECONDS = 120
METADATA_SCHEMA_VERSION = "0.2"
_OPTIONAL_COLLECTION_STATUSES = {"available", "empty", "unavailable", "not_requested"}


class GitHubPrMetadataError(RuntimeError):
    """PR metadata could not be fetched or normalized safely."""


def _json_bytes(raw: bytes, context: str) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise GitHubPrMetadataError(
            f"github metadata response is not JSON: {context}"
        ) from exc


def _flatten_pages(value: Any, context: str) -> list[Any]:
    if isinstance(value, list):
        flattened: list[Any] = []
        for page in value:
            if not isinstance(page, list):
                raise GitHubPrMetadataError(
                    f"github metadata page is not an array: {context}"
                )
            flattened.extend(page)
        return flattened
    raise GitHubPrMetadataError(
        f"github metadata collection is not an array: {context}"
    )


def _flatten_keyed_pages(value: Any, key: str, context: str) -> list[Any]:
    if not isinstance(value, list):
        raise GitHubPrMetadataError(
            f"github metadata pages are not an array: {context}"
        )
    flattened: list[Any] = []
    for page in value:
        if not isinstance(page, dict) or not isinstance(page.get(key), list):
            raise GitHubPrMetadataError(
                f"github metadata page is missing array {key}: {context}"
            )
        flattened.extend(page[key])
    return flattened


def _gh_json(
    endpoint: str, *, collection: bool, collection_key: str | None = None
) -> Any:
    if shutil.which("gh") is None:
        raise GitHubPrMetadataError("gh executable is unavailable")
    args = ["gh", "api", "--hostname", "github.com", endpoint]
    if collection:
        args.extend(["--paginate", "--slurp"])
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            check=False,
            timeout=_GITHUB_OPERATION_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitHubPrMetadataError(
            "gh api timed out after "
            f"{_GITHUB_OPERATION_TIMEOUT_SECONDS} seconds for {endpoint}; "
            "verify GitHub access and retry"
        ) from exc
    except OSError as exc:
        raise GitHubPrMetadataError(f"gh api could not run: {exc}") from exc
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise GitHubPrMetadataError(f"gh api failed: {detail or 'command failed'}")
    value = _json_bytes(result.stdout, endpoint)
    if not collection:
        return value
    return (
        _flatten_keyed_pages(value, collection_key, endpoint)
        if collection_key
        else _flatten_pages(value, endpoint)
    )


def _http_json(
    endpoint: str, *, token: str, collection: bool, collection_key: str | None = None
) -> Any:
    url = f"https://api.github.com/{endpoint.lstrip('/')}"
    values: list[Any] = []
    while url:
        request = Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Authorization": f"Bearer {token}",
                "User-Agent": "intacct-repo-intelligence",
            },
        )
        try:
            with urlopen(
                request, timeout=_GITHUB_OPERATION_TIMEOUT_SECONDS
            ) as response:
                raw = response.read()
                next_url = response.headers.get("Link", "")
        except TimeoutError as exc:
            raise GitHubPrMetadataError(
                "GitHub HTTP API timed out after "
                f"{_GITHUB_OPERATION_TIMEOUT_SECONDS} seconds for {endpoint}; "
                "verify GitHub access and retry"
            ) from exc
        except (HTTPError, URLError, OSError) as exc:
            raise GitHubPrMetadataError(f"GitHub HTTP API failed: {endpoint}") from exc
        value = _json_bytes(raw, endpoint)
        if not collection:
            return value
        if collection_key:
            if not isinstance(value, dict) or not isinstance(
                value.get(collection_key), list
            ):
                raise GitHubPrMetadataError(
                    f"GitHub HTTP collection is missing array {collection_key}: {endpoint}"
                )
            values.extend(value[collection_key])
        else:
            if not isinstance(value, list):
                raise GitHubPrMetadataError(
                    f"GitHub HTTP collection is not an array: {endpoint}"
                )
            values.extend(value)
        url = ""
        for link in next_url.split(","):
            if 'rel="next"' in link and "<" in link and ">" in link:
                url = urljoin(
                    "https://api.github.com/", link.split("<", 1)[1].split(">", 1)[0]
                )
                break
    return values


def _provider_call(
    endpoint: str, *, collection: bool, collection_key: str | None = None
) -> tuple[Any, str]:
    gh_error: Exception | None = None
    if shutil.which("gh") is not None:
        try:
            return _gh_json(
                endpoint, collection=collection, collection_key=collection_key
            ), "gh_api"
        except GitHubPrMetadataError as exc:
            gh_error = exc
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        detail = f"; gh error: {gh_error}" if gh_error else ""
        raise GitHubPrMetadataError(f"no GitHub provider is available{detail}")
    try:
        return _http_json(
            endpoint, token=token, collection=collection, collection_key=collection_key
        ), "github_http_api"
    except GitHubPrMetadataError as exc:
        detail = f"; gh error: {gh_error}" if gh_error else ""
        raise GitHubPrMetadataError(f"GitHub providers failed: {exc}{detail}") from exc


def _required_mapping(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise GitHubPrMetadataError(
            f"required GitHub response is not an object: {context}"
        )
    return value


def _record(value: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: value[key] for key in keys if key in value}


def _sorted_records(
    values: list[dict[str, Any]], keys: tuple[str, ...]
) -> list[dict[str, Any]]:
    return sorted(
        values,
        key=lambda value: tuple(str(value.get(key, "")) for key in keys),
    )


def _collection_status(values: list[Any], requested: bool = True) -> str:
    if not requested:
        return "not_requested"
    return "available" if values else "empty"


def evidence_fingerprint(value: dict[str, Any]) -> str:
    """Hash evidence while excluding fetch time and the stored hash itself."""

    canonical = json.loads(json.dumps(value))
    provenance = canonical.get("provenance")
    if isinstance(provenance, dict):
        provenance.pop("fetched_at", None)
        provenance.pop("evidence_sha256", None)
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _with_evidence_fingerprint(value: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(value))
    provenance = result.setdefault("provenance", {})
    provenance["evidence_sha256"] = evidence_fingerprint(result)
    return result


def _repository_from_issue(issue: dict[str, Any]) -> str | None:
    repository_url = issue.get("repository_url")
    if isinstance(repository_url, str) and "/repos/" in repository_url:
        return repository_url.split("/repos/", 1)[1].strip("/") or None
    html_url = issue.get("html_url")
    if isinstance(html_url, str) and "/issues/" in html_url:
        return html_url.split("github.com/", 1)[-1].split("/issues/", 1)[0]
    return None


def _normalize_linked_issues(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for event in events:
        if event.get("event") != "cross-referenced":
            continue
        source = event.get("source")
        issue = source.get("issue") if isinstance(source, dict) else None
        if not isinstance(issue, dict):
            continue
        record = {
            "event_id": event.get("id"),
            "event_url": event.get("url"),
            "relation": "cross_referenced",
            "created_at": event.get("created_at"),
            "repository": _repository_from_issue(issue),
            "number": issue.get("number"),
            "url": issue.get("html_url"),
            "title": issue.get("title"),
            "state": issue.get("state"),
        }
        records.append(
            {key: value for key, value in record.items() if value is not None}
        )
    return _sorted_records(records, ("repository", "number", "relation", "event_id"))


def normalize_pr_metadata(
    *,
    repository: str,
    repo_key: str,
    pull_request: dict[str, Any],
    files: list[dict[str, Any]],
    reviews: list[dict[str, Any]],
    inline_comments: list[dict[str, Any]],
    issue_comments: list[dict[str, Any]],
    check_runs: list[dict[str, Any]],
    provider: str,
    endpoints: list[str],
    linked_issues: list[dict[str, Any]] | None = None,
    workflow_runs: list[dict[str, Any]] | None = None,
    workflow_jobs: list[dict[str, Any]] | None = None,
    evidence_status: dict[str, str] | None = None,
    collection_errors: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    required = ("number", "html_url", "base", "head")
    if any(key not in pull_request for key in required):
        raise GitHubPrMetadataError("pull request response is missing required fields")
    base = _required_mapping(pull_request["base"], "pull_request.base")
    head = _required_mapping(pull_request["head"], "pull_request.head")
    for key, value in (("base.sha", base.get("sha")), ("head.sha", head.get("sha"))):
        if not isinstance(value, str) or not value:
            raise GitHubPrMetadataError(f"pull request response is missing {key}")
    linked_issues = linked_issues or []
    workflow_runs = workflow_runs or []
    workflow_jobs = workflow_jobs or []
    statuses = {
        "linked_issues": _collection_status(linked_issues),
        "workflow_runs": _collection_status(workflow_runs),
        "workflow_jobs": _collection_status(workflow_jobs),
        "check_runs": _collection_status(check_runs),
    }
    if evidence_status:
        statuses.update(evidence_status)
    invalid_statuses = set(statuses.values()) - _OPTIONAL_COLLECTION_STATUSES
    if invalid_statuses:
        raise GitHubPrMetadataError(
            "invalid federation evidence status: " + ", ".join(sorted(invalid_statuses))
        )
    result = {
        "schema_version": METADATA_SCHEMA_VERSION,
        "analysis_kind": "pr_impact_metadata",
        "repo_key": repo_key,
        "repository": repository,
        "pull_request": {
            "number": pull_request["number"],
            "id": pull_request.get("id"),
            "node_id": pull_request.get("node_id"),
            "url": pull_request["html_url"],
            "title": pull_request.get("title"),
            "body": pull_request.get("body"),
            "state": pull_request.get("state"),
            "draft": pull_request.get("draft"),
            "merged": pull_request.get("merged"),
            "mergeable": pull_request.get("mergeable"),
            "mergeable_state": pull_request.get("mergeable_state"),
            "base_revision": base["sha"],
            "target_revision": head["sha"],
            "base_branch": base.get("ref"),
            "target_branch": head.get("ref"),
            "author": pull_request.get("user"),
            "created_at": pull_request.get("created_at"),
            "updated_at": pull_request.get("updated_at"),
            "closed_at": pull_request.get("closed_at"),
            "merged_at": pull_request.get("merged_at"),
            "merge_commit_sha": pull_request.get("merge_commit_sha"),
            "labels": sorted(
                label.get("name")
                for label in pull_request.get("labels", [])
                if isinstance(label, dict) and isinstance(label.get("name"), str)
            ),
        },
        "changed_files": [
            _record(
                item,
                (
                    "filename",
                    "status",
                    "previous_filename",
                    "additions",
                    "deletions",
                    "changes",
                    "blob_url",
                    "contents_url",
                ),
            )
            for item in _sorted_records(
                files, ("filename", "status", "previous_filename")
            )
        ],
        "reviews": [
            _record(
                item,
                (
                    "id",
                    "user",
                    "state",
                    "submitted_at",
                    "commit_id",
                    "html_url",
                    "body",
                ),
            )
            for item in _sorted_records(reviews, ("id", "submitted_at", "commit_id"))
        ],
        "inline_comments": [
            _record(
                item,
                (
                    "id",
                    "path",
                    "line",
                    "start_line",
                    "side",
                    "body",
                    "user",
                    "created_at",
                    "updated_at",
                    "commit_id",
                    "html_url",
                    "diff_hunk",
                ),
            )
            for item in _sorted_records(inline_comments, ("path", "line", "id"))
        ],
        "issue_comments": [
            _record(
                item, ("id", "user", "created_at", "updated_at", "html_url", "body")
            )
            for item in _sorted_records(issue_comments, ("id", "created_at"))
        ],
        "check_runs": [
            _record(
                item,
                (
                    "id",
                    "name",
                    "status",
                    "conclusion",
                    "started_at",
                    "completed_at",
                    "html_url",
                    "head_sha",
                ),
            )
            for item in _sorted_records(check_runs, ("name", "id", "head_sha"))
        ],
        "linked_issues": _sorted_records(
            linked_issues, ("repository", "number", "relation", "event_id")
        ),
        "workflow_runs": _sorted_records(
            workflow_runs, ("id", "workflow_id", "head_sha")
        ),
        "workflow_jobs": _sorted_records(
            workflow_jobs, ("workflow_run_id", "id", "name")
        ),
        "evidence_status": statuses,
        "provenance": {
            "provider": provider,
            "endpoints": endpoints,
            "fetched_at": datetime.now(UTC).isoformat(),
            "collection_errors": collection_errors or [],
        },
    }
    return _with_evidence_fingerprint(result)


def _optional_provider_call(
    endpoint: str, *, collection_key: str | None = None
) -> tuple[list[dict[str, Any]], str, str | None, str | None]:
    try:
        value, provider = _provider_call(
            endpoint, collection=True, collection_key=collection_key
        )
        if not isinstance(value, list):
            raise GitHubPrMetadataError("provider collection is not a list")
        return value, provider, "available" if value else "empty", None
    except GitHubPrMetadataError as exc:
        return [], "unavailable", "unavailable", str(exc)


def fetch_pr_metadata(
    *,
    repo_key: str,
    manifest_path: str | Path,
    pr_number: int,
    include_check_runs: bool = True,
) -> dict[str, Any]:
    if isinstance(pr_number, bool) or not isinstance(pr_number, int) or pr_number <= 0:
        raise GitHubPrMetadataError("PR number must be a positive integer")
    try:
        resolve_manifest_repo_root(manifest_path, repo_key)
    except ValueError as exc:
        raise GitHubPrMetadataError(str(exc)) from exc
    from catalog.repositories import load_workspace_manifest

    manifest = load_workspace_manifest(Path(manifest_path))
    entry = next(
        (item for item in manifest["repositories"] if item.get("repo_key") == repo_key),
        None,
    )
    if entry is None:
        raise GitHubPrMetadataError(f"repository not found in manifest: {repo_key}")
    identity = normalized_github_identity(entry.get("remote_url"))
    if identity is None:
        raise GitHubPrMetadataError("manifest repository is not a GitHub repository")
    owner, repo = identity
    repository = f"{owner}/{repo}"
    base = f"repos/{repository}/pulls/{pr_number}"
    endpoints = [
        base,
        f"{base}/files",
        f"{base}/reviews",
        f"{base}/comments",
        f"repos/{repository}/issues/{pr_number}/comments",
    ]
    pull_request, provider = _provider_call(base, collection=False)
    files, files_provider = _provider_call(endpoints[1], collection=True)
    reviews, reviews_provider = _provider_call(endpoints[2], collection=True)
    inline_comments, comments_provider = _provider_call(endpoints[3], collection=True)
    issue_comments, issue_provider = _provider_call(endpoints[4], collection=True)
    target = _required_mapping(pull_request, "pull_request").get("head", {}).get("sha")
    if not isinstance(target, str) or not target:
        raise GitHubPrMetadataError("pull request target revision is missing")
    timeline_endpoint = f"repos/{repository}/issues/{pr_number}/timeline"
    workflow_endpoint = f"repos/{repository}/actions/runs?head_sha={target}"
    linked_events, timeline_provider, linked_status, linked_error = (
        _optional_provider_call(timeline_endpoint)
    )
    workflow_runs_raw, workflow_provider, workflow_status, workflow_error = (
        _optional_provider_call(workflow_endpoint, collection_key="workflow_runs")
    )
    check_runs: list[dict[str, Any]] = []
    checks_endpoint: str | None = None
    checks_provider: str | None = None
    check_status = "not_requested"
    check_error: str | None = None
    if include_check_runs:
        checks_endpoint = f"repos/{repository}/commits/{target}/check-runs"
        check_runs, checks_provider, check_status, check_error = (
            _optional_provider_call(checks_endpoint, collection_key="check_runs")
        )
    workflow_runs = [
        _record(
            item,
            (
                "id",
                "name",
                "workflow_id",
                "run_number",
                "run_attempt",
                "event",
                "status",
                "conclusion",
                "head_sha",
                "html_url",
                "created_at",
                "updated_at",
                "run_started_at",
            ),
        )
        for item in workflow_runs_raw
        if isinstance(item, dict)
    ]
    workflow_jobs: list[dict[str, Any]] = []
    job_status = (
        "unavailable"
        if workflow_status == "unavailable"
        else "empty"
        if not workflow_runs
        else "available"
    )
    job_errors: list[dict[str, str]] = []
    job_providers: set[str] = set()
    job_endpoints: list[str] = []
    for run in workflow_runs:
        run_id = run.get("id")
        if not isinstance(run_id, int) or isinstance(run_id, bool):
            continue
        jobs_endpoint = f"repos/{repository}/actions/runs/{run_id}/jobs"
        job_endpoints.append(jobs_endpoint)
        jobs, job_provider, run_job_status, run_job_error = _optional_provider_call(
            jobs_endpoint, collection_key="jobs"
        )
        if job_provider != "unavailable":
            job_providers.add(job_provider)
        for item in jobs:
            if not isinstance(item, dict):
                continue
            row = _record(
                item,
                (
                    "id",
                    "name",
                    "status",
                    "conclusion",
                    "started_at",
                    "completed_at",
                    "html_url",
                ),
            )
            row["workflow_run_id"] = run_id
            workflow_jobs.append(row)
        if run_job_status == "unavailable":
            job_status = "unavailable"
            if run_job_error:
                job_errors.append(
                    {"collection": "workflow_jobs", "error": run_job_error}
                )
    if workflow_runs and job_status != "unavailable":
        job_status = "available" if workflow_jobs else "empty"
    collection_errors = [
        {"collection": collection, "error": error}
        for collection, error in (
            ("linked_issues", linked_error),
            ("workflow_runs", workflow_error),
            ("check_runs", check_error),
        )
        if error
    ] + job_errors
    providers = {
        provider,
        files_provider,
        reviews_provider,
        comments_provider,
        issue_provider,
        timeline_provider,
        workflow_provider,
        *job_providers,
    }
    if checks_provider is not None:
        providers.add(checks_provider)
    if len(providers) != 1:
        provider = "mixed:" + ",".join(sorted(providers))
    return normalize_pr_metadata(
        repository=repository,
        repo_key=repo_key,
        pull_request=_required_mapping(pull_request, "pull_request"),
        files=files,
        reviews=reviews,
        inline_comments=inline_comments,
        issue_comments=issue_comments,
        check_runs=check_runs,
        provider=provider,
        endpoints=(
            endpoints
            + [timeline_endpoint, workflow_endpoint]
            + ([checks_endpoint] if checks_endpoint else [])
            + job_endpoints
        ),
        linked_issues=_normalize_linked_issues(linked_events),
        workflow_runs=workflow_runs,
        workflow_jobs=workflow_jobs,
        evidence_status={
            "linked_issues": linked_status,
            "workflow_runs": workflow_status,
            "workflow_jobs": job_status,
            "check_runs": check_status,
        },
        collection_errors=collection_errors,
    )
