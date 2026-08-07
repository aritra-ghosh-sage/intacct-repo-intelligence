from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from catalog.refresh_transaction import CatalogPromotionError
from catalog.repo_v1 import RepoV1Error, build_ia_main
from catalog.repo_v1_nextgen import EXTRACTOR, EXTRACTOR_VERSION


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _fixture(tmp_path: Path, files: dict[str, bytes]) -> tuple[Path, Path, str]:
    root = tmp_path / "source"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.test")
    _git(root, "config", "user.name", "Test")
    (root / "README.php").write_text("inventory\n")
    for path, content in files.items():
        destination = root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "NextGen fixture")
    commit = _git(root, "rev-parse", "HEAD")
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        f"""version: 1
repositories:
  - repo_key: ia-main
    name: Fixture
    kind: monorepo
    language: yaml
    remote_url: https://example.test/repo.git
    local_root: {root}
    tracked_branch: main
    enabled: true
    profile: null
    depends_on: null
    ignore_paths: []
    ignore_filenames: []
    ignore_filename_prefixes: []
    ignore_suffixes: []
    builders: []
"""
    )
    return root, manifest, commit


def _build(tmp_path: Path, files: dict[str, bytes]) -> tuple[Path, Path, str]:
    root, manifest, commit = _fixture(tmp_path, files)
    db = tmp_path / "catalog.db"
    build_ia_main(manifest_path=manifest, active_db=db, target_sha=commit)
    return db, root, commit


def _logical_rows(db: Path, table: str, columns: str) -> list[tuple]:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(
            f"SELECT {columns} FROM {table} ORDER BY {columns.split(',')[0]}"
        ).fetchall()
    finally:
        conn.close()


def test_schema_columns_constraints_indexes_and_composite_foreign_keys(
    tmp_path: Path,
) -> None:
    db, _root, _commit = _build(
        tmp_path,
        {"app/objects.gl.widget.s1.uimeta.yaml": b"{}\n"},
    )
    conn = sqlite3.connect(db)
    try:
        expected_columns = {
            "nextgen_families": [
                "id", "repo_id", "family_key", "source_file_id", "source_path",
                "source_commit_sha", "source_hash", "start_line", "end_line",
                "evidence", "extractor", "extractor_version",
            ],
            "nextgen_artifacts": [
                "id", "repo_id", "family_id", "artifact_key", "artifact_kind",
                "file_id", "source_path", "source_commit_sha", "source_hash",
                "start_line", "end_line", "evidence", "extractor",
                "extractor_version",
            ],
            "nextgen_diagnostics": [
                "id", "repo_id", "file_id", "diagnostic_key", "severity", "code",
                "message", "source_commit_sha", "source_hash", "start_line",
                "end_line", "evidence", "extractor", "extractor_version",
            ],
        }
        for table, columns in expected_columns.items():
            assert [row[1] for row in conn.execute(f"PRAGMA table_info({table})")] == columns
            assert conn.execute(f"PRAGMA index_list({table})").fetchall()
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        foreign_keys = {
            table: conn.execute(f"PRAGMA foreign_key_list({table})").fetchall()
            for table in expected_columns
        }
        assert any(row[2] == "files" and row[4] == "id" for row in foreign_keys["nextgen_families"])
        assert any(row[2] == "nextgen_families" for row in foreign_keys["nextgen_artifacts"])
        assert sum(row[2] == "files" for row in foreign_keys["nextgen_artifacts"]) == 2
        assert any(row[2] == "files" for row in foreign_keys["nextgen_diagnostics"])
    finally:
        conn.close()


def test_valid_kinds_multiple_artifacts_filename_family_and_summary(
    tmp_path: Path,
) -> None:
    db, _root, commit = _build(
        tmp_path,
        {
            "app/objects.gl.widget.s1.uimeta.yaml": b"{}\n",
            "app/objects.gl.widget.s1.viewmeta.yaml": b"object: gl/widget\n",
            "app/objects.gl.widget.s1.view.yaml": b"object: gl/widget\n",
            "app/template/objects.gl.template.s1.view.yaml": b"object: gl/template\n",
            "app/tests/fixtures/objects.ar.fixture.s1.view.yaml": b"object: ar/fixture\n",
        },
    )
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        assert conn.execute("SELECT COUNT(*) FROM nextgen_families").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM nextgen_artifacts").fetchone()[0] == 5
        family = conn.execute(
            "SELECT * FROM nextgen_families WHERE family_key='gl/widget'"
        ).fetchone()
        assert family["source_path"] == "app/objects.gl.widget.s1.uimeta.yaml"
        assert family["source_commit_sha"] == commit
        assert family["extractor"] == EXTRACTOR
        assert family["extractor_version"] == EXTRACTOR_VERSION
        summary = json.loads(
            conn.execute("SELECT validation_summary FROM catalog_builds").fetchone()[0]
        )
        assert summary["nextgen_family_count"] == 3
        assert summary["nextgen_artifact_count"] == 5
        assert summary["nextgen_diagnostic_count"] == 0
    finally:
        conn.close()


