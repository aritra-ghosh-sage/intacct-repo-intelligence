from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from catalog.content_fingerprint import logical_content_fingerprint
from catalog.ui_sync import (
    UiSnapshot,
    UiSnapshotError,
    assemble_ui_snapshot,
    synchronize_ui_snapshot,
)

ROOT = Path("/Users/aritra.ghosh/projects/main")


def _conn() -> tuple[sqlite3.Connection, int]:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(Path("catalog/schema.sql").read_text())
    repo_id = int(conn.execute("INSERT INTO repos(repo_key,local_root,tracked_branch) VALUES ('ia-main',?,'main')", (str(ROOT),)).lastrowid)
    return conn, repo_id


def _snapshot() -> UiSnapshot:
    snapshot = UiSnapshot()
    snapshot.add("ui_surfaces", ("actionui:form",), surface_key="actionui:form", surface_kind="actionui_form", display_name="form", source_path="app/source/gl/glbatch_form.xml", source_file_id=1, extractor="test", extractor_version="1", source_hash="hash")
    snapshot.add("ui_artifacts", ("actionui:form", "form:xml"), surface_key="actionui:form", artifact_key="form:xml", artifact_kind="actionui_form", file_id=1, source_path="app/source/gl/glbatch_form.xml", start_line=1, end_line=1, evidence_text="form", source_hash="hash", payload_json="{}")
    snapshot.add("ui_fields", ("actionui:form", "form:xml", "1:BATCHNO:"), surface_key="actionui:form", artifact_key="form:xml", field_key="1:BATCHNO:", field_name="BATCHNO", field_path=None, label=None, field_type=None, ordinal=0, source_line=1, evidence_text="BATCHNO")
    return snapshot


def test_sync_preserves_ids_fingerprint_and_removes_stale_children() -> None:
    conn, repo_id = _conn()
    conn.execute("INSERT INTO files(id,repo_id,path) VALUES (1,?,?)", (repo_id, "app/source/gl/glbatch_form.xml"))
    conn.commit()
    snapshot = _snapshot()
    synchronize_ui_snapshot(conn, repo_id=repo_id, snapshot=snapshot)
    before = logical_content_fingerprint(conn)
    ids = tuple(conn.execute("SELECT id FROM ui_surfaces UNION ALL SELECT id FROM ui_artifacts UNION ALL SELECT id FROM ui_fields ORDER BY id").fetchall())
    synchronize_ui_snapshot(conn, repo_id=repo_id, snapshot=snapshot)
    assert before == logical_content_fingerprint(conn)
    assert ids == tuple(conn.execute("SELECT id FROM ui_surfaces UNION ALL SELECT id FROM ui_artifacts UNION ALL SELECT id FROM ui_fields ORDER BY id").fetchall())
    snapshot.rows["ui_fields"].clear()
    synchronize_ui_snapshot(conn, repo_id=repo_id, snapshot=snapshot)
    assert conn.execute("SELECT COUNT(*) FROM ui_fields").fetchone()[0] == 0


def test_conflict_and_failure_leave_existing_rows_untouched() -> None:
    conn, repo_id = _conn()
    conn.execute("INSERT INTO files(id,repo_id,path) VALUES (1,?,?)", (repo_id, "app/source/gl/glbatch_form.xml"))
    conn.commit()
    snapshot = _snapshot()
    synchronize_ui_snapshot(conn, repo_id=repo_id, snapshot=snapshot)
    baseline = list(conn.execute("SELECT surface_key FROM ui_surfaces"))
    snapshot.add("ui_surfaces", ("actionui:form",), surface_key="actionui:form", surface_kind="nextgen", display_name="bad", source_path="app/source/gl/glbatch_form.xml", source_file_id=1, extractor="test", extractor_version="1", source_hash="hash")
    with pytest.raises(UiSnapshotError, match="duplicate desired"):
        synchronize_ui_snapshot(conn, repo_id=repo_id, snapshot=snapshot)
    assert list(conn.execute("SELECT surface_key FROM ui_surfaces")) == baseline


def _write(repo_root: Path, path: str, content: str) -> None:
    destination = repo_root / path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8")


