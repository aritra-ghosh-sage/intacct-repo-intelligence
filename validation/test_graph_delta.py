from __future__ import annotations

import inspect
import json
import re
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from catalog.content_fingerprint import logical_content_fingerprint
from catalog.graph_materialization import ensure_schema
from catalog.graph_projection import (
    GRAPH_PROJECTION_VERSION,
    NODE_PROJECTIONS,
    PROJECTIONS,
    RELATIONSHIP_PROJECTIONS,
    iter_available_projections,
    projection_diff,
)
from catalog.migrations import apply_delta_refresh_migration
from scripts.build_graph import (
    graph_delta_eligibility,
    promote_validated_graph,
)
from validation.validate_graph import validate_paths

ROOT = Path(__file__).resolve().parents[1]


class GraphDeltaTests(unittest.TestCase):
    def _generation_fixture(self):
        directory = tempfile.TemporaryDirectory()
        root = Path(directory.name)
        current_path = root / "catalog.db"
        previous_path = root / "catalog.db.previous"
        graph_path = root / "graph.lbug"

        previous = sqlite3.connect(previous_path)
        previous.executescript((ROOT / "catalog/schema.sql").read_text())
        previous.execute("DELETE FROM schema_migrations WHERE name='023_delta_refresh'")
        previous.commit()
        apply_delta_refresh_migration(previous)
        previous_build = previous.execute(
            "SELECT id,content_fingerprint,source_revisions_json FROM catalog_builds WHERE status='active'"
        ).fetchone()
        previous.close()
        shutil.copy2(previous_path, current_path)

        current = sqlite3.connect(current_path)
        current.execute("INSERT INTO entity_nodes(name) VALUES ('Invoice')")
        current.execute(
            "UPDATE catalog_builds SET status='previous' WHERE id=?",
            (previous_build[0],),
        )
        current_fingerprint = logical_content_fingerprint(current)
        current_build_id = current.execute(
            """INSERT INTO catalog_builds(
                   build_token,parent_catalog_build_id,catalog_path,requested_mode,
                   effective_mode,status,source_revisions_json,delta_contract_version,
                   content_fingerprint,completed_at
               ) VALUES ('current',?,'catalog.db','auto','delta','active',?,1,?,CURRENT_TIMESTAMP)""",
            (previous_build[0], previous_build[2], current_fingerprint),
        ).lastrowid
        base_graph_id = current.execute(
            """INSERT INTO graph_builds(
                   graph_path,source_db,status,source_fingerprint,catalog_build_id,
                   build_mode,projection_version,source_revisions_json,completed_at
               ) VALUES (?,?,'previous',?,?,'full',?,?,CURRENT_TIMESTAMP)""",
            (
                str(graph_path.resolve()),
                str(current_path),
                previous_build[1],
                previous_build[0],
                GRAPH_PROJECTION_VERSION,
                previous_build[2],
            ),
        ).lastrowid
        current.commit()
        current.close()
        graph_path.write_bytes(b"parent-graph")
        return (
            directory,
            current_path,
            previous_path,
            graph_path,
            int(current_build_id),
            int(base_graph_id),
        )

    def test_projection_diff_tracks_node_add_update_delete(self) -> None:
        old = sqlite3.connect(":memory:")
        new = sqlite3.connect(":memory:")
        for conn in (old, new):
            conn.executescript((ROOT / "catalog/schema.sql").read_text())
        old.execute(
            "INSERT INTO entity_nodes(id,name,entity_type) VALUES (1,'Old','record')"
        )
        old.execute(
            "INSERT INTO entity_nodes(id,name,entity_type) VALUES (2,'Gone','record')"
        )
        new.execute(
            "INSERT INTO entity_nodes(id,name,entity_type) VALUES (1,'Changed','record')"
        )
        new.execute(
            "INSERT INTO entity_nodes(id,name,entity_type) VALUES (3,'Added','record')"
        )
        summary = projection_diff(old, new)["Entity"]
        self.assertEqual(summary, {"added": 1, "deleted": 1, "changed": 1})
        old.close()
        new.close()

    def test_projection_diff_reports_actual_relationship_families(self) -> None:
        old = sqlite3.connect(":memory:")
        new = sqlite3.connect(":memory:")
        for conn in (old, new):
            conn.executescript((ROOT / "catalog/schema.sql").read_text())
        old.execute(
            "INSERT INTO relationships(id,repo_id,relationship_type,source_symbol_id,target_symbol_id,evidence) VALUES (1,1,'CALLS',10,20,'old')"
        )
        old.execute(
            "INSERT INTO relationships(id,repo_id,relationship_type,source_symbol_id,target_symbol_id,evidence) VALUES (2,1,'USES',20,30,'gone')"
        )
        new.execute(
            "INSERT INTO relationships(id,repo_id,relationship_type,source_symbol_id,target_symbol_id,evidence) VALUES (1,1,'STATIC_CALLS',10,20,'changed')"
        )
        new.execute(
            "INSERT INTO relationships(id,repo_id,relationship_type,source_symbol_id,target_symbol_id,evidence) VALUES (3,1,'REFERENCES',30,40,'added')"
        )
        summary = projection_diff(old, new)
        self.assertEqual(summary["CALLS"], {"added": 0, "deleted": 0, "changed": 0})
        self.assertEqual(summary["USES"], {"added": 0, "deleted": 1, "changed": 0})
        self.assertEqual(
            summary["REFERENCES"], {"added": 1, "deleted": 0, "changed": 0}
        )
        self.assertNotIn("CODE_RELATIONSHIPS", summary)
        old.close()
        new.close()

    def test_projection_diff_reports_entity_occurrence_edge_fanout(self) -> None:
        old = sqlite3.connect(":memory:")
        new = sqlite3.connect(":memory:")
        for conn in (old, new):
            conn.executescript((ROOT / "catalog/schema.sql").read_text())
        new.execute(
            "INSERT INTO entity_occurrences(id,repo_id,entity_id,ent_file,source_file_id) "
            "VALUES (7,2,3,'app/source/ap/APBill.ent',11)"
        )
        summary = projection_diff(old, new)
        for label in (
            "REPOSITORY_HAS_ENTITY_OCCURRENCE",
            "ENTITY_HAS_OCCURRENCE",
            "ENTITY_OCCURRENCE_FILE",
        ):
            self.assertEqual(summary[label], {"added": 1, "deleted": 0, "changed": 0})
        old.close()
        new.close()

    def test_projection_diff_reports_relationship_property_change(self) -> None:
        old = sqlite3.connect(":memory:")
        new = sqlite3.connect(":memory:")
        for conn in (old, new):
            conn.executescript((ROOT / "catalog/schema.sql").read_text())
        old.execute(
            "INSERT INTO entity_roots(id,repo_id,entity_id,symbol_id,role,weight) "
            "VALUES (1,1,10,20,'manager',0.7)"
        )
        new.execute(
            "INSERT INTO entity_roots(id,repo_id,entity_id,symbol_id,role,weight) "
            "VALUES (1,1,10,20,'manager',0.9)"
        )
        self.assertEqual(
            projection_diff(old, new)["ENTITY_ROOT"],
            {"added": 0, "deleted": 0, "changed": 1},
        )
        old.close()
        new.close()

    def test_relationship_registry_uses_unique_ladybug_labels(self) -> None:
        labels = [projection.ladybug_table for projection in RELATIONSHIP_PROJECTIONS]
        self.assertEqual(len(labels), len(set(labels)))
        self.assertIn("CALLS", labels)
        self.assertIn("ENTITY_HAS_OCCURRENCE", labels)
        self.assertNotIn("CODE_RELATIONSHIPS", labels)

    def test_relationship_registry_covers_full_graph_schema(self) -> None:
        schema_source = inspect.getsource(ensure_schema)
        node_schema_labels = set(
            re.findall(r"CREATE NODE TABLE IF NOT EXISTS ([A-Za-z]+)", schema_source)
        )
        schema_labels = set(
            re.findall(r"CREATE REL TABLE IF NOT EXISTS ([A-Z_]+)", schema_source)
        )
        node_registry_labels = {
            projection.ladybug_table for projection in NODE_PROJECTIONS
        }
        registry_labels = {
            projection.ladybug_table for projection in RELATIONSHIP_PROJECTIONS
        }
        self.assertEqual(node_registry_labels, node_schema_labels)
        self.assertEqual(registry_labels, schema_labels)

    def test_every_post_023_projection_query_matches_its_loader_shape(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.executescript((ROOT / "catalog/schema.sql").read_text())
        available = tuple(iter_available_projections(conn))
        self.assertEqual(available, PROJECTIONS)
        for projection in available:
            self.assertIsNotNone(projection.source_query)
            self.assertIsNotNone(projection.graph_query)
            cursor = conn.execute(projection.source_query)
            expected_columns = len(projection.property_keys)
            if projection.kind == "relationship":
                expected_columns += 2
            self.assertEqual(
                len(cursor.description or ()),
                expected_columns,
                projection.ladybug_table,
            )
        conn.close()

    def test_validator_rejects_unsupported_projection_version(self) -> None:
        with self.assertRaisesRegex(
            RuntimeError, "unsupported graph projection version"
        ):
            validate_paths(
                "/does/not/need/to/exist.db",
                "/does/not/need/to/exist.lbug",
                expected_projection_version=GRAPH_PROJECTION_VERSION - 1,
            )

    def test_delta_eligibility_links_exact_parent_generation(self) -> None:
        fixture = self._generation_fixture()
        self.addCleanup(fixture[0].cleanup)
        eligibility = graph_delta_eligibility(str(fixture[1]), str(fixture[3]))
        self.assertTrue(eligibility.eligible, eligibility.reason)
        self.assertEqual(eligibility.catalog_build_id, fixture[4])
        self.assertEqual(eligibility.base_graph_build_id, fixture[5])

    def test_delta_eligibility_rejects_parent_graph_for_another_path(self) -> None:
        fixture = self._generation_fixture()
        self.addCleanup(fixture[0].cleanup)
        current_path, graph_path, base_graph_id = fixture[1], fixture[3], fixture[5]
        conn = sqlite3.connect(current_path)
        conn.execute(
            "UPDATE graph_builds SET graph_path=? WHERE id=?",
            (str(graph_path.with_name("other.lbug").resolve()), base_graph_id),
        )
        conn.commit()
        conn.close()

        eligibility = graph_delta_eligibility(str(current_path), str(graph_path))
        self.assertFalse(eligibility.eligible)
        self.assertEqual(
            eligibility.reason, "no graph build is linked to the parent catalog"
        )

    @mock.patch("validation.validate_graph.validate_paths")
    @mock.patch("scripts.build_graph.build_graph_delta")
    def test_auto_delta_promotes_candidate_and_preserves_previous(
        self, mocked_delta, mocked_validate
    ) -> None:
        fixture = self._generation_fixture()
        self.addCleanup(fixture[0].cleanup)
        current_path, graph_path = fixture[1], fixture[3]

        def build_delta(_previous, _snapshot, candidate):
            Path(candidate).write_bytes(b"delta-graph")
            return {"Entity": {"added": 1, "deleted": 0, "changed": 0}}

        mocked_delta.side_effect = build_delta
        mocked_validate.return_value = json.dumps({"exact_check_count": 1})
        promote_validated_graph(str(current_path), str(graph_path), mode="auto")
        self.assertEqual(graph_path.read_bytes(), b"delta-graph")
        self.assertEqual(
            graph_path.with_name("graph.lbug.previous").read_bytes(), b"parent-graph"
        )
        conn = sqlite3.connect(current_path)
        row = conn.execute(
            "SELECT catalog_build_id,base_graph_build_id,build_mode,projection_version,status "
            "FROM graph_builds ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        self.assertEqual(
            row,
            (fixture[4], fixture[5], "delta", GRAPH_PROJECTION_VERSION, "active"),
        )

    def test_forced_delta_failure_preserves_graph_files(self) -> None:
        fixture = self._generation_fixture()
        self.addCleanup(fixture[0].cleanup)
        current_path, previous_path, graph_path = fixture[1], fixture[2], fixture[3]
        previous_path.unlink()
        previous_graph = graph_path.with_name("graph.lbug.previous")
        previous_graph.write_bytes(b"older")
        with self.assertRaisesRegex(RuntimeError, "forced graph delta unavailable"):
            promote_validated_graph(str(current_path), str(graph_path), mode="delta")
        self.assertEqual(graph_path.read_bytes(), b"parent-graph")
        self.assertEqual(previous_graph.read_bytes(), b"older")
        conn = sqlite3.connect(current_path)
        self.assertEqual(
            conn.execute(
                "SELECT status FROM graph_builds ORDER BY id DESC LIMIT 1"
            ).fetchone()[0],
            "failed",
        )
        conn.close()

    @mock.patch("validation.validate_graph.validate_paths")
    @mock.patch("scripts.build_graph.build_graph")
    def test_projection_version_mismatch_auto_falls_back_full(
        self, mocked_full, mocked_validate
    ) -> None:
        fixture = self._generation_fixture()
        self.addCleanup(fixture[0].cleanup)
        current_path, graph_path = fixture[1], fixture[3]
        conn = sqlite3.connect(current_path)
        conn.execute(
            "UPDATE graph_builds SET projection_version=999 WHERE id=?", (fixture[5],)
        )
        conn.commit()
        conn.close()

        mocked_full.side_effect = lambda _snapshot, candidate: Path(
            candidate
        ).write_bytes(b"full-fallback")
        mocked_validate.return_value = json.dumps({"exact_check_count": 1})
        promote_validated_graph(str(current_path), str(graph_path), mode="auto")
        conn = sqlite3.connect(current_path)
        row = conn.execute(
            "SELECT build_mode,validation_summary FROM graph_builds ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], "full")
        self.assertIn("projection version mismatch", row[1])


if __name__ == "__main__":
    unittest.main()
