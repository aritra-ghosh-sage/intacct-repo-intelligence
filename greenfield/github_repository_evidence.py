"""Read-only GitHub repository and workflow inventory evidence.

This adapter deliberately distinguishes repository inventory from executed CI
evidence. It can identify workflow/test surfaces, but it never claims that a
test ran unless a separately normalized CI artifact proves that fact.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import subprocess
from collections.abc import Callable
from typing import Any

SCHEMA_VERSION = "0.1"
TARGET_EVIDENCE_SCHEMA_VERSION = "0.1"
_TEST_EXECUTION = re.compile(
    r"(?:\bpytest\b|mvn\s+(?:test|verify)(?![-\w])|gradle\s+(?:test|check)(?![-\w])|npm\s+test\b|yarn\s+test\b|go\s+test\b|dotnet\s+test\b|\bctest\b)",
    re.IGNORECASE,
)
_TEST_PREPARATION = re.compile(
    r"(?:\b(?:mvn|mvnw)\s+test-compile\b|\bgradle\s+compileTest\w*\b|\bnpm\s+install\b)",
    re.IGNORECASE,
)
_INVENTORY_PATH = re.compile(
    r"(?:^|/)(?:features?|tests?|testdefinitions|testscripts|specs?)(?:/|$)|\.(?:feature|feature\.xml|jmx|test\.xml)$",
    re.IGNORECASE,
)
_MAX_COLLECTION_PAGES = 5


class RepositoryEvidenceError(RuntimeError):
    """GitHub repository evidence could not be fetched or normalized."""


Provider = Callable[[str], Any]


def classify_ci_execution(*, workflow_runs: list[Any], workflow_jobs: list[Any]) -> dict[str, Any]:
    """Classify execution without mistaking skipped/control work for test coverage."""

    jobs = [row for row in workflow_jobs if isinstance(row, dict)]
    text = "\n".join(str(row.get("name", "")) + " " + str(row.get("conclusion", "")) for row in jobs).lower()
    test_jobs = [row for row in jobs if _TEST_EXECUTION.search(str(row.get("name", "")))]
    if test_jobs:
        conclusions = {str(row.get("conclusion", "")).lower() for row in test_jobs}
        if conclusions & {"failure", "cancelled", "timed_out", "action_required"}:
            status = "executed_failed"
        elif conclusions and conclusions <= {"success", "neutral"}:
            status = "executed_passed"
        else:
            status = "not_run"
    elif "pending review" in text or "pending reviews" in text or "approval" in text:
        status = "workflow_control_only"
    elif jobs or workflow_runs:
        status = "not_run"
    else:
        status = "execution_unavailable"
    return {"execution_status": status, "test_job_count": len(test_jobs), "workflow_job_count": len(jobs)}


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: object) -> str:
    payload = value if isinstance(value, bytes) else _canonical(value).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _gh_provider(endpoint: str) -> Any:
    command = ["gh", "api", "--hostname", "github.com", endpoint]
    try:
        result = subprocess.run(command, capture_output=True, check=False, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RepositoryEvidenceError(f"github_provider_unavailable: {exc}") from exc
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RepositoryEvidenceError(f"github_api_failed: {detail or endpoint}")
    try:
        return json.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RepositoryEvidenceError(f"github_response_invalid: {endpoint}") from exc


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RepositoryEvidenceError(f"{label} must be an object")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise RepositoryEvidenceError(f"{label} must be a list")
    return value


def _workflow_classification(text: str) -> dict[str, Any]:
    has_test_execution = bool(_TEST_EXECUTION.search(text))
    has_test_preparation = bool(_TEST_PREPARATION.search(text))
    has_artifact_upload = "actions/upload-artifact@" in text
    if has_test_execution and has_artifact_upload:
        classification = "test_execution_with_artifact"
    elif has_test_execution:
        classification = "test_execution_without_artifact"
    elif has_test_preparation and has_artifact_upload:
        classification = "test_preparation_with_artifact"
    elif has_test_preparation:
        classification = "test_preparation_only"
    elif has_artifact_upload:
        classification = "artifact_without_test_execution"
    else:
        classification = "metadata_only"
    return {
        "classification": classification,
        "has_test_execution": has_test_execution,
        "has_test_preparation": has_test_preparation,
        "has_artifact_upload": has_artifact_upload,
    }


def _has_source_binding(
    value: Any, *, source_repository: str, source_revision: str
) -> bool:
    if not isinstance(value, dict):
        return False
    candidates = [value]
    for key in ("metadata", "workflow_run", "run", "check_run"):
        nested = value.get(key)
        if isinstance(nested, dict):
            candidates.append(nested)
    return any(
        candidate.get("source_repository") == source_repository
        and candidate.get("source_revision") == source_revision
        for candidate in candidates
    )


def _decode_content(value: MappingLike) -> bytes:
    content = value.get("content")
    if not isinstance(content, str):
        raise RepositoryEvidenceError("workflow content is missing")
    try:
        return base64.b64decode(content.replace("\n", ""), validate=False)
    except (ValueError, TypeError) as exc:
        raise RepositoryEvidenceError("workflow content is not base64") from exc


class MappingLike(dict[str, Any]):
    """Typing helper for provider response objects."""


def _scoped_tree_entries(
    call: Provider,
    *,
    repository: str,
    revision: str,
    roots: list[str],
    endpoints: list[str],
) -> tuple[list[Any], list[str]]:
    """Enumerate only declared inventory roots when a recursive root tree truncates."""

    errors: list[str] = []
    root_endpoint = f"repos/{repository}/git/trees/{revision}"
    root = _object(call(root_endpoint), root_endpoint)
    endpoints.append(root_endpoint)
    current = _list(root.get("tree"), f"{root_endpoint}.tree")
    result: list[Any] = []
    for configured_root in sorted(set(roots)):
        parts = [part for part in configured_root.split("/") if part]
        entries = current
        tree_sha: str | None = None
        for part in parts:
            match = next(
                (
                    row
                    for row in entries
                    if isinstance(row, dict)
                    and row.get("path") == part
                    and row.get("type") == "tree"
                    and isinstance(row.get("sha"), str)
                ),
                None,
            )
            if match is None:
                tree_sha = None
                break
            tree_sha = str(match["sha"])
            endpoint = f"repos/{repository}/git/trees/{tree_sha}"
            subtree = _object(call(endpoint), endpoint)
            endpoints.append(endpoint)
            entries = _list(subtree.get("tree"), f"{endpoint}.tree")
        if tree_sha is None:
            continue
        endpoint = f"repos/{repository}/git/trees/{tree_sha}?recursive=1"
        subtree = _object(call(endpoint), endpoint)
        endpoints.append(endpoint)
        if subtree.get("truncated") is True:
            errors.append(f"{endpoint}: response_truncated")
            continue
        for row in _list(subtree.get("tree"), f"{endpoint}.tree"):
            if isinstance(row, dict) and isinstance(row.get("path"), str):
                result.append({**row, "path": f"{configured_root}/{row['path']}"})
    return result, errors


def collect_repository_evidence(
    repository: str,
    *,
    source_repository: str,
    source_revision: str,
    test_roots: list[str] | None = None,
    provider: Provider | None = None,
) -> dict[str, Any]:
    """Collect a pinned repository/workflow inventory without mutating GitHub."""

    call = provider or _gh_provider
    endpoints: list[str] = []
    try:
        repo_endpoint = f"repos/{repository}"
        repo = _object(call(repo_endpoint), repo_endpoint)
        endpoints.append(repo_endpoint)
        default_branch = repo.get("default_branch")
        if not isinstance(default_branch, str) or not default_branch:
            raise RepositoryEvidenceError("repository has no default branch")

        ref_endpoint = f"repos/{repository}/git/ref/heads/{default_branch}"
        ref = _object(call(ref_endpoint), ref_endpoint)
        endpoints.append(ref_endpoint)
        ref_object = _object(ref.get("object"), f"{ref_endpoint}.object")
        inspected_revision = ref_object.get("sha")
        if not isinstance(inspected_revision, str) or not inspected_revision:
            raise RepositoryEvidenceError("default branch SHA is missing")

        tree_endpoint = f"repos/{repository}/git/trees/{inspected_revision}?recursive=1"
        tree = _object(call(tree_endpoint), tree_endpoint)
        endpoints.append(tree_endpoint)
        tree_entries = _list(tree.get("tree"), f"{tree_endpoint}.tree")
        collection_errors: list[str] = []
        if tree.get("truncated") is True:
            collection_errors.append(f"{tree_endpoint}: response_truncated")
            if test_roots:
                try:
                    scoped_entries, scoped_errors = _scoped_tree_entries(
                        call,
                        repository=repository,
                        revision=inspected_revision,
                        roots=[".github/workflows", *test_roots],
                        endpoints=endpoints,
                    )
                    collection_errors.extend(scoped_errors)
                    if scoped_entries:
                        tree_entries = scoped_entries
                except RepositoryEvidenceError as exc:
                    collection_errors.append(f"scoped_tree_fallback_unavailable: {exc}")
        workflow_paths = sorted(
            str(item["path"])
            for item in tree_entries
            if isinstance(item, dict)
            and item.get("type") == "blob"
            and isinstance(item.get("path"), str)
            and item["path"].startswith(".github/workflows/")
        )
        inventory_paths = sorted(
            str(item["path"])
            for item in tree_entries
            if isinstance(item, dict)
            and item.get("type") == "blob"
            and isinstance(item.get("path"), str)
            and _INVENTORY_PATH.search(item["path"])
        )

        workflows: list[dict[str, Any]] = []
        for path in workflow_paths:
            content_endpoint = (
                f"repos/{repository}/contents/{path}?ref={inspected_revision}"
            )
            content = _object(call(content_endpoint), content_endpoint)
            endpoints.append(content_endpoint)
            text = _decode_content(content).decode("utf-8", errors="replace")
            workflows.append(
                {
                    "path": path,
                    **_workflow_classification(text),
                    "source_sha256": _sha256(text.encode("utf-8")),
                }
            )

        runs_endpoint = f"repos/{repository}/actions/runs?head_sha={inspected_revision}&per_page=100"
        check_endpoint = (
            f"repos/{repository}/commits/{inspected_revision}/check-runs?per_page=100"
        )
        artifacts_endpoint = f"repos/{repository}/actions/artifacts?per_page=100"

        def optional_collection(endpoint: str, key: str) -> list[Any]:
            try:
                response = _object(call(endpoint), endpoint)
                endpoints.append(endpoint)
                rows = _list(response.get(key, []), f"{endpoint}.{key}")
                total = response.get("total_count")
                if isinstance(total, int) and total > len(rows):
                    for page in range(2, _MAX_COLLECTION_PAGES + 1):
                        page_endpoint = f"{endpoint}&page={page}"
                        endpoints.append(page_endpoint)
                        try:
                            page_response = _object(call(page_endpoint), page_endpoint)
                        except RepositoryEvidenceError as exc:
                            collection_errors.append(f"{page_endpoint}: {exc}")
                            collection_errors.append(f"{endpoint}: pagination_incomplete")
                            return rows
                        page_rows = _list(page_response.get(key, []), f"{page_endpoint}.{key}")
                        rows.extend(page_rows)
                        if len(rows) >= total or not page_rows:
                            break
                    if len(rows) < total:
                        collection_errors.append(f"{endpoint}: pagination_incomplete")
                return rows
            except RepositoryEvidenceError as exc:
                collection_errors.append(f"{endpoint}: {exc}")
                return []

        runs = optional_collection(runs_endpoint, "workflow_runs")
        checks = optional_collection(check_endpoint, "check_runs")
        artifacts = optional_collection(artifacts_endpoint, "artifacts")
        run_ids = {
            run.get("id")
            for run in runs
            if isinstance(run, dict)
            and isinstance(run.get("id"), int)
            and run.get("head_sha") == inspected_revision
        }
        source_bound_run_ids = {
            run.get("id")
            for run in runs
            if isinstance(run, dict)
            and run.get("id") in run_ids
            and _has_source_binding(
                run,
                source_repository=source_repository,
                source_revision=source_revision,
            )
        }
        source_bound_check_ids = {
            check.get("id")
            for check in checks
            if isinstance(check, dict)
            and isinstance(check.get("id"), int)
            and check.get("head_sha") == inspected_revision
            and _has_source_binding(
                check,
                source_repository=source_repository,
                source_revision=source_revision,
            )
        }
        workflow_jobs: list[dict[str, Any]] = []
        for run_id in sorted(run_ids):
            if not isinstance(run_id, int):
                continue
            jobs_endpoint = (
                f"repos/{repository}/actions/runs/{run_id}/jobs?per_page=100"
            )
            jobs = optional_collection(jobs_endpoint, "jobs")
            workflow_jobs.extend(job for job in jobs if isinstance(job, dict))
        linked_artifacts = [
            artifact
            for artifact in artifacts
            if isinstance(artifact, dict)
            and isinstance(artifact.get("workflow_run"), dict)
            and artifact["workflow_run"].get("id") in run_ids
        ]
        source_bound_artifacts = [
            artifact
            for artifact in linked_artifacts
            if artifact.get("workflow_run", {}).get("id") in source_bound_run_ids
            or _has_source_binding(
                artifact,
                source_repository=source_repository,
                source_revision=source_revision,
            )
        ]
        source_linked = bool(
            source_bound_run_ids or source_bound_check_ids or source_bound_artifacts
        )
        if source_bound_artifacts:
            artifact_status = "available"
        elif artifacts:
            artifact_status = "not_linked_to_source_revision"
        else:
            artifact_status = "empty"
        if not source_linked:
            collection_errors.append(
                "ci_linkage_unavailable:target_repository_has_no_source_revision"
            )
        return {
            "schema_version": SCHEMA_VERSION,
            "evidence_type": "repository_inventory",
            "repository": repository,
            "source_repository": source_repository,
            "source_revision": source_revision,
            "default_branch": default_branch,
            "inspected_revision": inspected_revision,
            "workflow_paths": workflow_paths,
            "inventory_paths": inventory_paths,
            "workflows": sorted(workflows, key=lambda item: item["path"]),
            "workflow_runs": sorted(
                [run for run in runs if isinstance(run, dict)],
                key=lambda item: (
                    str(item.get("id", "")),
                    str(item.get("head_sha", "")),
                ),
            ),
            "workflow_jobs": sorted(
                workflow_jobs,
                key=lambda item: (
                    str(item.get("run_id", "")),
                    str(item.get("id", "")),
                    str(item.get("name", "")),
                ),
            ),
            "check_runs": sorted(
                [check for check in checks if isinstance(check, dict)],
                key=lambda item: (str(item.get("name", "")), str(item.get("id", ""))),
            ),
            "artifacts": sorted(
                [
                    artifact
                    for artifact in linked_artifacts
                    if isinstance(artifact, dict)
                ],
                key=lambda item: (str(item.get("name", "")), str(item.get("id", ""))),
            ),
            "artifact_status": artifact_status,
            "ci_linkage": {
                "status": "available" if source_linked else "unavailable",
                "reason": None
                if source_linked
                else "target_repository_has_no_source_revision",
                "source_repository": source_repository,
                "source_revision": source_revision,
                "bound_run_ids": sorted(
                    run_id for run_id in source_bound_run_ids if isinstance(run_id, int)
                ),
                "bound_check_run_ids": sorted(
                    check_id
                    for check_id in source_bound_check_ids
                    if isinstance(check_id, int)
                ),
                "bound_artifact_ids": sorted(
                    artifact.get("id")
                    for artifact in source_bound_artifacts
                    if isinstance(artifact.get("id"), int)
                ),
            },
            **classify_ci_execution(workflow_runs=runs, workflow_jobs=workflow_jobs),
            "status": "available",
            "gaps": collection_errors,
            "provenance": {
                "endpoints": endpoints,
                "provider": "github_api",
                "response_sha256": _sha256(
                    {
                        "repository": repository,
                        "inspected_revision": inspected_revision,
                        "workflow_paths": workflow_paths,
                        "inventory_paths": inventory_paths,
                        "workflows": workflows,
                        "workflow_runs": runs,
                        "workflow_jobs": workflow_jobs,
                        "check_runs": checks,
                        "artifacts": linked_artifacts,
                    }
                ),
                "read_only": True,
            },
        }
    except RepositoryEvidenceError:
        raise
    except Exception as exc:
        raise RepositoryEvidenceError(
            f"repository_evidence_failed: {repository}: {exc}"
        ) from exc


def collect_target_evidence(
    repository: str,
    *,
    revision: str,
    paths: list[str],
    provider: Provider | None = None,
    include_content: bool = False,
    allow_missing: bool = False,
) -> dict[str, Any]:
    """Capture exact target file bytes from a GitHub commit without writes."""

    if not isinstance(repository, str) or not repository.strip():
        raise RepositoryEvidenceError("target repository is required")
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise RepositoryEvidenceError("target revision must be a lowercase 40-character SHA")
    if len(set(revision)) <= 1:
        raise RepositoryEvidenceError("target revision must not be synthetic")
    if not isinstance(paths, list) or not paths or paths != sorted(set(paths)):
        raise RepositoryEvidenceError("target paths must be a sorted, unique non-empty list")
    call = provider or _gh_provider
    tree_endpoint = f"repos/{repository}/git/trees/{revision}?recursive=1"
    try:
        tree = _object(call(tree_endpoint), tree_endpoint)
        if tree.get("truncated") is True:
            raise RepositoryEvidenceError("target tree response is truncated")
        entries = _list(tree.get("tree"), f"{tree_endpoint}.tree")
        by_path = {
            item.get("path"): item
            for item in entries
            if isinstance(item, dict) and item.get("type") == "blob"
        }
        endpoints = [tree_endpoint]
        files: list[dict[str, Any]] = []
        for path in paths:
            entry = by_path.get(path)
            if not isinstance(entry, dict) or not isinstance(entry.get("sha"), str):
                if allow_missing:
                    continue
                raise RepositoryEvidenceError(f"target file is not a blob at revision: {path}")
            blob_sha = entry["sha"]
            blob_endpoint = f"repos/{repository}/git/blobs/{blob_sha}"
            blob = _object(call(blob_endpoint), blob_endpoint)
            endpoints.append(blob_endpoint)
            content = _decode_content(blob)
            row = {
                "path": path,
                "content_sha256": _sha256(content),
                "blob_or_response_id": blob_sha,
            }
            if include_content:
                row["content"] = content.decode("utf-8", errors="replace")
            files.append(row)
        report: dict[str, Any] = {
            "schema_version": TARGET_EVIDENCE_SCHEMA_VERSION,
            "evidence_type": "target_snapshot",
            "provider": "github_git_api",
            "repository": repository,
            "revision": revision,
            "files": files,
            "provenance": {"endpoints": endpoints, "read_only": True},
        }
        report["evidence_sha256"] = _sha256(
            {key: value for key, value in report.items() if key != "evidence_sha256"}
        )
        return report
    except RepositoryEvidenceError:
        raise
    except Exception as exc:
        raise RepositoryEvidenceError(
            f"target_evidence_failed: {repository}@{revision}: {exc}"
        ) from exc
