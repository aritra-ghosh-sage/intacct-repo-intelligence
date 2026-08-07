from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from catalog.refresh_transaction import CatalogPromotionError
from catalog.repo_v1 import RepoV1Error, build_ia_main
from catalog.repo_v1_ui import normalize_include_path


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _fixture(tmp_path: Path, forms: dict[str, bytes]) -> tuple[Path, Path, str]:
    root = tmp_path / "source"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.test")
    _git(root, "config", "user.name", "Test")
    (root / "README.php").write_text("inventory\n")
    for path, content in forms.items():
        destination = root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "ActionUI fixture")
    commit = _git(root, "rev-parse", "HEAD")
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        f"""version: 1
repositories:
  - repo_key: ia-main
    name: Fixture
    kind: monorepo
    language: xml
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


def _rows(db: Path, table: str, columns: str) -> list[tuple]:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(f"SELECT {columns} FROM {table} ORDER BY 1").fetchall()
    finally:
        conn.close()


def test_root_empty_and_malformed_forms_materialize_only_expected_rows(
    tmp_path: Path,
) -> None:
    _root, manifest, _commit = _fixture(
        tmp_path,
        {
            "app/root_form.xml": b"<form/>\n",
            "app/empty_form.xml": b" \n\t",
            "app/broken_form.xml": b"<form>",
        },
    )
    db = tmp_path / "catalog.db"
    build_ia_main(manifest_path=manifest, active_db=db)
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM ui_surfaces").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM ui_artifacts").fetchone()[0] == 1
        diagnostics = conn.execute(
            "SELECT code,severity,surface_id FROM ui_diagnostics ORDER BY code,diagnostic_key"
        ).fetchall()
        assert diagnostics == [
            ("actionui.xml.parse_error", "error", None),
            ("actionui.xml.parse_error", "error", None),
        ]
        assert conn.execute("SELECT COUNT(*) FROM ui_fields").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM ui_events").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM ui_includes").fetchone()[0] == 0
    finally:
        conn.close()


def test_facts_provenance_diagnostics_and_include_states(tmp_path: Path) -> None:
    source = b"""<form xmlns:xi="http://www.w3.org/2001/XInclude">
  <field name="alpha"/>
  <field><path>beta</path></field>
  <field/>
  <events><onLoad>ignored()</onLoad><onLoad>again()</onLoad></events>
  <xi:include href="shared.xml"/>
  <xi:include href="missing.xml"/>
  <xi:include href="../../escape.xml"/>
