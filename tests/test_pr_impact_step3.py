from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest
import yaml

from catalog.pr_impact_step3 import Step3Error, analyze_fixture, blocked_report
from scripts.validate_pr_impact_step3 import validate


def git(cwd: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(cwd), *args], text=True).strip()


def make_repo(
    tmp_path: Path, *, changed_content: str = "target()\n"
) -> tuple[Path, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    (repo / "a.php").write_text("base()\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "a.php").write_text(changed_content, encoding="utf-8")
    git(repo, "commit", "-qam", "target")
    return repo, base, git(repo, "rev-parse", "HEAD")


def fixture(
    tmp_path: Path, base: str, target: str, changed: list[dict] | None = None
) -> Path:
    path = tmp_path / "fixture.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": "0.1",
                "analysis_kind": "pr_impact_step_0",
                "pull_request": {
                    "repository": "intacct/ia-app",
                    "base_revision": base,
                    "target_revision": target,
                },
                "changed_files": changed or [{"path": "a.php", "status": "modified"}],
            }
        ),
        encoding="utf-8",
    )
    return path


def manifest(tmp_path: Path, repo: Path) -> Path:
    path = tmp_path / "manifest.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "repositories": [
                    {
                        "repo_key": "ia-main",
                        "local_root": str(repo),
                        "tracked_branch": "main",
                        "profile": "intacct_app",
                        "builders": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def make_db(
    tmp_path: Path, repo: Path, target: str, *, files: list[str] | None = None
) -> Path:
    db = tmp_path / "catalog.db"
    conn = sqlite3.connect(db)
    conn.executescript(Path("catalog/repo_v1_schema.sql").read_text(encoding="utf-8"))
    conn.execute(
        "INSERT INTO catalog_builds(build_token,catalog_path,status,source_revisions_json) VALUES(?,?,?,?)",
        ("build", str(db), "active", json.dumps({"ia-main": target})),
    )
    conn.execute(
        "INSERT INTO repos(repo_key,local_root,tracked_branch,target_commit_sha,build_id) VALUES(?,?,?,?,?)",
        ("ia-main", str(repo), "main", target, 1),
    )
    for path in files or ["a.php", "b.php", "c.php"]:
        content = (
            (repo / path).read_bytes() if (repo / path).exists() else path.encode()
        )
        conn.execute(
            "INSERT INTO files(repo_id,path,blob_object_id,file_mode,size_bytes,language,source_commit_sha) VALUES(?,?,?,?,?,?,?)",
            (
                1,
                path,
                hashlib.sha1(content).hexdigest(),
                100644,
                len(content),
                "php",
                target,
            ),
        )
    conn.commit()
    conn.close()
    return db


def add_symbols_and_edges(db: Path, *, max_hop_fixture: bool = True) -> None:
    conn = sqlite3.connect(db)
    file_ids = {
        path: conn.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()[0]
        for path in ("a.php", "b.php", "c.php")
    }
    conn.execute(
        "INSERT INTO symbols(repo_id,file_id,name,kind,start_line,end_line,language,stable_key) VALUES(1,?,?,?,?,?,?,?)",
        (file_ids["a.php"], "target", "function", 1, 1, "php", "a-target"),
    )
    conn.execute(
        "INSERT INTO symbols(repo_id,file_id,name,kind,start_line,end_line,language,stable_key) VALUES(1,?,?,?,?,?,?,?)",
        (file_ids["b.php"], "caller", "function", 1, 1, "php", "b-caller"),
    )
    conn.execute(
        "INSERT INTO symbols(repo_id,file_id,name,kind,start_line,end_line,language,stable_key) VALUES(1,?,?,?,?,?,?,?)",
        (file_ids["c.php"], "outer", "function", 1, 1, "php", "c-outer"),
    )
    target_id, caller_id, outer_id = range(1, 4)

    def edge(
        source: int, target: int, relation: str, resolution: str, evidence: str
    ) -> None:
        conn.execute(
            """INSERT INTO relationships(
                repo_id,source_symbol_id,source_name,source_kind,target_symbol_id,target_name,
                target_kind,relationship_type,file_id,file_path,language,confidence,evidence,
                resolution_class,resolution_reason,extractor)
                VALUES(1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                source,
                "caller" if source == caller_id else "outer",
                "function",
                target,
                "target" if target == target_id else "caller",
                "function",
                relation,
                file_ids["b.php" if source == caller_id else "c.php"],
                "b.php" if source == caller_id else "c.php",
                "php",
                1.0,
                evidence,
                resolution,
                "target_symbol_id_present",
                "test",
            ),
        )

    edge(caller_id, target_id, "CALLS", "project_resolved", "caller-target")
    edge(outer_id, caller_id, "STATIC_CALLS", "project_resolved", "outer-caller")
    if max_hop_fixture:
        edge(caller_id, target_id, "CALLS", "project_resolved", "parallel")
    conn.commit()
    conn.close()


def run(tmp_path: Path, *, hops: int = 2) -> dict:
    repo, base, target = make_repo(tmp_path)
    db = make_db(tmp_path, repo, target)
    add_symbols_and_edges(db)
    return analyze_fixture(
        fixture(tmp_path, base, target), manifest(tmp_path, repo), db, "ia-main", hops
    )


def test_two_hop_incoming_traversal_preserves_parallel_edges(tmp_path: Path) -> None:
    report = run(tmp_path)
    assert report["status"] == "complete"
    assert [item["symbol_id"] for item in report["seed_symbols"]] == [1]
    assert [item["symbol_id"] for item in report["reached_symbols"]] == [2, 3]
    assert len(report["transitive_edges"]) == 3
    assert report["reached_symbols"][0]["contributing_edge_ids"] == [1, 3]
    assert validate(report) == []


def test_frontier_queries_are_batched_below_sqlite_variable_limit(
    tmp_path: Path,
) -> None:
    repo, base, target = make_repo(tmp_path)
    db = make_db(tmp_path, repo, target, files=["a.php", "b.php"])
    conn = sqlite3.connect(db)
    a_file, b_file = (
        conn.execute("SELECT id FROM files WHERE path='a.php'").fetchone()[0],
        conn.execute("SELECT id FROM files WHERE path='b.php'").fetchone()[0],
    )
    for index in range(401):
        conn.execute(
            "INSERT INTO symbols(repo_id,file_id,name,kind,start_line,end_line,language,stable_key) VALUES(1,?,?,?,?,?,?,?)",
            (a_file, f"seed{index}", "function", 1, 1, "php", f"seed-{index}"),
        )
    conn.execute(
        "INSERT INTO symbols(repo_id,file_id,name,kind,start_line,end_line,language,stable_key) VALUES(1,?,?,?,?,?,?,?)",
        (b_file, "caller", "function", 1, 1, "php", "caller"),
    )
    caller_id = 402
    for seed_id in range(1, 402):
        conn.execute(
            """INSERT INTO relationships(
                repo_id,source_symbol_id,source_name,source_kind,target_symbol_id,target_name,
                target_kind,relationship_type,file_id,file_path,language,confidence,evidence,
                resolution_class,resolution_reason,extractor)
                VALUES(1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                caller_id,
                "caller",
                "function",
                seed_id,
                f"seed{seed_id - 1}",
                "function",
                "CALLS",
                b_file,
                "b.php",
                "php",
                1.0,
                f"edge-{seed_id}",
                "project_resolved",
                "target_symbol_id_present",
                "test",
            ),
        )
    conn.commit()
    conn.close()
    report = analyze_fixture(
        fixture(tmp_path, base, target), manifest(tmp_path, repo), db, "ia-main", 1
    )
    assert report["status"] == "complete"
    assert len(report["seed_symbols"]) == 401
    assert len(report["transitive_edges"]) == 401
    assert [item["symbol_id"] for item in report["reached_symbols"]] == [402]
    assert validate(report) == []


def test_max_hops_one_excludes_second_hop(tmp_path: Path) -> None:
    report = run(tmp_path, hops=1)
    assert [item["symbol_id"] for item in report["reached_symbols"]] == [2]
    assert [item["hop"] for item in report["transitive_edges"]] == [1, 1]
    assert validate(report) == []


def test_entity_context_is_explicitly_unavailable_for_seed_and_reached_symbols(
    tmp_path: Path,
) -> None:
    report = run(tmp_path)
    symbol_ids = {
        item["symbol_id"]
        for item in [*report["seed_symbols"], *report["reached_symbols"]]
    }
    assert report["status"] == "complete"
    assert report["entity_context"] == {
        "status": "unavailable",
        "reason": "repo_v1_symbol_entity_mapping_not_modelled",
        "mappings": [],
        "unavailable_symbol_ids": sorted(symbol_ids),
    }
    assert "entity_context:repo_v1_symbol_entity_mapping_not_modelled" in report["gaps"]
    assert validate(report) == []


def test_same_file_entity_occurrence_does_not_infer_symbol_mapping(
    tmp_path: Path,
) -> None:
    repo, base, target = make_repo(tmp_path)
    db = make_db(tmp_path, repo, target)
    add_symbols_and_edges(db)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO entity_nodes(name) VALUES('SameFileEntity')")
    conn.execute(
        """INSERT INTO entity_occurrences(
            repo_id,entity_id,source_file_id,source_key,source_commit_sha,evidence,extractor
        ) VALUES(1,1,1,'same-file',?,'fixture','test')""",
        (target,),
    )
    conn.commit()
    conn.close()
    report = analyze_fixture(
        fixture(tmp_path, base, target), manifest(tmp_path, repo), db, "ia-main"
    )
    assert report["entity_context"]["mappings"] == []
    assert validate(report) == []


def test_same_name_entity_does_not_infer_symbol_mapping(tmp_path: Path) -> None:
    repo, base, target = make_repo(tmp_path)
    db = make_db(tmp_path, repo, target)
    add_symbols_and_edges(db)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO entity_nodes(name) VALUES('target')")
    conn.execute(
        """INSERT INTO entity_occurrences(
            repo_id,entity_id,source_file_id,source_key,source_commit_sha,evidence,extractor
        ) VALUES(1,1,1,'same-name',?,'fixture','test')""",
        (target,),
    )
    conn.commit()
    conn.close()
    report = analyze_fixture(
        fixture(tmp_path, base, target), manifest(tmp_path, repo), db, "ia-main"
    )
    assert report["entity_context"]["mappings"] == []
    assert validate(report) == []


def test_non_call_relationship_is_retained_as_skipped(tmp_path: Path) -> None:
    repo, base, target = make_repo(tmp_path)
    db = make_db(tmp_path, repo, target)
    add_symbols_and_edges(db)
    conn = sqlite3.connect(db)
    conn.execute(
        """INSERT INTO relationships(
            repo_id,source_symbol_id,source_name,source_kind,target_symbol_id,target_name,
            target_kind,relationship_type,file_id,file_path,language,confidence,evidence,
            resolution_class,resolution_reason,extractor
        ) VALUES(1,2,'caller','function',1,'target','function','USES',2,'b.php','php',1.0,
                 'non-call','project_resolved','target_symbol_id_present','test')"""
    )
    conn.commit()
    conn.close()
    report = analyze_fixture(
        fixture(tmp_path, base, target), manifest(tmp_path, repo), db, "ia-main"
    )
    assert any(
        item["skip_reason"] == "non_call_relationship"
        and item["relationship_type"] == "USES"
        for item in report["skipped_edges"]
    )
    assert validate(report) == []


def test_normalized_query_target_kind_is_accepted(tmp_path: Path) -> None:
    repo, base, target = make_repo(tmp_path)
    db = make_db(tmp_path, repo, target)
    add_symbols_and_edges(db)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE symbols SET kind='cqry' WHERE id=1")
    conn.execute(
        "UPDATE relationships SET target_kind='query' WHERE target_symbol_id=1"
    )
    conn.commit()
    conn.close()
    report = analyze_fixture(
        fixture(tmp_path, base, target), manifest(tmp_path, repo), db, "ia-main"
    )
    assert report["status"] == "complete"
    assert [item["symbol_id"] for item in report["reached_symbols"]] == [2, 3]
    assert validate(report) == []


def test_edge_evidence_is_required_by_validator(tmp_path: Path) -> None:
    report = run(tmp_path)
    report["transitive_edges"][0]["evidence"] = ""
    errors = validate(report)
    assert "transitive edge requires evidence" in errors


def test_edge_extractor_and_confidence_are_required_by_validator(
    tmp_path: Path,
) -> None:
    report = run(tmp_path)
    report["transitive_edges"][0]["extractor"] = ""
    report["transitive_edges"][1]["confidence"] = 2
    errors = validate(report)
    assert "transitive edge requires extractor" in errors
    assert "transitive edge requires confidence between 0 and 1" in errors


def test_oversized_confidence_is_rejected_without_validator_crash(
    tmp_path: Path,
) -> None:
    report = run(tmp_path)
    report["transitive_edges"][0]["confidence"] = 10**1000
    errors = validate(report)
    assert "transitive edge requires confidence between 0 and 1" in errors


@pytest.mark.parametrize(
    ("table", "column", "value"),
    [
        ("relationships", "source_name", "tampered"),
        ("relationships", "target_kind", "tampered"),
        ("relationships", "file_path", "tampered.php"),
        ("files", "source_commit_sha", "0" * 40),
    ],
)
def test_source_and_target_identity_tampering_is_rejected(
    tmp_path: Path, table: str, column: str, value: str
) -> None:
    repo, base, target = make_repo(tmp_path)
    db = make_db(tmp_path, repo, target)
    add_symbols_and_edges(db)
    conn = sqlite3.connect(db)
    if table == "relationships":
        conn.execute(f"UPDATE relationships SET {column}=? WHERE id=1", (value,))
    else:
        conn.execute(f"UPDATE files SET {column}=? WHERE path='b.php'", (value,))
    conn.commit()
    conn.close()
    with pytest.raises(Exception, match="identity|file path|revision|target revision"):
        analyze_fixture(
            fixture(tmp_path, base, target), manifest(tmp_path, repo), db, "ia-main"
        )


def test_cross_repository_edge_provenance_is_rejected(tmp_path: Path) -> None:
    repo, base, target = make_repo(tmp_path)
    db = make_db(tmp_path, repo, target)
    add_symbols_and_edges(db)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE relationships SET repo_id=2 WHERE id=1")
    conn.commit()
    conn.close()
    with pytest.raises(Exception, match="foreign.?key|repository|ownership"):
        analyze_fixture(
            fixture(tmp_path, base, target), manifest(tmp_path, repo), db, "ia-main"
        )


def test_entity_gap_does_not_change_complete_no_caller_status(tmp_path: Path) -> None:
    repo, base, target = make_repo(tmp_path)
    db = make_db(tmp_path, repo, target)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO symbols(repo_id,file_id,name,kind,start_line,end_line,language,stable_key) VALUES(1,1,'target','function',1,1,'php','target')"
    )
    conn.commit()
    conn.close()
    report = analyze_fixture(
        fixture(tmp_path, base, target), manifest(tmp_path, repo), db, "ia-main"
    )
    assert report["status"] == "complete"
    assert report["reached_symbols"] == []
    assert report["transitive_edges"] == []
    assert "entity_context:repo_v1_symbol_entity_mapping_not_modelled" in report["gaps"]
    assert validate(report) == []


