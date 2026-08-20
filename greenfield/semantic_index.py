"""Build a deterministic, immutable semantic sidecar for an ``ia-main`` revision."""

from __future__ import annotations

import hashlib
import posixpath
import re
import subprocess
import tarfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from greenfield.ent_parser import extract_ent_facts
from greenfield.semantic_contract import (
    SemanticIndexError,
    finalize_index,
    node_key,
    source_evidence,
    write_index,
)
from parser.actionui.xml_extractor import extract_actionui_xml_facts
from parser.extractors import php_extractor
from parser.ui.nextgen import NextGenSource, extract_nextgen_families

EXTRACTOR_VERSIONS = {
    "ent": "1",
    "php_entity_bridge": "1",
    "openapi_bridge": "1",
    "actionui_bridge": "1",
    "nextgen_bridge": "1",
    "import_bridge": "1",
}

_OBJECT_FILENAME = re.compile(r"^objects\.([^.]+)\.(.+?)\.s\d+(?:\.|$)", re.IGNORECASE)
_WORKFLOW_FILENAME = re.compile(
    r"^workflows\.([^.]+)\.([^.]+)\.([^.]+)\.s\d+\.api\.ya?ml$",
    re.IGNORECASE,
)
_LITERAL_ENTITY_CALL = re.compile(
    r"\b(?:getManager|GetListQuick|GetList|__construct)\s*\(\s*(['\"])([A-Za-z_][A-Za-z0-9_]*)\1"
)
_ENTITY_CONFIG = re.compile(
    r"['\"]entity['\"]\s*=>\s*(['\"])([A-Za-z_][A-Za-z0-9_]*)\1"
)
_ENTITY_ASSIGNMENT = re.compile(r"\$(\w+)\s*=\s*(['\"])([A-Za-z_][A-Za-z0-9_]*)\2")
_MANAGER_CLASS = re.compile(r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)Manager$")
_IMPORT_INCLUDE = re.compile(
    r"\b(?:include|require(?:_once)?)\s*\(?\s*(['\"])([^'\"]+\.(?:inc|cls))\1"
)
_FLATFILE_RULE = re.compile(r"\bcreateFlatfileRule\s*\(")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _line(text: str, offset: int) -> int:
    return text.count("\n", 0, max(0, offset)) + 1


def _node(kind: str, identity: str, **attributes: Any) -> dict[str, Any]:
    return {
        "key": node_key(kind, identity),
        "kind": kind,
        "identity": identity,
        **attributes,
    }


class _Builder:
    def __init__(self, repository: str, revision: str) -> None:
        self.repository = repository
        self.revision = revision
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: dict[tuple[Any, ...], dict[str, Any]] = {}
        self.diagnostics: list[dict[str, Any]] = []
        self.entity_nodes: dict[str, list[str]] = {}
        self.api_nodes: dict[str, str] = {}

    def add_node(self, node: dict[str, Any]) -> str:
        key = str(node["key"])
        existing = self.nodes.get(key)
        if existing is None:
            self.nodes[key] = node
        elif existing != node:
            # Identity is stable; merge non-conflicting attributes while keeping
            # the first source-backed definition deterministic.
            merged = dict(existing)
            for name, value in node.items():
                merged.setdefault(name, value)
            self.nodes[key] = merged
        return key

    def add_edge(
        self,
        *,
        source: str,
        target: str | None,
        kind: str,
        resolution: str,
        evidence: dict[str, Any],
        target_ref: str | None = None,
    ) -> None:
        key = (source, target, target_ref, kind, resolution)
        if key not in self.edges:
            self.edges[key] = {
                "source": source,
                "target": target,
                "target_ref": target_ref,
                "kind": kind,
                "resolution": resolution,
                "evidence": [evidence],
            }
        else:
            self.edges[key]["evidence"].append(evidence)

    def add_diagnostic(
        self, path: str, code: str, message: str, line: int = 1, **details: Any
    ) -> None:
        self.diagnostics.append(
            {
                "code": code,
                "message": message,
                "source_path": path,
                "start_line": line,
                "end_line": line,
                "details": details,
            }
        )

    def add_entity(self, name: str, path: str, source_hash: str) -> str:
        key = self.add_node(
            _node(
                "entity",
                name,
                repository=self.repository,
                source_path=path,
                source_hash=source_hash,
            )
        )
        self.entity_nodes.setdefault(name.casefold(), []).append(key)
        return key

    def resolve_entity(self, name: str) -> tuple[str | None, str]:
        matches = sorted(set(self.entity_nodes.get(name.casefold(), [])))
        if len(matches) == 1:
            return matches[0], "resolved_exact"
        if len(matches) > 1:
            return None, "ambiguous"
        return None, "unresolved"

    def add_entity_edge(
        self,
        *,
        source: str,
        entity_name: str,
        kind: str,
        resolution: str,
        evidence: dict[str, Any],
    ) -> None:
        target, exact_status = self.resolve_entity(entity_name)
        actual = (
            exact_status if resolution == "explicit_source" and target else resolution
        )
        self.add_edge(
            source=source,
            target=target,
            target_ref=entity_name,
            kind=kind,
            resolution=actual,
            evidence=evidence,
        )
        if target is None:
            self.add_diagnostic(
                evidence["source_path"],
                f"entity_{actual}",
                f"Entity reference {entity_name!r} could not be resolved uniquely.",
                int(evidence["source_lines"]["start"]),
                entity_name=entity_name,
            )

    def finish(self) -> dict[str, Any]:
        return finalize_index(
            repository=self.repository,
            revision=self.revision,
            nodes=list(self.nodes.values()),
            edges=list(self.edges.values()),
            diagnostics=self.diagnostics,
            extractor_versions=EXTRACTOR_VERSIONS,
        )


