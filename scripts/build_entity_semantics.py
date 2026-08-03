"""Extract evidence-backed semantic facts from static ``.ent`` declarations.

This parser intentionally does not execute PHP.  A literal declaration becomes a
fact with source provenance; expressions whose target cannot be deterministically
resolved are retained as ``UNRESOLVED`` evidence instead of guessed mappings.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import click

from catalog.db import get_connection, require_foreign_key_integrity

EXTRACTOR = "entity_semantics"
EXTRACTOR_VERSION = "1"

PERMISSION_DECLARATIONS = (
    ("read", "PERMISSION_READ"),
    ("create", "PERMISSION_CREATE"),
    ("update", "PERMISSION_UPDATE"),
    ("delete", "PERMISSION_DELETE"),
    ("approve", "PERMISSION_APPROVE"),
    ("submit", "PERMISSION_SUBMIT"),
    ("decline", "PERMISSION_DECLINE"),
)


@dataclass(frozen=True)
class Evidence:
    text: str
    start_line: int
    end_line: int


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _skip_quoted(text: str, index: int) -> int:
    quote = text[index]
    index += 1
    while index < len(text):
        if text[index] == "\\":
            index += 2
        elif text[index] == quote:
            return index + 1
        else:
            index += 1
    return len(text)


def _skip_comment(text: str, index: int) -> int:
    if text.startswith("//", index):
        end = text.find("\n", index)
        return len(text) if end == -1 else end + 1
    if text.startswith("/*", index):
        end = text.find("*/", index + 2)
        return len(text) if end == -1 else end + 2
    if text[index : index + 1] == "#":
        end = text.find("\n", index)
        return len(text) if end == -1 else end + 1
    return index


def _matching_delimiter(text: str, start: int) -> int | None:
    opener = text[start]
    closer = "]" if opener == "[" else ")"
    depth = 1
    index = start + 1
    while index < len(text):
        char = text[index]
        if char in "'\"":
            index = _skip_quoted(text, index)
            continue
        skipped = _skip_comment(text, index)
        if skipped != index:
            index = skipped
            continue
        if char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _array_after(text: str, match_end: int) -> tuple[int, int] | None:
    index = match_end
    while index < len(text) and text[index].isspace():
        index += 1
    if text.startswith("array", index):
        index += len("array")
        while index < len(text) and text[index].isspace():
            index += 1
    if index >= len(text) or text[index] not in "[(":
        return None
    end = _matching_delimiter(text, index)
    return (index, end) if end is not None else None


def _top_level_items(text: str, start: int, end: int) -> Iterator[tuple[int, int]]:
    item_start = start + 1
    depth = 0
    index = item_start
    while index < end:
        char = text[index]
        if char in "'\"":
            index = _skip_quoted(text, index)
            continue
        skipped = _skip_comment(text, index)
        if skipped != index:
            index = skipped
            continue
        if char in "[(":
            depth += 1
        elif char in ")]":
            depth -= 1
        elif char == "," and depth == 0:
            if text[item_start:index].strip():
                yield item_start, index
            item_start = index + 1
        index += 1
    if text[item_start:end].strip():
        yield item_start, end


def _literal_value(item: str, key: str) -> str | None:
    match = re.search(
        rf"['\"]{re.escape(key)}['\"]\s*=>\s*(['\"])(.*?)\1",
        item,
        re.IGNORECASE | re.DOTALL,
    )
    return match.group(2) if match else None


def _has_key(item: str, key: str) -> bool:
    return bool(re.search(rf"['\"]{re.escape(key)}['\"]\s*=>", item, re.IGNORECASE))


def _evidence(text: str, start: int, end: int) -> Evidence:
    return Evidence(
        text=text[start:end].strip(),
        start_line=_line_number(text, start),
        end_line=_line_number(text, end),
    )


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _find_block(text: str, key: str) -> tuple[int, int] | None:
    match = re.search(rf"['\"]{re.escape(key)}['\"]\s*=>", text, re.IGNORECASE)
    return _array_after(text, match.end()) if match else None


def _entity_name_for_occurrence(conn: sqlite3.Connection, occurrence_id: int) -> str:
    row = conn.execute(
        "SELECT en.name FROM entity_occurrences eo "
        "JOIN entity_nodes en ON en.id=eo.entity_id WHERE eo.id=?",
        (occurrence_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"entity occurrence not found: {occurrence_id}")
    return str(row[0])


def _target_occurrence_id(
    conn: sqlite3.Connection, repo_id: int, target_name: str | None
) -> int | None:
    if not target_name:
        return None
    row = conn.execute(
        "SELECT eo.id FROM entity_occurrences eo "
        "JOIN entity_nodes en ON en.id=eo.entity_id "
        "WHERE eo.repo_id=? AND lower(en.name)=lower(?)",
        (repo_id, target_name),
    ).fetchone()
    return int(row[0]) if row else None


def _insert_component(
    conn: sqlite3.Connection,
    *,
    repo_id: int,
    occurrence_id: int,
    kind: str,
    path: str,
    declared_name: str | None,
    target: str | None,
    evidence: Evidence,
    source_file_id: int | None,
    source_path: str,
    properties: dict[str, object] | None = None,
    confidence: float = 1.0,
) -> int:
    evidence_hash = _hash(evidence.text)
    conn.execute(
        """INSERT OR IGNORE INTO entity_schema_components(
               repo_id,occurrence_id,component_kind,component_path,declared_name,
               target_literal,properties_json,source_file_id,source_path,start_line,
               end_line,evidence_text,evidence_hash,extractor,extractor_version,confidence
           ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            repo_id,
            occurrence_id,
            kind,
            path,
            declared_name,
            target,
            json.dumps(properties or {}, sort_keys=True),
            source_file_id,
            source_path,
            evidence.start_line,
            evidence.end_line,
            evidence.text,
            evidence_hash,
            EXTRACTOR,
            EXTRACTOR_VERSION,
            confidence,
        ),
    )
    row = conn.execute(
        "SELECT id FROM entity_schema_components WHERE repo_id=? AND occurrence_id=? "
        "AND component_kind=? AND component_path=? AND evidence_hash=?",
        (repo_id, occurrence_id, kind, path, evidence_hash),
    ).fetchone()
    return int(row[0])


