"""Focused migration and ownership tests for the UI evidence catalog."""

from __future__ import annotations

import sqlite3
import unittest

from catalog.migrations import apply_delta_refresh_migration, apply_multi_repo_migration


class UiCatalogMigrationTests(unittest.TestCase):
    def _legacy_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(
            """
            CREATE TABLE files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT UNIQUE NOT NULL,
                language TEXT
            );
            CREATE TABLE symbols (
                id INTEGER PRIMARY KEY,
                file_id INTEGER NOT NULL,
                FOREIGN KEY(file_id) REFERENCES files(id)
            );
            CREATE TABLE relationships (id INTEGER PRIMARY KEY, file_id INTEGER);
            CREATE TABLE repos (id INTEGER PRIMARY KEY, name TEXT, kind TEXT, language TEXT);
            INSERT INTO files(id,path,language) VALUES (7,'app/source/Main.cls','php');
            INSERT INTO symbols(id,file_id) VALUES (11,7);
            """
        )
        conn.commit()
        return conn

    def _migrated_connection(self) -> tuple[sqlite3.Connection, int, int]:
        conn = self._legacy_connection()
        apply_multi_repo_migration(conn, local_root="/tmp/main")
        main_repo_id = int(
            conn.execute("SELECT id FROM repos WHERE repo_key='ia-main'").fetchone()[0]
        )
        other_repo_id = int(
            conn.execute(
                "INSERT INTO repos(repo_key,local_root,tracked_branch) "
                "VALUES ('other','/tmp/other','main')"
            ).lastrowid
        )
        conn.commit()
        return conn, main_repo_id, other_repo_id

    def _surface_and_artifact(self, conn: sqlite3.Connection, repo_id: int, file_id: int, key: str) -> tuple[int, int]:
        surface_id = int(
            conn.execute(
                """INSERT INTO ui_surfaces(
                       repo_id,surface_key,surface_kind,source_file_id,extractor,extractor_version
                   ) VALUES (?,?,'actionui_form',?,'test','1')""",
                (repo_id, key, file_id),
            ).lastrowid
        )
        artifact_id = int(
            conn.execute(
                """INSERT INTO ui_artifacts(
                       repo_id,surface_id,artifact_key,artifact_kind,file_id,source_path
                   ) VALUES (?,?,?,'actionui_form',?,?)""",
                (repo_id, surface_id, f"{key}:form", file_id, f"{key}.xml"),
            ).lastrowid
        )
        return surface_id, artifact_id

    def test_migration_is_idempotent_and_installs_all_ui_objects(self) -> None:
        conn, _, _ = self._migrated_connection()
        self.addCleanup(conn.close)
        apply_multi_repo_migration(conn, local_root="/tmp/main")

        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        self.assertTrue(
            {
                "ui_surfaces",
                "ui_artifacts",
                "ui_entity_references",
                "ui_artifact_includes",
                "ui_fields",
                "ui_events",
                "ui_script_dependencies",
                "ui_event_calls",
                "ui_resolution_issues",
            }.issubset(tables)
        )
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE name='026_ui_catalog'"
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE name='027_ui_negative_event_calls'"
            ).fetchone()[0],
            1,
        )
        indexes = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        self.assertTrue(
            {"uq_files_id_repo", "uq_ui_surfaces_id_repo", "uq_ui_artifacts_id_repo"}.issubset(indexes)
        )
        triggers = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
        }
        self.assertTrue(
            {
                "trg_ui_entity_references_entity_occurrence_insert",
                "trg_ui_event_calls_symbol_repo_insert",
                "trg_ui_event_calls_surface_match_insert",
            }.issubset(triggers)
        )
        self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_negative_event_call_migration_preserves_existing_ids(self) -> None:
        conn, main_repo_id, _ = self._migrated_connection()
        self.addCleanup(conn.close)
        for trigger in (
            "trg_ui_event_calls_symbol_repo_insert",
            "trg_ui_event_calls_symbol_repo_update",
            "trg_ui_event_calls_surface_match_insert",
            "trg_ui_event_calls_surface_match_update",
        ):
            conn.execute(f"DROP TRIGGER {trigger}")
        conn.execute("DROP INDEX idx_ui_event_calls_event")
        conn.execute("DROP INDEX idx_ui_event_calls_handler")
        conn.execute("DROP INDEX uq_ui_event_calls_without_dependency")
        conn.execute("DROP TABLE ui_event_calls")
        conn.execute(
            """CREATE TABLE ui_event_calls (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   repo_id INTEGER NOT NULL,
                   event_id INTEGER NOT NULL,
                   dependency_id INTEGER NOT NULL,
                   call_key TEXT NOT NULL,
                   handler_name TEXT NOT NULL,
                   handler_symbol_id INTEGER,
                   resolution_status TEXT NOT NULL,
                   resolution_reason TEXT NOT NULL,
                   evidence_text TEXT NOT NULL,
                   created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                   UNIQUE(repo_id,event_id,dependency_id,call_key)
               )"""
        )
        file_id = 7
        surface_id, artifact_id = self._surface_and_artifact(
            conn, main_repo_id, file_id, "actionui:negative"
        )
        event_id = int(
            conn.execute(
                """INSERT INTO ui_events(repo_id,artifact_id,event_key,event_type,evidence_text)
                   VALUES (?,?, 'event:1','load','fixture')""",
                (main_repo_id, artifact_id),
            ).lastrowid
        )
        dependency_id = int(
            conn.execute(
                """INSERT INTO ui_script_dependencies(
                       repo_id,surface_id,source_artifact_id,dependency_key,load_scope,
                       resolution_status,evidence_text
                   ) VALUES (?,?,?,'dependency:1','active','unresolved','fixture')""",
                (main_repo_id, surface_id, artifact_id),
            ).lastrowid
        )
        existing_id = int(
            conn.execute(
                """INSERT INTO ui_event_calls(
                       repo_id,event_id,dependency_id,call_key,handler_name,
                       resolution_status,resolution_reason,evidence_text
                   ) VALUES (?,?,?,'call:existing','handler','unresolved','fixture','fixture')""",
                (main_repo_id, event_id, dependency_id),
            ).lastrowid
        )
        conn.execute("DELETE FROM schema_migrations WHERE name='027_ui_negative_event_calls'")
        conn.commit()

        apply_multi_repo_migration(conn, local_root="/tmp/main")

        self.assertEqual(
            conn.execute("SELECT id FROM ui_event_calls WHERE call_key='call:existing'").fetchone()[0],
            existing_id,
        )
        self.assertEqual(
            conn.execute("PRAGMA table_info(ui_event_calls)").fetchall()[3][3],
            0,
        )
        conn.execute(
            """INSERT INTO ui_event_calls(
                   repo_id,event_id,dependency_id,call_key,handler_name,
                   resolution_status,resolution_reason,evidence_text
               ) VALUES (?,?,NULL,'call:negative','handler','unresolved','fixture','fixture')""",
            (main_repo_id, event_id),
        )
        self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_composite_file_foreign_keys_reject_cross_repo_artifacts(self) -> None:
        conn, main_repo_id, other_repo_id = self._migrated_connection()
        self.addCleanup(conn.close)
        other_file_id = int(
            conn.execute(
                "INSERT INTO files(repo_id,path,language) VALUES (?,?,'php')",
                (other_repo_id, "app/source/Other.cls"),
            ).lastrowid
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self._surface_and_artifact(conn, main_repo_id, other_file_id, "actionui:bad")
        conn.rollback()

        main_file_id = 7
        surface_id, _ = self._surface_and_artifact(
            conn, main_repo_id, main_file_id, "actionui:main"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                """INSERT INTO ui_artifacts(
                       repo_id,surface_id,artifact_key,artifact_kind,file_id,source_path
                   ) VALUES (?,?,'wrong-file','javascript',?,'app/resources/other.js')""",
                (main_repo_id, surface_id, other_file_id),
            )
        conn.rollback()

    def test_entity_and_symbol_ownership_triggers_reject_mismatches(self) -> None:
        conn, main_repo_id, other_repo_id = self._migrated_connection()
        self.addCleanup(conn.close)
        other_file_id = int(
            conn.execute(
                "INSERT INTO files(repo_id,path,language) VALUES (?,?,'javascript')",
                (other_repo_id, "app/resources/other.js"),
            ).lastrowid
        )
        other_symbol_id = int(
            conn.execute(
                "INSERT INTO symbols(file_id) VALUES (?)",
                (other_file_id,),
            ).lastrowid
        )
        surface_id, artifact_id = self._surface_and_artifact(
            conn, main_repo_id, 7, "actionui:main"
        )
        first_entity_id = int(
            conn.execute("INSERT INTO entity_nodes(name) VALUES ('First')").lastrowid
        )
        second_entity_id = int(
            conn.execute("INSERT INTO entity_nodes(name) VALUES ('Second')").lastrowid
        )
        first_occurrence_id = int(
            conn.execute(
                "INSERT INTO entity_occurrences(repo_id,entity_id) VALUES (?,?)",
                (main_repo_id, first_entity_id),
            ).lastrowid
        )
        second_occurrence_id = int(
            conn.execute(
                "INSERT INTO entity_occurrences(repo_id,entity_id) VALUES (?,?)",
                (main_repo_id, second_entity_id),
            ).lastrowid
        )
        conn.commit()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "same repository and entity"):
            conn.execute(
                """INSERT INTO ui_entity_references(
                       repo_id,surface_id,entity_id,entity_occurrence_id,evidence_artifact_id,
                       reference_kind,confidence,evidence_text
                   ) VALUES (?,?,?,?,?,'direct',1.0,'fixture')""",
                (
                    main_repo_id,
                    surface_id,
                    first_entity_id,
                    second_occurrence_id,
                    artifact_id,
                ),
            )
        conn.rollback()

        event_id = int(
            conn.execute(
                """INSERT INTO ui_events(
                       repo_id,artifact_id,event_key,event_type,handler_name,evidence_text
                   ) VALUES (?,?,'event:1','click','otherHandler','fixture')""",
                (main_repo_id, artifact_id),
            ).lastrowid
        )
        dependency_id = int(
            conn.execute(
                """INSERT INTO ui_script_dependencies(
                       repo_id,surface_id,source_artifact_id,dependency_key,script_path,
                       load_scope,resolution_status,evidence_text
                   ) VALUES (?,?,?,'dependency:1','app/resources/other.js',
                       'active','resolved','fixture')""",
                (main_repo_id, surface_id, artifact_id),
            ).lastrowid
        )
        conn.commit()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "belongs to another repository"):
            conn.execute(
                """INSERT INTO ui_event_calls(
                       repo_id,event_id,dependency_id,call_key,handler_name,handler_symbol_id,
                       resolution_status,resolution_reason,evidence_text
                   ) VALUES (?,?,?,'call:1','otherHandler',?,'resolved','fixture','fixture')""",
                (main_repo_id, event_id, dependency_id, other_symbol_id),
            )
        conn.rollback()

        # Both matching parent rows existed before the rejected entity mismatch.
        self.assertGreater(first_occurrence_id, 0)

    def test_event_calls_cannot_cross_surfaces_and_runner_restores_trigger(self) -> None:
        conn, main_repo_id, _ = self._migrated_connection()
        self.addCleanup(conn.close)
        first_surface_id, first_artifact_id = self._surface_and_artifact(
            conn, main_repo_id, 7, "actionui:first"
        )
        _, second_artifact_id = self._surface_and_artifact(
            conn, main_repo_id, 7, "actionui:second"
        )
        event_id = int(
            conn.execute(
                """INSERT INTO ui_events(
                       repo_id,artifact_id,event_key,event_type,evidence_text
                   ) VALUES (?,?,'event:1','click','fixture')""",
                (main_repo_id, second_artifact_id),
            ).lastrowid
        )
        dependency_id = int(
            conn.execute(
                """INSERT INTO ui_script_dependencies(
                       repo_id,surface_id,source_artifact_id,dependency_key,load_scope,
                       resolution_status,evidence_text
                   ) VALUES (?,?,?,'dependency:1','active','unresolved','fixture')""",
                (main_repo_id, first_surface_id, first_artifact_id),
            ).lastrowid
        )
        conn.commit()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "belong to one surface"):
            conn.execute(
                """INSERT INTO ui_event_calls(
                       repo_id,event_id,dependency_id,call_key,handler_name,resolution_status,
                       resolution_reason,evidence_text
                   ) VALUES (?,?,?,'call:1','handler','unresolved','fixture','fixture')""",
                (main_repo_id, event_id, dependency_id),
            )
        conn.rollback()

        conn.execute("DROP TRIGGER trg_ui_event_calls_symbol_repo_insert")
        conn.commit()
        apply_delta_refresh_migration(conn)
        self.assertIsNotNone(
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='trigger' "
                "AND name='trg_ui_event_calls_symbol_repo_insert'"
            ).fetchone()
        )


if __name__ == "__main__":
    unittest.main()
