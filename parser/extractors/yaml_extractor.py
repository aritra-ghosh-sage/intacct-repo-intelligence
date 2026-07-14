from __future__ import annotations

from pathlib import Path

import yaml

from .base import Symbol

RECOGNIZED_HTTP_METHODS = {"get", "post", "patch", "delete", "put"}

_STATS = {
    "files_seen": 0,
    "parse_failures": 0,
    "symbols_emitted": 0,
}
_PARSE_FAILURES: list[dict[str, str]] = []


def reset_stats() -> None:
    _STATS["files_seen"] = 0
    _STATS["parse_failures"] = 0
    _STATS["symbols_emitted"] = 0
    _PARSE_FAILURES.clear()


def get_stats() -> dict[str, int]:
    return {
        "files_seen": int(_STATS["files_seen"]),
        "parse_failures": int(_STATS["parse_failures"]),
        "symbols_emitted": int(_STATS["symbols_emitted"]),
    }


def get_parse_failures() -> list[dict[str, str]]:
    return list(_PARSE_FAILURES)


def _line_for_fragment(text: str, fragment: str) -> int:
    idx = text.find(fragment)
    if idx < 0:
        return 1
    return text.count("\n", 0, idx) + 1


def _infer_role(file_path: str) -> str:
    lowered = file_path.lower()
    if lowered.endswith(".uimeta.yaml"):
        return "uimeta"
    if lowered.endswith(".view.yaml") or lowered.endswith(".viewmeta.yaml"):
        return "view"
    if lowered.endswith(".schema.history.yaml"):
        return "history"
    if lowered.endswith(".schema.yaml"):
        return "schema"
    if lowered.endswith(".api.yaml"):
        return "api"
    return "generic"


def _role_kind(role: str) -> str:
    mapping = {
        "api": "yaml_api",
        "schema": "yaml_schema",
        "view": "yaml_view",
        "uimeta": "yaml_uimeta",
        "history": "yaml_history",
        "generic": "yaml_document",
    }
    return mapping.get(role, "yaml_document")


def _add_unique(
    symbols: list[Symbol], seen: set[tuple[str, str, str | None]], symbol: Symbol
) -> None:
    key = (symbol.name, symbol.kind, symbol.parent_symbol)
    if key in seen:
        return
    seen.add(key)
    symbols.append(symbol)


def extract(source: bytes, file_path: str = "") -> list[Symbol]:
    _STATS["files_seen"] += 1
    text = source.decode("utf-8", errors="replace")

    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        _STATS["parse_failures"] += 1
        _PARSE_FAILURES.append(
            {
                "source_file": file_path or "unknown.yaml",
                "reason": "invalid_yaml_syntax",
                "detail": str(exc),
            }
        )
        return []

    file_name = Path(file_path).name if file_path else "unknown.yaml"
    role = _infer_role(file_path)
    document_name = file_path or file_name

    symbols: list[Symbol] = []
    seen: set[tuple[str, str, str | None]] = set()

    # Every parseable YAML file contributes one deterministic document symbol.
    _add_unique(
        symbols,
        seen,
        Symbol(
            name=document_name,
            kind="yaml_document",
            language="yaml",
            start_line=1,
            end_line=max(1, text.count("\n") + 1),
        ),
    )

    role_line = _line_for_fragment(text, file_name)
    _add_unique(
        symbols,
        seen,
        Symbol(
            name=document_name,
            kind=_role_kind(role),
            language="yaml",
            start_line=role_line,
            end_line=role_line,
            parent_symbol=document_name,
        ),
    )

    if isinstance(doc, dict):
        for top_key in doc.keys():
            key_name = str(top_key)
            key_line = _line_for_fragment(text, f"{key_name}:")
            _add_unique(
                symbols,
                seen,
                Symbol(
                    name=key_name,
                    kind="yaml_keyspace",
                    language="yaml",
                    start_line=key_line,
                    end_line=key_line,
                    parent_symbol=document_name,
                ),
            )

        paths = doc.get("paths")
        if isinstance(paths, dict):
            for endpoint, methods in paths.items():
                if not isinstance(methods, dict):
                    continue
                for method in methods.keys():
                    method_lower = str(method).lower()
                    if method_lower not in RECOGNIZED_HTTP_METHODS:
                        continue
                    op_name = f"{method_lower.upper()} {endpoint}"
                    op_line = _line_for_fragment(text, f"{method_lower}:")
                    _add_unique(
                        symbols,
                        seen,
                        Symbol(
                            name=op_name,
                            kind="yaml_operation",
                            language="yaml",
                            start_line=op_line,
                            end_line=op_line,
                            parent_symbol=document_name,
                        ),
                    )

        components = doc.get("components")
        if isinstance(components, dict):
            schemas = components.get("schemas")
            if isinstance(schemas, dict):
                for schema_name in schemas.keys():
                    s_name = str(schema_name)
                    s_line = _line_for_fragment(text, f"{s_name}:")
                    _add_unique(
                        symbols,
                        seen,
                        Symbol(
                            name=s_name,
                            kind="yaml_schema",
                            language="yaml",
                            start_line=s_line,
                            end_line=s_line,
                            parent_symbol=document_name,
                        ),
                    )

            actions = components.get("actions")
            if isinstance(actions, dict):
                for action_name in actions.keys():
                    a_name = str(action_name)
                    a_line = _line_for_fragment(text, f"{a_name}:")
                    _add_unique(
                        symbols,
                        seen,
                        Symbol(
                            name=a_name,
                            kind="yaml_action",
                            language="yaml",
                            start_line=a_line,
                            end_line=a_line,
                            parent_symbol=document_name,
                        ),
                    )

        operations = doc.get("operations")
        if isinstance(operations, dict):
            for op_name in operations.keys():
                action_name = str(op_name)
                a_line = _line_for_fragment(text, f"{action_name}:")
                _add_unique(
                    symbols,
                    seen,
                    Symbol(
                        name=action_name,
                        kind="yaml_action",
                        language="yaml",
                        start_line=a_line,
                        end_line=a_line,
                        parent_symbol=document_name,
                    ),
                )

        actions = doc.get("actions")
        if isinstance(actions, list):
            for action in actions:
                if not isinstance(action, dict):
                    continue
                name = action.get("name")
                if not name:
                    continue
                action_name = str(name)
                a_line = _line_for_fragment(text, f"name: {action_name}")
                _add_unique(
                    symbols,
                    seen,
                    Symbol(
                        name=action_name,
                        kind="yaml_action",
                        language="yaml",
                        start_line=a_line,
                        end_line=a_line,
                        parent_symbol=document_name,
                    ),
                )

    _STATS["symbols_emitted"] += len(symbols)
    return symbols
