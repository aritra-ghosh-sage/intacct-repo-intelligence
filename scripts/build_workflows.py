#!/usr/bin/env python3

from __future__ import annotations

import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click
from tqdm import tqdm

try:
    from catalog.db import get_connection
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from catalog.db import get_connection

try:
    import yaml
except ModuleNotFoundError:
    yaml = None

DEFAULT_DB = "catalog/catalog.db"
DEFAULT_REPO_ROOT = "/home/aritraghosh/projects/main"

# Only these mapping_type values are used to discover behavioral workflows.
BEHAVIORAL_ROLES: dict[str, str] = {
    "allowed_operations_handler": "allowed_operations",
    "approval_manager": "approval",
    "reverse_manager": "reverse",
    "batch_manager": "batch",
    "item_manager": "item",
    "entry_manager": "entry",
}

# Words we recognize as REST/allowed operation actions from YAML declarations.
KNOWN_ACTIONS = {
    "create",
    "read",
    "list",
    "update",
    "delete",
    "post",
    "unpost",
    "reverse",
    "recall",
    "submit",
    "approve",
    "reject",
    "adjust",
    "release",
    "close",
    "reopen",
    "import",
    "export",
    "clone",
    "print",
    "email",
    "attach",
}

OPENAPI_MAPPING_PREFIX = "openapispec_"


@dataclass
class BuildStats:
    entities_processed: int = 0
    workflows_inserted: int = 0
    steps_inserted: int = 0


def get_entities(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, name, ent_file, module
        FROM entity_nodes
        ORDER BY name
        """
    ).fetchall()

def get_entity_roots(conn: sqlite3.Connection, entity_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT
            em.entity_id,
            em.symbol_id,
            em.mapping_type,
            em.source_text,
            s.name AS symbol_name,
            s.kind AS symbol_kind,
            s.file_id AS symbol_file_id
        FROM entity_mappings em
        LEFT JOIN symbols s ON s.id = em.symbol_id
        WHERE em.entity_id = ?
        """,
        (entity_id,),
    ).fetchall()


def find_file_id_by_path(conn: sqlite3.Connection, path: str | None) -> int | None:
    if not path:
        return None
    row = conn.execute(
        "SELECT id FROM files WHERE path = ? LIMIT 1",
        (path,),
    ).fetchone()
    return row["id"] if row else None


def insert_workflow(
    conn: sqlite3.Connection,
    entity_id: int,
    name: str,
    workflow_type: str,
    source_kind: str,
    source_file: str | None,
    source_symbol_id: int | None,
    reason: str,
) -> tuple[int | None, bool]:
    try:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO workflows(
                entity_id,
                name,
                workflow_type,
                source_kind,
                source_file,
                source_symbol_id,
                reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entity_id,
                name,
                workflow_type,
                source_kind,
                source_file,
                source_symbol_id,
                reason,
            ),
        )
    except sqlite3.IntegrityError:
        return None, False

    if cur.lastrowid:
        return int(cur.lastrowid), True

    row = conn.execute(
        """
        SELECT id
        FROM workflows
        WHERE entity_id = ?
          AND name = ?
          AND workflow_type = ?
          AND IFNULL(source_file,'') = IFNULL(?, '')
        """,
        (entity_id, name, workflow_type, source_file),
    ).fetchone()

    return (int(row["id"]) if row else None), False


