from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from catalog.archive_ownership import ARCHIVE_OWNED_REPO_TABLES, target_row_counts
from catalog.archive_repository import (
    ArchiveRepositoryError,
    archive_repository,
    repository_evidence_fingerprint,
)
from catalog.content_fingerprint import logical_content_fingerprint
from catalog.delta import DELTA_CONTRACT_VERSION
from catalog.refresh_transaction import promote_catalog_candidate


class ArchiveRepositoryTests(unittest.TestCase):
    def _database(self, directory: Path) -> tuple[Path, int, int]:
        db = directory / "catalog.db"
        conn = sqlite3.connect(db)
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(Path("catalog/schema.sql").read_text())
        conn.executemany(
            """INSERT INTO repos(repo_key,local_root,tracked_branch,indexed_commit_sha)
               VALUES (?,?,?,?)""",
            [
                ("active", "/fixture/active", "main", "a" * 40),
                ("archived-target", "/fixture/target", "main", "b" * 40),
            ],
        )
        active, target = [
            int(row[0])
            for row in conn.execute("SELECT id FROM repos ORDER BY id")
        ]
        conn.executemany(
            "INSERT INTO files(repo_id,path,language) VALUES (?,?,?)",
            [(active, "active.cls", "java"), (target, "target.cls", "java")],
        )
        active_file, target_file = [
            int(row[0]) for row in conn.execute("SELECT id FROM files ORDER BY id")
        ]
        conn.executemany(
            "INSERT INTO symbols(file_id,name,kind,stable_key) VALUES (?,?,?,?)",
            [(active_file, "Active", "class", "active"), (target_file, "Target", "class", "target")],
        )
        active_symbol, target_symbol = [
            int(row[0]) for row in conn.execute("SELECT id FROM symbols ORDER BY id")
        ]
        conn.execute(
            "INSERT INTO relationships(repo_id,source_symbol_id,target_symbol_id,relationship_type,file_id) VALUES (?,?,?,?,?)",
            (target, target_symbol, target_symbol, "calls", target_file),
        )
        conn.execute(
            "INSERT INTO openapispec_index(repo_id,file_id,file_path) VALUES (?,?,?)",
            (target, target_file, "target.yaml"),
        )
        conn.execute(
            "INSERT INTO test_cases(repo_id,file_id,feature_name,scenario_name,case_name,feature_line,scenario_line) VALUES (?,?,?,?,?,?,?)",
            (target, target_file, "F", "S", "S", 1, 2),
        )
        case_id = int(conn.execute("SELECT id FROM test_cases").fetchone()[0])
        conn.execute("INSERT INTO test_requests(test_case_id,ordinal,step_line) VALUES (?,?,?)", (case_id, 1, 3))
        request_id = int(conn.execute("SELECT id FROM test_requests").fetchone()[0])
        conn.execute(
            "INSERT INTO api_version_compatibility(repo_id,test_version,endpoint_version,rationale,evidence) VALUES (?,?,?,?,?)",
            (target, "v1", "v2", "fixture", "fixture"),
        )
        compatibility_id = int(conn.execute("SELECT id FROM api_version_compatibility").fetchone()[0])
        # The endpoint is target-owned, so all test descendants can cascade.
        conn.execute(
            "INSERT INTO rest_endpoints(id,repo_id,method,path,file_id) VALUES (?,?,?,?,?)",
            (101, target, "GET", "/target", target_file),
        )
        conn.execute(
            "INSERT INTO test_endpoint_links(test_request_id,rest_endpoint_id,compatibility_id,resolution_kind) VALUES (?,?,?,?)",
            (request_id, 101, compatibility_id, "compatible_version"),
        )
        fingerprint = logical_content_fingerprint(conn)
        conn.execute(
            """INSERT INTO catalog_builds(
                   build_token,catalog_path,requested_mode,effective_mode,status,
                   source_revisions_json,delta_contract_version,content_fingerprint,completed_at
               ) VALUES (?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
            (
                "baseline",
                str(db),
                "full",
                "full",
                "active",
                '{"active":"' + "a" * 40 + '","archived-target":"' + "b" * 40 + '"}',
                DELTA_CONTRACT_VERSION,
                fingerprint,
            ),
        )
        conn.commit()
        conn.close()
        return db, active, target

    @staticmethod
    def _digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_archives_only_target_evidence_and_preserves_active(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db, active, target = self._database(Path(directory))
            conn = sqlite3.connect(db)
            active_before = repository_evidence_fingerprint(conn, active)
            conn.close()
            result = archive_repository(db, "archived-target", source="manual", reason="fixture")
            self.assertTrue(result.promoted)
            self.assertTrue(result.graph_rebuild_required)
            conn = sqlite3.connect(db)
            conn.row_factory = sqlite3.Row
            self.assertEqual("archived", conn.execute("SELECT lifecycle_state FROM repos WHERE id=?", (target,)).fetchone()[0])
            self.assertFalse(any(target_row_counts(conn, target).values()))
            self.assertEqual(active_before, repository_evidence_fingerprint(conn, active))
            build = conn.execute(
                "SELECT requested_mode,effective_mode,source_revisions_json,status FROM catalog_builds WHERE build_token=?",
                (result.build_token,),
            ).fetchone()
            self.assertEqual(("archive", "archive", '{"active":"' + "a" * 40 + '"}', "active"), tuple(build))
            conn.close()
            self.assertTrue(db.with_name("catalog.db.previous").is_file())

    def test_active_inbound_reference_aborts_without_promoting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db, active, _target = self._database(Path(directory))
            conn = sqlite3.connect(db)
            target_symbol = int(conn.execute("SELECT id FROM symbols WHERE stable_key='target'").fetchone()[0])
            active_symbol = int(conn.execute("SELECT id FROM symbols WHERE stable_key='active'").fetchone()[0])
            active_file = int(conn.execute("SELECT id FROM files WHERE repo_id=?", (active,)).fetchone()[0])
            conn.execute(
                "INSERT INTO relationships(repo_id,source_symbol_id,target_symbol_id,relationship_type,file_id) VALUES (?,?,?,?,?)",
                (active, active_symbol, target_symbol, "calls", active_file),
            )
            conn.commit()
            conn.close()
            before = self._digest(db)
            previous = db.with_name("catalog.db.previous")
            self.assertFalse(previous.exists())
            with self.assertRaisesRegex(ArchiveRepositoryError, "active or user-owned"):
                archive_repository(db, "archived-target", source="manual", reason="fixture")
            self.assertEqual(before, self._digest(db))
            self.assertFalse(previous.exists())

    def test_active_indirect_endpoint_and_workflow_references_abort(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db, active, _target = self._database(Path(directory))
            conn = sqlite3.connect(db)
            active_file = int(conn.execute("SELECT id FROM files WHERE repo_id=?", (active,)).fetchone()[0])
            target_file = int(conn.execute("SELECT id FROM files WHERE path='target.cls'").fetchone()[0])
            target_symbol = int(conn.execute("SELECT id FROM symbols WHERE stable_key='target'").fetchone()[0])
            conn.execute(
                "INSERT INTO test_cases(repo_id,file_id,feature_name,scenario_name,case_name,feature_line,scenario_line) VALUES (?,?,?,?,?,?,?)",
                (active, active_file, "F", "S", "active", 1, 2),
            )
            active_case = int(conn.execute("SELECT MAX(id) FROM test_cases").fetchone()[0])
            conn.execute("INSERT INTO test_requests(test_case_id,ordinal,step_line) VALUES (?,?,?)", (active_case, 1, 3))
            active_request = int(conn.execute("SELECT MAX(id) FROM test_requests").fetchone()[0])
            conn.execute(
                "INSERT INTO test_endpoint_links(test_request_id,rest_endpoint_id,resolution_kind) VALUES (?,?,?)",
                (active_request, 101, "exact_version"),
            )
            conn.execute("INSERT INTO entity_nodes(name) VALUES ('ActiveEntity')")
            entity_id = int(conn.execute("SELECT id FROM entity_nodes WHERE name='ActiveEntity'").fetchone()[0])
            conn.execute(
                "INSERT INTO workflows(repo_id,entity_id,name,workflow_type,source_kind) VALUES (?,?,?,?,?)",
                (active, entity_id, "active workflow", "ui", "fixture"),
            )
            workflow = int(conn.execute("SELECT MAX(id) FROM workflows").fetchone()[0])
            conn.execute(
                "INSERT INTO workflow_nodes(workflow_id,entity_id,node_kind,node_key,file_id,symbol_id) VALUES (?,?,?,?,?,?)",
                (workflow, entity_id, "symbol", "target", target_file, target_symbol),
            )
            conn.commit()
            conn.close()
            before = self._digest(db)
            with self.assertRaisesRegex(ArchiveRepositoryError, "active or user-owned"):
                archive_repository(db, "archived-target", source="manual", reason="fixture")
            self.assertEqual(before, self._digest(db))

    def test_validation_failure_discards_candidate_and_preserves_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db, _active, _target = self._database(Path(directory))
            before = self._digest(db)
            with self.assertRaisesRegex(RuntimeError, "injected validation"):
                archive_repository(
                    db,
                    "archived-target",
                    source="manual",
                    reason="fixture",
                    validator=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("injected validation")),
                )
            self.assertEqual(before, self._digest(db))
            self.assertFalse(db.with_name("catalog.db.previous").exists())

    def test_parent_cas_failure_never_promotes_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db, _active, _target = self._database(Path(directory))

            def mutate_parent() -> None:
                conn = sqlite3.connect(db)
                conn.execute(
                    "UPDATE catalog_builds SET content_fingerprint='changed' WHERE status='active'"
                )
                conn.commit()
                conn.close()

            with self.assertRaisesRegex(Exception, "compare-and-swap"):
                archive_repository(
                    db,
                    "archived-target",
                    source="manual",
                    reason="fixture",
                    before_promote=mutate_parent,
                )
            conn = sqlite3.connect(db)
            self.assertEqual(
                "active", conn.execute("SELECT lifecycle_state FROM repos WHERE repo_key='archived-target'").fetchone()[0]
            )
            conn.close()
            self.assertFalse(db.with_name("catalog.db.previous").exists())

    def test_promotion_replace_failure_preserves_active_and_previous(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db, _active, _target = self._database(Path(directory))
            candidate = db.with_name("candidate.db")
            previous = db.with_name("catalog.db.previous")
            shutil.copy2(db, candidate)
            shutil.copy2(db, previous)
            active_before, previous_before = self._digest(db), self._digest(previous)
            original_replace = os.replace

            def fail_candidate_replace(source: str | Path, destination: str | Path) -> None:
                if Path(source) == candidate and Path(destination) == db:
                    raise OSError("injected candidate replace failure")
                original_replace(source, destination)

            with patch("catalog.refresh_transaction.os.replace", side_effect=fail_candidate_replace):
                with self.assertRaisesRegex(OSError, "injected candidate"):
                    promote_catalog_candidate(db, candidate, previous, "baseline")
            self.assertEqual(active_before, self._digest(db))
            self.assertEqual(previous_before, self._digest(previous))

    def test_idempotent_already_archived_empty_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db, _active, _target = self._database(Path(directory))
            archive_repository(db, "archived-target", source="manual", reason="fixture")
            before = self._digest(db)
            result = archive_repository(db, "archived-target", source="manual", reason="ignored")
            self.assertTrue(result.idempotent)
            self.assertEqual(before, self._digest(db))
            self.assertFalse(list(db.parent.glob("catalog.db.archive-candidate.*")))


if __name__ == "__main__":
    unittest.main()
