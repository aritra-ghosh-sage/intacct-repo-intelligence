#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def column_exists(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(str(row["name"]) == column_name for row in rows)




def check_security_entity_access_links(conn: sqlite3.Connection) -> dict:
    required = {"entity_access_links", "security_operations", "entity_nodes", "entity_occurrences"}
    existing = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if not required.issubset(existing):
        return {"ok": False, "count": -1, "error": "missing_security_link_tables"}

    operation_count = int(
        conn.execute("SELECT COUNT(*) FROM security_operations").fetchone()[0]
    )
    rows = conn.execute(
        """
        SELECT eal.surface, so.op_key, e.name, eo.module
        FROM entity_access_links eal
        JOIN security_operations so
          ON so.id = eal.record_id
        JOIN entity_nodes e
          ON e.id = eal.entity_id
        JOIN entity_occurrences eo
          ON eo.entity_id = e.id
         AND eo.repo_id = eal.repo_id
        WHERE eal.surface IN ('security_resource', 'security_operation')
        """
    ).fetchall()
    invalid = 0
    for row in rows:
        parts = [part.strip() for part in str(row["op_key"]).strip("/").split("/")]
        if len(parts) < 3:
            invalid += 1
            continue
        expected_surface = (
            "security_resource" if len(parts) == 3 else "security_operation"
        )
        if (
            row["surface"] != expected_surface
            or parts[2].lower() != str(row["name"]).lower()
            or parts[0].lower() != str(row["module"]).lower()
        ):
            invalid += 1
    missing_links = int(operation_count > 0 and not rows)
    return {
        "ok": missing_links == 0 and invalid == 0,
        "count": invalid + missing_links,
        "operation_count": operation_count,
        "linked_rows": len(rows),
    }


def check_allowops_resolution(conn: sqlite3.Connection) -> dict:
    if not column_exists(conn, "security_operation_allowops", "allowed_operation_id"):
        return {"ok": False, "count": -1, "error": "missing_column:allowed_operation_id"}
    row = conn.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM security_operation_allowops a
        LEFT JOIN security_operations target
          ON target.id = a.allowed_operation_id
        WHERE a.allowed_operation_id IS NOT NULL
          AND target.id IS NULL
        """
    ).fetchone()
    return {"ok": row["cnt"] == 0, "count": int(row["cnt"])}


def check_conflicting_key_to_id(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM (
            SELECT op_key
            FROM security_operations
            WHERE op_numeric_id IS NOT NULL
            GROUP BY op_key
            HAVING COUNT(DISTINCT op_numeric_id) > 1
        ) t
        """
    ).fetchone()
    return {"ok": row["cnt"] == 0, "count": int(row["cnt"])}


def check_conflicting_id_to_key(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM (
            SELECT op_numeric_id
            FROM security_operations
            WHERE op_numeric_id IS NOT NULL
            GROUP BY op_numeric_id
            HAVING COUNT(DISTINCT op_key) > 1
        ) t
        """
    ).fetchone()
    return {"ok": row["cnt"] == 0, "count": int(row["cnt"])}


def check_unresolved_policy_keys(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM security_policy_eops e
        LEFT JOIN security_operations o
          ON o.op_key = e.op_key
        WHERE o.id IS NULL
        """
    ).fetchone()
    return {"ok": row["cnt"] == 0, "count": int(row["cnt"])}


def check_unresolved_menu_keys(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM security_menu_op_links
        WHERE operation_id IS NULL
        """
    ).fetchone()
    return {"ok": row["cnt"] == 0, "count": int(row["cnt"])}


def check_orphan_dbschema_fields(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM dbschema_fields f
        LEFT JOIN dbschema_tables t
          ON t.id = f.dbschema_table_id
        WHERE t.id IS NULL
        """
    ).fetchone()
    return {"ok": row["cnt"] == 0, "count": int(row["cnt"])}


