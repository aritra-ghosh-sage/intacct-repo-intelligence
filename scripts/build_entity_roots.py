#!/usr/bin/env python3
# scripts/build_entity_roots.py

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import click

try:
    from catalog.db import get_connection, require_foreign_key_integrity
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from catalog.db import get_connection, require_foreign_key_integrity

DEFAULT_DB = os.environ.get("CATALOG_DB", "catalog/catalog.db")
DEFAULT_REPO_ROOT = "/home/aritraghosh/projects/ia-app"
DEFAULT_REPORT = "validation/phase2b_report.md"

ROLE_WEIGHT: dict[str, float] = {
    "manager": 1.00,
    "editor": 0.90,
    "lister": 0.90,
    "form_editor": 0.90,
    "allowed_operations_handler": 0.85,
    "approval_manager": 0.85,
    "entity_manager": 0.85,
    "entry_manager": 0.85,
    "item_manager": 0.85,
    "reverse_manager": 0.75,
    "batch_manager": 0.75,
    "pick_manager": 0.75,
    "picker": 0.40,
    "batch_picker": 0.40,
    "pick_picker": 0.40,
}

ROLE_REASON: dict[str, str] = {
    role: f"Deterministic role: {role.replace('_', ' ')}" for role in ROLE_WEIGHT
}


def q(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> list[Any]:
    return conn.execute(sql, params).fetchall()


def _resolve_repo_id(conn: Any, repo_root: str) -> int | None:
    normalized_root = str(Path(repo_root).resolve())
    row = conn.execute(
        "SELECT id FROM repos WHERE local_root = ?",
        (normalized_root,),
    ).fetchone()
    return int(row["id"]) if row else None


def ensure_entity_roots_columns(conn: Any) -> None:
    cols = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(entity_roots)").fetchall()
    }
    if "is_shared" not in cols:
        conn.execute("ALTER TABLE entity_roots ADD COLUMN is_shared INTEGER DEFAULT 0")
        conn.commit()


def build_entity_roots(conn: Any, reset: bool, repo_id: int) -> int:
    ensure_entity_roots_columns(conn)

    if reset:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM entity_roots WHERE repo_id = ?", (repo_id,))

    inserted = 0
    rows = conn.execute(
        """
        SELECT
            em.entity_id,
            em.symbol_id,
            em.mapping_type
        FROM entity_mappings em
        JOIN entity_occurrences eo
          ON eo.repo_id = em.repo_id
         AND eo.entity_id = em.entity_id
        WHERE em.symbol_id IS NOT NULL
          AND em.repo_id = ?
        """,
        (repo_id,),
    ).fetchall()

    for row in rows:
        role = row["mapping_type"]
        if role not in ROLE_WEIGHT:
            continue

        conn.execute(
            """
            INSERT INTO entity_roots(
                repo_id,
                entity_id,
                symbol_id,
                role,
                weight,
                reason,
                is_shared
            )
            VALUES (?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(repo_id, entity_id, symbol_id) DO UPDATE SET
                role = excluded.role,
                weight = excluded.weight,
                reason = excluded.reason,
                is_shared = 0
            """,
            (
                repo_id,
                row["entity_id"],
                row["symbol_id"],
                role,
                ROLE_WEIGHT[role],
                ROLE_REASON[role],
            ),
        )
        inserted += 1

    # conn.execute("UPDATE entity_roots SET is_shared = 0") #not required to reset the state.
    # the insert stmnt explicitly sets is_shared=0 for all rows, so no need to reset it before the update.
    conn.execute(
        """
        UPDATE entity_roots
        SET is_shared = 1
        WHERE repo_id = ? AND symbol_id IN (
            SELECT symbol_id
            FROM entity_roots
            WHERE repo_id = ?
            GROUP BY symbol_id
            HAVING COUNT(DISTINCT entity_id) > 1
        )
        """,
        (repo_id, repo_id),
    )

    shared_rows = conn.execute(
        "SELECT COUNT(*) AS c FROM entity_roots WHERE repo_id = ? AND is_shared = 1",
        (repo_id,),
    ).fetchone()["c"]
    shared_symbols = conn.execute(
        """
        SELECT COUNT(*) AS c
        FROM (
            SELECT symbol_id
            FROM entity_roots
            WHERE repo_id = ? AND is_shared = 1
            GROUP BY symbol_id
        ) x
        """,
        (repo_id,),
    ).fetchone()["c"]

    require_foreign_key_integrity(conn, context="entity roots build")
    conn.commit()
    click.echo(f"Inserted entity_roots rows: {inserted}")
    click.echo(f"Shared root rows (is_shared=1): {shared_rows}")
    click.echo(f"Shared symbols: {shared_symbols}")
    return inserted


