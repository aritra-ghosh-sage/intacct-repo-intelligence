# scripts/query_workflow.py

from collections import defaultdict
import click
import sys
from pathlib import Path

try:
    from catalog.db import get_connection
except ModuleNotFoundError:
    # Allow running as: python scripts/query_workflow.py ...
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from catalog.db import get_connection


@click.group()
def cli():
    pass


def get_entity(conn, entity_name):
    return conn.execute(
        "SELECT * FROM entity_nodes WHERE name = ?",
        (entity_name,),
    ).fetchone()


def get_workflows(conn, entity_id, workflow_type):
    if workflow_type:
        return conn.execute(
            """
            SELECT *
            FROM workflows
            WHERE entity_id = ?
              AND workflow_type = ?
            ORDER BY workflow_type, name
            """,
            (entity_id, workflow_type),
        ).fetchall()

    return conn.execute(
        """
        SELECT *
        FROM workflows
        WHERE entity_id = ?
        ORDER BY workflow_type, name
        """,
        (entity_id,),
    ).fetchall()


def build_grouped_workflows(conn, entity_name, workflow_type):
    entity = get_entity(conn, entity_name)
    if not entity:
        return None

    grouped = defaultdict(list)
    workflows = get_workflows(conn, entity["id"], workflow_type)

    for wf in workflows:
        grouped[wf["workflow_type"]].append(
            (
                wf["id"],
                wf["name"],
                wf["source_kind"],
                wf["source_file"],
            )
        )

    return grouped


@cli.command("list")
@click.argument("entity_name")
@click.option("--db", default=None, help="Path to SQLite catalog database")
@click.option("--type", "workflow_type", default=None, help="Filter by workflow type")
def list_workflows(entity_name, db, workflow_type):
    conn = get_connection(db)
    grouped = build_grouped_workflows(conn, entity_name, workflow_type)

    if grouped is None:
        print(f"Entity not found: {entity_name}")
        conn.close()
        return

    print(f"\nWorkflows for {entity_name}:")
    for wf_type, items in sorted(grouped.items()):
        for _, wf_name, _, _ in items:
            print(f"  [{wf_type}] {wf_name}")

    conn.close()


@cli.command("entity")
@click.argument("entity_name")
@click.option("--db", default=None, help="Path to SQLite catalog database")
@click.option("--type", "workflow_type", default=None, help="Filter by workflow type")
def show_entity_workflows(entity_name, db, workflow_type):
    conn = get_connection(db)
    grouped = build_grouped_workflows(conn, entity_name, workflow_type)

    if grouped is None:
        print(f"Entity not found: {entity_name}")
        conn.close()
        return

    print()
    print("=" * 100)
    print(f"WORKFLOWS FOR ENTITY: {entity_name}")
    print("=" * 100)

    if not grouped:
        print("No workflows discovered yet.")
        conn.close()
        return

    for wf_type in sorted(grouped.keys()):
        print()
        print(f"[{wf_type}]")
        print("-" * len(f"[{wf_type}]"))

        for _, wf_name, source_kind, source_file in grouped[wf_type]:
            print(f"  {wf_name}")
            print(
                f"    source: {source_kind}{' | ' + source_file if source_file else ''}"
            )

    conn.close()


if __name__ == "__main__":
    cli()
