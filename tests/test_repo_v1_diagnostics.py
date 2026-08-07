from __future__ import annotations

import json
import sqlite3

from catalog.repo_v1_diagnostics import (
    canonicalize_symbol_diagnostic_message,
    summarize_symbol_diagnostics,
)


def _connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE files (
            id INTEGER PRIMARY KEY,
            repo_id INTEGER NOT NULL,
            path TEXT NOT NULL
        );
        CREATE TABLE symbol_diagnostics (
            repo_id INTEGER NOT NULL,
            file_id INTEGER NOT NULL,
            diagnostic_key TEXT NOT NULL,
            severity TEXT NOT NULL,
            code TEXT NOT NULL,
            message TEXT NOT NULL,
            source_commit_sha TEXT NOT NULL
        );
        """
    )
    return conn


def _insert(
    conn: sqlite3.Connection,
    *,
    repo_id: int,
    file_id: int,
    file_path: str,
    key: str,
    code: str,
    message: str,
    source_sha: str = "a" * 40,
    severity: str = "error",
) -> None:
    conn.execute(
        "INSERT INTO files(id,repo_id,path) VALUES(?,?,?)",
        (file_id, repo_id, file_path),
    )
    conn.execute(
        """INSERT INTO symbol_diagnostics(
               repo_id,file_id,diagnostic_key,severity,code,message,source_commit_sha
           ) VALUES(?,?,?,?,?,?,?)""",
        (repo_id, file_id, key, severity, code, message, source_sha),
    )


def test_json_location_fields_group_and_raw_evidence_is_retained() -> None:
    conn = _connection()
    try:
        first = json.dumps(
            {
                "reason": "php_parse_error",
                "node_type": "ERROR",
                "is_missing": False,
                "source_file": "one.php",
                "start_line": 10,
                "end_line": 10,
                "start_byte": 20,
                "end_byte": 21,
            },
            sort_keys=True,
        )
        second = json.dumps(
            {
                "reason": "php_parse_error",
                "node_type": "ERROR",
                "is_missing": False,
                "source_file": "two.php",
                "start_line": 30,
                "end_line": 30,
                "start_byte": 40,
                "end_byte": 41,
            },
            sort_keys=True,
        )
        _insert(
            conn,
            repo_id=1,
            file_id=1,
            file_path="one.php",
            key="key-1",
            code="php_parse_error",
            message=first,
        )
        _insert(
            conn,
            repo_id=1,
            file_id=2,
            file_path="two.php",
            key="key-2",
            code="php_parse_error",
            message=second,
        )

        summaries = summarize_symbol_diagnostics(conn, repo_id=1)

        assert len(summaries) == 1
        summary = summaries[0]
        assert summary["count"] == 2
        assert summary["affected_file_count"] == 2
        assert summary["representative_file"] == "one.php"
        assert summary["canonical_message"] == json.dumps(
            {"is_missing": False, "node_type": "ERROR", "reason": "php_parse_error"},
            sort_keys=True,
            separators=(",", ":"),
        )
        evidence = summary["representative_evidence"]
        assert isinstance(evidence, dict)
        assert evidence == {
            "repo_id": 1,
            "file_id": 1,
            "diagnostic_key": "key-1",
            "severity": "error",
            "code": "php_parse_error",
            "message": first,
            "source_commit_sha": "a" * 40,
            "file_path": "one.php",
        }
    finally:
        conn.close()


def test_semantic_fields_remain_distinct() -> None:
    conn = _connection()
    try:
        base = {
            "reason": "php_parse_error",
            "source_file": "same.php",
            "start_line": 1,
            "end_line": 1,
            "start_byte": 1,
            "end_byte": 2,
        }
        for file_id, node_type, is_missing in (
            (1, "ERROR", False),
            (2, "MISSING", False),
            (3, "ERROR", True),
        ):
            message = {**base, "node_type": node_type, "is_missing": is_missing}
            _insert(
                conn,
                repo_id=1,
                file_id=file_id,
                file_path=f"file-{file_id}.php",
                key=f"key-{file_id}",
                code="php_parse_error",
                message=json.dumps(message),
            )

        summaries = summarize_symbol_diagnostics(conn, repo_id=1)

        assert [(row["count"], row["affected_file_count"]) for row in summaries] == [
            (1, 1),
            (1, 1),
            (1, 1),
        ]
        assert {row["canonical_message"] for row in summaries} == {
            json.dumps(
                {
                    "is_missing": False,
                    "node_type": "ERROR",
                    "reason": "php_parse_error",
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            json.dumps(
                {
                    "is_missing": False,
                    "node_type": "MISSING",
                    "reason": "php_parse_error",
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            json.dumps(
                {"is_missing": True, "node_type": "ERROR", "reason": "php_parse_error"},
                sort_keys=True,
                separators=(",", ":"),
            ),
        }
    finally:
        conn.close()


def test_non_json_fallback_is_lossless_and_deterministic() -> None:
    message = "parser exception at line 7: unexpected token"

    assert canonicalize_symbol_diagnostic_message(message) == message
    assert canonicalize_symbol_diagnostic_message(
        message
    ) == canonicalize_symbol_diagnostic_message(message)

    conn = _connection()
    try:
        _insert(
            conn,
            repo_id=1,
            file_id=1,
            file_path="a.php",
            key="key-a",
            code="parser_exception",
            message=message,
        )
        _insert(
            conn,
            repo_id=1,
            file_id=2,
            file_path="b.php",
            key="key-b",
            code="parser_exception",
            message=message,
        )
        assert (
            summarize_symbol_diagnostics(conn, repo_id=1)[0]["canonical_message"]
            == message
        )
    finally:
        conn.close()


def test_malformed_json_fallback_is_retained_through_summary() -> None:
    message = '{"reason":"php_parse_error",'
    conn = _connection()
    try:
        _insert(
            conn,
            repo_id=1,
            file_id=1,
            file_path="broken.php",
            key="key-broken",
            code="php_parse_error",
            message=message,
            source_sha="b" * 40,
            severity="warning",
        )

        summary = summarize_symbol_diagnostics(conn, repo_id=1)[0]

        assert summary["canonical_message"] == message
        assert summary["representative_evidence"] == {
            "repo_id": 1,
            "file_id": 1,
            "diagnostic_key": "key-broken",
            "severity": "warning",
            "code": "php_parse_error",
            "message": message,
            "source_commit_sha": "b" * 40,
            "file_path": "broken.php",
        }
    finally:
        conn.close()


def test_nested_and_extra_semantic_fields_are_retained() -> None:
    first = json.dumps(
        {
            "reason": "php_parse_error",
            "node_type": "ERROR",
            "context": {"source_file": "nested-one.php", "kind": "block"},
            "extra": {"token": "?"},
            "source_file": "one.php",
            "start_line": 10,
            "end_line": 10,
            "start_byte": 20,
            "end_byte": 21,
        },
        sort_keys=True,
    )
    second = json.dumps(
        {
            "reason": "php_parse_error",
            "node_type": "ERROR",
            "context": {"source_file": "nested-one.php", "kind": "block"},
            "extra": {"token": "?"},
            "source_file": "two.php",
            "start_line": 30,
            "end_line": 30,
            "start_byte": 40,
            "end_byte": 41,
        },
        sort_keys=True,
    )
    conn = _connection()
    try:
        _insert(
            conn,
            repo_id=1,
            file_id=1,
            file_path="one.php",
            key="key-1",
            code="php_parse_error",
            message=first,
        )
        _insert(
            conn,
            repo_id=1,
            file_id=2,
            file_path="two.php",
            key="key-2",
            code="php_parse_error",
            message=second,
        )

        summaries = summarize_symbol_diagnostics(conn, repo_id=1)

        assert len(summaries) == 1
        assert summaries[0]["canonical_message"] == json.dumps(
            {
                "context": {"source_file": "nested-one.php", "kind": "block"},
                "extra": {"token": "?"},
                "node_type": "ERROR",
                "reason": "php_parse_error",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    finally:
        conn.close()


def test_mismatched_diagnostic_and_file_repositories_fail_closed() -> None:
    conn = _connection()
    try:
        _insert(
            conn,
            repo_id=1,
            file_id=1,
            file_path="foreign.php",
            key="foreign-key",
            code="php_parse_error",
            message="foreign",
        )
        conn.execute("UPDATE files SET repo_id=2 WHERE id=1")

        try:
            summarize_symbol_diagnostics(conn, repo_id=1)
        except ValueError as exc:
            assert str(exc) == (
                "symbol diagnostic/file repository ownership mismatch: "
                "diagnostic repo_id=1, file repo_id=2, file_id=1"
            )
        else:
            raise AssertionError("mismatched diagnostic ownership was summarized")
    finally:
        conn.close()


def test_summary_order_and_repo_filter_are_deterministic() -> None:
    conn = _connection()
    try:
        _insert(
            conn,
            repo_id=2,
            file_id=1,
            file_path="z.php",
            key="key-z",
            code="z_code",
            message="z",
        )
        _insert(
            conn,
            repo_id=1,
            file_id=2,
            file_path="b.php",
            key="key-b",
            code="b_code",
            message="b",
        )
        _insert(
            conn,
            repo_id=1,
            file_id=3,
            file_path="a.php",
            key="key-a",
            code="a_code",
            message="a",
        )

        first = summarize_symbol_diagnostics(conn, repo_id=1)
        second = summarize_symbol_diagnostics(conn, repo_id=1)

        assert first == second
        assert [row["code"] for row in first] == ["a_code", "b_code"]
        assert [
            row["representative_file"] for row in summarize_symbol_diagnostics(conn)
        ] == [
            "a.php",
            "b.php",
            "z.php",
        ]
    finally:
        conn.close()
