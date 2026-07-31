from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from catalog.graph_projection import GRAPH_PROJECTION_VERSION
from intacct_mcp.server import Catalog, CatalogState

ROOT = Path(__file__).resolve().parents[1]


class McpMultiRepoTests(unittest.TestCase):
    def test_graph_freshness_is_scoped_to_configured_graph_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = root / "catalog.db"
            configured_graph = root / "configured.lbug"
            other_graph = root / "other.lbug"
            configured_graph.write_bytes(b"stale")
            conn = sqlite3.connect(db_path)
            try:
                conn.executescript((ROOT / "catalog/schema.sql").read_text())
                catalog_build_id = conn.execute(
                    """INSERT INTO catalog_builds(
                           build_token,catalog_path,requested_mode,effective_mode,
                           status,source_revisions_json,delta_contract_version,
                           content_fingerprint,completed_at
                       ) VALUES (?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
                    (
                        "catalog",
                        str(db_path),
                        "full",
                        "full",
                        "active",
                        "{}",
                        2,
                        "current-fingerprint",
                    ),
                ).lastrowid
                conn.execute(
                    """INSERT INTO graph_builds(
                           graph_path,source_db,status,source_fingerprint,
                           catalog_build_id,build_mode,projection_version,
                           source_revisions_json
                       ) VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        str(configured_graph.resolve()),
                        str(db_path),
                        "active",
                        "stale-fingerprint",
                        catalog_build_id,
                        "full",
                        GRAPH_PROJECTION_VERSION,
                        "{}",
                    ),
                )
                conn.execute(
                    """INSERT INTO graph_builds(
                           graph_path,source_db,status,source_fingerprint,
                           catalog_build_id,build_mode,projection_version,
                           source_revisions_json
                       ) VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        str(other_graph.resolve()),
                        str(db_path),
                        "active",
                        "current-fingerprint",
                        catalog_build_id,
                        "full",
                        GRAPH_PROJECTION_VERSION,
                        "{}",
                    ),
                )
                conn.commit()
            finally:
                conn.close()

            state = CatalogState(db_path, configured_graph)
            catalog_conn = state.conn()
            try:
                snapshot = state.snapshot(catalog_conn)
                self.assertFalse(snapshot["graph_fresh"])
                self.assertEqual(
                    snapshot["active_graph_build"]["source_fingerprint"],
                    "stale-fingerprint",
                )
                self.assertFalse(state.graph_active(catalog_conn))
            finally:
                catalog_conn.close()

    def test_entity_context_and_repository_status_are_repo_qualified(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "catalog.db"
            conn = sqlite3.connect(db_path)
            try:
                conn.executescript((ROOT / "catalog/schema.sql").read_text())
                conn.executescript(
                    """
                    INSERT INTO repos(
                        id, repo_key, local_root, tracked_branch, indexed_commit_sha,
                        index_status, last_attempt_status, last_attempted_at, last_attempt_error
                    ) VALUES
                        (1, 'one', '/repo/one', 'main', 'one-sha', 'active', 'failed', '2026-07-21T00:00:00Z', 'dirty checkout'),
                        (2, 'two', '/repo/two', 'main', 'two-sha', 'active', 'active', '2026-07-21T00:01:00Z', NULL);
                    INSERT INTO entity_nodes(id, name, entity_type) VALUES (1, 'Customer', 'entity');
                    INSERT INTO entity_occurrences(
                        repo_id, entity_id, ent_file, module, table_name, view_name, dummy, extractor, confidence
                    ) VALUES
                        (1, 1, 'one/Customer.ent', 'one-module', 'one_customer', NULL, 0, 'fixture', 1.0),
                        (2, 1, 'two/Customer.ent', 'two-module', 'two_customer', NULL, 0, 'fixture', 1.0);
                    """
                )
                conn.commit()
            finally:
                conn.close()

            catalog = Catalog(str(db_path), str(Path(directory) / "missing.lbug"))
            one = catalog.entity("Customer", "one")
            self.assertEqual(one["data"]["occurrences"][0]["repo_key"], "one")
            self.assertEqual(one["data"]["occurrences"][0]["module"], "one-module")
            self.assertEqual(len(one["data"]["occurrences"]), 1)

            status = catalog.repositories()
            repo_one = status["data"]["repositories"][0]
            self.assertEqual(repo_one["indexed_commit_sha"], "one-sha")
            self.assertEqual(repo_one["last_attempt_status"], "failed")
            self.assertEqual(repo_one["last_attempt_error"], "dirty checkout")
