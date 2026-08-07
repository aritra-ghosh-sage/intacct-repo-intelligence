"""Read-only, import-only summaries for immutable repo-v1 symbol diagnostics.

This module intentionally provides an API for inspecting existing SQLite rows;
it has no refresh, promotion, or command-line behavior and never writes to the
connection it receives.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

_LOCATION_FIELDS = frozenset(
    {"source_file", "start_line", "end_line", "start_byte", "end_byte"}
)


@dataclass(frozen=True)
class _DiagnosticRow:
    repo_id: int
    file_id: int
    diagnostic_key: str
    severity: str
    code: str
    message: str
    source_commit_sha: str
    file_path: str


def canonicalize_symbol_diagnostic_message(message: str) -> str:
    """Return a stable grouping value without changing the stored message.

    Parser messages are normally JSON objects.  Only location-specific fields
    are removed from those objects; all semantic fields are retained.  A
    non-JSON message is returned verbatim as a lossless, deterministic
    fallback.
    """

    try:
        parsed: Any = json.loads(message)
    except (TypeError, json.JSONDecodeError):
        return message
    if not isinstance(parsed, dict):
        return message
    canonical = {
        key: value for key, value in parsed.items() if key not in _LOCATION_FIELDS
    }
    return json.dumps(
        canonical, sort_keys=True, ensure_ascii=True, separators=(",", ":")
    )


def _diagnostic_rows(
    conn: sqlite3.Connection, repo_id: int | None
) -> list[_DiagnosticRow]:
    where = " WHERE d.repo_id=?" if repo_id is not None else ""
    parameters: tuple[int, ...] = (repo_id,) if repo_id is not None else ()
    rows = conn.execute(
        """SELECT d.repo_id,f.repo_id,d.file_id,d.diagnostic_key,d.severity,d.code,
                  d.message,d.source_commit_sha,f.path
           FROM symbol_diagnostics d
           JOIN files f ON f.id=d.file_id"""
        + where,
        parameters,
    ).fetchall()
    diagnostic_rows: list[_DiagnosticRow] = []
    for row in rows:
        if int(row[0]) != int(row[1]):
            raise ValueError(
                "symbol diagnostic/file repository ownership mismatch: "
                f"diagnostic repo_id={row[0]}, file repo_id={row[1]}, "
                f"file_id={row[2]}"
            )
        diagnostic_rows.append(
            _DiagnosticRow(
                repo_id=int(row[0]),
                file_id=int(row[2]),
                diagnostic_key=str(row[3]),
                severity=str(row[4]),
                code=str(row[5]),
                message=str(row[6]),
                source_commit_sha=str(row[7]),
                file_path=str(row[8]),
            )
        )
    return diagnostic_rows


def summarize_symbol_diagnostics(
    conn: sqlite3.Connection, *, repo_id: int | None = None
) -> list[dict[str, object]]:
    """Group existing diagnostic rows without writing to ``conn``.

    Results are ordered by code and canonical message.  Each result retains a
    complete representative row as ``representative_evidence`` so callers can
    inspect the original message, key, location-bearing file, severity, and
    source provenance.
    """

    groups: dict[tuple[str, str], list[_DiagnosticRow]] = defaultdict(list)
    for row in _diagnostic_rows(conn, repo_id):
        canonical_message = canonicalize_symbol_diagnostic_message(row.message)
        groups[(row.code, canonical_message)].append(row)

    summaries: list[dict[str, object]] = []
    for (code, canonical_message), rows in sorted(groups.items()):
        representative = min(rows, key=lambda row: (row.file_path, row.diagnostic_key))
        summaries.append(
            {
                "code": code,
                "canonical_message": canonical_message,
                "count": len(rows),
                "affected_file_count": len({row.file_id for row in rows}),
                "representative_file": representative.file_path,
                "representative_evidence": {
                    "repo_id": representative.repo_id,
                    "file_id": representative.file_id,
                    "diagnostic_key": representative.diagnostic_key,
                    "severity": representative.severity,
                    "code": representative.code,
                    "message": representative.message,
                    "source_commit_sha": representative.source_commit_sha,
                    "file_path": representative.file_path,
                },
            }
        )
    return summaries
