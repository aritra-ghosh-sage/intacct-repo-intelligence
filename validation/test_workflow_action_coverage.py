from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from intacct_mcp.server import CatalogState, entity_test_coverage_impl


ROOT = Path(__file__).resolve().parents[1]
EXTRACTOR = "gherkin-coverage-v2-workflow-action"


class WorkflowActionCoverageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = Path(self.directory.name) / "catalog.db"
        self.conn = sqlite3.connect(self.db)
        self.conn.executescript((ROOT / "catalog/schema.sql").read_text())
        self.conn.executescript(
            """INSERT INTO repos(id,repo_key,local_root,tracked_branch,indexed_commit_sha)
                   VALUES(1,'ia-main','/main','main','main-sha'),
                         (2,'suite','/suite','main','suite-sha');
               INSERT INTO entity_nodes(id,name) VALUES(1,'GLBatch');
               INSERT INTO files(id,repo_id,path,sha1) VALUES
                   (1,1,'openapi.yaml','x'),
                   (2,2,'src/test/resources/object-mapping.json','mapping-sha'),
                   (3,2,'feature.feature','feature-sha');
               INSERT INTO rest_endpoints(id,repo_id,method,path,source_version,entity_id,file_id)
                   VALUES(1,1,'POST','/workflows/gl/batch/approve','s1',1,1);
               INSERT INTO test_cases(id,repo_id,file_id,feature_name,scenario_name,case_name,feature_line,scenario_line)
                   VALUES(1,2,3,'GL','approve','approve',1,2),
                         (2,2,3,'GL','decline','decline',1,4),
                         (3,2,3,'GL','unrelated','unrelated',1,6);
               INSERT INTO test_requests(id,test_case_id,ordinal,step_line,operation_kind,normalized_path,workflow_action) VALUES
                   (1,1,1,3,'workflow','/workflows/gl/batch/approve','approve'),
                   (2,2,1,5,'workflow','/workflows/gl/batch/decline','decline'),
                   (3,3,1,7,'workflow','/workflows/gl/other/approve','approve');
               INSERT INTO test_entity_links(test_request_id,entity_id,rest_endpoint_id) VALUES(1,1,1),(2,1,1);
            """
        )
        revisions = {"ia-main": "main-sha", "suite": "suite-sha"}
        fingerprint = hashlib.sha256(json.dumps({"extractor_version": EXTRACTOR, "dependency_revisions": revisions, "entity_mapping_sha1": "mapping-sha"}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.conn.execute(
            "INSERT INTO test_coverage_build_state VALUES(2,?,?,?,?,?,?,CURRENT_TIMESTAMP)",
            (EXTRACTOR, "token", "suite-sha", json.dumps(revisions, sort_keys=True, separators=(",", ":")), "mapping-sha", fingerprint),
        )
        self.conn.commit()
        self.conn.close()
        self.state = CatalogState(self.db, Path(self.directory.name) / "graph.lbug")

    def test_action_is_case_insensitive_and_same_request_scoped(self) -> None:
        response = entity_test_coverage_impl(self.state, "GLBatch", workflow_action="APPROVE")
        self.assertEqual("ok", response["status"])
        self.assertEqual(["approve"], [case["case_name"] for case in response["data"]["test_cases"]])

    def test_legacy_workflow_name_only_filters_context(self) -> None:
        response = entity_test_coverage_impl(self.state, "GLBatch", workflow_name="approve")
        self.assertEqual(2, response["data"]["total_test_case_count"])
        self.assertIn("filter_warnings", response["data"])

    def test_mapping_drift_fails_closed(self) -> None:
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE files SET sha1='changed' WHERE id=2")
        conn.commit()
        conn.close()
        response = entity_test_coverage_impl(self.state, "GLBatch", workflow_action="approve")
        self.assertEqual("capability_unavailable", response["status"])
        self.assertEqual("workflow_action_filter_unavailable", response["error"]["code"])


if __name__ == "__main__":
    unittest.main()
