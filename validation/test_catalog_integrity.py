from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from catalog.content_fingerprint import (
    logical_content_fingerprint,
    normalized_evidence_fingerprint,
)
from catalog.delta import DELTA_CONTRACT_VERSION
from catalog.migrations import apply_delta_refresh_migration
from catalog.refresh_attempts import create_refresh_attempt, record_attempt_event
from validation.validate_catalog_integrity import (
    CatalogIntegrityError,
    validate_catalog_connection,
)

ROOT = Path(__file__).resolve().parents[1]


class CatalogIntegrityTests(unittest.TestCase):
    def _catalog(self) -> tuple[tempfile.TemporaryDirectory[str], sqlite3.Connection]:
        directory = tempfile.TemporaryDirectory()
        path = Path(directory.name) / "catalog.db"
        conn = sqlite3.connect(path)
        conn.executescript((ROOT / "catalog/schema.sql").read_text())
        conn.execute(
            "INSERT INTO repos(id,repo_key,local_root,tracked_branch,indexed_commit_sha,index_status) "
            "VALUES (1,'service','/tmp/service','main','abc','active')"
        )
        fingerprint = logical_content_fingerprint(conn)
        conn.execute(
            """INSERT INTO catalog_builds(
                   build_token,catalog_path,requested_mode,effective_mode,status,
               source_revisions_json,delta_contract_version,content_fingerprint,
               completed_at
               ) VALUES ('active','catalog.db','full','full','active',?,?,?,CURRENT_TIMESTAMP)""",
            (
                json.dumps({"service": "abc"}, sort_keys=True),
                DELTA_CONTRACT_VERSION,
                fingerprint,
            ),
        )
        conn.commit()
        return directory, conn

    def test_valid_active_catalog_passes(self) -> None:
        directory, conn = self._catalog()
        self.addCleanup(directory.cleanup)
        self.addCleanup(conn.close)
        summary = validate_catalog_connection(conn)
        self.assertTrue(summary["ok"])
        self.assertEqual(summary["foreign_key_violations"], 0)
        self.assertTrue(summary["content_fingerprint_matches"])
        self.assertTrue(
            all(value == 0 for value in summary["logical_orphans"].values())
        )

    def test_ui_validator_operational_error_blocks_integrity(self) -> None:
        directory, conn = self._catalog()
        self.addCleanup(directory.cleanup)
        self.addCleanup(conn.close)
        with mock.patch(
            "validation.validate_ui_catalog.validate_ui_catalog_connection",
            side_effect=sqlite3.OperationalError("damaged ui schema"),
        ), self.assertRaises(CatalogIntegrityError) as raised:
            validate_catalog_connection(conn)
        summary = json.loads(str(raised.exception))
        self.assertIn("ui_catalog", summary["failures"])
        self.assertEqual(
            summary["ui_catalog"]["failures"], ["ui_catalog_operational_error"]
        )

    def test_foreign_key_violation_fails(self) -> None:
        directory, conn = self._catalog()
        self.addCleanup(directory.cleanup)
        self.addCleanup(conn.close)
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(
            "INSERT INTO files(id,repo_id,path) VALUES (99,999,'missing-owner.py')"
        )
        conn.commit()
        with self.assertRaisesRegex(CatalogIntegrityError, "foreign_key_check"):
            validate_catalog_connection(conn)

    def test_logical_entity_root_orphan_fails(self) -> None:
        directory, conn = self._catalog()
        self.addCleanup(directory.cleanup)
        self.addCleanup(conn.close)
        conn.execute("INSERT INTO files(id,repo_id,path) VALUES (1,1,'source.py')")
        conn.execute(
            "INSERT INTO symbols(id,file_id,name,kind,stable_key) "
            "VALUES (1,1,'Source','class','class:Source')"
        )
        conn.execute("INSERT INTO entity_nodes(id,name) VALUES (1,'Entity')")
        conn.execute(
            "INSERT INTO entity_roots(repo_id,entity_id,symbol_id,role,weight) "
            "VALUES (1,1,1,'manager',1.0)"
        )
        conn.commit()
        with self.assertRaises(CatalogIntegrityError) as raised:
            validate_catalog_connection(conn)
        summary = json.loads(str(raised.exception))
        self.assertEqual(summary["logical_orphans"]["entity_roots_without_mapping"], 1)

    def test_migration_025_removes_integration_rows_idempotently(self) -> None:
        directory, conn = self._catalog()
        self.addCleanup(directory.cleanup)
        self.addCleanup(conn.close)
        conn.execute(
            """INSERT INTO integration_links(
                   source_repo_id,relation_type,resolution_status,evidence,extractor
               ) VALUES (1,'sentinel','unresolved','sentinel','test')"""
        )
        conn.commit()
        apply_delta_refresh_migration(conn)
        apply_delta_refresh_migration(conn)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM integration_links").fetchone()[0], 0
        )
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE name='025_delta_refresh_hardening'"
            ).fetchone()[0],
            1,
        )

    def test_reintroduced_integration_rows_fail_integrity(self) -> None:
        directory, conn = self._catalog()
        self.addCleanup(directory.cleanup)
        self.addCleanup(conn.close)
        conn.execute(
            """INSERT INTO integration_links(
                   source_repo_id,relation_type,resolution_status,evidence,extractor
               ) VALUES (1,'sentinel','unresolved','sentinel','test')"""
        )
        conn.commit()
        with self.assertRaisesRegex(CatalogIntegrityError, "integration_links"):
            validate_catalog_connection(conn)

    def test_legacy_active_quality_run_without_summary_is_ignored(self) -> None:
        directory, conn = self._catalog()
        self.addCleanup(directory.cleanup)
        self.addCleanup(conn.close)
        conn.execute(
            """INSERT INTO repo_index_runs(
                   repo_id,tracked_branch,commit_sha,status,completed_at,validation_summary
               ) VALUES (1,'main','abc','active',CURRENT_TIMESTAMP,NULL)"""
        )
        conn.commit()
        self.assertTrue(validate_catalog_connection(conn)["ok"])

    def test_required_active_quality_run_without_summary_fails_integrity(self) -> None:
        directory, conn = self._catalog()
        self.addCleanup(directory.cleanup)
        self.addCleanup(conn.close)
        run_id = conn.execute(
            """INSERT INTO repo_index_runs(
                   repo_id,tracked_branch,commit_sha,status,completed_at,validation_summary
               ) VALUES (1,'main','abc','active',CURRENT_TIMESTAMP,NULL)"""
        ).lastrowid
        conn.commit()
        with self.assertRaisesRegex(CatalogIntegrityError, "quality_runs"):
            validate_catalog_connection(conn, required_quality_run_ids={int(run_id)})

    def test_active_contract_run_ids_are_derived_from_change_sets(self) -> None:
        directory, conn = self._catalog()
        self.addCleanup(directory.cleanup)
        self.addCleanup(conn.close)
        run_id = conn.execute(
            """INSERT INTO repo_index_runs(
                   repo_id,tracked_branch,commit_sha,status,completed_at,validation_summary
               ) VALUES (1,'main','abc','active',CURRENT_TIMESTAMP,NULL)"""
        ).lastrowid
        conn.execute(
            """INSERT INTO repo_change_sets(
                   catalog_build_id,repo_index_run_id,repo_id,target_commit_sha,
                   requested_mode,effective_mode,status,completed_at
               ) VALUES (1,?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
            (int(run_id), 1, "abc", "full", "full", "succeeded"),
        )
        conn.commit()
        with self.assertRaisesRegex(CatalogIntegrityError, "quality_runs"):
            validate_catalog_connection(conn)

    def test_malformed_required_active_quality_run_fails_integrity(self) -> None:
        directory, conn = self._catalog()
        self.addCleanup(directory.cleanup)
        self.addCleanup(conn.close)
        run_id = conn.execute(
            """INSERT INTO repo_index_runs(
                   repo_id,tracked_branch,commit_sha,status,completed_at,validation_summary
               ) VALUES (1,'main','abc','active',CURRENT_TIMESTAMP,'{}')"""
        ).lastrowid
        conn.commit()
        with self.assertRaisesRegex(CatalogIntegrityError, "quality_runs"):
            validate_catalog_connection(conn, required_quality_run_ids={int(run_id)})

    def test_missing_required_quality_run_fails_integrity(self) -> None:
        directory, conn = self._catalog()
        self.addCleanup(directory.cleanup)
        self.addCleanup(conn.close)
        with self.assertRaisesRegex(CatalogIntegrityError, "quality_runs"):
            validate_catalog_connection(conn, required_quality_run_ids={999})

    def test_migration_032_is_idempotent_and_marks_legacy_active_recovery_required(self) -> None:
        directory, conn = self._catalog()
        self.addCleanup(directory.cleanup)
        self.addCleanup(conn.close)
        conn.execute("UPDATE catalog_builds SET refresh_readiness='ready' WHERE status='active'")
        conn.execute(
            "DELETE FROM schema_migrations WHERE name='032_refresh_attempts_and_readiness'"
        )
        conn.commit()
        apply_delta_refresh_migration(conn)
        apply_delta_refresh_migration(conn)
        self.assertEqual(
            conn.execute(
                "SELECT refresh_readiness FROM catalog_builds WHERE status='active'"
            ).fetchone()[0],
            "full_recovery_required",
        )
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM refresh_attempt_events"
            ).fetchone()[0],
            0,
        )

    def test_validator_rejects_falsely_ready_legacy_generation(self) -> None:
        directory, conn = self._catalog()
        self.addCleanup(directory.cleanup)
        self.addCleanup(conn.close)
        conn.execute("UPDATE catalog_builds SET refresh_readiness='ready' WHERE status='active'")
        conn.commit()
        with self.assertRaisesRegex(CatalogIntegrityError, "refresh_readiness"):
            validate_catalog_connection(conn)

    def test_attempt_ledger_is_append_only_and_terminal(self) -> None:
        directory, conn = self._catalog()
        self.addCleanup(directory.cleanup)
        self.addCleanup(conn.close)
        path = Path(conn.execute("PRAGMA database_list").fetchone()[2])
        token = create_refresh_attempt(path, repo_key="service", requested_mode="auto")
        record_attempt_event(
            path,
            token,
            stage="terminal",
            status="succeeded",
            fields={"status": "succeeded", "readiness_after": "full_recovery_required"},
        )
        with self.assertRaisesRegex(Exception, "terminal"):
            record_attempt_event(path, token, stage="late")
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM refresh_attempt_events"
            ).fetchone()[0],
            2,
        )

    def test_migration_033_rebuilds_ledger_without_catalog_parent_fk(self) -> None:
        directory, conn = self._catalog()
        self.addCleanup(directory.cleanup)
        self.addCleanup(conn.close)
        path = Path(conn.execute("PRAGMA database_list").fetchone()[2])
        token = create_refresh_attempt(path, repo_key="service", requested_mode="auto")
        record_attempt_event(path, token, stage="planning", detail="preserve me")
        conn.execute("DELETE FROM schema_migrations WHERE name='033_refresh_reliability_gates'")
        conn.commit()
        apply_delta_refresh_migration(conn)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM refresh_attempt_events").fetchone()[0], 2)
        self.assertEqual(conn.execute("PRAGMA foreign_key_list(refresh_attempts)").fetchall(), [])
        with self.assertRaisesRegex(sqlite3.DatabaseError, "events are immutable"):
            conn.execute("DELETE FROM refresh_attempt_events")

    def test_normalized_projection_ignores_surrogate_repository_ids(self) -> None:
        first = sqlite3.connect(":memory:")
        second = sqlite3.connect(":memory:")
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        schema = (ROOT / "catalog/schema.sql").read_text()
        first.executescript(schema)
        second.executescript(schema)
        for conn, repo_id in ((first, 1), (second, 99)):
            conn.execute(
                "INSERT INTO repos(id,repo_key,local_root,tracked_branch) VALUES (?,?,?,?)",
                (repo_id, "service", "/source", "main"),
            )
            conn.commit()
        self.assertEqual(
            normalized_evidence_fingerprint(first), normalized_evidence_fingerprint(second)
        )


if __name__ == "__main__":
    unittest.main()
