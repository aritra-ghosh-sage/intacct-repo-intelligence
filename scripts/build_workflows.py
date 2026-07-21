#!/usr/bin/env python3

from __future__ import annotations

import json
import posixpath
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
    from catalog.repositories import get_repository, resolve_repository_root
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from catalog.db import get_connection
    from catalog.repositories import get_repository, resolve_repository_root

try:
    import yaml
except ModuleNotFoundError:
    yaml = None

DEFAULT_DB = "catalog/catalog.db"
DEFAULT_REPO_ROOT = "/home/aritraghosh/projects/main"
OUTPUT_DIR = Path("outputs")
UNRESOLVED_WORKFLOW_FILE_IDS_LOG = OUTPUT_DIR / "workflows_unresolved_file_ids.jsonl"
WORKFLOW_PARSE_FAILURES_LOG = OUTPUT_DIR / "workflows_parse_failures.jsonl"

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
WORKFLOW_MAPPING_PREFIX = "workflow_"


@dataclass
class BuildStats:
    entities_processed: int = 0
    workflows_inserted: int = 0
    file_ids_backfilled: int = 0
    unresolved_source_files: int = 0
    workflow_nodes_inserted: int = 0
    workflow_edges_inserted: int = 0
    openapi_ref_edges_inserted: int = 0
    parse_failures_p0: int = 0
    parse_failures_p1: int = 0
    parse_failures_p2: int = 0


def record_parse_failure(
    parse_failures: list[dict[str, Any]],
    stats: BuildStats,
    *,
    severity: str,
    entity_id: int,
    entity_name: str,
    source_file: str,
    reason: str,
    detail: str | None = None,
) -> None:
    sev = severity.upper().strip()
    if sev not in {"P0", "P1", "P2"}:
        sev = "P2"

    parse_failures.append(
        {
            "severity": sev,
            "entity_id": entity_id,
            "entity_name": entity_name,
            "source_file": source_file,
            "reason": reason,
            "detail": detail or "",
        }
    )

    if sev == "P0":
        stats.parse_failures_p0 += 1
    elif sev == "P1":
        stats.parse_failures_p1 += 1
    else:
        stats.parse_failures_p2 += 1


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")


def ensure_workflows_file_id_column(conn: sqlite3.Connection) -> None:
    cols = {
        row["name"] for row in conn.execute("PRAGMA table_info(workflows)").fetchall()
    }
    if "file_id" not in cols:
        conn.execute("ALTER TABLE workflows ADD COLUMN file_id INTEGER")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_workflows_file_id ON workflows(file_id)"
    )
    conn.commit()


def _resolve_file_id(
    conn: sqlite3.Connection, repo_id: int, source_file: str
) -> tuple[int | None, str]:
    row = conn.execute(
        "SELECT id FROM files WHERE repo_id = ? AND path = ? LIMIT 1",
        (repo_id, source_file),
    ).fetchone()
    if row:
        return int(row["id"]), "exact_path"

    row = conn.execute(
        "SELECT id FROM files WHERE repo_id = ? AND LOWER(path) = LOWER(?) ORDER BY id LIMIT 1",
        (repo_id, source_file),
    ).fetchone()
    if row:
        return int(row["id"]), "case_insensitive_path"

    return None, "exact_path,case_insensitive_path"

def _resolve_ref_target_path(source_file_path: str, ref_value: str) -> str | None:
    ref_value = (ref_value or "").strip()
    if not ref_value:
        return None

    # Skip remote references.
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", ref_value):
        return None

    # Split fragment from file part.
    ref_file_part = ref_value.split("#", 1)[0].strip()

    # Internal ref like "#/components/..." -> points to same file.
    if ref_file_part == "":
        return source_file_path

    # Treat leading "/" as repo-relative.
    if ref_file_part.startswith("/"):
        return posixpath.normpath(ref_file_part.lstrip("/"))

    # Relative to source file directory.
    source_dir = posixpath.dirname(source_file_path)
    return posixpath.normpath(posixpath.join(source_dir, ref_file_part))


