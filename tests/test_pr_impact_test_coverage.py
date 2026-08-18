from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from catalog.pr_impact_test_coverage import analyze_test_coverage

TARGET = "b" * 40


def _catalog(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(Path("catalog/schema.sql").read_text(encoding="utf-8"))
    conn.execute(
        "INSERT INTO repos(id,repo_key,name,local_root,tracked_branch) VALUES(?,?,?,?,?)",
        (1, "ia-restapi-automation-tests", "REST", "/tmp/rest", "main"),
    )
    conn.execute(
        "INSERT INTO repos(id,repo_key,name,local_root,tracked_branch) VALUES(?,?,?,?,?)",
        (2, "ia-main", "Main", "/tmp/main", "main"),
    )
    conn.execute("INSERT INTO entity_nodes(id,name) VALUES(7,'GLBatch')")
    conn.execute(
        "INSERT INTO files(id,repo_id,path) VALUES(1,1,'features/gl/batch.feature')"
    )
    conn.execute(
        "INSERT INTO rest_endpoints(id,repo_id,method,path,entity_id,source_version) VALUES(1,2,'post','/objects/gl-batches',7,'v1')"
    )
    conn.execute(
        "INSERT INTO test_cases(id,repo_id,file_id,feature_name,scenario_name,case_name,feature_line,scenario_line) VALUES(1,1,1,'batch.feature','creates batch','creates batch',1,3)"
    )
    conn.execute(
        "INSERT INTO test_requests(id,test_case_id,ordinal,step_line,method,normalized_path,request_version,operation_kind,coverage_scope) VALUES(1,1,1,4,'post','/objects/gl-batches','v1','collection','endpoint')"
    )
    conn.execute(
        "INSERT INTO test_endpoint_links(test_request_id,rest_endpoint_id,resolution_kind) VALUES(1,1,'exact_version')"
    )
    conn.execute(
        "INSERT INTO test_entity_links(test_request_id,entity_id,rest_endpoint_id) VALUES(1,7,1)"
    )
    conn.execute(
        "INSERT INTO test_coverage_build_state(repo_id,extractor_version,candidate_build_token,indexed_suite_target_sha,dependency_revisions_json,entity_mapping_sha1,coverage_contract_version,coverage_dependency_fingerprint) VALUES(1,'test','candidate','suite',?,?, '1','fingerprint')",
        (json.dumps({"ia-main": TARGET}), "d" * 40),
    )
    conn.commit()
    conn.close()


def test_exact_dependency_revision_returns_feature_coverage(tmp_path: Path) -> None:
    catalog = tmp_path / "tests.db"
    _catalog(catalog)

    report = analyze_test_coverage(
        "config/workspace_repos.yaml",
        main_target_revision=TARGET,
        entity_names=["GLBatch"],
        catalog_path=catalog,
    )

    assert report["status"] == "ready"
    assert report["scope"]["endpoint_repository"] == "ia-main"
    assert report["scope"]["evaluated_entity_count"] == 1
    assert report["entities"][0]["summary"]["active_covered_endpoint_count"] == 1
    assert (
        report["entities"][0]["coverage"][0]["cases"][0]["feature_path"]
        == "features/gl/batch.feature"
    )


def test_missing_catalog_is_explicitly_deferred() -> None:
    report = analyze_test_coverage(
        "config/workspace_repos.yaml",
        main_target_revision=TARGET,
        entity_names=["GLBatch"],
    )

    assert report["status"] == "deferred"
    assert report["gaps"][0]["gap_code"] == "test_catalog_unavailable"


def test_empty_entity_scope_is_not_ready(tmp_path: Path) -> None:
    catalog = tmp_path / "tests.db"
    _catalog(catalog)

    report = analyze_test_coverage(
        "config/workspace_repos.yaml",
        main_target_revision=TARGET,
        entity_names=[],
        catalog_path=catalog,
    )

    assert report["status"] == "deferred"
    assert report["scope"]["evaluated_entity_count"] == 0
    assert report["gaps"][0]["gap_code"] == "test_coverage_unscoped"


def test_conditional_coverage_is_preserved_as_weak(tmp_path: Path) -> None:
    catalog = tmp_path / "tests.db"
    _catalog(catalog)
    conn = sqlite3.connect(catalog)
    conn.execute("UPDATE test_cases SET eligibility='conditional' WHERE id=1")
    conn.commit()
    conn.close()

    report = analyze_test_coverage(
        "config/workspace_repos.yaml",
        main_target_revision=TARGET,
        entity_names=["GLBatch"],
        catalog_path=catalog,
    )

    endpoint = report["entities"][0]["coverage"][0]
    assert endpoint["coverage"] == "conditional"
    assert endpoint["conditional_case_count"] == 1
    assert endpoint["conditional_case_count"] != endpoint["ci_conditional_case_count"]
    assert report["gaps"][0]["gap_code"] == "test_endpoint_weak_coverage"


def test_coverage_is_scoped_and_paginated(tmp_path: Path) -> None:
    catalog = tmp_path / "tests.db"
    _catalog(catalog)
    conn = sqlite3.connect(catalog)
    conn.executemany(
        "INSERT INTO rest_endpoints(id,repo_id,method,path,entity_id,source_version) VALUES(?,?,?,?,?,?)",
        [
            (endpoint_id, 2, "get", f"/objects/gl-batches/{endpoint_id}", 7, "v1")
            for endpoint_id in range(2, 503)
        ]
        + [(503, 1, "get", "/objects/should-not-appear", 7, "v1")],
    )
    conn.commit()
    conn.close()

    report = analyze_test_coverage(
        "config/workspace_repos.yaml",
        main_target_revision=TARGET,
        entity_names=["GLBatch"],
        catalog_path=catalog,
    )

    endpoints = report["entities"][0]["coverage"]
    assert len(endpoints) == 502
    assert all(item["path"] != "/objects/should-not-appear" for item in endpoints)
