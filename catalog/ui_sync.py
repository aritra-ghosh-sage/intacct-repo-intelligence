"""Build and atomically synchronize source-provenanced UI catalog evidence.

This module deliberately owns neither extraction nor refresh registration.  It
turns the bounded extraction facts already produced by ``parser`` into a
complete desired snapshot, validates it before a write transaction, and then
synchronizes it without replacing unchanged rows.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from catalog.db import require_foreign_key_integrity
from parser.actionui.javascript_callables import extract_javascript_callables
from parser.actionui.javascript_resolution import resolve_event_handlers
from parser.actionui.loader_resolution import (
    InheritanceEdge,
    build_script_dependencies,
    extract_common_script_dependencies,
    resolve_inherited_loader_facts,
)
from parser.actionui.model import Diagnostic
from parser.actionui.php_loader_extractor import extract_php_loader_facts
from parser.actionui.xml_extractor import extract_actionui_xml_facts
from parser.ui.nextgen import (
    ExplicitEntityMapping,
    NextGenSource,
    extract_nextgen_families,
)

EXTRACTOR_VERSION = "ui-sync-v1"
_OBJECT_SCHEMA = re.compile(r"(?:^|/)objects\.([^.]+)\.(.+)\.s\d+\.schema\.ya?ml$", re.IGNORECASE)


class UiSnapshotError(RuntimeError):
    """A desired snapshot is incomplete or internally inconsistent."""


@dataclass(frozen=True)
class UiRow:
    table: str
    key: tuple[str, ...]
    values: dict[str, Any]


@dataclass
class UiSnapshot:
    rows: dict[str, list[UiRow]] = field(default_factory=lambda: defaultdict(list))
    # A malformed form must not erase its prior evidence.  Synchronization uses
    # these keys to exempt the entire surface from stale-row deletion.
    protected_surface_keys: set[str] = field(default_factory=set)

    def add(self, table: str, key: tuple[str, ...], **values: Any) -> None:
        self.rows[table].append(UiRow(table, key, values))


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _file_rows(conn: sqlite3.Connection, repo_id: int) -> dict[str, sqlite3.Row]:
    return {
        str(row["path"]): row
        for row in conn.execute("SELECT id,path,sha1 FROM files WHERE repo_id=?", (repo_id,))
    }


def _need_file(files: dict[str, sqlite3.Row], path: str) -> sqlite3.Row:
    row = files.get(path)
    if row is None:
        raise UiSnapshotError(f"indexed source file is missing for UI evidence: {path}")
    return row


def _form_path(value: str, source_file: str) -> str | None:
    if not value.endswith(".pxml"):
        return None
    name = value[:-5] + ".xml"
    if "/" not in name:
        return str(PurePosixPath(source_file).parent / name)
    return name.lstrip("/")


def _artifact_key(path: str, role: str) -> str:
    return f"{role}:{path}"


def _key(*values: object) -> tuple[str, ...]:
    return tuple(str(value) for value in values)


def _payload(values: dict[str, Any]) -> str:
    return json.dumps(values, sort_keys=True, separators=(",", ":"), default=str)


def _issue_key(
    code: str,
    source_file: str,
    start_line: int,
    end_line: int,
    evidence: str | None,
    discriminator: str = "",
) -> str:
    digest = hashlib.sha256((evidence or "").encode("utf-8")).hexdigest()[:16]
    return ":".join(
        (code, source_file, str(start_line), str(end_line), discriminator, digest)
    )


def _add_diagnostic_issue(
    snapshot: UiSnapshot,
    *,
    surface_key: str,
    artifact_key: str | None,
    diagnostic: Any,
    event_key: str | None = None,
    dependency_key: str | None = None,
    discriminator: str = "",
) -> None:
    """Persist bounded extractor diagnostics instead of silently dropping them."""

    source_file = str(diagnostic.source_file)
    start_line = int(diagnostic.start_line)
    end_line = int(diagnostic.end_line)
    evidence = diagnostic.evidence
    issue_key = _issue_key(
        str(diagnostic.code), source_file, start_line, end_line, evidence, discriminator
    )
    snapshot.add(
        "ui_resolution_issues",
        _key(surface_key, issue_key),
        surface_key=surface_key,
        artifact_key=artifact_key,
        event_key=event_key,
        dependency_key=dependency_key,
        issue_key=issue_key,
        severity=diagnostic.severity,
        issue_code=diagnostic.code,
        message=diagnostic.message,
        evidence_text=evidence,
    )


def _event_key_for_call(result: Any, event_keys: tuple[str, ...], call: Any) -> str:
    matches = [
        key
        for fact, key in zip(result.events, event_keys, strict=True)
        if fact.event_name == call.event_name
        and fact.start_line <= call.start_line <= fact.end_line
    ]
    if len(matches) != 1:
        raise UiSnapshotError(
            "actionUI event call cannot be associated with exactly one containing event: "
            f"{call.source_file}:{call.start_line} {call.callable_name}"
        )
    return matches[0]


def _validate_snapshot(snapshot: UiSnapshot) -> None:
    """Reject conflicting desired natural keys before any database mutation."""
    for table, rows in snapshot.rows.items():
        seen: dict[tuple[str, ...], str] = {}
        for row in rows:
            payload = _payload(row.values)
            prior = seen.setdefault(row.key, payload)
            if prior != payload:
                raise UiSnapshotError(
                    f"duplicate desired {table} natural key has different payload: {row.key}"
                )


def _entity_occurrences(conn: sqlite3.Connection, repo_id: int) -> dict[str, tuple[int, int]]:
    return {
        str(row["name"]): (int(row["entity_id"]), int(row["occurrence_id"]))
        for row in conn.execute(
            """SELECT entity_nodes.name, entity_nodes.id AS entity_id, entity_occurrences.id AS occurrence_id
               FROM entity_nodes JOIN entity_occurrences ON entity_occurrences.entity_id=entity_nodes.id
               WHERE entity_occurrences.repo_id=?""",
            (repo_id,),
        )
    }


def assemble_ui_snapshot(conn: sqlite3.Connection, *, repo_id: int, repo_root: Path) -> UiSnapshot:
    """Read indexed sources and existing deterministic evidence into one snapshot."""
    from catalog.repository_lifecycle import require_repository_id_extractable

    require_repository_id_extractable(conn, repo_id)
    files = _file_rows(conn, repo_id)
    occurrences = _entity_occurrences(conn, repo_id)
    snapshot = UiSnapshot()
    form_paths = sorted(path for path in files if path.endswith("_form.xml"))
    form_artifacts: dict[str, str] = {}
    xml_results: dict[str, Any] = {}
    event_keys_by_form: dict[str, tuple[str, ...]] = {}
    for path in form_paths:
        source = repo_root / path
        if not source.is_file():
            raise UiSnapshotError(f"source checkout file is missing: {path}")
        result = extract_actionui_xml_facts(source.read_bytes(), path)
        surface_key = f"actionui:{path}"
        artifact_key = _artifact_key(path, "form")
        fatal_diagnostics = [
            diagnostic
            for diagnostic in result.diagnostics
            if diagnostic.severity == "error"
            and diagnostic.code == "actionui.xml.parse_error"
        ]
        if fatal_diagnostics:
            # Retain a previously successful surface verbatim.  For a new
            # malformed source, retain only its source-backed shell and issue.
            snapshot.protected_surface_keys.add(surface_key)
            existing = conn.execute(
                "SELECT 1 FROM ui_surfaces WHERE repo_id=? AND surface_key=?",
                (repo_id, surface_key),
            ).fetchone()
            if existing is None:
                snapshot.add("ui_surfaces", _key(surface_key), surface_key=surface_key, surface_kind="actionui_form", display_name=PurePosixPath(path).stem, source_path=path, source_file_id=int(files[path]["id"]), extractor="actionui_xml", extractor_version=EXTRACTOR_VERSION, source_hash=_hash(source))
                snapshot.add("ui_artifacts", _key(surface_key, artifact_key), surface_key=surface_key, artifact_key=artifact_key, artifact_kind="actionui_form", file_id=int(files[path]["id"]), source_path=path, start_line=1, end_line=1, evidence_text="actionUI form", source_hash=_hash(source), payload_json="{}")
            for diagnostic in fatal_diagnostics:
                _add_diagnostic_issue(
                    snapshot,
                    surface_key=surface_key,
                    artifact_key=artifact_key,
                    diagnostic=diagnostic,
                )
            continue
        xml_results[path] = result
        snapshot.add("ui_surfaces", _key(surface_key), surface_key=surface_key, surface_kind="actionui_form", display_name=PurePosixPath(path).stem, source_path=path, source_file_id=int(files[path]["id"]), extractor="actionui_xml", extractor_version=EXTRACTOR_VERSION, source_hash=_hash(source))
        form_artifacts[path] = artifact_key
        snapshot.add("ui_artifacts", _key(surface_key, artifact_key), surface_key=surface_key, artifact_key=artifact_key, artifact_kind="actionui_form", file_id=int(files[path]["id"]), source_path=path, start_line=1, end_line=1, evidence_text="actionUI form", source_hash=_hash(source), payload_json="{}")
        for ordinal, fact in enumerate(result.fields):
            field_key = f"{fact.start_line}:{fact.field_name}:{fact.field_path or ''}"
            snapshot.add("ui_fields", _key(surface_key, artifact_key, field_key), surface_key=surface_key, artifact_key=artifact_key, field_key=field_key, field_name=fact.field_name, field_path=fact.field_path, label=None, field_type=None, ordinal=ordinal, source_line=fact.start_line, evidence_text=fact.evidence)
        event_keys: list[str] = []
        for ordinal, fact in enumerate(result.events):
            event_key = f"{fact.start_line}:{ordinal}:{fact.event_name}"
            event_keys.append(event_key)
            snapshot.add("ui_events", _key(surface_key, artifact_key, event_key), surface_key=surface_key, artifact_key=artifact_key, event_key=event_key, event_type=fact.event_name, handler_name=None, handler_expression=fact.evidence, source_line=fact.start_line, evidence_text=fact.evidence)
        event_keys_by_form[path] = tuple(event_keys)
        for fact in result.includes:
            target = str(PurePosixPath(path).parent / fact.included_path)
            target_file = files.get(target)
            target_source = repo_root / target
            target_artifact = None
            if target_file is not None and target_source.is_file():
                target_artifact = _artifact_key(target, "actionui_include_fragment")
                snapshot.add("ui_artifacts", _key(surface_key, target_artifact), surface_key=surface_key, artifact_key=target_artifact, artifact_kind="actionui_include_fragment", file_id=int(target_file["id"]), source_path=target, start_line=1, end_line=1, evidence_text="actionUI include target", source_hash=_hash(target_source), payload_json="{}")
            else:
                code = "actionui.include.not_indexed" if target_file is None else "actionui.include.checkout_missing"
                _add_diagnostic_issue(snapshot, surface_key=surface_key, artifact_key=artifact_key, diagnostic=Diagnostic(code=code, message="Included actionUI fragment is not both indexed and present in the checkout.", source_file=path, start_line=fact.start_line, end_line=fact.end_line, severity="warning", evidence=fact.evidence), discriminator=target)
            include_key = f"{fact.start_line}:{fact.included_path}"
            snapshot.add("ui_artifact_includes", _key(surface_key, artifact_key, include_key), surface_key=surface_key, artifact_key=artifact_key, target_artifact_key=target_artifact, include_key=include_key, raw_include_path=fact.included_path, resolved_path=target if target_artifact else None, resolution_status="resolved" if target_artifact else "unresolved", source_line=fact.start_line, evidence_text=fact.evidence)
        for diagnostic in result.diagnostics:
            _add_diagnostic_issue(snapshot, surface_key=surface_key, artifact_key=artifact_key, diagnostic=diagnostic)

    # Editor ownership and FormEditor convention are both explicit catalog/source evidence.
    editor_rows = conn.execute(
        """SELECT entity_nodes.name AS entity_name, files.path
           FROM entity_mappings JOIN entity_nodes ON entity_nodes.id=entity_mappings.entity_id
           JOIN files ON files.id=entity_mappings.file_id
           WHERE entity_mappings.repo_id=? AND entity_mappings.mapping_type='editor'""",
        (repo_id,),
    ).fetchall()
    loaders = []
    loader_diagnostics_by_file: dict[str, list[Any]] = defaultdict(list)
    for row in editor_rows:
        path = str(row["path"])
        source = repo_root / path
        if source.is_file():
            extraction = extract_php_loader_facts(source.read_bytes(), path)
            loaders.extend(extraction.loaders)
            loader_diagnostics_by_file[path].extend(extraction.diagnostics)
    edges = tuple(InheritanceEdge(str(row["source_name"]), str(row["target_name"]), str(row["file_path"]), str(row["evidence"] or "")) for row in conn.execute("SELECT source_name,target_name,file_path,evidence FROM relationships WHERE repo_id=? AND relationship_type='INHERITS'", (repo_id,)))
    resolved = resolve_inherited_loader_facts(tuple(loaders), edges, form_editor_source_file="app/source/core/FormEditor.cls")
    by_class: dict[str, list[Any]] = defaultdict(list)
    for fact in resolved.loaders:
        by_class[fact.class_name].append(fact)
    editor_by_path = {str(row["path"]): str(row["entity_name"]) for row in editor_rows}
    for editor_path, entity_name in sorted(editor_by_path.items()):
        entity = occurrences.get(entity_name)
        if entity is None:
            continue
        class_name = PurePosixPath(editor_path).stem
        candidates = {_form_path(fact.value, editor_path) for fact in by_class.get(class_name, [])}
        candidates.discard(None)
        inherits_form_convention = any(
            fact.class_name == class_name
            and fact.loader_kind == "form"
            and fact.value_kind == "direct_call"
            and fact.value.startswith("parent::getMetadataKeyName")
            for fact in loaders
        ) and any(fact.value_kind == "form_editor_convention" for fact in resolved.loaders)
        if inherits_form_convention:
            directory = str(PurePosixPath(editor_path).parent)
            candidates.update({f"{directory}/{entity_name.lower()}_form.xml", f"{directory}/{entity_name.lower()}_2012_form.xml"})
        for form_path in sorted(path for path in candidates if path in form_artifacts):
            surface_key, artifact_key = f"actionui:{form_path}", form_artifacts[form_path]
            snapshot.add("ui_entity_references", _key(surface_key, entity_name, editor_path, "editor"), surface_key=surface_key, entity_id=entity[0], entity_occurrence_id=entity[1], artifact_key=artifact_key, reference_kind="editor", confidence=1.0, evidence_text=f"{editor_path} editor mapping and FormEditor form convention", source_line=None)
            editor_file = _need_file(files, editor_path)
            editor_artifact = _artifact_key(editor_path, "editor_loader")
            snapshot.add("ui_artifacts", _key(surface_key, editor_artifact), surface_key=surface_key, artifact_key=editor_artifact, artifact_kind="php_loader", file_id=int(editor_file["id"]), source_path=editor_path, start_line=None, end_line=None, evidence_text="editor loader source", source_hash=_hash(repo_root / editor_path), payload_json="{}")
            for diagnostic in loader_diagnostics_by_file[editor_path]:
                _add_diagnostic_issue(
                    snapshot,
                    surface_key=surface_key,
                    artifact_key=editor_artifact,
                    diagnostic=diagnostic,
                )
            for diagnostic in resolved.diagnostics:
                if diagnostic.source_file == editor_path:
                    _add_diagnostic_issue(
                        snapshot,
                        surface_key=surface_key,
                        artifact_key=editor_artifact,
                        diagnostic=diagnostic,
                    )
            editor_scripts = build_script_dependencies(
                tuple(by_class.get(class_name, [])), repo_root=repo_root
            )
            common_path = "app/source/common/html_header.inc"
            common_file = files.get(common_path) if inherits_form_convention else None
            common_source = repo_root / common_path
            common_artifact: str | None = None
            common_scripts = None
            if inherits_form_convention and common_file is not None and common_source.is_file():
                common_artifact = _artifact_key(common_path, "common_include")
                snapshot.add("ui_artifacts", _key(surface_key, common_artifact), surface_key=surface_key, artifact_key=common_artifact, artifact_kind="common_include", file_id=int(common_file["id"]), source_path=common_path, start_line=None, end_line=None, evidence_text="FormEditor common static script helper", source_hash=_hash(common_source), payload_json="{}")
                common_scripts = extract_common_script_dependencies(
                    common_source.read_bytes(), common_path, repo_root
                )
            elif inherits_form_convention:
                code = "actionui.common_helper.not_indexed" if common_file is None else "actionui.common_helper.checkout_missing"
                _add_diagnostic_issue(snapshot, surface_key=surface_key, artifact_key=editor_artifact, diagnostic=Diagnostic(code=code, message="FormEditor common script helper is not both indexed and present in the checkout.", source_file=editor_path, start_line=1, end_line=1, severity="warning", evidence=common_path))
            for diagnostic in editor_scripts.diagnostics:
                _add_diagnostic_issue(
                    snapshot,
                    surface_key=surface_key,
                    artifact_key=editor_artifact,
                    diagnostic=diagnostic,
                )
            if common_scripts is not None:
                for diagnostic in common_scripts.diagnostics:
                    _add_diagnostic_issue(snapshot, surface_key=surface_key, artifact_key=common_artifact, diagnostic=diagnostic)
            dependencies: list[tuple[Any, str]] = [
                (dependency, editor_artifact) for dependency in editor_scripts.dependencies
            ]
            if common_scripts is not None and common_artifact is not None:
                dependencies.extend((dependency, common_artifact) for dependency in common_scripts.dependencies)
            deduped: dict[tuple[str, int, str, str], tuple[Any, str]] = {}
            for dependency, provenance_artifact in dependencies:
                deduped.setdefault((dependency.source_file, dependency.start_line, dependency.script_path, dependency.activation_state), (dependency, provenance_artifact))
            js_results = []
            resolved_dependencies = []
            for dep, provenance_artifact in deduped.values():
                script_file = files.get(dep.script_path)
                dependency_key = f"{dep.source_file}:{dep.start_line}:{dep.script_path}:{dep.activation_state}"
                script = repo_root / dep.script_path
                if script_file is None or not script.is_file():
                    code = "actionui.script.not_indexed" if script_file is None else "actionui.script.checkout_missing"
                    _add_diagnostic_issue(snapshot, surface_key=surface_key, artifact_key=provenance_artifact, diagnostic=Diagnostic(code=code, message="Statically loaded script is not both indexed and present in the checkout.", source_file=dep.source_file, start_line=dep.start_line, end_line=dep.end_line, severity="warning", evidence=dep.evidence), discriminator=dependency_key)
                    continue
                javascript = extract_javascript_callables(script.read_bytes(), dep.script_path)
                js_results.append(javascript)
                resolved_dependencies.append(dep)
                snapshot.add("ui_script_dependencies", _key(surface_key, dependency_key), surface_key=surface_key, artifact_key=provenance_artifact, dependency_key=dependency_key, script_path=dep.script_path, target_file_id=int(script_file["id"]), load_scope=dep.activation_state, resolution_status="resolved", evidence_text=dep.evidence, source_line=dep.start_line)
                for diagnostic in javascript.diagnostics:
                    _add_diagnostic_issue(
                        snapshot,
                        surface_key=surface_key,
                        artifact_key=editor_artifact,
                        dependency_key=dependency_key,
                        diagnostic=diagnostic,
                        discriminator=dependency_key,
                    )
            calls = xml_results[form_path].event_calls
            for ordinal, outcome in enumerate(
                resolve_event_handlers(calls, tuple(resolved_dependencies), tuple(js_results))
            ):
                event_key = _event_key_for_call(
                    xml_results[form_path], event_keys_by_form[form_path], outcome.event_call
                )
                dependency_key = (
                    f"{outcome.dependency.source_file}:{outcome.dependency.start_line}:"
                    f"{outcome.dependency.script_path}:{outcome.dependency.activation_state}"
                    if outcome.dependency is not None
                    else None
                )
                status, reason = outcome.resolution_status, outcome.resolution_reason
                handler = outcome.handler_symbol
                if status == "resolved":
                    target_file = files.get(outcome.dependency.script_path) if outcome.dependency else None
                    exact = () if target_file is None or handler is None or handler.source_file != outcome.dependency.script_path else conn.execute("SELECT symbols.id FROM symbols WHERE symbols.file_id=? AND symbols.name=? AND symbols.start_line=?", (int(target_file["id"]), handler.symbol_name, handler.start_line)).fetchall()
                    if len(exact) != 1:
                        status, reason, handler = "unresolved", "catalog_symbol_not_found_for_dependency", None
                call_key = ":".join((str(outcome.event_call.start_line), str(outcome.event_call.end_line), str(ordinal), outcome.event_call.callable_name, status, reason))
                snapshot.add("ui_event_calls", _key(surface_key, event_key, dependency_key or "", call_key), surface_key=surface_key, event_key=event_key, dependency_key=dependency_key, call_key=call_key, handler_name=outcome.event_call.callable_name, handler_symbol_name=handler.symbol_name if handler else None, handler_symbol_line=handler.start_line if handler else None, handler_symbol_source_file=handler.source_file if handler else None, resolution_status=status, resolution_reason=reason, evidence_text=outcome.event_call.evidence)
                if status != "resolved":
                    _add_diagnostic_issue(
                        snapshot,
                        surface_key=surface_key,
                        artifact_key=editor_artifact,
                        event_key=event_key,
                        dependency_key=dependency_key,
                        diagnostic=Diagnostic(
                            code=f"actionui.handler.{status}",
                            message=reason,
                            source_file=outcome.event_call.source_file,
                            start_line=outcome.event_call.start_line,
                            end_line=outcome.event_call.end_line,
                            severity="warning",
                            evidence=outcome.event_call.evidence,
                        ),
                        discriminator=str(ordinal),
                    )

    # NextGen names enter only through a linked schema mapping, never a slug guess.
    nextgen_sources = [NextGenSource(path, (repo_root / path).read_text(errors="replace")) for path in sorted(files) if (".uimeta" in path or ".viewmeta" in path or path.endswith(".view.yaml")) and (repo_root / path).is_file()]
    mappings = []
    for row in conn.execute("""SELECT oi.file_path, entity_nodes.name
                               FROM openapispec_index oi JOIN entity_mappings em ON em.repo_id=oi.repo_id AND em.file_id=oi.file_id
                               JOIN entity_nodes ON entity_nodes.id=em.entity_id
                               WHERE oi.repo_id=? AND oi.kind='schema' AND em.mapping_type='openapispec_schema'""", (repo_id,)):
        source_file = str(row["file_path"])
        match = _OBJECT_SCHEMA.search(source_file)
        if match:
            mappings.append(ExplicitEntityMapping(f"{match.group(1)}/{match.group(2)}", str(row["name"]), source_file, 1, 1, "openapispec_schema entity mapping"))
    result = extract_nextgen_families(nextgen_sources, explicit_entity_mappings=mappings)
    for family in result.families:
        key = f"nextgen:{family.family_key}"
        source_file = _need_file(files, family.source_file)
        snapshot.add("ui_surfaces", _key(key), surface_key=key, surface_kind="nextgen", display_name=family.family_key, source_path=family.source_file, source_file_id=int(source_file["id"]), extractor="nextgen", extractor_version=EXTRACTOR_VERSION, source_hash=_hash(repo_root / family.source_file))
    for artifact in result.artifacts:
        key = f"nextgen:{artifact.family_key}"; artifact_key = _artifact_key(artifact.source_file, artifact.artifact_kind); source_file = _need_file(files, artifact.source_file)
        snapshot.add("ui_artifacts", _key(key, artifact_key), surface_key=key, artifact_key=artifact_key, artifact_kind=artifact.artifact_kind, file_id=int(source_file["id"]), source_path=artifact.source_file, start_line=artifact.start_line, end_line=artifact.end_line, evidence_text=artifact.evidence, source_hash=_hash(repo_root / artifact.source_file), payload_json="{}")
    for reference in result.entity_references:
        key = f"nextgen:{reference.family_key}"; artifact = next((item for item in result.artifacts if item.family_key == reference.family_key), None)
        if reference.entity_name and artifact and reference.entity_name in occurrences:
            entity_id, occurrence_id = occurrences[reference.entity_name]; artifact_key = _artifact_key(artifact.source_file, artifact.artifact_kind)
            snapshot.add("ui_entity_references", _key(key, reference.entity_name, reference.mapping_source_file or reference.source_file, "explicit_mapping"), surface_key=key, entity_id=entity_id, entity_occurrence_id=occurrence_id, artifact_key=artifact_key, reference_kind="explicit_mapping", confidence=1.0, evidence_text=reference.mapping_evidence or reference.evidence, source_line=reference.mapping_start_line)
    artifacts_by_source = {artifact.source_file: artifact for artifact in result.artifacts}
    for diagnostic in result.diagnostics:
        artifact = artifacts_by_source.get(diagnostic.source_file)
        if artifact is None:
            raise UiSnapshotError(
                "NextGen diagnostic cannot be attached to a source-backed UI surface: "
                f"{diagnostic.source_file}:{diagnostic.start_line} {diagnostic.code}"
            )
        _add_diagnostic_issue(
            snapshot,
            surface_key=f"nextgen:{artifact.family_key}",
            artifact_key=_artifact_key(artifact.source_file, artifact.artifact_kind),
            diagnostic=diagnostic,
        )
    return snapshot


_ORDER = ("ui_surfaces", "ui_artifacts", "ui_entity_references", "ui_artifact_includes", "ui_fields", "ui_events", "ui_script_dependencies", "ui_event_calls", "ui_resolution_issues")
_DELETE_ORDER = tuple(reversed(_ORDER))


def synchronize_ui_snapshot(conn: sqlite3.Connection, *, repo_id: int, snapshot: UiSnapshot) -> None:
    """Synchronize a prebuilt snapshot atomically, preserving matching row ids."""
    from catalog.repository_lifecycle import require_repository_id_extractable

    require_repository_id_extractable(conn, repo_id)
    _validate_snapshot(snapshot)
    if conn.in_transaction:
        raise UiSnapshotError("UI synchronization requires a connection without an active transaction")
    try:
        conn.execute("BEGIN IMMEDIATE")
        def one(sql: str, params: tuple[Any, ...]) -> int:
            row = conn.execute(sql, params).fetchone()
            if row is None:
                raise UiSnapshotError("desired UI parent was not synchronized")
            return int(row[0])

        def surface_id(surface_key: str) -> int:
            return one("SELECT id FROM ui_surfaces WHERE repo_id=? AND surface_key=?", (repo_id, surface_key))

        def artifact_id(surface_key: str, artifact_key: str) -> int:
            return one(
                """SELECT ui_artifacts.id FROM ui_artifacts JOIN ui_surfaces ON ui_surfaces.id=ui_artifacts.surface_id
                   WHERE ui_artifacts.repo_id=? AND ui_surfaces.surface_key=? AND ui_artifacts.artifact_key=?""",
                (repo_id, surface_key, artifact_key),
            )

        def event_id(surface_key: str, event_key: str) -> int:
            return one(
                """SELECT ui_events.id FROM ui_events JOIN ui_artifacts ON ui_artifacts.id=ui_events.artifact_id
                   JOIN ui_surfaces ON ui_surfaces.id=ui_artifacts.surface_id
                   WHERE ui_events.repo_id=? AND ui_surfaces.surface_key=? AND ui_events.event_key=?""",
                (repo_id, surface_key, event_key),
            )

        def dependency_id(surface_key: str, dependency_key: str) -> int:
            return one(
                """SELECT ui_script_dependencies.id FROM ui_script_dependencies JOIN ui_surfaces
                   ON ui_surfaces.id=ui_script_dependencies.surface_id
                   WHERE ui_script_dependencies.repo_id=? AND ui_surfaces.surface_key=? AND ui_script_dependencies.dependency_key=?""",
                (repo_id, surface_key, dependency_key),
            )

        def protected_surface_ids() -> tuple[int, ...]:
            if not snapshot.protected_surface_keys:
                return ()
            placeholders = ",".join("?" for _ in snapshot.protected_surface_keys)
            return tuple(
                int(row[0])
                for row in conn.execute(
                    f"SELECT id FROM ui_surfaces WHERE repo_id=? AND surface_key IN ({placeholders})",
                    (repo_id, *sorted(snapshot.protected_surface_keys)),
                )
            )

        conflicts = {
            "ui_surfaces": ("surface_key",),
            "ui_artifacts": ("surface_id", "artifact_key"),
            "ui_entity_references": ("surface_id", "entity_occurrence_id", "evidence_artifact_id", "reference_kind"),
            "ui_artifact_includes": ("source_artifact_id", "include_key"),
            "ui_fields": ("artifact_id", "field_key"),
            "ui_events": ("artifact_id", "event_key"),
            "ui_script_dependencies": ("surface_id", "source_artifact_id", "dependency_key"),
            "ui_event_calls": ("event_id", "dependency_id", "call_key"),
            "ui_resolution_issues": ("surface_id", "issue_key"),
        }
        for table in _ORDER:
            desired = sorted(snapshot.rows.get(table, ()), key=lambda row: row.key)
            conn.execute(f"CREATE TEMP TABLE IF NOT EXISTS desired_{table}(natural_key TEXT PRIMARY KEY, id INTEGER NOT NULL)")
            conn.execute(f"DELETE FROM desired_{table}")
            for row in desired:
                values = dict(row.values); values["repo_id"] = repo_id
                source_surface_key = str(values.get("surface_key", ""))
                if table == "ui_surfaces":
                    source_surface_key = ""
                else:
                    values.pop("surface_key", None)
                    if table in {"ui_artifacts", "ui_entity_references", "ui_script_dependencies", "ui_resolution_issues"}:
                        values["surface_id"] = surface_id(source_surface_key)
                if "artifact_key" in values and table != "ui_artifacts":
                    artifact_key_value = str(values.pop("artifact_key"))
                    artifact_column = "evidence_artifact_id" if table == "ui_entity_references" else "source_artifact_id" if table in {"ui_artifact_includes", "ui_script_dependencies"} else "artifact_id"
                    values[artifact_column] = artifact_id(source_surface_key, artifact_key_value)
                if "target_artifact_key" in values:
                    target_key = values.pop("target_artifact_key")
                    values["target_artifact_id"] = artifact_id(source_surface_key, str(target_key)) if target_key else None
                if "event_key" in values and table in {"ui_event_calls", "ui_resolution_issues"}:
                    event_key_value = values.pop("event_key")
                    values["event_id"] = event_id(source_surface_key, str(event_key_value)) if event_key_value else None
                if "dependency_key" in values and table in {"ui_event_calls", "ui_resolution_issues"}:
                    dependency_key_value = values.pop("dependency_key")
                    values["dependency_id"] = dependency_id(source_surface_key, str(dependency_key_value)) if dependency_key_value else None
                if table == "ui_event_calls" and values.get("handler_symbol_name"):
                    name, line, source_file = values.pop("handler_symbol_name"), values.pop("handler_symbol_line"), values.pop("handler_symbol_source_file")
                    found = conn.execute(
                        """SELECT symbols.id FROM symbols JOIN files ON files.id=symbols.file_id
                           JOIN ui_script_dependencies dependency ON dependency.target_file_id=files.id
                           WHERE dependency.id=? AND files.repo_id=? AND files.path=?
                             AND symbols.name=? AND symbols.start_line=?""",
                        (values["dependency_id"], repo_id, source_file, name, line),
                    ).fetchall()
                    if len(found) != 1:
                        values["handler_symbol_id"] = None
                        values["resolution_status"] = "unresolved"
                        values["resolution_reason"] = "catalog_symbol_not_found_for_dependency"
                    else:
                        values["handler_symbol_id"] = int(found[0][0])
                else:
                    values.pop("handler_symbol_name", None); values.pop("handler_symbol_line", None); values.pop("handler_symbol_source_file", None)
                columns = sorted(values); placeholders = ",".join("?" for _ in columns)
                updates = ",".join(f"{column}=excluded.{column}" for column in columns if column not in {"repo_id"})
                if table == "ui_event_calls" and values["dependency_id"] is None:
                    conn.execute(
                        f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders}) "
                        "ON CONFLICT(repo_id,event_id,call_key) WHERE dependency_id IS NULL "
                        f"DO UPDATE SET {updates}",
                        tuple(values[column] for column in columns),
                    )
                    row_id = one(
                        "SELECT id FROM ui_event_calls "
                        "WHERE repo_id=? AND event_id=? AND dependency_id IS NULL AND call_key=?",
                        (repo_id, values["event_id"], values["call_key"]),
                    )
                else:
                    conflict = ",".join(conflicts[table])
                    conn.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders}) ON CONFLICT(repo_id,{conflict}) DO UPDATE SET {updates}", tuple(values[column] for column in columns))
                    where = " AND ".join(f"{column}=?" for column in conflicts[table])
                    row_id = one(f"SELECT id FROM {table} WHERE repo_id=? AND {where}", (repo_id, *(values[column] for column in conflicts[table])))
                conn.execute(f"INSERT INTO desired_{table}(natural_key,id) VALUES (?,?)", ("\x1f".join(row.key), row_id))
        protected_ids = protected_surface_ids()
        protected = {} if not protected_ids else {
            "ui_surfaces": "id NOT IN ({})",
            "ui_artifacts": "surface_id NOT IN ({})",
            "ui_entity_references": "surface_id NOT IN ({})",
            "ui_artifact_includes": "source_artifact_id IN (SELECT id FROM ui_artifacts WHERE surface_id NOT IN ({}))",
            "ui_fields": "artifact_id IN (SELECT id FROM ui_artifacts WHERE surface_id NOT IN ({}))",
            "ui_events": "artifact_id IN (SELECT id FROM ui_artifacts WHERE surface_id NOT IN ({}))",
            "ui_script_dependencies": "surface_id NOT IN ({})",
            "ui_event_calls": "event_id IN (SELECT event.id FROM ui_events event JOIN ui_artifacts artifact ON artifact.id=event.artifact_id WHERE artifact.surface_id NOT IN ({}))",
            "ui_resolution_issues": "surface_id NOT IN ({})",
        }
        for table in _DELETE_ORDER:
            clause = " AND " + protected[table].format(",".join("?" for _ in protected_ids)) if protected else ""
            conn.execute(f"DELETE FROM {table} WHERE repo_id=? AND id NOT IN (SELECT id FROM desired_{table}){clause}", (repo_id, *protected_ids))
        require_foreign_key_integrity(conn, context="UI snapshot synchronization")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
