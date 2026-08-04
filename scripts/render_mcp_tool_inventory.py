#!/usr/bin/env python3
"""Render the live public MCP tool contract without opening a catalog database."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

try:
    from intacct_mcp.server import create_server
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from intacct_mcp.server import create_server


DEFAULT_OUTPUT = Path("docs/mcp_tool_inventory.md")


def _markdown(tools: list[Any]) -> str:
    rendered = ["# MCP Tool Inventory", "", "Generated from the live FastMCP registration. Do not edit by hand.", "", f"Public tool count: **{len(tools)}**", ""]
    for tool in sorted(tools, key=lambda item: item.name):
        payload = tool.model_dump(by_alias=True)
        rendered.extend(
            [
                f"## `{payload['name']}`",
                "",
                payload.get("description") or "No description published.",
                "",
                "Annotations:",
                "",
                "```json",
                json.dumps(payload.get("annotations", {}), sort_keys=True, indent=2),
                "```",
                "",
                "Input schema:",
                "",
                "```json",
                json.dumps(payload.get("inputSchema", {}), sort_keys=True, indent=2),
                "```",
                "",
            ]
        )
    return "\n".join(rendered)


def render() -> str:
    """Return the registered schema.  Server construction is deliberately lazy."""

    server, _state = create_server()
    return _markdown(asyncio.run(server.list_tools()))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true", help="fail if generated output is stale")
    args = parser.parse_args()
    expected = render()
    if args.check:
        actual = args.output.read_text(encoding="utf-8") if args.output.is_file() else ""
        if actual != expected:
            print(f"stale MCP inventory: {args.output}")
            return 1
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(expected, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
