"""Deterministic first-pass discovery from exact Step 1.5 trace evidence."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from greenfield.artifact_io import artifact_sha256
from greenfield.pr_analysis_contract import validate_claims


def discover_from_trace(*, request: Mapping[str, Any], trace: Mapping[str, Any], contract: Mapping[str, Any]) -> dict[str, Any]:
    repository = str(request["source_repository"])
    revision = str(request["head_revision"])
    behaviors = trace.get("behaviors", []) if isinstance(trace.get("behaviors"), list) else []
    claims = [{"repository": repository, "inspected_revision": revision, "evidence_status": "confirmed", "evidence": [{"artifact": "step1.5.trace.json", "sha256": artifact_sha256(trace)}, {"artifact": "step1.5.contract.json", "sha256": artifact_sha256(contract)}], "rationale": f"exact source trace retained {len(behaviors)} behavior groups", "behaviors": [row.get("behavior_id") for row in behaviors if isinstance(row, Mapping)]}]
    return {"schema_version": "0.1", "analysis_kind": "greenfield_impact_discovery", "status": "partial", "claims": claims, "read_requests": [], "gaps": ["cross_repository_discovery_requires_confirmed_relation_or_bound_ci_evidence"], "provenance": {"read_only": True, "github_writes": "none", "catalog_mutation": "none", "request_sha256": request.get("request_sha256")}}


def validate_discovery(report: Mapping[str, Any]) -> list[str]:
    return validate_claims(report, kind="greenfield_impact_discovery")


def validate_read_requests(
    requests: object,
    *,
    allowed_repository: str,
    allowed_revision: str,
    max_files: int = 40,
) -> list[str]:
    """Fail closed before an AI-proposed read reaches a repository."""

    if not isinstance(requests, list) or len(requests) > max_files:
        return ["read_requests must be a list within the configured file budget"]
    errors = []
    seen: set[str] = set()
    for index, request in enumerate(requests):
        if not isinstance(request, Mapping):
            errors.append(f"read_requests[{index}] must be an object")
            continue
        path = request.get("path")
        if request.get("repository") != allowed_repository or request.get("revision") != allowed_revision:
            errors.append(f"read_requests[{index}] is not bound to the approved repository and revision")
        if not isinstance(path, str) or not path or path.startswith("/") or ".." in Path(path).parts:
            errors.append(f"read_requests[{index}].path is invalid")
        elif path in seen:
            errors.append(f"read_requests[{index}].path is duplicated")
        else:
            seen.add(path)
    return errors


def materialize_local_reads(*, source_root: str | Path, requests: list[dict[str, Any]], max_file_bytes: int = 120_000) -> list[dict[str, Any]]:
    """Read approved blobs at the requested immutable revision only."""

    if not requests:
        return []
    root = Path(source_root).resolve()
    errors = validate_read_requests(requests, allowed_repository=str(requests[0].get("repository")), allowed_revision=str(requests[0].get("revision")), max_files=len(requests))
    if errors:
        raise ValueError("invalid discovery reads: " + "; ".join(errors))
    rows = []
    for request in requests:
        result = subprocess.run(["git", "-C", str(root), "show", f"{request['revision']}:{request['path']}"], capture_output=True, check=False, timeout=30)
        if result.returncode:
            rows.append({"path": request["path"], "status": "unavailable"})
        elif len(result.stdout) > max_file_bytes:
            rows.append({"path": request["path"], "status": "truncated"})
        else:
            rows.append({"path": request["path"], "status": "available", "content": result.stdout.decode("utf-8", errors="replace")})
    return rows


__all__ = ["discover_from_trace", "materialize_local_reads", "validate_discovery", "validate_read_requests"]
