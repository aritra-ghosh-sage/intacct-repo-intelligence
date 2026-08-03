"""Strict freshness checks for the derived Ladybug graph.

The graph is a cache of one *catalog generation*, not merely a file that
happens to exist.  Keep this contract independent of MCP and Ladybug so every
graph consumer can reject stale evidence before opening the graph database.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from catalog.content_fingerprint import logical_content_fingerprint


@dataclass(frozen=True)
class GraphFreshness:
    """Result of checking whether one graph exactly represents one catalog."""

    fresh: bool
    reason: str
    catalog_build: dict[str, Any] | None = None
    graph_build: dict[str, Any] | None = None


def canonical_path(path: str | Path) -> str:
    """Return the canonical absolute identity used by promotion metadata."""

    return str(Path(path).expanduser().resolve())


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')}


def graph_freshness(
    conn: sqlite3.Connection,
    *,
    catalog_path: str | Path,
    graph_path: str | Path,
    projection_version: int,
) -> GraphFreshness:
    """Require the active graph to match the active catalog generation exactly.

    This deliberately recomputes the logical catalog fingerprint.  Comparing
    metadata alone lets a manually modified SQLite file masquerade as the
    generation from which the graph was built.
    """

    if not _has_table(conn, "catalog_builds"):
        return GraphFreshness(False, "catalog build metadata is unavailable")
    if not _has_table(conn, "graph_builds"):
        return GraphFreshness(False, "graph build metadata is unavailable")

    required_catalog = {
        "id",
        "status",
        "content_fingerprint",
    }
    required_graph = {
        "id",
        "status",
        "graph_path",
        "source_db",
        "source_fingerprint",
        "catalog_build_id",
        "projection_version",
    }
    if not required_catalog.issubset(_columns(conn, "catalog_builds")):
        return GraphFreshness(False, "catalog generation metadata is incomplete")
    if not required_graph.issubset(_columns(conn, "graph_builds")):
        return GraphFreshness(False, "graph generation metadata is incomplete")

    catalog = conn.execute(
        "SELECT * FROM catalog_builds WHERE status='active' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if catalog is None:
        return GraphFreshness(False, "active catalog build metadata is unavailable")
    catalog_dict = dict(catalog)
    logical_fingerprint = logical_content_fingerprint(conn)
    if catalog["content_fingerprint"] != logical_fingerprint:
        return GraphFreshness(
            False,
            "active catalog logical fingerprint mismatch",
            catalog_dict,
        )

    canonical_graph = canonical_path(graph_path)
    graph = conn.execute(
        """SELECT * FROM graph_builds
           WHERE status='active' AND graph_path=?
           ORDER BY id DESC LIMIT 1""",
        (canonical_graph,),
    ).fetchone()
    if graph is None:
        return GraphFreshness(
            False,
            "active graph build metadata is unavailable for configured graph path",
            catalog_dict,
        )
    graph_dict = dict(graph)
    if not Path(canonical_graph).is_file():
        return GraphFreshness(False, "active graph file is unavailable", catalog_dict, graph_dict)
    if canonical_path(catalog_path) != canonical_path(graph["source_db"]):
        return GraphFreshness(
            False, "active graph source database identity mismatch", catalog_dict, graph_dict
        )
    if int(graph["catalog_build_id"] or -1) != int(catalog["id"]):
        return GraphFreshness(
            False, "active graph catalog generation mismatch", catalog_dict, graph_dict
        )
    if graph["source_fingerprint"] != logical_fingerprint:
        return GraphFreshness(
            False, "active graph logical fingerprint mismatch", catalog_dict, graph_dict
        )
    if int(graph["projection_version"] or -1) != projection_version:
        return GraphFreshness(
            False, "active graph projection version mismatch", catalog_dict, graph_dict
        )
    return GraphFreshness(True, "fresh", catalog_dict, graph_dict)


def require_fresh_graph(
    conn: sqlite3.Connection,
    *,
    catalog_path: str | Path,
    graph_path: str | Path,
    projection_version: int,
) -> GraphFreshness:
    """Return a fresh graph state or raise before any Ladybug operation."""

    freshness = graph_freshness(
        conn,
        catalog_path=catalog_path,
        graph_path=graph_path,
        projection_version=projection_version,
    )
    if not freshness.fresh:
        raise RuntimeError(f"graph is stale: {freshness.reason}")
    return freshness
