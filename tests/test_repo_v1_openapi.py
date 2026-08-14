from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from test_repo_v1 import _repo

from catalog import repo_v1
from catalog.refresh_transaction import CatalogPromotionError
from catalog.repo_v1 import RepoV1Error, build_ia_main
from catalog.repo_v1_openapi import (
    OpenAPIValidationError,
    extract_snapshot_openapi,
    validate_openapi_candidate,
)
from catalog.source_snapshot import GitTreeEntry, SourceSnapshot

TARGET = "a" * 40


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _fixture(
    tmp_path: Path, files: dict[str, bytes], entities: tuple[tuple[str, str], ...] = ()
) -> tuple[sqlite3.Connection, SourceSnapshot]:
    snapshot_root = tmp_path / "snapshot"
    snapshot_root.mkdir()
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    schema = Path(__file__).parents[1] / "catalog" / "repo_v1_schema.sql"
    conn.executescript(schema.read_text())
    build_id = int(
        conn.execute(
            "INSERT INTO catalog_builds(build_token,catalog_path,status,source_revisions_json) VALUES(?,?,?,?)",
            (
                "build",
                "/tmp/candidate.db",
                "validated",
                _canonical({"ia-main": TARGET}),
            ),
        ).lastrowid
    )
    repo_id = int(
        conn.execute(
            """INSERT INTO repos(repo_key,name,kind,language,local_root,tracked_branch,target_commit_sha,build_id)
                                  VALUES('ia-main','fixture','test','yaml',?,?,?,?)""",
            (str(tmp_path), "main", TARGET, build_id),
        ).lastrowid
    )
    entries: list[GitTreeEntry] = []
    for index, (path, content) in enumerate(sorted(files.items()), start=1):
        destination = snapshot_root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        blob = hashlib.sha1(content).hexdigest()
        entries.append(GitTreeEntry(path, 0o100644, blob, len(content)))
        conn.execute(
            """INSERT INTO files(repo_id,path,blob_object_id,file_mode,size_bytes,language,source_commit_sha)
                        VALUES(?,?,?,?,?,?,?)""",
            (repo_id, path, blob, 0o100644, len(content), "yaml", TARGET),
        )
    for path, source_key in entities:
        file_id = int(
            conn.execute(
                "SELECT id FROM files WHERE repo_id=? AND path=?", (repo_id, path)
            ).fetchone()[0]
        )
        conn.execute(
            "INSERT OR IGNORE INTO entity_nodes(name) VALUES(?)", (source_key,)
        )
        node_id = int(
            conn.execute(
                "SELECT id FROM entity_nodes WHERE name=?", (source_key,)
            ).fetchone()[0]
        )
        conn.execute(
            """INSERT INTO entity_occurrences(repo_id,entity_id,source_file_id,source_key,source_commit_sha,evidence,extractor)
                        VALUES(?,?,?,?,?,?,?)""",
            (
                repo_id,
                node_id,
                file_id,
                source_key,
                TARGET,
                "{}",
                "repo_v1_entities_v1",
            ),
        )
    conn.commit()
    return conn, SourceSnapshot(
        "ia-main",
        tmp_path,
        TARGET,
        snapshot_root,
        sum(len(v) for v in files.values()),
        len(files),
        tuple(entries),
    )


