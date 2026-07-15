# scripts/query_catalog.py

import click
from tabulate import tabulate

try:
    from catalog.db import get_connection
except ModuleNotFoundError:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from catalog.db import get_connection

try:
    from ._query_json import emit_json, success_response
except ImportError:
    from _query_json import emit_json, success_response


@click.group()
def cli():
    pass


@cli.command()
@click.option("--json", "json_output", is_flag=True, help="Emit JSON output.")
def stats(json_output):
    conn = get_connection()
    cur = conn.cursor()

    total = cur.execute("SELECT COUNT(*) FROM files").fetchone()[0]

    rows = cur.execute("""
        SELECT language, COUNT(*) AS c, SUM(size_bytes) AS bytes
        FROM files
        GROUP BY language
        ORDER BY c DESC
    """).fetchall()

    if json_output:
        emit_json(
            success_response(
                command="stats",
                args={},
                data={
                    "languages": [
                    {
                        "language": r["language"],
                        "file_count": r["c"],
                        "bytes": r["bytes"],
                    }
                    for r in rows
                    ]
                },
                summary={
                    "total_files": total,
                    "language_count": len(rows),
                },
            )
        )
        return

    print(f"Total files: {total}\n")

    print(
        tabulate(
            [(r["language"], r["c"], r["bytes"]) for r in rows],
            headers=["Language", "Files", "Bytes"],
        )
    )


@cli.command()
@click.argument("keyword")
@click.option("--limit", default=25)
@click.option("--json", "json_output", is_flag=True, help="Emit JSON output.")
def find(keyword, limit, json_output):
    conn = get_connection()
    cur = conn.cursor()

    rows = cur.execute(
        """
        SELECT path, language, size_bytes
        FROM files
        WHERE path LIKE ?
        LIMIT ?
    """,
        (f"%{keyword}%", limit),
    ).fetchall()

    if json_output:
        matches = [
                    {
                        "path": r["path"],
                        "language": r["language"],
                        "size_bytes": r["size_bytes"],
                    }
                    for r in rows
                ]
        emit_json(
            success_response(
                command="find",
                args={"keyword": keyword, "limit": limit},
                data={"matches": matches},
                summary={"match_count": len(matches)},
            )
        )
        return

    print(
        tabulate(
            [(r["path"], r["language"], r["size_bytes"]) for r in rows],
            headers=["Path", "Lang", "Size"],
        )
    )


@cli.command()
@click.option("--json", "json_output", is_flag=True, help="Emit JSON output.")
def toplevel(json_output):
    conn = get_connection()
    cur = conn.cursor()

    rows = cur.execute("""
        SELECT
            SUBSTR(path, 1, INSTR(path || '/', '/') - 1) AS top_dir,
            COUNT(*) AS c
        FROM files
        GROUP BY top_dir
        ORDER BY c DESC
    """).fetchall()

    if json_output:
        directories = [{"top_dir": r["top_dir"], "file_count": r["c"]} for r in rows]
        emit_json(
            success_response(
                command="toplevel",
                args={},
                data={"directories": directories},
                summary={"directory_count": len(directories)},
            )
        )
        return

    print(
        tabulate(
            [(r["top_dir"], r["c"]) for r in rows], headers=["Top-level dir", "Files"]
        )
    )


@cli.command()
@click.argument("keyword")
@click.option("--kind", default=None, help="class, method, table, template, etc.")
@click.option("--limit", default=50)
@click.option("--json", "json_output", is_flag=True, help="Emit JSON output.")
def symbols(keyword, kind, limit, json_output):
    """Search symbols by name substring."""
    conn = get_connection()
    cur = conn.cursor()

    query = """
        SELECT s.name, s.kind, s.language, s.parent_symbol,
               f.path, s.start_line
        FROM symbols s
        JOIN files f ON f.id = s.file_id
        WHERE s.name LIKE ?
    """
    params = [f"%{keyword}%"]

    if kind:
        query += " AND s.kind = ?"
        params.append(kind)

    query += " ORDER BY s.language, s.kind, s.name LIMIT ?"
    params.append(limit)

    rows = cur.execute(query, params).fetchall()

    if json_output:
        matches = [
                    {
                        "name": r["name"],
                        "kind": r["kind"],
                        "language": r["language"],
                        "parent_symbol": r["parent_symbol"],
                        "file_path": r["path"],
                        "start_line": r["start_line"],
                    }
                    for r in rows
                ]
        emit_json(
            success_response(
                command="symbols",
                args={
                    "keyword": keyword,
                    "kind": kind,
                    "limit": limit,
                },
                data={"matches": matches},
                summary={"match_count": len(matches)},
            )
        )
        return

    print(
        tabulate(
            [
                (
                    r["name"],
                    r["kind"],
                    r["language"],
                    r["parent_symbol"] or "",
                    r["path"],
                    r["start_line"],
                )
                for r in rows
            ],
            headers=["Symbol", "Kind", "Lang", "Parent", "File", "Line"],
        )
    )


@cli.command()
@click.option("--json", "json_output", is_flag=True, help="Emit JSON output.")
def symbol_stats(json_output=False):
    """Breakdown of symbols by kind and language."""
    conn = get_connection()
    cur = conn.cursor()

    rows = cur.execute("""
        SELECT language, kind, COUNT(*) AS c
        FROM symbols
        GROUP BY language, kind
        ORDER BY language, c DESC
    """).fetchall()

    if json_output:
        stats_rows = [
                    {
                        "language": r["language"],
                        "kind": r["kind"],
                        "count": r["c"],
                    }
                    for r in rows
                ]
        emit_json(
            success_response(
                command="symbol_stats",
                args={},
                data={"stats": stats_rows},
                summary={"row_count": len(stats_rows)},
            )
        )
        return

    print(
        tabulate(
            [(r["language"], r["kind"], r["c"]) for r in rows],
            headers=["Language", "Kind", "Count"],
        )
    )


@cli.command()
@click.argument("entity_prefix")
@click.option("--json", "json_output", is_flag=True, help="Emit JSON output.")
def entity(entity_prefix, json_output):
    """
    Rough entity view: show all symbols whose name
    starts with the given prefix (e.g. 'APBill').
    """
    conn = get_connection()
    cur = conn.cursor()

    rows = cur.execute(
        """
        SELECT s.name, s.kind, s.language,
               s.parent_symbol, f.path
        FROM symbols s
        JOIN files f ON f.id = s.file_id
        WHERE s.name LIKE ? OR s.parent_symbol LIKE ?
        ORDER BY s.language, s.kind, s.name
    """,
        (f"{entity_prefix}%", f"{entity_prefix}%"),
    ).fetchall()

    if json_output:
        symbols_payload = [
                    {
                        "name": r["name"],
                        "kind": r["kind"],
                        "language": r["language"],
                        "parent_symbol": r["parent_symbol"],
                        "file_path": r["path"],
                    }
                    for r in rows
                ]
        emit_json(
            success_response(
                command="entity",
                args={"entity_prefix": entity_prefix},
                data={"symbols": symbols_payload},
                summary={"symbol_count": len(symbols_payload)},
            )
        )
        return

    print(f"Entity: {entity_prefix}")
    print(f"Symbols found: {len(rows)}\n")

    print(
        tabulate(
            [
                (
                    r["name"],
                    r["kind"],
                    r["language"],
                    r["parent_symbol"] or "",
                    r["path"],
                )
                for r in rows
            ],
            headers=["Name", "Kind", "Lang", "Parent", "File"],
        )
    )


if __name__ == "__main__":
    cli()
