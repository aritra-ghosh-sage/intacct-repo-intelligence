"""Small, source-backed parser for the business semantics carried by ``.ent`` files."""

from __future__ import annotations

import re
from typing import Any

from greenfield.semantic_contract import source_evidence

_IDENTITY = re.compile(r"\$kSchemas\s*\[\s*(['\"])([^'\"]+)\1\s*\]")
_METADATA = re.compile(r"['\"](module|table|view|dummy)['\"]\s*=>\s*(['\"])([^'\"]+)\2")
_DUMMY_METADATA = re.compile(
    r"['\"]dummy['\"]\s*=>\s*(true|false|null)", re.IGNORECASE
)
_LITERAL_VALUE = re.compile(
    r"['\"]([^'\"]+)['\"]\s*=>\s*(['\"])([^'\"]*)\2"
)
_ARRAY_KEY = re.compile(
    r"(?m)^\s*['\"]([^'\"]+)['\"]\s*=>\s*array\s*\("
)
_ENTITY_REF = re.compile(r"['\"]entity['\"]\s*=>\s*(['\"])([^'\"]+)\1")
_PARENT_REF = re.compile(r"['\"]parententity['\"]\s*=>\s*(['\"])([^'\"]+)\1")
_INCLUDE = re.compile(
    r"\b(?:include|require(?:_once)?)\s*\(?\s*(['\"])([^'\"]+\.ent)\1"
)
_INHERIT = re.compile(r"\binheritEnts\s*\(([^)]*)\)")
_SECTION = re.compile(r"(?m)^\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\s*=>\s*array")
_TOP_SECTION = re.compile(r"(?m)^ {4}['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\s*=>\s*array")
_FACT_SECTIONS = frozenset(
    {
        "children",
        "fieldinfo",
        "importOrder",
        "nexus",
        "object",
        "ownedobjects",
        "publish",
        "schema",
    }
)
_FIELD_SECTIONS = frozenset({"schema"})


def _line(text: str, offset: int) -> int:
    return text.count("\n", 0, max(0, offset)) + 1


def _section_before(text: str, offset: int) -> str:
    sections = [match for match in _TOP_SECTION.finditer(text, 0, offset)]
    if not sections:
        sections = [match for match in _SECTION.finditer(text, 0, offset)]
    return sections[-1].group(1) if sections else "root"


def _diagnostic(
    path: str, text: str, code: str, message: str, offset: int
) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "source_path": path,
        "start_line": _line(text, offset),
        "end_line": _line(text, offset),
        "severity": "warning",
    }


def extract_ent_facts(text: str, path: str) -> dict[str, Any]:
    source_hash = __import__("hashlib").sha256(text.encode("utf-8")).hexdigest()
    diagnostics: list[dict[str, Any]] = []
    identity_match = _IDENTITY.search(text)
    if identity_match is None:
        diagnostics.append(
            _diagnostic(
                path,
                text,
                "entity_identity_missing",
                "No literal $kSchemas entity identity was found.",
                0,
            )
        )
        identity = None
    else:
        identity = identity_match.group(2).strip()

    metadata: dict[str, str] = {}
    for match in _METADATA.finditer(text):
        metadata.setdefault(match.group(1), match.group(3))
    for match in _DUMMY_METADATA.finditer(text):
        metadata.setdefault("dummy", match.group(1).lower())

    literal_facts: list[dict[str, Any]] = []
    field_facts: list[dict[str, Any]] = []
    for match in _LITERAL_VALUE.finditer(text):
        section = _section_before(text, match.start())
        if section not in _FACT_SECTIONS:
            continue
        line = _line(text, match.start())
        fact = {
            "section": section,
            "key": match.group(1),
            "value": match.group(3),
            "evidence": source_evidence(
                path=path,
                source_hash=source_hash,
                text=text,
                start_line=line,
                fact="ent_literal_value",
                section=section,
                key=match.group(1),
                value=match.group(3),
            ),
        }
        literal_facts.append(fact)
        if section in _FIELD_SECTIONS:
            field_facts.append(fact)

    array_facts: list[dict[str, Any]] = []
    for match in _ARRAY_KEY.finditer(text):
        line = _line(text, match.start())
        section = _section_before(text, match.start())
        if match.group(1) not in _FACT_SECTIONS and section not in _FACT_SECTIONS:
            continue
        array_facts.append(
            {
                "section": section,
                "key": match.group(1),
                "evidence": source_evidence(
                    path=path,
                    source_hash=source_hash,
                    text=text,
                    start_line=line,
                    fact="ent_array_key",
                    section=section,
                    key=match.group(1),
                ),
            }
        )

    relationships: list[dict[str, Any]] = []
    for match in list(_ENTITY_REF.finditer(text)) + list(_PARENT_REF.finditer(text)):
        relation_kind = (
            "parent_entity"
            if match.re.pattern == _PARENT_REF.pattern
            else "entity_reference"
        )
        section = _section_before(text, match.start())
        if section in {"children", "ownedobjects", "nexus"}:
            relation_kind = f"{section}_entity"
        line = _line(text, match.start())
        relationships.append(
            {
                "target": match.group(2).strip(),
                "kind": relation_kind,
                "evidence": source_evidence(
                    path=path,
                    source_hash=source_hash,
                    text=text,
                    start_line=line,
                    fact="ent_entity_reference",
                    target=match.group(2).strip(),
                    section=section,
                ),
            }
        )

    includes: list[dict[str, Any]] = []
    for match in _INCLUDE.finditer(text):
        line = _line(text, match.start())
        includes.append(
            {
                "target": match.group(2),
                "resolution": "explicit_source",
                "evidence": source_evidence(
                    path=path,
                    source_hash=source_hash,
                    text=text,
                    start_line=line,
                    fact="ent_include",
                    target=match.group(2),
                ),
            }
        )

    for match in _INHERIT.finditer(text):
        if not re.search(r"['\"][A-Za-z_][A-Za-z0-9_.-]*['\"]", match.group(1)):
            diagnostics.append(
                _diagnostic(
                    path,
                    text,
                    "entity_reference_dynamic",
                    "inheritEnts has no statically resolvable literal argument.",
                    match.start(),
                )
            )

    return {
        "identity": identity,
        "metadata": metadata,
        "literal_facts": literal_facts,
        "field_facts": field_facts,
        "array_facts": array_facts,
        "relationships": relationships,
        "includes": includes,
        "source_hash": source_hash,
        "diagnostics": diagnostics,
    }
