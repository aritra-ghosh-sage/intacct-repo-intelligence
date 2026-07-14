# scripts/query_catalog.py

import json

import click
from tabulate import tabulate
from catalog.db import get_connection


@click.group()
def cli():
    pass


def _emit_json(payload: dict[str, object]) -> None:
    click.echo(json.dumps(payload, ensure_ascii=True))


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
        _emit_json(
            {
                "query": {"command": "stats"},
                "total_files": total,
                "languages": [
                    {
                        "language": r["language"],
                        "file_count": r["c"],
                        "bytes": r["bytes"],
                    }
                    for r in rows
                ],
            }
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
        _emit_json(
            {
                "query": {"command": "find", "keyword": keyword, "limit": limit},
                "matches": [
                    {
                        "path": r["path"],
                        "language": r["language"],
                        "size_bytes": r["size_bytes"],
                    }
                    for r in rows
                ],
            }
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
        _emit_json(
            {
                "query": {"command": "toplevel"},
                "directories": [
                    {"top_dir": r["top_dir"], "file_count": r["c"]} for r in rows
                ],
            }
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
        _emit_json(
            {
                "query": {
                    "command": "symbols",
                    "keyword": keyword,
                    "kind": kind,
                    "limit": limit,
                },
                "matches": [
                    {
                        "name": r["name"],
                        "kind": r["kind"],
                        "language": r["language"],
                        "parent_symbol": r["parent_symbol"],
                        "file_path": r["path"],
                        "start_line": r["start_line"],
                    }
                    for r in rows
                ],
            }
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
        _emit_json(
            {
                "query": {"command": "symbol_stats"},
                "stats": [
                    {
                        "language": r["language"],
                        "kind": r["kind"],
                        "count": r["c"],
                    }
                    for r in rows
                ],
            }
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
        _emit_json(
            {
                "query": {"command": "entity", "entity_prefix": entity_prefix},
                "symbol_count": len(rows),
                "symbols": [
                    {
                        "name": r["name"],
                        "kind": r["kind"],
                        "language": r["language"],
                        "parent_symbol": r["parent_symbol"],
                        "file_path": r["path"],
                    }
                    for r in rows
                ],
            }
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
