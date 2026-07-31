from __future__ import annotations

import fcntl
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from catalog.db import get_connection
from catalog.migrations import apply_delta_refresh_migration
from scripts.build_graph import (
    create_sqlite_snapshot,
    preserve_previous_graph,
    promote_validated_graph,
)
from scripts.query_graph import (
    EntityAmbiguityError,
    _query_bounded_incoming_traversal,
    _query_entity_from_graph,
)

ROOT = Path(__file__).resolve().parents[1]


def schema_signature(conn: sqlite3.Connection):
    columns = [
        tuple(row) for row in conn.execute("PRAGMA table_info(graph_builds)").fetchall()
    ]
    indexes = [
        tuple(row[1:])
        for row in conn.execute("PRAGMA index_list(graph_builds)").fetchall()
        if row[1].startswith("idx_graph_builds")
    ]
    return columns, indexes


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def get_all(self):
        return self._rows


class FakeGraphConnection:
    def __init__(self):
        self.calls = []

    def execute(self, query, params):
        self.calls.append((query, params))
        target = params["symbol_id"]
        if target == 10:
            rows = [
                [20, "consumer", "method", 10, "CALLS"],
                [30, "other", "method", 10, "USES"],
            ]
        elif target == 20:
            rows = [[30, "other", "method", 20, "REFERENCES"]]
        else:
            rows = []
        return FakeResult(rows[: params["limit"]])


class EntityGraphConnection:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, _query, _params):
        return FakeResult(self.rows)


