#!/usr/bin/env python3

"""
Phase 2B.1 validator.

Runs structural, filesystem, and coverage validation against:
    entity_nodes
    entity_mappings
    entity_roots

and writes validation/phase2b_report.md
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

try:
    from scripts.build_entities import COMPANION_ROLES, RELATED_FILE_ROLES
    from scripts.build_entity_roots import ROLE_REASON, ROLE_WEIGHT
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.build_entities import COMPANION_ROLES, RELATED_FILE_ROLES
    from scripts.build_entity_roots import ROLE_REASON, ROLE_WEIGHT

DEFAULT_DB = "catalog/catalog.db"
DEFAULT_REPO_ROOT = "/home/aritraghosh/projects/main"
DEFAULT_REPORT = "validation/phase2b_report.md"
DEFAULT_ENTITIES = "config/entity_definitions.jsonl"
ENTITY_NODE_REPO_LOCAL_COLUMNS = {
    "ent_file",
    "module",
    "table_name",
    "view_name",
    "dummy",
    "source_file_id",
}
ENTITY_OCCURRENCE_REQUIRED_COLUMNS = {
    "id",
    "repo_id",
    "entity_id",
    "ent_file",
    "module",
    "table_name",
    "view_name",
    "dummy",
    "source_file_id",
    "extractor",
}


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def q(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list:
    return conn.execute(sql, params).fetchall()


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}


def missing_columns(
    conn: sqlite3.Connection, table: str, required: set[str]
) -> list[str]:
    return sorted(list(required - table_columns(conn, table)))


def resolve_repo_id(conn: sqlite3.Connection, repo_root: str) -> int | None:
    normalized_root = str(Path(repo_root).resolve())
    row = conn.execute(
        "SELECT id FROM repos WHERE local_root = ?",
        (normalized_root,),
    ).fetchone()
    return int(row["id"]) if row else None


def load_entities_jsonl(path: str) -> list[dict]:
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for idx, line in enumerate(f, start=1):
            payload = line.strip()
            if not payload:
                continue
            try:
                rows.append(json.loads(payload))
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSON in {path} at line {idx}: {exc}"
                ) from exc
    return rows


def check_scan_output_roles(entities_rows: list[dict]) -> list:
    findings = []

    expected = set(COMPANION_ROLES)
    invalid_role_sets = []
    for row in entities_rows:
        entity_name = row.get("entity_name")
        keys = set((row.get("companion_classes") or {}).keys())
        if keys != expected:
            invalid_role_sets.append(
                (
                    entity_name,
                    sorted(list(keys - expected)),
                    sorted(list(expected - keys)),
                )
            )

    findings.append(
        (
            "entity_definitions companion_classes keys must match expected role set",
            invalid_role_sets,
        )
    )
    return findings


def check_schema_contract(conn: sqlite3.Connection) -> list:
    findings = []

    entity_node_columns = table_columns(conn, "entity_nodes")
    forbidden = sorted(entity_node_columns & ENTITY_NODE_REPO_LOCAL_COLUMNS)
    findings.append(
        ("entity_nodes must not expose repo-local declaration columns", forbidden)
    )
    missing_occurrence_columns = missing_columns(
        conn, "entity_occurrences", ENTITY_OCCURRENCE_REQUIRED_COLUMNS
    )
    findings.append(
        ("entity_occurrences missing expected columns", missing_occurrence_columns)
    )
    return findings


def check_entities_jsonl_vs_db(
    conn: sqlite3.Connection, entities_rows: list[dict], repo_root: str
) -> list:
    findings = []

    required = {"id", "name", "entity_type"}
    missing = missing_columns(conn, "entity_nodes", required)
    findings.append(("entity_nodes missing expected columns", missing))
    if missing:
        return findings

    repo_id = resolve_repo_id(conn, repo_root)
    file_entities = {r["entity_name"]: r for r in entities_rows if r.get("entity_name")}
    sql = """
        SELECT en.id, en.name, eo.ent_file, eo.module, eo.table_name, eo.view_name, eo.dummy
        FROM entity_occurrences eo
        JOIN entity_nodes en ON en.id = eo.entity_id
    """
    params: tuple = ()
    if repo_id is not None:
        sql += " WHERE eo.repo_id = ?"
        params = (repo_id,)
    sql += " ORDER BY en.name"
    db_rows = q(conn, sql, params)
    db_entities = {r["name"]: r for r in db_rows}

    missing_in_db = sorted(set(file_entities) - set(db_entities))
    findings.append(("entities in JSONL but missing in entity_occurrences", missing_in_db))

    missing_in_jsonl = sorted(set(db_entities) - set(file_entities))
    findings.append(("entities in entity_occurrences but missing in JSONL", missing_in_jsonl))

    metadata_mismatches = []
    for name in sorted(set(file_entities) & set(db_entities)):
        src = file_entities[name]
        db = db_entities[name]
        expected_dummy = True if src.get("dummy") else False
        mismatches = []
        if (src.get("ent_file").lower() if src.get("ent_file") else None) != (db["ent_file"].lower() if db["ent_file"] else None):
            mismatches.append(("ent_file", src.get("ent_file"), db["ent_file"]))
        if (src.get("module").lower() if src.get("module") else None) != (db["module"].lower() if db["module"] else None):
            mismatches.append(("module", src.get("module"), db["module"]))
        if (src.get("table").lower() if src.get("table") else None) != (db["table_name"].lower() if db["table_name"] else None):
            mismatches.append(("table_name", src.get("table"), db["table_name"]))
        if (src.get("view").lower() if src.get("view") else None) != (db["view_name"].lower() if db["view_name"] else None):
            mismatches.append(("view_name", src.get("view"), db["view_name"]))
        if expected_dummy != (db["dummy"] if db["dummy"] is not None else 0):
            mismatches.append(("dummy", expected_dummy, db["dummy"]))
        if mismatches:
            metadata_mismatches.append((name, mismatches))

    findings.append(
        (
            "entity metadata mismatches between JSONL and entity_occurrences",
            metadata_mismatches,
        )
    )
    return findings


def check_mapping_roles_and_roots(conn: sqlite3.Connection) -> list:
    findings = []

    valid_roles = set(ROLE_WEIGHT)
    valid_roles.update(RELATED_FILE_ROLES)
    unknown_mapping_roles = q(
        conn,
        """
        SELECT DISTINCT mapping_type
        FROM entity_mappings
        WHERE mapping_type IS NOT NULL
        ORDER BY mapping_type
        """,
    )
    unknown = [
        r["mapping_type"]
        for r in unknown_mapping_roles
        if r["mapping_type"] not in valid_roles
        and (not str(r["mapping_type"]).startswith("openapispec_") and not str(r["mapping_type"]).startswith("workflow_"))
    ]
    findings.append(("entity_mappings with unknown mapping_type", unknown))

    rows = q(
        conn,
        """
        SELECT
            em.entity_id,
            em.symbol_id,
            em.mapping_type,
            er.role,
            er.weight,
            er.reason
        FROM entity_mappings em
        LEFT JOIN entity_roots er
            ON er.entity_id = em.entity_id
           AND er.symbol_id = em.symbol_id
        WHERE em.symbol_id IS NOT NULL
        """,
    )

    missing_roots = []
    mismatched_roots = []
    for r in rows:
        role = r["mapping_type"]
        if role not in ROLE_WEIGHT:
            continue
        expected_weight = ROLE_WEIGHT[role]
        expected_reason = ROLE_REASON[role]

        if r["role"] is None:
            missing_roots.append((r["entity_id"], r["symbol_id"], role))
            continue

        if (
            r["role"] != role
            or float(r["weight"]) != float(expected_weight)
            or r["reason"] != expected_reason
        ):
            mismatched_roots.append(
                (
                    r["entity_id"],
                    r["symbol_id"],
                    role,
                    r["role"],
                    r["weight"],
                    r["reason"],
                    expected_weight,
                    expected_reason,
                )
            )

    findings.append(
        ("entity_mappings missing corresponding entity_roots rows", missing_roots)
    )
    findings.append(("entity_roots role/weight/reason mismatches", mismatched_roots))
    return findings


def check_structural(conn: sqlite3.Connection) -> list:
    findings = []
    repo_id = resolve_repo_id(conn, DEFAULT_REPO_ROOT)

    required = {"name", "entity_type"}
    missing = missing_columns(conn, "entity_nodes", required)
    findings.append(("entity_nodes missing expected columns", missing))
    if missing:
        return findings

    repo_clause = " AND eo.repo_id = ?" if repo_id is not None else ""
    repo_params = (repo_id,) if repo_id is not None else ()
    missing_ent = q(
        conn,
        """
        SELECT DISTINCT en.name
        FROM entity_occurrences eo
        JOIN entity_nodes en ON en.id = eo.entity_id
        JOIN entity_mappings em
            ON em.repo_id = eo.repo_id
           AND em.entity_id = eo.entity_id
        WHERE (eo.ent_file IS NULL OR eo.ent_file = '')
          AND em.mapping_type IN ({roles})
    """.format(roles=", ".join(["?"] * len(ROLE_WEIGHT))) + repo_clause,
        tuple(ROLE_WEIGHT.keys()) + repo_params,
    )
    findings.append(("entity_occurrences without ent_file", [r["name"] for r in missing_ent]))

    orphan_mappings = q(
        conn,
        """
        SELECT em.entity_id, em.symbol_id
        FROM entity_mappings em
        LEFT JOIN symbols s
            ON s.id = em.symbol_id
        WHERE em.symbol_id IS NOT NULL
          AND s.id IS NULL
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
        WHERE en.entity_type IN ('business_entity', 'domain_object')
          AND NOT EXISTS (
              SELECT 1
              FROM entity_roots
              WHERE entity_id = en.id
                AND weight >= 0.75
          )
    """,
    )
    findings.append(
        (
            "domain entities with 0 seed roots at weight >= 0.75",
            [r["name"] for r in no_seed_at_075],
        )
    )

    non_domain_no_seed = q(
        conn,
        """
        SELECT en.name, en.entity_type
        FROM entity_nodes en
        WHERE en.entity_type NOT IN ('business_entity', 'domain_object')
          AND NOT EXISTS (
              SELECT 1
              FROM entity_roots
              WHERE entity_id = en.id
                AND weight >= 0.75
          )
        ORDER BY en.entity_type, en.name
    """,
    )
    findings.append(
        (
            "non-domain entities with 0 seed roots at weight >= 0.75",
            [(r["name"], r["entity_type"]) for r in non_domain_no_seed],
        )
    )

    unclassified_no_seed = q(
        conn,
        """
        SELECT en.name
        FROM entity_nodes en
        WHERE (en.entity_type IS NULL OR en.entity_type = '')
          AND NOT EXISTS (
              SELECT 1
              FROM entity_roots
              WHERE entity_id = en.id
                AND weight >= 0.75
          )
        ORDER BY en.name
    """,
    )
    findings.append(
        (
            "unclassified entities with 0 seed roots at weight >= 0.75",
            [r["name"] for r in unclassified_no_seed],
        )
    )

    non_domain_present = q(
        conn,
        """
        SELECT en.entity_type, COUNT(*) AS c
        FROM entity_nodes en
        WHERE en.entity_type NOT IN ('business_entity', 'domain_object')
        GROUP BY en.entity_type
        ORDER BY c DESC, en.entity_type
    """,
    )
    findings.append(
        (
            "non-domain entity counts",
            [(r["entity_type"], r["c"]) for r in non_domain_present],
        )
    )

    domain_without_type = q(
        conn,
        """
        SELECT DISTINCT en.name
        FROM entity_occurrences eo
        JOIN entity_nodes en ON en.id = eo.entity_id
        JOIN entity_mappings em
            ON em.repo_id = eo.repo_id
           AND em.entity_id = eo.entity_id
        WHERE en.entity_type IN ('business_entity', 'domain_object')
          AND (eo.ent_file IS NULL OR eo.ent_file = '')
          AND em.mapping_type IN ({roles})
    """.format(roles=", ".join(["?"] * len(ROLE_WEIGHT))),
        tuple(ROLE_WEIGHT.keys()),
    )
    if domain_without_type:
        findings.append(
            (
                "domain entities missing entity_occurrences.ent_file despite classification",
                [r["name"] for r in domain_without_type],
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


def check_filesystem(
    conn: sqlite3.Connection,
    repo_root: str,
) -> list:
    findings = []
    repo_id = resolve_repo_id(conn, repo_root)

    required = {"name"}
    missing = missing_columns(conn, "entity_nodes", required)
    findings.append(("entity_nodes missing expected columns", missing))
    if missing:
        return findings

    missing_ent_files = []
    sql = """
        SELECT en.name, eo.ent_file
        FROM entity_occurrences eo
        JOIN entity_nodes en ON en.id = eo.entity_id
        WHERE eo.ent_file IS NOT NULL
          AND eo.ent_file <> ''
    """
    params: tuple = ()
    if repo_id is not None:
        sql += " AND eo.repo_id = ?"
        params = (repo_id,)
    for r in q(conn, sql, params):
        if not r["ent_file"]:
            continue
        full = os.path.join(repo_root, r["ent_file"])
        if not os.path.exists(full):
            missing_ent_files.append((r["name"], r["ent_file"]))

    findings.append(("entity_occurrences with .ent files missing on disk", missing_ent_files))

    missing_class_files = []
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
          AND em.mapping_type IN ({roles})
    """.format(roles=", ".join(["?"] * len(COMPANION_ROLES))),
        tuple(COMPANION_ROLES),
    )

    for r in rows:
        candidate = os.path.join(repo_root, r["source_text"])
        if not os.path.exists(candidate):
            missing_class_files.append(
                (r["entity_name"], r["mapping_type"], r["source_text"])
            )

    findings.append(
        (
            "companion class files referenced in entity_mappings missing on disk",
            missing_class_files,
        )
    )

    return findings