def _object_key(path: str, document: Mapping[str, Any]) -> str | None:
    explicit = document.get("object")
    if isinstance(explicit, str) and "/" in explicit:
        return explicit.strip().strip("/")
    match = _OBJECT_FILENAME.match(Path(path).name)
    if match:
        return f"{match.group(1)}/{match.group(2)}"
    return None


def _is_nextgen(path: str) -> bool:
    name = Path(path).name.lower()
    return ".uimeta" in name or ".viewmeta" in name or name.endswith(".view.yaml")


def _is_php(path: str) -> bool:
    return Path(path).suffix.lower() in {".php", ".cls", ".inc"}


def _is_import(path: str) -> bool:
    name = Path(path).name.lower()
    return (
        name.startswith(("csvimport_", "csv_metadata_"))
        or "/dm/import/" in path.lower()
        or "/dm/validation/" in path.lower()
    )


def _nearest_symbol(symbols: list[Any], line: int) -> Any | None:
    containing = [
        symbol for symbol in symbols if symbol.start_line <= line <= symbol.end_line
    ]
    return min(
        containing,
        key=lambda symbol: (symbol.end_line - symbol.start_line, symbol.start_line),
        default=None,
    )


def _nearest_callable(symbols: list[Any], line: int) -> Any | None:
    containing = [
        symbol
        for symbol in symbols
        if symbol.kind in {"method", "function"}
        and symbol.start_line <= line <= symbol.end_line
    ]
    return min(
        containing,
        key=lambda symbol: (symbol.end_line - symbol.start_line, symbol.start_line),
        default=None,
    )


def _resolve_import_include(
    source_path: str, included_path: str, files: Mapping[str, str]
) -> tuple[str, str | None]:
    normalized = included_path.replace("\\", "/")
    candidates: list[str] = []
    relative = posixpath.normpath(
        posixpath.join(posixpath.dirname(source_path), normalized)
    )
    if relative in files:
        candidates.append(relative)
    if normalized in files and normalized not in candidates:
        candidates.append(normalized)
    if not candidates:
        basename = posixpath.basename(normalized)
        candidates = sorted(
            path for path in files if posixpath.basename(path) == basename
        )
    if len(candidates) == 1:
        return "resolved_exact", candidates[0]
    if len(candidates) > 1:
        return "ambiguous", None
    return "unresolved", None


