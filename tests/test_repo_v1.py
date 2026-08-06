from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from catalog.refresh_transaction import (
    CatalogPromotionError,
    assert_parent_unchanged,
    parent_descriptor,
)
from catalog.repo_v1 import SCHEMA_PATH, RepoV1Error, build_ia_main
from parser.scan_repo import detect_language


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _repo(tmp_path: Path, *, symlink: bool = False) -> tuple[Path, Path]:
    root = tmp_path / "source"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.test")
    _git(root, "config", "user.name", "Test")
    (root / "app").mkdir()
    (root / "app" / "main.php").write_text("<?php echo 'committed';\n")
    (root / "README.md").write_text("inventory\n")
    if symlink:
        (root / "link").symlink_to("README.md")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "fixture")
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        f"""version: 1
repositories:
  - repo_key: ia-main
    name: Fixture
    kind: monorepo
    language: php
    remote_url: https://example.test/repo.git
    local_root: {root}
    tracked_branch: main
    enabled: true
    profile: null
    depends_on: null
    builders: []
"""
    )
    return root, manifest


def _commit(root: Path, message: str) -> str:
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", message)
    return _git(root, "rev-parse", "HEAD")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _logical_state(db: Path) -> tuple[list[tuple], list[tuple]]:
    conn = sqlite3.connect(db)
    try:
        repos = conn.execute(
            """SELECT repo_key,name,kind,language,remote_url,local_root,
                      tracked_branch,target_commit_sha
               FROM repos ORDER BY repo_key"""
        ).fetchall()
        files = conn.execute(
            "SELECT path,blob_object_id,file_mode,size_bytes,language,source_commit_sha "
            "FROM files ORDER BY path"
        ).fetchall()
        return repos, files
    finally:
        conn.close()


def _git_tree_oracle(root: Path, commit: str) -> list[tuple]:
    output = subprocess.check_output(
        ["git", "-C", str(root), "ls-tree", "-r", "-z", "-l", commit]
    )
    rows: list[tuple] = []
    for record in output.rstrip(b"\0").split(b"\0"):
        metadata, raw_path = record.split(b"\t", 1)
        raw_mode, raw_kind, raw_blob, raw_size = metadata.split()
        assert raw_kind == b"blob"
        path = raw_path.decode("utf-8")
        rows.append(
            (
                path,
                raw_blob.decode("ascii"),
                int(raw_mode, 8),
                int(raw_size),
                detect_language(path),
                commit,
            )
        )
    return sorted(rows)


