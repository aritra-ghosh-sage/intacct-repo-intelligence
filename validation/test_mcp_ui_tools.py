"""Focused MCP adapter tests for UI catalog query contracts."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from intacct_mcp.server import (
    CatalogState,
    ui_impact_impl,
    ui_surface_detail_impl,
)
from scripts.query_ui import UiQueryError

ROOT = Path(__file__).resolve().parents[1]


class McpUiToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "catalog.db"
        conn = sqlite3.connect(self.db_path)
        try:
            conn.executescript((ROOT / "catalog" / "schema.sql").read_text())
            conn.execute(
                "INSERT INTO repos(repo_key, local_root, tracked_branch) VALUES (?, ?, ?)",
                ("ia-main", "/repo/main", "main"),
            )
            conn.commit()
        finally:
            conn.close()
        self.state = CatalogState(self.db_path, Path(self.tempdir.name) / "graph.lbug")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_ui_impact_reuses_query_result_and_response_envelope(self) -> None:
        data = {
            "entity": {"id": 1, "name": "GLBatch", "occurrence_id": 2, "repo_key": "ia-main"},
            "surfaces": [],
            "page": {"next_cursor": "MQ", "truncated": True},
            "summary": {"surface_count": 0},
        }
        with mock.patch("intacct_mcp.server.query_ui_impact", return_value=data) as query:
            response = ui_impact_impl(self.state, "GLBatch", "ia-main", 1, None)

        self.assertEqual("ok", response["status"])
        self.assertEqual("ui_impact", response["operation"])
        self.assertEqual(data, response["data"])
        self.assertEqual({"next_cursor": "MQ", "truncated": True}, response["page"])
        query.assert_called_once_with(
            mock.ANY,
            entity_name="GLBatch",
            repo_key="ia-main",
            limit=1,
            cursor=None,
        )

    def test_ui_surface_detail_preserves_query_errors_as_catalog_envelopes(self) -> None:
        error = UiQueryError(
            "ui_surface_not_found",
            "UI surface not found: actionui:missing",
            surface_key="actionui:missing",
        )
        with mock.patch("intacct_mcp.server.query_ui_surface_detail", side_effect=error):
            response = ui_surface_detail_impl(
                self.state,
                "actionui:missing",
                "ia-main",
                "events",
            )

        self.assertEqual("error", response["status"])
        self.assertEqual("ui_surface_detail", response["operation"])
        self.assertEqual("ui_surface_not_found", response["error"]["code"])
        self.assertEqual(
            {"surface_key": "actionui:missing"}, response["error"]["details"]
        )


if __name__ == "__main__":
    unittest.main()