def insert_workflow_step(
    conn: sqlite3.Connection,
    workflow_id: int,
    ordinal: int,
    name: str,
    action: str | None,
    step_kind: str,
    symbol_id: int | None,
    file_id: int | None,
    file_path: str | None,
    evidence: str,
) -> bool:
    cur = conn.execute(
        """
        INSERT INTO workflow_steps(
            workflow_id,
            ordinal,
            name,
            action,
            step_kind,
            symbol_id,
            file_id,
            file_path,
            evidence
        )
        SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?
        WHERE NOT EXISTS (
            SELECT 1
            FROM workflow_steps
            WHERE workflow_id = ?
              AND ordinal = ?
              AND name = ?
              AND IFNULL(action, '') = IFNULL(?, '')
              AND step_kind = ?
              AND IFNULL(symbol_id, -1) = IFNULL(?, -1)
              AND IFNULL(file_id, -1) = IFNULL(?, -1)
              AND IFNULL(file_path, '') = IFNULL(?, '')
        )
        """,
        (
            workflow_id,
            ordinal,
            name,
            action,
            step_kind,
            symbol_id,
            file_id,
            file_path,
            evidence[:500] if evidence else None,
            workflow_id,
            ordinal,
            name,
            action,
            step_kind,
            symbol_id,
            file_id,
            file_path,
        ),
    )
    return cur.rowcount > 0


def normalize_action(text: str | None) -> str | None:
    if not text:
        return None
    t = re.sub(r"[^a-zA-Z]+", "", text).lower()
    return t if t in KNOWN_ACTIONS else None

def get_entity_yaml_paths(conn: sqlite3.Connection, entity_id: int) -> list[str]:
    """
    Return deterministic YAML candidates wired to this entity.

    Includes generic YAML mappings plus OpenAPI-derived mapping types.
    """
    rows = conn.execute(
        """
        SELECT DISTINCT source_text
        FROM entity_mappings
        WHERE entity_id = ?
          AND source_text IS NOT NULL
          AND LOWER(source_text) LIKE '%.yaml'
          AND (
               mapping_type = 'yaml'
             OR LOWER(mapping_type) LIKE ?
             OR mapping_type IS NULL
          )
        ORDER BY source_text
        """,
        (entity_id, f"{OPENAPI_MAPPING_PREFIX}%"),
    ).fetchall()

    return [r["source_text"] for r in rows]

def read_yaml_file(repo_root: Path, rel_path: str) -> dict[str, Any] | None:
    if not yaml:
        return None
    full = repo_root / rel_path
    if not full.exists():
        return None
    with full.open("r", encoding="utf-8") as handle:
        try:
            loaded = yaml.safe_load(handle)
        except yaml.YAMLError as exc:
            raise click.ClickException(f"Invalid YAML in {full}: {exc}") from exc
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise click.ClickException(
            f"Expected top-level YAML object in {full}, got {type(loaded).__name__}"
        )
    return loaded


def extract_yaml_actions(doc: dict[str, Any]) -> list[tuple[str, str]]:
    """
    Return a list of (action_name, evidence_snippet).

    Extraction is deterministic and conservative.
    We look at common Intacct YAML structures:

        actions:
          - name: create
          - name: post
          - name: reverse

        operations:
          create: ...
          post:   ...
          reverse: ...

        allowed_operations:
          - create
          - post

        workflows:
          approval: {...}

    And OpenAPI-style structures:

        paths:
          /resource:
            post: ...
            delete: ...

        components:
          actions:
            approve: ...
            reject: ...

    Actions are deduplicated — first evidence wins.
    Only names present in KNOWN_ACTIONS are emitted.
    """
    results: list[tuple[str, str]] = []
    seen_actions: set[str] = set()

    def add(name: str | None, evidence: str) -> None:
        norm = normalize_action(name or "")
        if norm and norm not in seen_actions:
            seen_actions.add(norm)
            results.append((norm, evidence))

    if not isinstance(doc, dict):
        return results

    # actions: [ {name: ...} ]
    actions = doc.get("actions")
    if isinstance(actions, list):
        for entry in actions:
            if isinstance(entry, dict):
                add(str(entry.get("name")), f"actions.name={entry.get('name')}")

    # operations: {create: ..., post: ...}
    ops = doc.get("operations")
    if isinstance(ops, dict):
        for key in ops.keys():
            add(str(key), f"operations.{key}")

    # allowed_operations: [create, post, ...]
    allowed = doc.get("allowed_operations") or doc.get("allowedOperations")
    if isinstance(allowed, list):
        for entry in allowed:
            add(str(entry), f"allowed_operations={entry}")

    # workflows: {approval: {...}}
    workflows = doc.get("workflows") or doc.get("workflow")
    if isinstance(workflows, dict):
        for key in workflows.keys():
            add(str(key), f"workflows.{key}")

    # OpenAPI-style paths:
    # paths:
    #   /resource:
    #     post: ...
    #     delete: ...
    paths = doc.get("paths")
    if isinstance(paths, dict):
        for path_key, methods in paths.items():
            if isinstance(methods, dict):
                for method_name in methods.keys():
                    add(str(method_name), f"paths.{path_key}.{method_name}")

    # OpenAPI-style components/actions:
    # components:
    #   actions:
    #     post: ...
    components = doc.get("components")
    if isinstance(components, dict):
        actions_block = components.get("actions") or {}
        if isinstance(actions_block, dict):
            for key in actions_block.keys():
                add(str(key), f"components.actions.{key}")

    return results