def _process_entities(builder: _Builder, files: Mapping[str, str]) -> None:
    parsed: list[tuple[str, dict[str, Any]]] = []
    for path in sorted(path for path in files if path.lower().endswith(".ent")):
        text = files[path]
        facts = extract_ent_facts(text, path)
        source_hash = facts["source_hash"]
        builder.add_node(
            _node(
                "ent_file", path, repository=builder.repository, source_hash=source_hash
            )
        )
        identity = facts.get("identity")
        if not identity:
            builder.add_diagnostic(
                path, "entity_identity_missing", "No .ent identity found."
            )
            continue
        entity_key = builder.add_entity(identity, path, source_hash)
        builder.nodes[entity_key]["metadata"] = facts["metadata"]
        builder.nodes[entity_key]["ent_facts"] = {
            "literal_facts": facts["literal_facts"],
            "field_facts": facts["field_facts"],
            "array_facts": facts["array_facts"],
        }
        for field in facts["field_facts"]:
            field_identity = (
                f"{identity}:{field['section']}:{field['key']}:"
                f"{field['evidence']['source_lines']['start']}"
            )
            field_node = builder.add_node(
                _node(
                    "ent_field",
                    field_identity,
                    repository=builder.repository,
                    entity=identity,
                    section=field["section"],
                    field_name=field["key"],
                    field_value=field["value"],
                    source_path=path,
                    source_hash=source_hash,
                )
            )
            builder.add_edge(
                source=entity_key,
                target=field_node,
                kind="entity_field",
                resolution="explicit_source",
                evidence=field["evidence"],
            )
        parsed.append((entity_key, facts))

    # Resolve only after every committed .ent identity is in the index. Path
    # ordering must not turn a valid forward reference into a false gap.
    for entity_key, facts in parsed:
        for relation in facts["relationships"]:
            builder.add_entity_edge(
                source=entity_key,
                entity_name=relation["target"],
                kind=relation["kind"],
                resolution="explicit_source",
                evidence=relation["evidence"],
            )
        for include in facts["includes"]:
            include_resolution = "unresolved"
            include_path = posixpath.normpath(
                posixpath.join(
                    posixpath.dirname(include["evidence"]["source_path"]),
                    include["target"],
                )
            )
            if include_path not in files and include["target"] in files:
                include_path = include["target"]
            if include_path not in files:
                basename_matches = [
                    path
                    for path in files
                    if path.lower().endswith(".ent")
                    and posixpath.basename(path)
                    == posixpath.basename(include["target"])
                ]
                if len(basename_matches) == 1:
                    include_path = basename_matches[0]
                    include_resolution = "resolved_exact"
                elif len(basename_matches) > 1:
                    include_resolution = "ambiguous"
                    builder.add_diagnostic(
                        include["evidence"]["source_path"],
                        "ent_include_ambiguous",
                        "Static .ent include basename matches multiple committed files.",
                        include["evidence"]["source_lines"]["start"],
                        target=include["target"],
                        matches=sorted(basename_matches),
                    )
            elif include_path in files:
                include_resolution = "resolved_exact"
            include_target = (
                node_key("ent_file", include_path) if include_path in files else None
            )
            builder.add_edge(
                source=entity_key,
                target=include_target,
                target_ref=include["target"],
                kind="ent_include",
                resolution=include_resolution,
                evidence=include["evidence"],
            )
        builder.diagnostics.extend(facts["diagnostics"])


def _openapi_document_kind(path: str) -> str | None:
    lower = path.lower()
    name = Path(path).name.lower()
    if "/history/" in lower:
        return "openapi_history"
    if "/models/" in lower and name.endswith((".schema.yaml", ".schema.yml")):
        return "openapi_schema"
    if "/paths/" in lower and name.endswith((".api.yaml", ".api.yml")):
        return "openapi_path"
    return None


