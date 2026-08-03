from __future__ import annotations

import json
import sqlite3

from click.testing import CliRunner

from scripts.query_api_registry import (
    ApiRegistryQueryError,
    cli,
    decode_cursor,
    encode_cursor,
    query_api_registry_file,
    query_api_registry_issues,
    query_api_registry_releases,
    query_api_registry_resource,
)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE repos (id INTEGER PRIMARY KEY, repo_key TEXT UNIQUE);
        CREATE TABLE files (id INTEGER PRIMARY KEY, repo_id INTEGER, path TEXT);
        CREATE TABLE api_registry_entries (
            id INTEGER PRIMARY KEY, repo_id INTEGER, registry_release TEXT, registry_file_id INTEGER,
            json_pointer TEXT, module TEXT, resource_kind TEXT, resource_path TEXT,
            revision TEXT, declared_hash TEXT, api_type TEXT, runtime_owner TEXT,
            ui_metadata_hash TEXT, source_optional INTEGER, payload_json TEXT
        );
        CREATE TABLE api_registry_entry_links (
            id INTEGER PRIMARY KEY, repo_id INTEGER, entry_id INTEGER, source_file_id INTEGER,
            source_pointer TEXT, link_kind TEXT, component_hash TEXT, evidence_json TEXT
        );
        CREATE TABLE api_registry_issues (
            id INTEGER PRIMARY KEY, repo_id INTEGER, entry_id INTEGER, source_file_id INTEGER,
            source_pointer TEXT, issue_key TEXT, severity TEXT, issue_code TEXT, message TEXT,
            details_json TEXT
        );
        INSERT INTO repos VALUES (1, 'ia-main'), (2, 'ia-restapi-automation');
        INSERT INTO files VALUES
            (1, 1, 'app/source/api/registries/RegistryV1.json'),
            (2, 1, 'app/source/api/registries/RegistryBeta.json'),
            (3, 1, 'app/source/openapispec/ap/bill.s1.yaml'),
            (4, 1, 'app/source/openapispec/ap/bill.s2.yaml'),
            (5, 2, 'app/source/api/registries/RegistryV1.json');
        INSERT INTO api_registry_entries VALUES
            (10, 1, 'V1', 1, '/accounts-payable/objects/bill', 'accounts-payable', 'objects', 'bill',
             's1', 'abc', 'rootObject', 'php', 'ui1', 0, '{"type":"rootObject"}'),
            (11, 1, 'V1', 1, '/accounts-payable/objects/bill-line', 'accounts-payable', 'objects', 'bill-line',
             's1', 'def', 'ownedObject', 'php', '0', 0, '{"type":"ownedObject"}'),
            (12, 1, 'Beta', 2, '/accounts-payable/objects/bill', 'accounts-payable', 'objects', 'bill',
             's2', '0', 'rootObject', 'php', '0', 1, '{"type":"rootObject"}'),
            (20, 2, 'V1', 5, '/other/objects/bill', 'other', 'objects', 'bill',
             's1', 'other', 'rootObject', 'php', NULL, 0, '{}');
        INSERT INTO api_registry_entry_links VALUES
            (100, 1, 10, 3, '/components/schemas/Bill', 'openapi_component', 'abc', '{"matched":true}'),
            (101, 1, 10, 4, '/components/schemas/BillV2', 'openapi_component', 'abc2', '{"matched":true}'),
            (102, 1, 11, 3, '/components/schemas/BillLine', 'openapi_component', 'def', '{}');
        INSERT INTO api_registry_issues VALUES
            (200, 1, 10, 1, '/accounts-payable/objects/bill', 'v1-bill-warning', 'warning', 'hash_mismatch', 'Hash differs', '{"expected":"abc"}'),
            (201, 1, NULL, 2, '/accounts-payable/objects/unknown', 'beta-unknown', 'error', 'invalid_entry', 'Bad entry', '{}');
        """
    )
    return conn


def test_cursor_round_trip_and_invalid_cursor() -> None:
    assert decode_cursor(encode_cursor(4)) == 4
    try:
        decode_cursor("broken")
    except ApiRegistryQueryError as exc:
        assert exc.code == "invalid_cursor"
    else:
        raise AssertionError("invalid cursor accepted")


def test_releases_are_repo_scoped_and_source_provenanced() -> None:
    data = query_api_registry_releases(_conn(), repo_key="ia-main")
    assert [release["release"] for release in data["releases"]] == ["Beta", "V1"]
    v1 = data["releases"][1]
    assert v1["registry_provenance"] == {"file_path": "app/source/api/registries/RegistryV1.json"}
    assert v1["entry_count"] == 2
    assert v1["linked_entry_count"] == 2
    assert v1["source_component_count"] == 3
    assert v1["issue_counts"] == {"error": 0, "warning": 1}


def test_resource_preserves_file_and_pointer_provenance_and_paginates() -> None:
    first = query_api_registry_resource(
        _conn(), repo_key="ia-main", release="V1", module="accounts-payable",
        resource_kind="objects", resource_path="bill", limit=1,
    )
    entry = first["entries"][0]
    assert entry["registry_provenance"] == {
        "file_path": "app/source/api/registries/RegistryV1.json",
        "json_pointer": "/accounts-payable/objects/bill",
    }
    assert entry["source_components"] == [
        {
            "kind": "openapi_component", "component_hash": "abc",
            "provenance": {"file_path": "app/source/openapispec/ap/bill.s1.yaml", "json_pointer": "/components/schemas/Bill"},
            "evidence": {"matched": True},
        },
        {
            "kind": "openapi_component", "component_hash": "abc2",
            "provenance": {"file_path": "app/source/openapispec/ap/bill.s2.yaml", "json_pointer": "/components/schemas/BillV2"},
            "evidence": {"matched": True},
        },
    ]
    assert first["page"]["next_cursor"] is None


def test_file_and_issues_keep_exact_provenance_and_release_scope() -> None:
    file_data = query_api_registry_file(
        _conn(), repo_key="ia-main", file_path="app/source/api/registries/RegistryV1.json", limit=1
    )
    assert file_data["entries"][0]["resource_path"] == "bill"
    assert file_data["page"]["next_cursor"] == encode_cursor(1)
    second = query_api_registry_file(
        _conn(), repo_key="ia-main", file_path="app/source/api/registries/RegistryV1.json",
        limit=1, cursor=file_data["page"]["next_cursor"],
    )
    assert second["entries"][0]["resource_path"] == "bill-line"

    issues = query_api_registry_issues(_conn(), repo_key="ia-main", release="V1")
    assert issues["issues"] == [
        {
            "issue_id": 200, "issue_key": "v1-bill-warning", "severity": "warning",
            "code": "hash_mismatch", "message": "Hash differs", "details": {"expected": "abc"},
            "source_provenance": {"file_path": "app/source/api/registries/RegistryV1.json", "json_pointer": "/accounts-payable/objects/bill"},
            "entry": {
                "release": "V1", "module": "accounts-payable", "resource_kind": "objects", "resource_path": "bill",
                "registry_provenance": {"file_path": "app/source/api/registries/RegistryV1.json", "json_pointer": "/accounts-payable/objects/bill"},
            },
        }
    ]


def test_cli_uses_json_v1_envelope_and_error_contract(tmp_path) -> None:
    db = tmp_path / "registry.db"
    target = sqlite3.connect(db)
    _conn().backup(target)
    target.close()
    runner = CliRunner()
    result = runner.invoke(cli, ["releases", "--repo", "ia-main", "--db", str(db), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["contract_version"] == 1
    assert payload["query"]["command"] == "api_registry_releases"
    assert payload["status"] == "ok"

    invalid = runner.invoke(cli, ["issues", "--repo", "missing", "--db", str(db), "--json"])
    assert invalid.exit_code == 0
    assert json.loads(invalid.output)["error"]["code"] == "repository_not_found"


def test_missing_tables_fail_closed() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE repos (id INTEGER PRIMARY KEY, repo_key TEXT UNIQUE)")
    try:
        query_api_registry_releases(conn, repo_key="ia-main")
    except ApiRegistryQueryError as exc:
        assert exc.code == "api_registry_unavailable"
        assert "api_registry_entries" in exc.details["missing_tables"]
    else:
        raise AssertionError("missing Registry tables were accepted")
