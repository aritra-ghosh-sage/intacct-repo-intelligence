"""Explicit source repository identity handling for Greenfield reports."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


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