def _insert_relationship(
    conn: sqlite3.Connection,
    *,
    repo_id: int,
    occurrence_id: int,
    component_id: int | None,
    axis: str,
    relation_kind: str,
    fact_key: str,
    target_name: str | None,
    target_literal: str | None,
    status: str,
    evidence: Evidence,
    source_file_id: int | None,
    source_path: str,
    qualifiers: dict[str, object] | None = None,
    confidence: float = 1.0,
) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO entity_relationship_facts(
               repo_id,source_occurrence_id,source_component_id,axis,relation_kind,
               fact_key,target_occurrence_id,target_entity_name,target_literal,
               assertion_status,qualifiers_json,source_file_id,source_path,start_line,
               end_line,evidence_text,evidence_hash,extractor,extractor_version,confidence
           ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            repo_id,
            occurrence_id,
            component_id,
            axis,
            relation_kind,
            fact_key,
            _target_occurrence_id(conn, repo_id, target_name),
            target_name,
            target_literal,
            status,
            json.dumps(qualifiers or {}, sort_keys=True),
            source_file_id,
            source_path,
            evidence.start_line,
            evidence.end_line,
            evidence.text,
            _hash(evidence.text),
            EXTRACTOR,
            EXTRACTOR_VERSION,
            confidence,
        ),
    )


