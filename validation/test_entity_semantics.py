"""Focused regression coverage for static .ent semantic extraction and MCP output."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from intacct_mcp.server import (
    CatalogState,
    create_server,
    object_relationships_impl,
    qa_impact_impl,
)
from scripts.build_entity_semantics import build
from scripts.build_graph import build_graph
from scripts.query_graph import get_graph_connection, query_semantic_relationship_traversal
from validation.validate_graph import validate_paths


ROOT = Path(__file__).resolve().parents[1]


class EntitySemanticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name) / "source"
        self.root.mkdir()
        self.db = Path(self.tempdir.name) / "catalog.db"
        conn = sqlite3.connect(self.db)
        conn.executescript((ROOT / "catalog" / "schema.sql").read_text())
        repo_id = conn.execute(
            "INSERT INTO repos(repo_key,local_root,tracked_branch) VALUES ('ia-main',?, 'main')",
            (str(self.root),),
        ).lastrowid
        self.repo_id = int(repo_id)
        self._add_entity(conn, "Customer", "app/source/apar/customer.ent", self.customer_source())
        self._add_entity(conn, "Location", "app/source/company/location.ent", self.location_source())
        self._add_entity(conn, "GLBatch", "app/source/gl/glbatch.ent", self.batch_source())
        self._add_entity(conn, "GLEntry", "app/source/gl/glentry.ent", self.entry_source())
        conn.commit()
        conn.close()
        self.result = build(str(self.db), self.root, "ia-main")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _add_entity(self, conn: sqlite3.Connection, name: str, path: str, content: str) -> None:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        entity_id = conn.execute(
            "INSERT INTO entity_nodes(name,entity_type) VALUES (?, 'ent')", (name,)
        ).lastrowid
        conn.execute(
            "INSERT INTO entity_occurrences(repo_id,entity_id,ent_file,module,extractor,confidence) "
            "VALUES (?,?,?,?, 'fixture', 1.0)",
            (self.repo_id, entity_id, path, "fixture"),
        )

    @staticmethod
    def customer_source() -> str:
        return """<?php
return array(
 'ownedobjects' => array(array('entity' => 'Contact', 'path' => 'contactList')),
 'parent' => array('table' => 'Customer'),
 'fieldinfo' => array(
   array('path' => 'PARENT', 'entity' => 'Customer', 'fullname' => 'Parent customer'),
   array('path' => 'OBJECTRESTRICTION', 'validvalues' => array('Unrestricted', 'RootOnly', 'Restricted')),
   array('path' => 'ENTITY')
 ),
 'PERMISSION_READ' => 'NONE',
 'PERMISSION_CREATE' => 'CREATE',
 'PERMISSION_UPDATE' => $dynamicPermission,
 'PERMISSION_DELETE' => 'NONE',
 'PERMISSION_APPROVE' => 'APPROVE',
 'PERMISSION_SUBMIT' => $submitPermission,
 'PERMISSION_DECLINE' => 'DECLINE'
);
"""

    @staticmethod
    def location_source() -> str:
        return """<?php
return array(
 'parent' => array('table' => 'Location'),
 'showhierarchy' => true,
 'fieldinfo' => array(
   array('path' => 'PARENT', 'entity' => 'Location', 'fullname' => 'Parent location'),
   array('path' => 'ENTITY')
 )
);
"""

    @staticmethod
    def batch_source() -> str:
        return """<?php
return array('ownedobjects' => array(array('entity' => 'GLEntry', 'path' => 'entries')));
"""

    @staticmethod
    def entry_source() -> str:
        return """<?php
