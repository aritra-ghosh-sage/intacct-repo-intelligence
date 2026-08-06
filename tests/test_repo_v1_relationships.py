from __future__ import annotations

import hashlib
import sqlite3
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from catalog import repo_v1_relationships
from catalog.repo_v1 import SCHEMA_PATH, RepoV1Error, _validate_candidate, build_ia_main
from catalog.source_snapshot import SourceSnapshotError


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _fixture(tmp_path: Path) -> tuple[Path, Path, str]:
    root = tmp_path / "source"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.test")
    _git(root, "config", "user.name", "Test")
    (root / "targets.php").write_text(
        "<?php\nclass ParentClass {}\ninterface Contract {}\n",
        encoding="utf-8",
    )
    (root / "child.php").write_text(
        "<?php\n"
        "class Child extends ParentClass implements Contract {\n"
        "    public function go() {\n"
        "        new MissingClass();\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    (root / "notes.unknown").write_bytes(b"not a supported relationship language")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "relationship fixture")
    target = _git(root, "rev-parse", "HEAD")
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
    return root, manifest, target


def _normalized_rows(db: Path) -> list[tuple]:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(
            """SELECT f.path,r.source_name,r.source_kind,r.target_name,r.target_kind,
                      r.relationship_type,r.language,r.confidence,r.evidence,
                      r.resolution_class,r.resolution_reason,r.extractor
               FROM relationships r JOIN files f ON f.id=r.file_id
               ORDER BY f.path,r.relationship_type,r.target_name,r.evidence"""
        ).fetchall()
    finally:
        conn.close()


def test_relationships_resolve_and_preserve_unresolved_targets(tmp_path: Path) -> None:
    _root, manifest, target = _fixture(tmp_path)
    db = tmp_path / "catalog.db"
    build_ia_main(manifest_path=manifest, active_db=db, target_sha=target)

    conn = sqlite3.connect(db)
    try:
        rows = conn.execute(
            """SELECT r.source_symbol_id,r.target_symbol_id,r.target_name,
                      r.resolution_class,r.resolution_reason,r.file_id,r.repo_id,
                      f.id,f.repo_id
               FROM relationships r JOIN files f ON f.id=r.file_id"""
        ).fetchall()
        assert rows
        assert all(row[7] == row[5] and row[8] == row[6] for row in rows)
        resolved = conn.execute(
            """SELECT r.target_symbol_id,s.id,r.target_name,s.name
               FROM relationships r JOIN symbols s ON s.id=r.target_symbol_id
               WHERE r.target_name='ParentClass'"""
        ).fetchone()
        assert resolved is not None and resolved[0] == resolved[1] and resolved[2] == resolved[3]
        unresolved = conn.execute(
            """SELECT target_symbol_id,target_name,resolution_class,resolution_reason
               FROM relationships WHERE target_name='MissingClass'"""
        ).fetchone()
        assert unresolved == (
            None,
            "MissingClass",
            "project_unresolved",
            "unresolved_project_symbol",
        )
        assert conn.execute(
            "SELECT COUNT(*) FROM relationships WHERE file_path='notes.unknown'"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_relationships_are_snapshot_provenance_and_repetition_stable(
    tmp_path: Path,
) -> None:
    root, manifest, target = _fixture(tmp_path)
    first = tmp_path / "first.db"
    second = tmp_path / "second.db"
    build_ia_main(manifest_path=manifest, active_db=first, target_sha=target)
    committed_child = _git(root, "show", f"{target}:child.php")
    (root / "child.php").write_text("<?php class MutableCheckout {}\n", encoding="utf-8")
    build_ia_main(manifest_path=manifest, active_db=second, target_sha=target)

    assert _normalized_rows(first) == _normalized_rows(second)
    assert all(
        row[8] in committed_child for row in _normalized_rows(first) if row[0] == "child.php"
    )
    conn = sqlite3.connect(first)
    try:
        target_sha = conn.execute("SELECT target_commit_sha FROM repos").fetchone()[0]
        assert target_sha == target
    finally:
        conn.close()


def test_duplicate_relationships_are_deterministically_deduplicated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _root, manifest, target = _fixture(tmp_path)
    original = repo_v1_relationships.EXTRACTORS["php"]

    def duplicate(*args: object) -> list[object]:
        rows = list(original(*args))
        return rows + rows

    monkeypatch.setitem(repo_v1_relationships.EXTRACTORS, "php", duplicate)
    db = tmp_path / "catalog.db"
    build_ia_main(manifest_path=manifest, active_db=db, target_sha=target)
    rows = _normalized_rows(db)
    assert len(rows) == len(set(rows))


def test_failed_relationship_file_has_no_partial_rows_and_preserves_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _root, manifest, target = _fixture(tmp_path)
    db = tmp_path / "catalog.db"
    build_ia_main(manifest_path=manifest, active_db=db, target_sha=target)
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    original = repo_v1_relationships.EXTRACTORS["php"]

    def fail_on_targets(text: str, file_row: object, *args: object) -> list[object]:
        if getattr(file_row, "path") == "targets.php":
            raise ValueError("injected relationship failure")
        return list(original(text, file_row, *args))

    monkeypatch.setitem(repo_v1_relationships.EXTRACTORS, "php", fail_on_targets)
    with pytest.raises(RepoV1Error, match="relationship extraction failed"):
        build_ia_main(manifest_path=manifest, active_db=db, target_sha=target)
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    assert not list(tmp_path.glob(".catalog.db.candidate.*.db"))


def test_snapshot_relationship_read_failure_preserves_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _root, manifest, target = _fixture(tmp_path)
    db = tmp_path / "catalog.db"
    build_ia_main(manifest_path=manifest, active_db=db, target_sha=target)
    before = db.read_bytes()

    def fail(_snapshot: object, path: str) -> str:
        raise SourceSnapshotError(f"cannot read {path}")

    monkeypatch.setattr(repo_v1_relationships, "_read_snapshot_text", fail)
    with pytest.raises(RepoV1Error, match="cannot read"):
        build_ia_main(manifest_path=manifest, active_db=db, target_sha=target)
    assert db.read_bytes() == before


def test_invalid_relationship_target_reference_rejects_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _root, manifest, target = _fixture(tmp_path)
    db = tmp_path / "catalog.db"
    build_ia_main(manifest_path=manifest, active_db=db, target_sha=target)
    before = db.read_bytes()
    original = repo_v1_relationships.EXTRACTORS["php"]

    def invalid_target(*args: object) -> list[object]:
        return [replace(row, target_symbol_id=999999) for row in original(*args)]

    monkeypatch.setitem(repo_v1_relationships.EXTRACTORS, "php", invalid_target)
    with pytest.raises(RepoV1Error, match="FOREIGN KEY constraint failed"):
        build_ia_main(manifest_path=manifest, active_db=db, target_sha=target)
    assert db.read_bytes() == before


def test_candidate_validation_rejects_cross_file_relationship_source(
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "candidate.db"
    conn = sqlite3.connect(candidate)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA_PATH.read_text())
    build_id = conn.execute(
        """INSERT INTO catalog_builds(
               build_token,catalog_path,status,source_revisions_json
           ) VALUES(?,?,?,?)""",
        ("token", str(candidate), "validated", '{"ia-main":"target"}'),
    ).lastrowid
    repo_id = conn.execute(
        """INSERT INTO repos(
               repo_key,local_root,tracked_branch,target_commit_sha,build_id
           ) VALUES(?,?,?,?,?)""",
        ("ia-main", str(tmp_path), "main", "target", build_id),
    ).lastrowid
    first_file = conn.execute(
        """INSERT INTO files(
               repo_id,path,blob_object_id,file_mode,size_bytes,language,source_commit_sha
           ) VALUES(?,?,?,?,?,?,?)""",
        (repo_id, "first.php", "blob1", 0o100644, 1, "php", "target"),
    ).lastrowid
    second_file = conn.execute(
        """INSERT INTO files(
               repo_id,path,blob_object_id,file_mode,size_bytes,language,source_commit_sha
           ) VALUES(?,?,?,?,?,?,?)""",
        (repo_id, "second.php", "blob2", 0o100644, 1, "php", "target"),
    ).lastrowid
    symbol_id = conn.execute(
        """INSERT INTO symbols(
               repo_id,file_id,name,kind,start_line,end_line,language,stable_key
           ) VALUES(?,?,?,?,?,?,?,?)""",
        (repo_id, second_file, "Other", "class", 1, 1, "php", "stable"),
    ).lastrowid
    conn.execute(
        """INSERT INTO relationships(
               repo_id,source_symbol_id,source_name,source_kind,target_name,
               relationship_type,file_id,file_path,language,confidence,evidence,
               resolution_class,resolution_reason,extractor
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            repo_id,
            symbol_id,
            "Other",
            "class",
            "Target",
            "REFERENCES",
            first_file,
            "first.php",
            "php",
            0.7,
            "committed evidence",
            "project_unresolved",
            "unresolved_project_symbol",
            "phase2_regex_mvp",
        ),
    )
    conn.commit()
    conn.close()
    with pytest.raises(RepoV1Error, match="relationship ownership"):
        _validate_candidate(candidate, target_commit_sha="target", build_token="token")
