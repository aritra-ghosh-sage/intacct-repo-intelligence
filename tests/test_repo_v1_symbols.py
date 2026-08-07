from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from catalog import repo_v1_symbols
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
    (root / "notes.unknown").write_bytes(b"unsupported inventory language\n")
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
    assert not any(row[0] == "notes.unknown" for row in rows)
    conn = sqlite3.connect(first)
    try:
        ownership = conn.execute(
            """SELECT COUNT(*)
               FROM symbols s
               JOIN files f ON f.id=s.file_id
               JOIN repos r ON r.id=s.repo_id
               WHERE s.repo_id<>f.repo_id
                  OR f.source_commit_sha<>r.target_commit_sha"""
        ).fetchone()[0]
        assert ownership == 0
        assert conn.execute(
            "SELECT language FROM files WHERE path='notes.unknown'"
        ).fetchone()[0] == "unknown"
        assert conn.execute(
            """SELECT COUNT(*) FROM symbols s JOIN files f ON f.id=s.file_id
               WHERE f.path='notes.unknown'"""
        ).fetchone()[0] == 0
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


@pytest.mark.parametrize(
    ("filename", "source", "expected_code", "expected_missing"),
    [
        ("broken.js", "function broken( {\n", "javascript_parse_error", False),
        ("broken.java", "class Broken { void partial() {\n", "java_parse_error", True),
        (
            "broken.php",
            "<?php function partial() {} ???\n",
            "php_parse_error",
            False,
        ),
    ],
)
def test_parser_failure_retains_inventory_and_emits_no_symbols(
    tmp_path: Path,
    filename: str,
    source: str,
    expected_code: str,
    expected_missing: bool,
) -> None:
    root, manifest = _fixture(tmp_path)
    broken = root / filename
    broken.write_text(source, encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "broken javascript")
    target = _git(root, "rev-parse", "HEAD")
    first = tmp_path / "first.db"
    second = tmp_path / "second.db"

    build_ia_main(manifest_path=manifest, active_db=first, target_sha=target)
    build_ia_main(manifest_path=manifest, active_db=second, target_sha=target)

    def facts(db: Path) -> tuple[list[tuple], list[tuple]]:
        conn = sqlite3.connect(db)
        try:
            files = conn.execute(
                "SELECT path,language,source_commit_sha FROM files WHERE path=?",
                (filename,),
            ).fetchall()
            diagnostics = conn.execute(
                """SELECT f.path,d.severity,d.code,d.message,d.diagnostic_key,
                          d.source_commit_sha
                   FROM symbol_diagnostics d JOIN files f ON f.id=d.file_id
                   WHERE f.path=?""",
                (filename,),
            ).fetchall()
            symbols = conn.execute(
                """SELECT COUNT(*) FROM symbols s JOIN files f ON f.id=s.file_id
                   WHERE f.path=?""",
                (filename,),
            ).fetchone()[0]
        finally:
            conn.close()
        return files, diagnostics + [("symbol_count", symbols)]

    first_files, first_diagnostics = facts(first)
    second_files, second_diagnostics = facts(second)
    expected_language = filename.rsplit(".", 1)[-1]
    expected_language = "javascript" if expected_language == "js" else expected_language
    assert first_files == second_files == [(filename, expected_language, target)]
    assert first_diagnostics == second_diagnostics
    assert first_diagnostics[0][0:3] == (filename, "error", expected_code)
    assert first_diagnostics[0][5] == target
    assert first_diagnostics[0][4]
    assert json.loads(first_diagnostics[0][3]).get("is_missing", False) is expected_missing
    assert first_diagnostics[-1] == ("symbol_count", 0)


def test_snapshot_read_failure_rejects_candidate_and_preserves_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _root, manifest = _fixture(tmp_path)
    active = tmp_path / "active.db"
    build_ia_main(manifest_path=manifest, active_db=active)
    before = active.read_bytes()
    original_read_bytes = Path.read_bytes

    def fail_snapshot_read(path: Path) -> bytes:
        if path.name == "README.php":
            raise OSError("injected snapshot read failure")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", fail_snapshot_read)
    with pytest.raises(RepoV1Error, match="injected snapshot read failure"):
        build_ia_main(manifest_path=manifest, active_db=active)

    assert active.read_bytes() == before
    assert not list(active.parent.glob(f".{active.name}.candidate.*"))


def test_invalid_symbol_candidate_leaves_active_database_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, manifest = _fixture(tmp_path)
    active = tmp_path / "active.db"
    build_ia_main(manifest_path=manifest, active_db=active)
    before = active.read_bytes()
    target = _git(root, "rev-parse", "HEAD")
    original = repo_v1_symbols.extract_snapshot_symbols

    def inject_invalid_symbol(*args, **kwargs):
        result = original(*args, **kwargs)
        conn = args[0]
        repo_id = kwargs["repo_id"]
        file_id = conn.execute(
            "SELECT id FROM files WHERE repo_id=? ORDER BY id LIMIT 1", (repo_id,)
        ).fetchone()[0]
        conn.commit()
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(
            """INSERT INTO symbols(
                   repo_id,file_id,name,kind,start_line,end_line,language,stable_key
               ) VALUES(?,?,?,?,?,?,?,?)""",
            (999, file_id, "injected", "function", 1, 1, "php", "injected"),
        )
        conn.commit()
        conn.execute("PRAGMA foreign_keys=ON")
        return result

    monkeypatch.setattr(repo_v1_symbols, "extract_snapshot_symbols", inject_invalid_symbol)
    with pytest.raises(RepoV1Error, match="candidate foreign-key check failed"):
        build_ia_main(manifest_path=manifest, active_db=active, target_sha=target)

    assert active.read_bytes() == before
    assert not list(active.parent.glob(f".{active.name}.candidate.*"))