def test_index_scope_duplicate_keys_and_exact_endpoint_facts(tmp_path: Path) -> None:
    files = {
        "app/source/openapispec/ap/bill.schema.yaml": b"openapi: 3.0.0\nx-mappedTo: APBill\n",
        "app/source/openapispec/ap/paths/bill.api.yaml": b"openapi: 3.0.0\npaths:\n  /bills/{key}:\n    get:\n      operationId: list-bills\n      responses:\n        '200':\n          $ref: '#/components/responses/ok'\n    post:\n      responses: {}\n",
        "app/source/openapispec/ap/paths/uppercase.api.yaml": b"openapi: 3.0.0\npaths:\n  /bills/{key}:\n    GET:\n      operationId: uppercase-get\n      responses: {}\n    connect:\n      operationId: unsupported-connect\n      responses: {}\n",
        "app/source/openapispec/template/ignored.yaml": b"not: indexed\n",
        "app/source/openapispec/bad.yaml": b"a: 1\na: 2\n",
        "app/source/openapispec/not-a-map.yaml": b"- value\n",
        "app/source/openapispec/bad-utf8.yaml": b"openapi: 3.0.0\n\xff\n",
        "app/source/openapispec/ignored.yml": b"a: 1\n",
        "app/source/ap/APBill.ent": b"entity\n",
    }
    conn, snapshot = _fixture(
        tmp_path, files, (("app/source/ap/APBill.ent", "APBill"),)
    )
    try:
        stats = extract_snapshot_openapi(conn, repo_id=1, snapshot=snapshot)
        assert stats.document_count == 3
        assert stats.link_count == 1
        assert stats.endpoint_count == 2
        docs = conn.execute(
            "SELECT path,kind,document_key FROM openapi_documents ORDER BY path"
        ).fetchall()
        assert [(row["path"], row["kind"]) for row in docs] == [
            ("app/source/openapispec/ap/bill.schema.yaml", "schema"),
            ("app/source/openapispec/ap/paths/bill.api.yaml", "paths"),
            ("app/source/openapispec/ap/paths/uppercase.api.yaml", "paths"),
        ]
        endpoints = conn.execute(
            "SELECT path_template,http_method,operation_id,source_pointer FROM rest_endpoints ORDER BY http_method"
        ).fetchall()
        assert [
            (
                row["path_template"],
                row["http_method"],
                row["operation_id"],
                row["source_pointer"],
            )
            for row in endpoints
        ] == [
            ("/bills/{key}", "get", "list-bills", "/paths/~1bills~1{key}/get"),
            ("/bills/{key}", "post", None, "/paths/~1bills~1{key}/post"),
        ]
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM rest_endpoints WHERE operation_id IN (?, ?)",
                ("uppercase-get", "unsupported-connect"),
            ).fetchone()[0]
            == 0
        )
        codes = {row[0] for row in conn.execute("SELECT code FROM openapi_diagnostics")}
        assert "OPENAPI_YAML_DUPLICATE_KEY" in codes
        assert "OPENAPI_YAML_NON_MAPPING" in codes
        assert "OPENAPI_YAML_INVALID_UTF8" in codes
        validate_openapi_candidate(
            conn, repo_id=1, repo_key="ia-main", target_commit_sha=TARGET
        )
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", "OPENAPI_X_MAPPEDTO_BLANK"),
        ("__custom__", "OPENAPI_X_MAPPEDTO_CUSTOM"),
        ("app/source/ap/APBill.ent", "OPENAPI_X_MAPPEDTO_INVALID"),
        ("Missing", "OPENAPI_X_MAPPEDTO_ZERO_MATCHES"),
        ("APBill", "OPENAPI_X_MAPPEDTO_MULTIPLE_MATCHES"),
    ],
)
def test_x_mapped_to_diagnostics_are_exact_and_nonblocking(
    tmp_path: Path, value: str, expected: str
) -> None:
    files = {
        "app/source/openapispec/ap/object.schema.yaml": (
            b"openapi: 3.0.0\n" if value == "" else f"x-mappedTo: {value}\n".encode()
        ),
        "app/source/ap/one/APBill.ent": b"entity\n",
        "app/source/ap/two/APBill.ent": b"entity\n",
    }
    if value == "Missing":
        files.pop("app/source/ap/two/APBill.ent")
    conn, snapshot = _fixture(
        tmp_path,
        files,
        tuple((path, "APBill") for path in files if path.endswith(".ent")),
    )
    try:
        stats = extract_snapshot_openapi(conn, repo_id=1, snapshot=snapshot)
        assert stats.link_count == 0
        assert (
            conn.execute("SELECT COUNT(*) FROM openapi_entity_links").fetchone()[0] == 0
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM openapi_diagnostics WHERE code=?", (expected,)
            ).fetchone()[0]
            == 1
        )
        validate_openapi_candidate(
            conn, repo_id=1, repo_key="ia-main", target_commit_sha=TARGET
        )
    finally:
        conn.close()


