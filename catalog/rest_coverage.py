"""Shared read-only queries for Gherkin REST coverage evidence."""

from __future__ import annotations

import sqlite3
from typing import Any

REQUIRED_TABLES = (
    "rest_endpoints",
    "entity_nodes",
    "repos",
    "test_cases",
    "test_requests",
    "test_endpoint_links",
    "test_entity_links",
    "test_diagnostics",
    "api_version_compatibility",
)


def coverage_rows(
    conn: sqlite3.Connection, entity_id: int, version: str | None, limit: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return endpoint coverage and directly attributable diagnostics.

    Coverage credit requires both an endpoint link and an entity link. A feature
    name, folder, or unresolved object token cannot make an endpoint look tested.
    """
    version_predicate = (
        "AND (re.source_version = ? OR re.source_version IS NULL)" if version else ""
    )
    params: tuple[object, ...] = (
        entity_id,
        *((version,) if version else ()),
        limit,
    )
    endpoints = conn.execute(
        f"""
        SELECT re.id, re.method, re.path, re.source_version
        FROM rest_endpoints re
        WHERE re.entity_id = ? {version_predicate}
        ORDER BY re.source_version, re.path, re.method, re.id
        LIMIT ?
        """,
        params,
    ).fetchall()

    results: list[dict[str, Any]] = []
    for endpoint in endpoints:
        cases = conn.execute(
            """
            SELECT DISTINCT
                tc.id AS test_case_id,
                r.repo_key AS suite_id,
                tc.case_name,
                tc.scenario_name,
                tc.example_row,
                tc.eligibility,
                tc.jira_refs_json,
                f.path AS feature_path,
                tr.step_line,
                tr.request_version,
                tr.expected_status,
                tr.operation_kind,
                tel.resolution_kind,
                avc.test_version,
                avc.endpoint_version,
                avc.status AS compatibility_status
            FROM test_endpoint_links tel
            JOIN test_requests tr ON tr.id = tel.test_request_id
            JOIN test_cases tc ON tc.id = tr.test_case_id
            JOIN repos r ON r.id = tc.repo_id
            LEFT JOIN files f ON f.id = tc.file_id
            LEFT JOIN api_version_compatibility avc ON avc.id = tel.compatibility_id
            WHERE tel.rest_endpoint_id = ?
              AND EXISTS (
                  SELECT 1 FROM test_entity_links te
                  WHERE te.test_request_id = tr.id AND te.entity_id = ?
              )
            ORDER BY tc.eligibility, r.repo_key, tc.case_name, tr.step_line, tc.id
            """,
            (endpoint["id"], entity_id),
        ).fetchall()
        case_data = [dict(row) for row in cases]
        active_count = sum(row["eligibility"] == "active" for row in cases)
        conditional_count = sum(row["eligibility"] == "ci_only" for row in cases)
        known_issue_count = sum(row["eligibility"] == "known_issue" for row in cases)
        results.append(
            {
                "endpoint_id": endpoint["id"],
                "method": endpoint["method"],
                "path": endpoint["path"],
                "source_version": endpoint["source_version"],
                "coverage": (
                    "active"
                    if active_count
                    else "ci_conditional"
                    if conditional_count
                    else "known_issue_only"
                    if known_issue_count
                    else "uncovered"
                ),
                "active_case_count": active_count,
                "ci_conditional_case_count": conditional_count,
                "known_issue_case_count": known_issue_count,
                "cases": case_data,
            }
        )

    diagnostics = conn.execute(
        """
        SELECT DISTINCT td.kind, td.message, td.source_line AS line,
               r.repo_key AS suite_id, f.path AS feature_path,
               tc.case_name, tc.id AS test_case_id
        FROM test_diagnostics td
        JOIN test_cases tc ON tc.id = td.test_case_id
        JOIN repos r ON r.id = tc.repo_id
        LEFT JOIN files f ON f.id = td.file_id
        WHERE EXISTS (
            SELECT 1 FROM test_requests tr
            JOIN test_entity_links te ON te.test_request_id = tr.id
            WHERE tr.test_case_id = tc.id AND te.entity_id = ?
        )
        ORDER BY td.kind, r.repo_key, f.path, td.source_line, td.id
        """,
        (entity_id,),
    ).fetchall()
    return results, [dict(row) for row in diagnostics]


def coverage_summary(
    endpoints: list[dict[str, Any]], diagnostics: list[dict[str, Any]]
) -> dict[str, int]:
    return {
        "endpoint_count": len(endpoints),
        "active_covered_endpoint_count": sum(
            item["coverage"] == "active" for item in endpoints
        ),
        "uncovered_endpoint_count": sum(
            item["coverage"] == "uncovered" for item in endpoints
        ),
        "ci_conditional_only_endpoint_count": sum(
            item["coverage"] == "ci_conditional" for item in endpoints
        ),
        "known_issue_only_endpoint_count": sum(
            item["coverage"] == "known_issue_only" for item in endpoints
        ),
        "diagnostic_count": len(diagnostics),
    }