def _process_openapi(builder: _Builder, files: Mapping[str, str]) -> None:
    for path in sorted(files):
        if not path.lower().startswith("app/source/openapispec/"):
            continue
        document_kind = _openapi_document_kind(path)
        if document_kind is None:
            continue
        text = files[path]
        source_hash = _sha(text)
        if document_kind == "openapi_history":
            builder.add_node(
                _node(
                    document_kind,
                    path,
                    repository=builder.repository,
                    source_hash=source_hash,
                )
            )
            continue
        try:
            document = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            builder.add_diagnostic(path, "openapi_yaml_invalid", str(exc))
            continue
        if not isinstance(document, dict):
            builder.add_diagnostic(
                path, "openapi_yaml_not_mapping", "OpenAPI YAML is not a mapping."
            )
            continue
        object_key = _object_key(path, document)
        workflow_match = _WORKFLOW_FILENAME.match(Path(path).name)
        if object_key is None and workflow_match:
            object_key = f"{workflow_match.group(1)}/{workflow_match.group(2)}"
        if object_key is None:
            continue
        object_node = builder.api_nodes.get(object_key)
        if object_node is None:
            object_node = builder.add_node(
                _node("api_object", object_key, repository=builder.repository)
            )
            builder.api_nodes[object_key] = object_node
        schema_node = builder.add_node(
            _node(
                document_kind,
                path,
                repository=builder.repository,
                source_hash=source_hash,
            )
        )
        line = next(
            (
                index
                for index, value in enumerate(text.splitlines(), 1)
                if "object" in value or "x-mappedTo" in value
            ),
            1,
        )
        builder.add_edge(
            source=schema_node,
            target=object_node,
            kind=(
                "schema_object"
                if document_kind == "openapi_schema"
                else "api_path_object"
            ),
            resolution="resolved_exact",
            evidence=source_evidence(
                path=path,
                source_hash=source_hash,
                text=text,
                start_line=line,
                fact="openapi_object_identity",
                object_key=object_key,
            ),
        )
        mapped = document.get("x-mappedTo")
        if (
            isinstance(mapped, str)
            and mapped.strip()
            and "/" not in mapped
            and "\\" not in mapped
        ):
            builder.add_entity_edge(
                source=object_node,
                entity_name=mapped.strip(),
                kind="api_object_entity",
                resolution="explicit_source",
                evidence=source_evidence(
                    path=path,
                    source_hash=source_hash,
                    text=text,
                    start_line=line,
                    fact="openapi_x_mappedTo",
                    object_key=object_key,
                    mapped_to=mapped.strip(),
                ),
            )
        elif "x-mappedTo" in document:
            builder.add_diagnostic(
                path,
                "openapi_x_mappedTo_invalid",
                "x-mappedTo is not a usable literal entity name.",
                line,
            )

        if workflow_match:
            module, object_name, action = workflow_match.groups()
            object_key = f"{module}/{object_name}"
            object_node = builder.api_nodes.get(object_key)
            if object_node is None:
                object_node = builder.add_node(
                    _node("api_object", object_key, repository=builder.repository)
                )
                builder.api_nodes[object_key] = object_node
            workflow_identity = f"{object_key}/{action}"
            workflow_node = builder.add_node(
                _node("workflow", workflow_identity, repository=builder.repository)
            )
            builder.add_edge(
                source=workflow_node,
                target=object_node,
                kind="workflow_object",
                resolution="resolved_exact",
                evidence=source_evidence(
                    path=path,
                    source_hash=source_hash,
                    text=text,
                    start_line=1,
                    fact="workflow_object_path",
                    object_key=object_key,
                ),
            )


def _process_nextgen(builder: _Builder, files: Mapping[str, str]) -> None:
    sources = [
        NextGenSource(path, files[path]) for path in sorted(files) if _is_nextgen(path)
    ]
    result = extract_nextgen_families(sources)
    resolved_entity_families: set[str] = set()
    for family in result.families:
        ui_node = builder.add_node(
            _node("nextgen_surface", family.source_file, repository=builder.repository)
        )
        api_node = builder.api_nodes.get(family.family_key)
        line = family.start_line
        text = files[family.source_file]
        evidence = source_evidence(
            path=family.source_file,
            source_hash=_sha(text),
            text=text,
            start_line=line,
            fact="nextgen_object_identity",
            object_key=family.family_key,
        )
        builder.add_edge(
            source=ui_node,
            target=api_node,
            target_ref=family.family_key,
            kind="nextgen_api_object",
            resolution="resolved_exact" if api_node else "unresolved",
            evidence=evidence,
        )
        if api_node is None:
            builder.add_diagnostic(
                family.source_file,
                "nextgen_api_object_unresolved",
                "NextGen family has no matching API object in the inspected revision.",
                line,
                object_key=family.family_key,
            )
        elif any(
            edge["source"] == api_node
            and edge["kind"] == "api_object_entity"
            and edge["target"] is not None
            and edge["resolution"] == "resolved_exact"
            for edge in builder.edges.values()
        ):
            resolved_entity_families.add(family.family_key)
    for diagnostic in result.diagnostics:
        if (
            diagnostic.code.startswith("nextgen.entity_mapping.")
            and diagnostic.source_file
        ):
            family_key = next(
                (
                    family.family_key
                    for family in result.families
                    if family.source_file == diagnostic.source_file
                ),
                None,
            )
            if family_key in resolved_entity_families:
                continue
        builder.add_diagnostic(
            diagnostic.source_file,
            diagnostic.code,
            diagnostic.message,
            diagnostic.start_line,
            evidence=diagnostic.evidence,
        )