def _insert_operation(
    conn: sqlite3.Connection,
    *,
    repo_id: int,
    occurrence_id: int,
    operation: str,
    availability: str,
    evidence: Evidence,
    source_file_id: int | None,
    source_path: str,
    parent_occurrence_id: int | None = None,
    qualifiers: dict[str, object] | None = None,
) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO entity_operation_facts(
               repo_id,occurrence_id,axis,operation,surface_kind,availability,
               invocation_context,persistence_scope,standalone,parent_occurrence_id,
               qualifiers_json,source_file_id,source_path,start_line,end_line,evidence_text,
               evidence_hash,extractor,extractor_version,confidence
           ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            repo_id,
            occurrence_id,
            "E",
            operation,
            "ent_api_permission",
            availability,
            "unresolved",
            "unresolved",
            None,
            parent_occurrence_id,
            json.dumps(qualifiers or {}, sort_keys=True),
            source_file_id,
            source_path,
            evidence.start_line,
            evidence.end_line,
            evidence.text,
            _hash(f"{operation}:{evidence.text}"),
            EXTRACTOR,
            EXTRACTOR_VERSION,
            1.0,
        ),
    )


def _extract_occurrence(
    conn: sqlite3.Connection,
    *,
    repo_id: int,
    occurrence_id: int,
    source_file_id: int | None,
    source_path: str,
    source: str,
) -> tuple[int, int, str | None]:
    entity_name = _entity_name_for_occurrence(conn, occurrence_id)
    component_count = 0
    fact_count = 0
    partial_reasons: list[str] = []

    def add_owned_item(start: int, end: int, ordinal: int) -> None:
        nonlocal component_count, fact_count
        item = source[start:end]
        target = _literal_value(item, "entity")
        path = _literal_value(item, "path") or f"ownedobjects/{ordinal}"
        evidence = _evidence(source, start, end)
        component_id = _insert_component(
            conn,
            repo_id=repo_id,
            occurrence_id=occurrence_id,
            kind="owned_collection",
            path=path,
            declared_name=None,
            target=target,
            evidence=evidence,
            source_file_id=source_file_id,
            source_path=source_path,
            properties={
                "fkey": _literal_value(item, "fkey"),
                "invfkey": _literal_value(item, "invfkey"),
            },
        )
        component_count += 1
        status = "VERIFIED" if target else "UNRESOLVED"
        if target is None:
            partial_reasons.append("ownedobjects target is dynamic")
        _insert_relationship(
            conn,
            repo_id=repo_id,
            occurrence_id=occurrence_id,
            component_id=component_id,
            axis="A",
            relation_kind="owns_collection",
            fact_key=f"{entity_name.lower()}.owns.{(target or path).lower()}",
            target_name=target,
            target_literal=None if target else item,
            status=status,
            evidence=evidence,
            source_file_id=source_file_id,
            source_path=source_path,
        )
        fact_count += 1

    owned = _find_block(source, "ownedobjects")
    if owned:
        for ordinal, (start, end) in enumerate(_top_level_items(source, *owned)):
            add_owned_item(start, end, ordinal)

    # Some declarations append literal child objects after the base schema.
    # They are static facts too, but do not form part of the base array above.
    appended_owned = re.compile(r"\[['\"]ownedobjects['\"]\]\s*\[\]\s*=", re.IGNORECASE)
    for ordinal, match in enumerate(appended_owned.finditer(source), start=10_000):
        array = _array_after(source, match.end())
        if array is None:
            partial_reasons.append("ownedobjects append expression is dynamic")
            continue
        _, end = array
        add_owned_item(match.start(), end + 1, ordinal)

    parent_entity_match = re.search(
        r"['\"]parententity['\"]\s*=>\s*(['\"])(.*?)\1",
        source,
        re.IGNORECASE | re.DOTALL,
    )
    parent_occurrence_id = None
    if parent_entity_match:
        target = parent_entity_match.group(2)
        evidence = _evidence(
            source, parent_entity_match.start(), parent_entity_match.end()
        )
        component_id = _insert_component(
            conn,
            repo_id=repo_id,
            occurrence_id=occurrence_id,
            kind="parent_entity",
            path="parententity",
            declared_name="parententity",
            target=target,
            evidence=evidence,
            source_file_id=source_file_id,
            source_path=source_path,
        )
        component_count += 1
        parent_occurrence_id = _target_occurrence_id(conn, repo_id, target)
        _insert_relationship(
            conn,
            repo_id=repo_id,
            occurrence_id=occurrence_id,
            component_id=component_id,
            axis="A",
            relation_kind="owned_by_parent_entity",
            fact_key=f"{entity_name.lower()}.parententity.{target.lower()}",
            target_name=target,
            target_literal=None,
            status="VERIFIED",
            evidence=evidence,
            source_file_id=source_file_id,
            source_path=source_path,
        )
        fact_count += 1

    # Field-info arrays retain the field's path, typed target, and display
    # metadata in one static declaration.  This is the required evidence unit
    # for parent and visibility classifications; a field name alone is never
    # enough.
    fieldinfo = _find_block(source, "fieldinfo")
    if fieldinfo:
        for start, end in _top_level_items(source, *fieldinfo):
            item = source[start:end]
            path = _literal_value(item, "path")
            target = _literal_value(item, "entity")
            fullname = _literal_value(item, "fullname") or ""
            evidence = _evidence(source, start, end)
            if (
                path
                and target
                and target.lower() == entity_name.lower()
                and "PARENT" in path.upper()
            ):
                component_id = _insert_component(
                    conn,
                    repo_id=repo_id,
                    occurrence_id=occurrence_id,
                    kind="field",
                    path=path,
                    declared_name=fullname or path,
                    target=entity_name,
                    evidence=evidence,
                    source_file_id=source_file_id,
                    source_path=source_path,
                )
                component_count += 1
                is_location = entity_name.lower() == "location"
                has_hierarchy = bool(
                    re.search(
                        r"['\"]showhierarchy['\"]\s*=>\s*true", source, re.IGNORECASE
                    )
                )
                # A self-target pointer and a self table parent join are separate
                # source declarations.  Location additionally needs hierarchy
                # metadata to receive axis C.
                has_parent_join = bool(
                    re.search(
                        rf"['\"]parent['\"]\s*=>\s*(?:array\s*)?\(.*?['\"]table['\"]\s*=>\s*['\"]{re.escape(entity_name)}['\"]",
                        source,
                        re.IGNORECASE | re.DOTALL,
                    )
                )
                axis = "C" if is_location and has_hierarchy else "B"
                status = (
                    "CORROBORATED" if has_parent_join or has_hierarchy else "UNRESOLVED"
                )
                _insert_relationship(
                    conn,
                    repo_id=repo_id,
                    occurrence_id=occurrence_id,
                    component_id=component_id,
                    axis=axis,
                    relation_kind="location_parent_reference"
                    if axis == "C"
                    else "business_parent_reference",
                    fact_key=f"{entity_name.lower()}.parent",
                    target_name=entity_name,
                    target_literal=None,
                    status=status,
                    evidence=evidence,
                    source_file_id=source_file_id,
                    source_path=source_path,
                    qualifiers={
                        "has_parent_join": has_parent_join,
                        "has_hierarchy": has_hierarchy,
                    },
                )
                fact_count += 1
            elif path == "PARENTENTRY":
                component_id = _insert_component(
                    conn,
                    repo_id=repo_id,
                    occurrence_id=occurrence_id,
                    kind="field",
                    path=path,
                    declared_name=fullname or path,
                    target=None,
                    evidence=evidence,
                    source_file_id=source_file_id,
                    source_path=source_path,
                )
                component_count += 1
                _insert_relationship(
                    conn,
                    repo_id=repo_id,
                    occurrence_id=occurrence_id,
                    component_id=component_id,
                    axis="B",
                    relation_kind="business_parent_reference",
                    fact_key=f"{entity_name.lower()}.parententry",
                    target_name=None,
                    target_literal="PARENTENTRY",
                    status="UNRESOLVED",
                    evidence=evidence,
                    source_file_id=source_file_id,
                    source_path=source_path,
                    qualifiers={
                        "reason": "integer field has no deterministically declared target"
                    },
                )
                fact_count += 1
            elif path == "OBJECTRESTRICTION":
                values = re.findall(
                    r"['\"](Unrestricted|RootOnly|Restricted)['\"]", item, re.IGNORECASE
                )
                component_id = _insert_component(
                    conn,
                    repo_id=repo_id,
                    occurrence_id=occurrence_id,
                    kind="visibility_value",
                    path=path,
                    declared_name=fullname or path,
                    target=None,
                    evidence=evidence,
                    source_file_id=source_file_id,
                    source_path=source_path,
                    properties={"values": values},
                )
                component_count += 1
                _insert_relationship(
                    conn,
                    repo_id=repo_id,
                    occurrence_id=occurrence_id,
                    component_id=component_id,
                    axis="D",
                    relation_kind="visibility_enum",
                    fact_key=f"{entity_name.lower()}.visibility",
                    target_name=None,
                    target_literal=path,
                    status="VERIFIED",
                    evidence=evidence,
                    source_file_id=source_file_id,
                    source_path=source_path,
                    qualifiers={"values": values},
                )
                fact_count += 1

    # Explicit self-target parent pointers are considered hierarchy only with
    # corroborating structural evidence.  Location uses its own axis C.
    self_target_pattern = re.compile(
        r"['\"]entity['\"]\s*=>\s*(['\"])(?P<target>[^'\"]+)\1", re.IGNORECASE
    )
    self_targets = [
        m
        for m in self_target_pattern.finditer(source)
        if m.group("target").lower() == entity_name.lower()
    ]
    has_parent_storage = bool(
        re.search(
            r"['\"]parent['\"]\s*=>.*?['\"]table['\"]\s*=>\s*['\"]"
            + re.escape(entity_name.lower())
            + r"['\"]",
            source,
            re.IGNORECASE | re.DOTALL,
        )
    )
    has_hierarchy = bool(
        re.search(r"['\"]showhierarchy['\"]\s*=>\s*true", source, re.IGNORECASE)
    )
    for ordinal, match in enumerate(self_targets):
        context_start = max(0, source.rfind("array", 0, match.start()))
        context_end = source.find("),", match.end())
        context_end = len(source) if context_end == -1 else context_end + 1
        item = source[context_start:context_end]
        path = _literal_value(item, "path")
        if not path or "PARENT" not in path.upper():
            continue
        evidence = _evidence(source, context_start, context_end)
        component_id = _insert_component(
            conn,
            repo_id=repo_id,
            occurrence_id=occurrence_id,
            kind="field",
            path=path,
            declared_name=path,
            target=entity_name,
            evidence=evidence,
            source_file_id=source_file_id,
            source_path=source_path,
        )
        component_count += 1
        axis = "C" if entity_name.lower() == "location" and has_hierarchy else "B"
        status = "CORROBORATED" if has_parent_storage or has_hierarchy else "UNRESOLVED"
        _insert_relationship(
            conn,
            repo_id=repo_id,
            occurrence_id=occurrence_id,
            component_id=component_id,
            axis=axis,
            relation_kind="location_parent_reference"
            if axis == "C"
            else "business_parent_reference",
            fact_key=f"{entity_name.lower()}.parent",
            target_name=entity_name,
            target_literal=None,
            status=status,
            evidence=evidence,
            source_file_id=source_file_id,
            source_path=source_path,
            qualifiers={
                "has_parent_storage": has_parent_storage,
                "has_hierarchy": has_hierarchy,
            },
        )
        fact_count += 1

    visibility_match = re.search(
        r"['\"]path['\"]\s*=>\s*['\"]OBJECTRESTRICTION['\"].{0,1600}?['\"]validvalues['\"]\s*=>\s*(?:array\s*\()?\s*\[?\s*['\"]Unrestricted['\"]",
        source,
        re.IGNORECASE | re.DOTALL,
    )
    if visibility_match:
        evidence = _evidence(source, visibility_match.start(), visibility_match.end())
        component_id = _insert_component(
            conn,
            repo_id=repo_id,
            occurrence_id=occurrence_id,
            kind="visibility_value",
            path="OBJECTRESTRICTION",
            declared_name="OBJECTRESTRICTION",
            target=None,
            evidence=evidence,
            source_file_id=source_file_id,
            source_path=source_path,
            properties={"values": ["Unrestricted", "RootOnly", "Restricted"]},
        )
        component_count += 1
        _insert_relationship(
            conn,
            repo_id=repo_id,
            occurrence_id=occurrence_id,
            component_id=component_id,
            axis="D",
            relation_kind="visibility_enum",
            fact_key=f"{entity_name.lower()}.visibility",
            target_name=None,
            target_literal="OBJECTRESTRICTION",
            status="VERIFIED",
            evidence=evidence,
            source_file_id=source_file_id,
            source_path=source_path,
            qualifiers={"values": ["Unrestricted", "RootOnly", "Restricted"]},
        )
        fact_count += 1

    for match in re.finditer(
        r"['\"]path['\"]\s*=>\s*['\"]ENTITY['\"]", source, re.IGNORECASE
    ):
        evidence = _evidence(source, match.start(), match.end())
        component_id = _insert_component(
            conn,
            repo_id=repo_id,
            occurrence_id=occurrence_id,
            kind="field",
            path="ENTITY",
            declared_name="ENTITY",
            target=None,
            evidence=evidence,
            source_file_id=source_file_id,
            source_path=source_path,
        )
        component_count += 1
        _insert_relationship(
            conn,
            repo_id=repo_id,
            occurrence_id=occurrence_id,
            component_id=component_id,
            axis="E",
            relation_kind="entity_context_field",
            fact_key=f"{entity_name.lower()}.entity_context",
            target_name=None,
            target_literal="ENTITY",
            status="VERIFIED",
            evidence=evidence,
            source_file_id=source_file_id,
            source_path=source_path,
        )
        fact_count += 1

    for match in re.finditer(
        r"['\"]entityContext['\"]\s*=>\s*true", source, re.IGNORECASE
    ):
        evidence = _evidence(source, match.start(), match.end())
        component_id = _insert_component(
            conn,
            repo_id=repo_id,
            occurrence_id=occurrence_id,
            kind="entity_context_metadata",
            path=f"entityContext/{evidence.start_line}",
            declared_name="entityContext",
            target=None,
            evidence=evidence,
            source_file_id=source_file_id,
            source_path=source_path,
        )
        component_count += 1
        _insert_relationship(
            conn,
            repo_id=repo_id,
            occurrence_id=occurrence_id,
            component_id=component_id,
            axis="E",
            relation_kind="entity_context_metadata",
            fact_key=f"{entity_name.lower()}.entity_context_metadata.{evidence.start_line}",
            target_name=None,
            target_literal="entityContext",
            status="VERIFIED",
            evidence=evidence,
            source_file_id=source_file_id,
            source_path=source_path,
            qualifiers={"value": True},
        )
        fact_count += 1

    for operation, key in PERMISSION_DECLARATIONS:
        match = re.search(
            rf"['\"]{key}['\"]\s*=>\s*(?P<value>'[^']*'|\"[^\"]*\"|[^,\r\n]+)",
            source,
            re.IGNORECASE,
        )
        if not match:
            continue
        raw_requirement = match.group("value").strip()
        is_literal = (
            len(raw_requirement) >= 2
            and raw_requirement[0] == raw_requirement[-1]
            and raw_requirement[0] in {"'", '"'}
        )
        requirement = raw_requirement[1:-1] if is_literal else raw_requirement
        evidence = _evidence(source, match.start(), match.end())
        _insert_operation(
            conn,
            repo_id=repo_id,
            occurrence_id=occurrence_id,
            operation=operation,
            availability="denied"
            if is_literal and requirement.upper() == "NONE"
            else "unresolved",
            evidence=evidence,
            source_file_id=source_file_id,
            source_path=source_path,
            parent_occurrence_id=parent_occurrence_id,
            qualifiers={
                "permission_requirement": requirement,
                "declaration_kind": "literal" if is_literal else "dynamic",
            },
        )
        fact_count += 1

    return component_count, fact_count, "; ".join(sorted(set(partial_reasons))) or None