def test_malformed_xml_retains_existing_evidence_and_persists_parse_issue(tmp_path) -> None:
    repo_root = tmp_path / "repo"
    form_path = "app/source/gl/foo_form.xml"
    _write(repo_root, form_path, "<form><field name='FIRST'/></form>")
    conn, repo_id = _conn()
    conn.execute("INSERT INTO files(repo_id,path) VALUES (?,?)", (repo_id, form_path))
    conn.commit()

    synchronize_ui_snapshot(
        conn, repo_id=repo_id, snapshot=assemble_ui_snapshot(conn, repo_id=repo_id, repo_root=repo_root)
    )
    before = tuple(
        conn.execute("SELECT id,field_name FROM ui_fields ORDER BY id").fetchall()
    )

    _write(repo_root, form_path, "<form><field name='BROKEN'></form>")
    synchronize_ui_snapshot(
        conn,
        repo_id=repo_id,
        snapshot=assemble_ui_snapshot(conn, repo_id=repo_id, repo_root=repo_root),
    )

    assert tuple(conn.execute("SELECT id,field_name FROM ui_fields ORDER BY id")) == before
    assert conn.execute(
        "SELECT COUNT(*) FROM ui_resolution_issues WHERE issue_code='actionui.xml.parse_error'"
    ).fetchone()[0] == 1

    _write(repo_root, form_path, "<form><field name='SECOND'/></form>")
    synchronize_ui_snapshot(
        conn,
        repo_id=repo_id,
        snapshot=assemble_ui_snapshot(conn, repo_id=repo_id, repo_root=repo_root),
    )
    assert conn.execute("SELECT field_name FROM ui_fields").fetchone()[0] == "SECOND"
    assert conn.execute(
        "SELECT COUNT(*) FROM ui_resolution_issues WHERE issue_code='actionui.xml.parse_error'"
    ).fetchone()[0] == 0


def test_new_malformed_xml_creates_only_surface_artifact_and_issue(tmp_path) -> None:
    repo_root = tmp_path / "repo"
    form_path = "app/source/gl/new_form.xml"
    _write(repo_root, form_path, "<form><field name='BROKEN'></form>")
    conn, repo_id = _conn()
    conn.execute("INSERT INTO files(repo_id,path) VALUES (?,?)", (repo_id, form_path))
    conn.commit()

    synchronize_ui_snapshot(
        conn, repo_id=repo_id, snapshot=assemble_ui_snapshot(conn, repo_id=repo_id, repo_root=repo_root)
    )

    assert conn.execute("SELECT COUNT(*) FROM ui_surfaces").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM ui_artifacts").fetchone()[0] == 1
    assert tuple(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("ui_fields", "ui_events", "ui_script_dependencies", "ui_event_calls")) == (0, 0, 0, 0)


def test_include_target_requires_artifact_and_checkout_evidence(tmp_path) -> None:
    repo_root = tmp_path / "repo"
    form_path = "app/source/gl/foo_form.xml"
    fragment_path = "app/source/gl/shared_fragment.xml"
    _write(repo_root, form_path, "<form xmlns:xi='http://www.w3.org/2001/XInclude'><xi:include href='shared_fragment.xml'/><xi:include href='missing.xml'/></form>")
    _write(repo_root, fragment_path, "<fragment/>")
    conn, repo_id = _conn()
    for path in (form_path, fragment_path):
        conn.execute("INSERT INTO files(repo_id,path) VALUES (?,?)", (repo_id, path))
    conn.commit()

    synchronize_ui_snapshot(
        conn, repo_id=repo_id, snapshot=assemble_ui_snapshot(conn, repo_id=repo_id, repo_root=repo_root)
    )

    rows = conn.execute("SELECT resolution_status,target_artifact_id FROM ui_artifact_includes ORDER BY include_key").fetchall()
    assert [(row[0], row[1] is not None) for row in rows] == [("unresolved", False), ("resolved", True)]
    assert conn.execute("SELECT COUNT(*) FROM ui_artifacts WHERE artifact_kind='actionui_include_fragment'").fetchone()[0] == 1