def _process_actionui(builder: _Builder, files: Mapping[str, str]) -> None:
    for path in sorted(path for path in files if path.lower().endswith("_form.xml")):
        text = files[path]
        result = extract_actionui_xml_facts(text, path)
        ui_node = builder.add_node(
            _node("actionui_surface", path, repository=builder.repository)
        )
        source_hash = _sha(text)
        for reference in result.entity_references:
            builder.add_entity_edge(
                source=ui_node,
                entity_name=reference.entity_name,
                kind="actionui_entity",
                resolution="explicit_source",
                evidence=source_evidence(
                    path=path,
                    source_hash=source_hash,
                    text=text,
                    start_line=reference.start_line,
                    end_line=reference.end_line,
                    fact="actionui_entity_element",
                    entity_name=reference.entity_name,
                ),
            )
        for include in result.includes:
            include_path = posixpath.normpath(
                posixpath.join(posixpath.dirname(path), include.included_path)
            )
            include_target = (
                node_key("xml_file", include_path) if include_path in files else None
            )
            if include_target:
                builder.add_node(
                    _node(
                        "xml_file",
                        include_path,
                        repository=builder.repository,
                        source_hash=_sha(files[include_path]),
                    )
                )
            builder.add_edge(
                source=ui_node,
                target=include_target,
                target_ref=include.included_path,
                kind="ui_include",
                resolution="resolved_exact" if include_target else "unresolved",
                evidence=source_evidence(
                    path=path,
                    source_hash=source_hash,
                    text=text,
                    start_line=include.start_line,
                    end_line=include.end_line,
                    fact="actionui_include",
                    included_path=include.included_path,
                ),
            )
        for field in result.fields:
            field_node = builder.add_node(
                _node(
                    "ui_field",
                    f"{path}:{field.field_name}:{field.start_line}",
                    repository=builder.repository,
                )
            )
            builder.add_edge(
                source=ui_node,
                target=field_node,
                kind="ui_field",
                resolution="explicit_source",
                evidence=source_evidence(
                    path=path,
                    source_hash=source_hash,
                    text=text,
                    start_line=field.start_line,
                    end_line=field.end_line,
                    fact="actionui_field",
                    field_name=field.field_name,
                    field_path=field.field_path,
                ),
            )
        for diagnostic in result.diagnostics:
            builder.add_diagnostic(
                diagnostic.source_file,
                diagnostic.code,
                diagnostic.message,
                diagnostic.start_line,
            )


def _process_xml_inventory(builder: _Builder, files: Mapping[str, str]) -> None:
    """Keep unsupported XML visible without pretending to understand its dialect."""

    for path in sorted(path for path in files if path.lower().endswith(".xml")):
        if path.lower().endswith("_form.xml"):
            continue
        builder.add_node(_node("xml_file", path, repository=builder.repository))
        builder.add_diagnostic(
            path,
            "unsupported_xml_semantics",
            "XML was inventoried but no domain-specific semantic adapter was applied.",
            1,
        )


