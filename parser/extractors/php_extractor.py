# parser/extractors/php_extractor.py

from tree_sitter_languages import get_parser
from .base import Symbol

# Path B: Delegate .cqry files to dedicated extractor.
# .cqry files have a specific PHP structure (query definition arrays) that
# doesn't parse well with the standard PHP tree-sitter grammar.
# See cqry_extractor.py for .cqry-specific extraction logic.

_parser = get_parser("php")


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _find_name(node, source: bytes) -> str | None:
    for child in node.children:
        if child.type == "name":
            return _node_text(child, source)
    return None


def extract(source: bytes, file_path: str = "") -> list:
    # If this is a .cqry file, delegate to the dedicated extractor
    if file_path.endswith(".cqry"):
        from . import cqry_extractor
        return cqry_extractor.extract(source)
    
    tree = _parser.parse(source)
    root = tree.root_node
    symbols: list[Symbol] = []

    def walk(node, parent_class: str | None):
        if node.type == "class_declaration":
            name = _find_name(node, source)
            if name:
                symbols.append(Symbol(
                    name=name, kind="class", language="php",
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    parent_symbol=parent_class,
                ))
                parent_class = name

        elif node.type == "interface_declaration":
            name = _find_name(node, source)
            if name:
                symbols.append(Symbol(
                    name=name, kind="interface", language="php",
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    parent_symbol=parent_class,
                ))
                parent_class = name

        elif node.type == "method_declaration":
            name = _find_name(node, source)
            if name:
                symbols.append(Symbol(
                    name=name, kind="method", language="php",
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    parent_symbol=parent_class,
                ))

        elif node.type == "function_definition":
            name = _find_name(node, source)
            if name:
                symbols.append(Symbol(
                    name=name, kind="function", language="php",
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    parent_symbol=parent_class,
                ))

        for child in node.children:
            walk(child, parent_class)

    walk(root, None)
    return symbols