def check_unresolved_security_operations_file_ids(conn: sqlite3.Connection) -> dict:
    if not column_exists(conn, "security_operations", "file_id"):
        return {"ok": False, "count": -1, "error": "missing_column:file_id"}
    row = conn.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM security_operations
        WHERE source_file IS NOT NULL
          AND (file_id IS NULL OR file_id = 0)
        """
    ).fetchone()
    return {"ok": row["cnt"] == 0, "count": int(row["cnt"])}


def check_unresolved_security_allowops_file_ids(conn: sqlite3.Connection) -> dict:
    if not column_exists(conn, "security_operation_allowops", "file_id"):
        return {"ok": False, "count": -1, "error": "missing_column:file_id"}
    row = conn.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM security_operation_allowops
        WHERE source_file IS NOT NULL
          AND (file_id IS NULL OR file_id = 0)
        """
    ).fetchone()
    return {"ok": row["cnt"] == 0, "count": int(row["cnt"])}


def check_unresolved_security_policies_file_ids(conn: sqlite3.Connection) -> dict:
    if not column_exists(conn, "security_policies", "file_id"):
        return {"ok": False, "count": -1, "error": "missing_column:file_id"}
    row = conn.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM security_policies
        WHERE source_file IS NOT NULL
          AND (file_id IS NULL OR file_id = 0)
        """
    ).fetchone()
    return {"ok": row["cnt"] == 0, "count": int(row["cnt"])}


def check_unresolved_security_menus_file_ids(conn: sqlite3.Connection) -> dict:
    if not column_exists(conn, "security_menus", "file_id"):
        return {"ok": False, "count": -1, "error": "missing_column:file_id"}
    row = conn.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM security_menus
        WHERE source_file IS NOT NULL
          AND (file_id IS NULL OR file_id = 0)
        """
    ).fetchone()
    return {"ok": row["cnt"] == 0, "count": int(row["cnt"])}