def _materialize_conflicts(conn: sqlite3.Connection, repo_id: int) -> int:
    """Persist incompatible assertions instead of selecting a preferred target."""
    rows = conn.execute(
        "SELECT id,fact_key,target_occurrence_id,target_entity_name,target_literal,"
        "source_file_id,source_path FROM entity_relationship_facts "
        "WHERE repo_id=? ORDER BY fact_key,id",
        (repo_id,),
    ).fetchall()
    groups: dict[str, dict[str, list[sqlite3.Row]]] = {}
    for row in rows:
        target = str(
            row["target_occurrence_id"]
            if row["target_occurrence_id"] is not None
            else row["target_entity_name"]
            if row["target_entity_name"] is not None
            else row["target_literal"]
            if row["target_literal"] is not None
            else "<none>"
        )
        groups.setdefault(str(row["fact_key"]), {}).setdefault(target, []).append(row)
    inserted = 0
    for fact_key, targets in groups.items():
        if len(targets) < 2:
            continue
        representatives = [rows[0] for _, rows in sorted(targets.items())]
        left = representatives[0]
        for right in representatives[1:]:
            conn.execute(
                """INSERT OR IGNORE INTO entity_semantic_conflicts(
                       repo_id,fact_key,left_fact_id,right_fact_id,conflict_kind,status,
                       reason,source_file_id,source_path,confidence
                   ) VALUES (?,?,?,?,?,'open',?,?,?,?)""",
                (
                    repo_id,
                    fact_key,
                    int(left["id"]),
                    int(right["id"]),
                    "incompatible_target",
                    "Static assertions with one fact key resolve to different targets",
                    left["source_file_id"],
                    left["source_path"],
                    1.0,
                ),
            )
            inserted += 1
    return inserted


