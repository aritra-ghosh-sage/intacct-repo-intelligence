"""Revision/configuration identity used for safe cache keys."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Iterable
from pathlib import Path

from .config import RepoMapConfig


def _git_output(root: Path, arguments: list[str]) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    output = completed.stdout.strip()
    return output or None


def git_revision(root: Path) -> str | None:
    return git_ref_revision(root, "HEAD")


def git_ref_revision(root: Path, ref: str) -> str | None:
    """Resolve a Git reference to a full commit SHA without checking it out."""

    if not ref.strip():
        return None
    return _git_output(root, ["rev-parse", "--verify", f"{ref}^{{commit}}"])


def git_merge_base(root: Path, base_ref: str, head_ref: str = "HEAD") -> str | None:
    """Return the merge base that Ripwire's PR-context command uses."""

    if not base_ref.strip() or not head_ref.strip():
        return None
    return _git_output(root, ["merge-base", base_ref, head_ref])


def repository_id(root: Path) -> str:
    """Return a stable opaque identity for an artifact namespace."""

    remote = _git_output(root, ["remote", "get-url", "origin"])
    seed = f"origin={remote}" if remote else f"root={root.resolve()}"
    return hashlib.sha256(seed.encode()).hexdigest()


def configuration_digest(config: RepoMapConfig) -> str:
    """Return a canonical digest of every validated repository declaration field."""

    payload = {
        "schema_version": config.schema_version,
        "engine": config.engine,
        "scope": list(config.scope),
        "token_budget": config.token_budget,
        "php_family_extensions": list(config.php_family_extensions),
        "map_php_scope": list(config.map_php_scope),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def is_dirty(root: Path) -> bool | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return bool(completed.stdout.strip())


def cache_key(
    root: Path,
    scope: Iterable[str],
    engine: str,
    parser_version: str,
    token_budget: int,
    revision: str | None,
    dirty: bool | None,
) -> str:
    """Return a stable digest scoped to this repository checkout and revision."""

    payload = "\n".join(
        [
            f"repo={root.resolve()}",
            f"scope={','.join(sorted(scope))}",
            f"engine={engine}",
            f"parser={parser_version}",
            f"budget={token_budget}",
            f"revision={revision or 'unavailable'}",
            f"dirty={dirty if dirty is not None else 'unavailable'}",
        ]
    ).encode()
    return hashlib.sha256(payload).hexdigest()
