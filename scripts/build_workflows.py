#!/usr/bin/env python3

from __future__ import annotations

import json
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
OUTPUT_DIR = Path("outputs")
UNRESOLVED_WORKFLOW_FILE_IDS_LOG = OUTPUT_DIR / "workflows_unresolved_file_ids.jsonl"

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
    file_ids_backfilled: int = 0
    unresolved_source_files: int = 0


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")


def ensure_workflows_file_id_column(conn: sqlite3.Connection) -> None:
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(workflows)").fetchall()}
    if "file_id" not in cols:
        conn.execute("ALTER TABLE workflows ADD COLUMN file_id INTEGER")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_workflows_file_id ON workflows(file_id)"
    )
    conn.commit()


def _resolve_file_id(conn: sqlite3.Connection, source_file: str) -> tuple[int | None, str]:
    row = conn.execute(
        "SELECT id FROM files WHERE path = ? LIMIT 1",
        (source_file,),
    ).fetchone()
    if row:
        return int(row["id"]), "exact_path"

    row = conn.execute(
        "SELECT id FROM files WHERE LOWER(path) = LOWER(?) ORDER BY id LIMIT 1",
        (source_file,),
    ).fetchone()
    if row:
        return int(row["id"]), "case_insensitive_path"

    return None, "exact_path,case_insensitive_path"


def backfill_workflow_file_ids(
    conn: sqlite3.Connection,
    stats: BuildStats,
    unresolved_sources: list[dict[str, Any]],
) -> None:
    # First backfill from source_file using files.path as source of truth.
    rows = conn.execute(
        """
        SELECT id, source_file
        FROM workflows
        WHERE source_file IS NOT NULL
          AND (file_id IS NULL OR file_id = 0)
        """
    ).fetchall()

    for row in rows:
        workflow_id = int(row["id"])
        source_file = str(row["source_file"] or "").strip()
        if not source_file:
            continue
        file_id, strategy = _resolve_file_id(conn, source_file)
        if file_id is not None:
            conn.execute(
                "UPDATE workflows SET file_id = ? WHERE id = ?",
                (file_id, workflow_id),
            )
            stats.file_ids_backfilled += 1
            continue

        stats.unresolved_source_files += 1
        unresolved_sources.append(
            {
                "table_name": "workflows",
                "row_id": workflow_id,
                "source_file": source_file,
                "attempted_match_strategy": strategy,
                "remediation_hint": "path missing from files table or requires normalization fix",
            }
        )

    # Then backfill from source_symbol_id for class/inference rows without source_file.
    symbol_rows = conn.execute(
        """
        SELECT w.id AS workflow_id, s.file_id AS symbol_file_id
        FROM workflows w
        JOIN symbols s ON s.id = w.source_symbol_id
        WHERE w.source_symbol_id IS NOT NULL
          AND (w.file_id IS NULL OR w.file_id = 0)
          AND s.file_id IS NOT NULL
        """
    ).fetchall()
    for row in symbol_rows:
        conn.execute(
            "UPDATE workflows SET file_id = ? WHERE id = ?",
            (int(row["symbol_file_id"]), int(row["workflow_id"])),
        )
        stats.file_ids_backfilled += 1


