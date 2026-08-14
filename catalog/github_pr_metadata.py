"""Read-only GitHub pull-request metadata intake."""

from __future__ import annotations

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
) -> dict[str, Any]:
    required = ("number", "html_url", "base", "head")
    if any(key not in pull_request for key in required):
        raise GitHubPrMetadataError("pull request response is missing required fields")
    base = _required_mapping(pull_request["base"], "pull_request.base")
    head = _required_mapping(pull_request["head"], "pull_request.head")
    for key, value in (("base.sha", base.get("sha")), ("head.sha", head.get("sha"))):
        if not isinstance(value, str) or not value:
            raise GitHubPrMetadataError(f"pull request response is missing {key}")
    return {
        "schema_version": "0.1",
        "analysis_kind": "pr_impact_metadata",
        "repo_key": repo_key,
        "repository": repository,
        "pull_request": {
            "number": pull_request["number"],
            "url": pull_request["html_url"],
            "title": pull_request.get("title"),
            "state": pull_request.get("state"),
            "draft": pull_request.get("draft"),
            "merged": pull_request.get("merged"),
            "mergeable": pull_request.get("mergeable"),
            "mergeable_state": pull_request.get("mergeable_state"),
            "base_revision": base["sha"],
            "target_revision": head["sha"],
            "base_branch": base.get("ref"),
            "target_branch": head.get("ref"),
            "labels": [
                label.get("name")
                for label in pull_request.get("labels", [])
                if isinstance(label, dict)
            ],
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
            for item in files
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
            for item in reviews
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
            for item in inline_comments
        ],
        "issue_comments": [
            _record(
                item, ("id", "user", "created_at", "updated_at", "html_url", "body")
            )
            for item in issue_comments
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
            for item in check_runs
        ],
        "provenance": {
            "provider": provider,
            "endpoints": endpoints,
            "fetched_at": datetime.now(UTC).isoformat(),
        },
    }


def fetch_pr_metadata(
    *,
    repo_key: str,
    manifest_path: str | Path,
    pr_number: int,
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
    checks_endpoint = f"repos/{repository}/commits/{target}/check-runs"
    check_runs, checks_provider = _provider_call(
        checks_endpoint, collection=True, collection_key="check_runs"
    )
    providers = {
        provider,
        files_provider,
        reviews_provider,
        comments_provider,
        issue_provider,
        checks_provider,
    }
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
        endpoints=endpoints + [checks_endpoint],
    )
