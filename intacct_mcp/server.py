"""Local, read-only MCP server for the Intacct evidence catalog."""

from __future__ import annotations
import base64
import hashlib
import os
import sqlite3
from pathlib import Path
from typing import Any, Literal
from mcp.server.fastmcp import FastMCP
from config import CATALOG_DB, GRAPH_DB

DEFAULT_LIMIT, MAX_LIMIT = 25, 100


def _row(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def _limit(n: int) -> int:
    if not 1 <= n <= MAX_LIMIT:
        raise ValueError(f"limit must be 1..{MAX_LIMIT}")
    return n


def _offset(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        n = int(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("invalid cursor") from exc
    if n < 0:
        raise ValueError("invalid cursor")
    return n


def _cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode()).decode().rstrip("=")


class Catalog:
    def __init__(self, db: str = CATALOG_DB, graph: str = GRAPH_DB):
        self.db, self.graph = Path(db).resolve(), Path(graph).resolve()

    def conn(self) -> sqlite3.Connection:
        if not self.db.is_file():
            raise FileNotFoundError("catalog database is unavailable")
        c = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA query_only=ON")
        return c

    def table(self, c: sqlite3.Connection, name: str) -> bool:
        return bool(
            c.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
            ).fetchone()
        )

    def snapshot(self, c: sqlite3.Connection) -> dict[str, Any]:
        build = (
            c.execute(
                "SELECT id,status,source_fingerprint,started_at,completed_at FROM graph_builds WHERE status='active' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if self.table(c, "graph_builds")
            else None
        )
        return {
            "sqlite_sha256": hashlib.sha256(self.db.read_bytes()).hexdigest(),
            "graph_exists": self.graph.is_file(),
            "active_graph_build": _row(build) if build else None,
        }

    def out(
        self,
        operation: str,
        data: dict[str, Any],
        c: sqlite3.Connection,
        status="ok",
        error=None,
        next_cursor=None,
    ) -> dict[str, Any]:
        return {
            "contract_version": 1,
            "operation": operation,
            "status": status,
            "data": data,
            "snapshot": self.snapshot(c),
            "page": {"next_cursor": next_cursor, "truncated": bool(next_cursor)},
            "error": error,
        }

    def page(
        self, rows: list[sqlite3.Row], lim: int, off: int
    ) -> tuple[list[dict[str, Any]], str | None]:
        return [_row(x) for x in rows[:lim]], _cursor(off + lim) if len(
            rows
        ) > lim else None

    def search(
        self, query: str, kind: str, limit: int, cursor: str | None
    ) -> dict[str, Any]:
        if not query.strip():
            raise ValueError("query must not be empty")
        lim, off, like = _limit(limit), _offset(cursor), f"%{query.strip()}%"
        queries = {
            "entity": (
                "SELECT id,name,entity_type,confidence FROM entity_nodes WHERE name LIKE ? ORDER BY name,id",
                (like,),
            ),
            "file": (
                "SELECT id,path,language,size_bytes,sha1 FROM files WHERE path LIKE ? ORDER BY path,id",
                (like,),
            ),
            "symbol": (
                "SELECT s.id,s.name,s.kind,s.language,s.start_line,s.end_line,f.path file_path FROM symbols s JOIN files f ON f.id=s.file_id WHERE s.name LIKE ? ORDER BY s.name,s.id",
                (like,),
            ),
            "api": (
                "SELECT id,file_path,module,kind,canonical_name,resource_path,x_mapped_to FROM openapispec_index WHERE canonical_name LIKE ? OR resource_path LIKE ? ORDER BY file_path,id",
                (like, like),
            ),
            "workflow": (
                "SELECT w.id,w.name,w.workflow_type,w.source_file,e.name entity_name FROM workflows w JOIN entity_nodes e ON e.id=w.entity_id WHERE w.name LIKE ? OR e.name LIKE ? ORDER BY e.name,w.name,w.id",
                (like, like),
            ),
            "security": (
                "SELECT id,op_key,title,action,source_file,source_line FROM security_operations WHERE op_key LIKE ? OR title LIKE ? ORDER BY op_key,id",
                (like, like),
            ),
        }
        selected = queries if kind == "all" else {kind: queries[kind]}
        with self.conn() as c:
            records = []
            for label, (sql, args) in selected.items():
                records += [
                    {"kind": label, "record": _row(r)}
                    for r in c.execute(sql + " LIMIT ? OFFSET ?", (*args, lim + 1, off))
                ]
            records.sort(key=lambda r: (r["kind"], str(r["record"])))
            nxt = _cursor(off + lim) if len(records) > lim else None
            return self.out(
                "catalog_search", {"results": records[:lim]}, c, next_cursor=nxt
            )

    def entity(self, name: str) -> dict[str, Any]:
        with self.conn() as c:
            e = c.execute(
                "SELECT id,name,entity_type,confidence FROM entity_nodes WHERE name=? COLLATE NOCASE",
                (name,),
            ).fetchone()
            if not e:
                return self.out(
                    "entity_context",
                    {},
                    c,
                    "not_found",
                    {"code": "entity_not_found", "message": f"No entity named {name}"},
                )
            i = e["id"]
            data = {"entity": _row(e)}
            for key, sql in {
                "mappings": "SELECT em.id,em.mapping_type,em.confidence,em.source_text,s.id symbol_id,s.name symbol_name,f.path file_path,s.start_line,s.end_line FROM entity_mappings em LEFT JOIN symbols s ON s.id=em.symbol_id LEFT JOIN files f ON f.id=COALESCE(em.file_id,s.file_id) WHERE em.entity_id=? ORDER BY em.id",
                "roots": "SELECT er.id,er.role,er.weight,er.reason,er.is_shared,s.id symbol_id,s.name symbol_name,f.path file_path,s.start_line,s.end_line FROM entity_roots er JOIN symbols s ON s.id=er.symbol_id JOIN files f ON f.id=s.file_id WHERE er.entity_id=? ORDER BY er.weight DESC,er.id",
                "workflows": "SELECT id,name,workflow_type,source_kind,source_file,source_symbol_id,confidence,reason FROM workflows WHERE entity_id=? ORDER BY workflow_type,name,id",
                "rest_endpoints": "SELECT r.id,r.method,r.path,r.handler_symbol_id,f.path file_path FROM rest_endpoints r LEFT JOIN files f ON f.id=r.file_id WHERE r.entity_id=? ORDER BY r.path,r.method,r.id",
            }.items():
                data[key] = [_row(r) for r in c.execute(sql, (i,))]
            return self.out("entity_context", data, c)

    def records(
        self,
        operation: str,
        sql: str,
        args: tuple[Any, ...],
        limit: int,
        cursor: str | None,
        key: str,
    ) -> dict[str, Any]:
        lim, off = _limit(limit), _offset(cursor)
        with self.conn() as c:
            rows = c.execute(
                sql + " LIMIT ? OFFSET ?", (*args, lim + 1, off)
            ).fetchall()
            data, nxt = self.page(rows, lim, off)
            return self.out(operation, {key: data}, c, next_cursor=nxt)

    def provenance(self, kind: str, ident: int) -> dict[str, Any]:
        q = {
            "file": "SELECT id,path,language,size_bytes,sha1,last_indexed FROM files WHERE id=?",
            "symbol": "SELECT s.id,s.name,s.kind,s.language,s.start_line,s.end_line,s.signature,f.id file_id,f.path file_path FROM symbols s JOIN files f ON f.id=s.file_id WHERE s.id=?",
            "relationship": "SELECT id,source_symbol_id,target_symbol_id,relationship_type,file_path,confidence,evidence,resolution_class,resolution_reason,extractor,created_at FROM relationships WHERE id=?",
            "entity_mapping": "SELECT em.id,em.mapping_type,em.confidence,em.source_text,e.name entity_name,s.id symbol_id,f.path file_path,s.start_line,s.end_line FROM entity_mappings em JOIN entity_nodes e ON e.id=em.entity_id LEFT JOIN symbols s ON s.id=em.symbol_id LEFT JOIN files f ON f.id=COALESCE(em.file_id,s.file_id) WHERE em.id=?",
            "workflow": "SELECT w.id,w.name,w.workflow_type,w.source_kind,w.source_file,w.source_symbol_id,w.confidence,w.reason,e.name entity_name FROM workflows w JOIN entity_nodes e ON e.id=w.entity_id WHERE w.id=?",
            "rest_endpoint": "SELECT r.id,r.method,r.path,e.name entity_name,r.handler_symbol_id,f.path file_path FROM rest_endpoints r LEFT JOIN entity_nodes e ON e.id=r.entity_id LEFT JOIN files f ON f.id=r.file_id WHERE r.id=?",
            "security_operation": "SELECT id,op_key,title,action,source_file,source_line,source_kind,raw_hash FROM security_operations WHERE id=?",
        }[kind]
        with self.conn() as c:
            r = c.execute(q, (ident,)).fetchone()
            return self.out(
                "provenance",
                {"record_type": kind, "evidence": _row(r)} if r else {},
                c,
                "ok" if r else "not_found",
                None
                if r
                else {
                    "code": "record_not_found",
                    "message": f"No {kind} record {ident}",
                },
            )

    def graph_state(self, c):
        return (
            self.graph.is_file()
            and self.table(c, "graph_builds")
            and bool(
                c.execute("SELECT 1 FROM graph_builds WHERE status='active'").fetchone()
            )
        )

    def usages(self, name: str | None, ident: int | None) -> dict[str, Any]:
        with self.conn() as c:
            hits = c.execute(
                "SELECT s.id,s.name,s.kind,f.path file_path,s.start_line,s.end_line FROM symbols s JOIN files f ON f.id=s.file_id WHERE (? IS NOT NULL AND s.id=?) OR (? IS NULL AND s.name=?) ORDER BY s.id",
                (ident, ident, ident, name),
            ).fetchall()
            if not hits:
                return self.out(
                    "symbol_references",
                    {},
                    c,
                    "not_found",
                    {"code": "symbol_not_found", "message": "Symbol not found"},
                )
            if ident is None and len(hits) > 1:
                return self.out(
                    "symbol_references",
                    {"candidates": [_row(r) for r in hits]},
                    c,
                    "ambiguous",
                    {"code": "ambiguous_symbol", "message": "Retry with symbol_id"},
                )
            if not self.graph_state(c):
                return self.out(
                    "symbol_references",
                    {"target": _row(hits[0])},
                    c,
                    "graph_unavailable",
                    {"code": "graph_unavailable", "message": "No active Ladybug graph"},
                )
            from scripts.query_graph import (
                _query_symbol_usages,
                enrich_symbols_from_sql,
                get_graph_connection,
            )

            db, g = get_graph_connection(str(self.graph))
            try:
                x = _query_symbol_usages(g, int(hits[0]["id"]))
                both = x["callers"] + x["referencers"]
                y = enrich_symbols_from_sql(c, both)
                return self.out(
                    "symbol_references",
                    {
                        "target": _row(hits[0]),
                        "callers": y[: len(x["callers"])],
                        "referencers": y[len(x["callers"]) :],
                    },
                    c,
                )
            finally:
                g.close()
                db.close()

    def status(self):
        with self.conn() as c:
            return self.out(
                "catalog_status",
                {
                    "counts": {
                        t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                        for t in (
                            "files",
                            "symbols",
                            "relationships",
                            "entity_nodes",
                            "workflows",
                            "rest_endpoints",
                        )
                        if self.table(c, t)
                    }
                },
                c,
            )


def create_server(db_path: str | None = None, graph_path: str | None = None) -> FastMCP:
    cat = Catalog(
        db_path or os.getenv("CATALOG_DB", CATALOG_DB),
        graph_path or os.getenv("GRAPH_DB", GRAPH_DB),
    )
    s = FastMCP(
        "intacct-catalog",
        instructions="Read-only evidence-first catalog. Search first, cite returned source paths, line ranges, record IDs and confidence; do not infer missing evidence.",
    )

    @s.tool()
    def catalog_search(
        query: str,
        kind: Literal[
            "all", "entity", "file", "symbol", "api", "workflow", "security"
        ] = "all",
        limit: int = DEFAULT_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        return cat.search(query, kind, limit, cursor)

    @s.tool()
    def entity_context(entity_name: str) -> dict[str, Any]:
        return cat.entity(entity_name)

    @s.tool()
    def relationship_query(
        name: str,
        direction: Literal["outgoing", "incoming"],
        resolution_classes: list[str] | None = None,
        limit: int = DEFAULT_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        column = "source_name" if direction == "outgoing" else "target_name"
        sql = f"SELECT id,source_symbol_id,source_name,source_kind,target_symbol_id,target_name,target_kind,relationship_type,file_path,language,confidence,evidence,resolution_class,resolution_reason,extractor FROM relationships WHERE {column}=?"
        args = [name]
        if resolution_classes:
            sql += (
                " AND resolution_class IN ("
                + ",".join("?" for _ in resolution_classes)
                + ")"
            )
            args += resolution_classes
        return cat.records(
            "relationship_query",
            sql + " ORDER BY id",
            tuple(args),
            limit,
            cursor,
            "relationships",
        )

    @s.tool()
    def api_surface(
        entity_name: str | None = None,
        path_fragment: str | None = None,
        limit: int = DEFAULT_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        if not entity_name and not path_fragment:
            raise ValueError("entity_name or path_fragment is required")
        sql = "SELECT r.id,r.method,r.path,e.id entity_id,e.name entity_name,r.handler_symbol_id,f.path file_path FROM rest_endpoints r LEFT JOIN entity_nodes e ON e.id=r.entity_id LEFT JOIN files f ON f.id=r.file_id WHERE 1=1"
        args = []
        if entity_name:
            sql += " AND e.name=? COLLATE NOCASE"
            args.append(entity_name)
        if path_fragment:
            sql += " AND r.path LIKE ?"
            args.append("%" + path_fragment + "%")
        return cat.records(
            "api_surface",
            sql + " ORDER BY r.path,r.method,r.id",
            tuple(args),
            limit,
            cursor,
            "endpoints",
        )

    @s.tool()
    def workflow_context(
        entity_name: str, workflow_type: str | None = None
    ) -> dict[str, Any]:
        sql = "SELECT w.id,w.name,w.workflow_type,w.source_kind,w.source_file,w.source_symbol_id,w.confidence,w.reason FROM workflows w JOIN entity_nodes e ON e.id=w.entity_id WHERE e.name=? COLLATE NOCASE"
        args = [entity_name]
        if workflow_type:
            sql += " AND w.workflow_type=?"
            args.append(workflow_type)
        return cat.records(
            "workflow_context",
            sql + " ORDER BY w.workflow_type,w.name,w.id",
            tuple(args),
            DEFAULT_LIMIT,
            None,
            "workflows",
        )

    @s.tool()
    def security_surface(
        key_fragment: str, limit: int = DEFAULT_LIMIT, cursor: str | None = None
    ) -> dict[str, Any]:
        return cat.records(
            "security_surface",
            "SELECT id,op_key,op_numeric_id,title,action,script,source_file,source_line,source_kind FROM security_operations WHERE op_key LIKE ? OR title LIKE ? ORDER BY op_key,id",
            ("%" + key_fragment + "%", "%" + key_fragment + "%"),
            limit,
            cursor,
            "operations",
        )

    @s.tool()
    def symbol_references(
        symbol_name: str | None = None, symbol_id: int | None = None
    ) -> dict[str, Any]:
        return cat.usages(symbol_name, symbol_id)

    @s.tool()
    def file_impact(
        file_path: str, depth: int = 1, max_edges_per_symbol: int = 25
    ) -> dict[str, Any]:
        if not 1 <= depth <= 3 or not 1 <= max_edges_per_symbol <= 1000:
            raise ValueError("depth must be 1..3 and max_edges_per_symbol 1..1000")
        with cat.conn() as c:
            f = c.execute(
                "SELECT id,path FROM files WHERE path=?", (file_path,)
            ).fetchone()
            if not f:
                return cat.out(
                    "file_impact",
                    {},
                    c,
                    "not_found",
                    {
                        "code": "file_not_found",
                        "message": f"File not found: {file_path}",
                    },
                )
            if not cat.graph_state(c):
                return cat.out(
                    "file_impact",
                    {"file": _row(f)},
                    c,
                    "graph_unavailable",
                    {"code": "graph_unavailable", "message": "No active Ladybug graph"},
                )
            from scripts.query_graph import (
                _query_bounded_incoming_traversal,
                _query_file_symbols_from_graph,
                enrich_symbols_from_sql,
                get_graph_connection,
            )

            db, g = get_graph_connection(str(cat.graph))
            try:
                seeds = _query_file_symbols_from_graph(g, file_path)
                nodes, edges = _query_bounded_incoming_traversal(
                    g, [x["symbol_id"] for x in seeds], depth, max_edges_per_symbol
                )
                allnodes = [{**x, "depth": 0, "is_seed": True} for x in seeds] + nodes
                return cat.out(
                    "file_impact",
                    {
                        "file": _row(f),
                        "seed_symbols": enrich_symbols_from_sql(
                            c, allnodes[: len(seeds)]
                        ),
                        "affected_symbols": enrich_symbols_from_sql(c, allnodes),
                        "traversal": {
                            "depth": depth,
                            "max_edges_per_symbol": max_edges_per_symbol,
                            "edges": edges,
                        },
                    },
                    c,
                )
            finally:
                g.close()
                db.close()

    @s.tool()
    def provenance(
        record_type: Literal[
            "file",
            "symbol",
            "relationship",
            "entity_mapping",
            "workflow",
            "rest_endpoint",
            "security_operation",
        ],
        record_id: int,
    ) -> dict[str, Any]:
        return cat.provenance(record_type, record_id)

    @s.tool()
    def catalog_status() -> dict[str, Any]:
        return cat.status()

    @s.resource("catalog://schema")
    def schema() -> str:
        return (
            Path(__file__)
            .resolve()
            .parents[1]
            .joinpath("catalog/schema.sql")
            .read_text()
        )

    @s.resource("catalog://snapshot")
    def snapshot() -> str:
        with cat.conn() as c:
            return str(cat.snapshot(c))

    return s


mcp = create_server()
if __name__ == "__main__":
    transport = os.getenv("MCP_TRANSPORT", "streamable-http")
    if transport not in {"stdio", "sse", "streamable-http"}:
        raise ValueError("MCP_TRANSPORT must be stdio, sse, or streamable-http")
    mcp.run(transport=transport)
