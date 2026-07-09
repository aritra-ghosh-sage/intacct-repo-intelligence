# scripts/query_catalog.py

import click
from tabulate import tabulate
from catalog.db import get_connection


@click.group()
def cli():
    pass


@cli.command()
def stats():
    conn = get_connection()
    cur = conn.cursor()

    total = cur.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    print(f"Total files: {total}\n")

    rows = cur.execute("""
        SELECT language, COUNT(*) AS c, SUM(size_bytes) AS bytes
        FROM files
        GROUP BY language
        ORDER BY c DESC
    """).fetchall()

    print(
        tabulate(
            [(r["language"], r["c"], r["bytes"]) for r in rows],
            headers=["Language", "Files", "Bytes"],
        )
    )


@cli.command()
@click.argument("keyword")
@click.option("--limit", default=25)
def find(keyword, limit):
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

    print(
        tabulate(
            [(r["path"], r["language"], r["size_bytes"]) for r in rows],
            headers=["Path", "Lang", "Size"],
        )
    )


@cli.command()
def toplevel():
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

    print(
        tabulate(
            [(r["top_dir"], r["c"]) for r in rows], headers=["Top-level dir", "Files"]
        )
    )


@cli.command()
@click.argument("keyword")
@click.option("--kind", default=None, help="class, method, table, template, etc.")
@click.option("--limit", default=50)
def symbols(keyword, kind, limit):
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
def symbol_stats():
    """Breakdown of symbols by kind and language."""
    conn = get_connection()
    cur = conn.cursor()

    rows = cur.execute("""
        SELECT language, kind, COUNT(*) AS c
        FROM symbols
        GROUP BY language, kind
        ORDER BY language, c DESC
    """).fetchall()

    print(
        tabulate(
            [(r["language"], r["kind"], r["c"]) for r in rows],
            headers=["Language", "Kind", "Count"],
        )
    )


@cli.command()
@click.argument("entity_prefix")
def entity(entity_prefix):
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
