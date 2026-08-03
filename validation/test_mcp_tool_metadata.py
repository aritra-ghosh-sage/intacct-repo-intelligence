from __future__ import annotations

import asyncio
import unittest
from typing import Any

from intacct_mcp.server import create_server

EXPECTED_TOOL_NAMES = {
    "api_registry",
    "api_surface",
    "catalog_risk_summary",
    "catalog_search",
    "catalog_status",
    "confidence_band_query",
    "entity_access_detail",
    "entity_context",
    "entity_test_coverage",
    "file_impact",
    "object_relationships",
    "openapi_file_dependencies",
    "provenance",
    "qa_impact",
    "relationship_query",
    "repository_list",
    "rest_coverage",
    "risk_detail",
    "security_dependency_chain",
    "security_surface",
    "symbol_references",
    "ui_impact",
    "ui_surface_detail",
    "workflow_context",
    "workflow_structure",
}


def _has_description(schema: dict[str, Any]) -> bool:
    if schema.get("description"):
        return True
    return any(
        isinstance(branch, dict) and bool(branch.get("description"))
        for branch in schema.get("anyOf", [])
    )


class McpToolMetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        mcp, _ = create_server()
        tools = asyncio.run(mcp.list_tools())
        cls.tools = {tool.name: tool.model_dump(by_alias=True) for tool in tools}

    def test_all_tools_are_described_and_marked_read_only(self) -> None:
        self.assertEqual(EXPECTED_TOOL_NAMES, set(self.tools))
        for name, tool in self.tools.items():
            with self.subTest(tool=name):
                self.assertTrue(tool["description"])
                self.assertEqual(
                    {
                        "readOnlyHint": True,
                        "destructiveHint": False,
                        "idempotentHint": True,
                        "openWorldHint": False,
                    },
                    {
                        key: tool["annotations"][key]
                        for key in (
                            "readOnlyHint",
                            "destructiveHint",
                            "idempotentHint",
                            "openWorldHint",
                        )
                    },
                )
                for parameter, schema in (
                    tool["inputSchema"].get("properties", {}).items()
                ):
                    self.assertTrue(
                        _has_description(schema),
                        f"{name}.{parameter} has no published description",
                    )

    def test_file_impact_schema_explains_repository_and_graph_inputs(self) -> None:
        schema = self.tools["file_impact"]["inputSchema"]
        self.assertEqual(["file_path"], schema["required"])

        file_path = schema["properties"]["file_path"]
        self.assertIn("repository-relative", file_path["description"])
        self.assertIn("catalog_search", file_path["description"])

        repo_key = schema["properties"]["repo_key"]["anyOf"][0]
        self.assertEqual(["ia-main", "ia-restapi-automation"], repo_key["examples"])
        self.assertIn("repository_list", repo_key["description"])

        depth = schema["properties"]["depth"]
        self.assertEqual(1, depth["minimum"])
        self.assertEqual(3, depth["maximum"])
        self.assertIn("Ladybug", depth["description"])

        edges = schema["properties"]["max_edges_per_symbol"]
        self.assertEqual(1, edges["minimum"])
        self.assertEqual(1000, edges["maximum"])

    def test_qa_impact_change_items_require_file_path(self) -> None:
        schema = self.tools["qa_impact"]["inputSchema"]
        self.assertEqual(["changes", "repo_key"], schema["required"])
        changes = schema["properties"]["changes"]
        self.assertEqual(1, changes["minItems"])

        change_schema = schema["$defs"]["QaChange"]
        self.assertEqual(["file_path"], change_schema["required"])
        self.assertIn("file_path", change_schema["properties"])

    def test_closed_value_sets_are_json_schema_enums(self) -> None:
        risk_category = self.tools["risk_detail"]["inputSchema"]["properties"][
            "category"
        ]
        self.assertIn("openapi_unknown_kind", risk_category["enum"])

        confidence_category = self.tools["confidence_band_query"]["inputSchema"][
            "properties"
        ]["category"]
        self.assertEqual(
            {
                "relationships",
                "entity_mappings",
                "workflows",
                "entity_roots",
            },
            set(confidence_category["enum"]),
        )

        eligibility = self.tools["entity_test_coverage"]["inputSchema"]["properties"][
            "eligibility"
        ]["anyOf"][0]
        self.assertEqual(
            {"active", "known_issue", "ci_only", "conditional"},
            set(eligibility["enum"]),
        )

    def test_ui_tools_publish_pagination_and_detail_contracts(self) -> None:
        self.assertEqual(25, len(self.tools))

        impact = self.tools["ui_impact"]["inputSchema"]
        self.assertEqual(["entity_name", "repo_key"], impact["required"])
        self.assertEqual(1, impact["properties"]["limit"]["minimum"])
        self.assertEqual(100, impact["properties"]["limit"]["maximum"])
        self.assertIn("repository_list", impact["properties"]["repo_key"]["description"])

        detail = self.tools["ui_surface_detail"]["inputSchema"]
        self.assertEqual(
            ["surface_key", "repo_key", "record_kind"], detail["required"]
        )
        self.assertEqual(
            {"artifacts", "fields", "events", "scripts", "includes", "references", "issues"},
            set(detail["properties"]["record_kind"]["enum"]),
        )
        self.assertIn("actionui:", detail["properties"]["surface_key"]["description"])
        self.assertEqual(1, detail["properties"]["limit"]["minimum"])
        self.assertEqual(100, detail["properties"]["limit"]["maximum"])

    def test_api_registry_publishes_operation_specific_evidence_contract(self) -> None:
        schema = self.tools["api_registry"]["inputSchema"]
        self.assertEqual(["operation", "repo_key"], schema["required"])
        self.assertEqual(
            {"releases", "resource", "file", "issues"},
            set(schema["properties"]["operation"]["enum"]),
        )
        self.assertEqual(1, schema["properties"]["limit"]["minimum"])
        self.assertEqual(100, schema["properties"]["limit"]["maximum"])
        self.assertIn("repository_list", schema["properties"]["repo_key"]["description"])
        self.assertIn("Registry source file", schema["properties"]["file_path"]["anyOf"][0]["description"])
        release = schema["properties"]["release"]["anyOf"][0]
        self.assertEqual({"V1", "Beta", "V2i"}, set(release["enum"]))


if __name__ == "__main__":
    unittest.main()