def check_repo_vs_db(
    conn: sqlite3.Connection,
    repo_root: str,
) -> list:
    findings = []
    repo_id = resolve_repo_id(conn, repo_root)

    repo_ents = set()
    for root, _, files in os.walk(os.path.join(repo_root, "app")):
        for name in files:
            if name.endswith(".ent"):
                rel = os.path.relpath(
                    os.path.join(root, name),
                    repo_root,
                )
                repo_ents.add(rel.replace(os.sep, "/"))

    sql = """
        SELECT ent_file
        FROM entity_occurrences
        WHERE ent_file IS NOT NULL
          AND ent_file <> ''
    """
    params: tuple = ()
    if repo_id is not None:
        sql += " AND repo_id = ?"
        params = (repo_id,)
    db_ents = {r["ent_file"] for r in q(conn, sql, params)}

    only_in_repo = sorted(repo_ents - db_ents)
    only_in_db = sorted(db_ents - repo_ents)

    findings.append((".ent files present in repo but missing in DB", only_in_repo))
    findings.append((".ent files present in DB but missing in repo", only_in_db))

    return findings


def check_role_distribution(conn: sqlite3.Connection) -> list:
    findings = []

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


def _build_ground_truth_from_entities(entities_rows: list[dict]) -> dict[str, set[str]]:
    ground_truth: dict[str, set[str]] = {}
    for row in entities_rows:
        entity_name = row.get("entity_name")
        if not entity_name:
            continue

        expected_symbols: set[str] = set()
        companion_classes = row.get("companion_classes") or {}
        for role in COMPANION_ROLES:
            class_file = companion_classes.get(role)
            if not class_file:
                continue
            if role not in ROLE_WEIGHT:
                continue
            if ROLE_WEIGHT[role] < 0.75:
                continue
            expected_symbols.add(Path(class_file).stem.lower())

        if expected_symbols:
            ground_truth[entity_name] = expected_symbols
    return ground_truth


