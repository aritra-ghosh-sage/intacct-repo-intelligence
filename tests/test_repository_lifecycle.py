from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from catalog.repository_lifecycle import (
    RepositoryAdmissionError,
    RepositoryArchivedError,
    normalized_github_identity,
    require_repository_extractable,
    require_repository_id_extractable,
    verify_github_repository_active,
)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE repos(
            id INTEGER PRIMARY KEY, repo_key TEXT UNIQUE, local_root TEXT,
            lifecycle_state TEXT NOT NULL DEFAULT 'active', enabled INTEGER NOT NULL DEFAULT 1
        )"""
    )
    conn.execute("INSERT INTO repos(id,repo_key,lifecycle_state) VALUES(1,'active','active')")
    conn.execute("INSERT INTO repos(id,repo_key,lifecycle_state) VALUES(2,'old','archived')")
    return conn


def test_extractable_guards_only_archived_repository() -> None:
    conn = _conn()
    assert require_repository_extractable(conn, "active")["id"] == 1
    with pytest.raises(RepositoryArchivedError, match="old"):
        require_repository_extractable(conn, "old")
    with pytest.raises(RepositoryArchivedError):
        require_repository_id_extractable(conn, 2)


@pytest.mark.parametrize(
    ("remote", "expected"),
    [
        ("git@github.com:Owner/Repo.git", ("owner", "repo")),
        ("https://github.com/Owner/Repo.git", ("owner", "repo")),
        ("https://git.example.test/Owner/Repo.git", None),
    ],
)
def test_normalized_github_identity(remote: str, expected: tuple[str, str] | None) -> None:
    assert normalized_github_identity(remote) == expected


def test_configured_github_verification_fails_closed_without_branch(tmp_path: Path) -> None:
    with pytest.raises(RepositoryAdmissionError, match="tracked_branch"):
        verify_github_repository_active(
            remote_url="https://github.com/owner/repo.git", root=tmp_path, branch=None
        )


def test_non_github_repository_is_manually_managed(tmp_path: Path) -> None:
    assert verify_github_repository_active(
        remote_url="https://git.example.test/owner/repo.git", root=tmp_path, branch=None
    )