def build_workflows_for_entity(
    conn: sqlite3.Connection,
    repo_root: Path,
    entity: sqlite3.Row,
    roots: list[sqlite3.Row],
) -> tuple[int, int]:
    """
    Returns (workflows_inserted, steps_inserted) for this entity.
    """
    wf_count = 0
    step_count = 0

    entity_id = entity["id"]
    entity_name = entity["name"]

    # ------------------------------------------------------------------
    # 1. YAML-driven allowed operations / workflows
    # ------------------------------------------------------------------
    yaml_paths = get_entity_yaml_paths(conn, entity_id)

    for yaml_path in yaml_paths:
        doc = read_yaml_file(repo_root, yaml_path)
        if not doc:
            continue

        actions = extract_yaml_actions(doc)
        if not actions:
            continue

        wf_id, workflow_inserted = insert_workflow(
            conn,
            entity_id=entity_id,
            name=f"{entity_name} allowed operations",
            workflow_type="allowed_operations",
            source_kind="yaml",
            source_file=yaml_path,
            source_symbol_id=None,
            reason=f"Discovered from {yaml_path}",
        )

        if wf_id is None:
            continue

        if workflow_inserted:
            wf_count += 1

        ordinal = 0
        for action, evidence in actions:
            ordinal += 1
            step_inserted = insert_workflow_step(
                conn,
                workflow_id=wf_id,
                ordinal=ordinal,
                name=action,
                action=action,
                step_kind="rest_op",
                symbol_id=None,
                file_id=find_file_id_by_path(conn, yaml_path),
                file_path=yaml_path,
                evidence=evidence,
            )
            if step_inserted:
                step_count += 1

    # ------------------------------------------------------------------
    # 2. AllowedOperationsHandler workflow (surface even if no YAML)
    # ------------------------------------------------------------------
    for r in roots:
        if r["mapping_type"] != "allowed_operations_handler":
            continue

        wf_id, workflow_inserted = insert_workflow(
            conn,
            entity_id=entity_id,
            name=f"{entity_name} operations handler",
            workflow_type="allowed_operations",
            source_kind="class",
            source_file=None,
            source_symbol_id=r["symbol_id"],
            reason=f"AllowedOperationsHandler class present: {r['symbol_name']}",
        )

        if wf_id is not None:
            if workflow_inserted:
                wf_count += 1

            step_inserted = insert_workflow_step(
                conn,
                workflow_id=wf_id,
                ordinal=1,
                name=f"{r['symbol_name']} entry point",
                action=None,
                step_kind="handler",
                symbol_id=r["symbol_id"],
                file_id=r["symbol_file_id"],
                file_path=None,
                evidence=f"AllowedOperationsHandler class {r['symbol_name']}",
            )
            if step_inserted:
                step_count += 1

    # ------------------------------------------------------------------
    # 3. Behavioral workflows from Manager subclasses
    # ------------------------------------------------------------------
    for r in roots:
        wf_type = BEHAVIORAL_ROLES.get(r["mapping_type"] or "")
        if not wf_type:
            continue

        # Skip allowed_operations_handler - handled above.
        if wf_type == "allowed_operations":
            continue

        wf_id, workflow_inserted = insert_workflow(
            conn,
            entity_id=entity_id,
            name=f"{entity_name} {wf_type} workflow",
            workflow_type=wf_type,
            source_kind="class",
            source_file=None,
            source_symbol_id=r["symbol_id"],
            reason=f"Discovered from role={r['mapping_type']} symbol={r['symbol_name']}",
        )

        if wf_id is not None:
            if workflow_inserted:
                wf_count += 1

            step_inserted = insert_workflow_step(
                conn,
                workflow_id=wf_id,
                ordinal=1,
                name=r["symbol_name"],
                action=None,
                step_kind=f"{wf_type}_manager",
                symbol_id=r["symbol_id"],
                file_id=r["symbol_file_id"],
                file_path=None,
                evidence=(
                    f"{r['mapping_type']} class present: {r['symbol_name']}"
                ),
            )
            if step_inserted:
                step_count += 1

    # ------------------------------------------------------------------
    # 4. UI companion workflows (file_only_companion)
    # ------------------------------------------------------------------
    for r in roots:
        if r["mapping_type"] not in {"editor", "form_editor", "lister", "picker"}:
            continue

        companion_path = r["source_text"]

        if not companion_path or not companion_path.lower().endswith((".js", ".ts")):
            continue

        wf_id, workflow_inserted = insert_workflow(
            conn,
            entity_id=entity_id,
            name=f"{entity_name} UI {r['mapping_type']}",
            workflow_type="ui",
            source_kind="ui_companion",
            source_file=companion_path,
            source_symbol_id=r["symbol_id"],
            reason=f"UI companion {r['mapping_type']}: {companion_path}",
        )

        if wf_id is not None:
            if workflow_inserted:
                wf_count += 1

            step_inserted = insert_workflow_step(
                conn,
                workflow_id=wf_id,
                ordinal=1,
                name=Path(companion_path).stem,
                action=None,
                step_kind="ui_action",
                symbol_id=r["symbol_id"],
                file_id=find_file_id_by_path(conn, companion_path),
                file_path=companion_path,
                evidence=f"UI companion for {r['mapping_type']}",
            )
            if step_inserted:
                step_count += 1

    return wf_count, step_count