def check_ground_truth(conn: sqlite3.Connection, entities_rows: list[dict]) -> list:
    findings = []
    ground_truth = _build_ground_truth_from_entities(entities_rows)

    missing_entities = []
    entity_missing_expected = []
    entity_extra = []
    perfect = 0
    total_expected = 0
    total_got = 0
    total_correct = 0

    for entity_name, expected_set in sorted(ground_truth.items()):
        row = conn.execute(
            "SELECT id FROM entity_nodes WHERE name = ?",
            (entity_name,),
        ).fetchone()

        if not row:
            missing_entities.append(entity_name)
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
        got = {r["name"].lower() for r in got_rows}
        correct = expected_set & got
        missing = expected_set - got
        extra = got - expected_set

        total_expected += len(expected_set)
        total_got += len(got)
        total_correct += len(correct)

        if not missing and not extra:
            perfect += 1
            continue

        if missing:
            entity_missing_expected.append((entity_name, sorted(list(missing))[:10]))
        if extra:
            entity_extra.append((entity_name, sorted(list(extra))[:10]))

    precision = (total_correct / total_got) if total_got else 0.0
    recall = (total_correct / total_expected) if total_expected else 0.0
    findings.append(
        (
            "ground truth summary (derived from entity_definitions + deterministic role weights)",
            [
                f"entities_with_expected_roots={len(ground_truth)}",
                f"entities_with_perfect_match={perfect}",
                f"total_expected_symbols={total_expected}",
                f"total_actual_symbols={total_got}",
                f"total_correct_symbols={total_correct}",
                f"precision={precision:.4f}",
                f"recall={recall:.4f}",
            ],
        )
    )
    findings.append(("ground-truth entities missing in entity_nodes", missing_entities))
    findings.append(("entities missing expected >=0.75 roots", entity_missing_expected))
    findings.append(("entities with unexpected >=0.75 extra roots", entity_extra))

    return findings


