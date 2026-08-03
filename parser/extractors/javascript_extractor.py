"""Conservative tree-sitter JavaScript callable extraction.

Only callable declarations whose AST node is outside a parser ERROR/MISSING
range are emitted.  This makes partially malformed files useful without
allowing malformed declarations to become handler-resolution evidence.
"""

from __future__ import annotations

from tree_sitter_languages import get_parser

from .base import Symbol

_PARSER = get_parser("javascript")
_CALLABLE_VALUES = {"function", "arrow_function"}
_STATS = {"files_seen": 0, "parse_failures": 0, "symbols_emitted": 0}
_PARSE_FAILURES: list[dict[str, object]] = []


def reset_stats() -> None:
    for key in _STATS:
        _STATS[key] = 0
    _PARSE_FAILURES.clear()


def get_stats() -> dict[str, int]:
    return {key: int(value) for key, value in _STATS.items()}


def get_parse_failures() -> list[dict[str, object]]:
    return list(_PARSE_FAILURES)


def _text(node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _named_children(node):
    return tuple(child for child in node.children if child.is_named)


def _error_nodes(node) -> tuple:
    errors: list[object] = []

    def visit(current) -> None:
        if current.type in {"ERROR", "MISSING"} or current.is_missing:
            errors.append(current)
        for child in current.children:
            visit(child)

    visit(node)
    return tuple(errors)


def _intersects_error(node, errors: tuple) -> bool:
    for error in errors:
        # MISSING nodes can have an empty range at a declaration boundary.
        if error.start_byte <= node.end_byte and error.end_byte >= node.start_byte:
            return True
    return False


def _identifier_text(node, source: bytes) -> str | None:
    if node is None or node.type not in {"identifier", "property_identifier"}:
        return None
    return _text(node, source)


def _property_name(node, source: bytes) -> str | None:
    if node.type in {"property_identifier", "identifier"}:
        return _text(node, source)
    if node.type == "string":
        value = _text(node, source)
        if len(value) >= 2 and value[0] in {"'", '"'} and value[-1] == value[0]:
            return value[1:-1]
    return None


def _emit(
    symbols: list[Symbol],
    *,
    name: str,
    kind: str,
    parent_symbol: str | None,
    node,
) -> None:
    symbols.append(
        Symbol(
            name=name,
            kind=kind,
            language="javascript",
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            parent_symbol=parent_symbol,
        )
    )


def _top_level_callable_binding(statement, source: bytes, errors: tuple, symbols: list[Symbol]) -> None:
    """Extract ``const handler = function/arrow`` at program scope only."""

    for declarator in (child for child in statement.children if child.type == "variable_declarator"):
        children = _named_children(declarator)
        if len(children) != 2 or children[1].type not in _CALLABLE_VALUES:
            continue
        name = _identifier_text(children[0], source)
        if name is not None and not _intersects_error(declarator, errors):
            _emit(symbols, name=name, kind="function", parent_symbol=None, node=declarator)


def _top_level_object_methods(statement, source: bytes, errors: tuple, symbols: list[Symbol]) -> None:
    """Extract callable properties of a top-level object assigned to one name."""

    for declarator in (child for child in statement.children if child.type == "variable_declarator"):
        children = _named_children(declarator)
        if len(children) != 2 or children[1].type != "object":
            continue
        object_name = _identifier_text(children[0], source)
        if object_name is None:
            continue
        for member in _named_children(children[1]):
            name: str | None = None
            callable_node = None
            if member.type == "pair":
                parts = _named_children(member)
                if len(parts) == 2 and parts[1].type in _CALLABLE_VALUES:
                    name = _property_name(parts[0], source)
                    callable_node = parts[1]
            elif member.type == "method_definition":
                parts = _named_children(member)
                if parts:
                    name = _property_name(parts[0], source)
                    callable_node = member
            if (
                name is not None
                and callable_node is not None
                and not _intersects_error(member, errors)
            ):
                _emit(
                    symbols,
                    name=name,
                    kind="object_method",
                    parent_symbol=object_name,
                    node=callable_node,
                )


def extract(source: bytes, file_path: str = "") -> list[Symbol]:
    """Return exact, parseable JavaScript callables suitable for catalog symbols."""

    _STATS["files_seen"] += 1
    root = _PARSER.parse(source).root_node
    errors = _error_nodes(root)
    if errors:
        _STATS["parse_failures"] += len(errors)
        for error in errors:
            _PARSE_FAILURES.append(
                {
                    "source_file": file_path or "unknown.js",
                    "reason": "javascript_parse_error",
                    "node_type": error.type,
                    "start_line": error.start_point[0] + 1,
                    "end_line": error.end_point[0] + 1,
                    "start_byte": error.start_byte,
                    "end_byte": error.end_byte,
                }
            )

    symbols: list[Symbol] = []
    for statement in _named_children(root):
        if statement.type == "function_declaration":
            name = next(
                (_identifier_text(child, source) for child in statement.children if child.type == "identifier"),
                None,
            )
            if name is not None and not _intersects_error(statement, errors):
                _emit(symbols, name=name, kind="function", parent_symbol=None, node=statement)
        elif statement.type in {"lexical_declaration", "variable_declaration"}:
            _top_level_callable_binding(statement, source, errors, symbols)
            _top_level_object_methods(statement, source, errors, symbols)

    _STATS["symbols_emitted"] += len(symbols)
    return symbols

