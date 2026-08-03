"""Bounded tree-sitter extraction of actionUI loader methods.

The extractor is deliberately conservative.  It recognizes literal values,
literal arrays, local assignments of those values, and direct calls.  Anything
else is retained as a diagnostic instead of being converted to a catalog fact.
"""

from __future__ import annotations

from dataclasses import dataclass

from tree_sitter_languages import get_parser

from .model import Diagnostic, LoaderFact

_PARSER = get_parser("php")

_FORM_METHODS = {"getMetadataKeyName", "getMetadataFileName"}
_SCRIPT_METHODS = {"getJavaScriptFileNames", "getJavascriptFileNames"}


@dataclass(frozen=True)
class PhpLoaderExtractionResult:
    loaders: tuple[LoaderFact, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()


def _text(node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _line(node) -> tuple[int, int]:
    return node.start_point[0] + 1, node.end_point[0] + 1


def _named_children(node):
    return tuple(child for child in node.children if child.is_named)


def _method_name(method, source: bytes) -> str | None:
    for child in method.children:
        if child.type == "name":
            return _text(child, source)
    return None


def _class_name(class_node, source: bytes) -> str | None:
    for child in class_node.children:
        if child.type == "name":
            return _text(child, source)
    return None


def _literal_string(node, source: bytes) -> str | None:
    if node.type not in {"string", "encapsed_string"}:
        return None
    text = _text(node, source)
    if len(text) < 2 or text[0] not in {"'", '"'} or text[-1] != text[0]:
        return None
    # Interpolated double-quoted strings are dynamic and must not be inferred.
    if node.type == "encapsed_string" and any(
        child.type in {"variable_name", "${", "{"} for child in node.children
    ):
        return None
    return text[1:-1]


def _expression_values(node, source: bytes, assignments: dict[str, tuple[str, ...]]):
    """Return only values that can be proven without evaluating PHP."""

    literal = _literal_string(node, source)
    if literal is not None:
        return ("literal", (literal,))

    if node.type == "variable_name":
        name = _text(node, source)
        values = assignments.get(name)
        if values is not None:
            return ("assignment", values)
        return None

    if node.type == "array_creation_expression":
        values: list[str] = []
        for child in _named_children(node):
            if child.type != "array_element_initializer":
                continue
            elements = _named_children(child)
            value_node = elements[-1] if elements else None
            if value_node is None:
                return None
            nested = _expression_values(value_node, source, assignments)
            if nested is None or nested[0] not in {"literal", "assignment"}:
                return None
            values.extend(nested[1])
        return ("array", tuple(values))

    return None


def _direct_call(node, source: bytes) -> str | None:
    if node.type not in {"scoped_call_expression", "member_call_expression", "function_call_expression"}:
        return None
    # A direct call is recorded as source evidence, but never evaluated.
    return _text(node, source)


def _assignment(statement, source: bytes, assignments: dict[str, tuple[str, ...]]):
    children = _named_children(statement)
    if len(children) != 1 or children[0].type != "assignment_expression":
        return None
    assignment = children[0]
    parts = _named_children(assignment)
    if len(parts) != 2 or parts[0].type != "variable_name":
        return None
    variable_name = _text(parts[0], source)
    value = _expression_values(parts[1], source, assignments)
    if value is None:
        # Do not accidentally reuse a prior literal after a dynamic reassignment.
        assignments.pop(variable_name, None)
        return None
    assignments[variable_name] = value[1]
    return value


def _assignment_call(statement, source: bytes) -> str | None:
    """Return a direct call assigned to a local variable, without evaluating it."""

    children = _named_children(statement)
    if len(children) != 1 or children[0].type != "assignment_expression":
        return None
    parts = _named_children(children[0])
    if len(parts) != 2 or parts[0].type != "variable_name":
        return None
    return _direct_call(parts[1], source)


def _loader_kind(method_name: str) -> str | None:
    if method_name in _FORM_METHODS:
        return "form"
    if method_name in _SCRIPT_METHODS:
        return "script"
    return None


def _contains_parse_error(node) -> bool:
    return bool(node.has_error or node.is_missing or node.type in {"ERROR", "MISSING"})


def extract_php_loader_facts(source: bytes, source_file: str) -> PhpLoaderExtractionResult:
    """Extract loader facts from known actionUI loader methods in PHP source."""

    root = _PARSER.parse(source).root_node
    loaders: list[LoaderFact] = []
    diagnostics: list[Diagnostic] = []

    def diagnostic(code: str, message: str, node) -> None:
        start_line, end_line = _line(node)
        diagnostics.append(
            Diagnostic(
                code=code,
                message=message,
                source_file=source_file,
                start_line=start_line,
                end_line=end_line,
                evidence=_text(node, source),
            )
        )

    def emit(class_name: str, method_name: str, kind: str, value_kind: str, value: str, node) -> None:
        start_line, end_line = _line(node)
        loaders.append(
            LoaderFact(
                source_file=source_file,
                class_name=class_name,
                method_name=method_name,
                loader_kind=kind,
                value_kind=value_kind,
                value=value,
                start_line=start_line,
                end_line=end_line,
                evidence=_text(node, source),
            )
        )

    def visit(node) -> None:
        if node.type == "class_declaration":
            class_name = _class_name(node, source)
            if class_name is None:
                diagnostic("actionui.php.class_name_missing", "Class name is required for loader extraction.", node)
                return
            for child in node.children:
                if child.type != "declaration_list":
                    continue
                for member in child.children:
                    if member.type != "method_declaration":
                        continue
                    method_name = _method_name(member, source)
                    loader_kind = _loader_kind(method_name or "")
                    if loader_kind is None:
                        continue
                    if _contains_parse_error(member):
                        diagnostic(
                            "actionui.php.parse_error",
                            "Loader method contains a PHP parse error and was skipped.",
                            member,
                        )
                        continue
                    body = next((item for item in member.children if item.type == "compound_statement"), None)
                    if body is None:
                        diagnostic("actionui.php.loader_body_missing", "Loader method has no compound statement.", member)
                        continue
                    assignments: dict[str, tuple[str, ...]] = {}
                    for statement in body.children:
                        if statement.type == "expression_statement":
                            assigned = _assignment(statement, source, assignments)
                            if assigned is None:
                                call = _assignment_call(statement, source)
                                if call is not None:
                                    emit(
                                        class_name,
                                        method_name,
                                        loader_kind,
                                        "direct_call",
                                        call,
                                        statement,
                                    )
                                else:
                                    expression = _named_children(statement)
                                    if expression:
                                        diagnostic("actionui.php.dynamic_assignment", "Assignment is not a static literal or literal array.", expression[0])
                            continue
                        if statement.type != "return_statement":
                            if statement.is_named:
                                diagnostic(
                                    "actionui.php.unsupported_control_flow",
                                    "Loader control flow is not evaluated.",
                                    statement,
                                )
                            continue
                        expressions = _named_children(statement)
                        expression = expressions[0] if expressions else None
                        if expression is None:
                            diagnostic("actionui.php.empty_return", "Loader method returns without a value.", statement)
                            continue
                        static_value = _expression_values(expression, source, assignments)
                        if static_value is not None:
                            source_kind, values = static_value
                            for value in values:
                                emit(class_name, method_name, loader_kind, source_kind, value, expression)
                            continue
                        call = _direct_call(expression, source)
                        if call is not None:
                            emit(class_name, method_name, loader_kind, "direct_call", call, expression)
                            continue
                        diagnostic("actionui.php.dynamic_return", "Loader return is not statically resolvable.", expression)
            return
        for child in node.children:
            visit(child)

    visit(root)
    return PhpLoaderExtractionResult(tuple(loaders), tuple(diagnostics))