def test_yaml_failures_invalid_objects_and_entity_diagnostics_are_filtered(
    tmp_path: Path,
) -> None:
    db, _root, commit = _build(
        tmp_path,
        {
            "app/empty.view.yaml": b" \n\t",
            "app/scalar.view.yaml": b"scalar\n",
            "app/list.view.yaml": b"- item\n",
            "app/null.view.yaml": b"null\n",
            "app/broken.view.yaml": b"object: [\n",
            "app/invalid.view.yaml": b"object: scalar\n",
            "app/unresolved.view.yaml": b"{}\n",
        },
    )
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT f.path AS source_path,d.code,d.severity,d.start_line,d.end_line,
                      d.source_commit_sha
                 FROM nextgen_diagnostics d JOIN files f ON f.id=d.file_id
                ORDER BY f.path"""
        ).fetchall()
        assert len(rows) == 7
        assert {row["code"] for row in rows} == {
            "nextgen.yaml.document_not_mapping",
            "nextgen.yaml.invalid",
            "nextgen.family.invalid_object",
            "nextgen.family.unresolved",
        }
        assert all(row["source_commit_sha"] == commit for row in rows)
        assert all(row["severity"] == "error" for row in rows if row["code"].startswith("nextgen.yaml"))
        assert all(row["severity"] == "warning" for row in rows if row["code"].startswith("nextgen.family"))
        null_evidence = conn.execute(
            "SELECT evidence FROM nextgen_diagnostics WHERE code='nextgen.yaml.document_not_mapping' LIMIT 1"
        ).fetchone()[0]
        assert json.loads(null_evidence)["parser_fact"]["evidence"] is None
        assert json.dumps(
            json.loads(null_evidence), sort_keys=True, separators=(",", ":"),
            ensure_ascii=True, allow_nan=False
        ) == null_evidence
        assert conn.execute("SELECT COUNT(*) FROM nextgen_families").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM nextgen_artifacts").fetchone()[0] == 0
        assert not conn.execute(
            "SELECT 1 FROM nextgen_diagnostics WHERE code LIKE 'nextgen.entity_mapping.%'"
        ).fetchone()
    finally:
        conn.close()


def test_canonical_evidence_keys_null_and_raw_hashes(tmp_path: Path) -> None:
    content = "object: gl/widget\nlabel: é\n".encode()
    db, _root, commit = _build(
        tmp_path,
        {"app/objects.gl.widget.s1.view.yaml": content},
    )
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM nextgen_artifacts").fetchone()
        source_hash = hashlib.sha256(content).hexdigest()
        assert row["source_hash"] == source_hash
        assert row["artifact_key"] == json.dumps(
            {
                "artifact_kind": "view",
                "family_key": "gl/widget",
                "source_path": "app/objects.gl.widget.s1.view.yaml",
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        evidence = json.loads(row["evidence"])
        assert evidence["source_commit_sha"] == commit
        assert evidence["source_hash"] == source_hash
        assert evidence["source_lines"] == {"start": 1, "end": 1}
        assert evidence["parser_fact"]["evidence"] == "gl/widget"
        assert json.dumps(
            evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
        ) == row["evidence"]
    finally:
        conn.close()


def test_dirty_checkout_does_not_change_nextgen_rows(tmp_path: Path) -> None:
    source = b"object: gl/widget\n"
    root, manifest, commit = _fixture(
        tmp_path, {"app/objects.gl.widget.s1.view.yaml": source}
    )
    first = tmp_path / "first.db"
    second = tmp_path / "second.db"
    build_ia_main(manifest_path=manifest, active_db=first, target_sha=commit)
    (root / "app/objects.gl.widget.s1.view.yaml").write_bytes(b"object: dirty/value\n")
    build_ia_main(manifest_path=manifest, active_db=second, target_sha=commit)
    for table, columns in (
        ("nextgen_families", "repo_id,family_key,source_path,source_commit_sha,source_hash,start_line,end_line,evidence"),
        ("nextgen_artifacts", "repo_id,artifact_key,artifact_kind,source_path,source_commit_sha,source_hash,start_line,end_line,evidence"),
        ("nextgen_diagnostics", "repo_id,diagnostic_key,code,severity,message,source_commit_sha,source_hash,start_line,end_line,evidence"),
    ):
        assert _logical_rows(first, table, columns) == _logical_rows(second, table, columns)


def test_parent_truth_table_rejects_partial_phase7a_and_phase7b(
    tmp_path: Path,
) -> None:
    db, _root, _commit = _build(
        tmp_path,
        {"app/objects.gl.widget.s1.view.yaml": b"object: gl/widget\n"},
    )
    conn = sqlite3.connect(db)
    try:
        conn.execute("DROP TABLE nextgen_artifacts")
        conn.commit()
    finally:
        conn.close()
    before = db.read_bytes()
    with pytest.raises(CatalogPromotionError, match="partial NextGen table set"):
        build_ia_main(manifest_path=tmp_path / "manifest.yaml", active_db=db)
    assert db.read_bytes() == before


def test_failed_nextgen_validation_preserves_active_and_previous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, _root, _commit = _build(
        tmp_path,
        {"app/objects.gl.widget.s1.view.yaml": b"object: gl/widget\n"},
    )
    manifest = tmp_path / "manifest.yaml"
    build_ia_main(manifest_path=manifest, active_db=db)
    previous = db.with_name("catalog.db.previous")
    active_before = db.read_bytes()
    previous_before = previous.read_bytes()

    def fail(*_args, **_kwargs):
        raise RuntimeError("injected NextGen validation failure")

    monkeypatch.setattr("catalog.repo_v1.validate_nextgen_candidate", fail)
    with pytest.raises(RepoV1Error, match="injected NextGen validation failure"):
        build_ia_main(manifest_path=manifest, active_db=db)
    assert db.read_bytes() == active_before
    assert previous.read_bytes() == previous_before
    assert not list(db.parent.glob(f".{db.name}.candidate.*"))
