#!/usr/bin/env python3

"""
Phase 2C.1 validation.

Checks structural integrity of workflows and workflow_steps.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


DEFAULT_DB = "catalog/catalog.db"
DEFAULT_REPORT = "validation/phase2c1_report.md"


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def q(conn, sql, params=()):
    return conn.execute(sql, params).fetchall()


def check_structural(conn):
    findings = []

    orphan_workflows = q(conn, """
        SELECT w.id
        FROM workflows w
        LEFT JOIN entity_nodes en ON en.id = w.entity_id
        WHERE en.id IS NULL
    """)
    findings.append(
        ("workflows with missing entity", [r["id"] for r in orphan_workflows])
    )

    orphan_steps = q(conn, """
        SELECT ws.id
        FROM workflow_steps ws
        LEFT JOIN workflows w ON w.id = ws.workflow_id
        WHERE w.id IS NULL
    """)
    findings.append(
        ("workflow_steps with missing workflow",
         [r["id"] for r in orphan_steps])
    )

    empty_workflows = q(conn, """
        SELECT w.id, w.name
        FROM workflows w
        LEFT JOIN workflow_steps ws ON ws.workflow_id = w.id
        WHERE ws.id IS NULL
    """)
    findings.append(
        ("workflows without any steps",
         [f"{r['id']} {r['name']}" for r in empty_workflows])
    )

    invalid_types = q(conn, """
        SELECT DISTINCT workflow_type
        FROM workflows
        WHERE workflow_type NOT IN (
            'allowed_operations',
            'approval',
            'posting',
            'reverse',
            'batch',
            'item',
            'entry',
            'ui',
            'rest'
        )
    """)
    findings.append(
        ("unknown workflow_type values",
         [r["workflow_type"] for r in invalid_types])
    )

    return findings


def check_distribution(conn):
    findings = []
    rows = q(conn, """
        SELECT workflow_type, COUNT(*) AS cnt
        FROM workflows
        GROUP BY workflow_type
        ORDER BY cnt DESC
    """)
    findings.append(
        ("workflow_type distribution",
         [(r["workflow_type"], r["cnt"]) for r in rows])
    )

    rows = q(conn, """
        SELECT step_kind, COUNT(*) AS cnt
        FROM workflow_steps
        GROUP BY step_kind
        ORDER BY cnt DESC
    """)
    findings.append(
        ("step_kind distribution",
         [(r["step_kind"], r["cnt"]) for r in rows])
    )

    return findings


def write_report(sections, path):
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)

    with out.open("w", encoding="utf-8") as f:
        f.write("# Phase 2C.1 Validation Report\n\n")

        for section_title, findings in sections:
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--report", default=DEFAULT_REPORT)
    args = parser.parse_args()

    conn = connect(args.db)

    sections = [
        ("Structural checks", check_structural(conn)),
        ("Distribution checks", check_distribution(conn)),
    ]

    write_report(sections, args.report)
    print(f"Phase 2C.1 validation report written to {args.report}")


if __name__ == "__main__":
    main()