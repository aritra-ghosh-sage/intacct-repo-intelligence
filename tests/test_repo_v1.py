from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
from pathlib import Path, PurePosixPath
from unittest.mock import patch

import pytest

from catalog.refresh_transaction import (
    CatalogPromotionError,
    assert_parent_unchanged,
    parent_descriptor,
)
from catalog.repo_v1 import (
    DEFAULT_ACTIVE_DB,
    SCHEMA_PATH,
    RepoV1Error,
    _v1_detect_language,
    build_ia_main,
)
from catalog.repositories import load_workspace_manifest
from parser.scan_repo import detect_language

_IGNORED_FILENAMES = (".gitattributes", ".gitignore", ".gitkeep", "Makefile")
_IGNORED_FILENAME_PREFIXES = (".env",)
_IGNORED_SUFFIXES = (
    ".bin",
    ".cert",
    ".cfg",
    ".conf",
    ".crt",
    ".csv",
    ".deploy",
    ".dll",
    ".doc",
    ".docx",
    ".eot",
    ".exe",
    ".gif",
    ".jar",
    ".key",
    ".md",
    ".mo",
    ".pdf",
    ".pem",
    ".pfg",
    ".pfm",
    ".po",
    ".png",
    ".pot",
    ".properties",
    ".sh",
    ".svg",
    ".swf",
    ".tmpl",
    ".ttf",
    ".txt",
    ".xls",
    ".xlsx",
    ".woff",
    ".woff2",
)
# Keep the oracle's expectations independent from the V1 implementation map.
_ORACLE_LANGUAGE_BY_SUFFIX = {
    ".java": "java",
    ".php": "php",
    ".inc": "php",
    ".menu": "php",
    ".pol": "php",
    ".ent": "php",
    ".cls": "php",
    ".phtml": "php",
    ".cqry": "php",
    ".qry": "php",
    ".rpt": "php",
    ".wfl": "php",
    ".map": "php",
    ".shortcuts": "php",
    ".js": "javascript",
    ".ts": "typescript",
    ".sql": "sql",
    ".xml": "xml",
    ".xsd": "xml",
    ".wsdl": "xml",
    ".json": "json",
    ".py": "python",
    ".yaml": "yaml",
    ".html": "html",
    ".xsl": "xslt",
    ".xslt": "xslt",
}


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _repo(
    tmp_path: Path,
    *,
    symlink: bool = False,
    ignore_paths: list[str] | None = None,
    ignore_filenames: list[str] | None = None,
    ignore_filename_prefixes: list[str] | None = None,
    ignore_suffixes: list[str] | None = None,
) -> tuple[Path, Path]:
    root = tmp_path / "source"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.test")
    _git(root, "config", "user.name", "Test")
    (root / "app").mkdir()
    (root / "app" / "main.php").write_text("<?php echo 'committed';\n")
    (root / "README.php").write_text("inventory\n")
    if symlink:
        (root / "link").symlink_to("README.php")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "fixture")
    ignore_block = "    ignore_paths: []\n"
    if ignore_paths:
        ignore_block = "    ignore_paths:\n" + "".join(
            f"      - {path}\n" for path in ignore_paths
        )
    filenames = _IGNORED_FILENAMES if ignore_filenames is None else ignore_filenames
    filename_prefixes = (
        _IGNORED_FILENAME_PREFIXES
        if ignore_filename_prefixes is None
        else ignore_filename_prefixes
    )
    suffixes = _IGNORED_SUFFIXES if ignore_suffixes is None else ignore_suffixes
    ignore_filenames_block = "    ignore_filenames:\n" + "".join(
        f"      - {name}\n" for name in filenames
    )
    ignore_prefixes_block = "    ignore_filename_prefixes:\n" + "".join(
        f"      - {prefix}\n" for prefix in filename_prefixes
    )
    ignore_suffixes_block = "    ignore_suffixes:\n" + "".join(
        f"      - {suffix}\n" for suffix in suffixes
    )
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
{ignore_block}{ignore_filenames_block}{ignore_prefixes_block}{ignore_suffixes_block}    builders: []
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
                _ORACLE_LANGUAGE_BY_SUFFIX.get(
                    PurePosixPath(path).suffix.lower(), "unknown"
                ),
                commit,
            )
        )
    return sorted(rows)


def _filtered_git_tree_oracle(root: Path, commit: str, manifest: Path) -> list[tuple]:
    entry = load_workspace_manifest(manifest)["repositories"][0]
    ignored_paths = tuple(entry["ignore_paths"])
    ignored_filenames = set(entry["ignore_filenames"])
    ignored_prefixes = tuple(entry["ignore_filename_prefixes"])
    ignored_suffixes = set(entry["ignore_suffixes"])
    rows = _git_tree_oracle(root, commit)
    return [
        row
        for row in rows
        if not any(
            part.startswith(".")
            for part in PurePosixPath(row[0]).parts[:-1]
        )
        and row[0].rsplit("/", 1)[-1] not in ignored_filenames
        and not any(
            row[0].rsplit("/", 1)[-1].lower().startswith(prefix)
            for prefix in ignored_prefixes
        )
        and PurePosixPath(row[0]).suffix.lower()
        not in ignored_suffixes
        and not any(
            row[0] == ignored or row[0].startswith(f"{ignored}/")
            for ignored in ignored_paths
        )
    ]


