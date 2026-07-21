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
        sqlite_sha256 = hashlib.sha256(self.db.read_bytes()).hexdigest()
        repositories = (
            [_row(row) for row in c.execute(
                "SELECT repo_key,tracked_branch,indexed_commit_sha,last_scanned_at,last_built_at,index_status,diagnostic_error,last_attempt_status,last_attempted_at,last_attempt_error FROM repos ORDER BY repo_key"
            )]
            if self.table(c, "repos")
            else []
        )
        return {
            "sqlite_sha256": sqlite_sha256,
            "graph_exists": self.graph.is_file(),
            "active_graph_build": _row(build) if build else None,
            "graph_fresh": bool(build and build["source_fingerprint"] == sqlite_sha256),
            "repositories": repositories,
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
        self, query: str, kind: str, limit: int, cursor: str | None, repo_key: str | None = None
    ) -> dict[str, Any]:
        if not query.strip():
            raise ValueError("query must not be empty")
        lim, off, like = _limit(limit), _offset(cursor), f"%{query.strip()}%"
        queries = {
            "entity": (
                "SELECT e.id,e.name,e.entity_type,e.confidence,r.repo_key,eo.ent_file,eo.module,eo.table_name,eo.view_name,eo.dummy,eo.source_file_id,eo.extractor,eo.confidence occurrence_confidence FROM entity_nodes e JOIN entity_occurrences eo ON eo.entity_id=e.id JOIN repos r ON r.id=eo.repo_id WHERE e.name LIKE ? AND (? IS NULL OR r.repo_key=?) ORDER BY e.name,r.repo_key,e.id",
                (like, repo_key, repo_key),
            ),
            "file": (
                "SELECT f.id,r.repo_key,f.path,f.language,f.size_bytes,f.sha1 FROM files f JOIN repos r ON r.id=f.repo_id WHERE f.path LIKE ? AND (? IS NULL OR r.repo_key=?) ORDER BY r.repo_key,f.path,f.id",
                (like, repo_key, repo_key),
            ),
            "symbol": (
                "SELECT s.id,s.name,s.kind,s.language,s.start_line,s.end_line,r.repo_key,f.path file_path FROM symbols s JOIN files f ON f.id=s.file_id JOIN repos r ON r.id=f.repo_id WHERE s.name LIKE ? AND (? IS NULL OR r.repo_key=?) ORDER BY s.name,r.repo_key,s.id",
                (like, repo_key, repo_key),
            ),
            "api": (
                "SELECT o.id,r.repo_key,o.file_path,o.module,o.kind,o.canonical_name,o.resource_path,o.x_mapped_to FROM openapispec_index o JOIN repos r ON r.id=o.repo_id WHERE (o.canonical_name LIKE ? OR o.resource_path LIKE ?) AND (? IS NULL OR r.repo_key=?) ORDER BY r.repo_key,o.file_path,o.id",
                (like, like, repo_key, repo_key),
            ),
            "workflow": (
                "SELECT w.id,r.repo_key,w.name,w.workflow_type,w.source_file,e.name entity_name FROM workflows w JOIN entity_nodes e ON e.id=w.entity_id JOIN repos r ON r.id=w.repo_id WHERE (w.name LIKE ? OR e.name LIKE ?) AND (? IS NULL OR r.repo_key=?) ORDER BY e.name,w.name,r.repo_key,w.id",
                (like, like, repo_key, repo_key),
            ),
            "security": (
                "SELECT o.id,r.repo_key,o.op_key,o.title,o.action,o.source_file,o.source_line FROM security_operations o JOIN repos r ON r.id=o.repo_id WHERE (o.op_key LIKE ? OR o.title LIKE ?) AND (? IS NULL OR r.repo_key=?) ORDER BY o.op_key,r.repo_key,o.id",
                (like, like, repo_key, repo_key),
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
                "catalog_search", {"repo_key": repo_key, "results": records[:lim]}, c, next_cursor=nxt
            )

    def entity(self, name: str, repo_key: str | None = None) -> dict[str, Any]:
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
            data["occurrences"] = [_row(r) for r in c.execute(
                "SELECT eo.id,r.repo_key,eo.ent_file,eo.module,eo.table_name,eo.view_name,eo.dummy,eo.source_file_id,eo.extractor,eo.confidence,eo.created_at,eo.updated_at FROM entity_occurrences eo JOIN repos r ON r.id=eo.repo_id WHERE eo.entity_id=? AND (? IS NULL OR r.repo_key=?) ORDER BY r.repo_key,eo.id",
                (i, repo_key, repo_key),
            )]
            for key, sql in {
                "mappings": "SELECT em.id,r.repo_key,em.mapping_type,em.confidence,em.source_text,s.id symbol_id,s.name symbol_name,f.path file_path,s.start_line,s.end_line FROM entity_mappings em JOIN repos r ON r.id=em.repo_id LEFT JOIN symbols s ON s.id=em.symbol_id LEFT JOIN files f ON f.id=COALESCE(em.file_id,s.file_id) WHERE em.entity_id=? AND (? IS NULL OR r.repo_key=?) ORDER BY r.repo_key,em.id",
                "roots": "SELECT er.id,r.repo_key,er.role,er.weight,er.reason,er.is_shared,s.id symbol_id,s.name symbol_name,f.path file_path,s.start_line,s.end_line FROM entity_roots er JOIN repos r ON r.id=er.repo_id JOIN symbols s ON s.id=er.symbol_id JOIN files f ON f.id=s.file_id WHERE er.entity_id=? AND (? IS NULL OR r.repo_key=?) ORDER BY r.repo_key,er.weight DESC,er.id",
                "workflows": "SELECT w.id,r.repo_key,w.name,w.workflow_type,w.source_kind,w.source_file,w.source_symbol_id,w.confidence,w.reason FROM workflows w JOIN repos r ON r.id=w.repo_id WHERE w.entity_id=? AND (? IS NULL OR r.repo_key=?) ORDER BY r.repo_key,w.workflow_type,w.name,w.id",
                "rest_endpoints": "SELECT ep.id,r.repo_key,ep.method,ep.path,ep.handler_symbol_id,f.path file_path FROM rest_endpoints ep JOIN repos r ON r.id=ep.repo_id LEFT JOIN files f ON f.id=ep.file_id WHERE ep.entity_id=? AND (? IS NULL OR r.repo_key=?) ORDER BY r.repo_key,ep.path,ep.method,ep.id",
            }.items():
                data[key] = [_row(r) for r in c.execute(sql, (i, repo_key, repo_key))]
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
            "file": "SELECT f.id,r.repo_key,r.tracked_branch,r.indexed_commit_sha,r.last_scanned_at,r.last_built_at,r.index_status,f.path,f.language,f.size_bytes,f.sha1,f.last_indexed FROM files f JOIN repos r ON r.id=f.repo_id WHERE f.id=?",
            "symbol": "SELECT s.id,s.name,s.kind,s.language,s.start_line,s.end_line,s.signature,f.id file_id,r.repo_key,r.tracked_branch,r.indexed_commit_sha,f.path file_path FROM symbols s JOIN files f ON f.id=s.file_id JOIN repos r ON r.id=f.repo_id WHERE s.id=?",
            "relationship": "SELECT rel.id,r.repo_key,r.tracked_branch,r.indexed_commit_sha,rel.source_symbol_id,rel.target_symbol_id,rel.relationship_type,rel.file_path,rel.confidence,rel.evidence,rel.resolution_class,rel.resolution_reason,rel.extractor,rel.created_at FROM relationships rel JOIN repos r ON r.id=rel.repo_id WHERE rel.id=?",
            "entity_mapping": "SELECT em.id,r.repo_key,em.mapping_type,em.confidence,em.source_text,e.name entity_name,eo.ent_file,eo.module,eo.table_name,eo.view_name,s.id symbol_id,f.path file_path,s.start_line,s.end_line FROM entity_mappings em JOIN repos r ON r.id=em.repo_id JOIN entity_nodes e ON e.id=em.entity_id LEFT JOIN entity_occurrences eo ON eo.repo_id=em.repo_id AND eo.entity_id=em.entity_id LEFT JOIN symbols s ON s.id=em.symbol_id LEFT JOIN files f ON f.id=COALESCE(em.file_id,s.file_id) WHERE em.id=?",
            "workflow": "SELECT w.id,r.repo_key,w.name,w.workflow_type,w.source_kind,w.source_file,w.source_symbol_id,w.confidence,w.reason,e.name entity_name,eo.ent_file,eo.module,eo.table_name,eo.view_name FROM workflows w JOIN repos r ON r.id=w.repo_id JOIN entity_nodes e ON e.id=w.entity_id LEFT JOIN entity_occurrences eo ON eo.repo_id=w.repo_id AND eo.entity_id=w.entity_id WHERE w.id=?",
            "rest_endpoint": "SELECT ep.id,r.repo_key,ep.method,ep.path,e.name entity_name,eo.ent_file,eo.module,eo.table_name,eo.view_name,ep.handler_symbol_id,f.path file_path FROM rest_endpoints ep JOIN repos r ON r.id=ep.repo_id LEFT JOIN entity_nodes e ON e.id=ep.entity_id LEFT JOIN entity_occurrences eo ON eo.repo_id=ep.repo_id AND eo.entity_id=ep.entity_id LEFT JOIN files f ON f.id=ep.file_id WHERE ep.id=?",
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
        active = (
            c.execute("SELECT source_fingerprint FROM graph_builds WHERE status='active' ORDER BY id DESC LIMIT 1").fetchone()
            if self.table(c, "graph_builds")
            else None
        )
        return bool(
            self.graph.is_file()
            and active
            and active["source_fingerprint"] == hashlib.sha256(self.db.read_bytes()).hexdigest()
        )

    def repositories(self) -> dict[str, Any]:
        with self.conn() as c:
            rows = [_row(row) for row in c.execute(
                "SELECT repo_key,tracked_branch,indexed_commit_sha,last_scanned_at,last_built_at,index_status,diagnostic_error,last_attempt_status,last_attempted_at,last_attempt_error FROM repos ORDER BY repo_key"
            )]
            return self.out("repository_list", {"repositories": rows}, c)

    def usages(self, name: str | None, ident: int | None, repo_key: str | None = None) -> dict[str, Any]:
        with self.conn() as c:
            hits = c.execute(
                "SELECT s.id,s.name,s.kind,r.repo_key,f.path file_path,s.start_line,s.end_line FROM symbols s JOIN files f ON f.id=s.file_id JOIN repos r ON r.id=f.repo_id WHERE ((? IS NOT NULL AND s.id=?) OR (? IS NULL AND s.name=?)) AND (? IS NULL OR r.repo_key=?) ORDER BY r.repo_key,s.id",
                (ident, ident, ident, name, repo_key, repo_key),
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
    transport = os.getenv("MCP_TRANSPORT", "streamable-http")
    kwargs = {"name": "intacct-catalog", "instructions": "Read-only evidence-first catalog. Search first, cite returned source paths, line ranges, record IDs and confidence; do not infer missing evidence."}
    if transport in {"sse", "streamable-http"}:
        kwargs["port"] = int(os.getenv("MCP_PORT", "8010"))
    s = FastMCP(**kwargs)

    @s.tool()
    def catalog_search(
        query: str,
        kind: Literal[
            "all", "entity", "file", "symbol", "api", "workflow", "security"
        ] = "all",
        limit: int = DEFAULT_LIMIT,
        cursor: str | None = None,
        repo_key: str | None = None,
    ) -> dict[str, Any]:
        return cat.search(query, kind, limit, cursor, repo_key)

    @s.tool()
    def repository_list() -> dict[str, Any]:
        """List repositories and their branch/revision/index freshness."""
        return cat.repositories()

    @s.tool()
    def entity_context(entity_name: str, repo_key: str | None = None) -> dict[str, Any]:
        return cat.entity(entity_name, repo_key)

    @s.tool()
    def relationship_query(
        name: str,
        direction: Literal["outgoing", "incoming"],
        resolution_classes: list[str] | None = None,
        repo_key: str | None = None,
        limit: int = DEFAULT_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        column = "source_name" if direction == "outgoing" else "target_name"
        sql = f"SELECT rel.id,r.repo_key,rel.source_symbol_id,rel.source_name,rel.source_kind,rel.target_symbol_id,rel.target_name,rel.target_kind,rel.relationship_type,rel.file_path,rel.language,rel.confidence,rel.evidence,rel.resolution_class,rel.resolution_reason,rel.extractor FROM relationships rel JOIN repos r ON r.id=rel.repo_id WHERE rel.{column}=? AND (? IS NULL OR r.repo_key=?)"
        args = [name, repo_key, repo_key]
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
        repo_key: str | None = None,
        limit: int = DEFAULT_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        if not entity_name and not path_fragment:
            raise ValueError("entity_name or path_fragment is required")
        sql = "SELECT r.id,rp.repo_key,r.method,r.path,e.id entity_id,e.name entity_name,r.handler_symbol_id,f.path file_path FROM rest_endpoints r JOIN repos rp ON rp.id=r.repo_id LEFT JOIN entity_nodes e ON e.id=r.entity_id LEFT JOIN files f ON f.id=r.file_id WHERE (? IS NULL OR rp.repo_key=?)"
        args = [repo_key, repo_key]
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
        entity_name: str, workflow_type: str | None = None, repo_key: str | None = None
    ) -> dict[str, Any]:
        sql = "SELECT w.id,r.repo_key,w.name,w.workflow_type,w.source_kind,w.source_file,w.source_symbol_id,w.confidence,w.reason FROM workflows w JOIN entity_nodes e ON e.id=w.entity_id JOIN repos r ON r.id=w.repo_id WHERE e.name=? COLLATE NOCASE AND (? IS NULL OR r.repo_key=?)"
        args = [entity_name, repo_key, repo_key]
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
        key_fragment: str, limit: int = DEFAULT_LIMIT, cursor: str | None = None, repo_key: str | None = None
    ) -> dict[str, Any]:
        return cat.records(
            "security_surface",
            "SELECT o.id,r.repo_key,o.op_key,o.op_numeric_id,o.title,o.action,o.script,o.source_file,o.source_line,o.source_kind FROM security_operations o JOIN repos r ON r.id=o.repo_id WHERE (o.op_key LIKE ? OR o.title LIKE ?) AND (? IS NULL OR r.repo_key=?) ORDER BY o.op_key,r.repo_key,o.id",
            ("%" + key_fragment + "%", "%" + key_fragment + "%", repo_key, repo_key),
            limit,
            cursor,
            "operations",
        )

    @s.tool()
    def symbol_references(
        symbol_name: str | None = None, symbol_id: int | None = None, repo_key: str | None = None
    ) -> dict[str, Any]:
        return cat.usages(symbol_name, symbol_id, repo_key)

    @s.tool()
    def file_impact(
        file_path: str, repo_key: str | None = None, depth: int = 1, max_edges_per_symbol: int = 25
    ) -> dict[str, Any]:
        if not 1 <= depth <= 3 or not 1 <= max_edges_per_symbol <= 1000:
            raise ValueError("depth must be 1..3 and max_edges_per_symbol 1..1000")
        with cat.conn() as c:
            files = c.execute(
                "SELECT f.id,f.path,r.repo_key FROM files f JOIN repos r ON r.id=f.repo_id "
                "WHERE f.path=? AND (? IS NULL OR r.repo_key=?) ORDER BY r.repo_key,f.id",
                (file_path, repo_key, repo_key),
            ).fetchall()
            if repo_key is None and len(files) > 1:
                return cat.out(
                    "file_impact", {"candidates": [_row(row) for row in files]}, c, "ambiguous",
                    {"code": "ambiguous_file", "message": "File path exists in multiple repositories; retry with repo_key"},
                )
            f = files[0] if files else None
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
                    {"code": "graph_stale", "message": "No active graph matches the current SQLite catalog"},
                )
            from scripts.query_graph import (
                _query_bounded_incoming_traversal,
                _query_file_symbols_from_graph,
                enrich_symbols_from_sql,
                get_graph_connection,
            )

            db, g = get_graph_connection(str(cat.graph))
            try:
                seeds = _query_file_symbols_from_graph(g, file_path, repo_key)
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
