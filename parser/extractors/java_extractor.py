# parser/extractors/java_extractor.py

from tree_sitter_languages import get_parser
from .base import Symbol

_parser = get_parser("java")

# Node types we care about
_CLASS_LIKE = {"class_declaration", "interface_declaration", "enum_declaration"}
_METHOD_LIKE = {"method_declaration", "constructor_declaration"}


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _find_name(node, source: bytes) -> str | None:
    for child in node.children:
        if child.type == "identifier":
            return _node_text(child, source)
    return None


def extract(source: bytes) -> list:
    tree= _parser.parse(source)
    root = tree.root_node

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
                symbols.append(Symbol(
                    name=name,
                    kind=kind,
                    language="java",
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    parent_symbol=parent_class,
                ))
                parent_class = name

        elif node.type in _METHOD_LIKE:
            name = _find_name(node, source)
            if name:
                symbols.append(Symbol(
                    name=name,
                    kind="method" if node.type == "method_declaration" else "constructor",
                    language="java",
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    parent_symbol=parent_class,
                ))

        for child in node.children:
            walk(child, parent_class)

    walk(root, None)
    return symbols