def _process_php_and_imports(builder: _Builder, files: Mapping[str, str]) -> None:
    for path in sorted(files):
        text = files[path]
        if not _is_php(path):
            continue
        source_hash = _sha(text)
        symbols: list[Any] = []
        try:
            symbols = php_extractor.extract(text.encode("utf-8"), path)
        except (AttributeError, TypeError, ValueError, RuntimeError) as exc:
            # Parser diagnostics must not erase the committed file inventory.
            builder.add_diagnostic(path, "php_symbol_parser_failed", str(exc))
        symbol_nodes: list[tuple[Any, str]] = []
        for symbol in symbols:
            identity = f"{path}:{symbol.kind}:{symbol.parent_symbol or ''}:{symbol.name}:{symbol.start_line}"
            symbol_node = builder.add_node(
                _node(
                    "php_symbol",
                    identity,
                    repository=builder.repository,
                    name=symbol.name,
                    symbol_kind=symbol.kind,
                    start_line=symbol.start_line,
                    end_line=symbol.end_line,
                    parent_symbol=symbol.parent_symbol,
                )
            )
            symbol_nodes.append((symbol, symbol_node))

        assignments: dict[
            tuple[str, int, int] | None, dict[str, list[tuple[str, str, int]]]
        ] = {}
        for match in _ENTITY_ASSIGNMENT.finditer(text):
            assignment_line = _line(text, match.start())
            scope = _nearest_callable(symbols, assignment_line)
            scope_key = (
                (scope.kind, scope.start_line, scope.end_line) if scope else None
            )
            assignments.setdefault(scope_key, {}).setdefault(match.group(1), []).append(
                (match.group(2), match.group(3), match.start())
            )
        references = list(_LITERAL_ENTITY_CALL.finditer(text)) + list(
            _ENTITY_CONFIG.finditer(text)
        )
        for match in references:
            entity_name = match.group(2)
            line = _line(text, match.start())
            symbol = _nearest_symbol([item[0] for item in symbol_nodes], line)
            symbol_node = next(
                (node for item, node in symbol_nodes if item is symbol), None
            )
            source_node = symbol_node or builder.add_node(
                _node("source_file", path, repository=builder.repository)
            )
            builder.add_entity_edge(
                source=source_node,
                entity_name=entity_name,
                kind="symbol_entity",
                resolution="explicit_source",
                evidence=source_evidence(
                    path=path,
                    source_hash=source_hash,
                    text=text,
                    start_line=line,
                    fact="php_literal_entity_reference",
                    expression=match.group(0),
                ),
            )

        for manager_match in re.finditer(r"\bgetManager\s*\(\s*\$(\w+)\s*\)", text):
            manager_line = _line(text, manager_match.start())
            scope = _nearest_callable(symbols, manager_line)
            scope_key = (
                (scope.kind, scope.start_line, scope.end_line) if scope else None
            )
            assignment_candidates = [
                assignment
                for assignment in (
                    assignments.get(scope_key, {}).get(manager_match.group(1), [])
                    if scope is not None
                    else []
                )
                if assignment[2] < manager_match.start()
            ]
            if len(assignment_candidates) != 1:
                builder.add_diagnostic(
                    path,
                    "symbol_entity_dynamic",
                    "getManager did not receive one safe local literal assignment.",
                    manager_line,
                )
                continue
            assignment = assignment_candidates[0]
            entity_name = assignment[1]
            line = manager_line
            symbol = _nearest_symbol([item[0] for item in symbol_nodes], line)
            symbol_node = next(
                (node for item, node in symbol_nodes if item is symbol), None
            )
            source_node = symbol_node or builder.add_node(
                _node("source_file", path, repository=builder.repository)
            )
            builder.add_entity_edge(
                source=source_node,
                entity_name=entity_name,
                kind="symbol_entity",
                resolution="explicit_source",
                evidence=source_evidence(
                    path=path,
                    source_hash=source_hash,
                    text=text,
                    start_line=line,
                    fact="php_local_entity_reference",
                    variable=manager_match.group(1),
                    assignment_line=_line(text, assignment[2]),
                ),
            )

        for symbol, symbol_node in symbol_nodes:
            if symbol.kind != "class":
                continue
            match = _MANAGER_CLASS.fullmatch(symbol.name)
            if match is None:
                continue
            class_text = "\n".join(
                text.splitlines()[symbol.start_line - 1 : symbol.end_line]
            )
            if "retrieveEntityName" in class_text or "getEntity" in class_text:
                continue
            entity_name = match.group("name").lower()
            target, status = builder.resolve_entity(entity_name)
            if status == "resolved_exact":
                builder.add_edge(
                    source=symbol_node,
                    target=target,
                    target_ref=entity_name,
                    kind="symbol_entity",
                    resolution="framework_convention",
                    evidence=source_evidence(
                        path=path,
                        source_hash=source_hash,
                        text=text,
                        start_line=symbol.start_line,
                        end_line=symbol.end_line,
                        fact="entity_manager_name_convention",
                        class_name=symbol.name,
                        entity_name=entity_name,
                    ),
                )

        if _is_import(path):
            import_node = builder.add_node(
                _node("import_surface", path, repository=builder.repository)
            )
            for match in _IMPORT_INCLUDE.finditer(text):
                included = match.group(2)
                resolution, resolved_path = _resolve_import_include(path, included, files)
                target = None
                if resolved_path is not None:
                    target = builder.add_node(
                        _node(
                            "source_file",
                            resolved_path,
                            repository=builder.repository,
                            source_hash=_sha(files[resolved_path]),
                        )
                    )
                builder.add_edge(
                    source=import_node,
                    target=target,
                    target_ref=included,
                    kind="import_include",
                    resolution=resolution,
                    evidence=source_evidence(
                        path=path,
                        source_hash=source_hash,
                        text=text,
                        start_line=_line(text, match.start()),
                        fact="import_static_include",
                        included_path=included,
                    ),
                )
                if resolution != "resolved_exact":
                    builder.add_diagnostic(
                        path,
                        f"import_include_{resolution}",
                        f"Static import include could not be resolved uniquely: {included}",
                        _line(text, match.start()),
                    )
            for match in references:
                builder.add_entity_edge(
                    source=import_node,
                    entity_name=match.group(2),
                    kind="import_entity",
                    resolution="explicit_source",
                    evidence=source_evidence(
                        path=path,
                        source_hash=source_hash,
                        text=text,
                        start_line=_line(text, match.start()),
                        fact="import_literal_entity_reference",
                        expression=match.group(0),
                    ),
                )
            if _FLATFILE_RULE.search(text):
                builder.add_diagnostic(
                    path,
                    "flatfile_rule_owner_unresolved",
                    "Flat-file validation rules were found without an owning entity unless another explicit bridge resolves it.",
                    _line(text, _FLATFILE_RULE.search(text).start()),
                )


