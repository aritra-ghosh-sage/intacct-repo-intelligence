# scripts/query_relationships.py

from __future__ import annotations

import os
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import click

try:
    from catalog.db import get_connection
except ModuleNotFoundError:
    # Allow running as: python scripts/query_relationships.py ...
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from catalog.db import get_connection

try:
    from ._query_json import emit_json, success_response
except ImportError:
    from _query_json import emit_json, success_response


DEFAULT_DB = os.environ.get("CATALOG_DB", "catalog/catalog.db")


def _relationship_row_to_dict(row: sqlite3.Row) -> dict[str, object]:
    payload: dict[str, object] = {
        "relationship_type": row["relationship_type"],
        "confidence": row.get("confidence", None),
        "file_path": row.get("file_path", None),
        "resolution_class": (
            row.get("resolution_class", None)
        ),
        "evidence": row.get("evidence", None),
    }
    for key in (
        "source_name",
        "source_kind",
        "target_name",
        "target_kind",
    ):
        if key in row:
            payload[key] = row[key]
    return payload


def _fetch_stats_payload(conn: sqlite3.Connection) -> dict[str, object]:
    total = conn.execute("SELECT COUNT(*) AS c FROM relationships").fetchone()["c"]
    rel_rows = conn.execute(
        """
        SELECT relationship_type, COUNT(*) AS c
        FROM relationships
        GROUP BY relationship_type
        ORDER BY c DESC
        """
    ).fetchall()

    payload: dict[str, object] = {
        "total_relationships": total,
        "by_relationship_type": [
            {"relationship_type": r["relationship_type"], "count": r["c"]}
            for r in rel_rows
        ],
    }

    if has_resolution_classification(conn):
        class_rows = conn.execute(
            """
            SELECT resolution_class, COUNT(*) AS c
            FROM relationships
            GROUP BY resolution_class
            ORDER BY c DESC
            """
        ).fetchall()
        payload["by_resolution_class"] = [
            {"resolution_class": r["resolution_class"], "count": r["c"]}
            for r in class_rows
        ]

    return payload


def _fetch_deps_rows(
    conn: sqlite3.Connection, name: str, limit: int, classes: list[str]
) -> list[sqlite3.Row]:
    sql = """
        SELECT
            relationship_type,
            source_name,
            target_name,
            target_kind,
            confidence,
            file_path,
            evidence,
            resolution_class
        FROM relationships
        WHERE (source_name LIKE ?
           OR file_path LIKE ?)
    """
    params: list[object] = [f"%{name}%", f"%{name}%"]
    if classes and has_resolution_classification(conn):
        placeholders = ",".join(["?"] * len(classes))
        sql += f" AND resolution_class IN ({placeholders})"
        params.extend(classes)
    sql += " ORDER BY relationship_type, target_name LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def _fetch_rdeps_rows(
    conn: sqlite3.Connection, name: str, limit: int, classes: list[str]
) -> list[sqlite3.Row]:
    symbol_ids = get_exact_symbol_ids(conn, name)

    if symbol_ids:
        placeholders = ",".join(["?"] * len(symbol_ids))
        sql = f"""
            SELECT
                relationship_type,
                source_name,
                source_kind,
                target_name,
                confidence,
                file_path,
                evidence,
                resolution_class
            FROM relationships
            WHERE target_symbol_id IN ({placeholders})
        """
        params: list[object] = [*symbol_ids]
    else:
        sql = """
            SELECT
                relationship_type,
                source_name,
                source_kind,
                target_name,
                confidence,
                file_path,
                evidence,
                resolution_class
            FROM relationships
            WHERE target_name LIKE ?
        """
        params = [f"%{name}%"]

    if classes and has_resolution_classification(conn):
        placeholders = ",".join(["?"] * len(classes))
        sql += f" AND resolution_class IN ({placeholders})"
        params.extend(classes)
    sql += " ORDER BY relationship_type, source_name LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def _fetch_unresolved_rows(
    conn: sqlite3.Connection,
    name: str | None,
    limit: int,
    classes: list[str],
) -> list[sqlite3.Row]:
    class_sql = ""
    class_params: list[object] = []
    if classes and has_resolution_classification(conn):
        placeholders = ",".join(["?"] * len(classes))
        class_sql = f" AND resolution_class IN ({placeholders})"
        class_params.extend(classes)

    if name:
        return conn.execute(
            f"""
            SELECT relationship_type, source_name, target_name, file_path, evidence, resolution_class
            FROM relationships
            WHERE target_symbol_id IS NULL
              AND target_name LIKE ?
              {class_sql}
            ORDER BY target_name
            LIMIT ?
            """,
            [f"%{name}%", *class_params, limit],
        ).fetchall()

    return conn.execute(
        f"""
        SELECT relationship_type, source_name, target_name, file_path, evidence, resolution_class
        FROM relationships
        WHERE target_symbol_id IS NULL
        {class_sql}
        ORDER BY target_name
        LIMIT ?
        """,
        [*class_params, limit],
    ).fetchall()