def test_validator_rejects_source_first_reached_after_edge_hop(
    tmp_path: Path,
) -> None:
    report = run(tmp_path)
    malformed = copy.deepcopy(report)
    # Edge 3 claims a hop-1 caller that the report says was first reached at hop 2.
    malformed["transitive_edges"][-1]["source_symbol_id"] = 3
    malformed["reached_symbols"][0]["contributing_edge_ids"] = [1]
    errors = validate(malformed)
    assert "transitive edge source was first reached after its edge hop" in errors


def test_outgoing_non_call_and_unresolved_rows_are_not_traversed(
    tmp_path: Path,
) -> None:
    repo, base, target = make_repo(tmp_path)
    db = make_db(tmp_path, repo, target)
    add_symbols_and_edges(db, max_hop_fixture=False)
    conn = sqlite3.connect(db)
    # The target is the frontier; this row is intentionally outgoing from it.
    conn.execute(
        "INSERT INTO relationships(repo_id,source_symbol_id,source_name,source_kind,target_symbol_id,target_name,target_kind,relationship_type,file_id,file_path,language,confidence,evidence,resolution_class,resolution_reason,extractor) VALUES(1,1,'target','function',2,'caller','function','CALLS',2,'b.php','php',1,'outgoing','project_resolved','target_symbol_id_present','test')"
    )
    conn.execute(
        "UPDATE relationships SET resolution_class='project_unresolved', resolution_reason='unresolved' WHERE id=1"
    )
    conn.commit()
    conn.close()
    report = analyze_fixture(
        fixture(tmp_path, base, target), manifest(tmp_path, repo), db, "ia-main"
    )
    assert report["status"] == "partial"
    assert {item["skip_reason"] for item in report["skipped_edges"]} == {
        "unresolved_resolution"
    }
    assert report["reached_symbols"] == []
    assert validate(report) == []


