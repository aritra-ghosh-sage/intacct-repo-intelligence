"""Focused migration-contract tests for repository archival foundation."""

from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path

from catalog.content_fingerprint import logical_content_fingerprint
from catalog.migrations import (
    REPOSITORY_ARCHIVAL_MIGRATION,
    apply_delta_refresh_migration,
    apply_multi_repo_migration,
)
from catalog.source_revisions import active_source_revisions

ROOT = Path(__file__).resolve().parents[1]


class RepositoryArchivalMigrationTests(unittest.TestCase):
    def _connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript((ROOT / "catalog" / "schema.sql").read_text())
        return conn

    def _legacy_compatibility_fixture(
        self, *, owners: tuple[int, ...]
    ) -> sqlite3.Connection:
        conn = self._connection()
        conn.execute(
            "DELETE FROM schema_migrations WHERE name=?", (REPOSITORY_ARCHIVAL_MIGRATION,)
        )
        for column in ("archived_at", "archive_reason", "archive_source", "lifecycle_state"):
            conn.execute(f"ALTER TABLE repos DROP COLUMN {column}")
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("PRAGMA legacy_alter_table=ON")
        conn.execute(
            "ALTER TABLE api_version_compatibility "
            "RENAME TO api_version_compatibility_current"
        )
        conn.execute(
            """CREATE TABLE api_version_compatibility (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   test_version TEXT NOT NULL,
                   endpoint_version TEXT NOT NULL,
                   status TEXT NOT NULL DEFAULT 'active'
                       CHECK(status IN ('active', 'deprecated', 'disabled')),
                   rationale TEXT NOT NULL,
                   evidence TEXT NOT NULL,
                   created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                   UNIQUE(test_version, endpoint_version)
               )"""
        )
        # SQLite rewrites child FK targets during the parent rename on this
        # runtime, even with legacy_alter_table enabled.  Recreate the empty
        # child so this fixture is a faithful pre-029 schema.
        conn.execute("DROP TABLE test_endpoint_links")
        conn.execute("DROP TABLE api_version_compatibility_current")
        conn.execute(
            """CREATE TABLE test_endpoint_links (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   test_request_id INTEGER NOT NULL,
                   rest_endpoint_id INTEGER NOT NULL,
                   compatibility_id INTEGER,
                   resolution_kind TEXT NOT NULL
                       CHECK(resolution_kind IN ('exact_version', 'compatible_version')),
                   FOREIGN KEY(test_request_id) REFERENCES test_requests(id) ON DELETE CASCADE,
                   FOREIGN KEY(rest_endpoint_id) REFERENCES rest_endpoints(id) ON DELETE CASCADE,
                   FOREIGN KEY(compatibility_id) REFERENCES api_version_compatibility(id) ON DELETE SET NULL,
                   UNIQUE(test_request_id, rest_endpoint_id)
               )"""
        )
        conn.execute(
            """INSERT INTO api_version_compatibility(
                   id,test_version,endpoint_version,status,rationale,evidence,created_at
               ) VALUES (77,'v1-beta2','s1','active','fixture','{}','2000-01-01')"""
        )
        for repo_id in owners:
            conn.execute(
                "INSERT INTO repos(id,repo_key,local_root,tracked_branch) VALUES(?,?,?, 'main')",
                (repo_id, f"suite-{repo_id}", f"/tmp/suite-{repo_id}"),
            )
            file_id = conn.execute(
                "INSERT INTO files(repo_id,path) VALUES(?,?)",
                (repo_id, f"feature-{repo_id}.feature"),
            ).lastrowid
            case_id = conn.execute(
                """INSERT INTO test_cases(
                       repo_id,file_id,feature_name,scenario_name,case_name,
                       feature_line,scenario_line
                   ) VALUES(?,?, 'Feature','Scenario','Case',1,2)""",
                (repo_id, file_id),
            ).lastrowid
            request_id = conn.execute(
                "INSERT INTO test_requests(test_case_id,ordinal,step_line) VALUES(?,?,?)",
                (case_id, 1, 3),
            ).lastrowid
            endpoint_file_id = conn.execute(
                "INSERT INTO files(repo_id,path) VALUES(?,?)",
                (repo_id, f"endpoint-{repo_id}.yaml"),
            ).lastrowid
            entity_id = conn.execute(
                "INSERT INTO entity_nodes(name) VALUES(?)", (f"Entity{repo_id}",)
            ).lastrowid
            endpoint_id = conn.execute(
                """INSERT INTO rest_endpoints(repo_id,method,path,entity_id,file_id)
                   VALUES(?, 'GET', ?, ?, ?)""",
                (repo_id, f"/objects/{repo_id}", entity_id, endpoint_file_id),
            ).lastrowid
            conn.execute(
                """INSERT INTO test_endpoint_links(
                       test_request_id,rest_endpoint_id,compatibility_id,resolution_kind
                   ) VALUES(?,?,77,'compatible_version')""",
                (request_id, endpoint_id),
            )
        conn.execute("PRAGMA legacy_alter_table=OFF")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.commit()
        return conn

    def test_fresh_schema_markers_converge_without_028(self) -> None:
        conn = self._connection()
        self.addCleanup(conn.close)
        markers = [
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM schema_migrations "
                "WHERE name GLOB '0[1-2][0-9]_*' ORDER BY name"
            )
        ]
        self.assertEqual(
            [
                "019_multi_repo",
                "020_rest_automation_coverage",
                "021_entity_semantics",
                "022_entity_semantics_repo_scope",
                "023_delta_refresh",
                "024_refresh_contracts",
                "025_delta_refresh_hardening",
                "026_ui_catalog",
                "027_ui_negative_event_calls",
                "028_api_registry",
                "029_repository_archival",
            ],
            markers,
        )

    def test_migrates_compatibility_with_one_proven_owner_and_preserves_links(self) -> None:
        conn = self._legacy_compatibility_fixture(owners=(1,))
        self.addCleanup(conn.close)

        apply_delta_refresh_migration(conn)

        row = conn.execute(
            "SELECT id,repo_id,test_version,endpoint_version FROM api_version_compatibility"
        ).fetchone()
        self.assertEqual((77, 1, "v1-beta2", "s1"), tuple(row))
        self.assertEqual(
            77,
            conn.execute("SELECT compatibility_id FROM test_endpoint_links").fetchone()[0],
        )
        self.assertEqual(
            "api_version_compatibility",
            conn.execute("PRAGMA foreign_key_list(test_endpoint_links)").fetchone()[2],
        )
        self.assertIn("repo_id", {row[1] for row in conn.execute("PRAGMA table_info(api_version_compatibility)")})
        self.assertEqual(
            1,
            conn.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE name=?",
                (REPOSITORY_ARCHIVAL_MIGRATION,),
            ).fetchone()[0],
        )
        conn.execute(
            """INSERT INTO catalog_builds(
                   build_token,catalog_path,requested_mode,effective_mode,status,
                   source_revisions_json,delta_contract_version
               ) VALUES('archive-fixture',':memory:','archive','archive','failed','{}',3)"""
        )

    def test_multi_repo_runner_applies_029_without_an_028_marker(self) -> None:
        conn = self._connection()
        self.addCleanup(conn.close)
        conn.execute(
            "DELETE FROM schema_migrations WHERE name=?", (REPOSITORY_ARCHIVAL_MIGRATION,)
        )
        conn.commit()

        apply_multi_repo_migration(conn, local_root="/tmp/main")

        self.assertEqual(
            1,
            conn.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE name=?",
                (REPOSITORY_ARCHIVAL_MIGRATION,),
            ).fetchone()[0],
        )

    def test_ambiguous_compatibility_owner_rolls_back_atomically(self) -> None:
        conn = self._legacy_compatibility_fixture(owners=(1, 2))
        self.addCleanup(conn.close)

        with self.assertRaisesRegex(RuntimeError, "exactly one proven repository owner"):
            apply_delta_refresh_migration(conn)

        self.assertNotIn(
            "lifecycle_state", {row[1] for row in conn.execute("PRAGMA table_info(repos)")}
        )
        self.assertNotIn(
            "repo_id",
            {row[1] for row in conn.execute("PRAGMA table_info(api_version_compatibility)")},
        )
        self.assertEqual(
            0,
            conn.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE name=?",
                (REPOSITORY_ARCHIVAL_MIGRATION,),
            ).fetchone()[0],
        )

    def test_unlinked_compatibility_owner_rolls_back_atomically(self) -> None:
        conn = self._legacy_compatibility_fixture(owners=())
        self.addCleanup(conn.close)

        with self.assertRaisesRegex(RuntimeError, "exactly one proven repository owner"):
            apply_delta_refresh_migration(conn)

        self.assertNotIn(
            "repo_id",
            {row[1] for row in conn.execute("PRAGMA table_info(api_version_compatibility)")},
        )
        self.assertEqual(
            0,
            conn.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE name=?",
                (REPOSITORY_ARCHIVAL_MIGRATION,),
            ).fetchone()[0],
        )

    def test_lifecycle_is_fingerprinted_but_archival_timestamp_is_not(self) -> None:
        conn = self._connection()
        self.addCleanup(conn.close)
        conn.execute(
            "INSERT INTO repos(repo_key,local_root,tracked_branch) VALUES('active','/tmp/a','main')"
        )
        before = logical_content_fingerprint(conn)
        conn.execute(
            "UPDATE repos SET lifecycle_state='archived',archive_source='manual',archive_reason='retired' "
            "WHERE repo_key='active'"
        )
        archived = logical_content_fingerprint(conn)
        conn.execute(
            "UPDATE repos SET archived_at='2026-08-03T00:00:00Z' WHERE repo_key='active'"
        )
        self.assertNotEqual(before, archived)
        self.assertEqual(archived, logical_content_fingerprint(conn))

    def test_active_source_revisions_exclude_archived_repositories(self) -> None:
        conn = self._connection()
        self.addCleanup(conn.close)
        conn.executemany(
            """INSERT INTO repos(repo_key,local_root,tracked_branch,indexed_commit_sha,lifecycle_state)
               VALUES(?,?,?,?,?)""",
            [
                ("active", "/tmp/a", "main", "active-sha", "active"),
                ("archived", "/tmp/b", "main", "old-sha", "archived"),
            ],
        )
        self.assertEqual({"active": "active-sha"}, active_source_revisions(conn))


if __name__ == "__main__":
    unittest.main()
