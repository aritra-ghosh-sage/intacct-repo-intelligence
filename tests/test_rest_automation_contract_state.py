from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from catalog.migrations import _apply_rest_automation_contract_migration
from catalog.rest_automation_contract import resolve_contract_v1_paths
from intacct_mcp.server import (
    CatalogState,
    entity_test_coverage_impl,
    rest_coverage_impl,
)
from scripts.build_gherkin_coverage import build

ROOT = Path(__file__).resolve().parents[1]


class RestAutomationContractStateTests(unittest.TestCase):
    def test_migration_preserves_legacy_request_as_unknown_scope(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.executescript(
            """CREATE TABLE repos(id INTEGER PRIMARY KEY);
               CREATE TABLE test_cases(id INTEGER PRIMARY KEY);
               CREATE TABLE test_requests(
                 id INTEGER PRIMARY KEY, test_case_id INTEGER NOT NULL,
                 ordinal INTEGER NOT NULL, step_line INTEGER NOT NULL,
                 operation_kind TEXT NOT NULL DEFAULT 'unknown'
               );
               CREATE TABLE test_coverage_build_state(
                 repo_id INTEGER PRIMARY KEY, extractor_version TEXT NOT NULL,
                 candidate_build_token TEXT NOT NULL, indexed_suite_target_sha TEXT NOT NULL,
                 dependency_revisions_json TEXT NOT NULL, entity_mapping_sha1 TEXT NOT NULL,
                 coverage_dependency_fingerprint TEXT NOT NULL
               );
               INSERT INTO test_cases VALUES(1);
               INSERT INTO test_requests(id,test_case_id,ordinal,step_line) VALUES(1,1,1,1);
               INSERT INTO test_coverage_build_state VALUES(1,'legacy','token','sha','{}','mapping','fingerprint');
            """
        )
        _apply_rest_automation_contract_migration(conn)
        self.assertEqual(
            "unknown", conn.execute("SELECT coverage_scope FROM test_requests").fetchone()[0]
        )
        self.assertEqual(
            (0, "[]"),
            tuple(
                conn.execute(
                    "SELECT coverage_contract_version,contract_input_hashes_json FROM test_coverage_build_state"
                ).fetchone()
            ),
        )

    def _coverage_db(self, contract_version: int, contract_inputs: str) -> tuple[CatalogState, sqlite3.Connection]:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        scope = "endpoint" if contract_version == 1 else "unknown"
        db_path = Path(directory.name) / "catalog.db"
        conn = sqlite3.connect(db_path)
        conn.executescript((ROOT / "catalog" / "schema.sql").read_text())
        conn.executescript(
            """INSERT INTO repos(id,repo_key,local_root,tracked_branch) VALUES
                   (1,'ia-main','/main','main'),(2,'suite','/suite','main');
               INSERT INTO entity_nodes(id,name) VALUES(1,'Account');
               INSERT INTO files(id,repo_id,path,sha1) VALUES
                   (1,1,'endpoint.yaml','endpoint'),(2,2,'mapping.json','mapping');
               INSERT INTO rest_endpoints(id,repo_id,method,path,source_version,entity_id,file_id)
                   VALUES(1,1,'GET','/objects/accounts/account','v1',1,1);
               INSERT INTO test_cases(id,repo_id,file_id,feature_name,scenario_name,case_name,feature_line,scenario_line)
                   VALUES(1,2,2,'Account','read','read',1,1);
               INSERT INTO test_requests(id,test_case_id,ordinal,step_line,method,normalized_path,request_version,operation_kind,coverage_scope)
                   VALUES(1,1,1,2,'GET','/objects/accounts/account','v1','collection','""" + scope + """');
               INSERT INTO test_endpoint_links(test_request_id,rest_endpoint_id,resolution_kind)
                   VALUES(1,1,'exact_version');
               INSERT INTO test_entity_links(test_request_id,entity_id,rest_endpoint_id) VALUES(1,1,1);
            """
        )
        conn.execute(
            """INSERT INTO test_coverage_build_state(
                   repo_id,extractor_version,candidate_build_token,indexed_suite_target_sha,
                   dependency_revisions_json,entity_mapping_sha1,coverage_contract_version,
                   contract_input_hashes_json,coverage_dependency_fingerprint
               ) VALUES(2,'gherkin-coverage-v2-workflow-action','token','suite-sha','{}','mapping',?,?, 'fingerprint')""",
            (contract_version, contract_inputs),
        )
        conn.commit()
        conn.close()
        return CatalogState(db_path, Path(directory.name) / "graph.lbug"), sqlite3.connect(db_path)

    def test_mcp_rejects_contract_v1_with_missing_or_stale_inputs_but_keeps_v0_output(self) -> None:
        state, conn = self._coverage_db(1, "[]")
        conn.close()
        stale = rest_coverage_impl(state, "Account")
        self.assertEqual("error", stale["status"])
        self.assertEqual("contract_v1_coverage_stale", stale["error"]["code"])

        stale_entity = entity_test_coverage_impl(state, "Account")
        self.assertEqual("capability_unavailable", stale_entity["status"])
        self.assertEqual("contract_v1_coverage_stale", stale_entity["error"]["code"])

        state, conn = self._coverage_db(1, "[]")
        conn.execute("DELETE FROM test_coverage_build_state WHERE repo_id=2")
        conn.commit()
        conn.close()
        missing_entity = entity_test_coverage_impl(state, "Account")
        self.assertEqual("capability_unavailable", missing_entity["status"])
        self.assertEqual("contract_v1_coverage_stale", missing_entity["error"]["code"])

        legacy_state, conn = self._coverage_db(0, "[]")
        conn.close()
        legacy = rest_coverage_impl(legacy_state, "Account")
        self.assertEqual("ok", legacy["status"])
        self.assertIn("endpoint_coverage", legacy["data"])
        legacy_entity = entity_test_coverage_impl(legacy_state, "Account")
        self.assertEqual("ok", legacy_entity["status"])

    def test_candidate_build_persists_v1_input_hashes_and_mapping_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            features = root / "features"
            features.mkdir()
            (features / "account.feature").write_text(
                '@version:v1\nFeature: Account\n  Scenario: read\n    When "GET" to "account"\n',
                encoding="utf-8",
            )
            artifact_contents = {
                "mapping.json": (
                    '{"contract_version":1,"mappings":[{"coverage_scope":"endpoint",'
                    '"path":"/objects/accounts/account","token":"account"}]}'
                ),
                "compatibility.json": '{"bridges":[],"contract_version":1}',
                "inventory.json": '{"contract_version":1,"entries":[]}',
            }
            for name, contents in artifact_contents.items():
                (root / name).write_text(contents, encoding="utf-8")
            paths = resolve_contract_v1_paths(
                {
                    "features_root": "features",
                    "object_mapping": "mapping.json",
                    "version_compatibility": "compatibility.json",
                    "non_request_inventory": "inventory.json",
                },
                root,
            )
            db_path = root / "catalog.db"
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            conn.executescript((ROOT / "catalog" / "schema.sql").read_text())
            conn.executescript(
                """INSERT INTO repos(repo_key,local_root,tracked_branch,indexed_commit_sha) VALUES
                       ('ia-main','/main','main','main-sha'),
                       ('suite','/suite','main','suite-sha');
                   INSERT INTO entity_nodes(id,name) VALUES(1,'Account');
                   INSERT INTO files(id,repo_id,path,sha1) VALUES(1,1,'endpoint.yaml','endpoint');
                   INSERT INTO rest_endpoints(id,repo_id,method,path,source_version,entity_id,file_id)
                       VALUES(1,1,'GET','/objects/accounts/account','v1',1,1);"""
            )
            suite_id = conn.execute("SELECT id FROM repos WHERE repo_key='suite'").fetchone()[0]
            for index, (name, contents) in enumerate(artifact_contents.items(), start=10):
                conn.execute(
                    "INSERT INTO files(id,repo_id,path,sha1) VALUES(?,?,?,?)",
                    (index, suite_id, name, hashlib.sha1(contents.encode()).hexdigest()),
                )
            stats = build(
                conn,
                "suite",
                root,
                paths.object_mapping,
                paths.features_root,
                contract_v1_paths=paths,
                candidate_build_token="candidate",
                indexed_suite_target_sha="suite-sha",
                dependency_revisions={"ia-main": "main-sha", "suite": "suite-sha"},
            )
            state = CatalogState(db_path, root / "graph.lbug")
            self.assertEqual("ok", rest_coverage_impl(state, "Account")["status"])
            self.assertEqual("ok", entity_test_coverage_impl(state, "Account")["status"])
            conn.execute(
                "UPDATE files SET sha1=? WHERE repo_id=? AND path='mapping.json'",
                ("0" * 40, suite_id),
            )
            conn.commit()
            stale = rest_coverage_impl(state, "Account")
            self.assertEqual("error", stale["status"])
            self.assertEqual("contract_v1_coverage_stale", stale["error"]["code"])
            for name, contents in artifact_contents.items():
                expected_sha1 = hashlib.sha1(contents.encode()).hexdigest()
                conn.execute(
                    "UPDATE files SET sha1=? WHERE repo_id=? AND path=?",
                    ("0" * 40, suite_id, name),
                )
                pattern = "manifest object_mapping" if name == "mapping.json" else name
                with self.assertRaisesRegex(ValueError, pattern):
                    build(
                        conn,
                        "suite",
                        root,
                        paths.object_mapping,
                        paths.features_root,
                        contract_v1_paths=paths,
                        candidate_build_token="candidate",
                        indexed_suite_target_sha="suite-sha",
                        dependency_revisions={
                            "ia-main": "main-sha",
                            "suite": "suite-sha",
                        },
                    )
                conn.execute(
                    "UPDATE files SET sha1=? WHERE repo_id=? AND path=?",
                    (expected_sha1, suite_id, name),
                )
            conn.commit()
        self.assertEqual(1, stats["links"])
        request = conn.execute(
            "SELECT coverage_scope,mapping_provenance_json FROM test_requests"
        ).fetchone()
        self.assertEqual("endpoint", request["coverage_scope"])
        self.assertEqual(
            {"coverage_scope": "endpoint", "path": "/objects/accounts/account", "token": "account"},
            json.loads(request["mapping_provenance_json"]),
        )
        state = conn.execute(
            "SELECT coverage_contract_version,contract_input_hashes_json FROM test_coverage_build_state"
        ).fetchone()
        self.assertEqual(1, state["coverage_contract_version"])
        self.assertEqual(
            ["object_mapping", "version_compatibility", "non_request_inventory"],
            [item["field"] for item in json.loads(state["contract_input_hashes_json"])],
        )
        conn.close()


if __name__ == "__main__":
    unittest.main()
