#!/usr/bin/env python3

from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import click

try:
    from catalog.db import get_connection
    from catalog.repositories import get_repository
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from catalog.db import get_connection
    from catalog.repositories import get_repository

DEFAULT_DB = "catalog/catalog.db"
SECURITY_UNRESOLVED_LOG = Path("outputs/security_unresolved_keys.jsonl")
ENTITY_SECURITY_UNRESOLVED_CATEGORY = "entity_security_key_unresolved"


@dataclass
class BuildStats:
    rows_inserted: int = 0
    security_keys_linked: int = 0
    security_keys_unresolved: int = 0


def ensure_entity_access_table(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS entity_access_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repo_id INTEGER NOT NULL,
            entity_id INTEGER NOT NULL,
            surface TEXT NOT NULL,
            record_id INTEGER NOT NULL,
            link_type TEXT NOT NULL,
            evidence_file_id INTEGER,
            evidence_symbol_id INTEGER,
            confidence_mode TEXT NOT NULL DEFAULT 'deterministic_exact',
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(entity_id) REFERENCES entity_nodes(id) ON DELETE CASCADE,
            FOREIGN KEY(evidence_file_id) REFERENCES files(id) ON DELETE SET NULL,
            FOREIGN KEY(evidence_symbol_id) REFERENCES symbols(id) ON DELETE SET NULL,
            UNIQUE(repo_id, entity_id, surface, record_id, link_type, evidence_file_id, evidence_symbol_id)
        );

        CREATE INDEX IF NOT EXISTS idx_entity_access_links_entity_surface
            ON entity_access_links(entity_id, surface);
        CREATE INDEX IF NOT EXISTS idx_entity_access_links_surface_record
            ON entity_access_links(surface, record_id);
        CREATE INDEX IF NOT EXISTS idx_entity_access_links_evidence_file
            ON entity_access_links(evidence_file_id);
        """
    )


def ensure_dbschema_file_id_column(conn: sqlite3.Connection, repo_id: int) -> None:
    cols = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(dbschema_tables)").fetchall()
    }
    if "file_id" not in cols:
        conn.execute("ALTER TABLE dbschema_tables ADD COLUMN file_id INTEGER")

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_dbschema_tables_file_id ON dbschema_tables(file_id)"
    )

    conn.execute(
        """
        UPDATE dbschema_tables
        SET file_id = (
            SELECT f.id
            FROM files f
            WHERE f.repo_id = dbschema_tables.repo_id
              AND f.path = dbschema_tables.source_file
            LIMIT 1
        )
        WHERE repo_id = ?
          AND (file_id IS NULL OR file_id = 0)
          AND source_file IS NOT NULL
        """,
        (repo_id,),
    )


def _entity_file_anchors_cte() -> str:
    return """
    WITH entity_file_anchors AS (
        SELECT DISTINCT em.entity_id, em.file_id
        FROM entity_mappings em
        WHERE em.repo_id = :repo_id
          AND em.entity_id IS NOT NULL
          AND em.file_id IS NOT NULL

        UNION

        SELECT DISTINCT em.entity_id, f.id AS file_id
        FROM entity_mappings em
        JOIN files f
          ON f.repo_id = em.repo_id AND f.path = em.source_text
        WHERE em.repo_id = :repo_id
          AND em.entity_id IS NOT NULL
          AND em.source_text IS NOT NULL

        UNION

        SELECT DISTINCT eo.entity_id, f.id AS file_id
        FROM entity_occurrences eo
        JOIN files f
          ON f.id = eo.source_file_id
        WHERE eo.repo_id = :repo_id
          AND eo.source_file_id IS NOT NULL

        UNION

        SELECT DISTINCT w.entity_id, w.file_id
        FROM workflows w
        WHERE w.repo_id = :repo_id
          AND w.entity_id IS NOT NULL
          AND w.file_id IS NOT NULL

        UNION

        SELECT DISTINCT r.entity_id, r.file_id
        FROM rest_endpoints r
        WHERE r.repo_id = :repo_id
          AND r.entity_id IS NOT NULL
          AND r.file_id IS NOT NULL
    )
    """


def _run_insert(conn: sqlite3.Connection, sql: str, repo_id: int) -> int:
    before = conn.total_changes
    conn.execute(sql, {"repo_id": repo_id})
    return conn.total_changes - before


def link_by_file_overlap(conn: sqlite3.Connection, repo_id: int) -> int:
    total = 0

    total += _run_insert(
        conn,
        _entity_file_anchors_cte()
        + """
        INSERT OR IGNORE INTO entity_access_links (
            repo_id, entity_id,
            surface,
            record_id,
            link_type,
            evidence_file_id,
            confidence_mode,
            notes
        )
        SELECT DISTINCT
            :repo_id, efa.entity_id,
            'dbschema_table',
            dt.id,
            'file_id_overlap',
            dt.file_id,
            'deterministic_exact',
            'entity anchor file_id equals dbschema source file_id'
        FROM entity_file_anchors efa
        JOIN dbschema_tables dt
          ON dt.file_id = efa.file_id
         AND dt.repo_id = :repo_id
        """,
        repo_id,
    )

    total += _run_insert(
        conn,
        _entity_file_anchors_cte()
        + """
        INSERT OR IGNORE INTO entity_access_links (
            repo_id, entity_id,
            surface,
            record_id,
            link_type,
            evidence_file_id,
            confidence_mode,
            notes
        )
        SELECT DISTINCT
            :repo_id, efa.entity_id,
            'rest_endpoint',
            r.id,
            'file_id_overlap',
            r.file_id,
            'deterministic_exact',
            'entity anchor file_id equals rest endpoint source file_id'
        FROM entity_file_anchors efa
        JOIN rest_endpoints r
          ON r.file_id = efa.file_id
         AND r.repo_id = :repo_id
        """,
        repo_id,
    )

    total += _run_insert(
        conn,
        _entity_file_anchors_cte()
        + """
        INSERT OR IGNORE INTO entity_access_links (
            repo_id, entity_id,
            surface,
            record_id,
            link_type,
            evidence_file_id,
            confidence_mode,
            notes
        )
        SELECT DISTINCT
            :repo_id, efa.entity_id,
            'workflow',
            w.id,
            'file_id_overlap',
            w.file_id,
            'deterministic_exact',
            'entity anchor file_id equals workflow source file_id'
        FROM entity_file_anchors efa
        JOIN workflows w
          ON w.file_id = efa.file_id
         AND w.repo_id = :repo_id
        """,
        repo_id,
    )

    return total


def link_by_entity_fk(conn: sqlite3.Connection, repo_id: int) -> int:
    total = 0

    total += _run_insert(
        conn,
        """
        INSERT OR IGNORE INTO entity_access_links (
            repo_id, entity_id,
            surface,
            record_id,
            link_type,
            evidence_file_id,
            confidence_mode,
            notes
        )
        SELECT
            :repo_id, w.entity_id,
            'workflow',
            w.id,
            'entity_fk',
            w.file_id,
            'deterministic_exact',
            'direct workflow.entity_id'
        FROM workflows w
        WHERE w.repo_id = :repo_id AND w.entity_id IS NOT NULL
        """,
        repo_id,
    )

    total += _run_insert(
        conn,
        """
        INSERT OR IGNORE INTO entity_access_links (
            repo_id, entity_id,
            surface,
            record_id,
            link_type,
            evidence_file_id,
            confidence_mode,
            notes
        )
        SELECT
            :repo_id, r.entity_id,
            'rest_endpoint',
            r.id,
            'entity_fk',
            r.file_id,
            'deterministic_exact',
            'direct rest_endpoints.entity_id'
        FROM rest_endpoints r
        WHERE r.repo_id = :repo_id AND r.entity_id IS NOT NULL
        """,
        repo_id,
    )

    return total


def link_by_table_name_match(conn: sqlite3.Connection, repo_id: int) -> int:
    """
    Link entities to dbschema_tables by matching the repository declaration's table_name
    to dbschema_tables.table_name (case-insensitive).

    This is the primary tracing path for dbschema: a single shared source
    file (dbschema.inc) means file_id overlap never fires for this surface.
    """
    return _run_insert(
        conn,
        """
        INSERT OR IGNORE INTO entity_access_links (
            repo_id, entity_id,
            surface,
            record_id,
            link_type,
            evidence_file_id,
            confidence_mode,
            notes
        )
        SELECT DISTINCT
            :repo_id, eo.entity_id,
            'dbschema_table',
            dt.id,
            'table_name_match',
            dt.file_id,
            'deterministic_exact',
            'entity_occurrences.table_name = dbschema_tables.table_name'
        FROM entity_occurrences eo
        JOIN dbschema_tables dt
          ON LOWER(dt.table_name) = LOWER(eo.table_name)
        WHERE dt.repo_id = :repo_id
          AND eo.repo_id = :repo_id
          AND eo.table_name IS NOT NULL
          AND TRIM(eo.table_name) <> ''
        """,
        repo_id,
    )


def parse_security_operation_key(op_key: str) -> dict[str, str] | None:
    parts = [part.strip() for part in op_key.strip("/").split("/")]
    if len(parts) < 3 or any(not part for part in parts[:3]):
        return None
    return {
        "module": parts[0].lower(),
        "route": parts[1].lower(),
        "entity": parts[2].lower(),
        "action": "/".join(parts[3:]).lower(),
        "surface": "security_resource" if len(parts) == 3 else "security_operation",
    }


def _insert_access_link(
    conn: sqlite3.Connection,
    repo_id: int,
    entity_id: int,
    surface: str,
    record_id: int,
    link_type: str,
    evidence_file_id: int | None,
    notes: str,
) -> int:
    existing = conn.execute(
        """
        SELECT 1
        FROM entity_access_links
        WHERE repo_id = ? AND entity_id = ? AND surface = ? AND record_id = ?
          AND link_type = ? AND evidence_file_id IS ?
          AND evidence_symbol_id IS NULL
        LIMIT 1
        """,
        (repo_id, entity_id, surface, record_id, link_type, evidence_file_id),
    ).fetchone()
    if existing:
        return 0
    cur = conn.execute(
        """
        INSERT INTO entity_access_links (
            repo_id, entity_id, surface, record_id, link_type,
            evidence_file_id, confidence_mode, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, 'deterministic_exact', ?)
        """,
        (repo_id, entity_id, surface, record_id, link_type, evidence_file_id, notes),
    )
    return int(cur.rowcount)


def _security_key_diagnostic(
    row: sqlite3.Row,
    parsed: dict[str, str] | None,
    reason: str,
    candidates: list[tuple[int, str]],
) -> dict:
    record = {
        "category": ENTITY_SECURITY_UNRESOLVED_CATEGORY,
        "security_operation_id": int(row["id"]),
        "op_key": str(row["op_key"]),
        "source_file": row["source_file"],
        "file_id": row["file_id"],
        "reason": reason,
        "candidate_entities": [
            {"entity_id": entity_id, "module": module}
            for entity_id, module in candidates
        ],
    }
    if parsed is not None:
        record["parsed"] = parsed
    return record


def _write_security_key_diagnostics(records: list[dict]) -> None:
    path = SECURITY_UNRESOLVED_LOG
    existing_lines: list[str] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if value.get("category") != ENTITY_SECURITY_UNRESOLVED_CATEGORY:
                existing_lines.append(line)
    new_lines = [
        json.dumps(value, ensure_ascii=False, sort_keys=True) for value in records
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [*existing_lines, *new_lines]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def link_security_surfaces(
    conn: sqlite3.Connection,
    repo_id: int,
    diagnostics: list[dict] | None = None,
) -> tuple[int, int]:
    """Link security surfaces by exact key evidence and report rejected keys."""
    if diagnostics is None:
        diagnostics = []
    entities_by_name: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for row in conn.execute(
        """
        SELECT en.id, en.name, eo.module
        FROM entity_nodes en
        JOIN entity_occurrences eo ON eo.entity_id = en.id
        WHERE eo.repo_id = ?
        """,
        (repo_id,),
    ):
        if row["name"] and row["module"]:
            entities_by_name[str(row["name"]).strip().lower()].append(
                (int(row["id"]), str(row["module"]).strip().lower())
            )

    total = 0
    linked_key_count = 0
    linked_keys_by_entity: dict[int, set[str]] = defaultdict(set)
    for row in conn.execute(
        "SELECT id, op_key, source_file, file_id FROM security_operations WHERE repo_id = ? ORDER BY id",
        (repo_id,),
    ):
        parsed = parse_security_operation_key(str(row["op_key"]))
        if parsed is None:
            diagnostics.append(_security_key_diagnostic(row, None, "malformed_key", []))
            continue
        name_candidates = entities_by_name.get(parsed["entity"], [])
        candidates = [
            (entity_id, module)
            for entity_id, module in name_candidates
            if module == parsed["module"]
        ]
        if len(candidates) == 0:
            reason = "module_mismatched" if name_candidates else "entity_unmatched"
            diagnostics.append(
                _security_key_diagnostic(row, parsed, reason, name_candidates)
            )
            continue
        if len(candidates) != 1:
            diagnostics.append(
                _security_key_diagnostic(row, parsed, "entity_ambiguous", candidates)
            )
            continue
        entity_id = candidates[0][0]
        linked_key_count += 1
        linked_keys_by_entity[entity_id].add(str(row["op_key"]))
        total += _insert_access_link(
            conn,
            repo_id,
            entity_id,
            parsed["surface"],
            int(row["id"]),
            "security_key_match",
            row["file_id"],
            (
                f"parsed key module={parsed['module']} route={parsed['route']} "
                f"entity={parsed['entity']} action={parsed['action'] or '<resource>'}; "
                f"source={row['source_file']}"
            ),
        )

    for entity_id, op_keys in linked_keys_by_entity.items():
        for op_key in sorted(op_keys):
            for row in conn.execute(
                """
                SELECT DISTINCT sp.id, sp.file_id
                FROM security_policy_values spv
                JOIN security_policy_eops spe ON spe.policy_value_id = spv.id
                JOIN security_policies sp ON sp.id = spv.policy_id
                WHERE sp.repo_id = ? AND spe.op_key = ?
                ORDER BY sp.id
                """,
                (repo_id, op_key),
            ):
                total += _insert_access_link(
                    conn,
                    repo_id,
                    entity_id,
                    "security_policy",
                    int(row["id"]),
                    "operation_policy_grant",
                    row["file_id"],
                    f"policy supported by security operation key={op_key}",
                )

            for row in conn.execute(
                """
                SELECT DISTINCT smi.id AS item_id, sm.id AS menu_id, sm.file_id
                FROM security_menu_op_links mol
                JOIN security_menu_items smi ON smi.id = mol.menu_item_id
                JOIN security_menus sm ON sm.id = smi.menu_id
                WHERE sm.repo_id = ? AND mol.op_key = ? AND mol.operation_id IS NOT NULL
                ORDER BY smi.id
                """,
                (repo_id, op_key),
            ):
                total += _insert_access_link(
                    conn,
                    repo_id,
                    entity_id,
                    "security_menu_item",
                    int(row["item_id"]),
                    "operation_menu_item",
                    row["file_id"],
                    f"menu item supported by security operation key={op_key}",
                )
                total += _insert_access_link(
                    conn,
                    repo_id,
                    entity_id,
                    "security_menu",
                    int(row["menu_id"]),
                    "operation_menu",
                    row["file_id"],
                    f"menu supported by security operation key={op_key}",
                )
    return total, linked_key_count


def build(db: str, reset: bool, repo_key: str) -> BuildStats:
    conn = get_connection(db)
    stats = BuildStats()
    diagnostics: list[dict] = []

    try:
        ensure_entity_access_table(conn)
        repo_id = int(get_repository(conn, repo_key)["id"])
        ensure_dbschema_file_id_column(conn, repo_id)
        if reset:
            conn.execute(
                "DELETE FROM entity_access_links WHERE repo_id = ?", (repo_id,)
            )

        stats.rows_inserted += link_by_file_overlap(conn, repo_id)
        security_links, linked_key_count = link_security_surfaces(
            conn, repo_id, diagnostics
        )
        stats.rows_inserted += security_links
        stats.security_keys_linked = linked_key_count
        stats.security_keys_unresolved = len(diagnostics)
        _write_security_key_diagnostics(diagnostics)
        stats.rows_inserted += link_by_entity_fk(conn, repo_id)
        stats.rows_inserted += link_by_table_name_match(conn, repo_id)

        conn.commit()
    finally:
        conn.close()

    return stats


@click.group()
def cli() -> None:
    pass


@cli.command("build")
@click.option("--db", default=DEFAULT_DB, show_default=True)
@click.option(
    "--reset/--no-reset",
    default=True,
    show_default=True,
    help="Rebuild entity access links from a clean snapshot.",
)
@click.option("--repo", "repo_key", required=True, help="Registered repository key.")
def build_command(db: str, reset: bool, repo_key: str) -> None:
    stats = build(db=db, reset=reset, repo_key=repo_key)
    click.echo(f"Entity access links inserted: {stats.rows_inserted}")
    click.echo(f"Security keys linked: {stats.security_keys_linked}")
    click.echo(f"Security keys unresolved: {stats.security_keys_unresolved}")


if __name__ == "__main__":
    cli()
