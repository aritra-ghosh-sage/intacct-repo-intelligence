"""Focused regressions for repository-scoped entity mapping construction."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from click.testing import CliRunner

from scripts import build_entities


class RepoScopedEntityBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "catalog.db"
        self.entities = self.root / "entities.jsonl"
        self.second_entities = self.root / "second-entities.jsonl"

        conn = sqlite3.connect(self.db)
        try:
            schema = (Path(__file__).parents[1] / "catalog" / "schema.sql").read_text()
            conn.executescript(schema)
            conn.executemany(
                """
                INSERT INTO repos (id, repo_key, local_root, tracked_branch)
                VALUES (?, ?, ?, 'main')
                """,
                [
                    (1, "one", str(self.root / "one")),
                    (2, "two", str(self.root / "two")),
                ],
            )
            conn.executemany(
                """
                INSERT INTO files (id, repo_id, path, language)
                VALUES (?, ?, 'app/source/CustomerEditor.cls', 'cls')
                """,
                [(10, 1), (20, 2)],
            )
            conn.executemany(
                """
                INSERT INTO symbols (id, file_id, name, kind, start_line, end_line)
                VALUES (?, ?, 'CustomerEditor', 'class', 1, 2)
                """,
                [(100, 10), (200, 20)],
            )
            conn.commit()
        finally:
            conn.close()

        self.entities.write_text(
            json.dumps(
                {
                    "entity_name": "Customer",
                    "ent_file": "app/source/one/Customer.ent",
                    "module": "ar",
                    "table": "ARCUSTOMER",
                    "companion_classes": {
                        "editor": "app/source/CustomerEditor.cls",
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        self.second_entities.write_text(
            json.dumps(
                {
                    "entity_name": "Customer",
                    "ent_file": "app/source/two/Customer.ent",
                    "module": "co",
                    "table": "COCUSTOMER",
                    "companion_classes": {
                        "editor": "app/source/CustomerEditor.cls",
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )

    def _rows(self) -> list[sqlite3.Row]:
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(
                "SELECT repo_id, symbol_id, file_id FROM entity_mappings ORDER BY repo_id"
            ).fetchall()
        finally:
            conn.close()

    def test_same_named_companion_symbols_resolve_within_each_repository(self) -> None:
        build_entities.build(str(self.db), self.entities, reset=False, repo_key="one")
        build_entities.build(str(self.db), self.entities, reset=False, repo_key="two")

        self.assertEqual(
            [
                (row["repo_id"], row["symbol_id"], row["file_id"])
                for row in self._rows()
            ],
            [(1, 100, 10), (2, 200, 20)],
        )

    def test_reset_removes_only_selected_repository_mappings_and_preserves_entity(
        self,
    ) -> None:
        build_entities.build(str(self.db), self.entities, reset=False, repo_key="one")
        build_entities.build(str(self.db), self.entities, reset=False, repo_key="two")
        build_entities.build(str(self.db), self.entities, reset=True, repo_key="one")

        self.assertEqual(
            [
                (row["repo_id"], row["symbol_id"], row["file_id"])
                for row in self._rows()
            ],
            [(1, 100, 10), (2, 200, 20)],
        )
        conn = sqlite3.connect(self.db)
        try:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM entity_nodes WHERE name = 'Customer'"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM entity_occurrences").fetchone()[0],
                2,
            )
        finally:
            conn.close()

    def test_cli_requires_repository_key(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            build_entities.cli,
            ["build", "--db", str(self.db), "--entities", str(self.entities)],
        )
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Missing option '--repo'", result.output)

    def test_same_canonical_entity_keeps_distinct_repo_occurrences(self) -> None:
        build_entities.build(str(self.db), self.entities, reset=False, repo_key="one")
        build_entities.build(
            str(self.db), self.second_entities, reset=False, repo_key="two"
        )

        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            canonical = conn.execute(
                "SELECT COUNT(*) AS count FROM entity_nodes WHERE name = 'Customer'"
            ).fetchone()
            occurrences = conn.execute(
                """
                SELECT eo.repo_id, eo.ent_file, eo.module, eo.table_name
                FROM entity_occurrences eo
                JOIN entity_nodes en ON en.id = eo.entity_id
                WHERE en.name = 'Customer'
                ORDER BY eo.repo_id
                """
            ).fetchall()
        finally:
            conn.close()

        self.assertEqual(canonical["count"], 1)
        self.assertEqual(
            [tuple(row) for row in occurrences],
            [
                (1, "app/source/one/Customer.ent", "ar", "ARCUSTOMER"),
                (2, "app/source/two/Customer.ent", "co", "COCUSTOMER"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