def materialize_openapi_ref_edges(
    conn: sqlite3.Connection,
    stats: BuildStats,
    repo_id: int,
) -> None:
    """
    Materialize shared YAML $ref edges from relationships into openapi_file_ref_edges.

    Source rows:
    - relationships.relationship_type = 'REFERENCES'
    - relationships.language = 'yaml'
    - relationships.evidence path ending in '$ref'

    Target resolution:
    - '#/...' => same file
    - 'relative/path.yaml#/...' => source-dir relative
    - '/repo/relative/path.yaml#/...' => repo-relative
    - external URLs are skipped
    """
    # Cache files table paths for quick id<->path resolution.
    file_rows = conn.execute(
        """
        SELECT id, path
        FROM files
        WHERE repo_id = ? AND path IS NOT NULL
        """,
        (repo_id,),
    ).fetchall()

    id_to_path: dict[int, str] = {}
    path_to_id: dict[str, int] = {}
    lower_path_to_id: dict[str, int] = {}

    for row in file_rows:
        fid = int(row["id"])
        fpath = str(row["path"])
        id_to_path[fid] = fpath
        path_to_id[fpath] = fid
        lower_path_to_id[fpath.lower()] = fid

    rel_rows = conn.execute(
        """
        SELECT
            file_id,
            file_path,
            target_name AS ref_value,
            evidence
        FROM relationships
        WHERE relationship_type = 'REFERENCES'
          AND repo_id = ?
          AND LOWER(COALESCE(language, '')) = 'yaml'
          AND COALESCE(TRIM(target_name), '') <> ''
          AND COALESCE(evidence, '') LIKE '%$ref'
        """,
        (repo_id,),
    ).fetchall()

    for row in rel_rows:
        source_file_id = int(row["file_id"]) if row["file_id"] is not None else None
        source_file_path = str(row["file_path"] or "").strip()
        ref_value = str(row["ref_value"] or "").strip()
        ref_path = str(row["evidence"] or "").strip()

        if source_file_id is None and source_file_path:
            source_file_id = path_to_id.get(source_file_path)
            if source_file_id is None:
                source_file_id = lower_path_to_id.get(source_file_path.lower())

        if source_file_id is None:
            continue

        if not source_file_path:
            source_file_path = id_to_path.get(source_file_id, "")
        if not source_file_path:
            continue

        target_path = _resolve_ref_target_path(source_file_path, ref_value)
        if not target_path:
            continue

        target_file_id = path_to_id.get(target_path)
        if target_file_id is None:
            target_file_id = lower_path_to_id.get(target_path.lower())
        if target_file_id is None:
            continue

        before = conn.total_changes
        conn.execute(
            """
            INSERT OR IGNORE INTO openapi_file_ref_edges(
                repo_id,
                source_file_id,
                target_file_id,
                ref_value,
                ref_path,
                confidence
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                repo_id,
                source_file_id,
                target_file_id,
                ref_value,
                ref_path,
                1.0,
            ),
        )
        if conn.total_changes > before:
            stats.openapi_ref_edges_inserted += 1


def backfill_workflow_file_ids(
    conn: sqlite3.Connection,
    stats: BuildStats,
    unresolved_sources: list[dict[str, Any]],
    repo_id: int,
) -> None:
    # First backfill from source_file using files.path as source of truth.
    rows = conn.execute(
        """
        SELECT id, source_file
        FROM workflows
        WHERE repo_id = ? AND source_file IS NOT NULL
          AND (file_id IS NULL OR file_id = 0)
        """,
        (repo_id,),
    ).fetchall()

    for row in rows:
        workflow_id = int(row["id"])
        source_file = str(row["source_file"] or "").strip()
        if not source_file:
            continue
        file_id, strategy = _resolve_file_id(conn, repo_id, source_file)
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
        WHERE w.repo_id = ? AND w.source_symbol_id IS NOT NULL
          AND (w.file_id IS NULL OR w.file_id = 0)
          AND s.file_id IS NOT NULL
        """,
        (repo_id,),
    ).fetchall()
    for row in symbol_rows:
        conn.execute(
            "UPDATE workflows SET file_id = ? WHERE id = ?",
            (int(row["symbol_file_id"]), int(row["workflow_id"])),
        )
        stats.file_ids_backfilled += 1


