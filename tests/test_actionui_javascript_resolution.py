from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from catalog.db import get_connection
from parser import extract_symbols
from parser.actionui.javascript_callables import extract_javascript_callables
from parser.actionui.javascript_resolution import resolve_event_handlers
from parser.actionui.model import (
    EventCallFact,
    JavascriptExtractionResult,
    ScriptDependencyFact,
)
from parser.extract_symbols import write_jsonl
from parser.extractors import javascript_extractor


def _event(name: str) -> EventCallFact:
    return EventCallFact(
        source_file="app/source/gl/glbatch_form.xml",
        event_name="onload",
        callable_name=name,
        start_line=10,
        end_line=10,
        evidence=f"{name}();",
    )


def _dependency(path: str, scope: str = "active") -> ScriptDependencyFact:
    return ScriptDependencyFact(
        source_file="app/source/gl/GLBatchEditor.cls",
        script_path=path,
        dependency_kind="editor_loader",
        activation_state=scope,
        start_line=20,
        end_line=20,
        evidence=path,
    )


def _javascript(path: str, source: bytes) -> JavascriptExtractionResult:
    return extract_javascript_callables(source, path)


def test_extracts_only_supported_top_level_callable_shapes() -> None:
    symbols = javascript_extractor.extract(
        b'''function onLoadFunctionCalls() {}\nconst toggleBillable = () => {};\nconst handlers = { reloadJournals: function() {}, showHideEReportingSection() {} };\nobj.notSupported = function() {};\nfunction nested() { function hidden() {} }\n''',
        "app/resources/js/glbatch.js",
    )

    assert [(symbol.name, symbol.kind, symbol.parent_symbol) for symbol in symbols] == [
        ("onLoadFunctionCalls", "function", None),
        ("toggleBillable", "function", None),
        ("reloadJournals", "object_method", "handlers"),
        ("showHideEReportingSection", "object_method", "handlers"),
        ("nested", "function", None),
    ]


def test_resolver_uses_only_linked_scripts_not_global_same_name_symbols() -> None:
    linked = _javascript(
        "app/resources/js/glbatch.js", b"function toggleBillable() {}\n"
    )
    unrelated = _javascript(
        "app/resources/js/unrelated.js", b"function toggleBillable() {}\n"
    )

    outcome = resolve_event_handlers(
        [_event("toggleBillable")], [_dependency(linked.source_file)], [linked, unrelated]
    )[0]

    assert outcome.resolution_status == "resolved"
    assert outcome.handler_symbol is not None
    assert outcome.handler_symbol.source_file == linked.source_file


def test_multiple_active_and_mixed_candidates_are_ambiguous() -> None:
    first = _javascript("app/resources/js/first.js", b"function handler() {}\n")
    second = _javascript("app/resources/js/second.js", b"function handler() {}\n")

    outcomes = resolve_event_handlers(
        [_event("handler")],
        [_dependency(first.source_file), _dependency(second.source_file)],
        [first, second],
    )
    assert outcomes[0].resolution_status == "ambiguous"
    assert outcomes[0].resolution_reason == "multiple_active_exact_callables"

    mixed = resolve_event_handlers(
        [_event("handler")],
        [_dependency(first.source_file), _dependency(second.source_file, "conditional")],
        [first, second],
    )
    assert mixed[0].resolution_status == "ambiguous"
    assert mixed[0].resolution_reason == "mixed_active_and_conditional"


def test_conditional_only_and_member_expression_do_not_resolve() -> None:
    result = _javascript("app/resources/js/conditional.js", b"function handler() {}\n")

    conditional = resolve_event_handlers(
        [_event("handler")], [_dependency(result.source_file, "conditional")], [result]
    )[0]
    member = resolve_event_handlers(
        [_event("window.handler")], [_dependency(result.source_file)], [result]
    )[0]

    assert conditional.resolution_status == "conditional"
    assert member.resolution_status == "unsupported"


def test_parse_error_declaration_is_logged_and_cannot_resolve() -> None:
    javascript_extractor.reset_stats()
    path = "app/resources/js/broken.js"
    result = extract_javascript_callables(
        b"function handler( { return 1; }", path
    )

    outcome = resolve_event_handlers([_event("handler")], [_dependency(path)], [result])[0]
    failures = javascript_extractor.get_parse_failures()

    assert result.symbols == ()
    assert outcome.resolution_status == "unresolved"
    assert all(failure["source_file"] == path for failure in failures)
    assert all(failure["reason"] == "javascript_parse_error" for failure in failures)
    assert all(int(failure["start_line"]) == 1 for failure in failures)


def test_parse_error_elsewhere_in_linked_script_blocks_valid_handler() -> None:
    path = "app/resources/js/partially-broken.js"
    result = _javascript(path, b"function handler() {}\nif (\n")

    outcome = resolve_event_handlers([_event("handler")], [_dependency(path)], [result])[0]

    assert result.symbols
    assert result.diagnostics
    assert outcome.resolution_status == "unresolved"
    assert outcome.resolution_reason == "linked_script_parse_error"
    assert outcome.handler_symbol is None


def test_javascript_parse_failure_jsonl_contract(tmp_path) -> None:
    javascript_extractor.reset_stats()
    javascript_extractor.extract(b"function broken( {", "app/resources/js/broken.js")
    output = tmp_path / "javascript_parse_failures.jsonl"

    write_jsonl(output, javascript_extractor.get_parse_failures())

    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert rows
    assert set(rows[0]) == {
        "source_file",
        "reason",
        "node_type",
        "start_line",
        "end_line",
        "start_byte",
        "end_byte",
    }
    assert rows[0]["source_file"] == "app/resources/js/broken.js"


def test_symbol_extraction_persists_javascript_and_writes_failure_log(
    tmp_path, monkeypatch
) -> None:
    repo_root = tmp_path / "repo"
    script_dir = repo_root / "app/resources/js"
    script_dir.mkdir(parents=True)
    (script_dir / "valid.js").write_text("function handler() {}\n", encoding="utf-8")
    (script_dir / "broken.js").write_text("function broken( {", encoding="utf-8")

    db_path = tmp_path / "catalog.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(Path("catalog/schema.sql").read_text(encoding="utf-8"))
    repo_id = conn.execute(
        "INSERT INTO repos(repo_key,local_root,tracked_branch) VALUES ('fixture',?,'main')",
        (str(repo_root),),
    ).lastrowid
    conn.executemany(
        "INSERT INTO files(repo_id,path,language) VALUES (?,?,'javascript')",
        [
            (repo_id, "app/resources/js/valid.js"),
            (repo_id, "app/resources/js/broken.js"),
        ],
    )
    conn.commit()
    conn.close()

    output = tmp_path / "outputs/javascript_parse_failures.jsonl"
    monkeypatch.setattr(extract_symbols, "JAVASCRIPT_PARSE_FAILURES_LOG", output)
    extract_symbols.extract_all(
        only_changed=False,
        languages=["javascript"],
        repo_key="fixture",
        db_path=str(db_path),
    )

    verify = get_connection(str(db_path))
    try:
        symbols = verify.execute(
            "SELECT name,kind,language FROM symbols ORDER BY name"
        ).fetchall()
    finally:
        verify.close()
    assert [tuple(row) for row in symbols] == [("handler", "function", "javascript")]
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert rows and rows[0]["source_file"] == "app/resources/js/broken.js"