def check_security_derived_links(conn: sqlite3.Connection) -> dict:
    checks = [
        """
        SELECT COUNT(*)
        FROM entity_access_links p
        WHERE p.surface = 'security_policy'
          AND NOT EXISTS (
              SELECT 1
              FROM security_policy_values spv
              JOIN security_policy_eops spe ON spe.policy_value_id = spv.id
              JOIN security_operations so ON so.op_key = spe.op_key
              JOIN entity_access_links op
                ON op.entity_id = p.entity_id
               AND op.surface IN ('security_resource', 'security_operation')
               AND op.record_id = so.id
              WHERE spv.policy_id = p.record_id
          )
        """,
        """
        SELECT COUNT(*)
        FROM entity_access_links mi
        WHERE mi.surface = 'security_menu_item'
          AND NOT EXISTS (
              SELECT 1
              FROM security_menu_op_links mol
              JOIN security_operations so ON so.op_key = mol.op_key
              JOIN entity_access_links op
                ON op.entity_id = mi.entity_id
               AND op.surface IN ('security_resource', 'security_operation')
               AND op.record_id = so.id
              WHERE mol.menu_item_id = mi.record_id
          )
        """,
    ]
    count = sum(int(conn.execute(query).fetchone()[0]) for query in checks)
    return {"ok": count == 0, "count": count}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="catalog/catalog.db")
    parser.add_argument("--output", default="")
    parser.add_argument(
        "--max-conflicting-key-to-id",
        type=int,
        default=-1,
        help="Fail when conflicting key->id groups exceed this threshold (-1 disables).",
    )
    parser.add_argument(
        "--max-conflicting-id-to-key",
        type=int,
        default=-1,
        help="Fail when conflicting id->key groups exceed this threshold (-1 disables).",
    )
    parser.add_argument(
        "--max-unresolved-policy-keys",
        type=int,
        default=-1,
        help="Fail when unresolved policy keys exceed this threshold (-1 disables).",
    )
    parser.add_argument(
        "--max-unresolved-menu-keys",
        type=int,
        default=-1,
        help="Fail when unresolved menu keys exceed this threshold (-1 disables).",
    )
    parser.add_argument(
        "--max-orphan-dbschema-fields",
        type=int,
        default=0,
        help="Fail when orphan dbschema fields exceed this threshold.",
    )
    parser.add_argument(
        "--max-unresolved-security-operations-file-ids",
        type=int,
        default=-1,
        help="Fail when security_operations unresolved file_ids exceed this threshold (-1 disables).",
    )
    parser.add_argument(
        "--max-unresolved-security-allowops-file-ids",
        type=int,
        default=-1,
        help="Fail when security_operation_allowops unresolved file_ids exceed this threshold (-1 disables).",
    )
    parser.add_argument(
        "--max-unresolved-security-policies-file-ids",
        type=int,
        default=-1,
        help="Fail when security_policies unresolved file_ids exceed this threshold (-1 disables).",
    )
    parser.add_argument(
        "--max-unresolved-security-menus-file-ids",
        type=int,
        default=-1,
        help="Fail when security_menus unresolved file_ids exceed this threshold (-1 disables).",
    )
    args = parser.parse_args()

    conn = connect(args.db)
    try:
        checks = {
            "conflicting_key_to_id": check_conflicting_key_to_id(conn),
            "security_entity_access_links": check_security_entity_access_links(conn),
            "allowops_resolution": check_allowops_resolution(conn),
            "security_derived_links": check_security_derived_links(conn),
            "conflicting_id_to_key": check_conflicting_id_to_key(conn),
            "unresolved_policy_keys": check_unresolved_policy_keys(conn),
            "unresolved_menu_keys": check_unresolved_menu_keys(conn),
            "orphan_dbschema_fields": check_orphan_dbschema_fields(conn),
            "unresolved_security_operations_file_ids": check_unresolved_security_operations_file_ids(
                conn
            ),
            "unresolved_security_allowops_file_ids": check_unresolved_security_allowops_file_ids(
                conn
            ),
            "unresolved_security_policies_file_ids": check_unresolved_security_policies_file_ids(
                conn
            ),
            "unresolved_security_menus_file_ids": check_unresolved_security_menus_file_ids(
                conn
            ),
        }
    finally:
        conn.close()

    threshold_failures: dict[str, dict] = {}

    def threshold_exceeded(count: int, threshold: int) -> bool:
        return threshold >= 0 and count > threshold

    threshold_map = {
        "security_entity_access_links": 0,
        "allowops_resolution": -1,
        "security_derived_links": -1,
        "conflicting_key_to_id": args.max_conflicting_key_to_id,
        "conflicting_id_to_key": args.max_conflicting_id_to_key,
        "unresolved_policy_keys": args.max_unresolved_policy_keys,
        "unresolved_menu_keys": args.max_unresolved_menu_keys,
        "orphan_dbschema_fields": args.max_orphan_dbschema_fields,
        "unresolved_security_operations_file_ids": args.max_unresolved_security_operations_file_ids,
        "unresolved_security_allowops_file_ids": args.max_unresolved_security_allowops_file_ids,
        "unresolved_security_policies_file_ids": args.max_unresolved_security_policies_file_ids,
        "unresolved_security_menus_file_ids": args.max_unresolved_security_menus_file_ids,
    }
    for key, threshold in threshold_map.items():
        count = checks[key]["count"]
        exceeded = threshold_exceeded(count, threshold)
        checks[key]["threshold"] = threshold
        checks[key]["threshold_exceeded"] = exceeded
        if exceeded:
            threshold_failures[key] = {"count": count, "threshold": threshold}

    payload = {
        "db": str(Path(args.db)),
        "checks": checks,
        "all_zero": all(check["ok"] for check in checks.values()),
        "threshold_failures": threshold_failures,
        "thresholds_ok": not threshold_failures,
    }

    rendered = json.dumps(payload, indent=2)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)

    if not payload["thresholds_ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