def _fetch_files_rows(
    conn: sqlite3.Connection, name: str, limit: int, classes: list[str]
) -> list[sqlite3.Row]:
    sql = """
        SELECT DISTINCT file_path
        FROM relationships
        WHERE (source_name LIKE ?
           OR target_name LIKE ?
           OR file_path LIKE ?)
    """
    params: list[object] = [f"%{name}%", f"%{name}%", f"%{name}%"]
    if classes and has_resolution_classification(conn):
        placeholders = ",".join(["?"] * len(classes))
        sql += f" AND resolution_class IN ({placeholders})"
        params.extend(classes)
    sql += " ORDER BY file_path LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def get_exact_symbol_ids(conn: sqlite3.Connection, name: str) -> list[int]:
    rows = conn.execute(
        """
        SELECT id
        FROM symbols
        WHERE name = ?
        ORDER BY id
        """,
        (name,),
    ).fetchall()
    return [r["id"] for r in rows]


def has_resolution_classification(conn: sqlite3.Connection) -> bool:
    cols = conn.execute("PRAGMA table_info(relationships)").fetchall()
    names = {r["name"] for r in cols}
    return "resolution_class" in names and "resolution_reason" in names


def parse_resolution_classes(classes: str | None) -> list[str]:
    if not classes:
        return []
    return [c.strip() for c in classes.split(",") if c.strip()]


def show_stats(conn: sqlite3.Connection) -> None:
    # Show a quick cardinality snapshot for the relationships table.
    total = conn.execute("SELECT COUNT(*) AS c FROM relationships").fetchone()["c"]
    print(f"Total relationships: {total}")
    print()

    rows = conn.execute("""
        SELECT relationship_type, COUNT(*) AS c
        FROM relationships
        GROUP BY relationship_type
        ORDER BY c DESC
    """).fetchall()

    for r in rows:
        print(f"{r['relationship_type']:16} {r['c']}")

    if not has_resolution_classification(conn):
        return

    print()
    print("By resolution_class:")
    rows = conn.execute("""
        SELECT resolution_class, COUNT(*) AS c
        FROM relationships
        GROUP BY resolution_class
        ORDER BY c DESC
    """).fetchall()
    for r in rows:
        print(f"{(r['resolution_class'] or 'unknown'):16} {r['c']}")


def show_deps(
    conn: sqlite3.Connection, name: str, limit: int, classes: list[str]
) -> None:
    # Outgoing edges where the source symbol (or file path) matches the search term.
    sql = """
        SELECT
            relationship_type,
            source_name,
            target_name,
            target_kind,
            confidence,
            file_path,
            evidence,
            resolution_class
        FROM relationships
        WHERE (source_name LIKE ?
           OR file_path LIKE ?)
    """
    params: list[object] = [f"%{name}%", f"%{name}%"]
    if classes and has_resolution_classification(conn):
        placeholders = ",".join(["?"] * len(classes))
        sql += f" AND resolution_class IN ({placeholders})"
        params.extend(classes)
    sql += " ORDER BY relationship_type, target_name LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()

    if not rows:
        print(f"No outgoing dependencies found for: {name}")
        return

    grouped = defaultdict(list)
    for r in rows:
        grouped[r["relationship_type"]].append(r)

    print(f"Dependencies for: {name}")
    print()

    for rel_type, items in grouped.items():
        print(f"{rel_type}")
        for r in items:
            cls = (
                f" class={r['resolution_class']}"
                if r.get("resolution_class")
                else ""
            )
            print(
                f"  -> {r['target_name']} [{r['target_kind'] or 'unknown'}] confidence={r['confidence']}{cls}"
            )
            print(f"     file: {r['file_path']}")
            if r["evidence"]:
                print(f"     evidence: {r['evidence'][:160]}")
        print()