def get_entities(conn: sqlite3.Connection, repo_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, name
        FROM entity_nodes en
        WHERE EXISTS (
            SELECT 1 FROM entity_mappings em
            WHERE em.entity_id = en.id AND em.repo_id = ?
        )
        ORDER BY name
        """
    , (repo_id,)).fetchall()


def get_entity_roots(conn: sqlite3.Connection, repo_id: int, entity_id: int) -> list[sqlite3.Row]:
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
        WHERE em.entity_id = ? AND em.repo_id = ?
        """,
        (entity_id, repo_id),
    ).fetchall()


def insert_workflow(
    conn: sqlite3.Connection,
    repo_id: int,
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
                repo_id,
                entity_id,
                name,
                workflow_type,
                source_kind,
                source_file,
                file_id,
                source_symbol_id,
                reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                repo_id,
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
        WHERE repo_id = ? AND entity_id = ?
          AND name = ?
          AND workflow_type = ?
          AND IFNULL(source_file,'') = IFNULL(?, '')
        """,
        (repo_id, entity_id, name, workflow_type, source_file),
    ).fetchone()

    return (int(row["id"]) if row else None), False


def normalize_action(text: str | None) -> str | None:
    if not text:
        return None
    t = re.sub(r"[^a-zA-Z]+", "", text).lower()
    return t if t in KNOWN_ACTIONS else None


def get_entity_yaml_paths(conn: sqlite3.Connection, repo_id: int, entity_id: int) -> list[str]:
    """
    Return deterministic YAML candidates wired to this entity.

    Includes generic YAML mappings plus OpenAPI-derived YAML mappings and
    workflow API files.
    """
    rows = conn.execute(
        """
        SELECT DISTINCT source_text
        FROM entity_mappings
        WHERE entity_id = ? AND repo_id = ?
          AND source_text IS NOT NULL
          AND LOWER(source_text) LIKE '%.yaml'
          AND (
               mapping_type = 'yaml'
             OR LOWER(mapping_type) LIKE ?
                         OR mapping_type = 'workflow_api_files'
             OR mapping_type IS NULL
          )
        ORDER BY source_text
        """,
                (entity_id, repo_id, f"{OPENAPI_MAPPING_PREFIX}%"),
    ).fetchall()

    return [r["source_text"] for r in rows]


def get_yaml_actions_from_symbols_for_path(
    conn: sqlite3.Connection,
    repo_id: int,
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
          ON f.repo_id = em.repo_id AND f.path = em.source_text
        JOIN symbols s
          ON s.file_id = f.id
        WHERE em.entity_id = ? AND em.repo_id = ?
          AND em.source_text = ?
          AND s.language = 'yaml'
          AND s.kind IN ('yaml_action', 'yaml_operation')
        ORDER BY s.kind, s.name
        """,
        (entity_id, repo_id, yaml_path),
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


def extract_yaml_actions(doc: dict[str, Any]) -> list[tuple[int, str, str]]:
    """
    Return a list of (ordinal, action_name, evidence_snippet).

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
    results: list[tuple[int, str, str]] = []
    seen_actions: set[str] = set()
    idx = 0

    def add(name: str | None, evidence: str) -> None:
        nonlocal idx
        idx += 1
        norm = normalize_action(name or "")
        if norm and norm not in seen_actions:
            seen_actions.add(norm)
            results.append((idx, norm, evidence))

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


def insert_workflow_node(
    # Insert or reuse by unique key (workflow_id, node_kind, node_key).
    # Args: workflow_id, entity_id, node_kind, node_key, name, ordinal, action, source_kind, file_id, symbol_id, metadata_json.
    # Return node_id, inserted_flag.
    conn: sqlite3.Connection,
    workflow_id: int,
    entity_id: int | None,
    node_kind: str,
    node_key: str,
    name: str,
    ordinal: int,
    action: str,
    source_kind: str | None,
    file_id: int | None,
    symbol_id: int | None,
    metadata_json: str,
) -> tuple[int | None, bool]:
    # insert workflow, if workflow_id, node_kind, node_key
    # does not already exist
    row = conn.execute(
        """
        SELECT id FROM workflow_nodes
        WHERE workflow_id = ? AND node_kind = ? AND node_key = ?
        """,
        (workflow_id, node_kind, node_key),
    ).fetchone()

    if row:
        return int(row["id"]), False

    try:
        cur = conn.execute(
            """
            INSERT INTO workflow_nodes (
                workflow_id, entity_id, node_kind, node_key, name, ordinal, action, source_kind, file_id, symbol_id, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                workflow_id,
                entity_id,
                node_kind,
                node_key,
                name,
                ordinal,
                action,
                source_kind,
                file_id,
                symbol_id,
                metadata_json,
            ),
        )
    except sqlite3.IntegrityError:
        return None, False

    return (int(cur.lastrowid), True) if cur.lastrowid is not None else (None, False)


