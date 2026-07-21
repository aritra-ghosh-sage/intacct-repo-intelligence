"""Focused regression tests for repository registry and 019 migration."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from catalog.migrations import apply_multi_repo_migration
from catalog.repositories import (
    RepositoryError,
    get_repository,
    load_workspace_manifest,
    register_manifest,
    resolve_repository_root,
)


class MultiRepoMigrationTests(unittest.TestCase):
    def legacy_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(
            """
            CREATE TABLE files (id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT UNIQUE NOT NULL, language TEXT);
            CREATE TABLE symbols (id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL,
                FOREIGN KEY(file_id) REFERENCES files(id));
            CREATE TABLE relationships (id INTEGER PRIMARY KEY, file_id INTEGER);
            CREATE TABLE repos (id INTEGER PRIMARY KEY, name TEXT, kind TEXT, language TEXT);
            INSERT INTO files(id, path, language) VALUES (7, 'app/source/Foo.cls', 'php');
            INSERT INTO symbols(id, file_id) VALUES (11, 7);
            """
        )
        conn.commit()
        return conn

    def test_migration_preserves_file_ids_and_allows_colliding_paths(self) -> None:
        conn = self.legacy_connection()
        apply_multi_repo_migration(conn, local_root="/tmp/main")
        main = get_repository(conn, "ia-main")
        self.assertEqual(tuple(conn.execute("SELECT id, repo_id FROM files").fetchone()), (7, main["id"]))
        second_id = conn.execute(
            "INSERT INTO repos(repo_key, local_root, tracked_branch) VALUES ('service', '/tmp/service', 'main')"
        ).lastrowid
        conn.execute("INSERT INTO files(repo_id, path) VALUES (?, ?)", (second_id, "app/source/Foo.cls"))
        self.assertEqual(conn.execute("SELECT file_id FROM symbols WHERE id = 11").fetchone()[0], 7)
        self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_migration_is_idempotent(self) -> None:
        conn = self.legacy_connection()
        apply_multi_repo_migration(conn, local_root="/tmp/main")
        apply_multi_repo_migration(conn, local_root="/tmp/main")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM schema_migrations WHERE name = '019_multi_repo'").fetchone()[0], 1)

    def test_migration_makes_workflow_identity_repo_qualified(self) -> None:
        conn = self.legacy_connection()
        conn.executescript(
            """
            CREATE TABLE entity_nodes (id INTEGER PRIMARY KEY, name TEXT);
            INSERT INTO entity_nodes(id, name) VALUES (1, 'Invoice');
            CREATE TABLE workflows (
                id INTEGER PRIMARY KEY, entity_id INTEGER NOT NULL, name TEXT NOT NULL,
                workflow_type TEXT NOT NULL, source_kind TEXT NOT NULL, source_file TEXT,
                file_id INTEGER, source_symbol_id INTEGER, confidence REAL, reason TEXT,
                created_at TEXT,
                UNIQUE(entity_id, name, workflow_type, source_file)
            );
            INSERT INTO workflows(id, entity_id, name, workflow_type, source_kind, source_file)
                VALUES (31, 1, 'post', 'posting', 'yaml', 'app/source/workflows.yml');
            """
        )
        conn.commit()
        apply_multi_repo_migration(conn, local_root="/tmp/main")
        second_id = conn.execute(
            "INSERT INTO repos(repo_key, local_root, tracked_branch) VALUES ('service', '/tmp/service', 'main')"
        ).lastrowid
        conn.execute(
            """INSERT INTO workflows(repo_id, entity_id, name, workflow_type, source_kind, source_file)
               VALUES (?, 1, 'post', 'posting', 'yaml', 'app/source/workflows.yml')""",
            (second_id,),
        )
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM workflows").fetchone()[0], 2)

    def test_migration_rejects_populated_unsafe_legacy_unique_table(self) -> None:
        conn = self.legacy_connection()
        conn.execute("INSERT INTO relationships(id, file_id) VALUES (1, 7)")
        conn.commit()
        with self.assertRaisesRegex(RuntimeError, "relationships"):
            apply_multi_repo_migration(conn, local_root="/tmp/main")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM files").fetchone()[0], 1)

    def test_empty_legacy_families_receive_repo_qualified_constraints(self) -> None:
        conn = self.legacy_connection()
        conn.executescript(
            """
            CREATE TABLE security_operations (
                id INTEGER PRIMARY KEY, op_key TEXT NOT NULL UNIQUE,
                source_file TEXT NOT NULL, source_kind TEXT NOT NULL
            );
            CREATE TABLE openapispec_index (
                id INTEGER PRIMARY KEY, file_path TEXT NOT NULL UNIQUE
            );
            CREATE TABLE entity_nodes (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL);
            CREATE TABLE entity_access_links (
                id INTEGER PRIMARY KEY, entity_id INTEGER NOT NULL, surface TEXT NOT NULL,
                record_id INTEGER NOT NULL, link_type TEXT NOT NULL,
                UNIQUE(entity_id, surface, record_id, link_type)
            );
            """
        )
        conn.commit()
        apply_multi_repo_migration(conn, local_root="/tmp/main")
        second_id = conn.execute(
            "INSERT INTO repos(repo_key, local_root, tracked_branch) VALUES ('service', '/tmp/service', 'main')"
        ).lastrowid
        main_id = get_repository(conn, "ia-main")["id"]
        for repo_id in (main_id, second_id):
            conn.execute(
                """INSERT INTO security_operations(repo_id, op_key, op_numeric_id, source_file, source_kind)
                   VALUES (?, 'same.op', 1, 'app/security.xml', 'xml')""",
                (repo_id,),
            )
            conn.execute(
                """INSERT INTO openapispec_index(repo_id, file_path)
                   VALUES (?, 'openapi/shared.yaml')""",
                (repo_id,),
            )
        entity_id = conn.execute("INSERT INTO entity_nodes(name) VALUES ('Invoice')").lastrowid
        for repo_id in (main_id, second_id):
            conn.execute(
                """INSERT INTO entity_access_links(repo_id, entity_id, surface, record_id, link_type)
                   VALUES (?, ?, 'security_operation', 1, 'exact')""",
                (repo_id, entity_id),
            )
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM security_operations").fetchone()[0], 2)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM openapispec_index").fetchone()[0], 2)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM entity_access_links").fetchone()[0], 2)

    def test_manifest_registration_and_root_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "checkout"
            root.mkdir()
            manifest_path = Path(directory) / "repos.yaml"
            manifest_path.write_text(
                "version: 1\nrepositories:\n  - repo_key: service\n    local_root: "
                f"{root}\n    tracked_branch: main\n    builders: [scan, symbols]\n"
            )
            manifest = load_workspace_manifest(manifest_path)
            conn = sqlite3.connect(":memory:")
            conn.row_factory = sqlite3.Row
            conn.executescript(
                """CREATE TABLE repos (id INTEGER PRIMARY KEY, repo_key TEXT UNIQUE,
                name TEXT, kind TEXT, language TEXT, remote_url TEXT, local_root TEXT,
                tracked_branch TEXT, enabled INTEGER, profile TEXT, effective_builders_json TEXT)"""
            )
            register_manifest(conn, manifest)
            self.assertEqual(resolve_repository_root(conn, "service"), root.resolve())
            with self.assertRaises(RepositoryError):
                get_repository(conn, "missing")


if __name__ == "__main__":
    unittest.main()
