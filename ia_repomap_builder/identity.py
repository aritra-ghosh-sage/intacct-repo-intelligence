"""Revision/configuration identity used for safe cache keys."""

from __future__ import annotations

import hashlib
import subprocess
from collections.abc import Iterable
from pathlib import Path


def git_revision(root: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    revision = completed.stdout.strip()
    return revision if revision else None


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
