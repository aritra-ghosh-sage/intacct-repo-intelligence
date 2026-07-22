from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts import build_security_mappings


class RepoScopedSecurityTests(unittest.TestCase):
    def test_reset_only_deletes_selected_repository_security_records(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            db_path = root / "catalog.db"
            conn = sqlite3.connect(db_path)
            try:
                schema = Path("catalog/schema.sql").read_text(encoding="utf-8")
                conn.executescript(schema)
                first_root = root / "first"
                second_root = root / "second"
                first_root.mkdir()
                second_root.mkdir()
                conn.executemany(
                    """
                    INSERT INTO repos(repo_key, local_root, tracked_branch)
                    VALUES (?, ?, 'main')
                    """,
                    [("first", str(first_root)), ("second", str(second_root))],
                )
                conn.execute(
                    """
                    INSERT INTO security_operations(
                        repo_id, op_key, source_file, source_kind
                    ) VALUES (1, 'gl/lists/first', 'security.inc', 'security')
                    """
                )
                conn.execute(
                    """
                    INSERT INTO security_operations(
                        repo_id, op_key, source_file, source_kind
                    ) VALUES (2, 'gl/lists/second', 'security.inc', 'security')
                    """
                )
                conn.commit()
            finally:
                conn.close()

            log_dir = root / "logs"
            log_dir.mkdir()
            original_logs = (
                build_security_mappings.PARSE_FAILURES_LOG,
                build_security_mappings.UNRESOLVED_LOG,
                build_security_mappings.CONFLICTS_LOG,
                build_security_mappings.UNRESOLVED_FILE_IDS_LOG,
            )
            build_security_mappings.PARSE_FAILURES_LOG = log_dir / "parse.jsonl"
            build_security_mappings.UNRESOLVED_LOG = log_dir / "unresolved.jsonl"
            build_security_mappings.CONFLICTS_LOG = log_dir / "conflicts.jsonl"
            build_security_mappings.UNRESOLVED_FILE_IDS_LOG = log_dir / "files.jsonl"
            try:
                build_security_mappings.build(
                    db=str(db_path),
                    repo_key="first",
                    reset=True,
                    max_parse_failures=-1,
                    max_unresolved=-1,
                )
            finally:
                (
                    build_security_mappings.PARSE_FAILURES_LOG,
                    build_security_mappings.UNRESOLVED_LOG,
                    build_security_mappings.CONFLICTS_LOG,
                    build_security_mappings.UNRESOLVED_FILE_IDS_LOG,
                ) = original_logs

            conn = sqlite3.connect(db_path)
            try:
                self.assertEqual(
                    conn.execute(
                        "SELECT COUNT(*) FROM security_operations WHERE repo_id = 1"
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    conn.execute(
                        "SELECT COUNT(*) FROM security_operations WHERE repo_id = 2"
                    ).fetchone()[0],
                    1,
                )
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