def test_deleted_file_is_explicit_and_partial(tmp_path: Path) -> None:
    repo, _base, target = make_repo(tmp_path)
    git(repo, "rm", "-q", "a.php")
    git(repo, "commit", "-qm", "delete")
    deleted_target = git(repo, "rev-parse", "HEAD")
    db = make_db(tmp_path, repo, deleted_target, files=["b.php", "c.php"])
    report = analyze_fixture(
        fixture(
            tmp_path, target, deleted_target, [{"path": "a.php", "status": "deleted"}]
        ),
        manifest(tmp_path, repo),
        db,
        "ia-main",
    )
    assert report["status"] == "partial"
    assert report["seed_files"][0]["state"] == "deleted"
    assert report["seed_symbols"] == []
    assert validate(report) == []


def test_symbol_less_files_are_empty(tmp_path: Path) -> None:
    repo, base, target = make_repo(tmp_path)
    db = make_db(tmp_path, repo, target, files=["a.php"])
    report = analyze_fixture(
        fixture(tmp_path, base, target), manifest(tmp_path, repo), db, "ia-main"
    )
    assert report["status"] == "empty"
    assert report["seed_files"][0]["state"] == "symbol_less"
    assert validate(report) == []


def test_parser_failed_file_is_explicit_and_partial(tmp_path: Path) -> None:
    repo, base, target = make_repo(tmp_path)
    db = make_db(tmp_path, repo, target, files=["a.php"])
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO symbol_diagnostics(repo_id,file_id,diagnostic_key,severity,code,message,source_commit_sha) VALUES(1,1,'diag','error','parse','failed',?)",
        (target,),
    )
    conn.commit()
    conn.close()
    report = analyze_fixture(
        fixture(tmp_path, base, target), manifest(tmp_path, repo), db, "ia-main"
    )
    assert report["status"] == "partial"
    assert report["seed_files"][0]["state"] == "parser_failed"
    assert report["seed_symbols"] == []
    assert validate(report) == []