</form>
"""
    _root, manifest, commit = _fixture(
        tmp_path,
        {"app/form_form.xml": source, "app/shared.xml": b"<shared/>\n"},
    )
    db = tmp_path / "catalog.db"
    build_ia_main(manifest_path=manifest, active_db=db)
    source_hash = hashlib.sha256(source).hexdigest()
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        surface = conn.execute("SELECT * FROM ui_surfaces").fetchone()
        artifact = conn.execute("SELECT * FROM ui_artifacts").fetchone()
        assert surface["display_name"] == "form_form"
        assert surface["source_commit_sha"] == commit
        assert surface["source_hash"] == source_hash
        assert (
            artifact["start_line"] == 1
            and artifact["end_line"] == source.decode().count("\n") + 1
        )
        assert json.loads(artifact["evidence"]) == {
            "end_line": artifact["end_line"],
            "source_commit_sha": commit,
            "source_hash": source_hash,
            "source_path": "app/form_form.xml",
            "start_line": 1,
        }
        fields = conn.execute(
            "SELECT field_name,field_path,field_key FROM ui_fields ORDER BY id"
        ).fetchall()
        assert [(row["field_name"], row["field_path"]) for row in fields] == [
            ("alpha", None),
            ("beta", "beta"),
        ]
        events = conn.execute(
            "SELECT event_name,event_key FROM ui_events ORDER BY id"
        ).fetchall()
        assert [row["event_name"] for row in events] == ["onLoad", "onLoad"]
        assert events[0]["event_key"].endswith(":0") and events[1][
            "event_key"
        ].endswith(":1")
        includes = conn.execute(
            "SELECT raw_include_path,resolved_path,resolution_status FROM ui_includes ORDER BY id"
        ).fetchall()
        assert [(row[0], row[1], row[2]) for row in includes] == [
            ("shared.xml", "app/shared.xml", "resolved"),
            ("missing.xml", "app/missing.xml", "unresolved"),
            ("../../escape.xml", None, "invalid"),
        ]
        diagnostics = conn.execute(
            "SELECT code,severity,surface_id,evidence FROM ui_diagnostics ORDER BY code,diagnostic_key"
        ).fetchall()
        assert [(row[0], row[1], row[2]) for row in diagnostics] == [
            ("actionui.include.invalid", "warning", surface["id"]),
            ("actionui.include.unresolved", "warning", surface["id"]),
            ("actionui.xml.field_identity_missing", "warning", surface["id"]),
        ]
        for row in diagnostics:
            evidence = json.loads(row[3])
            assert evidence["source_commit_sha"] == commit
            assert evidence["source_line"] is None and evidence["evidence"] is None
    finally:
        conn.close()


def test_same_line_duplicate_fact_ordinals_cross_numeric_boundary(
    tmp_path: Path,
) -> None:
    duplicate_fields = "".join('<field name="duplicate"/> ' for _ in range(11))
    duplicate_events = "".join("<onLoad>ignored()</onLoad> " for _ in range(11))
    duplicate_includes = "".join('<xi:include href="missing.xml"/> ' for _ in range(11))
    source = (
        '<form xmlns:xi="http://www.w3.org/2001/XInclude">\n'
        f"{duplicate_fields}<events>{duplicate_events}</events> "
        f"{duplicate_includes}\n</form>\n"
    ).encode()
    _root, manifest, _commit = _fixture(tmp_path, {"app/duplicates_form.xml": source})
    db = tmp_path / "catalog.db"

    build_ia_main(manifest_path=manifest, active_db=db)

    conn = sqlite3.connect(db)
    try:
        for table, key_column in (
            ("ui_fields", "field_key"),
            ("ui_events", "event_key"),
            ("ui_includes", "include_key"),
        ):
            keys = [
                row[0]
                for row in conn.execute(f"SELECT {key_column} FROM {table} ORDER BY id")
            ]
            assert len(keys) == 11
            assert [int(key.rsplit(":", 1)[-1]) for key in keys] == list(range(11))
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("raw", "including", "status", "resolved"),
    [
        ("shared.xml", "app/form_form.xml", "resolved", "app/shared.xml"),
        ("./shared.xml", "app/form_form.xml", "resolved", "app/shared.xml"),
        ("..\\shared.xml", "app/forms/form_form.xml", "resolved", "app/shared.xml"),
        ("missing.xml", "app/form_form.xml", "unresolved", "app/missing.xml"),
        ("/shared.xml", "app/form_form.xml", "invalid", None),
        ("C:\\shared.xml", "app/form_form.xml", "invalid", None),
        ("../../shared.xml", "app/form_form.xml", "invalid", None),
    ],
)
def test_include_normalization_is_pure_and_deterministic(
    raw, including, status, resolved
) -> None:
    assert normalize_include_path(raw, including, {"app/shared.xml"}) == (
        status,
        resolved,
    )


def test_schema_constraints_indexes_and_build_summary(tmp_path: Path) -> None:
    _root, manifest, _commit = _fixture(tmp_path, {"app/root_form.xml": b"<form/>"})
    db = tmp_path / "catalog.db"
    build_ia_main(manifest_path=manifest, active_db=db)
    conn = sqlite3.connect(db)
    try:
        expected = {
            "ui_surfaces",
            "ui_artifacts",
            "ui_fields",
            "ui_events",
            "ui_includes",
            "ui_diagnostics",
        }
        actual = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'ui_%'"
            )
        }
        assert actual == expected
        for table in expected:
            assert conn.execute(f"PRAGMA foreign_key_check({table})").fetchall() == []
            assert conn.execute(f"PRAGMA index_list({table})").fetchall()
        summary = json.loads(
            conn.execute("SELECT validation_summary FROM catalog_builds").fetchone()[0]
        )
        assert summary["ui_surface_count"] == 1
        assert summary["ui_artifact_count"] == 1
        assert (
            summary["ui_field_count"]
            == summary["ui_event_count"]
            == summary["ui_include_count"]
            == 0
        )
    finally:
        conn.close()


def test_dirty_checkout_does_not_change_ui_rows(tmp_path: Path) -> None:
    root, manifest, commit = _fixture(
        tmp_path, {"app/root_form.xml": b"<form><field name='committed'/></form>"}
    )
    first = tmp_path / "first.db"
    second = tmp_path / "second.db"
    build_ia_main(manifest_path=manifest, active_db=first, target_sha=commit)
    (root / "app/root_form.xml").write_bytes(b"<form><field name='dirty'/></form>")
    build_ia_main(manifest_path=manifest, active_db=second, target_sha=commit)
    for table, columns in (
        (
            "ui_surfaces",
            "repo_id,surface_key,surface_kind,display_name,source_path,source_commit_sha,extractor,extractor_version,source_hash",
        ),
        (
            "ui_artifacts",
            "repo_id,surface_id,artifact_key,artifact_kind,file_id,source_path,source_commit_sha,start_line,end_line,evidence",
        ),
        (
            "ui_fields",
            "repo_id,artifact_id,field_key,field_name,field_path,source_line,evidence",
        ),
    ):
        assert _rows(first, table, columns) == _rows(second, table, columns)


def test_phase6_upgrade_and_partial_schema_rejection(tmp_path: Path) -> None:
    _root, manifest, _commit = _fixture(tmp_path, {"app/root_form.xml": b"<form/>"})
    upgrade = tmp_path / "upgrade.db"
    build_ia_main(manifest_path=manifest, active_db=upgrade)
    conn = sqlite3.connect(upgrade)
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        for table in (
            "ui_fields",
            "ui_events",
            "ui_includes",
            "ui_artifacts",
            "ui_diagnostics",
            "ui_surfaces",
        ):
            conn.execute(f"DROP TABLE {table}")
        conn.commit()
    finally:
        conn.close()
    result = build_ia_main(manifest_path=manifest, active_db=upgrade)
    assert result.promoted
    assert upgrade.with_name("upgrade.db.previous").exists()

    partial = tmp_path / "partial.db"
    build_ia_main(manifest_path=manifest, active_db=partial)
    conn = sqlite3.connect(partial)
    try:
        conn.execute("DROP TABLE ui_fields")
        conn.commit()
    finally:
        conn.close()
    before = partial.read_bytes()
    with pytest.raises(CatalogPromotionError, match="partial UI table set"):
        build_ia_main(manifest_path=manifest, active_db=partial)
    assert partial.read_bytes() == before


def test_candidate_validation_failure_preserves_active_and_previous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _root, manifest, _commit = _fixture(tmp_path, {"app/root_form.xml": b"<form/>"})
    active = tmp_path / "active.db"
    build_ia_main(manifest_path=manifest, active_db=active)
    before = active.read_bytes()

    def fail(*_args, **_kwargs):
        raise RuntimeError("injected UI validation failure")

    monkeypatch.setattr("catalog.repo_v1.validate_ui_candidate", fail)
    with pytest.raises(RepoV1Error, match="injected UI validation failure"):
        build_ia_main(manifest_path=manifest, active_db=active)
    assert active.read_bytes() == before
    assert not list(active.parent.glob(f".{active.name}.candidate.*"))
