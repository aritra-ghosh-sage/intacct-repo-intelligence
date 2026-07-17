from __future__ import annotations

import sqlite3
import unittest

from scripts.build_entity_access_links import (
    link_security_surfaces,
    parse_security_operation_key,
)


class EntityAccessLinkTests(unittest.TestCase):
    def test_security_key_parser(self) -> None:
        self.assertEqual(
            parse_security_operation_key("gl/lists/glbatch"),
            {
                "module": "gl",
                "route": "lists",
                "entity": "glbatch",
                "action": "",
                "surface": "security_resource",
            },
        )
        parsed = parse_security_operation_key("gl/lists/glbatch/reclass")
        assert parsed is not None
        self.assertEqual(parsed["surface"], "security_operation")
        self.assertEqual(parsed["action"], "reclass")
        self.assertIsNone(parse_security_operation_key("gl/lists"))
        self.assertIsNone(parse_security_operation_key("gl//glbatch/edit"))

    def test_links_only_exact_entity_and_module_matches(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE entity_nodes(id INTEGER PRIMARY KEY, name TEXT, module TEXT);
            CREATE TABLE files(id INTEGER PRIMARY KEY, path TEXT);
            CREATE TABLE security_operations(
                id INTEGER PRIMARY KEY, op_key TEXT, source_file TEXT, file_id INTEGER
            );
            CREATE TABLE security_policy_values(id INTEGER PRIMARY KEY, policy_id INTEGER);
            CREATE TABLE security_policy_eops(policy_value_id INTEGER, op_key TEXT);
            CREATE TABLE security_policies(id INTEGER PRIMARY KEY, file_id INTEGER);
            CREATE TABLE security_menu_op_links(
                menu_item_id INTEGER, operation_id INTEGER, op_key TEXT
            );
            CREATE TABLE security_menu_items(id INTEGER PRIMARY KEY, menu_id INTEGER);
            CREATE TABLE security_menus(id INTEGER PRIMARY KEY, file_id INTEGER);
            CREATE TABLE entity_access_links(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id INTEGER NOT NULL,
                surface TEXT NOT NULL,
                record_id INTEGER NOT NULL,
                link_type TEXT NOT NULL,
                evidence_file_id INTEGER,
                evidence_symbol_id INTEGER,
                confidence_mode TEXT NOT NULL,
                notes TEXT,
                UNIQUE(
                    entity_id, surface, record_id, link_type,
                    evidence_file_id, evidence_symbol_id
                )
            );
            INSERT INTO entity_nodes VALUES
                (1, 'GLBatch', 'gl'),
                (2, 'GLAccount', 'gl');
            INSERT INTO security_operations VALUES
                (10, 'gl/lists/glbatch', 'app/source/common/security.inc', 1),
                (11, 'gl/lists/glbatch/edit', 'app/source/common/security.inc', 1),
                (12, 'gl/lists/glaccount/edit', 'app/source/common/security.inc', 1),
                (13, 'ar/lists/glbatch/edit', 'app/source/common/security.inc', 1);
            """
        )
        try:
            diagnostics = []
            result = link_security_surfaces(conn, diagnostics)
            self.assertEqual(result[1], 3)
            self.assertEqual(
                {item["reason"] for item in diagnostics},
                {"module_mismatched"},
            )
            rows = conn.execute(
                """
                SELECT surface, record_id
                FROM entity_access_links
                WHERE entity_id = 1
                ORDER BY surface, record_id
                """
            ).fetchall()
            self.assertEqual(
                [(row["surface"], row["record_id"]) for row in rows],
                [
                    ("security_operation", 11),
                    ("security_resource", 10),
                ],
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM entity_access_links WHERE entity_id = 1 AND record_id = 12"
                ).fetchone()[0],
                0,
            )
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