def test_missing_target_file_blocks_without_traversal(tmp_path: Path) -> None:
    repo, base, target = make_repo(tmp_path)
    db = make_db(tmp_path, repo, target, files=["b.php"])
    report = None
    try:
        analyze_fixture(
            fixture(tmp_path, base, target), manifest(tmp_path, repo), db, "ia-main"
        )
    except Step3Error as exc:
        report = blocked_report(exc)
    assert report is not None
    assert report["status"] == "blocked"
    assert report["error"]["code"] == "catalog_provenance_mismatch"
    assert report["transitive_edges"] == []
    assert report["entity_context"]["mappings"] == []
    assert report["entity_context"]["unavailable_symbol_ids"] == []
    assert report["seed_files"][0]["state"] == "missing_target_file"
    assert validate(report) == []


def test_stale_target_file_blocks(tmp_path: Path) -> None:
    repo, base, target = make_repo(tmp_path)
    db = make_db(tmp_path, repo, target)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE files SET source_commit_sha=? WHERE path='a.php'", (base,))
    conn.commit()
    conn.close()
    with pytest.raises(Exception, match="target revision"):
        analyze_fixture(
            fixture(tmp_path, base, target), manifest(tmp_path, repo), db, "ia-main"
        )


def test_repeated_reports_are_deterministic_and_read_only(tmp_path: Path) -> None:
    repo, base, target = make_repo(tmp_path)
    db = make_db(tmp_path, repo, target)
    add_symbols_and_edges(db)
    fixture_path, manifest_path = (
        fixture(tmp_path, base, target),
        manifest(tmp_path, repo),
    )
    before_fixture, before_manifest, before_db = (
        fixture_path.read_bytes(),
        manifest_path.read_bytes(),
        db.read_bytes(),
    )
    first = analyze_fixture(fixture_path, manifest_path, db, "ia-main")
    second = analyze_fixture(fixture_path, manifest_path, db, "ia-main")
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert "entity_context:repo_v1_symbol_entity_mapping_not_modelled" in first["gaps"]
    assert (fixture_path.read_bytes(), manifest_path.read_bytes(), db.read_bytes()) == (
        before_fixture,
        before_manifest,
        before_db,
    )
