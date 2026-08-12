from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_repo_v1_openapi import _fixture

from catalog.repo_v1_openapi import extract_snapshot_openapi
from catalog.repo_v1_workflows import (
    extract_snapshot_workflows,
    validate_workflow_candidate,
)


def test_workflow_endpoint_fact_and_transition_provenance(tmp_path: Path) -> None:
    conn, snapshot = _fixture(
        tmp_path,
        {
            "app/source/openapispec/ap/paths/bill.api.yaml": b"""paths:
  /workflows/ap/bill/approve:
    post:
      operationId: approve-bill
      x-transition:
        type: stateTransition
        from: [submitted]
        to: [approved]
""",
        },
    )
    extract_snapshot_openapi(conn, repo_id=1, snapshot=snapshot)
    stats = extract_snapshot_workflows(conn, repo_id=1, snapshot=snapshot)
    assert stats.fact_count == 1
    row = conn.execute("SELECT * FROM workflow_facts").fetchone()
    assert row["module"] == "ap"
    assert row["object_name"] == "bill"
    assert row["action"] == "approve"
    assert row["operation_id"] == "approve-bill"
    assert (
        row["transition_json"]
        == '{"from":["submitted"],"to":["approved"],"type":"stateTransition"}'
    )
    assert row["entity_link_status"] == "unresolved"
    evidence = json.loads(row["evidence"])
    assert evidence["source_hash"] == row["source_hash"]
    validate_workflow_candidate(conn, repo_id=1, target_commit_sha="a" * 40)


def test_workflow_selection_and_tampering_rejected(tmp_path: Path) -> None:
    conn, snapshot = _fixture(
        tmp_path,
        {
            "app/source/openapispec/ap/paths/routes.api.yaml": b"""paths:
  /objects/ap/bill/list:
    get: {}
  /workflows/ap/bill/approve/:
    post: {}
  /workflows/ap/bill/approve:
    GET: {}
    connect: {}
    post: {}
""",
        },
    )
    extract_snapshot_openapi(conn, repo_id=1, snapshot=snapshot)
    extract_snapshot_workflows(conn, repo_id=1, snapshot=snapshot)
    assert conn.execute("SELECT COUNT(*) FROM workflow_facts").fetchone()[0] == 1
    conn.execute("UPDATE workflow_facts SET source_hash=?", ("b" * 64,))
    with pytest.raises(RuntimeError):
        validate_workflow_candidate(conn, repo_id=1, target_commit_sha="a" * 40)
    extract_snapshot_workflows(conn, repo_id=1, snapshot=snapshot)
    conn.execute("UPDATE workflow_diagnostics SET diagnostic_key=?", ("d" * 64,))
    with pytest.raises(RuntimeError):
        validate_workflow_candidate(conn, repo_id=1, target_commit_sha="a" * 40)


def test_workflow_endpoint_binding_and_explicit_null_transition_rejected(
    tmp_path: Path,
) -> None:
    conn, snapshot = _fixture(
        tmp_path,
        {
            "app/source/openapispec/ap/paths/routes.api.yaml": b"""paths:
  /workflows/ap/bill/approve:
    post:
      x-transition: null
  /workflows/ap/bill/review:
    post: {}
""",
        },
    )
    extract_snapshot_openapi(conn, repo_id=1, snapshot=snapshot)
    stats = extract_snapshot_workflows(conn, repo_id=1, snapshot=snapshot)
    assert stats.fact_count == 2
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM workflow_diagnostics "
            "WHERE code='workflow.transition.invalid'"
        ).fetchone()[0]
        == 1
    )
    conn.execute(
        "UPDATE workflow_facts SET source_pointer='/paths/tampered/post', "
        "workflow_key=? WHERE id=(SELECT MIN(id) FROM workflow_facts)",
        ("c" * 64,),
    )
    with pytest.raises(RuntimeError):
        validate_workflow_candidate(conn, repo_id=1, target_commit_sha="a" * 40)


