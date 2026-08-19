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
_TEST_COMMAND = re.compile(
    r"(?:\bpytest\b|mvn\s+(?:test|verify)(?![-\w])|gradle\s+(?:test|check)(?![-\w])|npm\s+test\b|yarn\s+test\b|go\s+test\b|dotnet\s+test\b|\bctest\b)",
    re.IGNORECASE,
)
_INVENTORY_PATH = re.compile(
    r"(?:^|/)(?:features?|tests?|testdefinitions|testscripts|specs?)(?:/|$)|\.(?:feature|feature\.xml|jmx|test\.xml)$",
    re.IGNORECASE,
)


class RepositoryEvidenceError(RuntimeError):
    """GitHub repository evidence could not be fetched or normalized."""


Provider = Callable[[str], Any]


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
    has_test_execution = bool(_TEST_COMMAND.search(text))
    has_artifact_upload = "actions/upload-artifact@" in text
    if has_test_execution and has_artifact_upload:
        classification = "test_execution_with_artifact"
    elif has_test_execution:
        classification = "test_execution_without_artifact"
    elif has_artifact_upload:
        classification = "artifact_without_test_execution"
    else:
        classification = "metadata_only"
    return {
        "classification": classification,
        "has_test_execution": has_test_execution,
        "has_artifact_upload": has_artifact_upload,
    }


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


def collect_repository_evidence(
    repository: str,
    *,
    source_repository: str,
    source_revision: str,
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
            content_endpoint = f"repos/{repository}/contents/{path}?ref={inspected_revision}"
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

        runs_endpoint = f"repos/{repository}/actions/runs?head_sha={source_revision}&per_page=100"
        check_endpoint = f"repos/{repository}/commits/{source_revision}/check-runs?per_page=100"
        artifacts_endpoint = f"repos/{repository}/actions/artifacts?per_page=100"

        def optional_collection(endpoint: str, key: str) -> list[Any]:
            try:
                response = _object(call(endpoint), endpoint)
                endpoints.append(endpoint)
                return _list(response.get(key, []), f"{endpoint}.{key}")
            except RepositoryEvidenceError as exc:
                collection_errors.append(f"{endpoint}: {exc}")
                return []

        runs = optional_collection(runs_endpoint, "workflow_runs")
        checks = optional_collection(check_endpoint, "check_runs")
        artifacts = optional_collection(artifacts_endpoint, "artifacts")
        run_ids = {run.get("id") for run in runs if isinstance(run, dict)}
        workflow_jobs: list[dict[str, Any]] = []
        for run_id in sorted(run_ids):
            if not isinstance(run_id, int):
                continue
            jobs_endpoint = f"repos/{repository}/actions/runs/{run_id}/jobs?per_page=100"
            jobs = optional_collection(jobs_endpoint, "jobs")
            workflow_jobs.extend(
                job for job in jobs if isinstance(job, dict)
            )
        linked_artifacts = [
            artifact
            for artifact in artifacts
            if isinstance(artifact, dict)
            and isinstance(artifact.get("workflow_run"), dict)
            and artifact["workflow_run"].get("id") in run_ids
        ]
        if linked_artifacts:
            artifact_status = "available"
        elif artifacts:
            artifact_status = "not_linked_to_source_revision"
        else:
            artifact_status = "empty"
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
                key=lambda item: (str(item.get("id", "")), str(item.get("head_sha", ""))),
            ),
            "workflow_jobs": sorted(
                workflow_jobs,
                key=lambda item: (str(item.get("run_id", "")), str(item.get("id", "")), str(item.get("name", ""))),
            ),
            "check_runs": sorted(
                [check for check in checks if isinstance(check, dict)],
                key=lambda item: (str(item.get("name", "")), str(item.get("id", ""))),
            ),
            "artifacts": sorted(
                [artifact for artifact in linked_artifacts if isinstance(artifact, dict)],
                key=lambda item: (str(item.get("name", "")), str(item.get("id", ""))),
            ),
            "artifact_status": artifact_status,
            "status": "available",
            "gaps": collection_errors,
            "provenance": {
                "endpoints": endpoints,
                "provider": "github_api",
                "response_sha256": _sha256({
                    "repository": repository,
                    "inspected_revision": inspected_revision,
                    "workflow_paths": workflow_paths,
                    "inventory_paths": inventory_paths,
                    "workflows": workflows,
                    "workflow_runs": runs,
                    "workflow_jobs": workflow_jobs,
                    "check_runs": checks,
                    "artifacts": linked_artifacts,
                }),
                "read_only": True,
            },
        }
    except RepositoryEvidenceError:
        raise
    except Exception as exc:
        raise RepositoryEvidenceError(f"repository_evidence_failed: {repository}: {exc}") from exc
