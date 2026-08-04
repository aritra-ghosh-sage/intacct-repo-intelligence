"""Immutable, source-provenanced facts shared by actionUI extraction stages."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    source_file: str
    start_line: int
    end_line: int
    severity: str = "warning"
    evidence: str | None = None


@dataclass(frozen=True)
class EntityReferenceFact:
    entity_name: str
    source_file: str
    reference_kind: str
    start_line: int
    end_line: int
    evidence: str


@dataclass(frozen=True)
class IncludeFact:
    source_file: str
    included_path: str
    start_line: int
    end_line: int
    evidence: str


@dataclass(frozen=True)
class FieldFact:
    source_file: str
    field_name: str
    field_path: str | None
    start_line: int
    end_line: int
    evidence: str


@dataclass(frozen=True)
class EventCallFact:
    source_file: str
    event_name: str
    callable_name: str
    start_line: int
    end_line: int
    evidence: str


@dataclass(frozen=True)
class EventFact:
    source_file: str
    event_name: str
    start_line: int
    end_line: int
    evidence: str


@dataclass(frozen=True)
class ActionUiArtifact:
    source_file: str
    artifact_kind: str
    start_line: int
    end_line: int
    evidence: str


@dataclass(frozen=True)
class LoaderFact:
    """A statically proven actionUI loader value or method delegation."""

    source_file: str
    class_name: str
    method_name: str
    loader_kind: str
    value_kind: str
    value: str
    start_line: int
    end_line: int
    evidence: str


@dataclass(frozen=True)
class LoaderMethodFact:
    """Evidence that a class declares (and therefore overrides) a loader."""

    source_file: str
    class_name: str
    method_name: str
    loader_kind: str
    start_line: int
    end_line: int
    evidence: str


@dataclass(frozen=True)
class ResolvedLoaderFact:
    effective_class: str
    source_fact: LoaderFact
    inheritance_path: tuple[object, ...] = ()


@dataclass(frozen=True)
class ScriptDependencyFact:
    source_file: str
    script_path: str
    dependency_kind: str
    activation_state: str
    start_line: int
    end_line: int
    evidence: str


@dataclass(frozen=True)
class JavascriptSymbolFact:
    source_file: str
    symbol_name: str
    symbol_kind: str
    start_line: int
    end_line: int
    evidence: str
    parent_symbol: str | None = None


@dataclass(frozen=True)
class JavascriptExtractionResult:
    source_file: str
    symbols: tuple[JavascriptSymbolFact, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True)
class HandlerResolutionFact:
    """A dependency-scoped outcome for one XML event handler call."""

    event_call: EventCallFact
    dependency: ScriptDependencyFact | None
    handler_symbol: JavascriptSymbolFact | None
    resolution_status: str
    resolution_reason: str


@dataclass(frozen=True)
class DesiredUiSnapshot:
    """Future synchronization input; tuples keep repeated builds deterministic."""

    artifacts: tuple[ActionUiArtifact, ...] = ()
    entity_references: tuple[EntityReferenceFact, ...] = ()
    includes: tuple[IncludeFact, ...] = ()
    fields: tuple[FieldFact, ...] = ()
    events: tuple[EventFact, ...] = ()
    event_calls: tuple[EventCallFact, ...] = ()
    loaders: tuple[LoaderFact, ...] = ()
    script_dependencies: tuple[ScriptDependencyFact, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