def build(db: str, repo_root: Path, reset: bool) -> BuildStats:
    stats = BuildStats()

    if yaml is None:
        click.echo(
            "Warning: PyYAML not installed. YAML actions will not be parsed. "
            "Run: pip install pyyaml",
            err=True,
        )

    conn = get_connection(db)
    try:
        if reset:
            conn.execute("DELETE FROM workflow_steps")
            conn.execute("DELETE FROM workflows")
            conn.commit()

        entities = get_entities(conn)
        for entity in tqdm(entities, desc="Building workflows", unit="entity"):
            roots = get_entity_roots(conn, entity["id"])
            wf, steps = build_workflows_for_entity(
                conn=conn,
                repo_root=repo_root,
                entity=entity,
                roots=roots,
            )
            stats.workflows_inserted += wf
            stats.steps_inserted += steps
            stats.entities_processed += 1

            if stats.entities_processed % 500 == 0:
                conn.commit()

        conn.commit()
    finally:
        conn.close()

    return stats


@click.group()
def cli() -> None:
    pass


@cli.command("build")
@click.option("--db", default=DEFAULT_DB, show_default=True, help="Path to SQLite catalog database.")
@click.option(
    "--repo-root",
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    default=Path(DEFAULT_REPO_ROOT),
    show_default=True,
    help="Repository root path used to resolve YAML/source files.",
)
@click.option("--reset", is_flag=True, help="Delete workflow tables before rebuilding.")
def build_command(db: str, repo_root: Path, reset: bool) -> None:
    stats = build(db=db, repo_root=repo_root.resolve(), reset=reset)
    click.echo(f"Processed entities:  {stats.entities_processed}")
    click.echo(f"Workflows inserted:  {stats.workflows_inserted}")
    click.echo(f"Workflow steps:      {stats.steps_inserted}")



if __name__ == "__main__":
    cli()