def show_rdeps(
    conn: sqlite3.Connection, name: str, limit: int, classes: list[str]
) -> None:
    # Incoming edges where the requested symbol appears as a relationship target.
    symbol_ids = get_exact_symbol_ids(conn, name)

    if symbol_ids:
        placeholders = ",".join(["?"] * len(symbol_ids))
        sql = f"""
            SELECT
                relationship_type,
                source_name,
                source_kind,
                target_name,
                confidence,
                file_path,
                evidence,
                resolution_class
            FROM relationships
            WHERE target_symbol_id IN ({placeholders})
        """
        params: list[object] = [*symbol_ids]
    else:
        sql = """
            SELECT
                relationship_type,
                source_name,
                source_kind,
                target_name,
                confidence,
                file_path,
                evidence,
                resolution_class
            FROM relationships
            WHERE target_name LIKE ?
        """
        params = [f"%{name}%"]

    if classes and has_resolution_classification(conn):
        placeholders = ",".join(["?"] * len(classes))
        sql += f" AND resolution_class IN ({placeholders})"
        params.extend(classes)
    sql += " ORDER BY relationship_type, source_name LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()

    if not rows:
        print(f"No reverse dependencies found for: {name}")
        return

    grouped = defaultdict(list)
    for r in rows:
        grouped[r["relationship_type"]].append(r)

    print(f"Reverse dependencies for: {name}")
    print()

    for rel_type, items in grouped.items():
        print(f"{rel_type}")
        for r in items:
            cls = (
                f" class={r['resolution_class']}"
                if r.get("resolution_class")
                else ""
            )
            print(
                f"  <- {r['source_name']} [{r['source_kind'] or 'unknown'}] confidence={r['confidence']}{cls}"
            )
            print(f"     file: {r['file_path']}")
            if r["evidence"]:
                print(f"     evidence: {r['evidence'][:160]}")
        print()


def show_unresolved(
    conn: sqlite3.Connection, name: str | None, limit: int, classes: list[str]
) -> None:
    # Relationships that could not be linked to a concrete target symbol id.
    class_sql = ""
    class_params: list[object] = []
    if classes and has_resolution_classification(conn):
        placeholders = ",".join(["?"] * len(classes))
        class_sql = f" AND resolution_class IN ({placeholders})"
        class_params.extend(classes)

    if name:
        rows = conn.execute(
            f"""
            SELECT relationship_type, source_name, target_name, file_path, evidence, resolution_class
            FROM relationships
            WHERE target_symbol_id IS NULL
              AND target_name LIKE ?
              {class_sql}
            ORDER BY target_name
            LIMIT ?
        """,
            [f"%{name}%", *class_params, limit],
        ).fetchall()
    else:
        rows = conn.execute(
            f"""
            SELECT relationship_type, source_name, target_name, file_path, evidence, resolution_class
            FROM relationships
            WHERE target_symbol_id IS NULL
            {class_sql}
            ORDER BY target_name
            LIMIT ?
        """,
            [*class_params, limit],
        ).fetchall()

    print(f"Unresolved relationships: {len(rows)} shown")
    print()

    for r in rows:
        cls = (
            f" class={r['resolution_class']}"
            if r.get("resolution_class")
            else ""
        )
        print(
            f"{r['relationship_type']:14} {r['source_name']} -> {r['target_name']}{cls}"
        )
        print(f"  file: {r['file_path']}")
        if r["evidence"]:
            print(f"  evidence: {r['evidence'][:160]}")
        print()


def show_files(
    conn: sqlite3.Connection, name: str, limit: int, classes: list[str]
) -> None:
    # Distinct files associated with matching source/target symbols or path text.
    sql = """
        SELECT DISTINCT file_path
        FROM relationships
        WHERE (source_name LIKE ?
           OR target_name LIKE ?
           OR file_path LIKE ?)
    """
    params: list[object] = [f"%{name}%", f"%{name}%", f"%{name}%"]
    if classes and has_resolution_classification(conn):
        placeholders = ",".join(["?"] * len(classes))
        sql += f" AND resolution_class IN ({placeholders})"
        params.extend(classes)
    sql += " ORDER BY file_path LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()

    print(f"Files related to: {name}")
    print()

    for r in rows:
        print(r["file_path"])


@click.group()
def cli() -> None:
    pass


@cli.command("stats")
@click.option(
    "--db",
    default=DEFAULT_DB,
    show_default=True,
    help="Path to SQLite catalog database.",
)
@click.option("--json", "json_output", is_flag=True, help="Emit JSON output.")
def stats(db: str, json_output: bool) -> None:
    conn = get_connection(db)
    if json_output:
        stats_payload = _fetch_stats_payload(conn)
        emit_json(
            success_response(
                command="stats",
                args={"db": db},
                data={
                    "by_relationship_type": stats_payload["by_relationship_type"],
                    "by_resolution_class": stats_payload.get("by_resolution_class", []),
                },
                summary={"total_relationships": stats_payload["total_relationships"]},
            )
        )
        conn.close()
        return
    show_stats(conn)
    conn.close()


