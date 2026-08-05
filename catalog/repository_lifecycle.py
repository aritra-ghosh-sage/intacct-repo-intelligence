"""Repository lifecycle admission checks.

The catalog continues to retain archived repository metadata for provenance,
but archived repositories are never admissible inputs to extractors/builders.
"""

from __future__ import annotations

import re
import shutil
import sqlite3
import subprocess
from pathlib import Path
from urllib.parse import urlparse


class RepositoryArchivedError(RuntimeError):
    """Raised before an archived repository can read source or mutate facts."""


class RepositoryAdmissionError(RuntimeError):
    """Raised when configured remote archival state cannot be verified."""


def _repo_columns(conn: sqlite3.Connection) -> set[str]:
    return {str(row[1]) for row in conn.execute("PRAGMA table_info(repos)")}


def _get_repository(conn: sqlite3.Connection, where: str, value: object) -> sqlite3.Row:
    row = conn.execute(f"SELECT * FROM repos WHERE {where}=?", (value,)).fetchone()
    if row is None:
        raise RepositoryAdmissionError(f"unknown repository {value!r}")
    return row


def _assert_extractable(conn: sqlite3.Connection, row: sqlite3.Row) -> sqlite3.Row:
    # The fallback intentionally supports pre-029 databases during migration;
    # archive admission is mandatory once lifecycle metadata exists.
    if "lifecycle_state" in _repo_columns(conn) and row["lifecycle_state"] == "archived":
        raise RepositoryArchivedError(
            f"repository is archived and cannot be scanned or extracted: {row['repo_key']}"
        )
    return row


def require_repository_extractable(conn: sqlite3.Connection, repo_key: str) -> sqlite3.Row:
    """Return an active repository row or raise before any source traversal."""
    return _assert_extractable(conn, _get_repository(conn, "repo_key", repo_key))


def require_repository_id_extractable(conn: sqlite3.Connection, repo_id: int) -> sqlite3.Row:
    """ID variant used by legacy public builder services."""
    return _assert_extractable(conn, _get_repository(conn, "id", repo_id))


def normalized_github_identity(remote_url: str | None) -> tuple[str, str] | None:
    """Return lower-case (owner, repo) only for github.com remotes."""
    if not remote_url:
        return None
    value = remote_url.strip()
    scp = re.fullmatch(r"(?:[^@]+@)?github\.com:([^/]+)/(.+)", value, re.IGNORECASE)
    if scp:
        owner, repo = scp.groups()
    else:
        parsed = urlparse(value)
        if parsed.hostname is None or parsed.hostname.lower() != "github.com":
            return None
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) != 2:
            return None
        owner, repo = parts
    repo = repo.removesuffix(".git")
    if not owner or not repo:
        return None
    return owner.lower(), repo.lower()


def _run(argv: list[str], *, cwd: Path | None = None) -> str:
    try:
        result = subprocess.run(argv, cwd=cwd, text=True, capture_output=True, check=False)
    except OSError as exc:
        raise RepositoryAdmissionError(f"unable to execute {argv[0]}: {exc}") from exc
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "command failed"
        raise RepositoryAdmissionError(f"{' '.join(argv[:2])} failed: {detail}")
    return result.stdout.strip()


def verify_github_repository_active(*, remote_url: str | None, root: Path, branch: str | None) -> bool:
    """Fetch and verify GitHub's literal archived flag.

    ``True`` means remotely active.  Non-GitHub/no-remote repositories are
    manually controlled and therefore return True without a provider call.
    Every configured GitHub verification uncertainty raises (fail closed).
    """
    expected = normalized_github_identity(remote_url)
    if expected is None:
        return True
    if not branch:
        raise RepositoryAdmissionError("GitHub archival verification requires tracked_branch")
    actual_url = _run(["git", "-C", str(root), "remote", "get-url", "origin"])
    actual = normalized_github_identity(actual_url)
    if actual != expected:
        raise RepositoryAdmissionError(
            f"origin remote does not match configured GitHub identity: expected={expected!r} actual={actual!r}"
        )
    _run([
        "git", "-C", str(root), "fetch", "--prune", "--no-tags", "origin",
        f"refs/heads/{branch}:refs/remotes/origin/{branch}",
    ])
    if shutil.which("gh") is None:
        raise RepositoryAdmissionError("gh executable is required to verify configured GitHub repository state")
    response = _run([
        "gh", "api", "--hostname", "github.com", f"repos/{expected[0]}/{expected[1]}", "--jq", ".archived",
    ]).strip()
    if response == "false":
        return True
    if response == "true":
        raise RepositoryArchivedError(
            f"GitHub reports repository archived: {expected[0]}/{expected[1]}"
        )
    raise RepositoryAdmissionError(
        f"GitHub archive response must be literal true or false, got {response!r}"
    )