def test_handler_symbol_binds_only_to_the_proven_dependency_file() -> None:
    conn, repo_id = _conn()
    paths = ("app/source/gl/foo_form.xml", "app/resources/js/first.js", "app/resources/js/second.js")
    for path in paths:
        conn.execute("INSERT INTO files(repo_id,path) VALUES (?,?)", (repo_id, path))
    first_id, second_id = (
        int(conn.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()[0])
        for path in paths[1:]
    )
    conn.execute("INSERT INTO symbols(file_id,name,kind,start_line,stable_key) VALUES (?,'handler','function',7,'first-handler')", (first_id,))
    conn.execute("INSERT INTO symbols(file_id,name,kind,start_line,stable_key) VALUES (?,'handler','function',7,'second-handler')", (second_id,))
    conn.commit()
    snapshot = UiSnapshot()
    surface = "actionui:app/source/gl/foo_form.xml"
    snapshot.add("ui_surfaces", (surface,), surface_key=surface, surface_kind="actionui_form", display_name="foo", source_path=paths[0], source_file_id=1, extractor="test", extractor_version="1", source_hash="hash")
    snapshot.add("ui_artifacts", (surface, "form"), surface_key=surface, artifact_key="form", artifact_kind="actionui_form", file_id=1, source_path=paths[0], start_line=1, end_line=1, evidence_text="form", source_hash="hash", payload_json="{}")
    snapshot.add("ui_events", (surface, "form", "event"), surface_key=surface, artifact_key="form", event_key="event", event_type="load", handler_name=None, handler_expression="handler()", source_line=1, evidence_text="handler()")
    snapshot.add("ui_script_dependencies", (surface, "first"), surface_key=surface, artifact_key="form", dependency_key="first", script_path=paths[1], target_file_id=first_id, load_scope="active", resolution_status="resolved", evidence_text="loader", source_line=1)
    snapshot.add("ui_event_calls", (surface, "event", "first", "call"), surface_key=surface, event_key="event", dependency_key="first", call_key="call", handler_name="handler", handler_symbol_name="handler", handler_symbol_line=7, handler_symbol_source_file=paths[1], resolution_status="resolved", resolution_reason="exact", evidence_text="handler()")
    synchronize_ui_snapshot(conn, repo_id=repo_id, snapshot=snapshot)

    assert conn.execute("SELECT handler_symbol_id FROM ui_event_calls").fetchone()[0] == conn.execute("SELECT id FROM symbols WHERE file_id=?", (first_id,)).fetchone()[0]


def test_formeditor_surface_uses_common_static_script_dependency(tmp_path) -> None:
    repo_root = tmp_path / "repo"
    form, editor, header, script = (
        "app/source/gl/foo_form.xml", "app/source/gl/FooEditor.cls",
        "app/source/common/html_header.inc", "app/resources/js/common.js",
    )
    _write(repo_root, form, "<form><events><onload>commonHandler();</onload></events></form>")
    _write(repo_root, editor, "<?php\nclass FooEditor extends FormEditor { function getMetadataKeyName() { return parent::getMetadataKeyName(); } }\n")
    _write(repo_root, header, "<?php function jsCommonIncludes() { echo '<script src=\"../resources/js/common.js\"></script>'; }")
    _write(repo_root, script, "function commonHandler() {}\n")
    conn, repo_id = _conn()
    for path in (form, editor, header, script):
        conn.execute("INSERT INTO files(repo_id,path) VALUES (?,?)", (repo_id, path))
    entity_id = int(conn.execute("INSERT INTO entity_nodes(name) VALUES ('Foo')").lastrowid)
    conn.execute("INSERT INTO entity_occurrences(repo_id,entity_id) VALUES (?,?)", (repo_id, entity_id))
    editor_id = int(conn.execute("SELECT id FROM files WHERE path=?", (editor,)).fetchone()[0])
    script_id = int(conn.execute("SELECT id FROM files WHERE path=?", (script,)).fetchone()[0])
    conn.execute("INSERT INTO entity_mappings(repo_id,entity_id,file_id,mapping_type) VALUES (?,?,?,'editor')", (repo_id, entity_id, editor_id))
    conn.execute("INSERT INTO relationships(repo_id,source_name,target_name,relationship_type,file_path,evidence) VALUES (?,'FooEditor','FormEditor','INHERITS',?,'fixture')", (repo_id, editor))
    conn.execute("INSERT INTO symbols(file_id,name,kind,start_line,stable_key) VALUES (?,'commonHandler','function',1,'common-handler')", (script_id,))
    conn.commit()

    synchronize_ui_snapshot(conn, repo_id=repo_id, snapshot=assemble_ui_snapshot(conn, repo_id=repo_id, repo_root=repo_root))

    assert conn.execute("SELECT resolution_status FROM ui_event_calls").fetchone()[0] == "resolved"
    assert conn.execute("SELECT artifact_kind FROM ui_artifacts JOIN ui_script_dependencies ON ui_script_dependencies.source_artifact_id=ui_artifacts.id").fetchone()[0] == "common_include"