def build(
    db: str, repo_root: str | Path, repo_key: str, reset: bool = True
) -> dict[str, int]:
    """Build semantic facts for all .ent occurrences in a repository."""
    root = Path(repo_root)
    conn = get_connection(db)
    try:
        from catalog.repository_lifecycle import require_repository_extractable
        repo = require_repository_extractable(conn, repo_key)
        repo_id = int(repo[0])
        conn.execute("BEGIN IMMEDIATE")
        if reset:
            conn.execute(
                "DELETE FROM entity_semantic_conflicts WHERE repo_id=?", (repo_id,)
            )
            conn.execute(
                "DELETE FROM entity_operation_facts WHERE repo_id=?", (repo_id,)
            )
            conn.execute(
                "DELETE FROM entity_relationship_facts WHERE repo_id=?", (repo_id,)
            )
            conn.execute(
                "DELETE FROM entity_schema_components WHERE repo_id=?", (repo_id,)
            )
            conn.execute(
                "DELETE FROM entity_extraction_coverage WHERE repo_id=?", (repo_id,)
            )

        rows = conn.execute(
            "SELECT eo.id,eo.ent_file,eo.source_file_id,f.path "
            "FROM entity_occurrences eo LEFT JOIN files f ON f.id=eo.source_file_id "
            "WHERE eo.repo_id=? AND eo.ent_file IS NOT NULL ORDER BY eo.id",
            (repo_id,),
        ).fetchall()
        totals = {
            "occurrences": 0,
            "components": 0,
            "facts": 0,
            "partial": 0,
            "failed": 0,
        }
        for row in rows:
            occurrence_id = int(row[0])
            source_path = str(row[1])
            source_file_id = int(row[2]) if row[2] is not None else None
            path = root / source_path
            totals["occurrences"] += 1
            try:
                source = path.read_text(encoding="utf-8", errors="ignore")
                components, facts, diagnostic = _extract_occurrence(
                    conn,
                    repo_id=repo_id,
                    occurrence_id=occurrence_id,
                    source_file_id=source_file_id,
                    source_path=source_path,
                    source=source,
                )
                status = "partial" if diagnostic else "complete"
                totals["components"] += components
                totals["facts"] += facts
                totals["partial"] += int(status == "partial")
            except OSError as exc:
                components, facts, diagnostic, status = 0, 0, str(exc), "failed"
                totals["failed"] += 1
            source_hash = _hash(source) if status != "failed" else None
            # Coverage is deliberately axis-specific.  The current static
            # extractor is complete only for the narrow declaration families it
            # actually evaluates.  A missing fact in a partial family is an
            # investigation gap, never evidence for NOT_OBSERVED.
            axis_fact_counts = {
                axis: conn.execute(
                    "SELECT COUNT(*) FROM entity_relationship_facts "
                    "WHERE source_occurrence_id=? AND axis=?",
                    (occurrence_id, axis),
                ).fetchone()[0]
                for axis in ("A", "B", "C", "D", "E")
            }
            entity_name = _entity_name_for_occurrence(conn, occurrence_id).lower()
            family_status = {
                "components": status,
                "A": status,
                # Inheritance, nexus and cross-file storage are not evaluated
                # yet, so hierarchy cannot be closed-world.
                "B": "partial" if status != "failed" else "failed",
                # Only Location can declare a Location hierarchy.  Other
                # objects are not applicable to this axis.
                "C": (status if entity_name == "location" else "not_applicable"),
                # Visibility and entity context are currently source-local;
                # OpenAPI, configuration and override extraction remains pending.
                "D": "partial" if status != "failed" else "failed",
                "E": "partial" if status != "failed" else "failed",
                "operations": "partial" if status != "failed" else "failed",
            }
            for family, family_state in family_status.items():
                family_components = components if family == "components" else 0
                family_facts = (
                    facts
                    if family == "components"
                    else int(axis_fact_counts.get(family, 0))
                )
                family_diagnostic = diagnostic
                if family_state == "partial" and not family_diagnostic:
                    family_diagnostic = "declaration family is not yet closed-world"
                conn.execute(
                    """INSERT OR REPLACE INTO entity_extraction_coverage(
                           repo_id,occurrence_id,source_file_id,source_path,extractor,
                           extractor_version,declaration_family,source_hash,status,
                           component_count,fact_count,diagnostic
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        repo_id,
                        occurrence_id,
                        source_file_id,
                        source_path,
                        EXTRACTOR,
                        EXTRACTOR_VERSION,
                        family,
                        source_hash,
                        family_state,
                        family_components,
                        family_facts,
                        family_diagnostic,
                    ),
                )
        totals["conflicts"] = _materialize_conflicts(conn, repo_id)
        require_foreign_key_integrity(conn, context="entity semantic build")
        conn.commit()
        return totals
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        conn.close()


@click.command()
@click.option("--db", default="catalog/catalog.db", show_default=True)
@click.option(
    "--repo-root", required=True, type=click.Path(path_type=Path, exists=True)
)
@click.option("--repo", "repo_key", required=True)
@click.option("--reset/--no-reset", default=True)
def main(db: str, repo_root: Path, repo_key: str, reset: bool) -> None:
    """Build static .ent semantic evidence for one repository."""
    click.echo(json.dumps(build(db, repo_root, repo_key, reset), sort_keys=True))


if __name__ == "__main__":
    main()
