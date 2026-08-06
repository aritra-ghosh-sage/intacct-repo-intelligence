# parser/extractors/java_extractor.py

from tree_sitter_languages import get_parser

from .base import Symbol

_parser = get_parser("java")
_PARSE_FAILURES: list[dict[str, object]] = []

# Node types we care about
_CLASS_LIKE = {"class_declaration", "interface_declaration", "enum_declaration"}
_METHOD_LIKE = {"method_declaration", "constructor_declaration"}


def reset_stats() -> None:
    _PARSE_FAILURES.clear()


def get_parse_failures() -> list[dict[str, object]]:
    return list(_PARSE_FAILURES)


def _error_nodes(node) -> tuple:
    errors: list[object] = []

    def visit(current) -> None:
        if current.type in {"ERROR", "MISSING"} or current.is_missing:
            errors.append(current)
        for child in current.children:
            visit(child)

    visit(node)
    return tuple(errors)


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _find_name(node, source: bytes) -> str | None:
    for child in node.children:
        if child.type == "identifier":
            return _node_text(child, source)
    return None


def extract(source: bytes, file_path: str = "") -> list:
    tree = _parser.parse(source)
    root = tree.root_node
    for error in _error_nodes(root):
        _PARSE_FAILURES.append(
            {
                "source_file": file_path or "unknown.java",
                "reason": "java_parse_error",
                "node_type": error.type,
                "is_missing": bool(error.is_missing),
                "start_line": error.start_point[0] + 1,
                "end_line": error.end_point[0] + 1,
                "start_byte": error.start_byte,
                "end_byte": error.end_byte,
            }
        )

    symbols: list[Symbol] = []

    def walk(node, parent_class: str | None):
        if node.type in _CLASS_LIKE:
            name = _find_name(node, source)
            if name:
                kind = "class"
                if node.type == "interface_declaration":
                    kind = "interface"
                elif node.type == "enum_declaration":
                    kind = "enum"
                symbols.append(
                    Symbol(
                        name=name,
                        kind=kind,
                        language="java",
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        parent_symbol=parent_class,
                    )
                )
                parent_class = name

        elif node.type in _METHOD_LIKE:
            name = _find_name(node, source)
            if name:
                symbols.append(
                    Symbol(
                        name=name,
                        kind="method"
                        if node.type == "method_declaration"
                        else "constructor",
                        language="java",
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        parent_symbol=parent_class,
                    )
                )

        for child in node.children:
            walk(child, parent_class)

    walk(root, None)
    return symbols
