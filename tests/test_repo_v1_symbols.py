from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

from catalog.repo_v1 import SCHEMA_PATH, RepoV1Error, _validate_candidate, build_ia_main


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "source"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.test")
    _git(root, "config", "user.name", "Test")
    (root / "README.php").write_text("inventory\n")
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
    local_root: {root}
    tracked_branch: main
    builders: []
""",
        encoding="utf-8",
    )
    return root, manifest


def _symbol_rows(db: Path) -> list[tuple]:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(
            """SELECT f.path,s.name,s.kind,s.parent_symbol,s.start_line,
                      s.end_line,s.language,s.stable_key
               FROM symbols s JOIN files f ON f.id=s.file_id
               ORDER BY f.path,s.stable_key"""
        ).fetchall()
    finally:
        conn.close()


def test_symbols_use_committed_snapshot_bytes_and_are_deterministic(
    tmp_path: Path,
) -> None:
    root, manifest = _fixture(tmp_path)
    source = root / "committed.php"
    source.write_text(
        "<?php\nclass CommittedClass {\n    public function run() {}\n}\n",
        encoding="utf-8",
    )
    (root / "committed.js").write_text(
        "function committedHandler() {}\n",
        encoding="utf-8",
    )
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "committed symbols")
    target = _git(root, "rev-parse", "HEAD")

    first = tmp_path / "first.db"
    second = tmp_path / "second.db"
    build_ia_main(manifest_path=manifest, active_db=first, target_sha=target)
    source.write_text("<?php class MutableCheckout {}\n", encoding="utf-8")
    build_ia_main(manifest_path=manifest, active_db=second, target_sha=target)

    rows = _symbol_rows(first)
    assert rows == _symbol_rows(second)
    class_row = next(row for row in rows if row[0:3] == ("committed.php", "CommittedClass", "class"))
    assert class_row[3:7] == (None, 2, 4, "php")
    assert any(row[0:3] == ("committed.php", "run", "method") for row in rows)
    assert any(row[0:3] == ("committed.js", "committedHandler", "function") for row in rows)
    conn = sqlite3.connect(first)
    try:
        assert conn.execute("SELECT COUNT(*) FROM symbol_diagnostics").fetchone()[0] == 0
    finally:
        conn.close()


def test_candidate_validation_rejects_invalid_symbol_line_ranges(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.db"
    conn = sqlite3.connect(candidate)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text())
    build_id = conn.execute(
        """INSERT INTO catalog_builds(
               build_token,catalog_path,status,source_revisions_json
           ) VALUES(?,?,?,?)""",
        ("token", str(candidate), "validated", '{"ia-main":"target"}'),
    ).lastrowid
    repo_id = conn.execute(
        """INSERT INTO repos(
               repo_key,name,kind,language,local_root,tracked_branch,
               target_commit_sha,build_id
           ) VALUES(?,?,?,?,?,?,?,?)""",
        ("ia-main", "Fixture", "monorepo", "php", str(tmp_path), "main", "target", build_id),
    ).lastrowid
    file_id = conn.execute(
        """INSERT INTO files(
               repo_id,path,blob_object_id,file_mode,size_bytes,language,source_commit_sha
           ) VALUES(?,?,?,?,?,?,?)""",
        (repo_id, "broken.php", "blob", 0o100644, 1, "php", "target"),
    ).lastrowid
    conn.execute(
        """INSERT INTO symbols(
               repo_id,file_id,name,kind,start_line,end_line,language,stable_key
           ) VALUES(?,?,?,?,?,?,?,?)""",
        (repo_id, file_id, "broken", "function", 0, 1, "php", "stable"),
    )
    conn.commit()
    conn.close()

    try:
        _validate_candidate(candidate, target_commit_sha="target", build_token="token")
    except RepoV1Error as exc:
        assert str(exc) == "candidate symbol facts are invalid"
    else:
        raise AssertionError("invalid symbol line range was accepted")


def test_candidate_validation_rejects_orphan_symbols(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.db"
    conn = sqlite3.connect(candidate)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text())
    build_id = conn.execute(
        """INSERT INTO catalog_builds(
               build_token,catalog_path,status,source_revisions_json
           ) VALUES(?,?,?,?)""",
        ("token", str(candidate), "validated", '{"ia-main":"target"}'),
    ).lastrowid
    repo_id = conn.execute(
        """INSERT INTO repos(
               repo_key,name,kind,language,local_root,tracked_branch,
               target_commit_sha,build_id
           ) VALUES(?,?,?,?,?,?,?,?)""",
        ("ia-main", "Fixture", "monorepo", "php", str(tmp_path), "main", "target", build_id),
    ).lastrowid
    file_id = conn.execute(
        """INSERT INTO files(
               repo_id,path,blob_object_id,file_mode,size_bytes,language,source_commit_sha
           ) VALUES(?,?,?,?,?,?,?)""",
        (repo_id, "orphan.php", "blob", 0o100644, 1, "php", "target"),
    ).lastrowid
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        """INSERT INTO symbols(
               repo_id,file_id,name,kind,start_line,end_line,language,stable_key
           ) VALUES(?,?,?,?,?,?,?,?)""",
        (999, file_id, "orphan", "function", 1, 1, "php", "stable"),
    )
    conn.commit()
    conn.close()

    try:
        _validate_candidate(candidate, target_commit_sha="target", build_token="token")
    except RepoV1Error as exc:
        assert str(exc).startswith("candidate foreign-key check failed:")
    else:
        raise AssertionError("orphan symbol was accepted")


def test_parser_failure_retains_inventory_and_emits_no_symbols(tmp_path: Path) -> None:
    root, manifest = _fixture(tmp_path)
    broken = root / "broken.yaml"
    broken.write_text("key: [\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "broken yaml")
    target = _git(root, "rev-parse", "HEAD")
    active = tmp_path / "active.db"

    build_ia_main(manifest_path=manifest, active_db=active, target_sha=target)

    conn = sqlite3.connect(active)
    try:
        assert conn.execute("SELECT COUNT(*) FROM files WHERE path='broken.yaml'").fetchone()[0] == 1
        assert conn.execute(
            """SELECT COUNT(*) FROM symbols s JOIN files f ON f.id=s.file_id
               WHERE f.path='broken.yaml'"""
        ).fetchone()[0] == 0
        diagnostic = conn.execute(
            """SELECT d.severity,d.code,d.source_commit_sha
               FROM symbol_diagnostics d JOIN files f ON f.id=d.file_id
               WHERE f.path='broken.yaml'"""
        ).fetchone()
        assert diagnostic == ("error", "invalid_yaml_syntax", target)
    finally:
        conn.close()
