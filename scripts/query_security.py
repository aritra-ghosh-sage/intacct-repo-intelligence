#!/usr/bin/env python3

from __future__ import annotations

import json
from pathlib import Path

import click

from catalog.db import get_connection

DEFAULT_DB = "catalog/catalog.db"
PARSE_FAILURES_LOG = Path("outputs/security_parse_failures.jsonl")
UNRESOLVED_LOG = Path("outputs/security_unresolved_keys.jsonl")
CONFLICTS_LOG = Path("outputs/security_conflicts.jsonl")


@click.group()
def cli() -> None:
    pass


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            rows.append(json.loads(text))
    return rows


@cli.command("stats")
@click.option("--db", default=DEFAULT_DB, show_default=True)
def stats_command(db: str) -> None:
    conn = get_connection(db)
    try:
        tables = [
            "security_operations",
            "security_operation_allowops",
            "security_policies",
            "security_policy_values",
            "security_policy_eops",
            "security_menus",
            "security_menu_items",
            "security_menu_op_links",
            "dbschema_tables",
            "dbschema_fields",
        ]
        for table in tables:
            count = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
            click.echo(f"{table:28} {count:>10}")
    finally:
        conn.close()


@cli.command("op")
@click.argument("key_or_id")
@click.option("--db", default=DEFAULT_DB, show_default=True)
def op_command(key_or_id: str, db: str) -> None:
    conn = get_connection(db)
    try:
        if key_or_id.isdigit():
            rows = conn.execute(
                """
                SELECT *
                FROM security_operations
                WHERE op_numeric_id = ?
                ORDER BY op_key
                """,
                (int(key_or_id),),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT *
                FROM security_operations
                WHERE op_key = ?
                ORDER BY op_numeric_id
                """,
                (key_or_id,),
            ).fetchall()

        if not rows:
            click.echo("No matching operations found.")
            return

        for row in rows:
            click.echo(
                f"{row['op_key']} | id={row['op_numeric_id']} | force={row['force_mode']} "
                f"| secure_only={row['secure_only']} | source={row['source_file']}"
            )
            allowops = conn.execute(
                """
                SELECT allowed_op_key
                FROM security_operation_allowops
                WHERE operation_id = ?
                ORDER BY allowed_op_key
                """,
                (row["id"],),
            ).fetchall()
            if allowops:
                click.echo("  allowops:")
                for allow in allowops:
                    click.echo(f"    - {allow['allowed_op_key']}")
    finally:
        conn.close()


@cli.command("policy")
@click.argument("name_fragment")
@click.option("--db", default=DEFAULT_DB, show_default=True)
def policy_command(name_fragment: str, db: str) -> None:
    conn = get_connection(db)
    try:
        rows = conn.execute(
            """
            SELECT id, policy_name, module, label, source_file
            FROM security_policies
            WHERE LOWER(policy_name) LIKE LOWER(?)
            ORDER BY module, policy_name
            """,
            (f"%{name_fragment}%",),
        ).fetchall()
        if not rows:
            click.echo("No matching policies found.")
            return

        for row in rows:
            click.echo(
                f"{row['policy_name']} [{row['module']}] label={row['label']} source={row['source_file']}"
            )
            values = conn.execute(
                """
                SELECT pv.id, pv.value_key, pv.display, pv.value_label
                FROM security_policy_values pv
                WHERE pv.policy_id = ?
                ORDER BY pv.value_key
                """,
                (row["id"],),
            ).fetchall()
            for value in values:
                click.echo(
                    f"  - {value['value_key']} display={value['display']} value={value['value_label']}"
                )
                eops = conn.execute(
                    """
                    SELECT op_key
                    FROM security_policy_eops
                    WHERE policy_value_id = ?
                    ORDER BY op_key
                    """,
                    (value["id"],),
                ).fetchall()
                for eop in eops:
                    click.echo(f"      * {eop['op_key']}")
    finally:
        conn.close()


@cli.command("menu")
@click.argument("key_fragment")
@click.option("--db", default=DEFAULT_DB, show_default=True)
def menu_command(key_fragment: str, db: str) -> None:
    conn = get_connection(db)
    try:
        rows = conn.execute(
            """
            SELECT
                m.module,
                m.source_file,
                i.item_path,
                i.menu_key,
                l.resolution_reason
            FROM security_menu_items i
            JOIN security_menus m ON m.id = i.menu_id
            LEFT JOIN security_menu_op_links l
              ON l.menu_item_id = i.id
             AND l.op_key = i.menu_key
            WHERE i.menu_key IS NOT NULL
              AND LOWER(i.menu_key) LIKE LOWER(?)
            ORDER BY m.module, i.item_path
            """,
            (f"%{key_fragment}%",),
        ).fetchall()
        if not rows:
            click.echo("No matching menu links found.")
            return
        for row in rows:
            click.echo(
                f"[{row['module']}] {row['item_path']} -> {row['menu_key']} "
                f"({row['resolution_reason']}) source={row['source_file']}"
            )
    finally:
        conn.close()


@cli.command("can")
@click.argument("op_key")
@click.option("--db", default=DEFAULT_DB, show_default=True)
def can_command(op_key: str, db: str) -> None:
    conn = get_connection(db)
    try:
        ops = conn.execute(
            """
            SELECT id, op_key, op_numeric_id, force_mode, source_file
            FROM security_operations
            WHERE op_key = ?
            ORDER BY op_numeric_id, source_file
            """,
            (op_key,),
        ).fetchall()
        if not ops:
            click.echo("No matching operation key found.")
            return

        click.echo("Operation mapping")
        for op in ops:
            click.echo(
                f"  key={op['op_key']} id={op['op_numeric_id']} force={op['force_mode']} source={op['source_file']}"
            )

        click.echo("")
        click.echo("Policy buckets")
        policy_rows = conn.execute(
            """
            SELECT
                p.module,
                p.policy_name,
                pv.value_key,
                pv.display,
                pv.value_label
            FROM security_policy_eops e
            JOIN security_policy_values pv
              ON pv.id = e.policy_value_id
            JOIN security_policies p
              ON p.id = pv.policy_id
            WHERE e.op_key = ?
            ORDER BY p.module, p.policy_name, pv.value_key
            """,
            (op_key,),
        ).fetchall()
        if not policy_rows:
            click.echo("  (no policy mappings)")
        else:
            for row in policy_rows:
                click.echo(
                    f"  [{row['module']}] {row['policy_name']} -> {row['value_key']} "
                    f"(display={row['display']}, value={row['value_label']})"
                )

        click.echo("")
        click.echo("Menu locations")
        menu_rows = conn.execute(
            """
            SELECT
                m.module,
                m.source_file,
                i.item_path,
                i.menu_script,
                l.resolution_reason
            FROM security_menu_items i
            JOIN security_menus m
              ON m.id = i.menu_id
            LEFT JOIN security_menu_op_links l
              ON l.menu_item_id = i.id
             AND l.op_key = i.menu_key
            WHERE i.menu_key = ?
            ORDER BY m.module, i.item_path
            """,
            (op_key,),
        ).fetchall()
        if not menu_rows:
            click.echo("  (no menu mappings)")
        else:
            for row in menu_rows:
                click.echo(
                    f"  [{row['module']}] {row['item_path']} script={row['menu_script']} "
                    f"({row['resolution_reason']}) source={row['source_file']}"
                )
    finally:
        conn.close()


@cli.command("unresolved")
@click.option(
    "--kind",
    type=click.Choice(["all", "policy", "menu"], case_sensitive=False),
    default="all",
    show_default=True,
)
def unresolved_command(kind: str) -> None:
    rows = _read_jsonl(UNRESOLVED_LOG)
    if kind != "all":
        prefix = "policy_" if kind == "policy" else "menu_"
        rows = [row for row in rows if str(row.get("category", "")).startswith(prefix)]
    if not rows:
        click.echo("No unresolved rows.")
        return
    for row in rows:
        click.echo(json.dumps(row, ensure_ascii=True))


@cli.command("failures")
def failures_command() -> None:
    rows = _read_jsonl(PARSE_FAILURES_LOG)
    if not rows:
        click.echo("No parse failures logged.")
        return
    for row in rows:
        click.echo(json.dumps(row, ensure_ascii=True))


@cli.command("conflicts")
def conflicts_command() -> None:
    rows = _read_jsonl(CONFLICTS_LOG)
    if not rows:
        click.echo("No conflicts logged.")
        return
    for row in rows:
        click.echo(json.dumps(row, ensure_ascii=True))


if __name__ == "__main__":
    cli()
