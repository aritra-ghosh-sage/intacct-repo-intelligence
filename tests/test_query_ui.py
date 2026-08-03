from __future__ import annotations

import json
import sqlite3

from click.testing import CliRunner

from scripts.query_ui import (
    DETAIL_RECORD_KINDS,
    cli,
    decode_cursor,
    encode_cursor,
    query_ui_impact,
    query_ui_surface_detail,
)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE repos (id INTEGER PRIMARY KEY, repo_key TEXT UNIQUE);
        CREATE TABLE entity_nodes (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE entity_occurrences (id INTEGER PRIMARY KEY, repo_id INTEGER, entity_id INTEGER);
        CREATE TABLE ui_surfaces (
            id INTEGER PRIMARY KEY, repo_id INTEGER, surface_key TEXT, surface_kind TEXT,
            display_name TEXT, source_path TEXT
        );
        CREATE TABLE ui_artifacts (
            id INTEGER PRIMARY KEY, repo_id INTEGER, surface_id INTEGER, artifact_key TEXT,
            artifact_kind TEXT, source_path TEXT, start_line INTEGER, end_line INTEGER,
            evidence_text TEXT, payload_json TEXT
        );
        CREATE TABLE ui_entity_references (
            id INTEGER PRIMARY KEY, repo_id INTEGER, surface_id INTEGER, entity_id INTEGER,
            entity_occurrence_id INTEGER, evidence_artifact_id INTEGER, reference_kind TEXT,
            confidence REAL, evidence_text TEXT, source_line INTEGER
        );
        CREATE TABLE ui_artifact_includes (
            id INTEGER PRIMARY KEY, repo_id INTEGER, source_artifact_id INTEGER,
            target_artifact_id INTEGER, include_key TEXT, raw_include_path TEXT,
            resolved_path TEXT, resolution_status TEXT, source_line INTEGER, evidence_text TEXT
        );
        CREATE TABLE ui_fields (
            id INTEGER PRIMARY KEY, repo_id INTEGER, artifact_id INTEGER, field_key TEXT,
            field_name TEXT, field_path TEXT, label TEXT, field_type TEXT, ordinal INTEGER,
            source_line INTEGER, evidence_text TEXT
        );
        CREATE TABLE ui_events (
            id INTEGER PRIMARY KEY, repo_id INTEGER, artifact_id INTEGER, event_key TEXT,
            event_type TEXT, handler_name TEXT, handler_expression TEXT, source_line INTEGER,
            evidence_text TEXT
        );
        CREATE TABLE ui_script_dependencies (
            id INTEGER PRIMARY KEY, repo_id INTEGER, surface_id INTEGER, dependency_key TEXT,
            script_path TEXT, load_scope TEXT, resolution_status TEXT, evidence_text TEXT,
            source_line INTEGER
        );
        CREATE TABLE symbols (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE ui_event_calls (
            id INTEGER PRIMARY KEY, repo_id INTEGER, event_id INTEGER, dependency_id INTEGER,
            call_key TEXT, handler_name TEXT, handler_symbol_id INTEGER, resolution_status TEXT,
            resolution_reason TEXT, evidence_text TEXT
        );
        CREATE TABLE ui_resolution_issues (
            id INTEGER PRIMARY KEY, repo_id INTEGER, surface_id INTEGER, artifact_id INTEGER,
            event_id INTEGER, dependency_id INTEGER, issue_key TEXT, severity TEXT,
            issue_code TEXT, message TEXT, evidence_text TEXT
        );
        INSERT INTO repos VALUES (1, 'ia-main');
        INSERT INTO entity_nodes VALUES (10, 'GLBatch');
        INSERT INTO entity_occurrences VALUES (20, 1, 10);
        INSERT INTO ui_surfaces VALUES
            (30, 1, 'actionui:app/source/gl/glbatch_form.xml', 'actionui_form', 'GL Batch', 'app/source/gl/glbatch_form.xml'),
            (31, 1, 'nextgen:general-ledger/journal-entry', 'nextgen', 'Journal Entry', 'app/source/openapispec/gl/journal-entry.uimeta.yaml');
        INSERT INTO ui_artifacts VALUES
            (40, 1, 30, 'form', 'actionui_form', 'app/source/gl/glbatch_form.xml', 1, 1, '<form>', '{}'),
            (41, 1, 31, 'uimeta', 'uimeta', 'app/source/openapispec/gl/journal-entry.uimeta.yaml', 1, 1, 'object', '{}');
        INSERT INTO ui_entity_references VALUES
            (50, 1, 30, 10, 20, 40, 'direct', 1.0, 'GLBatch form', 1),
            (51, 1, 31, 10, 20, 41, 'editor', 0.9, 'GLBatchEditor', 8),
            (52, 1, 31, 10, 20, 41, 'unrelated_role', 1.0, 'must not leak', 9);
        INSERT INTO ui_fields VALUES
            (60, 1, 40, 'batchno', 'BATCHNO', 'GLBATCH.BATCHNO', 'Batch no', 'text', 1, 11, '<field>'),
            (61, 1, 40, 'state', 'STATE', 'GLBATCH.STATE', 'State', 'text', 2, 12, '<field>');
        INSERT INTO ui_events VALUES
            (70, 1, 40, 'load', 'load', 'onLoadFunctionCalls', 'onLoadFunctionCalls()', 13, '<load>');
        INSERT INTO ui_script_dependencies VALUES
            (80, 1, 30, 'glbatch', 'app/resources/js/glbatch.js', 'active', 'resolved', 'loader', 4);
        INSERT INTO symbols VALUES (90, 'onLoadFunctionCalls');
        INSERT INTO ui_event_calls VALUES
            (100, 1, 70, 80, 'load:1', 'onLoadFunctionCalls', 90, 'resolved', 'exact active match', 'call');
        INSERT INTO ui_resolution_issues VALUES
            (110, 1, 30, 40, NULL, NULL, 'dynamic-loader', 'warning', 'loader.dynamic', 'Dynamic loader', 'x');
        """
    )
    return conn


def test_cursor_round_trip_and_validation() -> None:
    assert decode_cursor(encode_cursor(27)) == 27
    try:
        decode_cursor("not a cursor")
    except ValueError as exc:
        assert exc.code == "invalid_cursor"
    else:
        raise AssertionError("invalid cursor was accepted")


def test_impact_scopes_to_supported_roles_and_preserves_family() -> None:
    data = query_ui_impact(_conn(), entity_name="GLBatch", repo_key="ia-main", limit=25)
    assert [surface["surface_key"] for surface in data["surfaces"]] == [
        "actionui:app/source/gl/glbatch_form.xml",
        "nextgen:general-ledger/journal-entry",
    ]
    assert [surface["surface_family"] for surface in data["surfaces"]] == ["actionui", "nextgen"]
    assert data["surfaces"][1]["references"][0]["kind"] == "editor"
    assert data["summary"] == {
        "surface_count": 2,
        "direct_surface_count": 1,
        "related_surface_count": 1,
    }


def test_impact_paginates_deterministically() -> None:
    first = query_ui_impact(_conn(), entity_name="GLBatch", repo_key="ia-main", limit=1)
    assert first["page"]["next_cursor"] == encode_cursor(1)
    second = query_ui_impact(
        _conn(), entity_name="GLBatch", repo_key="ia-main", limit=1,
        cursor=first["page"]["next_cursor"],
    )
    assert second["surfaces"][0]["surface_family"] == "nextgen"
    assert second["page"]["next_cursor"] is None


def test_detail_supports_every_contract_record_kind() -> None:
    conn = _conn()
    key = "actionui:app/source/gl/glbatch_form.xml"
    for record_kind in DETAIL_RECORD_KINDS:
        data = query_ui_surface_detail(
            conn, surface_key=key, repo_key="ia-main", record_kind=record_kind
        )
        assert data["record_kind"] == record_kind
        assert data["surface"]["surface_family"] == "actionui"
    events = query_ui_surface_detail(
        conn, surface_key=key, repo_key="ia-main", record_kind="events"
    )
    assert events["records"][0]["calls"][0]["handler_symbol_name"] == "onLoadFunctionCalls"


def test_event_detail_keeps_dependencyless_negative_calls() -> None:
    conn = _conn()
    conn.execute(
        """INSERT INTO ui_event_calls VALUES
           (101, 1, 70, NULL, 'load:missing', 'missing', NULL, 'unresolved',
            'catalog_symbol_not_found_for_dependency', 'call')"""
    )
    events = query_ui_surface_detail(
        conn, surface_key="actionui:app/source/gl/glbatch_form.xml", repo_key="ia-main", record_kind="events"
    )
    calls = events["records"][0]["calls"]
    missing = next(call for call in calls if call["handler_name"] == "missing")
    assert missing["dependency_key"] is None
    assert missing["script_path"] is None


def test_cli_emits_stable_envelope_and_error(tmp_path) -> None:
    db = tmp_path / "ui.db"
    target = sqlite3.connect(db)
    source = _conn()
    source.backup(target)
    target.close()
    runner = CliRunner()
    result = runner.invoke(cli, ["impact", "GLBatch", "--repo", "ia-main", "--db", str(db), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["query"]["command"] == "ui_impact"
    invalid = runner.invoke(cli, ["detail", "missing", "events", "--repo", "ia-main", "--db", str(db), "--json"])
    assert invalid.exit_code == 0
    assert json.loads(invalid.output)["error"]["code"] == "ui_surface_not_found"
    bad_kind = runner.invoke(cli, ["detail", "missing", "bad", "--repo", "ia-main", "--db", str(db), "--json"])
    assert bad_kind.exit_code == 0
    assert json.loads(bad_kind.output)["error"]["code"] == "invalid_record_kind"


def test_cli_impact_renders_all_reference_evidence(tmp_path) -> None:
    db = tmp_path / "ui.db"
    target = sqlite3.connect(db)
    source = _conn()
    source.backup(target)
    target.close()
    result = CliRunner().invoke(cli, ["impact", "GLBatch", "--repo", "ia-main", "--db", str(db)])

    assert result.exit_code == 0, result.output
    assert "(direct:direct)" in result.output
    assert "(related:editor)" in result.output
