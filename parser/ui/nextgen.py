"""Extract source-backed NextGen UI families without guessing entities.

This module intentionally does not query the catalog or the OpenAPI linker.
It groups only ``uimeta*``, ``viewmeta*``, and ``*.view.yaml`` artifacts by an
object key that is explicit in the YAML source or structurally present in a
uimeta filename.  Entity names enter the result only through supplied,
source-provenanced explicit mapping evidence.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath

import yaml

_UI_META_SUFFIX = re.compile(r"\.uimeta[^.]*\.ya?ml$", re.IGNORECASE)
_VIEW_META_SUFFIX = re.compile(r"\.viewmeta[^.]*\.ya?ml$", re.IGNORECASE)
_VIEW_SUFFIX = re.compile(r"\.view\.ya?ml$", re.IGNORECASE)
_UI_META_FAMILY = re.compile(
    r"^objects\.([^.]+)\.(.+)\.s\d+\.uimeta[^.]*\.ya?ml$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class NextGenSource:
    """One repository-relative YAML input supplied by a later build stage."""

    source_file: str
    text: str


@dataclass(frozen=True)
class ExplicitEntityMapping:
    """A separately extracted mapping from an API object key to an entity."""

    object_key: str
    entity_name: str
    source_file: str
    start_line: int
    end_line: int
    evidence: str


@dataclass(frozen=True)
class NextGenDiagnostic:
    """A source-backed extraction diagnostic.

    Diagnostics whose source cannot be assigned to a NextGen family are still
    useful evidence.  Consumers must retain those separately from canonical UI
    surfaces rather than inventing a family, artifact, or entity association.
    """

    code: str
    message: str
    source_file: str
    start_line: int
    end_line: int
    evidence: str | None = None
    severity: str = "warning"


@dataclass(frozen=True)
class NextGenFamilyFact:
    """One NextGen surface family, keyed by the API object identifier."""

    family_key: str
    source_file: str
    start_line: int
    end_line: int
    evidence: str


@dataclass(frozen=True)
class NextGenArtifactFact:
    family_key: str
    source_file: str
    artifact_kind: str
    start_line: int
    end_line: int
    evidence: str


@dataclass(frozen=True)
class NextGenEntityReferenceFact:
    """An entity mapping resolved only from explicit supplied evidence."""

    family_key: str
    entity_name: str | None
    resolution: str
    source_file: str
    start_line: int
    end_line: int
    evidence: str
    mapping_source_file: str | None = None
    mapping_start_line: int | None = None
    mapping_end_line: int | None = None
    mapping_evidence: str | None = None


@dataclass(frozen=True)
class NextGenExtractionResult:
    families: tuple[NextGenFamilyFact, ...] = ()
    artifacts: tuple[NextGenArtifactFact, ...] = ()
    entity_references: tuple[NextGenEntityReferenceFact, ...] = ()
    diagnostics: tuple[NextGenDiagnostic, ...] = ()


def _artifact_kind(source_file: str) -> str | None:
    name = PurePosixPath(source_file).name
    if _UI_META_SUFFIX.search(name):
        return "uimeta"
    if _VIEW_META_SUFFIX.search(name):
        return "viewmeta"
    if _VIEW_SUFFIX.search(name):
        return "view"
    return None


def _line_for_key(text: str, key: str) -> int:
    match = re.search(rf"(?m)^\s*{re.escape(key)}\s*:", text)
    return text.count("\n", 0, match.start()) + 1 if match else 1


def _normalise_object_key(value: str) -> str | None:
    normalized = value.strip().strip("/")
    if not normalized or "/" not in normalized:
        return None
    return normalized


def _uimeta_filename_family(source_file: str) -> str | None:
    """Read a family key from the documented ``objects.<module>.<name>`` form."""

    match = _UI_META_FAMILY.match(PurePosixPath(source_file).name)
    if not match:
        return None
    module, object_name = match.groups()
    return f"{module}/{object_name}"


def _parse_document(source: NextGenSource) -> tuple[dict[object, object] | None, NextGenDiagnostic | None]:
    try:
        document = yaml.safe_load(source.text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = int(mark.line) + 1 if mark is not None else 1
        return None, NextGenDiagnostic(
            code="nextgen.yaml.invalid",
            message="NextGen YAML could not be parsed.",
            source_file=source.source_file,
            start_line=line,
            end_line=line,
            evidence=str(exc),
            severity="error",
        )
    if not isinstance(document, dict):
        return None, NextGenDiagnostic(
            code="nextgen.yaml.document_not_mapping",
            message="NextGen YAML must contain a mapping document.",
            source_file=source.source_file,
            start_line=1,
            end_line=1,
            severity="error",
        )
    return document, None


def _family_key(
    source: NextGenSource, artifact_kind: str, document: dict[object, object]
) -> tuple[str | None, int, str, NextGenDiagnostic | None]:
    object_value = document.get("object")
    if isinstance(object_value, str):
        object_key = _normalise_object_key(object_value)
        if object_key is not None:
            line = _line_for_key(source.text, "object")
            return object_key, line, object_value, None
        return None, 1, object_value, NextGenDiagnostic(
            code="nextgen.family.invalid_object",
            message="The explicit NextGen object value is not an object key.",
            source_file=source.source_file,
            start_line=_line_for_key(source.text, "object"),
            end_line=_line_for_key(source.text, "object"),
            evidence=object_value,
        )

    if artifact_kind == "uimeta":
        family_key = _uimeta_filename_family(source.source_file)
        if family_key is not None:
            return family_key, 1, PurePosixPath(source.source_file).name, None

    return None, 1, "", NextGenDiagnostic(
        code="nextgen.family.unresolved",
        message=(
            "No explicit object key is available; this artifact cannot be "
            "assigned to a NextGen family."
        ),
        source_file=source.source_file,
        start_line=1,
        end_line=1,
        evidence=PurePosixPath(source.source_file).name,
    )


def _mapping_index(
    mappings: Iterable[ExplicitEntityMapping],
) -> dict[str, tuple[ExplicitEntityMapping, ...]]:
    indexed: dict[str, list[ExplicitEntityMapping]] = defaultdict(list)
    for mapping in mappings:
        object_key = _normalise_object_key(mapping.object_key)
        if object_key is not None:
            indexed[object_key].append(mapping)
    return {
        object_key: tuple(
            sorted(
                values,
                key=lambda value: (
                    value.entity_name,
                    value.source_file,
                    value.start_line,
                    value.end_line,
                    value.evidence,
                ),
            )
        )
        for object_key, values in indexed.items()
    }


def extract_nextgen_families(
    sources: Iterable[NextGenSource],
    *,
    explicit_entity_mappings: Iterable[ExplicitEntityMapping] = (),
) -> NextGenExtractionResult:
    """Extract deterministic NextGen family evidence from YAML artifacts.

    A matching ``ExplicitEntityMapping`` is required before an entity name is
    emitted.  In particular, ``general-ledger/journal-entry`` is not converted
    to ``Journal`` merely because that word appears in its artifact name.
    """

    families: dict[str, NextGenFamilyFact] = {}
    artifacts: list[NextGenArtifactFact] = []
    diagnostics: list[NextGenDiagnostic] = []
    first_artifact_by_family: dict[str, NextGenArtifactFact] = {}

    for source in sorted(sources, key=lambda value: value.source_file):
        artifact_kind = _artifact_kind(source.source_file)
        if artifact_kind is None:
            continue

        document, parse_diagnostic = _parse_document(source)
        if parse_diagnostic is not None:
            diagnostics.append(parse_diagnostic)
            continue
        assert document is not None

        family_key, line, evidence, family_diagnostic = _family_key(
            source, artifact_kind, document
        )
        if family_diagnostic is not None:
            diagnostics.append(family_diagnostic)
            continue
        assert family_key is not None

        artifact = NextGenArtifactFact(
            family_key=family_key,
            source_file=source.source_file,
            artifact_kind=artifact_kind,
            start_line=line,
            end_line=line,
            evidence=evidence,
        )
        artifacts.append(artifact)
        first_artifact_by_family.setdefault(family_key, artifact)
        families.setdefault(
            family_key,
            NextGenFamilyFact(
                family_key=family_key,
                source_file=source.source_file,
                start_line=line,
                end_line=line,
                evidence=evidence,
            ),
        )

    entity_references: list[NextGenEntityReferenceFact] = []
    mappings_by_object = _mapping_index(explicit_entity_mappings)
    for family_key, artifact in sorted(first_artifact_by_family.items()):
        mappings = mappings_by_object.get(family_key, ())
        entity_names = {mapping.entity_name for mapping in mappings}
        if len(entity_names) == 1:
            # Multiple source records may corroborate one entity.  Keep the
            # first sorted record as provenance without manufacturing conflict.
            mapping = mappings[0]
            entity_references.append(
                NextGenEntityReferenceFact(
                    family_key=family_key,
                    entity_name=mapping.entity_name,
                    resolution="explicit_mapping",
                    source_file=artifact.source_file,
                    start_line=artifact.start_line,
                    end_line=artifact.end_line,
                    evidence=artifact.evidence,
                    mapping_source_file=mapping.source_file,
                    mapping_start_line=mapping.start_line,
                    mapping_end_line=mapping.end_line,
                    mapping_evidence=mapping.evidence,
                )
            )
            continue

        entity_references.append(
            NextGenEntityReferenceFact(
                family_key=family_key,
                entity_name=None,
                resolution="unresolved" if not mappings else "ambiguous",
                source_file=artifact.source_file,
                start_line=artifact.start_line,
                end_line=artifact.end_line,
                evidence=artifact.evidence,
            )
        )
        diagnostics.append(
            NextGenDiagnostic(
                code=(
                    "nextgen.entity_mapping.unresolved"
                    if not mappings
                    else "nextgen.entity_mapping.ambiguous"
                ),
                message=(
                    "No explicit entity mapping exists for this NextGen family."
                    if not mappings
                    else "Multiple explicit entity mappings exist for this NextGen family."
                ),
                source_file=artifact.source_file,
                start_line=artifact.start_line,
                end_line=artifact.end_line,
                evidence=family_key,
            )
        )

    return NextGenExtractionResult(
        families=tuple(sorted(families.values(), key=lambda value: value.family_key)),
        artifacts=tuple(
            sorted(
                artifacts,
                key=lambda value: (
                    value.family_key,
                    value.source_file,
                    value.artifact_kind,
                ),
            )
        ),
        entity_references=tuple(
            sorted(
                entity_references,
                key=lambda value: (value.family_key, value.resolution, value.entity_name or ""),
            )
        ),
        diagnostics=tuple(
            sorted(
                diagnostics,
                key=lambda value: (
                    value.source_file,
                    value.start_line,
                    value.code,
                    value.evidence or "",
                ),
            )
        ),
    )