def test_non_string_mapping_and_invalid_path_operation_emit_diagnostics(
    tmp_path: Path,
) -> None:
    files = {
        "app/source/openapispec/ap/object.schema.yaml": b"x-mappedTo:\n  nested: true\n",
        "app/source/openapispec/ap/null.schema.yaml": b"x-mappedTo: null\n",
        "app/source/openapispec/ap/paths/bad.api.yaml": b"paths:\n  bills:\n    get: []\n  /bad:\n    get: []\n  /ok:\n    get: {}\n",
    }
    conn, snapshot = _fixture(tmp_path, files)
    try:
        stats = extract_snapshot_openapi(conn, repo_id=1, snapshot=snapshot)
        assert stats.link_count == 0
        assert stats.endpoint_count == 1
        codes = {row[0] for row in conn.execute("SELECT code FROM openapi_diagnostics")}
        assert {
            "OPENAPI_X_MAPPEDTO_INVALID",
            "OPENAPI_PATH_KEY_INVALID",
            "OPENAPI_OPERATION_INVALID",
        } <= codes
        evidence = conn.execute(
            "SELECT evidence FROM openapi_diagnostics WHERE code='OPENAPI_X_MAPPEDTO_INVALID' AND file_id=(SELECT id FROM files WHERE path='app/source/openapispec/ap/object.schema.yaml')"
        ).fetchone()[0]
        assert '"value":{"nested":true}' in evidence
        null_evidence = conn.execute(
            "SELECT evidence FROM openapi_diagnostics WHERE file_id=(SELECT id FROM files WHERE path='app/source/openapispec/ap/null.schema.yaml')"
        ).fetchone()[0]
        assert '"value":null' in null_evidence
    finally:
        conn.close()


def test_endpoint_key_tampering_rejects_candidate(tmp_path: Path) -> None:
    files = {
        "app/source/openapispec/ap/paths/bill.api.yaml": b"paths:\n  /bill:\n    get: {}\n"
    }
    conn, snapshot = _fixture(tmp_path, files)
    try:
        extract_snapshot_openapi(conn, repo_id=1, snapshot=snapshot)
        conn.execute("UPDATE rest_endpoints SET endpoint_key='bad'")
        with pytest.raises(OpenAPIValidationError, match="endpoint key"):
            validate_openapi_candidate(
                conn, repo_id=1, repo_key="ia-main", target_commit_sha=TARGET
            )
    finally:
        conn.close()


def test_operation_id_tampering_rejects_candidate(tmp_path: Path) -> None:
    files = {
        "app/source/openapispec/ap/paths/bill.api.yaml": b"paths:\n  /bill:\n    get:\n      operationId: committed\n"
    }
    conn, snapshot = _fixture(tmp_path, files)
    try:
        extract_snapshot_openapi(conn, repo_id=1, snapshot=snapshot)
        conn.execute("UPDATE rest_endpoints SET operation_id='tampered'")
        with pytest.raises(OpenAPIValidationError, match="operation_id provenance"):
            validate_openapi_candidate(
                conn, repo_id=1, repo_key="ia-main", target_commit_sha=TARGET
            )
    finally:
        conn.close()