@cli.command("deps")
@click.argument("name")
@click.option(
    "--db",
    default=DEFAULT_DB,
    show_default=True,
    help="Path to SQLite catalog database.",
)
@click.option("--limit", type=int, default=100, show_default=True)
@click.option("--classes", default="", help="Comma-separated resolution_class filters.")
@click.option("--json", "json_output", is_flag=True, help="Emit JSON output.")
def deps(name: str, db: str, limit: int, classes: str, json_output: bool) -> None:
    conn = get_connection(db)
    parsed_classes = parse_resolution_classes(classes)
    if json_output:
        rows = _fetch_deps_rows(conn, name, limit, parsed_classes)
        relationships = [_relationship_row_to_dict(r) for r in rows]
        emit_json(
            success_response(
                command="deps",
                args={
                    "name": name,
                    "db": db,
                    "limit": limit,
                    "classes": parsed_classes,
                },
                data={"relationships": relationships},
                summary={"count": len(relationships)},
            )
        )
        conn.close()
        return
    show_deps(conn, name, limit, parse_resolution_classes(classes))
    conn.close()


@cli.command("rdeps")
@click.argument("name")
@click.option(
    "--db",
    default=DEFAULT_DB,
    show_default=True,
    help="Path to SQLite catalog database.",
)
@click.option("--limit", type=int, default=100, show_default=True)
@click.option("--classes", default="", help="Comma-separated resolution_class filters.")
@click.option("--json", "json_output", is_flag=True, help="Emit JSON output.")
def rdeps(name: str, db: str, limit: int, classes: str, json_output: bool) -> None:
    conn = get_connection(db)
    parsed_classes = parse_resolution_classes(classes)
    if json_output:
        rows = _fetch_rdeps_rows(conn, name, limit, parsed_classes)
        relationships = [_relationship_row_to_dict(r) for r in rows]
        emit_json(
            success_response(
                command="rdeps",
                args={
                    "name": name,
                    "db": db,
                    "limit": limit,
                    "classes": parsed_classes,
                },
                data={"relationships": relationships},
                summary={"count": len(relationships)},
            )
        )
        conn.close()
        return
    show_rdeps(conn, name, limit, parse_resolution_classes(classes))
    conn.close()


@cli.command("unresolved")
@click.argument("name", required=False)
@click.option(
    "--db",
    default=DEFAULT_DB,
    show_default=True,
    help="Path to SQLite catalog database.",
)
@click.option("--limit", type=int, default=100, show_default=True)
@click.option("--classes", default="", help="Comma-separated resolution_class filters.")
@click.option("--json", "json_output", is_flag=True, help="Emit JSON output.")
def unresolved(
    name: str | None, db: str, limit: int, classes: str, json_output: bool
) -> None:
    conn = get_connection(db)
    parsed_classes = parse_resolution_classes(classes)
    if json_output:
        rows = _fetch_unresolved_rows(conn, name, limit, parsed_classes)
        relationships = [_relationship_row_to_dict(r) for r in rows]
        emit_json(
            success_response(
                command="unresolved",
                args={
                    "name": name,
                    "db": db,
                    "limit": limit,
                    "classes": parsed_classes,
                },
                data={"relationships": relationships},
                summary={"count": len(relationships)},
            )
        )
        conn.close()
        return
    show_unresolved(conn, name, limit, parse_resolution_classes(classes))
    conn.close()


@cli.command("files")
@click.argument("name")
@click.option(
    "--db",
    default=DEFAULT_DB,
    show_default=True,
    help="Path to SQLite catalog database.",
)
@click.option("--limit", type=int, default=200, show_default=True)
@click.option("--classes", default="", help="Comma-separated resolution_class filters.")
@click.option("--json", "json_output", is_flag=True, help="Emit JSON output.")
def files(name: str, db: str, limit: int, classes: str, json_output: bool) -> None:
    conn = get_connection(db)
    parsed_classes = parse_resolution_classes(classes)
    if json_output:
        rows = _fetch_files_rows(conn, name, limit, parsed_classes)
        files_payload = [r["file_path"] for r in rows]
        emit_json(
            success_response(
                command="files",
                args={
                    "name": name,
                    "db": db,
                    "limit": limit,
                    "classes": parsed_classes,
                },
                data={"files": files_payload},
                summary={"count": len(files_payload)},
            )
        )
        conn.close()
        return
    show_files(conn, name, limit, parse_resolution_classes(classes))
    conn.close()


if __name__ == "__main__":
    cli()
