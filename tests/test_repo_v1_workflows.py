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