def get_entities(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, name
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


def insert_workflow(
    conn: sqlite3.Connection,
    entity_id: int,
    name: str,
    workflow_type: str,
    source_kind: str,
    source_file: str | None,
    file_id: int | None,
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
                file_id,
                source_symbol_id,
                reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entity_id,
                name,
                workflow_type,
                source_kind,
                source_file,
                file_id,
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


def get_yaml_actions_from_symbols_for_path(
    conn: sqlite3.Connection,
    entity_id: int,
    yaml_path: str,
) -> list[tuple[str, str]]:
    """
    Reuse parser-emitted YAML symbols when direct YAML parsing is unavailable.

    Returns normalized action candidates as (action_name, evidence).
    """
    rows = conn.execute(
        """
        SELECT DISTINCT s.name, s.kind
        FROM entity_mappings em
        JOIN files f
          ON f.path = em.source_text
        JOIN symbols s
          ON s.file_id = f.id
        WHERE em.entity_id = ?
          AND em.source_text = ?
          AND s.language = 'yaml'
          AND s.kind IN ('yaml_action', 'yaml_operation')
        ORDER BY s.kind, s.name
        """,
        (entity_id, yaml_path),
    ).fetchall()

    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    for row in rows:
        name = str(row["name"] or "").strip()
        kind = str(row["kind"] or "")
        if not name:
            continue

        if kind == "yaml_operation" and " " in name:
            # Operation names are stored as "METHOD /path".
            method = name.split(" ", 1)[0].strip()
            normalized = normalize_action(method)
        else:
            normalized = normalize_action(name)

        if not normalized or normalized in seen:
            continue

        seen.add(normalized)
        out.append((normalized, f"symbol:{kind}:{name}"))

    return out


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
) -> int:
    """
    Returns workflows_inserted for this entity.
    """
    wf_count = 0

    entity_id = entity["id"]
    entity_name = entity["name"]

    # ------------------------------------------------------------------
    # 1. YAML-driven allowed operations / workflows
    # ------------------------------------------------------------------
    yaml_paths = get_entity_yaml_paths(conn, entity_id)

    for yaml_path in yaml_paths:
        actions: list[tuple[str, str]] = []
        action_source = "yaml"

        try:
            doc = read_yaml_file(repo_root, yaml_path)
        except click.ClickException:
            doc = None

        if doc:
            actions = extract_yaml_actions(doc)

        if not actions:
            actions = get_yaml_actions_from_symbols_for_path(
                conn=conn,
                entity_id=entity_id,
                yaml_path=yaml_path,
            )
            if actions:
                action_source = "yaml_symbols"

        if not actions:
            continue

        wf_id, workflow_inserted = insert_workflow(
            conn,
            entity_id=entity_id,
            name=f"{entity_name} allowed operations",
            workflow_type="allowed_operations",
            source_kind="yaml" if action_source == "yaml" else "inference",
            source_file=yaml_path,
            file_id=None,
            source_symbol_id=None,
            reason=f"Discovered from {yaml_path} via {action_source}",
        )

        if wf_id is None:
            continue

        if workflow_inserted:
            wf_count += 1

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
            file_id=int(r["symbol_file_id"]) if r["symbol_file_id"] is not None else None,
            source_symbol_id=r["symbol_id"],
            reason=f"AllowedOperationsHandler class present: {r['symbol_name']}",
        )

        if wf_id is not None and workflow_inserted:
            wf_count += 1

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
            file_id=int(r["symbol_file_id"]) if r["symbol_file_id"] is not None else None,
            source_symbol_id=r["symbol_id"],
            reason=f"Discovered from role={r['mapping_type']} symbol={r['symbol_name']}",
        )

        if wf_id is not None and workflow_inserted:
            wf_count += 1

    return wf_count


def build(db: str, repo_root: Path, reset: bool) -> BuildStats:
    stats = BuildStats()
    unresolved_sources: list[dict[str, Any]] = []

    if yaml is None:
        click.echo(
            "Warning: PyYAML not installed. YAML actions will not be parsed. "
            "Run: pip install pyyaml",
            err=True,
        )

    conn = get_connection(db)
    try:
        ensure_workflows_file_id_column(conn)

        if reset:
            conn.execute("DELETE FROM workflows")
            conn.commit()

        entities = get_entities(conn)
        for entity in tqdm(entities, desc="Building workflows", unit="entity"):
            roots = get_entity_roots(conn, entity["id"])
            wf = build_workflows_for_entity(
                conn=conn,
                repo_root=repo_root,
                entity=entity,
                roots=roots,
            )
            stats.workflows_inserted += wf
            stats.entities_processed += 1

            if stats.entities_processed % 500 == 0:
                conn.commit()

        backfill_workflow_file_ids(conn, stats, unresolved_sources)

        conn.commit()
    finally:
        conn.close()

    write_jsonl(UNRESOLVED_WORKFLOW_FILE_IDS_LOG, unresolved_sources)

    return stats


@click.group()
def cli() -> None:
    pass


@cli.command("build")
@click.option(
    "--db",
    default=DEFAULT_DB,
    show_default=True,
    help="Path to SQLite catalog database.",
)
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
    click.echo(f"file_id backfilled:  {stats.file_ids_backfilled}")
    click.echo(f"Unresolved sources:  {stats.unresolved_source_files}")
    click.echo(f"Unresolved file log: {UNRESOLVED_WORKFLOW_FILE_IDS_LOG.as_posix()}")


if __name__ == "__main__":
    cli()
