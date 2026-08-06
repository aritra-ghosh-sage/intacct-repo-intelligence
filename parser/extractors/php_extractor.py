# parser/extractors/php_extractor.py

from tree_sitter_languages import get_parser

from .base import Symbol

# Path B: Delegate .cqry/.qry files to dedicated extractor.
# .cqry/.qry files have a specific PHP structure (query definition arrays) that
# doesn't parse well with the standard PHP tree-sitter grammar.
# See cqry_extractor.py for .cqry-specific extraction logic.

_parser = get_parser("php")
_PARSE_FAILURES: list[dict[str, object]] = []


def reset_stats() -> None:
    _PARSE_FAILURES.clear()


def get_parse_failures() -> list[dict[str, object]]:
    return list(_PARSE_FAILURES)


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _find_name(node, source: bytes) -> str | None:
    for child in node.children:
        if child.type == "name":
            return _node_text(child, source)
    return None


def _error_nodes(node) -> tuple:
    errors: list[object] = []

    def visit(current) -> None:
        if current.type in {"ERROR", "MISSING"} or current.is_missing:
            errors.append(current)
        for child in current.children:
            visit(child)

    visit(node)
    return tuple(errors)


def extract(source: bytes, file_path: str = "") -> list:
    # If this is a .cqry or .qry file, delegate to the dedicated extractor.
    if file_path.endswith((".cqry", ".qry")):
        from . import cqry_extractor

        return cqry_extractor.extract(source)

    tree = _parser.parse(source)
    root = tree.root_node
    for error in _error_nodes(root):
        _PARSE_FAILURES.append(
            {
                "source_file": file_path or "unknown.php",
                "reason": "php_parse_error",
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
        if node.type == "class_declaration":
            name = _find_name(node, source)
            if name:
                symbols.append(
                    Symbol(
                        name=name,
                        kind="class",
                        language="php",
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        parent_symbol=parent_class,
                    )
                )
                parent_class = name

        elif node.type == "interface_declaration":
            name = _find_name(node, source)
            if name:
                symbols.append(
                    Symbol(
                        name=name,
                        kind="interface",
                        language="php",
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        parent_symbol=parent_class,
                    )
                )
                parent_class = name

        elif node.type == "method_declaration":
            name = _find_name(node, source)
            if name:
                symbols.append(
                    Symbol(
                        name=name,
                        kind="method",
                        language="php",
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        parent_symbol=parent_class,
                    )
                )

        elif node.type == "function_definition":
            name = _find_name(node, source)
            if name:
                symbols.append(
                    Symbol(
                        name=name,
                        kind="function",
                        language="php",
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        parent_symbol=parent_class,
                    )
                )

        for child in node.children:
            walk(child, parent_class)

    walk(root, None)
    return symbols