def test_dirty_checkout_bytes_do_not_change_snapshot_facts(tmp_path: Path) -> None:
    files = {
        "app/source/openapispec/ap/paths/bill.api.yaml": b"paths:\n  /bill:\n    get:\n      operationId: committed\n",
    }
    conn, snapshot = _fixture(tmp_path, files)
    try:
        extract_snapshot_openapi(conn, repo_id=1, snapshot=snapshot)
        first = conn.execute(
            "SELECT path_template,http_method,operation_id,source_pointer FROM rest_endpoints"
        ).fetchall()
        dirty = snapshot.git_root / "app/source/openapispec/ap/paths/bill.api.yaml"
        dirty.parent.mkdir(parents=True, exist_ok=True)
        dirty.write_text("paths:\n  /bill:\n    delete:\n      operationId: dirty\n")
        extract_snapshot_openapi(conn, repo_id=1, snapshot=snapshot)
        second = conn.execute(
            "SELECT path_template,http_method,operation_id,source_pointer FROM rest_endpoints"
        ).fetchall()
        assert first == second
    finally:
        conn.close()


def test_workflow_schema_refs_link_endpoint_document_from_snapshot_bytes(
    tmp_path: Path,
) -> None:
    schema = b"""workflow-request:
  type: object
  x-mappedTo: APAdjustment
workflow-response:
  type: object
  x-mappedTo: APAdjustment
"""
    operation_files = {
        action: f"""paths:
  /workflows/accounts-payable/adjustment/{action}:
    post:
      requestBody:
        content:
          application/json:
            schema:
              $ref: '../models/workflows.s1.schema.yaml#/workflow-request'
      responses:
        200:
          content:
            application/json:
              schema:
                type: object
                properties:
                  ia::result:
                    $ref: '../models/workflows.s1.schema.yaml#/workflow-response'
""".encode()
        for action in ("reclassify", "reverse", "submit")
    }
    files = {
        "app/source/openapispec/ap/models/workflows.s1.schema.yaml": schema,
        "app/source/ap/apadjustment.ent": b"entity\n",
        **{
            f"app/source/openapispec/ap/paths/{action}.api.yaml": content
            for action, content in operation_files.items()
        },
    }
    conn, snapshot = _fixture(
        tmp_path, files, (("app/source/ap/apadjustment.ent", "apadjustment"),)
    )
    try:
        stats = extract_snapshot_openapi(conn, repo_id=1, snapshot=snapshot)
        assert stats.link_count == 3
        endpoint_links = conn.execute(
            """SELECT d.path,l.entity_occurrence_id,l.evidence
               FROM openapi_entity_links l JOIN openapi_documents d ON d.id=l.document_id
               WHERE d.path LIKE '%/paths/%' ORDER BY d.path"""
        ).fetchall()
        assert len(endpoint_links) == 3
        assert len({row["entity_occurrence_id"] for row in endpoint_links}) == 1
        for row in endpoint_links:
            evidence = json.loads(row["evidence"])
            assert len(evidence["references"]) == 2
            assert all(
                reference["target_path"].endswith("workflows.s1.schema.yaml")
                and reference["target_source_sha256"]
                == hashlib.sha256(schema).hexdigest()
                and reference["matched_ent_path"] == "app/source/ap/apadjustment.ent"
                for reference in evidence["references"]
            )
        validate_openapi_candidate(
            conn, repo_id=1, repo_key="ia-main", target_commit_sha=TARGET
        )
    finally:
        conn.close()


