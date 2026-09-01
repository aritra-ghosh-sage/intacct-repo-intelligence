"""Immutable capture context for one Greenfield PR analysis run."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from greenfield.artifact_io import artifact_sha256
from greenfield.pr_analysis_contract import make_request
from greenfield.repository_handbook import validate_repository_handbook

SCHEMA_VERSION = "0.1"
ANALYSIS_KIND = "greenfield_run_context"
SHA = re.compile(r"^[0-9a-f]{40}$")


class RunContextError(ValueError):
    """Raised when capture inputs cannot form an immutable run context."""


def _repository_identity(remote_url: Any, repo_key: str) -> str:
    remote = str(remote_url or "").strip()
    for marker in ("github.com:", "github.com/"):
        if marker in remote:
            return remote.split(marker, 1)[1].removesuffix(".git")
    return repo_key


def _manifest_rows(manifest: Path) -> list[Mapping[str, Any]]:
    loaded = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    if not isinstance(loaded, Mapping) or not isinstance(
        loaded.get("repositories"), list
    ):
        raise RunContextError("manifest.repositories must be a list")
    rows = loaded["repositories"]
    if any(not isinstance(row, Mapping) for row in rows):
        raise RunContextError("manifest.repositories must contain objects")
    return list(rows)


def _repository_aliases(value: Any) -> set[str]:
    text = str(value or "").strip().removesuffix(".git").lower()
    if not text:
        return set()
    return {text, text.removeprefix("intacct/")}


def _contract_targets(paths: Sequence[str | Path]) -> set[str]:
    targets: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            for key in ("consumer_repository", "target_repository"):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    targets.update(_repository_aliases(candidate))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for path_value in paths:
        path = Path(path_value)
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise RunContextError(
                f"contract artifact read failed: {path}: {exc}"
            ) from exc
        visit(loaded)
    return targets


def _candidate_repositories(
    rows: Sequence[Mapping[str, Any]],
    source_repository: str,
    source_repo_key: str,
    *,
    explicit_targets: set[str] | None = None,
) -> list[dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("enabled") is False:
            continue
        repo_key = str(row.get("repo_key") or "").strip()
        if not repo_key:
            continue
        repository = _repository_identity(row.get("remote_url"), repo_key)
        if repository in {source_repository, source_repo_key}:
            continue
        analysis = row.get("greenfield_analysis")
        discovery_eligible = bool(
            isinstance(analysis, Mapping)
            and analysis.get("role") == "test"
            and analysis.get("discovery_eligible") is True
        )
        explicit = bool(_repository_aliases(repository) & (explicit_targets or set()))
        relationship_types: set[str] = set()
        contracts = row.get("pr_impact_contracts", [])
        if isinstance(contracts, list):
            for contract in contracts:
                if not isinstance(contract, Mapping):
                    continue
                target = str(contract.get("target_repository") or "")
                if target in {source_repository, source_repo_key}:
                    explicit = True
                    relationship_types.add(str(contract.get("type") or "explicit"))
        if not explicit and not discovery_eligible:
            continue
        local_root = Path(str(row.get("local_root", ""))).expanduser()
        inspected_revision = None
        if local_root.is_dir():
            result = subprocess.run(
                ["git", "-C", str(local_root), "rev-parse", "HEAD"],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            candidate_revision = result.stdout.strip().lower()
            if result.returncode == 0 and SHA.fullmatch(candidate_revision):
                inspected_revision = candidate_revision
        candidates[repository] = {
            "repository": repository,
            "repo_key": repo_key,
            "priority": "explicit_contract" if explicit else "discovery_screen",
            "discovery_eligible": discovery_eligible,
            "relationship_types": sorted(relationship_types),
            "local_root": str(local_root),
            "inspected_revision": inspected_revision,
            "tracked_branch": row.get("tracked_branch"),
            "test_roots": sorted(
                str(value)
                for value in (
                    analysis.get("test_roots", [])
                    if isinstance(analysis, Mapping)
                    else []
                )
            ),
            "test_formats": sorted(
                str(value)
                for value in (
                    analysis.get("test_formats", [])
                    if isinstance(analysis, Mapping)
                    else []
                )
            ),
        }
    return sorted(
        candidates.values(),
        key=lambda row: (
            0 if row["priority"] == "explicit_contract" else 1,
            row["repository"],
        ),
    )


def build_run_context(
    step1: Mapping[str, Any],
    manifest: str | Path,
    *,
    source_root: str | Path | None = None,
    evidence_artifacts: Sequence[str | Path] = (),
    contract_artifacts: Sequence[str | Path] = (),
    repository_handbooks: Mapping[str, str | Path] | None = None,
    tool_limits: Mapping[str, int] | None = None,
    execution: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Capture source identity, candidate scope, and evidence fingerprints once."""

    request = make_request(step1)
    manifest_path = Path(manifest).resolve()
    rows = _manifest_rows(manifest_path)
    source_repo_key = str(
        request.get("source_repo_key") or request["source_repository"]
    )
    artifacts = []
    for value in sorted({str(Path(path).resolve()) for path in evidence_artifacts}):
        path = Path(value)
        if not path.is_file():
            raise RunContextError(f"evidence artifact is unavailable: {path}")
        artifacts.append(
            {
                "path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    handbooks = []
    candidates = _candidate_repositories(
        rows,
        request["source_repository"],
        source_repo_key,
        explicit_targets=_contract_targets(contract_artifacts),
    )
    candidate_by_alias = {
        alias: row
        for row in candidates
        for alias in _repository_aliases(row["repository"])
        | _repository_aliases(row["repo_key"])
    }
    for repository, value in sorted((repository_handbooks or {}).items()):
        path = Path(value).resolve()
        if not path.is_file():
            raise RunContextError(f"repository handbook is unavailable: {path}")
        try:
            handbook = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RunContextError(
                f"repository handbook read failed: {path}: {exc}"
            ) from exc
        handbook_errors = validate_repository_handbook(handbook)
        if handbook_errors:
            raise RunContextError(
                f"invalid repository handbook {path}: " + "; ".join(handbook_errors)
            )
        handbook_repository = str(handbook["repository"])
        if not _repository_aliases(repository) & _repository_aliases(
            handbook_repository
        ):
            raise RunContextError(
                f"repository handbook identity does not match {repository}: {path}"
            )
        aliases = _repository_aliases(handbook_repository)
        if aliases & _repository_aliases(request["source_repository"]):
            expected_revision = request["head_revision"]
        else:
            candidate = next(
                (
                    candidate_by_alias[alias]
                    for alias in aliases
                    if alias in candidate_by_alias
                ),
                None,
            )
            expected_revision = (
                candidate.get("inspected_revision") if candidate else None
            )
        if not expected_revision or handbook["revision"] != expected_revision:
            raise RunContextError(
                f"repository handbook revision is not the captured revision: {path}"
            )
        handbooks.append(
            {
                "repository": handbook_repository,
                "path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    limits = {
        "max_tool_calls": 80,
        "max_files": 40,
        "max_file_bytes": 120_000,
        "max_search_results": 50,
    }
    limits.update({key: int(value) for key, value in (tool_limits or {}).items()})
    if any(value <= 0 for value in limits.values()):
        raise RunContextError("tool limits must be positive integers")

    context: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "analysis_kind": ANALYSIS_KIND,
        "source": {
            "repository": request["source_repository"],
            "repo_key": source_repo_key,
            "pr_number": request.get("pr_number"),
            "base_revision": request["base_revision"],
            "head_revision": request["head_revision"],
            "changed_paths": request["changed_paths"],
            "local_root": str(Path(source_root).resolve()) if source_root else None,
        },
        "manifest": {
            "path": str(manifest_path),
            "sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        },
        "candidate_repositories": candidates,
        "evidence_artifacts": artifacts,
        "repository_handbooks": handbooks,
        "tool_policy": {
            "read_only": True,
            "revision_bound": True,
            "network_access": "none",
            "limits": limits,
        },
        "provenance": {
            "step1_sha256": artifact_sha256(step1),
            "request_sha256": request["request_sha256"],
            "github_writes": "none",
            "catalog_mutation": "none",
        },
    }
    if execution is not None:
        context["execution"] = dict(execution)
    context["context_sha256"] = artifact_sha256(context)
    errors = validate_run_context(context)
    if errors:
        raise RunContextError("invalid run context: " + "; ".join(errors))
    return context


def validate_run_context(value: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, Mapping):
        return ["run context must be an object"]
    if value.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    if value.get("analysis_kind") != ANALYSIS_KIND:
        errors.append(f"analysis_kind must be {ANALYSIS_KIND}")
    source = value.get("source")
    if not isinstance(source, Mapping):
        errors.append("source must be an object")
    else:
        for field in ("base_revision", "head_revision"):
            if not isinstance(source.get(field), str) or not SHA.fullmatch(
                source[field]
            ):
                errors.append(f"source.{field} must be a lowercase SHA")
        if (
            not isinstance(source.get("changed_paths"), list)
            or not source["changed_paths"]
        ):
            errors.append("source.changed_paths must be non-empty")
    candidates = value.get("candidate_repositories")
    if not isinstance(candidates, list):
        errors.append("candidate_repositories must be a list")
    elif any(
        not isinstance(row, Mapping)
        or row.get("priority") not in {"explicit_contract", "discovery_screen"}
        for row in candidates
    ):
        errors.append("candidate_repositories contains an invalid row")
    execution = value.get("execution")
    if execution is not None:
        if not isinstance(execution, Mapping):
            errors.append("execution must be an object")
        else:
            if "dry_run" in execution and not isinstance(execution["dry_run"], bool):
                errors.append("execution.dry_run must be a boolean")
            if execution.get("planner_mode") not in {None, "default"}:
                errors.append("execution.planner_mode is invalid")
            if "model" in execution and not isinstance(execution["model"], str):
                errors.append("execution.model must be a string")
            if "strands_model" in execution and not isinstance(
                execution["strands_model"], str
            ):
                errors.append("execution.strands_model must be a string")
            if "nexau_model" in execution and not isinstance(
                execution["nexau_model"], str
            ):
                errors.append("execution.nexau_model must be a string")
            if "base_url" in execution and not isinstance(execution["base_url"], str):
                errors.append("execution.base_url must be a string")
    digest = value.get("context_sha256")
    unsigned = dict(value)
    unsigned.pop("context_sha256", None)
    if not isinstance(digest, str) or artifact_sha256(unsigned) != digest:
        errors.append("context_sha256 does not match context")
    return errors


__all__ = [
    "ANALYSIS_KIND",
    "RunContextError",
    "build_run_context",
    "validate_run_context",
]