def test_workflow_schema_refs_drive_resolved_and_ambiguous_statuses(
    tmp_path: Path,
) -> None:
    files = {
        "app/source/openapispec/ap/models/workflows.s1.schema.yaml": b"""same-request:
  x-mappedTo: adjustment
same-response:
  x-mappedTo: adjustment
different-response:
  x-mappedTo: other
""",
        "app/source/openapispec/ap/paths/resolved.api.yaml": b"""paths:
  /workflows/ap/adjustment/submit:
    post:
      requestBody:
        content:
          application/json:
            schema:
              $ref: '../models/workflows.s1.schema.yaml#/same-request'
      responses:
        200:
          content:
            application/json:
              schema:
                properties:
                  ia::result:
                    $ref: '../models/workflows.s1.schema.yaml#/same-response'
""",
        "app/source/openapispec/ap/paths/ambiguous.api.yaml": b"""paths:
  /workflows/ap/adjustment/reverse:
    post:
      requestBody:
        content:
          application/json:
            schema:
              $ref: '../models/workflows.s1.schema.yaml#/same-request'
      responses:
        200:
          content:
            application/json:
              schema:
                properties:
                  ia::result:
                    $ref: '../models/workflows.s1.schema.yaml#/different-response'
""",
        "app/source/ap/adjustment.ent": b"entity\n",
        "app/source/ap/other.ent": b"entity\n",
    }
    conn, snapshot = _fixture(
        tmp_path,
        files,
        (
            ("app/source/ap/adjustment.ent", "adjustment"),
            ("app/source/ap/other.ent", "other"),
        ),
    )
    try:
        extract_snapshot_openapi(conn, repo_id=1, snapshot=snapshot)
        stats = extract_snapshot_workflows(conn, repo_id=1, snapshot=snapshot)
        assert stats.resolved_entity_link_count == 1
        assert stats.ambiguous_entity_link_count == 1
        rows = conn.execute(
            "SELECT action,entity_link_status,entity_occurrence_id FROM workflow_facts ORDER BY action"
        ).fetchall()
        assert [(row[0], row[1]) for row in rows] == [
            ("reverse", "ambiguous"),
            ("submit", "resolved"),
        ]
        assert rows[1][2] is not None
        assert all(
            "same-document" not in row[0]
            for row in conn.execute("SELECT message FROM workflow_diagnostics")
        )
    finally:
        conn.close()


def test_workflow_schema_ref_failures_and_multiple_operations_stay_unresolved(
    tmp_path: Path,
) -> None:
    files = {
        "app/source/openapispec/ap/models/workflows.s1.schema.yaml": b"request:\n  x-mappedTo: adjustment\n",
        "app/source/openapispec/ap/paths/missing.api.yaml": b"""paths:
  /workflows/ap/adjustment/missing:
    post:
      requestBody:
        content:
          application/json:
            schema:
              $ref: '../models/not-retained.yaml#/request'
""",
        "app/source/openapispec/ap/paths/invalid-pointer.api.yaml": b"""paths:
  /workflows/ap/adjustment/invalid:
    post:
      requestBody:
        content:
          application/json:
            schema:
              $ref: '../models/workflows.s1.schema.yaml#/missing'
""",
        "app/source/openapispec/ap/paths/multi.api.yaml": b"""paths:
  /workflows/ap/adjustment/one:
    post:
      requestBody:
        content:
          application/json:
            schema:
              $ref: '../models/workflows.s1.schema.yaml#/request'
  /workflows/ap/adjustment/two:
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
        stats = extract_snapshot_workflows(conn, repo_id=1, snapshot=snapshot)
        assert stats.fact_count == 4
        assert stats.unresolved_entity_link_count == 4
        assert (
            conn.execute("SELECT COUNT(*) FROM openapi_entity_links").fetchone()[0] == 0
        )
    finally:
        conn.close()
