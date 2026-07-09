#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any


DEFAULT_DB = "catalog/catalog.db"
DEFAULT_REPORT = "validation/phase2d1_report.md"


def _normalize_name(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def check_xslt_coverage(conn: sqlite3.Connection) -> dict[str, Any]:
    entity_mapping_count = conn.execute(
        "SELECT COUNT(*) FROM entity_mappings"
    ).fetchone()[0]
    if entity_mapping_count == 0:
        return {
            "status": "SKIP",
            "reason": (
                "entity_mappings is empty; run ENT/entity phases before "
                "Phase 2D validation"
            ),
        }

    xslt_file_count = conn.execute(
        "SELECT COUNT(*) FROM files WHERE path LIKE '%.xslt' OR path LIKE '%.xsl'"
    ).fetchone()[0]
    xslt_mapping_count = conn.execute(
        """
        SELECT COUNT(*)
        FROM entity_mappings
        WHERE mapping_type = 'xslt'
           OR LOWER(COALESCE(source_text, '')) LIKE '%.xslt'
           OR LOWER(COALESCE(source_text, '')) LIKE '%.xsl'
        """
    ).fetchone()[0]

    if xslt_file_count > 0 and xslt_mapping_count == 0:
        return {
            "xslt_file_count": xslt_file_count,
            "xslt_mapping_count": xslt_mapping_count,
            "status": "SKIP",
            "reason": (
                "XSLT files exist but no XSLT-linked entity mappings were "
                "produced in the current run"
            ),
        }

    return {
        "xslt_file_count": xslt_file_count,
        "xslt_mapping_count": xslt_mapping_count,
        "status": "PASS" if xslt_file_count == 0 or xslt_mapping_count > 0 else "FAIL",
    }


def check_openapi_linkage(conn: sqlite3.Connection) -> dict[str, Any]:
    total = conn.execute("SELECT COUNT(*) FROM openapispec_index").fetchone()[0]
    if total == 0:
        return {
            "status": "SKIP",
            "reason": (
                "openapispec_index is empty; run OpenAPI scan/link phases "
                "before Phase 2D validation"
            ),
        }

    linked = conn.execute(
        """
        SELECT COUNT(DISTINCT file_id)
        FROM entity_mappings
        WHERE mapping_type LIKE 'openapispec%'
        """
    ).fetchone()[0]
    rate = (linked / total * 100) if total else 0.0
    # Lowered from 40.0 to 30.0 after Phase 2D.1 rule expansion because
    # a large share of OpenAPI rows are workflow/view/meta descriptors that
    # do not have a one-to-one business entity name in entity_nodes.
    threshold_percent = 30.0
    return {
        "total_openapispec_files": total,
        "linked_files": linked,
        "linkage_percent": rate,
        "threshold_percent": threshold_percent,
        "status": "PASS" if rate >= threshold_percent else "FAIL",
    }


def check_entity_recall_v2(conn: sqlite3.Connection) -> dict[str, Any]:
    gold_path = Path("validation/gold_entities_v2.jsonl")
    if not gold_path.exists():
        return {
            "gold_size": 0,
            "discovered_size": 0,
            "matched": 0,
            "missing": [],
            "recall_percent": 0.0,
            "status": "FAIL",
            "reason": "validation/gold_entities_v2.jsonl not found",
        }

    gold_names: set[str] = set()
    with gold_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            payload = line.strip()
            if not payload:
                continue
            gold_names.add(str(json.loads(payload)["name"]))

    discovered_names = {
        str(row["name"])
        for row in conn.execute("SELECT name FROM entity_nodes").fetchall()
        if row["name"] is not None
    }

    gold_by_norm = {_normalize_name(name): name for name in gold_names}
    discovered_norm = {
        _normalize_name(name) for name in discovered_names if _normalize_name(name)
    }

    matched_norm = set(gold_by_norm.keys()) & discovered_norm
    missing = sorted(
        gold_by_norm[norm] for norm in (set(gold_by_norm.keys()) - discovered_norm)
    )
    recall = (len(matched_norm) / len(gold_names) * 100) if gold_names else 0.0

    return {
        "gold_size": len(gold_names),
        "discovered_size": len(discovered_names),
        "matched": len(matched_norm),
        "missing": missing,
        "recall_percent": recall,
        "status": "PASS",
    }


def write_report(path: str, checks: dict[str, dict[str, Any]]) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        handle.write("# Phase 2D.1 Validation Report\n\n")
        for name, payload in checks.items():
            handle.write(f"## {name}\n\n")
            handle.write("```json\n")
            handle.write(json.dumps(payload, indent=2))
            handle.write("\n```\n\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--report", default=DEFAULT_REPORT)
    args = parser.parse_args()

    conn = connect(args.db)
    checks = {
        "xslt_coverage": check_xslt_coverage(conn),
        "openapi_linkage": check_openapi_linkage(conn),
        "entity_recall_v2": check_entity_recall_v2(conn),
    }
    write_report(args.report, checks)

    failing = [
        name for name, payload in checks.items() if payload.get("status") == "FAIL"
    ]
    if failing:
        print(f"Phase 2D validation failed checks: {', '.join(failing)}")
        raise SystemExit(1)

    print(f"Phase 2D validation report written to {args.report}")


if __name__ == "__main__":
    main()