def test_multiline_negative_handler_calls_and_stage_diagnostics_are_persisted(tmp_path) -> None:
    repo_root = tmp_path / "repo"
    form_path = "app/source/gl/foo_form.xml"
    editor_path = "app/source/gl/FooEditor.cls"
    script_path = "app/resources/js/foo.js"
    nextgen_path = "app/source/openapispec/gl/uimeta/objects.general-ledger.foo.s1.uimeta.yaml"
    _write(
        repo_root,
        form_path,
        """<form><events><onload>
    missingHandler();
</onload></events></form>""",
    )
    _write(
        repo_root,
        editor_path,
        """<?php
class FooEditor extends FormEditor {
    function getMetadataKeyName() { return 'foo_form.pxml'; }
    function getMetadataFileName() { return parent::getMetadataFileName(); }
    function getJavaScriptFileNames() {
        if ($enabled) { return array('../resources/js/foo.js', 'bare.js'); }
        return array('../resources/js/foo.js', 'bare.js');
    }
}
""",
    )
    _write(repo_root, script_path, "function valid() {}\nif (\n")
    _write(repo_root, nextgen_path, "object: general-ledger/foo\n")
    conn, repo_id = _conn()
    for path in (form_path, editor_path, script_path, nextgen_path):
        conn.execute("INSERT INTO files(repo_id,path) VALUES (?,?)", (repo_id, path))
    entity_id = int(conn.execute("INSERT INTO entity_nodes(name) VALUES ('Foo')").lastrowid)
    conn.execute("INSERT INTO entity_occurrences(repo_id,entity_id) VALUES (?,?)", (repo_id, entity_id))
    editor_file_id = int(
        conn.execute("SELECT id FROM files WHERE repo_id=? AND path=?", (repo_id, editor_path)).fetchone()[0]
    )
    conn.execute(
        "INSERT INTO entity_mappings(repo_id,entity_id,file_id,mapping_type) VALUES (?,?,?,'editor')",
        (repo_id, entity_id, editor_file_id),
    )
    conn.commit()

    snapshot = assemble_ui_snapshot(conn, repo_id=repo_id, repo_root=repo_root)
    synchronize_ui_snapshot(conn, repo_id=repo_id, snapshot=snapshot)

    event_call = conn.execute(
        "SELECT dependency_id,resolution_status,resolution_reason FROM ui_event_calls"
    ).fetchone()
    assert tuple(event_call) == (None, "unresolved", "linked_script_parse_error")
    issue_codes = {
        row[0] for row in conn.execute("SELECT issue_code FROM ui_resolution_issues")
    }
    assert {
        "actionui.loader.inheritance_missing",
        "actionui.php.unsupported_control_flow",
        "actionui.script.bare_path",
        "actionui.javascript.parse_error",
        "actionui.handler.unresolved",
        "nextgen.entity_mapping.unresolved",
    }.issubset(issue_codes)