return array(
 'parententity' => 'GLBatch',
 'fieldinfo' => array(array('path' => 'PARENTENTRY', 'entityContext' => true)),
 'PERMISSION_CREATE' => 'CREATE'
);
$kSchemas['glentry']['ownedobjects'][] = array('entity' => 'TaxEntry', 'path' => 'taxEntries');
"""

    def test_extracts_independent_axes_without_conflation(self) -> None:
        self.assertEqual(self.result["failed"], 0)
        conn = sqlite3.connect(self.db)
        rows = conn.execute(
            "SELECT en.name,rf.axis,rf.relation_kind,rf.assertion_status "
            "FROM entity_relationship_facts rf "
            "JOIN entity_occurrences eo ON eo.id=rf.source_occurrence_id "
            "JOIN entity_nodes en ON en.id=eo.entity_id ORDER BY en.name,rf.axis,rf.id"
        ).fetchall()
        observed = set(rows)
        self.assertIn(("Customer", "A", "owns_collection", "VERIFIED"), observed)
        self.assertIn(("Customer", "B", "business_parent_reference", "CORROBORATED"), observed)
        self.assertIn(("Customer", "D", "visibility_enum", "VERIFIED"), observed)
        self.assertIn(("Customer", "E", "entity_context_field", "VERIFIED"), observed)
        self.assertIn(("Location", "C", "location_parent_reference", "CORROBORATED"), observed)
        self.assertIn(("GLBatch", "A", "owns_collection", "VERIFIED"), observed)
        self.assertIn(("GLEntry", "A", "owned_by_parent_entity", "VERIFIED"), observed)
        self.assertIn(("GLEntry", "A", "owns_collection", "VERIFIED"), observed)
        self.assertIn(("GLEntry", "B", "business_parent_reference", "UNRESOLVED"), observed)
        self.assertIn(("GLEntry", "E", "entity_context_metadata", "VERIFIED"), observed)
        self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        conn.close()

    def test_mcp_returns_provenance_and_explicit_coverage_gap(self) -> None:
        state = CatalogState(self.db, Path(self.tempdir.name) / "graph.lbug")
        object_result = object_relationships_impl(state, "Customer", repo_key="ia-main")
        self.assertEqual(object_result["status"], "ok")
        axes = object_result["data"]["axes"]
        self.assertEqual(axes["B"]["status"], "CORROBORATED")
        self.assertEqual(axes["D"]["status"], "VERIFIED")
        self.assertTrue(axes["B"]["facts"][0]["source_path"])

        entry_result = object_relationships_impl(state, "GLEntry", repo_key="ia-main")
        # D is not implemented as a closed-world family.  An empty result must
        # remain an investigation gap rather than a negative semantic claim.
        self.assertEqual(entry_result["data"]["axes"]["D"]["status"], "UNRESOLVED")
        self.assertTrue(any(
            row["declaration_family"] == "D" and row["status"] == "partial"
            for row in entry_result["data"]["coverage"]
        ))

        impact = qa_impact_impl(
            state,
            [{"file_path": "app/source/apar/customer.ent"}],
            repo_key="ia-main",
        )
        self.assertEqual(impact["status"], "ok")
        self.assertEqual(impact["data"]["seed_entity_occurrences"][0]["name"], "Customer")
        self.assertEqual(impact["data"]["coverage_gaps"][0]["kind"], "investigation_gap")
        self.assertTrue(impact["data"]["impacted_components"])
        self.assertTrue(any(
            item["risk"] == "unresolved" and item["component_type"] == "extraction_coverage"
            for item in impact["data"]["impacted_components"]
        ))
        self.assertEqual(impact["data"]["input_resolution"][0]["status"], "resolved")

    def test_permission_declarations_never_infer_allowed_access(self) -> None:
        conn = sqlite3.connect(self.db)
        rows = conn.execute(
            "SELECT operation,availability,qualifiers_json "
            "FROM entity_operation_facts op "
            "JOIN entity_occurrences eo ON eo.id=op.occurrence_id "
            "JOIN entity_nodes en ON en.id=eo.entity_id "
            "WHERE en.name='Customer' ORDER BY operation"
        ).fetchall()
        conn.close()
        operations = {
            operation: (availability, json.loads(qualifiers))
            for operation, availability, qualifiers in rows
        }
        self.assertEqual(operations["create"][0], "unresolved")
        self.assertEqual(
            operations["create"][1]["permission_requirement"], "CREATE"
        )
        self.assertEqual(operations["read"][0], "denied")
        self.assertEqual(operations["read"][1]["permission_requirement"], "NONE")
        self.assertEqual(operations["update"][0], "unresolved")
        self.assertEqual(
            operations["update"][1]["declaration_kind"], "dynamic"
        )
        self.assertEqual(operations["delete"][0], "denied")
        self.assertEqual(operations["approve"][0], "unresolved")
        self.assertEqual(operations["submit"][0], "unresolved")
        self.assertEqual(operations["decline"][0], "unresolved")
        self.assertNotIn("allowed", {row[0] for row in operations.values()})

    def test_semantic_rebuild_rolls_back_reset_on_unexpected_failure(self) -> None:
        conn = sqlite3.connect(self.db)
        before = {
            table: conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE repo_id=?", (self.repo_id,)
            ).fetchone()[0]
            for table in (
                "entity_schema_components",
                "entity_relationship_facts",
                "entity_operation_facts",
                "entity_extraction_coverage",
            )
        }
        conn.close()
        with mock.patch(
            "scripts.build_entity_semantics._extract_occurrence",
            side_effect=RuntimeError("forced extraction failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "forced extraction failure"):
                build(str(self.db), self.root, "ia-main")
        conn = sqlite3.connect(self.db)
        after = {
            table: conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE repo_id=?", (self.repo_id,)
            ).fetchone()[0]
            for table in before
        }
        conn.close()
        self.assertEqual(after, before)

    def test_mcp_filters_operation_axes_and_resolves_mapped_files(self) -> None:
        state = CatalogState(self.db, Path(self.tempdir.name) / "graph.lbug")
        result = object_relationships_impl(
            state,
            "Customer",
            repo_key="ia-main",
            axes=["A"],
            include=["operations"],
        )
        self.assertEqual(result["data"]["operations"], [])

        conn = sqlite3.connect(self.db)
        file_id = conn.execute(
            "INSERT INTO files(repo_id,path,language) VALUES (?,?,?)",
            (self.repo_id, "app/source/apar/CustomerManager.cls", "php"),
        ).lastrowid
        symbol_id = conn.execute(
            "INSERT INTO symbols(file_id,name,kind,language) VALUES (?,?,?,?)",
            (file_id, "CustomerManager", "class", "php"),
        ).lastrowid
        customer_id = conn.execute(
            "SELECT id FROM entity_nodes WHERE name='Customer'"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO entity_mappings(repo_id,entity_id,symbol_id,file_id,"
            "mapping_type,confidence) VALUES (?,?,?,?,?,?)",
            (
                self.repo_id,
                customer_id,
                symbol_id,
                file_id,
                "manager",
                1.0,
            ),
        )
        conn.commit()
        conn.close()

        impact = qa_impact_impl(
            state,
            [{"file_path": "app/source/apar/CustomerManager.cls"}],
            repo_key="ia-main",
        )
        resolution = impact["data"]["input_resolution"][0]
        self.assertEqual(resolution["status"], "resolved")
        self.assertIn("entity_mapping_file", resolution["seed_sources"])
        self.assertIn("entity_mapping_symbol", resolution["seed_sources"])
        self.assertEqual(
            impact["data"]["seed_entity_occurrences"][0]["name"], "Customer"
        )

        unresolved = qa_impact_impl(
            state,
            [{"file_path": "app/source/apar/UnknownManager.cls"}],
            repo_key="ia-main",
        )
        self.assertEqual(unresolved["status"], "ok")
        self.assertEqual(
            unresolved["data"]["input_resolution"][0]["status"], "unresolved"
        )
        self.assertEqual(unresolved["data"]["seed_entity_occurrences"], [])
        self.assertEqual(
            unresolved["data"]["coverage_gaps"][0]["kind"], "investigation_gap"
        )

    def test_mcp_public_tool_schemas_remain_v1_compatible(self) -> None:
        server, _state = create_server(
            db_path=str(self.db),
            graph_path=str(Path(self.tempdir.name) / "graph.lbug"),
        )
        tools = {tool.name: tool for tool in server._tool_manager.list_tools()}
        self.assertIn("object_relationships", tools)
        self.assertIn("qa_impact", tools)
        self.assertEqual(
            tools["qa_impact"].parameters["required"], ["changes", "repo_key"]
        )
        response = qa_impact_impl(
            CatalogState(self.db, Path(self.tempdir.name) / "graph.lbug"),
            [{"file_path": "app/source/apar/customer.ent"}],
            repo_key="ia-main",
        )
        self.assertEqual(response["contract_version"], 1)
        self.assertIn("status", response)
        self.assertIn("data", response)
        self.assertIn("error", response)

    def test_repository_scoped_fks_and_test_coverage(self) -> None:
        conn = sqlite3.connect(self.db)
        conn.execute("PRAGMA foreign_keys=ON")
        repo_two = conn.execute(
            "INSERT INTO repos(repo_key,local_root,tracked_branch) "
            "VALUES ('other','/other','main')"
        ).lastrowid
        customer_id = conn.execute(
            "SELECT id FROM entity_nodes WHERE name='Customer'"
        ).fetchone()[0]
        other_occurrence = conn.execute(
            "INSERT INTO entity_occurrences(repo_id,entity_id,ent_file) "
            "VALUES (?,?,?)",
            (repo_two, customer_id, "other/customer.ent"),
        ).lastrowid
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                """INSERT INTO entity_schema_components(
                       repo_id,occurrence_id,component_kind,component_path,
                       source_path,evidence_text,evidence_hash,extractor,
                       extractor_version,confidence
                   ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    self.repo_id,
                    other_occurrence,
                    "field",
                    "bad",
                    "bad.ent",
                    "bad",
                    "bad",
                    "fixture",
                    "1",
                    1.0,
                ),
            )

        test_file = conn.execute(
            "INSERT INTO files(repo_id,path,language) VALUES (?,?,?)",
            (repo_two, "features/customer.feature", "gherkin"),
        ).lastrowid
        endpoint_file = conn.execute(
            "INSERT INTO files(repo_id,path,language) VALUES (?,?,?)",
            (repo_two, "openapi/customer.yaml", "yaml"),
        ).lastrowid
        endpoint_id = conn.execute(
            "INSERT INTO rest_endpoints(repo_id,method,path,entity_id,file_id) "
            "VALUES (?,?,?,?,?)",
            (repo_two, "POST", "/customer", customer_id, endpoint_file),
        ).lastrowid
        case_id = conn.execute(
            """INSERT INTO test_cases(
                   repo_id,file_id,feature_name,scenario_name,case_name,
                   feature_line,scenario_line
               ) VALUES (?,?,?,?,?,?,?)""",
            (repo_two, test_file, "Customer", "Other repo test", "case", 1, 2),
        ).lastrowid
        request_id = conn.execute(
            "INSERT INTO test_requests(test_case_id,ordinal,step_line) "
            "VALUES (?,?,?)",
            (case_id, 1, 3),
        ).lastrowid
        conn.execute(
            "INSERT INTO test_entity_links(test_request_id,entity_id,rest_endpoint_id) "
            "VALUES (?,?,?)",
            (request_id, customer_id, endpoint_id),
        )
        conn.commit()
        conn.close()

        impact = qa_impact_impl(
            CatalogState(self.db, Path(self.tempdir.name) / "graph.lbug"),
            [{"file_path": "app/source/apar/customer.ent"}],
            repo_key="ia-main",
        )
        self.assertEqual(impact["data"]["surfaces"]["tests"], [])
        self.assertTrue(any(
            gap.get("entity_name") == "Customer"
            for gap in impact["data"]["coverage_gaps"]
        ))

    def test_semantic_graph_projection_builds_and_traverses(self) -> None:
        graph_path = Path(self.tempdir.name) / "semantic.lbug"
        build_graph(str(self.db), str(graph_path))
        validate_paths(str(self.db), str(graph_path))
        db, graph = get_graph_connection(str(graph_path))
        try:
            conn = sqlite3.connect(self.db)
            customer_occurrence = conn.execute(
                "SELECT eo.id FROM entity_occurrences eo JOIN entity_nodes en ON en.id=eo.entity_id "
                "WHERE en.name='Customer'"
            ).fetchone()[0]
            conn.close()
            rows = query_semantic_relationship_traversal(
                graph, int(customer_occurrence), ["A", "B", "C", "D", "E"], 1
            )
            self.assertTrue(rows)
            conn = sqlite3.connect(self.db)
            entry_occurrence = conn.execute(
                "SELECT eo.id FROM entity_occurrences eo "
                "JOIN entity_nodes en ON en.id=eo.entity_id WHERE en.name='GLEntry'"
            ).fetchone()[0]
            batch_occurrence = conn.execute(
                "SELECT eo.id FROM entity_occurrences eo "
                "JOIN entity_nodes en ON en.id=eo.entity_id WHERE en.name='GLBatch'"
            ).fetchone()[0]
            conn.close()
            unresolved = query_semantic_relationship_traversal(
                graph, int(entry_occurrence), ["B"], 3
            )
            self.assertEqual(unresolved, [])
            traversed = query_semantic_relationship_traversal(
                graph, int(batch_occurrence), ["A"], 3
            )
            self.assertEqual([row["depth"] for row in traversed], [1, 2])
            self.assertTrue(all(
                row["assertion_status"] in {"VERIFIED", "CORROBORATED"}
                for row in traversed
            ))
        finally:
            graph.close()
            db.close()


if __name__ == "__main__":
    unittest.main()