def build_semantic_index_from_files(
    files: Mapping[str, str], *, repository: str, revision: str
) -> dict[str, Any]:
    """Build from already captured committed bytes; useful for deterministic replay."""

    builder = _Builder(repository, revision)
    _process_entities(builder, files)
    _process_openapi(builder, files)
    _process_php_and_imports(builder, files)
    _process_actionui(builder, files)
    _process_nextgen(builder, files)
    _process_xml_inventory(builder, files)
    return builder.finish()


def _git(repo_root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_root), *arguments],
        capture_output=True,
        check=False,
        text=True,
        timeout=120,
    )
    if result.returncode:
        raise SemanticIndexError(result.stderr.strip() or "git command failed")
    return result.stdout


def _git_files(repo_root: Path, revision: str) -> dict[str, str]:
    resolved = _git(
        repo_root, "rev-parse", "--verify", f"{revision}^{{commit}}"
    ).strip()
    if resolved != revision:
        raise SemanticIndexError(
            "requested revision is not the resolved committed revision"
        )
    paths = _git(repo_root, "ls-tree", "-r", "--name-only", revision).splitlines()
    selected = [
        path
        for path in paths
        if path.lower().endswith(".ent")
        or path.lower().startswith("app/source/openapispec/")
        or path.lower().endswith("_form.xml")
        or path.lower().endswith(".xml")
        or _is_nextgen(path)
        or _is_import(path)
        or _is_php(path)
    ]
    selected_paths = set(selected)
    files: dict[str, str] = {}
    process = subprocess.Popen(
        ["git", "-C", str(repo_root), "archive", "--format=tar", revision],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdout is not None
    try:
        with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
            for member in archive:
                if not member.isfile() or member.name not in selected_paths:
                    continue
                handle = archive.extractfile(member)
                if handle is not None:
                    files[member.name] = handle.read().decode("utf-8", errors="replace")
    finally:
        process.stdout.close()
    stderr = (
        process.stderr.read().decode("utf-8", errors="replace")
        if process.stderr
        else ""
    )
    return_code = process.wait()
    if return_code:
        raise SemanticIndexError(stderr.strip() or "git archive failed")
    return files


def build_semantic_index(
    repo_root: str | Path,
    *,
    repository: str,
    revision: str,
    output: str | Path | None = None,
) -> dict[str, Any]:
    """Build only from committed Git blobs at ``revision`` and optionally write it."""

    root = Path(repo_root).resolve()
    files = _git_files(root, revision)
    index = build_semantic_index_from_files(
        files, repository=repository, revision=revision
    )
    if output is not None:
        write_index(index, output)
    return index
