"""Archive lifecycle regressions at the SQLite-to-graph query boundary."""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from click.testing import CliRunner

from catalog.content_fingerprint import logical_content_fingerprint
from catalog.graph_projection import GRAPH_PROJECTION_VERSION
from intacct_mcp.server import Catalog
from scripts.build_graph import graph_delta_eligibility
from scripts.query_graph import cli

ROOT = Path(__file__).resolve().parents[1]


def _active_catalog(conn: sqlite3.Connection, db_path: Path, *, mode: str = "full") -> int:
    fingerprint = logical_content_fingerprint(conn)
    return int(
        conn.execute(
            """INSERT INTO catalog_builds(
                   build_token,catalog_path,requested_mode,effective_mode,status,
                   source_revisions_json,delta_contract_version,content_fingerprint,
                   completed_at
               ) VALUES (?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
            ("fixture", str(db_path.resolve()), mode, mode, "active", "{}", 2, fingerprint),
        ).lastrowid
    )


class GraphArchiveSafetyTests(unittest.TestCase):
    def _database(self, root: Path) -> tuple[Path, sqlite3.Connection]:
        db_path = root / "catalog.db"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.executescript((ROOT / "catalog/schema.sql").read_text())
        return db_path, conn

    def test_query_graph_blocks_stale_generation_before_ladybug_open(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path, conn = self._database(root)
            graph_path = root / "graph.lbug"
            graph_path.write_bytes(b"not opened")
            try:
                conn.execute(
                    "INSERT INTO repos(repo_key,local_root,tracked_branch) VALUES ('active','/active','main')"
                )
                build_id = _active_catalog(conn, db_path)
                conn.execute(
                    """INSERT INTO graph_builds(
                           graph_path,source_db,status,source_fingerprint,catalog_build_id,
                           build_mode,projection_version,source_revisions_json
                       ) VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        str(graph_path.resolve()),
                        str(db_path.resolve()),
                        "active",
                        "deliberately-stale",
                        build_id,
                        "full",
                        GRAPH_PROJECTION_VERSION,
                        "{}",
                    ),
                )
                conn.commit()
            finally:
                conn.close()

            with mock.patch("scripts.query_graph.get_graph_connection") as graph_open:
                result = CliRunner().invoke(
                    cli,
                    [
                        "who-uses",
                        "--symbol-id",
                        "1",
                        "--db",
                        str(db_path),
                        "--graph",
                        str(graph_path),
                        "--json",
                    ],
                )
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertEqual(json.loads(result.output)["error"]["code"], "graph_stale")
            graph_open.assert_not_called()

    def test_archive_catalog_generation_is_never_delta_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path, conn = self._database(root)
            graph_path = root / "graph.lbug"
            graph_path.write_bytes(b"prior graph")
            try:
                conn.execute(
                    "INSERT INTO repos(repo_key,local_root,tracked_branch) VALUES ('archived','/archive','main')"
                )
                _active_catalog(conn, db_path, mode="archive")
                conn.commit()
            finally:
                conn.close()
            shutil.copy2(db_path, db_path.with_name("catalog.db.previous"))

            eligibility = graph_delta_eligibility(str(db_path), str(graph_path))
            self.assertFalse(eligibility.eligible)
            self.assertEqual(
                eligibility.reason, "archive catalog generations require a full graph build"
            )

    def test_mcp_explicit_archived_repository_returns_structured_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path, conn = self._database(root)
            try:
                conn.execute(
                    """INSERT INTO repos(
                           repo_key,local_root,tracked_branch,lifecycle_state,
                           archive_source,archive_reason
                       ) VALUES ('archived','/archive','main','archived','manual','fixture')"""
                )
                conn.commit()
            finally:
                conn.close()

            response = Catalog(str(db_path), str(root / "graph.lbug")).entity(
                "Anything", "archived"
            )
            self.assertEqual(response["status"], "error")
            self.assertEqual(response["error"]["code"], "repository_archived")
            self.assertEqual(
                response["error"]["details"]["repository"]["archive_reason"], "fixture"
            )