def insert_workflow_edge(
    # Insert or ignore by unique key.
    # Args: workflow_id, from_node_id, to_node_id, edge_kind, ordinal, evidence, confidence, file_id, symbol_id
    # Return inserted_flag: bool
    conn: sqlite3.Connection,
    workflow_id: int,
    from_node_id: int,
    to_node_id: int,
    edge_kind: str,
    ordinal: int,
    evidence: str,
    confidence: float,
    file_id: int | None,
    symbol_id: int | None,
) -> bool:
    # Insert or ignore by unique key (workflow_id, from_node_id, to_node_id, edge_kind, ordinal, evidence)
    row = conn.execute(
        """
        SELECT id FROM workflow_edges
        WHERE workflow_id = ? AND from_node_id = ? AND to_node_id = ? AND edge_kind = ? AND ordinal = ? AND evidence = ?
        """,
        (workflow_id, from_node_id, to_node_id, edge_kind, ordinal, evidence),
    ).fetchone()

    if row:
        return False

    try:
        cur = conn.execute(
            """
            INSERT INTO workflow_edges (
                workflow_id, from_node_id, to_node_id, edge_kind, ordinal, evidence, confidence, file_id, symbol_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                workflow_id,
                from_node_id,
                to_node_id,
                edge_kind,
                ordinal,
                evidence,
                confidence,
                file_id,
                symbol_id,
            ),
        )
    except sqlite3.IntegrityError:
        return False

    return True if cur.lastrowid else False


def build_action_chain_for_workflow(
    # Args:  workflow_id, entity_id, source_file, source_kind, actions[(int, action,evidence)].
    # Return: None
    conn: sqlite3.Connection,
    workflow_id: int,
    entity_id: int,
    source_file: str | None,
    source_kind: str,
    actions: list[tuple[int, str, str]],
    repo_id: int,
) -> tuple[int, int]:
    # Steps:
    #   Create one workflow node (node_kind=workflow, node_key=workflow:{workflow_id}).
    #   For each action in ordinal order:
    #     Create action node (node_kind=action, node_key=action:{ordinal}:{action}).
    #     Edge workflow_contains from workflow node to action node.
    #     Edge step_next from previous action node to current action node.
    #   If source_file resolved:
    #     Create file node once.
    #     Link each action node with step_uses_file edge.
    workflow_node_id, _ = insert_workflow_node(
        conn=conn,
        workflow_id=workflow_id,
        entity_id=entity_id,
        node_kind="workflow",
        node_key=f"workflow:{workflow_id}",
        name=f"Workflow {workflow_id}",
        ordinal=0,
        action="",
        source_kind=source_kind,
        file_id=None,
        symbol_id=None,
        metadata_json="{}",
    )
    if workflow_node_id is None:
        return 0, 0

    nodes_inserted = 0
    edges_inserted = 0

    source_file_id: int | None = None
    file_node_id: int | None = None
    if source_file:
        source_file_id, _ = _resolve_file_id(conn, repo_id, source_file)
        file_node_id, _ = insert_workflow_node(
            conn=conn,
            workflow_id=workflow_id,
            entity_id=entity_id,
            node_kind="file",
            node_key=f"file:{source_file}",
            name=source_file,
            ordinal=0,
            action="",
            source_kind="file",
            file_id=source_file_id,
            symbol_id=None,
            metadata_json=json.dumps({"source_file": source_file}, ensure_ascii=True),
        )

        if file_node_id is not None:
            nodes_inserted += 1

    prev_action_node_id: int | None = None
    ordered_actions = sorted(actions, key=lambda item: item[0])

    for ordinal, action, evidence in ordered_actions:
        action_node_id, _ = insert_workflow_node(
            conn=conn,
            workflow_id=workflow_id,
            entity_id=entity_id,
            node_kind="action",
            node_key=f"action:{ordinal}:{action}",
            name=action,
            ordinal=ordinal,
            action=action,
            source_kind=source_kind,
            file_id=source_file_id,
            symbol_id=None,
            metadata_json=json.dumps({"evidence": evidence}, ensure_ascii=True),
        )
        if action_node_id is None:
            continue

        nodes_inserted += 1

        edge_inserted = insert_workflow_edge(
            conn=conn,
            workflow_id=workflow_id,
            from_node_id=workflow_node_id,
            to_node_id=action_node_id,
            edge_kind="workflow_contains",
            ordinal=ordinal,
            evidence=evidence,
            confidence=1.0,
            file_id=source_file_id,
            symbol_id=None,
        )
        if edge_inserted:
            edges_inserted += 1

        if prev_action_node_id is not None:
            edge_inserted = insert_workflow_edge(
                conn=conn,
                workflow_id=workflow_id,
                from_node_id=prev_action_node_id,
                to_node_id=action_node_id,
                edge_kind="step_next",
                ordinal=ordinal,
                evidence=evidence,
                confidence=1.0,
                file_id=source_file_id,
                symbol_id=None,
            )
            if edge_inserted:
                edges_inserted += 1

        if file_node_id is not None:
            edge_inserted = insert_workflow_edge(
                conn=conn,
                workflow_id=workflow_id,
                from_node_id=action_node_id,
                to_node_id=file_node_id,
                edge_kind="step_uses_file",
                ordinal=ordinal,
                evidence=evidence,
                confidence=1.0,
                file_id=source_file_id,
                symbol_id=None,
            )
            if edge_inserted:
                edges_inserted += 1

        prev_action_node_id = action_node_id

    return nodes_inserted, edges_inserted


def link_workflow_to_symbol_roots(
    conn: sqlite3.Connection,
    workflow_id: int,
    entity_id: int,
    workflow_type: str,
    roots: list[sqlite3.Row],
    source_symbol_id: int | None = None,
    source_kind: str = "class",
) -> tuple[int, int]:
    """
    Link a workflow graph to root symbols.

    Behavior:
    - Reuses/creates the workflow node.
    - Selects relevant roots:
      - If source_symbol_id is provided: only that symbol.
      - Else if workflow_type maps to a root mapping_type: only matching roots.
      - Else: all roots with symbol_id.
    - Creates symbol nodes (node_kind='symbol').
    - Adds workflow_contains edges from workflow -> symbol.
    - Adds step_uses_symbol edges from action -> symbol when action nodes exist;
      otherwise from workflow -> symbol.

    Returns:
        (workflow_nodes_inserted, workflow_edges_inserted)
    """
    workflow_node_id, _ = insert_workflow_node(
        conn=conn,
        workflow_id=workflow_id,
        entity_id=entity_id,
        node_kind="workflow",
        node_key=f"workflow:{workflow_id}",
        name=f"Workflow {workflow_id}",
        ordinal=0,
        action="",
        source_kind=source_kind,
        file_id=None,
        symbol_id=None,
        metadata_json="{}",
    )
    if workflow_node_id is None:
        return 0, 0

    nodes_inserted = 0
    edges_inserted = 0

    workflow_to_mapping_type = {
        "allowed_operations": "allowed_operations_handler",
        "approval": "approval_manager",
        "reverse": "reverse_manager",
        "batch": "batch_manager",
        "item": "item_manager",
        "entry": "entry_manager",
    }

    expected_mapping_type = workflow_to_mapping_type.get(workflow_type)

    selected_roots: list[sqlite3.Row] = []
    seen_symbol_ids: set[int] = set()

    for r in roots:
        if r["symbol_id"] is None:
            continue
        sid = int(r["symbol_id"])
        if sid in seen_symbol_ids:
            continue

        mapping_type = str(r["mapping_type"] or "")

        if source_symbol_id is not None and sid != int(source_symbol_id):
            continue

        if (
            source_symbol_id is None
            and expected_mapping_type
            and mapping_type != expected_mapping_type
        ):
            continue

        seen_symbol_ids.add(sid)
        selected_roots.append(r)

    action_nodes = conn.execute(
        """
        SELECT id, ordinal
        FROM workflow_nodes
        WHERE workflow_id = ?
          AND node_kind = 'action'
        ORDER BY COALESCE(ordinal, 0), id
        """,
        (workflow_id,),
    ).fetchall()

    ordinal_base = 10_000

    for i, r in enumerate(selected_roots, start=1):
        symbol_id = int(r["symbol_id"])
        symbol_name = str(r["symbol_name"] or f"symbol:{symbol_id}")
        symbol_kind = str(r["symbol_kind"] or "")
        mapping_type = str(r["mapping_type"] or "")
        symbol_file_id = (
            int(r["symbol_file_id"]) if r["symbol_file_id"] is not None else None
        )

        symbol_node_id, symbol_node_inserted = insert_workflow_node(
            conn=conn,
            workflow_id=workflow_id,
            entity_id=entity_id,
            node_kind="symbol",
            node_key=f"symbol:{symbol_id}",
            name=symbol_name,
            ordinal=ordinal_base + i,
            action="",
            source_kind=source_kind,
            file_id=symbol_file_id,
            symbol_id=symbol_id,
            metadata_json=json.dumps(
                {
                    "mapping_type": mapping_type,
                    "symbol_kind": symbol_kind,
                },
                ensure_ascii=True,
            ),
        )
        if symbol_node_id is None:
            continue

        if symbol_node_inserted:
            nodes_inserted += 1

        evidence = f"root:{mapping_type}:{symbol_name}"

        if insert_workflow_edge(
            conn=conn,
            workflow_id=workflow_id,
            from_node_id=workflow_node_id,
            to_node_id=symbol_node_id,
            edge_kind="workflow_contains",
            ordinal=ordinal_base + i,
            evidence=evidence,
            confidence=1.0,
            file_id=symbol_file_id,
            symbol_id=symbol_id,
        ):
            edges_inserted += 1

        if action_nodes:
            for a in action_nodes:
                action_node_id = int(a["id"])
                action_ordinal = int(a["ordinal"] or 0)
                if insert_workflow_edge(
                    conn=conn,
                    workflow_id=workflow_id,
                    from_node_id=action_node_id,
                    to_node_id=symbol_node_id,
                    edge_kind="step_uses_symbol",
                    ordinal=action_ordinal,
                    evidence=evidence,
                    confidence=1.0,
                    file_id=symbol_file_id,
                    symbol_id=symbol_id,
                ):
                    edges_inserted += 1
        else:
            if insert_workflow_edge(
                conn=conn,
                workflow_id=workflow_id,
                from_node_id=workflow_node_id,
                to_node_id=symbol_node_id,
                edge_kind="step_uses_symbol",
                ordinal=ordinal_base + i,
                evidence=evidence,
                confidence=1.0,
                file_id=symbol_file_id,
                symbol_id=symbol_id,
            ):
                edges_inserted += 1
    return nodes_inserted, edges_inserted


def build_workflows_for_entity(
    conn: sqlite3.Connection,
    repo_root: Path,
    repo_id: int,
    entity: sqlite3.Row,
    roots: list[sqlite3.Row],
    stats: BuildStats,
    parse_failures: list[dict[str, Any]],
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
    yaml_paths = get_entity_yaml_paths(conn, repo_id, entity_id)

    for yaml_path in yaml_paths:
        actions: list[tuple[int, str, str]] = []
        action_source = "yaml"

        doc: dict[str, Any] | None = None
        yaml_file = repo_root / yaml_path

        if yaml is None:
            record_parse_failure(
                parse_failures,
                stats,
                severity="P2",
                entity_id=int(entity_id),
                entity_name=str(entity_name),
                source_file=yaml_path,
                reason="yaml_parser_unavailable",
                detail="PyYAML not installed",
            )
        elif not yaml_file.exists():
            record_parse_failure(
                parse_failures,
                stats,
                severity="P2",
                entity_id=int(entity_id),
                entity_name=str(entity_name),
                source_file=yaml_path,
                reason="source_file_missing",
                detail=str(yaml_file),
            )
        else:
            with yaml_file.open("r", encoding="utf-8") as handle:
                try:
                    loaded = yaml.safe_load(handle)
                except yaml.YAMLError as exc:
                    record_parse_failure(
                        parse_failures,
                        stats,
                        severity="P0",
                        entity_id=int(entity_id),
                        entity_name=str(entity_name),
                        source_file=yaml_path,
                        reason="invalid_yaml_syntax",
                        detail=str(exc),
                    )
                    loaded = None

            if loaded is None:
                doc = {}
            elif not isinstance(loaded, dict):
                record_parse_failure(
                    parse_failures,
                    stats,
                    severity="P1",
                    entity_id=int(entity_id),
                    entity_name=str(entity_name),
                    source_file=yaml_path,
                    reason="invalid_top_level_type",
                    detail=type(loaded).__name__,
                )
            else:
                doc = loaded

        if doc:
            actions = extract_yaml_actions(doc)

        if not actions:
            fallback_actions = get_yaml_actions_from_symbols_for_path(
                conn=conn,
                repo_id=repo_id,
                entity_id=entity_id,
                yaml_path=yaml_path,
            )
            if fallback_actions:
                actions = [
                    (idx, action, evidence)
                    for idx, (action, evidence) in enumerate(fallback_actions, start=1)
                ]
                action_source = "yaml_symbols"

        if not actions:
            continue

        wf_id, workflow_inserted = insert_workflow(
            conn,
            repo_id=repo_id,
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

        chain_nodes, chain_edges = build_action_chain_for_workflow(
            conn=conn,
            workflow_id=wf_id,
            entity_id=entity_id,
            source_file=yaml_path,
            source_kind="yaml" if action_source == "yaml" else "inference",
            actions=actions,
            repo_id=repo_id,
        )
        stats.workflow_nodes_inserted += chain_nodes
        stats.workflow_edges_inserted += chain_edges

        if workflow_inserted:
            wf_count += 1

        nodes_added, edges_added = link_workflow_to_symbol_roots(
            conn=conn,
            workflow_id=wf_id,
            entity_id=entity_id,
            workflow_type="allowed_operations",
            roots=roots,
            source_symbol_id=None,
            source_kind="yaml" if action_source == "yaml" else "inference",
        )
        stats.workflow_nodes_inserted += nodes_added
        stats.workflow_edges_inserted += edges_added

    # ------------------------------------------------------------------
    # 2. AllowedOperationsHandler workflow (surface even if no YAML)
    # ------------------------------------------------------------------
    for r in roots:
        if r["mapping_type"] != "allowed_operations_handler":
            continue

        wf_id, workflow_inserted = insert_workflow(
            conn,
            repo_id=repo_id,
            entity_id=entity_id,
            name=f"{entity_name} operations handler",
            workflow_type="allowed_operations",
            source_kind="class",
            source_file=None,
            file_id=int(r["symbol_file_id"])
            if r["symbol_file_id"] is not None
            else None,
            source_symbol_id=r["symbol_id"],
            reason=f"AllowedOperationsHandler class present: {r['symbol_name']}",
        )

        if wf_id is not None and workflow_inserted:
            wf_count += 1

        if wf_id is not None:
            chain_nodes, chain_edges = build_action_chain_for_workflow(
                conn=conn,
                workflow_id=wf_id,
                entity_id=entity_id,
                source_file=None,
                source_kind="class",
                actions=[],
                repo_id=repo_id,
            )
            stats.workflow_nodes_inserted += chain_nodes
            stats.workflow_edges_inserted += chain_edges

            nodes_added, edges_added = link_workflow_to_symbol_roots(
                conn=conn,
                workflow_id=wf_id,
                entity_id=entity_id,
                workflow_type="allowed_operations",
                roots=roots,
                source_symbol_id=r["symbol_id"],
                source_kind="class",
            )
            stats.workflow_nodes_inserted += nodes_added
            stats.workflow_edges_inserted += edges_added

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
            repo_id=repo_id,
            entity_id=entity_id,
            name=f"{entity_name} {wf_type} workflow",
            workflow_type=wf_type,
            source_kind="class",
            source_file=None,
            file_id=int(r["symbol_file_id"])
            if r["symbol_file_id"] is not None
            else None,
            source_symbol_id=r["symbol_id"],
            reason=f"Discovered from role={r['mapping_type']} symbol={r['symbol_name']}",
        )

        if wf_id is not None and workflow_inserted:
            wf_count += 1

        if wf_id is not None:
            chain_nodes, chain_edges = build_action_chain_for_workflow(
                conn=conn,
                workflow_id=wf_id,
                entity_id=entity_id,
                source_file=None,
                source_kind="class",
                actions=[],
                repo_id=repo_id,
            )
            stats.workflow_nodes_inserted += chain_nodes
            stats.workflow_edges_inserted += chain_edges

            nodes_added, edges_added = link_workflow_to_symbol_roots(
                conn=conn,
                workflow_id=wf_id,
                entity_id=entity_id,
                workflow_type=wf_type,
                roots=roots,
                source_symbol_id=r["symbol_id"],
                source_kind="class",
            )
            stats.workflow_nodes_inserted += nodes_added
            stats.workflow_edges_inserted += edges_added

    return wf_count


def build(db: str, repo_root: Path, repo_id: int, reset: bool) -> BuildStats:
    stats = BuildStats()
    unresolved_sources: list[dict[str, Any]] = []
    parse_failures: list[dict[str, Any]] = []

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
            conn.execute(
                "DELETE FROM workflow_edges WHERE workflow_id IN (SELECT id FROM workflows WHERE repo_id = ?)",
                (repo_id,),
            )
            conn.execute(
                "DELETE FROM workflow_nodes WHERE workflow_id IN (SELECT id FROM workflows WHERE repo_id = ?)",
                (repo_id,),
            )
            conn.execute("DELETE FROM workflows WHERE repo_id = ?", (repo_id,))
            conn.execute("DELETE FROM openapi_file_ref_edges WHERE repo_id = ?", (repo_id,))
            conn.commit()

        entities = get_entities(conn, repo_id)
        for entity in tqdm(entities, desc="Building workflows", unit="entity"):
            roots = get_entity_roots(conn, repo_id, entity["id"])
            wf = build_workflows_for_entity(
                conn=conn,
                repo_root=repo_root,
                repo_id=repo_id,
                entity=entity,
                roots=roots,
                stats=stats,
                parse_failures=parse_failures,
            )
            stats.workflows_inserted += wf
            stats.entities_processed += 1

            if stats.entities_processed % 500 == 0:
                conn.commit()

        materialize_openapi_ref_edges(conn, stats, repo_id)
        backfill_workflow_file_ids(conn, stats, unresolved_sources, repo_id)

        conn.commit()
    finally:
        conn.close()

    write_jsonl(UNRESOLVED_WORKFLOW_FILE_IDS_LOG, unresolved_sources)
    write_jsonl(WORKFLOW_PARSE_FAILURES_LOG, parse_failures)

    return stats


@click.group()
def cli() -> None:
    pass


@cli.command("build")
@click.option("--repo", required=True, help="Registered repository key to build.")
@click.option(
    "--db",
    default=DEFAULT_DB,
    show_default=True,
    help="Path to SQLite catalog database.",
)
@click.option(
    "--repo-root",
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    default=None,
    help="Repository root path used to resolve YAML/source files.",
)
@click.option("--reset", is_flag=True, help="Delete workflow tables before rebuilding.")
def build_command(db: str, repo: str, repo_root: Path | None, reset: bool) -> None:
    conn = get_connection(db)
    try:
        repository = get_repository(conn, repo)
        resolved_root = repo_root.resolve() if repo_root is not None else resolve_repository_root(conn, repo)
    finally:
        conn.close()
    stats = build(db=db, repo_root=resolved_root, repo_id=int(repository["id"]), reset=reset)
    click.echo(f"Processed entities:  {stats.entities_processed}")
    click.echo(f"Workflows inserted:  {stats.workflows_inserted}")
    click.echo(f"Workflow nodes inserted:  {stats.workflow_nodes_inserted}")
    click.echo(f"Workflow edges inserted:  {stats.workflow_edges_inserted}")
    click.echo(f"OpenAPI ref edges:  {stats.openapi_ref_edges_inserted}")
    click.echo(f"file_id backfilled:  {stats.file_ids_backfilled}")
    click.echo(f"Unresolved sources:  {stats.unresolved_source_files}")
    click.echo(
        "Parse failures (P0/P1/P2):  "
        f"{stats.parse_failures_p0}/{stats.parse_failures_p1}/{stats.parse_failures_p2}"
    )
    click.echo(f"Unresolved file log: {UNRESOLVED_WORKFLOW_FILE_IDS_LOG.as_posix()}")
    click.echo(f"Parse failure log:  {WORKFLOW_PARSE_FAILURES_LOG.as_posix()}")


if __name__ == "__main__":
    cli()
