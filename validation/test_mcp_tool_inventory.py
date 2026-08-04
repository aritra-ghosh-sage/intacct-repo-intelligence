from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from intacct_mcp.server import CatalogState
from scripts import render_mcp_tool_inventory


ROOT = Path(__file__).resolve().parents[1]


class McpToolInventoryTests(unittest.TestCase):
    def test_render_is_current_and_never_opens_catalog(self) -> None:
        with mock.patch.object(CatalogState, "conn", side_effect=AssertionError("DB access")):
            rendered = render_mcp_tool_inventory.render()
        self.assertEqual((ROOT / "docs/mcp_tool_inventory.md").read_text(), rendered)
        self.assertIn("Public tool count: **25**", rendered)


if __name__ == "__main__":
    unittest.main()
