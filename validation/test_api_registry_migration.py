"""Focused schema and migration coverage for Registry evidence tables."""

from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path

from catalog.content_fingerprint import (
    AUTHORITATIVE_EVIDENCE_TABLES,
    CATALOG_CONTENT_VERSION,
    logical_content_fingerprint,
)
from catalog.migrations import API_REGISTRY_MIGRATION, apply_delta_refresh_migration

ROOT = Path(__file__).resolve().parents[1]


class ApiRegistryMigrationTests(unittest.TestCase):
    def _connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript((ROOT / "catalog/schema.sql").read_text())
        return conn

    def _seed_sources(self, conn: sqlite3.Connection) -> tuple[int, int, int, int]:
        main_repo_id = int(
            conn.execute(
                "INSERT INTO repos(repo_key,local_root,tracked_branch) VALUES "
                "('ia-main','/tmp/main','main')"
            ).lastrowid
        )
        other_repo_id = int(
            conn.execute(
                "INSERT INTO repos(repo_key,local_root,tracked_branch) VALUES "
                "('other','/tmp/other','main')"
            ).lastrowid
        )
        registry_file_id = int(
            conn.execute(
                "INSERT INTO files(repo_id,path) VALUES (?,?)",
                (main_repo_id, "app/source/api/registries/RegistryV1.json"),
            ).lastrowid
        )
        component_file_id = int(
            conn.execute(
                "INSERT INTO files(repo_id,path) VALUES (?,?)",
                (main_repo_id, "app/source/openapispec/ap/bill.s1.schema.yaml"),
            ).lastrowid
        )
        other_file_id = int(
            conn.execute(
                "INSERT INTO files(repo_id,path) VALUES (?,?)",
                (other_repo_id, "app/source/api/registries/RegistryV1.json"),
            ).lastrowid
        )
        return main_repo_id, registry_file_id, component_file_id, other_file_id

    @staticmethod
    def _insert_entry(conn: sqlite3.Connection, repo_id: int, registry_file_id: int) -> int:
        return int(
            conn.execute(
                """INSERT INTO api_registry_entries(
                       repo_id,registry_release,registry_file_id,json_pointer,module,
                       resource_kind,resource_path,revision,declared_hash,api_type,
                       payload_json
                   ) VALUES (?,'V1',?,'/accounts-payable/objects/bill',
                       'accounts-payable','objects','bill','s1','abc','rootObject','{}')""",
                (repo_id, registry_file_id),
            ).lastrowid
        )

    def test_fresh_schema_registers_all_028_evidence_tables(self) -> None:
        conn = self._connection()
        self.addCleanup(conn.close)
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        self.assertTrue(
            {
                "api_registry_entries",
                "api_registry_entry_links",
                "api_registry_issues",
                "ui_source_diagnostics",
            }.issubset(tables)
        )
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE name=?",
                (API_REGISTRY_MIGRATION,),
            ).fetchone()[0],
            1,
        )
        fingerprint_tables = {table.name for table in AUTHORITATIVE_EVIDENCE_TABLES}
        self.assertTrue(
            {
                "api_registry_entries",
                "api_registry_entry_links",
                "api_registry_issues",
                "ui_source_diagnostics",
            }.issubset(fingerprint_tables)
        )
        self.assertEqual(CATALOG_CONTENT_VERSION, 4)

    def test_upgrade_recreates_028_tables_idempotently(self) -> None:
        conn = self._connection()
        self.addCleanup(conn.close)
        for table in (
            "api_registry_issues",
            "api_registry_entry_links",
            "api_registry_entries",
            "ui_source_diagnostics",
        ):
            conn.execute(f"DROP TABLE {table}")
        conn.execute(
            "DELETE FROM schema_migrations WHERE name=?", (API_REGISTRY_MIGRATION,)
        )
        conn.commit()

        apply_delta_refresh_migration(conn)
        apply_delta_refresh_migration(conn)

        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE name=?",
                (API_REGISTRY_MIGRATION,),
            ).fetchone()[0],
            1,
        )
        self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_composite_file_and_entry_keys_reject_cross_repository_rows(self) -> None:
        conn = self._connection()
        self.addCleanup(conn.close)
        main_repo_id, registry_file_id, component_file_id, other_file_id = self._seed_sources(conn)
        entry_id = self._insert_entry(conn, main_repo_id, registry_file_id)

        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                """INSERT INTO api_registry_entries(
                       repo_id,registry_release,registry_file_id,json_pointer,module,
                       resource_kind,resource_path,payload_json
                   ) VALUES (?,'V1',?,'/bad','module','objects','bad','{}')""",
                (main_repo_id, other_file_id),
            )
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                """INSERT INTO api_registry_entry_links(
                       repo_id,entry_id,source_file_id,source_pointer,link_kind
                   ) VALUES (?,?,?,?, 'schema')""",
                (main_repo_id, entry_id, other_file_id, "/components/schemas/Bill"),
            )
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                """INSERT INTO api_registry_issues(
                       repo_id,entry_id,source_file_id,source_pointer,issue_key,
                       severity,issue_code,message
                   ) VALUES (?,?,?,?,?,'warning','missing_component','fixture')""",
                (
                    main_repo_id,
                    entry_id,
                    other_file_id,
                    "/accounts-payable/objects/bill",
                    "fixture:issue",
                ),
            )
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                """INSERT INTO ui_source_diagnostics(
                       repo_id,source_file_id,source_path,source_kind,diagnostic_key,
                       severity,diagnostic_code,message
                   ) VALUES (?,?,?,'uimeta','fixture:ui','warning','unattached','fixture')""",
                (main_repo_id, other_file_id, "app/source/openapispec/bad.uimeta.yaml"),
            )

        conn.execute(
            """INSERT INTO api_registry_entry_links(
                   repo_id,entry_id,source_file_id,source_pointer,link_kind
               ) VALUES (?,?,?,?, 'schema')""",
            (main_repo_id, entry_id, component_file_id, "/components/schemas/Bill"),
        )
        self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_each_new_evidence_family_changes_the_logical_fingerprint(self) -> None:
        conn = self._connection()
        self.addCleanup(conn.close)
        main_repo_id, registry_file_id, component_file_id, _ = self._seed_sources(conn)
        baseline = logical_content_fingerprint(conn)
        entry_id = self._insert_entry(conn, main_repo_id, registry_file_id)
        after_entry = logical_content_fingerprint(conn)
        self.assertNotEqual(baseline, after_entry)
        conn.execute(
            """INSERT INTO api_registry_entry_links(
                   repo_id,entry_id,source_file_id,source_pointer,link_kind
               ) VALUES (?,?,?,?, 'schema')""",
            (main_repo_id, entry_id, component_file_id, "/components/schemas/Bill"),
        )
        after_link = logical_content_fingerprint(conn)
        self.assertNotEqual(after_entry, after_link)
        conn.execute(
            """INSERT INTO api_registry_issues(
                   repo_id,entry_id,source_file_id,source_pointer,issue_key,
                   severity,issue_code,message
               ) VALUES (?,?,?,?,?,'warning','fixture','fixture')""",
            (
                main_repo_id,
                entry_id,
                registry_file_id,
                "/accounts-payable/objects/bill",
                "fixture:issue",
            ),
        )
        after_issue = logical_content_fingerprint(conn)
        self.assertNotEqual(after_link, after_issue)
        conn.execute(
            """INSERT INTO ui_source_diagnostics(
                   repo_id,source_file_id,source_path,source_kind,diagnostic_key,
                   severity,diagnostic_code,message
               ) VALUES (?,?,?,'uimeta','fixture:ui','warning','unattached','fixture')""",
            (
                main_repo_id,
                component_file_id,
                "app/source/openapispec/ap/bill.s1.uimeta.yaml",
            ),
        )
        self.assertNotEqual(after_issue, logical_content_fingerprint(conn))


if __name__ == "__main__":
    unittest.main()