def test_workflow_schema_ref_ambiguous_occurrences_are_all_persisted(
    tmp_path: Path,
) -> None:
    files = {
        "app/source/openapispec/ap/models/workflows.s1.schema.yaml": b"""request:
  x-mappedTo: adjustment
""",
        "app/source/openapispec/ap/paths/workflow.api.yaml": b"""paths:
  /workflows/ap/adjustment/submit:
    post:
      requestBody:
        content:
          application/json:
            schema:
              $ref: '../models/workflows.s1.schema.yaml#/request'
""",
        "app/source/ap/one/adjustment.ent": b"entity\n",
        "app/source/ap/two/adjustment.ent": b"entity\n",
    }
    conn, snapshot = _fixture(
        tmp_path,
        files,
        (
            ("app/source/ap/one/adjustment.ent", "adjustment"),
            ("app/source/ap/two/adjustment.ent", "adjustment"),
        ),
    )
    try:
        extract_snapshot_openapi(conn, repo_id=1, snapshot=snapshot)
        assert (
            conn.execute(
                """SELECT COUNT(*) FROM openapi_entity_links l
                   JOIN openapi_documents d ON d.id=l.document_id
                   WHERE d.path LIKE '%/paths/%'"""
            ).fetchone()[0]
            == 2
        )
        validate_openapi_candidate(
            conn, repo_id=1, repo_key="ia-main", target_commit_sha=TARGET
        )
    finally:
        conn.close()


def test_workflow_schema_ref_link_evidence_tampering_rejected(tmp_path: Path) -> None:
    files = {
        "app/source/openapispec/ap/models/workflows.s1.schema.yaml": b"request:\n  x-mappedTo: adjustment\n",
        "app/source/openapispec/ap/paths/workflow.api.yaml": b"""paths:
  /workflows/ap/adjustment/submit:
    post:
      requestBody:
        content:
          application/json:
            schema:
              $ref: '../models/workflows.s1.schema.yaml#/request'
""",
        "app/source/ap/adjustment.ent": b"entity\n",
    }
    conn, snapshot = _fixture(
        tmp_path, files, (("app/source/ap/adjustment.ent", "adjustment"),)
    )
    try:
        extract_snapshot_openapi(conn, repo_id=1, snapshot=snapshot)
        evidence = json.loads(
            conn.execute("SELECT evidence FROM openapi_entity_links").fetchone()[0]
        )
        evidence["references"][0]["target_pointer"] = "/not-real"
        conn.execute(
            "UPDATE openapi_entity_links SET evidence=?",
            (_canonical(evidence),),
        )
        with pytest.raises(OpenAPIValidationError, match="target"):
            validate_openapi_candidate(
                conn, repo_id=1, repo_key="ia-main", target_commit_sha=TARGET
            )
    finally:
        conn.close()


def test_phase6_failure_preserves_active_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _root, manifest = _repo(tmp_path)
    active = tmp_path / "active.db"
    build_ia_main(manifest_path=manifest, active_db=active, promote=True)
    before = active.read_bytes()

    def fail(*args: object, **kwargs: object) -> object:
        raise RuntimeError("injected Phase 6 failure")

    monkeypatch.setattr(repo_v1, "extract_snapshot_openapi", fail)
    with pytest.raises(RepoV1Error, match="injected Phase 6 failure"):
        build_ia_main(manifest_path=manifest, active_db=active, promote=True)
    assert active.read_bytes() == before
    assert not list(active.parent.glob(f".{active.name}.candidate.*"))


def test_phase6_later_families_without_phase6_rejected(
    tmp_path: Path,
) -> None:
    _root, manifest = _repo(tmp_path)
    active = tmp_path / "active.db"
    build_ia_main(manifest_path=manifest, active_db=active, promote=True)
    build_ia_main(manifest_path=manifest, active_db=active, promote=True)
    previous = active.with_name(active.name + ".previous")
    previous_before = previous.read_bytes()

    conn = sqlite3.connect(active)
    try:
        for table in (
            "openapi_entity_links",
            "rest_endpoints",
            "openapi_diagnostics",
            "openapi_documents",
        ):
            conn.execute(f"DROP TABLE {table}")
        conn.commit()
    finally:
        conn.close()

    active_before = active.read_bytes()
    with pytest.raises(
        CatalogPromotionError,
        match="incomplete or out-of-order Phase 6-8 family",
    ):
        build_ia_main(manifest_path=manifest, active_db=active, promote=True)

    assert active.read_bytes() == active_before
    assert previous.read_bytes() == previous_before
    assert not list(active.parent.glob(f".{active.name}.candidate.*"))
