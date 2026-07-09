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
    args = parser.parse_args()

    conn = connect(args.db)
    try:
        checks = {
            "conflicting_key_to_id": check_conflicting_key_to_id(conn),
            "conflicting_id_to_key": check_conflicting_id_to_key(conn),
            "unresolved_policy_keys": check_unresolved_policy_keys(conn),
            "unresolved_menu_keys": check_unresolved_menu_keys(conn),
            "orphan_dbschema_fields": check_orphan_dbschema_fields(conn),
        }
    finally:
        conn.close()

    threshold_failures: dict[str, dict] = {}

    def threshold_exceeded(count: int, threshold: int) -> bool:
        return threshold >= 0 and count > threshold

    threshold_map = {
        "conflicting_key_to_id": args.max_conflicting_key_to_id,
        "conflicting_id_to_key": args.max_conflicting_id_to_key,
        "unresolved_policy_keys": args.max_unresolved_policy_keys,
        "unresolved_menu_keys": args.max_unresolved_menu_keys,
        "orphan_dbschema_fields": args.max_orphan_dbschema_fields,
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