def _rich_repo(tmp_path: Path) -> tuple[Path, Path, str]:
    root, manifest = _repo(tmp_path)
    executable = root / "run.sh"
    executable.write_bytes(b"#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    (root / "empty.dat").write_bytes(b"")
    (root / "binary.bin").write_bytes(b"\x00\x01\xff\x80\n")
    return root, manifest, _commit(root, "ordinary executable empty binary")


def test_same_commit_uses_committed_blobs_and_is_deterministic(tmp_path: Path) -> None:
    root, manifest = _repo(tmp_path)
    commit = _git(root, "rev-parse", "HEAD")
    first = tmp_path / "first.db"
    second = tmp_path / "second.db"

    build_ia_main(manifest_path=manifest, active_db=first, target_sha=commit, promote=True)
    (root / "app" / "main.php").write_text("<?php echo 'mutable checkout';\n")
    build_ia_main(manifest_path=manifest, active_db=second, target_sha=commit, promote=True)

    first_rows = _logical_state(first)
    second_rows = _logical_state(second)
    assert first_rows == second_rows
    assert first_rows[0][0][7] == commit
    php_row = next(row for row in first_rows[1] if row[0] == "app/main.php")
    assert php_row[1] == _git(root, "rev-parse", f"{commit}:app/main.php")


def test_inventory_matches_complete_git_tree_oracle(tmp_path: Path) -> None:
    root, manifest, commit = _rich_repo(tmp_path)
    active = tmp_path / "active.db"

    build_ia_main(manifest_path=manifest, active_db=active, target_sha=commit)

    conn = sqlite3.connect(active)
    try:
        rows = conn.execute(
            """SELECT f.path,f.blob_object_id,f.file_mode,f.size_bytes,f.language,
                      f.source_commit_sha
               FROM files f ORDER BY f.path"""
        ).fetchall()
    finally:
        conn.close()
    assert rows == _git_tree_oracle(root, commit)
    assert any(row[0] == "run.sh" and row[2] == 0o100755 for row in rows)
    assert any(row[0] == "empty.dat" and row[3] == 0 for row in rows)
    assert any(row[0] == "binary.bin" and row[4] == "unknown" for row in rows)


def test_first_promotion_creates_active_catalog_without_previous(tmp_path: Path) -> None:
    _root, manifest = _repo(tmp_path)
    active = tmp_path / "nested" / "active.db"
    result = build_ia_main(manifest_path=manifest, active_db=active)
    assert result.promoted
    assert active.exists()
    assert not active.with_name("active.db.previous").exists()
    conn = sqlite3.connect(active)
    try:
        assert conn.execute("SELECT status FROM catalog_builds").fetchone()[0] == "active"
        assert conn.execute("SELECT COUNT(*) FROM repos").fetchone()[0] == 1
    finally:
        conn.close()


def test_replacement_promotion_retains_only_filesystem_previous_artifact(tmp_path: Path) -> None:
    root, manifest = _repo(tmp_path)
    active = tmp_path / "active.db"
    first = build_ia_main(manifest_path=manifest, active_db=active)
    first_digest = _digest(active)
    first_state = _logical_state(active)
    (root / "new.txt").write_text("new\n")
    second_commit = _commit(root, "replacement")

    second = build_ia_main(
        manifest_path=manifest, active_db=active, target_sha=second_commit
    )

    assert second.promoted
    assert second.target_commit_sha != first.target_commit_sha
    assert _logical_state(active.with_name("active.db.previous")) == first_state
    assert first_digest != _digest(active)
    conn = sqlite3.connect(active)
    try:
        assert conn.execute("SELECT status FROM catalog_builds").fetchone()[0] == "active"
    finally:
        conn.close()


def test_inventory_follows_target_tree_for_renamed_paths(tmp_path: Path) -> None:
    root, manifest = _repo(tmp_path)
    _git(root, "mv", "README.md", "README-renamed.md")
    _git(root, "commit", "-qm", "rename")
    active = tmp_path / "active.db"

    build_ia_main(manifest_path=manifest, active_db=active)

    conn = sqlite3.connect(active)
    try:
        paths = {
            row[0] for row in conn.execute("SELECT path FROM files ORDER BY path")
        }
    finally:
        conn.close()
    assert "README-renamed.md" in paths
    assert "README.md" not in paths


def test_deletion_commit_removes_deleted_path_from_full_inventory(tmp_path: Path) -> None:
    root, manifest = _repo(tmp_path)
    target = root / "README.md"
    target.unlink()
    commit = _commit(root, "delete README")
    active = tmp_path / "active.db"

    build_ia_main(manifest_path=manifest, active_db=active, target_sha=commit)

    conn = sqlite3.connect(active)
    try:
        paths = {row[0] for row in conn.execute("SELECT path FROM files")}
    finally:
        conn.close()
    assert "README.md" not in paths


def test_failed_source_preparation_preserves_active_and_previous(tmp_path: Path) -> None:
    root, manifest = _repo(tmp_path)
    active = tmp_path / "active.db"
    build_ia_main(manifest_path=manifest, active_db=active)
    (root / "new.txt").write_text("new\n")
    _commit(root, "replacement")
    build_ia_main(manifest_path=manifest, active_db=active)
    before = active.read_bytes()
    previous = active.with_name("active.db.previous")
    previous_before = previous.read_bytes()
    (root / "link").symlink_to("README.md")
    _commit(root, "unsupported object")
    with pytest.raises(RepoV1Error, match="unsupported Git tree mode"):
        build_ia_main(manifest_path=manifest, active_db=active)
    assert active.read_bytes() == before
    assert previous.read_bytes() == previous_before
    assert not list(active.parent.glob(f".{active.name}.candidate.*"))


def test_gitlink_is_rejected_by_v1_inventory(tmp_path: Path) -> None:
    root, manifest = _repo(tmp_path)
    nested = tmp_path / "nested-repository"
    nested.mkdir()
    _git(nested, "init", "-q", "-b", "main")
    (nested / "tracked.txt").write_text("nested\n")
    nested_commit = _commit(nested, "nested")
    _git(root, "update-index", "--add", "--cacheinfo", f"160000,{nested_commit},nested-repository")
    _git(root, "commit", "-qm", "gitlink")

    with pytest.raises(RepoV1Error, match="160000"):
        build_ia_main(manifest_path=manifest, active_db=tmp_path / "active.db")


def test_injected_backup_failure_preserves_active_and_previous(tmp_path: Path) -> None:
    root, manifest = _repo(tmp_path)
    active = tmp_path / "active.db"
    build_ia_main(manifest_path=manifest, active_db=active)
    (root / "new.txt").write_text("new\n")
    _commit(root, "replacement")
    build_ia_main(manifest_path=manifest, active_db=active)
    before = active.read_bytes()
    previous = active.with_name("active.db.previous")
    previous_before = previous.read_bytes()
    (root / "newer.txt").write_text("newer\n")
    target = _commit(root, "backup failure target")

    with patch(
        "catalog.refresh_transaction.backup_database",
        side_effect=OSError("injected backup failure"),
    ), pytest.raises(OSError, match="injected backup failure"):
        build_ia_main(manifest_path=manifest, active_db=active, target_sha=target)

    assert active.read_bytes() == before
    assert previous.read_bytes() == previous_before
    assert not list(active.parent.glob(f".{active.name}.candidate.*"))


def test_injected_candidate_replace_failure_preserves_active_and_previous(tmp_path: Path) -> None:
    root, manifest = _repo(tmp_path)
    active = tmp_path / "active.db"
    build_ia_main(manifest_path=manifest, active_db=active)
    (root / "new.txt").write_text("new\n")
    _commit(root, "replacement")
    build_ia_main(manifest_path=manifest, active_db=active)
    before = active.read_bytes()
    previous = active.with_name("active.db.previous")
    previous_before = previous.read_bytes()
    (root / "newer.txt").write_text("newer\n")
    target = _commit(root, "candidate replace failure target")
    original_replace = os.replace

    def fail_candidate_replace(source: str | Path, destination: str | Path) -> None:
        if Path(destination) == active and Path(source).name.startswith(f".{active.name}.candidate."):
            raise OSError("injected candidate replace failure")
        original_replace(source, destination)

    with patch(
        "catalog.refresh_transaction.os.replace",
        side_effect=fail_candidate_replace,
    ), pytest.raises(OSError, match="injected candidate replace failure"):
        build_ia_main(manifest_path=manifest, active_db=active, target_sha=target)

    assert active.read_bytes() == before
    assert previous.read_bytes() == previous_before
    assert not list(active.parent.glob(f".{active.name}.candidate.*"))


def test_cas_detects_active_generation_change(tmp_path: Path) -> None:
    _root, manifest = _repo(tmp_path)
    active = tmp_path / "active.db"
    build_ia_main(manifest_path=manifest, active_db=active)
    expected = parent_descriptor(active)
    conn = sqlite3.connect(active)
    try:
        conn.execute(
            "UPDATE catalog_builds SET source_revisions_json=? WHERE status='active'",
            ("{\"ia-main\":\"changed\"}",),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(CatalogPromotionError, match="compare-and-swap"):
        assert_parent_unchanged(active, expected)


def test_unpromoted_candidate_does_not_touch_active(tmp_path: Path) -> None:
    _root, manifest = _repo(tmp_path)
    active = tmp_path / "active.db"
    build_ia_main(manifest_path=manifest, active_db=active)
    before = active.read_bytes()
    result = build_ia_main(manifest_path=manifest, active_db=active, promote=False)
    assert not result.promoted
    assert active.read_bytes() == before
    assert not active.with_name("active.db.previous").exists()
    assert not list(active.parent.glob(f".{active.name}.candidate.*"))


def test_v1_schema_has_only_minimal_build_lifecycle(tmp_path: Path) -> None:
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(SCHEMA_PATH.read_text())
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(catalog_builds)")
        }
        assert columns == {
            "id",
            "build_token",
            "catalog_path",
            "status",
            "source_revisions_json",
            "started_at",
            "completed_at",
            "validation_summary",
        }
        schema_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='catalog_builds'"
        ).fetchone()[0]
        assert "requested_mode" not in schema_sql
        assert "effective_mode" not in schema_sql
        assert "diagnostic_error" not in schema_sql
        assert "previous" not in schema_sql
        assert "failed" not in schema_sql
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO catalog_builds(build_token,catalog_path,status,source_revisions_json) "
                "VALUES ('bad','bad','failed','{}')"
            )
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("x.java", "java"),
        ("x.PHP", "php"),
        ("x.INC", "php"),
        ("x.ENT", "php"),
        ("x.js", "javascript"),
        ("x.TS", "typescript"),
        ("x.SQL", "sql"),
        ("x.Xml", "xml"),
        ("x.JSON", "json"),
        ("x.PY", "python"),
        ("x.YAML", "yaml"),
        ("x.XSLT", "xslt"),
        ("x.unknown", "unknown"),
        ("README", "unknown"),
    ],
)
def test_language_classification_parity(path: str, expected: str) -> None:
    assert detect_language(path) == expected
