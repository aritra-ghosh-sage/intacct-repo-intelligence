"""Explicit source repository identity handling for Greenfield reports."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

SHA = re.compile(r"^[0-9a-f]{40}$")


def source_identity(data: Mapping[str, Any]) -> tuple[str, str]:
    """Return canonical owner/name identity and the internal repository key.

    The manifest/provider supplies both values. This helper never derives one
    from a basename or a repository name.
    """

    repo_key = data.get("repo_key") or data.get("source_repo_key")
    canonical = data.get("repository") or data.get("canonical_repository")
    legacy = data.get("source_repository")
    if not isinstance(repo_key, str) or not repo_key.strip():
        repo_key = legacy
    if not isinstance(canonical, str) or not canonical.strip():
        canonical = legacy or repo_key
    if not isinstance(canonical, str) or not canonical.strip():
        raise ValueError("canonical source repository is required")
    if not isinstance(repo_key, str) or not repo_key.strip():
        raise ValueError("source repository key is required")
    return canonical.strip(), repo_key.strip()


def repository_matches(value: object, canonical: str, repo_key: str) -> bool:
    return isinstance(value, str) and value in {canonical, repo_key}


def validate_identity_fields(data: Mapping[str, Any], prefix: str = "input") -> list[str]:
    """Validate additive identity/revision fields while keeping old 0.1 reports readable."""

    errors: list[str] = []
    canonical = data.get("canonical_repository")
    repo_key = data.get("source_repo_key")
    if canonical is not None or repo_key is not None:
        if not isinstance(canonical, str) or not canonical.strip():
            errors.append(f"{prefix}.canonical_repository is required with source_repo_key")
        if not isinstance(repo_key, str) or not repo_key.strip():
            errors.append(f"{prefix}.source_repo_key is required with canonical_repository")
        source = data.get("source_repository")
        if isinstance(canonical, str) and isinstance(repo_key, str) and source not in {canonical, repo_key}:
            errors.append(f"{prefix}.source_repository does not match canonical identity mapping")
    for field in ("base_revision", "source_revision", "target_revision"):
        value = data.get(field)
        if value is not None and (not isinstance(value, str) or not SHA.fullmatch(value.lower())):
            errors.append(f"{prefix}.{field} must be a 40-character SHA")
    pr_number = data.get("source_pr_number")
    if pr_number is not None and (isinstance(pr_number, bool) or not isinstance(pr_number, int) or pr_number < 1):
        errors.append(f"{prefix}.source_pr_number must be a positive integer")
    return errors