def check_structural(
    conn: Any, repo_root: str = DEFAULT_REPO_ROOT
) -> list[tuple[str, list[Any]]]:
    findings: list[tuple[str, list[Any]]] = []
    repo_id = _resolve_repo_id(conn, repo_root)

    sql = """
        SELECT DISTINCT en.name
        FROM entity_occurrences eo
        JOIN entity_nodes en ON en.id = eo.entity_id
        WHERE (eo.ent_file IS NULL OR eo.ent_file = '')
    """
    params: tuple[Any, ...] = ()
    if repo_id is not None:
        sql += " AND eo.repo_id = ?"
        params = (repo_id,)
    missing_ent = q(conn, sql, params)
    findings.append(
        ("entity_occurrences without ent_file", [r["name"] for r in missing_ent])
    )

    orphan_mappings = q(
        conn,
        """
        SELECT em.entity_id, em.symbol_id
        FROM entity_mappings em
        LEFT JOIN symbols s
            ON s.id = em.symbol_id
        WHERE s.id IS NULL
        """,
    )
    findings.append(
        (
            "entity_mappings pointing to missing symbols",
            [(r["entity_id"], r["symbol_id"]) for r in orphan_mappings],
        )
    )

    orphan_roots = q(
        conn,
        """
        SELECT er.entity_id, er.symbol_id
        FROM entity_roots er
        LEFT JOIN entity_mappings em
            ON em.entity_id = er.entity_id
           AND em.symbol_id = er.symbol_id
        WHERE em.id IS NULL
        """,
    )
    findings.append(
        (
            "entity_roots not backed by entity_mappings",
            [(r["entity_id"], r["symbol_id"]) for r in orphan_roots],
        )
    )

    no_seed_at_075 = q(
        conn,
        """
        SELECT en.name
        FROM entity_nodes en
        WHERE NOT EXISTS (
            SELECT 1
            FROM entity_roots
            WHERE entity_id = en.id
              AND weight >= 0.75
        )
        """,
    )
    findings.append(
        (
            "entities with 0 seed roots at weight >= 0.75",
            [r["name"] for r in no_seed_at_075],
        )
    )

    dup_root_symbols = q(
        conn,
        """
        SELECT symbol_id, COUNT(DISTINCT entity_id) AS entities
        FROM entity_roots
        GROUP BY symbol_id
        HAVING entities > 1
        """,
    )
    findings.append(
        (
            "symbols acting as root for multiple entities",
            [(r["symbol_id"], r["entities"]) for r in dup_root_symbols],
        )
    )

    return findings


def check_filesystem(conn: Any, repo_root: str) -> list[tuple[str, list[Any]]]:
    findings: list[tuple[str, list[Any]]] = []
    repo_id = _resolve_repo_id(conn, repo_root)

    missing_ent_files: list[tuple[str, str | None]] = []
    sql = """
        SELECT en.name, eo.ent_file
        FROM entity_occurrences eo
        JOIN entity_nodes en ON en.id = eo.entity_id
        WHERE eo.ent_file IS NOT NULL AND eo.ent_file <> ''
    """
    params: tuple[Any, ...] = ()
    if repo_id is not None:
        sql += " AND eo.repo_id = ?"
        params = (repo_id,)
    for row in q(conn, sql, params):
        full = os.path.join(repo_root, row["ent_file"] or "")
        if not row["ent_file"] or not os.path.exists(full):
            missing_ent_files.append((row["name"], row["ent_file"]))

    findings.append(
        ("entity_occurrences with .ent files missing on disk", missing_ent_files)
    )

    missing_class_files: list[tuple[str, str | None, str | None]] = []
    rows = q(
        conn,
        """
        SELECT
            en.name AS entity_name,
            em.mapping_type,
            em.source_text
        FROM entity_mappings em
        JOIN entity_nodes en ON en.id = em.entity_id
        WHERE em.source_text IS NOT NULL
        """,
    )
    for row in rows:
        candidate = os.path.join(repo_root, row["source_text"])
        if not os.path.exists(candidate):
            missing_class_files.append(
                (row["entity_name"], row["mapping_type"], row["source_text"])
            )

    findings.append(
        (
            "companion class files referenced in entity_mappings missing on disk",
            missing_class_files,
        )
    )

    return findings


def check_repo_vs_db(conn: Any, repo_root: str) -> list[tuple[str, list[Any]]]:
    findings: list[tuple[str, list[Any]]] = []
    repo_id = _resolve_repo_id(conn, repo_root)

    repo_ents: set[str] = set()
    app_root = os.path.join(repo_root, "app")
    for root, _, files in os.walk(app_root):
        for name in files:
            if not name.endswith(".ent"):
                continue
            rel = os.path.relpath(os.path.join(root, name), repo_root)
            repo_ents.add(rel.replace(os.sep, "/"))

    sql = "SELECT ent_file FROM entity_occurrences WHERE ent_file IS NOT NULL AND ent_file <> ''"
    params: tuple[Any, ...] = ()
    if repo_id is not None:
        sql += " AND repo_id = ?"
        params = (repo_id,)
    db_ents = {r["ent_file"] for r in q(conn, sql, params)}
    only_in_repo = sorted(repo_ents - db_ents)
    only_in_db = sorted(db_ents - repo_ents)

    findings.append((".ent files present in repo but missing in DB", only_in_repo))
    findings.append((".ent files present in DB but missing in repo", only_in_db))
    return findings


