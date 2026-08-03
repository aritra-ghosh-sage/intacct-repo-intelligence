"""Deterministic extraction primitives for NextGen UI source artifacts."""

from .nextgen import (
    ExplicitEntityMapping,
    NextGenArtifactFact,
    NextGenDiagnostic,
    NextGenEntityReferenceFact,
    NextGenExtractionResult,
    NextGenFamilyFact,
    NextGenSource,
    extract_nextgen_families,
)

__all__ = [
    "ExplicitEntityMapping",
    "NextGenArtifactFact",
    "NextGenDiagnostic",
    "NextGenEntityReferenceFact",
    "NextGenExtractionResult",
    "NextGenFamilyFact",
    "NextGenSource",
    "extract_nextgen_families",
]