def _rich_repo(tmp_path: Path) -> tuple[Path, Path, str]:
    root, manifest = _repo(tmp_path)
    executable = root / "run.php"
    executable.write_bytes(b"#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    (root / "empty.dat").write_bytes(b"")
    (root / "binary.data").write_bytes(b"\x00\x01\xff\x80\n")
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


def test_inventory_matches_filtered_git_tree_oracle(tmp_path: Path) -> None:
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
    assert rows == _filtered_git_tree_oracle(root, commit, manifest)
    assert any(row[0] == "run.php" and row[2] == 0o100755 for row in rows)
    assert any(row[0] == "empty.dat" and row[3] == 0 for row in rows)
    assert any(row[0] == "binary.data" and row[4] == "unknown" for row in rows)


def test_v1_inventory_progress_indicator_is_opt_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, manifest = _repo(tmp_path)
    commit = _git(root, "rev-parse", "HEAD")
    calls: list[dict[str, object]] = []

    def fake_tqdm(iterable, **kwargs):
        calls.append(kwargs)
        return iterable

    monkeypatch.setattr("catalog.repo_v1.tqdm", fake_tqdm)
    build_ia_main(
        manifest_path=manifest,
        active_db=tmp_path / "quiet.db",
        target_sha=commit,
    )
    assert calls == [{"desc": "Writing V1 inventory", "unit": "file", "disable": True}]

    calls.clear()
    build_ia_main(
        manifest_path=manifest,
        active_db=tmp_path / "shown.db",
        target_sha=commit,
        show_progress=True,
    )
    assert calls == [{"desc": "Writing V1 inventory", "unit": "file", "disable": False}]


def test_v1_cli_and_library_use_canonical_active_database_path() -> None:
    assert DEFAULT_ACTIVE_DB == Path("catalog/catalog.db")
    assert build_ia_main.__kwdefaults__["active_db"] == DEFAULT_ACTIVE_DB


def test_inventory_applies_v1_tree_filters_and_manifest_ignore_paths(
    tmp_path: Path,
) -> None:
    root, manifest = _repo(
        tmp_path,
        ignore_paths=["app/resources/thirdparty/", "app/resources/thirdparty"],
    )
    (root / ".github").mkdir()
    (root / ".github" / "workflow.yml").write_text("ignored\n")
    (root / ".idea").mkdir()
    (root / ".idea" / "settings.xml").write_text("ignored\n")
    (root / ".vscode").mkdir()
    (root / ".vscode" / "settings.json").write_text("ignored\n")
    (root / "src" / ".hidden").mkdir(parents=True)
    (root / "src" / ".hidden" / "secret.php").write_text("ignored\n")
    (root / ".gitignore").write_text("ignored\n")
    (root / ".gitkeep").write_text("ignored\n")
    (root / ".gitattributes").write_text("ignored\n")
    (root / ".env").write_text("ignored\n")
    (root / ".env.local").write_text("ignored\n")
    (root / "Makefile").write_text("ignored\n")
    for suffix in _IGNORED_SUFFIXES:
        (root / f"ignored{suffix.upper()}").write_bytes(b"ignored")
    (root / "app" / "resources" / "thirdparty").mkdir(parents=True)
    (root / "app" / "resources" / "thirdparty" / "library.php").write_text(
        "ignored\n"
    )
    (root / "retained.PHP").write_text("retained\n")
    commit = _commit(root, "add V1 inventory exclusions")
    active = tmp_path / "active.db"

    build_ia_main(manifest_path=manifest, active_db=active, target_sha=commit)

    conn = sqlite3.connect(active)
    try:
        rows = conn.execute(
            "SELECT path,blob_object_id,file_mode,size_bytes,language,source_commit_sha "
            "FROM files ORDER BY path"
        ).fetchall()
    finally:
        conn.close()
    assert rows == _filtered_git_tree_oracle(root, commit, manifest)
    assert "retained.PHP" in {row[0] for row in rows}
    assert all("/." not in f"/{row[0]}" for row in rows)


def test_manifest_normalizes_v1_ignore_lists(tmp_path: Path) -> None:
    _root, manifest = _repo(
        tmp_path,
        ignore_filenames=["Makefile", "Makefile"],
        ignore_filename_prefixes=[".ENV", ".env"],
        ignore_suffixes=[".CSV", ".csv"],
    )

    entry = load_workspace_manifest(manifest)["repositories"][0]

    assert entry["ignore_filenames"] == ["Makefile"]
    assert entry["ignore_filename_prefixes"] == [".env"]
    assert entry["ignore_suffixes"] == [".csv"]


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


@pytest.mark.parametrize(
    ("contents", "error"),
    [
        (b"", "active catalog is empty"),
        (b"not a sqlite database", "not a readable SQLite database"),
    ],
)
def test_invalid_existing_active_catalog_fails_closed(
    tmp_path: Path, contents: bytes, error: str
) -> None:
    _root, manifest = _repo(tmp_path)
    active = tmp_path / "active.db"
    active.write_bytes(contents)

    with pytest.raises(CatalogPromotionError, match=error):
        build_ia_main(manifest_path=manifest, active_db=active)

    assert active.read_bytes() == contents
    assert not list(active.parent.glob(f".{active.name}.candidate.*"))


def test_incompatible_existing_catalog_fails_closed(tmp_path: Path) -> None:
    _root, manifest = _repo(tmp_path)
    active = tmp_path / "active.db"
    conn = sqlite3.connect(active)
    try:
        conn.executescript((Path(__file__).parents[1] / "catalog/schema.sql").read_text())
        conn.commit()
    finally:
        conn.close()
    before = active.read_bytes()

    with pytest.raises(CatalogPromotionError, match="schema is incompatible"):
        build_ia_main(manifest_path=manifest, active_db=active)

    assert active.read_bytes() == before
    assert not list(active.parent.glob(f".{active.name}.candidate.*"))


def test_partial_v1_catalog_fails_closed(tmp_path: Path) -> None:
    _root, manifest = _repo(tmp_path)
    active = tmp_path / "active.db"
    conn = sqlite3.connect(active)
    try:
        conn.execute(
            "CREATE TABLE catalog_builds ("
            "id INTEGER PRIMARY KEY, build_token TEXT NOT NULL, "
            "catalog_path TEXT NOT NULL, status TEXT NOT NULL, "
            "source_revisions_json TEXT NOT NULL)"
        )
        conn.commit()
    finally:
        conn.close()
    before = active.read_bytes()

    with pytest.raises(CatalogPromotionError, match="missing tables"):
        build_ia_main(manifest_path=manifest, active_db=active)

    assert active.read_bytes() == before
    assert not list(active.parent.glob(f".{active.name}.candidate.*"))


def test_complete_v1_schema_without_active_build_fails_closed(tmp_path: Path) -> None:
    _root, manifest = _repo(tmp_path)
    active = tmp_path / "active.db"
    conn = sqlite3.connect(active)
    try:
        conn.executescript(SCHEMA_PATH.read_text())
        conn.commit()
    finally:
        conn.close()
    before = active.read_bytes()

    with pytest.raises(CatalogPromotionError, match="no active build"):
        build_ia_main(manifest_path=manifest, active_db=active)

    assert active.read_bytes() == before
    assert not list(active.parent.glob(f".{active.name}.candidate.*"))


def test_v1_schema_with_extra_table_fails_closed(tmp_path: Path) -> None:
    _root, manifest = _repo(tmp_path)
    active = tmp_path / "active.db"
    conn = sqlite3.connect(active)
    try:
        conn.executescript(SCHEMA_PATH.read_text())
        conn.execute("CREATE TABLE unrelated_extra (id INTEGER PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()
    before = active.read_bytes()

    with pytest.raises(CatalogPromotionError, match="unexpected tables"):
        build_ia_main(manifest_path=manifest, active_db=active)

    assert active.read_bytes() == before
    assert not list(active.parent.glob(f".{active.name}.candidate.*"))


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
    _git(root, "mv", "README.php", "README-renamed.php")
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
    assert "README-renamed.php" in paths
    assert "README.php" not in paths


def test_deletion_commit_removes_deleted_path_from_full_inventory(tmp_path: Path) -> None:
    root, manifest = _repo(tmp_path)
    target = root / "README.php"
    target.unlink()
    commit = _commit(root, "delete README")
    active = tmp_path / "active.db"

    build_ia_main(manifest_path=manifest, active_db=active, target_sha=commit)

    conn = sqlite3.connect(active)
    try:
        paths = {row[0] for row in conn.execute("SELECT path FROM files")}
    finally:
        conn.close()
    assert "README.php" not in paths


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
    (root / "link").symlink_to("README.php")
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


def test_legacy_parser_does_not_define_v1_local_wfl_mapping() -> None:
    assert detect_language("workflow.WFL") == "unknown"


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("Makefile", "unknown"),
        ("schema.XSD", "xml"),
        ("service.WSDL", "xml"),
        ("modules.MAP", "php"),
        ("menu.SHORTCUTS", "php"),
    ],
)
def test_v1_language_classification(path: str, expected: str) -> None:
    assert _v1_detect_language(path) == expected