def write_report(all_findings: list, path: str) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)

    with out.open("w", encoding="utf-8") as f:
        f.write("# Phase 2B.1 Validation Report\n\n")

        for section_title, findings in all_findings:
            f.write(f"## {section_title}\n\n")

            for label, items in findings:
                f.write(f"### {label}\n\n")

                if not items:
                    f.write("OK — no issues found.\n\n")
                    continue

                for item in items[:200]:
                    f.write(f"- `{item}`\n")

                if len(items) > 200:
                    f.write(f"\n_(truncated — {len(items)} total)_\n\n")
                else:
                    f.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--repo-root", default=DEFAULT_REPO_ROOT)
    parser.add_argument("--report", default=DEFAULT_REPORT)
    parser.add_argument("--entities", default=DEFAULT_ENTITIES)
    args = parser.parse_args()

    conn = connect(args.db)
    entities_rows = load_entities_jsonl(args.entities)

    all_findings = [
        ("Scan output checks", check_scan_output_roles(entities_rows)),
        ("Schema contract checks", check_schema_contract(conn)),
        ("JSONL vs DB checks", check_entities_jsonl_vs_db(conn, entities_rows)),
        ("Mapping/roots checks", check_mapping_roles_and_roots(conn)),
        ("Structural checks", check_structural(conn)),
        ("Filesystem checks", check_filesystem(conn, args.repo_root)),
        ("Repo vs DB coverage", check_repo_vs_db(conn, args.repo_root)),
        ("Role distribution", check_role_distribution(conn)),
        ("Ground truth checks", check_ground_truth(conn, entities_rows)),
    ]

    write_report(all_findings, args.report)

    print(f"Wrote Phase 2B.1 validation report to: {args.report}")


if __name__ == "__main__":
    main()