def check_role_distribution(conn: Any) -> list[tuple[str, list[Any]]]:
    findings: list[tuple[str, list[Any]]] = []

    rows = q(
        conn,
        """
        SELECT role, COUNT(*) AS cnt
        FROM entity_roots
        GROUP BY role
        ORDER BY cnt DESC
        """,
    )
    findings.append(
        ("entity_roots role distribution", [(r["role"], r["cnt"]) for r in rows])
    )

    core_low_weight = q(
        conn,
        """
        SELECT er.symbol_id, er.role, er.weight
        FROM entity_roots er
        WHERE role = 'manager'
          AND weight < 0.9
        """,
    )
    findings.append(
        (
            "manager roles with unexpectedly low weight",
            [(r["symbol_id"], r["role"], r["weight"]) for r in core_low_weight],
        )
    )

    return findings


def check_ground_truth(conn: Any) -> list[tuple[str, list[str]]]:
    findings: list[tuple[str, list[str]]] = []
    ground_truth = {
        "APBill": ["APBillManager", "APBillEditor", "APBillLister"],
        "Vendor": ["VendorManager", "VendorEditor", "VendorLister"],
        "Customer": ["CustomerManager", "CustomerEditor", "CustomerLister"],
        "GLAccount": ["GLAccountManager", "GLAccountEditor", "GLAccountLister"],
        "GLBatch": ["GLBatchManager"],
    }

    for entity_name, expected in ground_truth.items():
        row = conn.execute(
            "SELECT id FROM entity_nodes WHERE name = ?", (entity_name,)
        ).fetchone()
        if not row:
            findings.append((f"{entity_name} not found in entity_nodes", []))
            continue

        got_rows = q(
            conn,
            """
            SELECT s.name
            FROM entity_roots er
            JOIN symbols s ON s.id = er.symbol_id
            WHERE er.entity_id = ?
              AND er.weight >= 0.75
            """,
            (row["id"],),
        )
        got = {r["name"] for r in got_rows}
        expected_set = set(expected)
        correct = expected_set & got
        missing = expected_set - got
        extra = got - expected_set

        precision = len(correct) / len(got) if got else 0.0
        recall = len(correct) / len(expected_set) if expected_set else 0.0
        findings.append(
            (
                f"ground truth: {entity_name}",
                [
                    f"precision={precision:.2f}",
                    f"recall={recall:.2f}",
                    f"missing={sorted(missing)}",
                    f"extra_top={sorted(list(extra))[:10]}",
                ],
            )
        )

    return findings


def write_report(
    all_findings: list[tuple[str, list[tuple[str, list[Any]]]]], path: str
) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)

    with out.open("w", encoding="utf-8") as handle:
        handle.write("# Phase 2B.1 Validation Report\n\n")
        for section_title, findings in all_findings:
            handle.write(f"## {section_title}\n\n")
            for label, items in findings:
                handle.write(f"### {label}\n\n")
                if not items:
                    handle.write("OK — no issues found.\n\n")
                    continue

                for item in items[:200]:
                    handle.write(f"- `{item}`\n")

                if len(items) > 200:
                    handle.write(f"\n_(truncated — {len(items)} total)_\n\n")
                else:
                    handle.write("\n")


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
    "--reset", is_flag=True, help="Reset entity_roots table before rebuilding."
)
@click.option("--repo", "repo_key", required=True, help="Registered repository key.")
def build_command(db: str, reset: bool, repo_key: str) -> None:
    conn = get_connection(db)
    try:
        from catalog.repositories import get_repository

        repo_id = int(get_repository(conn, repo_key)["id"])
        raise SystemExit(build_entity_roots(conn, reset=reset, repo_id=repo_id))
    finally:
        conn.close()


@cli.command("validate")
@click.option(
    "--db",
    default=DEFAULT_DB,
    show_default=True,
    help="Path to SQLite catalog database.",
)
@click.option(
    "--repo-root",
    default=DEFAULT_REPO_ROOT,
    show_default=True,
    help="Repository root path.",
)
@click.option(
    "--report",
    default=DEFAULT_REPORT,
    show_default=True,
    help="Output report markdown path.",
)
def validate_command(db: str, repo_root: str, report: str) -> None:
    conn = get_connection(db)
    try:
        all_findings = [
            ("Structural checks", check_structural(conn)),
            ("Filesystem checks", check_filesystem(conn, repo_root)),
            ("Repo vs DB coverage", check_repo_vs_db(conn, repo_root)),
            ("Role distribution", check_role_distribution(conn)),
            ("Ground truth checks", check_ground_truth(conn)),
        ]
        write_report(all_findings, report)
        click.echo(f"Wrote Phase 2B.1 validation report to: {report}")
    finally:
        conn.close()


if __name__ == "__main__":
    cli()
