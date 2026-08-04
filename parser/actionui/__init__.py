"""Deterministic extraction primitives for legacy actionUI sources."""

from .model import (
    ActionUiArtifact,
    DesiredUiSnapshot,
    Diagnostic,
    EntityReferenceFact,
    EventCallFact,
    EventFact,
    FieldFact,
    HandlerResolutionFact,
    IncludeFact,
    JavascriptExtractionResult,
    JavascriptSymbolFact,
    LoaderFact,
    LoaderMethodFact,
    ResolvedLoaderFact,
    ScriptDependencyFact,
)
from .xml_extractor import ActionUiXmlExtractionResult, extract_actionui_xml_facts

__all__ = [
    "ActionUiArtifact",
    "ActionUiXmlExtractionResult",
    "DesiredUiSnapshot",
    "Diagnostic",
    "EntityReferenceFact",
    "EventCallFact",
    "EventFact",
    "FieldFact",
    "HandlerResolutionFact",
    "IncludeFact",
    "JavascriptExtractionResult",
    "JavascriptSymbolFact",
    "LoaderFact",
    "LoaderMethodFact",
    "ResolvedLoaderFact",
    "ScriptDependencyFact",
    "extract_actionui_xml_facts",
]
