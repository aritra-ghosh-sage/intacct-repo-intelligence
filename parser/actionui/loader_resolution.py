"""Resolve bounded actionUI loader evidence into script dependencies.

This module deliberately consumes explicit inheritance edges rather than symbol
parents.  It does not inspect JavaScript declarations, so callers cannot use
it for a global handler lookup.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

from tree_sitter_languages import get_parser

from .model import Diagnostic, LoaderFact, LoaderMethodFact, ResolvedLoaderFact, ScriptDependencyFact

_PARSER = get_parser("php")
_SCRIPT_SUFFIX = ".js"
_RESOURCE_PREFIX = "../resources/"
_FORM_EDITOR = "FormEditor"
_SCRIPT_TAG_EMITTERS = frozenset({"getLiveOrDebugScriptTag"})


class _StaticScriptTagParser(HTMLParser):
    """Read ``src`` values from one already-proven literal HTML fragment."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.paths: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "script":
            return
        for name, value in attrs:
            if name.lower() == "src" and value is not None:
                self.paths.append(value)


@dataclass(frozen=True)
class InheritanceEdge:
    """A single existing ``relationships.INHERITS`` fact projected for lookup."""

    child_class: str
    parent_class: str
    source_file: str
    evidence: str


@dataclass(frozen=True)
class LoaderResolutionResult:
    loaders: tuple[LoaderFact, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    resolved: tuple[ResolvedLoaderFact, ...] = ()


@dataclass(frozen=True)
class ScriptDependencyBuildResult:
    dependencies: tuple[ScriptDependencyFact, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()


def _line(node) -> tuple[int, int]:
    return node.start_point[0] + 1, node.end_point[0] + 1


def _text(node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _named(node):
    return tuple(child for child in node.children if child.is_named)


def _function_name(node, source: bytes) -> str | None:
    if node.type != "function_definition":
        return None
    name = node.child_by_field_name("name")
    return _text(name, source) if name is not None else None


def _walk(node):
    yield node
    for child in node.children:
        yield from _walk(child)


def _is_inside_conditional(node, owner) -> bool:
    current = node.parent
    while current is not None and current != owner:
        if current.type in {"if_statement", "else_clause", "else_if_clause", "switch_block", "for_statement", "foreach_statement", "while_statement", "do_statement", "conditional_expression"}:
            return True
        current = current.parent
    return False


def _direct_function_call_name(node, source: bytes) -> str | None:
    if node.type != "function_call_expression":
        return None
    function = node.child_by_field_name("function")
    if function is None or function.type != "name":
        return None
    return _text(function, source)


def _literal_string(node, source: bytes) -> str | None:
    if node.type != "string":
        return None
    value = _text(node, source)
    if len(value) < 2 or value[0] not in {"'", '"'} or value[-1] != value[0]:
        return None
    return value[1:-1]


def _has_ancestor(node, ancestor_type: str, boundary) -> bool:
    current = node.parent
    while current is not None and current != boundary:
        if current.type == ancestor_type:
            return True
        current = current.parent
    return False


def _function_call_name(node, source: bytes) -> str | None:
    if node.type == "function_call_expression":
        function = node.child_by_field_name("function")
        if function is not None and function.type == "name":
            return _text(function, source)
        return None
    if node.type == "scoped_call_expression":
        names = [child for child in _named(node) if child.type == "name"]
        return _text(names[-1], source) if len(names) == 2 else None
    return None


def _literal_call_arguments(node, source: bytes) -> tuple[tuple[str, object], ...]:
    arguments = next((child for child in _named(node) if child.type == "arguments"), None)
    if arguments is None:
        return ()
    values: list[tuple[str, object]] = []
    for argument in _named(arguments):
        if argument.type != "argument":
            continue
        expression = next(iter(_named(argument)), None)
        if expression is None:
            continue
        value = _literal_string(expression, source)
        if value is not None:
            values.append((value, expression))
    return tuple(values)


def _script_tag_interpolation(node, source: bytes) -> bool:
    echo = node.parent
    while echo is not None and echo.type != "echo_statement":
        echo = echo.parent
    if echo is None or echo.parent is None:
        return False
    siblings = tuple(echo.parent.children)
    index = siblings.index(echo)
    if index == 0 or index == len(siblings) - 1:
        return False
    before = siblings[index - 1]
    after = siblings[index + 1]
    if before.type != "text_interpolation" or after.type != "text_interpolation":
        return False
    before_text = "".join(
        _text(child, source) for child in _named(before) if child.type == "text"
    ).lower()
    after_text = "".join(
        _text(child, source) for child in _named(after) if child.type == "text"
    ).lower()
    return "<script" in before_text and "src=" in before_text and "</script>" in after_text


def _is_parent_loader_call(fact: LoaderFact) -> bool:
    return fact.value_kind == "direct_call" and fact.value.startswith("parent::")


def _parent_method_name(fact: LoaderFact) -> str | None:
    match = re.match(r"parent::([A-Za-z_][A-Za-z0-9_]*)\s*\(", fact.value)
    return match.group(1) if match else None


def resolve_inherited_loader_facts(
    loader_facts: tuple[LoaderFact, ...],
    inheritance_edges: tuple[InheritanceEdge, ...],
    *,
    form_editor_source_file: str,
    method_facts: tuple[LoaderMethodFact, ...] = (),
    concrete_editor_classes: tuple[str, ...] = (),
) -> LoaderResolutionResult:
    """Resolve only literal base loaders and the explicit FormEditor convention.

    A ``parent::`` call is followed through the supplied ``INHERITS`` facts.
    The only special base behavior is FormEditor's documented dynamic form
    convention; it remains a convention fact rather than a guessed XML path.
    """

    by_class_method: dict[tuple[str, str], list[LoaderFact]] = {}
    declared: dict[tuple[str, str], LoaderMethodFact] = {}
    parents: dict[str, list[InheritanceEdge]] = {}
    for fact in loader_facts:
        by_class_method.setdefault((fact.class_name, fact.method_name.lower()), []).append(fact)
    for method in method_facts:
        declared[(method.class_name, method.method_name.lower())] = method
    for edge in inheritance_edges:
        parents.setdefault(edge.child_class, []).append(edge)

    resolved: list[ResolvedLoaderFact] = []
    diagnostics: list[Diagnostic] = []

    def issue(code: str, message: str, fact: LoaderFact | LoaderMethodFact) -> None:
        diagnostics.append(
            Diagnostic(code, message, fact.source_file, fact.start_line, fact.end_line, evidence=fact.evidence)
        )

    def edges_for(cls: str) -> list[InheritanceEdge]:
        unique = {(e.child_class, e.parent_class, e.source_file, e.evidence): e for e in parents.get(cls, ())}
        return sorted(unique.values(), key=lambda e: (e.parent_class, e.source_file, e.evidence))

    def visit(cls: str, method_name: str, seen: tuple[str, ...], path: tuple[InheritanceEdge, ...]) -> list[ResolvedLoaderFact]:
        slot = (cls, method_name.lower())
        method = declared.get(slot)
        own = sorted(by_class_method.get(slot, ()), key=lambda f: (f.source_file, f.start_line, f.value))
        if method is not None:
            # A declaration is an override even when extraction produced no values.
            if not own:
                return []
            output: list[ResolvedLoaderFact] = []
            for fact in own:
                if _is_parent_loader_call(fact):
                    output.extend(visit_parent(cls, _parent_method_name(fact) or method_name, seen, path, fact))
                elif fact.value_kind != "direct_call":
                    output.append(ResolvedLoaderFact(cls, fact, path))
            return output
        return visit_parent(cls, method_name, seen, path, None)

    def visit_parent(cls: str, method_name: str, seen: tuple[str, ...], path: tuple[InheritanceEdge, ...], evidence_fact: LoaderFact | None) -> list[ResolvedLoaderFact]:
        edges = edges_for(cls)
        anchor = evidence_fact or (own_first(cls, method_name) or declared.get((cls, method_name.lower())))
        if len(edges) != 1:
            if anchor is not None:
                issue("actionui.loader.inheritance_ambiguous" if edges else "actionui.loader.inheritance_missing", "Parent loader cannot be resolved from exactly one INHERITS relationship.", anchor)
            return []
        edge = edges[0]
        if edge.parent_class in seen:
            if anchor is not None:
                issue("actionui.loader.inheritance_cycle", "Parent loader resolution found an inheritance cycle.", anchor)
            return []
        next_path = path + (edge,)
        if edge.parent_class == _FORM_EDITOR and method_name.lower() == "getmetadatakeyname":
            fact = LoaderFact(form_editor_source_file, _FORM_EDITOR, method_name, "form", "form_editor_convention", "{entity}_form.pxml", anchor.start_line if anchor else 1, anchor.end_line if anchor else 1, "FormEditor::getMetadataKeyName returns {entity}_form.pxml; " + edge.evidence)
            return [ResolvedLoaderFact(cls, fact, next_path)]
        if (
            method_name.lower() != "getmetadatakeyname"
            and (edge.parent_class, method_name.lower()) not in declared
            and not by_class_method.get((edge.parent_class, method_name.lower()))
        ):
            if anchor is not None:
                issue("actionui.loader.parent_method_missing", "Inherited loader method has no static evidence.", anchor)
            return []
        return [
            ResolvedLoaderFact(cls, item.source_fact, item.inheritance_path)
            for item in visit(edge.parent_class, method_name, seen + (edge.parent_class,), next_path)
        ]

    def own_first(cls: str, method_name: str) -> LoaderFact | None:
        facts = by_class_method.get((cls, method_name.lower()), ())
        return facts[0] if facts else None

    classes = set(concrete_editor_classes) or {f.class_name for f in loader_facts} | {m.class_name for m in method_facts}
    output: list[ResolvedLoaderFact] = []
    methods_by_class: dict[str, set[str]] = {}
    for f in loader_facts:
        methods_by_class.setdefault(f.class_name, set()).add(f.method_name.lower())
    for m in method_facts:
        methods_by_class.setdefault(m.class_name, set()).add(m.method_name.lower())

    def ancestor_classes(cls: str, seen: frozenset[str] = frozenset()) -> set[str]:
        if cls in seen:
            return set()
        result: set[str] = set()
        for edge in edges_for(cls):
            result.add(edge.parent_class)
            result.update(ancestor_classes(edge.parent_class, seen | {cls}))
        return result

    for cls in sorted(classes):
        methods = set(methods_by_class.get(cls, ()))
        for ancestor in ancestor_classes(cls):
            methods.update(methods_by_class.get(ancestor, ()))
        # FormEditor metadata naming is the sole documented loader convention.
        # It applies to a concrete editor even when neither it nor an
        # intermediate base declares an override.
        methods.add("getmetadatakeyname")
        for method in sorted(methods):
            output.extend(visit(cls, method, (cls,), ()))
    unique: dict[tuple[str, str, str, str, str, int, int], ResolvedLoaderFact] = {}
    for item in output:
        f = item.source_fact
        # Values may intentionally repeat in one loader.  Their source range
        # is provenance, so only exact same-location facts may coalesce.
        unique.setdefault(
            (
                item.effective_class,
                f.loader_kind,
                f.value_kind,
                f.value,
                f.source_file,
                f.start_line,
                f.end_line,
            ),
            item,
        )
    final = tuple(unique.values())
    return LoaderResolutionResult(tuple(item.source_fact for item in final), tuple(diagnostics), final)


def _normalize_script_path(raw_path: str, repo_root: Path) -> tuple[str | None, str | None]:
    if raw_path.startswith(("http://", "https://", "//")):
        return None, "actionui.script.external_path"
    if "${" in raw_path or "$" in raw_path or "{" in raw_path:
        return None, "actionui.script.dynamic_path"
    if raw_path.startswith(_RESOURCE_PREFIX):
        normalized = "app/resources/" + raw_path[len(_RESOURCE_PREFIX) :]
    elif raw_path.startswith("app/resources/"):
        normalized = raw_path
    else:
        return None, "actionui.script.bare_path"
    candidate = (repo_root / normalized).resolve()
    if not candidate.is_relative_to(repo_root.resolve()):
        return None, "actionui.script.ambiguous_path"
    if not candidate.is_file():
        return None, "actionui.script.missing_path"
    return normalized, None


def _dependency(
    source_file: str,
    raw_path: str,
    dependency_kind: str,
    activation_state: str,
    start_line: int,
    end_line: int,
    evidence: str,
    repo_root: Path,
) -> tuple[ScriptDependencyFact | None, Diagnostic | None]:
    normalized, error = _normalize_script_path(raw_path, repo_root)
    if error is not None:
        return None, Diagnostic(error, "Script path is not a usable local JavaScript dependency.", source_file, start_line, end_line, evidence=evidence)
    return ScriptDependencyFact(source_file, normalized, dependency_kind, activation_state, start_line, end_line, evidence), None


def _ast_script_paths(function, source: bytes) -> tuple[tuple[str, int, int, str, bool], ...]:
    """Return literal script paths proven by PHP syntax-tree output nodes.

    Raw source text is intentionally never scanned.  A path must come from a
    literal ``echo`` script tag, a script-tag interpolation's literal
    ``URLReplace::replaceRelativeURL`` argument, or a known literal script-tag
    emitter.  Comments and arbitrary string literals are therefore not facts.
    """

    facts: list[tuple[str, int, int, str, bool]] = []
    seen: set[tuple[str, int, int]] = set()

    def emit(path: str, node, evidence: str) -> None:
        start_line, end_line = _line(node)
        key = (path, node.start_byte, node.end_byte)
        if key not in seen:
            seen.add(key)
            facts.append((path, start_line, end_line, evidence, _is_inside_conditional(node, function)))

    for node in _walk(function):
        if node.has_error or node.is_missing:
            continue
        if node.type == "echo_statement":
            for child in _walk(node):
                literal = _literal_string(child, source)
                if literal is None:
                    continue
                parser = _StaticScriptTagParser()
                parser.feed(literal)
                parser.close()
                for path in parser.paths:
                    emit(path, child, _text(child, source))

        if node.type not in {"function_call_expression", "scoped_call_expression"}:
            continue
        if not _has_ancestor(node, "echo_statement", function):
            continue
        name = _function_call_name(node, source)
        is_script_tag_path = (
            name == "replaceRelativeURL" and _script_tag_interpolation(node, source)
        )
        if is_script_tag_path or name in _SCRIPT_TAG_EMITTERS:
            for path, literal_node in _literal_call_arguments(node, source):
                emit(path, literal_node, _text(literal_node, source))
    return tuple(facts)


def extract_common_script_dependencies(source: bytes, source_file: str, repo_root: Path) -> ScriptDependencyBuildResult:
    """Extract the static same-file call tree rooted at ``jsCommonIncludes``.

    Calls guarded by control flow are conditional.  Literal script paths are
    retained only when their containing helper is reachable from that root.
    """

    root = _PARSER.parse(source).root_node
    functions = {name: node for node in _walk(root) if (name := _function_name(node, source)) is not None}
    root_function = functions.get("jsCommonIncludes")
    if root_function is None:
        return ScriptDependencyBuildResult()

    dependencies: list[ScriptDependencyFact] = []
    diagnostics: list[Diagnostic] = []
    pending = [(root_function, "active")]
    visited: set[tuple[int, str]] = set()
    while pending:
        function, activation = pending.pop()
        key = (function.start_byte, activation)
        if key in visited:
            continue
        visited.add(key)
        for raw_path, start_line, end_line, evidence, conditional in _ast_script_paths(function, source):
            state = "conditional" if activation == "conditional" or conditional else "active"
            fact, issue = _dependency(source_file, raw_path, "common_include", state, start_line, end_line, evidence, repo_root)
            if fact is not None:
                dependencies.append(fact)
            if issue is not None:
                diagnostics.append(issue)
        for node in _walk(function):
            callee = _direct_function_call_name(node, source)
            target = functions.get(callee or "")
            if target is None:
                continue
            state = "conditional" if activation == "conditional" or _is_inside_conditional(node, function) else "active"
            pending.append((target, state))
    return ScriptDependencyBuildResult(tuple(dependencies), tuple(diagnostics))


def build_script_dependencies(
    loaders: tuple[LoaderFact, ...],
    *,
    repo_root: Path,
) -> ScriptDependencyBuildResult:
    """Normalize statically returned editor script paths into dependency facts."""

    dependencies: list[ScriptDependencyFact] = []
    diagnostics: list[Diagnostic] = []
    for fact in sorted(loaders, key=lambda item: (item.source_file, item.start_line, item.value)):
        if fact.loader_kind != "script":
            continue
        dependency, issue = _dependency(
            fact.source_file,
            fact.value,
            "editor_loader",
            "active",
            fact.start_line,
            fact.end_line,
            fact.evidence,
            repo_root,
        )
        if dependency is not None:
            dependencies.append(dependency)
        if issue is not None:
            diagnostics.append(issue)
    return ScriptDependencyBuildResult(tuple(dependencies), tuple(diagnostics))