class GraphRemediationTests(unittest.TestCase):
    def test_entity_lookup_requires_repo_for_multiple_occurrences(self):
        graph = EntityGraphConnection(
            [
                [
                    1,
                    "Customer",
                    "entity",
                    "one",
                    10,
                    "ar",
                    "ARCustomer",
                    None,
                    "one/Customer.ent",
                ],
                [
                    1,
                    "Customer",
                    "entity",
                    "two",
                    20,
                    "co",
                    "COCustomer",
                    None,
                    "two/Customer.ent",
                ],
            ]
        )
        with self.assertRaises(EntityAmbiguityError) as caught:
            _query_entity_from_graph(graph, "Customer")
        self.assertEqual(
            caught.exception.candidates,
            [
                {
                    "repo_key": "one",
                    "occurrence_id": 10,
                    "ent_file": "one/Customer.ent",
                },
                {
                    "repo_key": "two",
                    "occurrence_id": 20,
                    "ent_file": "two/Customer.ent",
                },
            ],
        )

    def test_migration_matches_fresh_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            fresh = sqlite3.connect(Path(directory) / "fresh.db")
            migrated = sqlite3.connect(Path(directory) / "migrated.db")
            try:
                fresh.executescript((ROOT / "catalog/schema.sql").read_text())
                migrated.executescript((ROOT / "catalog/schema.sql").read_text())
                migrated.execute(
                    "DELETE FROM schema_migrations WHERE name='023_delta_refresh'"
                )
                migrated.execute("DROP TABLE graph_builds")
                migrated.executescript(
                    """
                    CREATE TABLE graph_builds (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        graph_path TEXT NOT NULL,
                        source_db TEXT NOT NULL,
                        status TEXT NOT NULL,
                        source_fingerprint TEXT,
                        started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        completed_at TEXT,
                        validation_summary TEXT,
                        error TEXT
                    );
                    INSERT INTO graph_builds(
                        graph_path, source_db, status, source_fingerprint
                    ) VALUES ('old.lbug', 'catalog.db', 'active', NULL);
                    """
                )
                migrated.commit()
                apply_delta_refresh_migration(migrated)
                self.assertEqual(schema_signature(fresh), schema_signature(migrated))
                row = migrated.execute(
                    "SELECT status, source_fingerprint,catalog_build_id FROM graph_builds"
                ).fetchone()
                self.assertEqual(row, ("active", "legacy-unknown", None))
            finally:
                fresh.close()
                migrated.close()

    def test_get_connection_does_not_create_graph_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "catalog.db"
            sqlite3.connect(db_path).close()
            conn = get_connection(str(db_path))
            try:
                tables = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
                self.assertEqual(tables, [])
                self.assertFalse(conn.in_transaction)
            finally:
                conn.close()

    def test_snapshot_is_stable_after_live_database_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            live_path = Path(directory) / "live.db"
            snapshot_path = Path(directory) / "snapshot.db"
            live = sqlite3.connect(live_path)
            live.execute("CREATE TABLE evidence(id INTEGER PRIMARY KEY, value TEXT)")
            live.execute("INSERT INTO evidence(value) VALUES ('before')")
            live.commit()
            live.close()

            create_sqlite_snapshot(str(live_path), snapshot_path)
            live = sqlite3.connect(live_path)
            live.execute("UPDATE evidence SET value='after'")
            live.commit()
            live.close()

            snapshot = sqlite3.connect(snapshot_path)
            try:
                self.assertEqual(
                    snapshot.execute("SELECT value FROM evidence").fetchone()[0],
                    "before",
                )
            finally:
                snapshot.close()

    def test_previous_graph_is_preserved_without_moving_active(self):
        with tempfile.TemporaryDirectory() as directory:
            active = Path(directory) / "graph.lbug"
            previous = Path(directory) / "graph.lbug.previous"
            active.write_bytes(b"active")
            previous.write_bytes(b"older")
            preserve_previous_graph(active, previous, "test")
            self.assertEqual(active.read_bytes(), b"active")
            self.assertEqual(previous.read_bytes(), b"active")

    def _create_migrated_catalog(self, path: Path):
        conn = sqlite3.connect(path)
        conn.executescript((ROOT / "catalog/schema.sql").read_text())
        conn.execute("DELETE FROM schema_migrations WHERE name='023_delta_refresh'")
        conn.execute("CREATE TABLE evidence(id INTEGER PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO evidence(value) VALUES ('snapshot')")
        conn.commit()
        apply_delta_refresh_migration(conn)
        conn.close()

    @mock.patch("validation.validate_graph.validate_paths")
    @mock.patch("scripts.build_graph.build_graph")
    def test_two_promotions_mark_one_active_and_one_previous(
        self, mocked_build, mocked_validate
    ):
        with tempfile.TemporaryDirectory() as directory:
            catalog = Path(directory) / "catalog.db"
            active = Path(directory) / "graph.lbug"
            active.write_bytes(b"old-active")
            self._create_migrated_catalog(catalog)
            candidate_contents = iter((b"candidate-one", b"candidate-two"))

            def write_candidate(_snapshot, candidate):
                Path(candidate).write_bytes(next(candidate_contents))

            mocked_build.side_effect = write_candidate
            mocked_validate.return_value = '{"exact_check_count": 52}'
            promote_validated_graph(str(catalog), str(active))
            promote_validated_graph(str(catalog), str(active))

            self.assertEqual(active.read_bytes(), b"candidate-two")
            self.assertEqual(
                active.with_name("graph.lbug.previous").read_bytes(),
                b"candidate-one",
            )
            conn = sqlite3.connect(catalog)
            try:
                statuses = conn.execute(
                    "SELECT status FROM graph_builds ORDER BY id"
                ).fetchall()
                self.assertEqual(statuses, [("previous",), ("active",)])
            finally:
                conn.close()
            self.assertEqual(list(Path(directory).glob("graph.lbug.candidate.*")), [])
            self.assertEqual(list(Path(directory).glob("graph.lbug.snapshot.*")), [])

    def test_policy_grant_count_deduplicates_source_rows(self):
        conn = sqlite3.connect(":memory:")
        try:
            conn.executescript(
                """
                CREATE TABLE security_policies(id INTEGER PRIMARY KEY, repo_id INTEGER);
                CREATE TABLE security_policy_values(id INTEGER PRIMARY KEY, policy_id INTEGER);
                CREATE TABLE security_policy_eops(policy_value_id INTEGER, op_key TEXT);
                CREATE TABLE security_operations(id INTEGER PRIMARY KEY, repo_id INTEGER, op_key TEXT);
                INSERT INTO security_policies VALUES (1, 10);
                INSERT INTO security_policy_values VALUES (1, 1);
                INSERT INTO security_policy_eops VALUES (1, 'read'), (1, 'read');
                INSERT INTO security_operations VALUES (10, 10, 'read');
                """
            )
            count = conn.execute(
                """
                SELECT COUNT(*) FROM (
                    SELECT DISTINCT spv.id, so.id
                    FROM security_policy_values spv
                    JOIN security_policies sp ON sp.id = spv.policy_id
                    JOIN security_policy_eops spe ON spe.policy_value_id = spv.id
                    JOIN security_operations so ON so.repo_id = sp.repo_id AND so.op_key = spe.op_key
                    WHERE (sp.repo_id, spe.op_key) IN (
                        SELECT repo_id, op_key FROM security_operations
                        GROUP BY repo_id, op_key HAVING COUNT(*) = 1
                    )
                )
                """
            ).fetchone()[0]
            self.assertEqual(count, 1)
        finally:
            conn.close()

    def test_policy_grants_are_unique_per_repository(self):
        conn = sqlite3.connect(":memory:")
        try:
            conn.executescript(
                """
                CREATE TABLE security_policies(id INTEGER PRIMARY KEY, repo_id INTEGER);
                CREATE TABLE security_policy_values(id INTEGER PRIMARY KEY, policy_id INTEGER);
                CREATE TABLE security_policy_eops(policy_value_id INTEGER, op_key TEXT);
                CREATE TABLE security_operations(id INTEGER PRIMARY KEY, repo_id INTEGER, op_key TEXT);
                INSERT INTO security_policies VALUES (1, 10), (2, 20);
                INSERT INTO security_policy_values VALUES (101, 1), (201, 2);
                INSERT INTO security_policy_eops VALUES (101, 'read'), (201, 'read');
                INSERT INTO security_operations VALUES (11, 10, 'read'), (22, 20, 'read');
                """
            )
            rows = conn.execute(
                """
                SELECT DISTINCT spv.id, so.id
                FROM security_policy_values spv
                JOIN security_policies sp ON sp.id = spv.policy_id
                JOIN security_policy_eops spe ON spe.policy_value_id = spv.id
                JOIN security_operations so ON so.repo_id = sp.repo_id AND so.op_key = spe.op_key
                WHERE (sp.repo_id, spe.op_key) IN (
                    SELECT repo_id, op_key FROM security_operations
                    GROUP BY repo_id, op_key HAVING COUNT(*) = 1
                )
                ORDER BY spv.id
                """
            ).fetchall()
            self.assertEqual(rows, [(101, 11), (201, 22)])
        finally:
            conn.close()

    def test_previous_graph_artifact_is_ignored(self):
        ignored = (ROOT / ".gitignore").read_text()
        self.assertIn("catalog/*.lbug.previous", ignored)

    @mock.patch("validation.validate_graph.validate_paths")
    @mock.patch("scripts.build_graph.build_graph")
    def test_validation_failure_leaves_active_unchanged(
        self, mocked_build, mocked_validate
    ):
        with tempfile.TemporaryDirectory() as directory:
            catalog = Path(directory) / "catalog.db"
            active = Path(directory) / "graph.lbug"
            active.write_bytes(b"old-active")
            self._create_migrated_catalog(catalog)

            mocked_build.side_effect = lambda _snapshot, candidate: Path(
                candidate
            ).write_bytes(b"bad")
            mocked_validate.side_effect = RuntimeError("parity mismatch")
            with self.assertRaisesRegex(RuntimeError, "parity mismatch"):
                promote_validated_graph(str(catalog), str(active))

            self.assertEqual(active.read_bytes(), b"old-active")
            self.assertFalse(active.with_name("graph.lbug.previous").exists())
            conn = sqlite3.connect(catalog)
            try:
                status, error = conn.execute(
                    "SELECT status,error FROM graph_builds ORDER BY id DESC LIMIT 1"
                ).fetchone()
                self.assertEqual(status, "failed")
                self.assertIn("parity mismatch", error)
            finally:
                conn.close()

    def test_concurrent_build_is_rejected_before_work_starts(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = Path(directory) / "catalog.db"
            active = Path(directory) / "graph.lbug"
            lock_path = active.with_name("graph.lbug.build.lock")
            self._create_migrated_catalog(catalog)
            with lock_path.open("a+b") as held_lock:
                fcntl.flock(held_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaisesRegex(RuntimeError, "another graph build"):
                    promote_validated_graph(str(catalog), str(active))
                fcntl.flock(held_lock.fileno(), fcntl.LOCK_UN)

    @mock.patch("validation.validate_graph.validate_paths")
    @mock.patch("scripts.build_graph.build_graph")
    def test_atomic_replace_failure_leaves_active_readable(
        self, mocked_build, mocked_validate
    ):
        with tempfile.TemporaryDirectory() as directory:
            catalog = Path(directory) / "catalog.db"
            active = Path(directory) / "graph.lbug"
            active.write_bytes(b"old-active")
            self._create_migrated_catalog(catalog)
            mocked_build.side_effect = lambda _snapshot, candidate: Path(
                candidate
            ).write_bytes(b"new")
            mocked_validate.return_value = '{"exact_check_count": 52}'
            real_replace = os.replace

            def fail_candidate_replace(source, destination):
                if (
                    ".candidate." in str(source)
                    and Path(destination).resolve() == active.resolve()
                ):
                    raise OSError("simulated atomic replace failure")
                return real_replace(source, destination)

            with (
                mock.patch(
                    "scripts.build_graph.os.replace", side_effect=fail_candidate_replace
                ),
                self.assertRaisesRegex(OSError, "simulated atomic"),
            ):
                promote_validated_graph(str(catalog), str(active))

            self.assertEqual(active.read_bytes(), b"old-active")
            self.assertFalse(active.with_name("graph.lbug.previous").exists())

    @mock.patch("validation.validate_graph.validate_paths")
    @mock.patch("scripts.build_graph.build_graph")
    def test_metadata_activation_failure_restores_graph_and_fails_build(
        self, mocked_build, mocked_validate
    ):
        with tempfile.TemporaryDirectory() as directory:
            catalog = Path(directory) / "catalog.db"
            active = Path(directory) / "graph.lbug"
            previous = active.with_name("graph.lbug.previous")
            active.write_bytes(b"old-active")
            previous.write_bytes(b"old-previous")
            self._create_migrated_catalog(catalog)
            conn = sqlite3.connect(catalog)
            try:
                conn.execute(
                    """INSERT INTO graph_builds(
                           graph_path,source_db,status,source_fingerprint,build_mode
                       ) VALUES (?,?, 'active',?,'full')""",
                    (str(active), str(catalog), "old-fingerprint"),
                )
                conn.commit()
            finally:
                conn.close()
            mocked_build.side_effect = lambda _snapshot, candidate: Path(
                candidate
            ).write_bytes(b"new-active")
            mocked_validate.return_value = '{"exact_check_count": 52}'

            with (
                mock.patch(
                    "scripts.build_graph._activate_graph_metadata",
                    side_effect=sqlite3.OperationalError("activation failed"),
                ),
                self.assertRaisesRegex(sqlite3.OperationalError, "activation failed"),
            ):
                promote_validated_graph(str(catalog), str(active))

            self.assertEqual(active.read_bytes(), b"old-active")
            self.assertEqual(previous.read_bytes(), b"old-previous")
            conn = sqlite3.connect(catalog)
            try:
                statuses = conn.execute(
                    "SELECT status,error FROM graph_builds ORDER BY id"
                ).fetchall()
                self.assertEqual(statuses[0], ("active", None))
                self.assertEqual(statuses[1][0], "failed")
                self.assertIn("activation failed", statuses[1][1])
            finally:
                conn.close()

    def test_traversal_uses_global_budget_and_minimum_depth(self):
        graph = FakeGraphConnection()
        nodes, edges = _query_bounded_incoming_traversal(graph, [10], 2, 1)
        self.assertEqual(len(graph.calls), 2)
        self.assertTrue(all(call[1]["limit"] == 1 for call in graph.calls))
        self.assertEqual(len(edges), 2)
        self.assertEqual(
            nodes,
            [
                {
                    "symbol_id": 20,
                    "name": "consumer",
                    "kind": "method",
                    "depth": 1,
                    "is_seed": False,
                },
                {
                    "symbol_id": 30,
                    "name": "other",
                    "kind": "method",
                    "depth": 2,
                    "is_seed": False,
                },
            ],
        )


if __name__ == "__main__":
    unittest.main()
