from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from catalog.pr_impact_step1 import Step1Error, analyze_fixture, blocked_report
from scripts.validate_pr_impact_step1 import validate


def git(cwd: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(cwd), *args], text=True).strip()


def make_repo(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    (repo / "a.php").write_text("<?php\nfunction old() {}\n", encoding="utf-8")
    git(repo, "add", "."); git(repo, "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "a.php").write_text("<?php\nfunction old() {}\nfunction newThing() {}\n", encoding="utf-8")
    git(repo, "commit", "-qam", "target")
    return repo, base, git(repo, "rev-parse", "HEAD")


def make_entity_repo(tmp_path: Path, *, change_entity: bool = True, change_openapi: bool = False) -> tuple[Path, str, str]:
    repo = tmp_path / "entity-repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    (repo / "apbill.ent").write_text("base\n", encoding="utf-8")
    (repo / "openapi.yaml").write_text("openapi\n", encoding="utf-8")
    git(repo, "add", "."); git(repo, "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD")
    if change_entity:
        (repo / "apbill.ent").write_text("target\n", encoding="utf-8")
    if change_openapi:
        (repo / "openapi.yaml").write_text("target\n", encoding="utf-8")
    git(repo, "commit", "-qam", "target")
    return repo, base, git(repo, "rev-parse", "HEAD")


def make_fixture(tmp_path: Path, base: str, target: str, changed: list[dict] | None = None) -> Path:
    fixture = tmp_path / "fixture.yaml"
    fixture.write_text(yaml.safe_dump({"schema_version": "0.1", "analysis_kind": "pr_impact_step_0", "pull_request": {
        "repository": "intacct/ia-app", "number": 1, "base_revision": base, "target_revision": target,
    }, "changed_files": changed or [{"path": "a.php", "status": "modified"}]}), encoding="utf-8")
    return fixture


def make_manifest(tmp_path: Path, repo: Path) -> Path:
    manifest = tmp_path / "workspace.yaml"
    manifest.write_text(yaml.safe_dump({
        "version": 1,
        "repositories": [{
            "repo_key": "ia-main",
            "local_root": str(repo),
            "tracked_branch": "main",
            "profile": "intacct_app",
            "builders": [],
        }],
    }), encoding="utf-8")
    return manifest


def make_db(tmp_path: Path, target: str, *, populate: bool = True) -> Path:
    db = tmp_path / "catalog.db"
    conn = sqlite3.connect(db)
    conn.executescript(Path("catalog/repo_v1_schema.sql").read_text())
    conn.execute("INSERT INTO catalog_builds(build_token,catalog_path,status,source_revisions_json) VALUES(?,?,?,?)", ("b", str(db), "active", json.dumps({"ia-main": target})))
    conn.execute("INSERT INTO repos(repo_key,local_root,tracked_branch,target_commit_sha,build_id) VALUES(?,?,?,?,?)", ("ia-main", str(tmp_path / "repo"), "main", target, 1))
    if populate:
        conn.execute("INSERT INTO files(repo_id,path,blob_object_id,file_mode,size_bytes,language,source_commit_sha) VALUES(1,'a.php','x',100644,1,'php',?)", (target,))
        conn.execute("INSERT INTO symbols(repo_id,file_id,name,kind,start_line,end_line,language,stable_key) VALUES(1,1,'newThing','function',2,2,'php','new')")
        conn.execute("INSERT INTO relationships(repo_id,source_symbol_id,source_name,target_symbol_id,target_name,relationship_type,file_id,file_path,language,confidence,evidence,resolution_class,resolution_reason,extractor) VALUES(1,1,'newThing',NULL,'old','calls',1,'a.php','php',0.5,'line','project_unresolved','fixture','test')")
    conn.commit(); conn.close()
    return db


def add_entity_openapi_link(db: Path, target: str) -> None:
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO files(repo_id,path,blob_object_id,file_mode,size_bytes,language,source_commit_sha) VALUES(1,'apbill.ent','entity',100644,1,'ent',?)", (target,))
    entity_file_id = conn.execute("SELECT id FROM files WHERE path='apbill.ent'").fetchone()[0]
    conn.execute("INSERT INTO files(repo_id,path,blob_object_id,file_mode,size_bytes,language,source_commit_sha) VALUES(1,'openapi.yaml','openapi',100644,1,'yaml',?)", (target,))
    openapi_file_id = conn.execute("SELECT id FROM files WHERE path='openapi.yaml'").fetchone()[0]
    entity_id = conn.execute("INSERT INTO entity_nodes(name) VALUES('apbill')").lastrowid
    occurrence_id = conn.execute("INSERT INTO entity_occurrences(repo_id,entity_id,source_file_id,source_key,source_commit_sha,evidence,extractor) VALUES(?,?,?,?,?,?,?)", (1, entity_id, entity_file_id, "apbill", target, "entity", "test")).lastrowid
    document_id = conn.execute("INSERT INTO openapi_documents(repo_id,file_id,path,kind,document_key,source_commit_sha,evidence,extractor) VALUES(?,?,?,?,?,?,?,?)", (1, openapi_file_id, "openapi.yaml", "schema", "doc", target, "document", "test")).lastrowid
    conn.execute("INSERT INTO openapi_entity_links(repo_id,document_id,entity_occurrence_id,mapped_value,match_key,link_key,source_commit_sha,evidence,extractor) VALUES(?,?,?,?,?,?,?,?,?)", (1, document_id, occurrence_id, "apbill", "apbill", "link", target, "link", "test"))
    conn.commit(); conn.close()


def test_valid_diff_traces_exact_rows_and_is_read_only(tmp_path: Path) -> None:
    repo, base, target = make_repo(tmp_path)
    fixture = make_fixture(tmp_path, base, target)
    db = make_db(tmp_path, target)
    before = db.read_bytes()
    report = analyze_fixture(fixture, make_manifest(tmp_path, repo), db, "ia-main")
    assert report["status"] == "partial"
    assert report["input"]["repo_key"] == "ia-main"
    assert report["input"]["repo_root"] == str(repo.resolve())
    assert report["changed_files"] == [{"path": "a.php", "status": "modified", "old_path": None}]
    assert report["direct_traces"][1]["facts"][0]["source_path"] == "a.php"
    assert report["direct_traces"][2]["facts"]
    assert next(trace for trace in report["direct_traces"] if trace["surface"] == "outgoing_relationships")["status"] == "unresolved"
    assert next(trace for trace in report["direct_traces"] if trace["surface"] == "openapi_entity_links")["status"] == "empty"
    assert db.read_bytes() == before
    assert report == analyze_fixture(fixture, make_manifest(tmp_path, repo), db, "ia-main")


def test_workflow_and_permissions_trace_existing_repo_v1_facts(tmp_path: Path) -> None:
    repo, base, target = make_repo(tmp_path)
    fixture = make_fixture(tmp_path, base, target)
    db = make_db(tmp_path, target)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO openapi_documents(repo_id,file_id,path,kind,document_key,source_commit_sha,evidence,extractor) VALUES(?,?,?,?,?,?,?,?)",
        (1, 1, "a.php", "paths", "doc", target, "doc", "test"),
    )
    document_id = conn.execute("SELECT id FROM openapi_documents").fetchone()[0]
    conn.execute(
        "INSERT INTO rest_endpoints(repo_id,document_id,endpoint_key,path_template,http_method,operation_id,source_pointer,source_commit_sha,evidence,extractor) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (1, document_id, "endpoint", "/a", "get", "getA", "/paths/~1a/get", target, "endpoint", "test"),
    )
    endpoint_id = conn.execute("SELECT id FROM rest_endpoints").fetchone()[0]
    entity_id = conn.execute("INSERT INTO entity_nodes(name) VALUES('a')").lastrowid
    occurrence_id = conn.execute(
        "INSERT INTO entity_occurrences(repo_id,entity_id,source_file_id,source_key,source_commit_sha,evidence,extractor) VALUES(?,?,?,?,?,?,?)",
        (1, entity_id, 1, "a", target, "entity", "test"),
    ).lastrowid
    conn.execute(
        "INSERT INTO workflow_facts(repo_id,workflow_key,endpoint_id,source_file_id,source_path,source_commit_sha,source_hash,source_pointer,start_line,end_line,module,object_name,action,http_method,path_template,operation_id,transition_json,entity_occurrence_id,entity_link_status,evidence,extractor,extractor_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (1, "workflow", endpoint_id, 1, "a.php", target, "hash", "/paths/~1a/get", 1, 1, "test", "A", "read", "get", "/a", "getA", None, occurrence_id, "resolved", "workflow", "test", "1"),
    )
    conn.execute(
        "INSERT INTO security_operations(repo_id,fact_key,op_key,source_file_id,source_path,source_commit_sha,source_hash,source_pointer,start_line,end_line,evidence,extractor,extractor_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (1, "security", "op.a", 1, "a.php", target, "hash", "/kElements/0", 1, 1, "security", "test", "1"),
    )
    conn.commit()
    before = db.read_bytes()
    conn.close()

    report = analyze_fixture(fixture, make_manifest(tmp_path, repo), db, "ia-main")
    assert next(trace for trace in report["direct_traces"] if trace["surface"] == "workflows")["status"] == "available"
    assert next(trace for trace in report["direct_traces"] if trace["surface"] == "permissions")["status"] == "available"
    assert db.read_bytes() == before


@pytest.mark.parametrize("database_status", ["not_in_scope_for_this_change", "confirmed", "assessed"])
def test_step0_database_assertion_does_not_make_report_complete(tmp_path: Path, database_status: str) -> None:
    repo, base, target = make_repo(tmp_path)
    fixture = make_fixture(tmp_path, base, target)
    document = yaml.safe_load(fixture.read_text(encoding="utf-8"))
    document["affected_surfaces"] = {"database": {"status": database_status}}
    fixture.write_text(yaml.safe_dump(document), encoding="utf-8")
    report = analyze_fixture(fixture, make_manifest(tmp_path, repo), make_db(tmp_path, target), "ia-main")
    database = next(trace for trace in report["direct_traces"] if trace["surface"] == "database_consumers")
    assert database["status"] == "empty"
    assert report["status"] == "partial"
    assert database["facts"] == []


def test_report_validator_rejects_fixture_only_database_evidence() -> None:
    report = {
        "schema_version": "0.3", "analysis_kind": "pr_impact_step_1", "status": "partial",
        "input": {"manifest": "m", "repo_key": "ia-main", "repo_root": "r", "base_revision": "b", "target_revision": "t"},
        "preflight": {}, "changed_files": [],
        "direct_traces": [{"surface": "database_consumers", "status": "available", "facts": [{"fact_key": "step0:database:0", "extractor": "pr_impact_step0_fixture"}]}],
        "pr_metadata": {"status": "not_provided"}, "onboarding_feasibility": [], "impact_ranking": [],
        "gaps": [], "warnings": [], "provenance": {},
    }
    assert "available database_consumers requires direct catalog facts" in validate(report)


def test_metadata_artifact_revision_and_path_parity(tmp_path: Path) -> None:
    repo, base, target = make_repo(tmp_path)
    fixture = make_fixture(tmp_path, base, target)
    db = make_db(tmp_path, target)
    metadata = tmp_path / "metadata.json"
    metadata.write_text(json.dumps({
        "schema_version": "0.1",
        "analysis_kind": "pr_impact_metadata",
        "repo_key": "ia-main",
        "repository": "intacct/ia-app",
        "pull_request": {
            "number": 1,
            "url": "https://github.com/intacct/ia-app/pull/1",
            "base_revision": base,
            "target_revision": target,
        },
        "changed_files": [{"filename": "a.php", "status": "modified"}],
        "provenance": {"provider": "test"},
    }), encoding="utf-8")
    report = analyze_fixture(fixture, make_manifest(tmp_path, repo), db, "ia-main", metadata)
    assert report["pr_metadata"]["status"] == "available"
    assert report["pr_metadata"]["target_revision"] == target

    metadata.write_text(metadata.read_text(encoding="utf-8").replace(base, "0" * 40), encoding="utf-8")
    with pytest.raises(Step1Error, match="metadata revisions"):
        analyze_fixture(fixture, make_manifest(tmp_path, repo), db, "ia-main", metadata)


@pytest.mark.parametrize("changed_files", [None, [], [{}], [{"filename": "a.php"}], [{"status": "modified"}], ["a.php"]])
def test_metadata_changed_files_are_required_and_exact(tmp_path: Path, changed_files: object) -> None:
    repo, base, target = make_repo(tmp_path)
    fixture = make_fixture(tmp_path, base, target)
    db = make_db(tmp_path, target)
    metadata = tmp_path / "metadata.json"
    payload = {
        "schema_version": "0.1", "analysis_kind": "pr_impact_metadata", "repo_key": "ia-main", "repository": "intacct/ia-app",
        "pull_request": {"number": 1, "url": "https://github.com/intacct/ia-app/pull/1", "base_revision": base, "target_revision": target},
        "changed_files": changed_files, "provenance": {"provider": "test"},
    }
    metadata.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(Step1Error, match="metadata changed") as error:
        analyze_fixture(fixture, make_manifest(tmp_path, repo), db, "ia-main", metadata)
    assert error.value.code == "metadata_changed_path_mismatch"


def test_manifest_tilde_resolution(tmp_path: Path, monkeypatch) -> None:
    repo, base, target = make_repo(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    manifest = tmp_path / "workspace.yaml"
    manifest.write_text(yaml.safe_dump({
        "version": 1,
        "repositories": [{"repo_key": "ia-main", "local_root": "~/repo", "tracked_branch": "main", "builders": []}],
    }))
    report = analyze_fixture(make_fixture(tmp_path, base, target), manifest, make_db(tmp_path, target), "ia-main")
    assert report["input"]["repo_root"] == str(repo.resolve())


def test_catalog_source_revision_mismatch_blocks(tmp_path: Path) -> None:
    repo, base, target = make_repo(tmp_path)
    fixture = make_fixture(tmp_path, base, target)
    db = make_db(tmp_path, target)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE catalog_builds SET source_revisions_json=?", (json.dumps({"ia-main": base}),))
    conn.commit(); conn.close()
    try:
        analyze_fixture(fixture, make_manifest(tmp_path, repo), db, "ia-main")
    except Step1Error as exc:
        assert exc.code == "catalog_provenance_mismatch"
    else:
        raise AssertionError("source revision mismatch was accepted")


def test_changed_entity_returns_reverse_openapi_link(tmp_path: Path) -> None:
    repo, base, target = make_entity_repo(tmp_path)
    fixture = make_fixture(tmp_path, base, target, [{"path": "apbill.ent", "status": "modified"}])
    db = make_db(tmp_path, target, populate=False)
    add_entity_openapi_link(db, target)
    report = analyze_fixture(fixture, make_manifest(tmp_path, repo), db, "ia-main")
    links = next(trace for trace in report["direct_traces"] if trace["surface"] == "openapi_entity_links")
    assert links["status"] == "available"
    assert links["facts"][0]["source_path"] == "openapi.yaml"


def test_changed_openapi_file_keeps_direct_openapi_link_lookup(tmp_path: Path) -> None:
    repo, base, target = make_entity_repo(tmp_path, change_entity=False, change_openapi=True)
    fixture = make_fixture(tmp_path, base, target, [{"path": "openapi.yaml", "status": "modified"}])
    db = make_db(tmp_path, target, populate=False)
    add_entity_openapi_link(db, target)
    report = analyze_fixture(fixture, make_manifest(tmp_path, repo), db, "ia-main")
    links = next(trace for trace in report["direct_traces"] if trace["surface"] == "openapi_entity_links")
    assert links["status"] == "available"


def test_entity_and_openapi_changes_do_not_duplicate_link(tmp_path: Path) -> None:
    repo, base, target = make_entity_repo(tmp_path, change_entity=True, change_openapi=True)
    fixture = make_fixture(tmp_path, base, target, [
        {"path": "apbill.ent", "status": "modified"},
        {"path": "openapi.yaml", "status": "modified"},
    ])
    db = make_db(tmp_path, target, populate=False)
    add_entity_openapi_link(db, target)
    report = analyze_fixture(fixture, make_manifest(tmp_path, repo), db, "ia-main")
    links = next(trace for trace in report["direct_traces"] if trace["surface"] == "openapi_entity_links")
    assert len(links["facts"]) == 1


def test_absent_onboarding_repositories_are_deferred(tmp_path: Path) -> None:
    repo, base, target = make_repo(tmp_path)
    manifest = make_manifest(tmp_path, repo)
    report = analyze_fixture(make_fixture(tmp_path, base, target), manifest, make_db(tmp_path, target), "ia-main")
    assert report["onboarding_feasibility"] == [
        {"repository": "ia-restapi-automation-tests", "status": "deferred", "reason": "repository is absent from the workspace manifest"},
        {"repository": "ia-gwdata-gl", "status": "deferred", "reason": "repository is absent from the workspace manifest"},
    ]


def test_empty_diff_is_blocked(tmp_path: Path) -> None:
    repo = tmp_path / "repo"; repo.mkdir(); git(repo, "init", "-q")
    (repo / "a").write_text("x"); git(repo, "add", "."); git(repo, "config", "user.email", "x@y"); git(repo, "config", "user.name", "x"); git(repo, "commit", "-qm", "one")
    sha = git(repo, "rev-parse", "HEAD")
    fixture = make_fixture(tmp_path, sha, sha, [])
    try: analyze_fixture(fixture, make_manifest(tmp_path, repo), tmp_path / "missing.db", "ia-main")
    except Step1Error as exc: assert exc.code == "empty_diff"
    else: raise AssertionError("empty diff was accepted")


def test_revision_and_path_contracts_block(tmp_path: Path) -> None:
    repo, base, target = make_repo(tmp_path)
    db = make_db(tmp_path, target)
    bad = make_fixture(tmp_path, base, target, [{"path": "other.php", "status": "modified"}])
    try: analyze_fixture(bad, make_manifest(tmp_path, repo), db, "ia-main")
    except Step1Error as exc: assert exc.code == "changed_path_mismatch"
    else: raise AssertionError("path mismatch was accepted")
    malformed = make_fixture(tmp_path, "deadbeef", target)
    try: analyze_fixture(malformed, make_manifest(tmp_path, repo), db, "ia-main")
    except Step1Error as exc: assert exc.code == "malformed_git_revision"
    else: raise AssertionError("malformed revision was accepted")


def test_missing_or_empty_declared_paths_block(tmp_path: Path) -> None:
    repo, base, target = make_repo(tmp_path)
    db = make_db(tmp_path, target)
    for declared in (None, []):
        fixture = make_fixture(tmp_path, base, target)
        document = yaml.safe_load(fixture.read_text(encoding="utf-8"))
        if declared is None:
            document.pop("changed_files", None)
        else:
            document["changed_files"] = declared
        fixture.write_text(yaml.safe_dump(document), encoding="utf-8")
        try:
            analyze_fixture(fixture, make_manifest(tmp_path, repo), db, "ia-main")
        except Step1Error as exc:
            assert exc.code == "changed_path_mismatch"
        else:
            raise AssertionError("missing or empty changed_files was accepted")


def test_ambiguous_relationship_status(tmp_path: Path) -> None:
    repo, base, target = make_repo(tmp_path)
    fixture = make_fixture(tmp_path, base, target)
    db = make_db(tmp_path, target)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE relationships SET resolution_reason='ambiguous_project_symbol'")
    conn.commit(); conn.close()
    report = analyze_fixture(fixture, make_manifest(tmp_path, repo), db, "ia-main")
    trace = next(trace for trace in report["direct_traces"] if trace["surface"] == "outgoing_relationships")
    assert trace["status"] == "ambiguous"
    assert trace["warning"]


def test_stale_relationship_status(tmp_path: Path) -> None:
    repo, base, target = make_repo(tmp_path)
    fixture = make_fixture(tmp_path, base, target)
    db = make_db(tmp_path, target)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE files SET source_commit_sha=? WHERE path='a.php'", (base,))
    conn.commit(); conn.close()
    report = analyze_fixture(fixture, make_manifest(tmp_path, repo), db, "ia-main")
    trace = next(trace for trace in report["direct_traces"] if trace["surface"] == "outgoing_relationships")
    assert trace["status"] == "stale"
    assert trace["warning"]


def test_schema_index_mismatch_blocks(tmp_path: Path) -> None:
    repo, base, target = make_repo(tmp_path)
    fixture = make_fixture(tmp_path, base, target)
    db = make_db(tmp_path, target)
    conn = sqlite3.connect(db)
    conn.execute("DROP INDEX idx_repo_v1_files_repo_path")
    conn.commit(); conn.close()
    try:
        analyze_fixture(fixture, make_manifest(tmp_path, repo), db, "ia-main")
    except Step1Error as exc:
        assert exc.code == "catalog_schema_mismatch"
    else:
        raise AssertionError("schema index mismatch was accepted")


def test_added_check_constraint_blocks(tmp_path: Path) -> None:
    repo, base, target = make_repo(tmp_path)
    fixture = make_fixture(tmp_path, base, target)
    db = make_db(tmp_path, target)
    conn = sqlite3.connect(db)
    table_sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='files'").fetchone()[0]
    altered_sql = table_sql.replace("size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0)", "size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0) CHECK (size_bytes <> 999)")
    conn.execute("PRAGMA writable_schema=ON")
    conn.execute("UPDATE sqlite_master SET sql=? WHERE type='table' AND name='files'", (altered_sql,))
    conn.execute("PRAGMA schema_version = 2")
    conn.commit(); conn.close()
    try:
        analyze_fixture(fixture, make_manifest(tmp_path, repo), db, "ia-main")
    except Step1Error as exc:
        assert exc.code == "catalog_schema_mismatch"
    else:
        raise AssertionError("added CHECK constraint was accepted")


def test_active_build_absence_blocks(tmp_path: Path) -> None:
    repo, base, target = make_repo(tmp_path)
    fixture = make_fixture(tmp_path, base, target)
    db = make_db(tmp_path, target)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE catalog_builds SET status='validated'")
    conn.commit(); conn.close()
    try:
        analyze_fixture(fixture, make_manifest(tmp_path, repo), db, "ia-main")
    except Step1Error as exc:
        assert exc.code == "active_build_missing"
    else:
        raise AssertionError("missing active build was accepted")


def test_foreign_key_failure_blocks(tmp_path: Path) -> None:
    repo, base, target = make_repo(tmp_path)
    fixture = make_fixture(tmp_path, base, target)
    db = make_db(tmp_path, target)
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("INSERT INTO repos(repo_key,local_root,tracked_branch,target_commit_sha,build_id) VALUES(?,?,?,?,?)", ("orphan", str(tmp_path), "main", target, 999))
    conn.commit(); conn.close()
    try:
        analyze_fixture(fixture, make_manifest(tmp_path, repo), db, "ia-main")
    except Step1Error as exc:
        assert exc.code == "catalog_foreign_key_failure"
    else:
        raise AssertionError("foreign-key failure was accepted")


def test_catalog_target_revision_mismatch_blocks(tmp_path: Path) -> None:
    repo, base, target = make_repo(tmp_path)
    fixture = make_fixture(tmp_path, base, target)
    db = make_db(tmp_path, target)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE repos SET target_commit_sha=?", (base,))
    conn.commit(); conn.close()
    try:
        analyze_fixture(fixture, make_manifest(tmp_path, repo), db, "ia-main")
    except Step1Error as exc:
        assert exc.code == "catalog_revision_mismatch"
    else:
        raise AssertionError("catalog target revision mismatch was accepted")


def test_catalog_preflight_failures(tmp_path: Path) -> None:
    repo, base, target = make_repo(tmp_path)
    fixture = make_fixture(tmp_path, base, target)
    try: analyze_fixture(fixture, make_manifest(tmp_path, repo), tmp_path / "missing.db", "ia-main")
    except Step1Error as exc: assert exc.code == "catalog_unavailable"
    else: raise AssertionError("missing catalog was accepted")
    bad = tmp_path / "bad.db"; sqlite3.connect(bad).close()
    try: analyze_fixture(fixture, make_manifest(tmp_path, repo), bad, "ia-main")
    except Step1Error as exc: assert exc.code == "catalog_schema_mismatch"
    else: raise AssertionError("schema mismatch was accepted")


def test_report_validator_and_blocked_envelope() -> None:
    report = blocked_report(Step1Error("empty_diff", "none"))
    assert validate(report) == []
    assert report["error"]["code"] == "empty_diff"


def test_report_validator_requires_classification_warnings_and_complete_surfaces() -> None:
    report = {
        "schema_version": "0.1",
        "analysis_kind": "pr_impact_step_1",
        "status": "complete",
        "input": {"manifest": "m", "repo_key": "ia-main", "repo_root": "r", "base_revision": "b", "target_revision": "t"},
        "preflight": {}, "changed_files": [], "direct_traces": [
            {"surface": "files", "status": "empty", "facts": []}
        ],
        "onboarding_feasibility": [], "impact_ranking": [], "gaps": [], "warnings": [], "provenance": {},
    }
    assert "empty trace must include a warning" in validate(report)
    report["direct_traces"][0]["warning"] = "not proof"
    errors = validate(report)
    assert "complete report is missing direct traces: actionui, actionui_artifacts, actionui_events, actionui_fields, actionui_includes, database_consumers, entity_metadata, entity_occurrences, incoming_relationships, nextgen, nextgen_artifacts, openapi_documents, openapi_entity_links, outgoing_relationships, permissions, rest_endpoints, source_diagnostics, symbols, tests, workflows" in errors
    assert "complete report contains a supported direct-trace gap" in errors