def test_bare_uimeta_persists_only_one_source_diagnostic_and_removes_it_when_resolved(
    tmp_path,
) -> None:
    repo_root = tmp_path / "repo"
    bare_uimeta = "app/source/openapispec/inv/uimeta/objects.aisle.s1.uimeta.yaml"
    _write(repo_root, bare_uimeta, "uiLabel: IA.AISLE\nfields: {}\n")
    conn, repo_id = _conn()
    source_file_id = int(
        conn.execute("INSERT INTO files(repo_id,path) VALUES (?,?)", (repo_id, bare_uimeta)).lastrowid
    )
    conn.commit()

    synchronize_ui_snapshot(
        conn,
        repo_id=repo_id,
        snapshot=assemble_ui_snapshot(conn, repo_id=repo_id, repo_root=repo_root),
    )

    diagnostics = conn.execute(
        """SELECT source_file_id,source_path,source_kind,source_pointer,severity,
                  diagnostic_code,message,evidence_text
           FROM ui_source_diagnostics"""
    ).fetchall()
    assert [tuple(row) for row in diagnostics] == [
        (
            source_file_id,
            bare_uimeta,
            "uimeta",
            "lines:1-1",
            "warning",
            "nextgen.family.unresolved",
            "No explicit object key is available; this artifact cannot be assigned to a NextGen family.",
            "objects.aisle.s1.uimeta.yaml",
        )
    ]
    assert conn.execute("SELECT COUNT(*) FROM ui_surfaces").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM ui_artifacts").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM ui_entity_references").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM api_registry_entries").fetchone()[0] == 0

    # The desired-snapshot deletion is deterministic: once source evidence
    # proves a family, the old unattached warning cannot survive.
    _write(repo_root, bare_uimeta, "object: inventory-control/aisle\n")
    synchronize_ui_snapshot(
        conn,
        repo_id=repo_id,
        snapshot=assemble_ui_snapshot(conn, repo_id=repo_id, repo_root=repo_root),
    )
    assert conn.execute("SELECT COUNT(*) FROM ui_source_diagnostics").fetchone()[0] == 0
    assert conn.execute("SELECT surface_key FROM ui_surfaces").fetchone()[0] == (
        "nextgen:inventory-control/aisle"
    )


def test_assemble_glbatch_actionui_and_nextgen_evidence() -> None:
    conn, repo_id = _conn()
    paths = (
        "app/source/gl/glbatch_form.xml",
        "app/source/gl/glbatch_2012_form.xml",
        "app/source/gl/GLBatchEditor.cls",
        "app/resources/js/glbatch.js",
        "app/source/openapispec/gl/uimeta/objects.general-ledger.journal-entry.s1.uimeta.yaml",
        "app/source/openapispec/gl/views/objects.general-ledger.journal-entry.systemfw1.s1.view.yaml",
        "app/source/openapispec/gl/models/objects.general-ledger.journal-entry.s1.schema.yaml",
    )
    for path in paths:
        conn.execute("INSERT INTO files(repo_id,path) VALUES (?,?)", (repo_id, path))
    entity_id = int(conn.execute("INSERT INTO entity_nodes(name) VALUES ('GLBatch')").lastrowid)
    conn.execute("INSERT INTO entity_occurrences(repo_id,entity_id) VALUES (?,?)", (repo_id, entity_id))
    editor_file = int(conn.execute("SELECT id FROM files WHERE path LIKE '%GLBatchEditor.cls'").fetchone()[0])
    schema_file = int(conn.execute("SELECT id FROM files WHERE path LIKE '%schema.yaml'").fetchone()[0])
    conn.execute("INSERT INTO entity_mappings(repo_id,entity_id,file_id,mapping_type) VALUES (?,?,?,'editor')", (repo_id, entity_id, editor_file))
    conn.execute("INSERT INTO entity_mappings(repo_id,entity_id,file_id,mapping_type) VALUES (?,?,?,'openapispec_schema')", (repo_id, entity_id, schema_file))
    conn.execute("INSERT INTO relationships(repo_id,source_name,target_name,relationship_type,file_path,evidence) VALUES (?,?,?,'INHERITS',?,'fixture')", (repo_id, "GLBatchEditor", "FormEditor", "app/source/gl/GLBatchEditor.cls"))
    conn.execute("INSERT INTO openapispec_index(repo_id,file_id,file_path,module,canonical_name,kind,state) VALUES (?,?,?,?,?,'schema','active')", (repo_id, schema_file, paths[-1], "general-ledger", "journal-entry"))
    conn.commit()
    snapshot = assemble_ui_snapshot(conn, repo_id=repo_id, repo_root=ROOT)
    synchronize_ui_snapshot(conn, repo_id=repo_id, snapshot=snapshot)
    keys = {row[0] for row in conn.execute("SELECT surface_key FROM ui_surfaces")}
    assert "actionui:app/source/gl/glbatch_form.xml" in keys
    assert "nextgen:general-ledger/journal-entry" in keys
    assert conn.execute("SELECT COUNT(*) FROM ui_fields").fetchone()[0] > 100
    assert conn.execute("SELECT COUNT(*) FROM ui_entity_references WHERE reference_kind='editor'").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM ui_entity_references WHERE reference_kind='explicit_mapping'").fetchone()[0] == 1
