"""Snapshot-native P0 database and entity metadata extraction."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from catalog.repo_v1_entities import (
    _delimiter_state,
    _lex,
    _matching_close,
    _statement_end,
)
from catalog.source_snapshot import SourceSnapshot, SourceSnapshotError

EXTRACTOR = "repo_v1_database_v1"
VERSION = "1"
STATUS = {"resolved", "unresolved", "ambiguous", "unsupported"}
SECTIONS = {
    "fieldinfo",
    "schema",
    "api",
    "dbfilters",
    "children",
    "nexus",
    "ownedobjects",
    "object",
    "publish",
    "importorder",
    "table",
    "view",
    "module",
    "dummy",
    "parententity",
    "vid",
    "autoincrement",
    "sqldomarkup",
    "sqlmarkupfields",
}


@dataclass(frozen=True)
class DatabaseExtractionStats:
    table_count: int
    field_count: int
    section_count: int
    entity_field_count: int
    mapping_count: int
    table_link_count: int
    field_link_count: int
    diagnostic_count: int


@dataclass(frozen=True)
class _Span:
    start: int
    end: int


@dataclass
class _ValueNode:
    value: Any
    start: int
    end: int
    valid: bool
    children: list[tuple[str, _Span, "_ValueNode"]]

    def child(self, key: str) -> "_ValueNode | None":
        for child_key, _span, child in reversed(self.children):
            if child_key == key:
                return child
        return None

    def child_span(self, key: str) -> _Span | None:
        for child_key, span, _child in reversed(self.children):
            if child_key == key:
                return span
        return None


@dataclass(frozen=True)
class _Assignment:
    path: list[str]
    value: Any
    valid: bool
    start: int
    end: int
    evidence: str
    value_node: _ValueNode | None


@dataclass(frozen=True)
class _LocatedSpan:
    path: str
    span: _Span


def _canon(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False
    )


def _key(kind: str, path: str, pointer: str, extra: Any = None) -> str:
    return hashlib.sha256(_canon([kind, path, pointer, extra]).encode()).hexdigest()


def _line(text: str, offset: int) -> int:
    return text.count("\n", 0, max(0, offset)) + 1


def _source(
    path: str,
    file_id: int,
    sha: str,
    raw: bytes,
    text: str,
    start: int,
    end: int,
    pointer: str,
) -> dict[str, Any]:
    return {
        "source_file_id": file_id,
        "source_path": path,
        "source_commit_sha": sha,
        "source_hash": hashlib.sha256(raw).hexdigest(),
        "source_pointer": pointer,
        "start_line": _line(text, start),
        "end_line": _line(text, end),
        "evidence": text[start:end],
    }


def _bounded_source(
    path: str,
    file_id: int,
    sha: str,
    raw: bytes,
    text: str,
    start: int,
    end: int,
    pointer: str,
) -> dict[str, Any]:
    if not 0 <= start < end <= len(text):
        raise SourceSnapshotError(f"database evidence span is invalid: {pointer}")
    source = _source(path, file_id, sha, raw, text, start, end, pointer)
    if not source["evidence"] or len(source["evidence"]) >= len(text):
        raise SourceSnapshotError(f"database evidence span is not bounded: {pointer}")
    if source["start_line"] < 1 or source["end_line"] < source["start_line"]:
        raise SourceSnapshotError(f"database evidence lines are invalid: {pointer}")
    return source


def _parse_value(
    tokens: list[Any], start: int = 0
) -> tuple[_ValueNode | None, int, bool]:
    if start >= len(tokens):
        return None, start, False
    token = tokens[start]
    if token.kind == "string":
        valid = bool(token.valid and not token.interpolation)
        return (
            _ValueNode(
                token.value if valid else None,
                token.start,
                token.end,
                valid,
                [],
            ),
            start + 1,
            valid,
        )
    if token.kind == "number":
        value = float(token.value) if "." in str(token.value) else int(str(token.value))
        return (
            _ValueNode(value, token.start, token.end, True, []),
            start + 1,
            True,
        )
    if token.kind == "ident" and str(token.value).lower() in {"true", "false", "null"}:
        value = {"true": True, "false": False, "null": None}[str(token.value).lower()]
        return (
            _ValueNode(value, token.start, token.end, True, []),
            start + 1,
            True,
        )
    opener = start
    if (
        token.value == "array"
        and start + 1 < len(tokens)
        and tokens[start + 1].value == "("
    ):
        opener = start + 1
    if tokens[opener].value not in {"[", "("}:
        return None, start + 1, False
    close = _matching_close(tokens, opener)
    if close is None:
        return None, len(tokens), False
    result: dict[str, Any] = {}
    positional: list[Any] = []
    positional_nodes: list[_ValueNode] = []
    children: list[tuple[str, _Span, _ValueNode]] = []
    static = True
    cursor = opener + 1
    index = 0
    while cursor < close:
        if tokens[cursor].value == ",":
            cursor += 1
            continue
        key: str | None = None
        value_start = cursor
        entry_start = tokens[cursor].start
        if cursor + 1 < close and tokens[cursor + 1].value == "=>":
            key_token = tokens[cursor]
            if (
                key_token.kind == "string"
                and key_token.valid
                and not key_token.interpolation
            ):
                key = str(key_token.value)
            elif key_token.kind == "number":
                key = str(key_token.value)
            else:
                static = False
            value_start = cursor + 2
        value_node, next_cursor, valid = _parse_value(tokens, value_start)
        if value_node is None or next_cursor <= value_start:
            return None, close + 1, False
        static = static and valid
        if key is None:
            positional.append(value_node.value)
            positional_nodes.append(value_node)
            index += 1
        else:
            result[key] = value_node.value
            children.append((key, _Span(entry_start, value_node.end), value_node))
        cursor = next_cursor
        if cursor < close and tokens[cursor].value == ",":
            cursor += 1
    if result and positional:
        for i, (value, value_node) in enumerate(zip(positional, positional_nodes)):
            key = str(i)
            result[key] = value
            children.append((key, _Span(value_node.start, value_node.end), value_node))
        value: Any = result
    else:
        value = result if result else positional
    return (
        _ValueNode(
            value,
            tokens[start].start,
            tokens[close].end,
            static,
            children,
        ),
        close + 1,
        static,
    )


def _value(tokens: list[Any], start: int = 0) -> tuple[Any, int, bool]:
    node, next_cursor, valid = _parse_value(tokens, start)
    return (node.value if node is not None else None), next_cursor, valid


def _assignments(text: str) -> list[_Assignment]:
    tokens, lexical = _lex(text)
    states, delimiter = _delimiter_state(tokens)
    if lexical is not None or delimiter is not None:
        return []
    out: list[_Assignment] = []
    for i, token in enumerate(tokens):
        if (
            token.kind != "var"
            or token.value not in {"kTables", "kSchemas"}
            or states[i] != (0, 0, 0)
        ):
            continue
        cursor = i + 1
        path: list[str] = [str(token.value)]
        while (
            cursor + 2 < len(tokens)
            and tokens[cursor].value == "["
            and tokens[cursor + 2].value == "]"
        ):
            key = tokens[cursor + 1]
            if key.kind != "string" or not key.valid or key.interpolation:
                break
            path.append(str(key.value))
            cursor += 3
            if (
                cursor < len(tokens)
                and tokens[cursor].value == "["
                and cursor + 1 < len(tokens)
                and tokens[cursor + 1].value == "]"
            ):
                path.append("[]")
                cursor += 2
        if (
            cursor >= len(tokens)
            or tokens[cursor].value != "="
            or states[i] != (0, 0, 0)
        ):
            continue
        end = _statement_end(tokens, states, cursor + 1)
        rhs = tokens[cursor + 1 : end]
        value_node, _, valid = _parse_value(rhs)
        value = value_node.value if value_node is not None else None
        start = token.start
        finish = (
            tokens[end].end
            if end < len(tokens)
            else (rhs[-1].end if rhs else token.end)
        )
        out.append(
            _Assignment(
                path,
                value,
                valid,
                start,
                finish,
                text[start:finish],
                value_node,
            )
        )
    return out


def _walk(value: Any, prefix: str = "") -> list[tuple[str, Any]]:
    if isinstance(value, dict):
        result: list[tuple[str, Any]] = []
        for key in sorted(value):
            result.extend(_walk(value[key], f"{prefix}.{key}" if prefix else str(key)))
        return result
    return [(prefix, value)]


def _merge_entity_assignment(
    values: dict[str, Any], path: list[str], value: Any
) -> None:
    """Apply a source-shaped kSchemas assignment to the normalized tree."""
    if not path:
        if isinstance(value, dict):
            values.update(value)
        return
    section = path[0]
    if len(path) == 1:
        if (
            section in values
            and isinstance(values[section], dict)
            and isinstance(value, dict)
        ):
            values[section].update(value)
        else:
            values[section] = value
        return
    current = values.setdefault(section, {})
    if isinstance(current, list) and path[1] != "[]":
        current = {}
        values[section] = current
    for index, key in enumerate(path[1:]):
        last = index == len(path[1:]) - 1
        if key == "[]":
            if not isinstance(current, list):
                return
            if last:
                current.append(value)
                return
            current.append({})
            current = current[-1]
            continue
        if last:
            if isinstance(current, dict):
                current[key] = value
            return
        if not isinstance(current, dict):
            return
        next_key = path[index + 2]
        if next_key == "[]":
            current = current.setdefault(key, [])
        else:
            current = current.setdefault(key, {})


def _insert_diag(
    conn: sqlite3.Connection,
    repo_id: int,
    file_id: int,
    path: str,
    sha: str,
    raw: bytes,
    text: str,
    code: str,
    message: str,
    start: int,
    status: str = "unresolved",
) -> None:
    pointer = f"diagnostic:{code}:{start}"
    source = _source(
        path, file_id, sha, raw, text, start, min(len(text), start + 200), pointer
    )
    conn.execute(
        "INSERT OR IGNORE INTO repo_v1_database_diagnostics(repo_id,file_id,fact_key,code,message,diagnostic_key,source_file_id,source_path,source_commit_sha,source_hash,source_pointer,start_line,end_line,evidence,extractor,extractor_version,resolution_status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            repo_id,
            file_id,
            None,
            code,
            message,
            _key("diagnostic", path, pointer),
            *source.values(),
            EXTRACTOR,
            VERSION,
            status,
        ),
    )


def _facts(
    source: SourceSnapshot, conn: sqlite3.Connection, repo_id: int
) -> tuple[
    dict[str, Any],
    dict[str, _LocatedSpan],
    dict[tuple[str, str], _LocatedSpan],
    list[dict[str, Any]],
]:
    files = {
        str(r["path"]): r
        for r in conn.execute("SELECT * FROM files WHERE repo_id=?", (repo_id,))
    }
    assignments: dict[str, list[_Assignment]] = {}
    for path in sorted(files):
        if path != "app/source/common/dbschema.inc" and not (
            path.startswith("app/source/") and path.endswith(".ent")
        ):
            continue
        entry = next((x for x in source.entries if x.path == path), None)
        if entry is None:
            raise SourceSnapshotError(
                f"snapshot entry is not present in candidate files: {path}"
            )
        raw = (source.snapshot_root / Path(path)).read_bytes()
        text = raw.decode("utf-8", errors="replace")
        values = _assignments(text)
        if not values and (path.endswith("dbschema.inc") or path.endswith(".ent")):
            _insert_diag(
                conn,
                repo_id,
                int(files[path]["id"]),
                path,
                str(files[path]["source_commit_sha"]),
                raw,
                text,
                "dbschema_parse_error"
                if path.endswith("dbschema.inc")
                else "entity_section_unsupported",
                "no static assignments parsed",
                0,
            )
        assignments[path] = values
    tables: dict[str, Any] = {}
    table_spans: dict[str, _LocatedSpan] = {}
    field_spans: dict[tuple[str, str], _LocatedSpan] = {}
    entities: list[dict[str, Any]] = []
    for path, values in assignments.items():
        for assignment in values:
            lhs = assignment.path
            value = assignment.value
            valid = assignment.valid
            if (
                lhs[0] == "kTables"
                and valid
                and isinstance(value, dict)
                and assignment.value_node is not None
            ):
                tables.update(value)
                for table_name in value:
                    table_node = assignment.value_node.child(str(table_name))
                    table_span = assignment.value_node.child_span(str(table_name))
                    if table_node is None or table_span is None:
                        raise SourceSnapshotError(
                            f"database table span is not provable: {path}:{table_name}"
                        )
                    table_spans[str(table_name)] = _LocatedSpan(path, table_span)
                    field_node = table_node.child("db_fieldinfo")
                    fields = value[table_name]
                    if not isinstance(fields, dict):
                        continue
                    fields = fields.get("db_fieldinfo", {})
                    if not isinstance(fields, dict):
                        continue
                    if not fields:
                        continue
                    if field_node is None:
                        raise SourceSnapshotError(
                            f"database field map span is not provable: {path}:{table_name}"
                        )
                    for field_name in fields:
                        field_value_node = field_node.child(str(field_name))
                        field_span = field_node.child_span(str(field_name))
                        if field_value_node is None or field_span is None:
                            raise SourceSnapshotError(
                                "database field span is not provable: "
                                f"{path}:{table_name}:{field_name}"
                            )
                        field_spans[(str(table_name), str(field_name))] = _LocatedSpan(
                            path, field_span
                        )
            if lhs[0] == "kSchemas" and len(lhs) >= 2:
                item = next(
                    (x for x in entities if x["path"] == path and x["name"] == lhs[1]),
                    None,
                )
                if item is None:
                    item = {
                        "path": path,
                        "name": lhs[1],
                        "values": {},
                        "assignments": [],
                    }
                    entities.append(item)
                item["assignments"].append(assignment)
                if valid:
                    _merge_entity_assignment(item["values"], lhs[2:], value)
    return tables, table_spans, field_spans, entities


def extract_snapshot_database_facts(
    conn: sqlite3.Connection,
    *,
    repo_id: int,
    snapshot: SourceSnapshot,
    show_progress: bool = False,
) -> DatabaseExtractionStats:
    del show_progress
    for table in (
        "dbschema_fields",
        "dbschema_tables",
        "entity_section_facts",
        "entity_field_facts",
        "entity_schema_mappings",
        "entity_db_field_links",
        "entity_db_table_links",
        "repo_v1_database_diagnostics",
    ):
        conn.execute(f"DELETE FROM {table} WHERE repo_id=?", (repo_id,))
    tables, table_spans, field_spans, entities = _facts(snapshot, conn, repo_id)
    file_rows = {
        str(r["path"]): r
        for r in conn.execute("SELECT * FROM files WHERE repo_id=?", (repo_id,))
    }
    table_ids: dict[str, int] = {}
    field_ids: dict[tuple[str, str], int] = {}
    dbpath = "app/source/common/dbschema.inc"
    source_cache: dict[str, tuple[sqlite3.Row, bytes, str]] = {}

    def source_for(path: str) -> tuple[sqlite3.Row, bytes, str]:
        cached = source_cache.get(path)
        if cached is not None:
            return cached
        file = file_rows.get(path)
        if file is None:
            raise SourceSnapshotError(f"database source file is unavailable: {path}")
        raw = (snapshot.snapshot_root / path).read_bytes()
        value = (file, raw, raw.decode("utf-8", errors="replace"))
        source_cache[path] = value
        return value

    if dbpath in file_rows:
        for name in sorted(tables):
            value = tables[name] if isinstance(tables[name], dict) else {}
            pointer = f"$kTables[{name!r}]"
            table_location = table_spans.get(name)
            if table_location is None:
                raise SourceSnapshotError(
                    f"database table source span is unavailable: {name}"
                )
            table_file, raw, text = source_for(table_location.path)
            src = _bounded_source(
                table_location.path,
                int(table_file["id"]),
                str(table_file["source_commit_sha"]),
                raw,
                text,
                table_location.span.start,
                table_location.span.end,
                pointer,
            )
            cur = conn.execute(
                "INSERT INTO dbschema_tables(repo_id,fact_key,table_name,properties_json,primary_keys_json,source_file_id,source_path,source_commit_sha,source_hash,source_pointer,start_line,end_line,evidence,extractor,extractor_version,resolution_status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    repo_id,
                    _key("table", dbpath, pointer),
                    name,
                    _canon(value),
                    _canon(
                        value.get("primarykey", value.get("primary_key", []))
                        if isinstance(value, dict)
                        else []
                    ),
                    *src.values(),
                    EXTRACTOR,
                    VERSION,
                    "resolved",
                ),
            )
            table_ids[name] = int(cur.lastrowid)
            fields = value.get("db_fieldinfo", {}) if isinstance(value, dict) else {}
            if not isinstance(fields, dict):
                dbfile, raw, text = source_for(dbpath)
                _insert_diag(
                    conn,
                    repo_id,
                    int(dbfile["id"]),
                    dbpath,
                    str(dbfile["source_commit_sha"]),
                    raw,
                    text,
                    "dbschema_dynamic_field_map",
                    pointer,
                    0,
                )
            for field in sorted(fields if isinstance(fields, dict) else {}):
                info = fields[field] if isinstance(fields[field], dict) else {}
                fp = f"{pointer}['db_fieldinfo'][{field!r}]"
                field_location = field_spans.get((name, field))
                if field_location is None:
                    raise SourceSnapshotError(
                        f"database field source span is unavailable: {name}:{field}"
                    )
                field_file, field_raw, field_text = source_for(field_location.path)
                fs = _bounded_source(
                    field_location.path,
                    int(field_file["id"]),
                    str(field_file["source_commit_sha"]),
                    field_raw,
                    field_text,
                    field_location.span.start,
                    field_location.span.end,
                    fp,
                )
                fid = int(
                    conn.execute(
                        "INSERT INTO dbschema_fields(repo_id,fact_key,table_id,table_name,field_name,field_type,properties_json,source_file_id,source_path,source_commit_sha,source_hash,source_pointer,start_line,end_line,evidence,extractor,extractor_version,resolution_status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            repo_id,
                            _key("field", dbpath, fp),
                            table_ids[name],
                            name,
                            field,
                            info.get("type")
                            if isinstance(info, dict)
                            and isinstance(info.get("type"), str)
                            else None,
                            _canon(info),
                            *fs.values(),
                            EXTRACTOR,
                            VERSION,
                            "resolved",
                        ),
                    ).lastrowid
                )
                field_ids[(name, field)] = fid
    occurrence_rows = {
        (str(r["source_file_id"]), str(r["source_key"])): int(r["id"])
        for r in conn.execute(
            "SELECT id,source_file_id,source_key FROM entity_occurrences WHERE repo_id=?",
            (repo_id,),
        )
    }
    for entity in sorted(entities, key=lambda x: (x["path"], x["name"])):
        file = file_rows[entity["path"]]
        occurrence = occurrence_rows.get((str(file["id"]), entity["name"]))
        if occurrence is None:
            continue
        raw = (snapshot.snapshot_root / Path(entity["path"])).read_bytes()
        text = raw.decode("utf-8", errors="replace")
        sha = str(file["source_commit_sha"])
        values = entity["values"]
        for assignment in entity["assignments"]:
            lhs = assignment.path[2:]
            value = assignment.value
            valid = assignment.valid
            start = assignment.start
            end = assignment.end
            evidence = assignment.evidence
            section = str(lhs[0]).lower() if lhs else "entity"
            if section == "[]":
                continue
            pointer = (
                "$kSchemas["
                + repr(entity["name"])
                + "]"
                + "".join("[" + repr(x) + "]" for x in lhs if x != "[]")
            )
            src = _source(
                entity["path"], int(file["id"]), sha, raw, text, start, end, pointer
            )
            literal = "literal" if valid else "dynamic"
            resolution = "resolved" if valid else "unresolved"
            if lhs and lhs[-1] == "[]":
                value = value if valid else None
            if section in SECTIONS or not lhs:
                conn.execute(
                    "INSERT OR IGNORE INTO entity_section_facts(repo_id,fact_key,occurrence_id,section,value_json,literal_status,source_file_id,source_path,source_commit_sha,source_hash,source_pointer,start_line,end_line,evidence,extractor,extractor_version,resolution_status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        repo_id,
                        _key("section", entity["path"], pointer),
                        occurrence,
                        section,
                        _canon(value),
                        literal,
                        int(file["id"]),
                        entity["path"],
                        sha,
                        src["source_hash"],
                        pointer,
                        src["start_line"],
                        src["end_line"],
                        evidence,
                        EXTRACTOR,
                        VERSION,
                        resolution,
                    ),
                )
        for section, section_value in sorted(values.items()):
            section = str(section).lower()
            if section not in SECTIONS:
                continue
            if section in {
                "table",
                "view",
                "module",
                "dummy",
                "parententity",
                "vid",
                "autoincrement",
            }:
                continue
            for field_path, field_value in _walk(section_value):
                pointer = f"$kSchemas[{entity['name']!r}][{section!r}][{field_path!r}]"
                src = _source(
                    entity["path"],
                    int(file["id"]),
                    sha,
                    raw,
                    text,
                    0,
                    len(text),
                    pointer,
                )
                conn.execute(
                    "INSERT OR IGNORE INTO entity_field_facts(repo_id,fact_key,occurrence_id,section,field_path,value_json,literal_status,source_file_id,source_path,source_commit_sha,source_hash,source_pointer,start_line,end_line,evidence,extractor,extractor_version,resolution_status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        repo_id,
                        _key("entity_field", entity["path"], pointer),
                        occurrence,
                        section,
                        field_path,
                        _canon(field_value),
                        "literal",
                        "%s" % int(file["id"]),
                        entity["path"],
                        sha,
                        src["source_hash"],
                        pointer,
                        src["start_line"],
                        src["end_line"],
                        src["evidence"],
                        EXTRACTOR,
                        VERSION,
                        "resolved",
                    ),
                )
            if section == "schema" and isinstance(section_value, dict):
                for field, target in sorted(section_value.items()):
                    target_kind = (
                        "direct_literal"
                        if isinstance(target, str) and "." not in target
                        else "dotted_literal"
                        if isinstance(target, str)
                        else "nested_mapping"
                        if isinstance(target, dict)
                        else "dynamic"
                    )
                    status = "resolved" if isinstance(target, str) else "unresolved"
                    pointer = f"$kSchemas[{entity['name']!r}]['schema'][{field!r}]"
                    src = _source(
                        entity["path"],
                        int(file["id"]),
                        sha,
                        raw,
                        text,
                        0,
                        len(text),
                        pointer,
                    )
                    cur = conn.execute(
                        "INSERT INTO entity_schema_mappings(repo_id,fact_key,occurrence_id,entity_field,target_value_json,target_kind,source_file_id,source_path,source_commit_sha,source_hash,source_pointer,start_line,end_line,evidence,extractor,extractor_version,resolution_status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            repo_id,
                            _key("mapping", entity["path"], pointer),
                            occurrence,
                            str(field),
                            _canon(target),
                            target_kind,
                            *src.values(),
                            EXTRACTOR,
                            VERSION,
                            status,
                        ),
                    )
                    mapping_id = int(cur.lastrowid)
                    target_name = (
                        target.split(".")[-1] if isinstance(target, str) else ""
                    )
                    entity_table = values.get("table") or values.get("view")
                    table_matches = [
                        n
                        for n in table_ids
                        if isinstance(entity_table, str)
                        and n.casefold() == entity_table.strip().casefold()
                    ]
                    if len(table_matches) != 1:
                        continue
                    child_table = table_matches[0]
                    if isinstance(target, str) and "." in target:
                        child = values.get("children", {})
                        child_table = (
                            child.get(target.split(".")[0], {}).get("table")
                            if isinstance(child, dict)
                            and isinstance(child.get(target.split(".")[0]), dict)
                            else None
                        )
                    dbfield = (
                        field_ids.get((child_table, target_name))
                        if child_table
                        else None
                    )
                    link_status = "resolved" if dbfield else "unresolved"
                    link_type = "schema_literal"
                    link_pointer = pointer + ":link"
                    ls = _source(
                        entity["path"],
                        int(file["id"]),
                        sha,
                        raw,
                        text,
                        0,
                        len(text),
                        link_pointer,
                    )
                    conn.execute(
                        "INSERT INTO entity_db_field_links(repo_id,fact_key,occurrence_id,schema_mapping_id,db_field_id,entity_field,target_field,link_type,source_file_id,source_path,source_commit_sha,source_hash,source_pointer,start_line,end_line,evidence,extractor,extractor_version,resolution_status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            repo_id,
                            _key("field_link", entity["path"], link_pointer),
                            occurrence,
                            mapping_id,
                            dbfield,
                            str(field),
                            target_name,
                            link_type,
                            *ls.values(),
                            EXTRACTOR,
                            VERSION,
                            link_status,
                        ),
                    )
                    if not dbfield:
                        _insert_diag(
                            conn,
                            repo_id,
                            int(file["id"]),
                            entity["path"],
                            sha,
                            raw,
                            text,
                            "entity_schema_mapping_missing_db_field",
                            pointer,
                            0,
                        )
        entity_table = values.get("table") or values.get("view")
        if isinstance(entity_table, str):
            matches = [
                n for n in table_ids if n.casefold() == entity_table.strip().casefold()
            ]
            status = (
                "resolved"
                if len(matches) == 1
                else "ambiguous"
                if len(matches) > 1
                else "unresolved"
            )
            tid = table_ids[matches[0]] if len(matches) == 1 else None
            pointer = f"$kSchemas[{entity['name']!r}]['table/view']"
            src = _source(
                entity["path"], int(file["id"]), sha, raw, text, 0, len(text), pointer
            )
            conn.execute(
                "INSERT INTO entity_db_table_links(repo_id,fact_key,occurrence_id,db_table_id,entity_table,link_type,source_file_id,source_path,source_commit_sha,source_hash,source_pointer,start_line,end_line,evidence,extractor,extractor_version,resolution_status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    repo_id,
                    _key("table_link", entity["path"], pointer),
                    occurrence,
                    tid,
                    entity_table,
                    "entity_table",
                    *src.values(),
                    EXTRACTOR,
                    VERSION,
                    status,
                ),
            )
    return DatabaseExtractionStats(
        *(
            int(
                conn.execute(
                    f"SELECT COUNT(*) FROM {t} WHERE repo_id=?", (repo_id,)
                ).fetchone()[0]
            )
            for t in (
                "dbschema_tables",
                "dbschema_fields",
                "entity_section_facts",
                "entity_field_facts",
                "entity_schema_mappings",
                "entity_db_table_links",
                "entity_db_field_links",
                "repo_v1_database_diagnostics",
            )
        )
    )


def validate_database_candidate(
    conn: sqlite3.Connection, *, repo_id: int, target_commit_sha: str
) -> None:
    for table in (
        "dbschema_tables",
        "dbschema_fields",
        "entity_section_facts",
        "entity_field_facts",
        "entity_schema_mappings",
        "entity_db_table_links",
        "entity_db_field_links",
        "repo_v1_database_diagnostics",
    ):
        bad = conn.execute(
            f"""SELECT COUNT(*)
                FROM {table} x
                JOIN repos r ON r.id=x.repo_id
                LEFT JOIN files f
                  ON f.repo_id=x.repo_id AND f.id=x.source_file_id
                WHERE x.repo_id<>?
                   OR r.target_commit_sha<>?
                   OR x.source_commit_sha<>?
                   OR x.source_path=''
                   OR x.evidence=''
                   OR x.extractor<>?
                   OR f.id IS NULL
                   OR f.path<>x.source_path
                   OR f.source_commit_sha<>x.source_commit_sha
                   OR length(x.source_hash)<>64
                   OR lower(x.source_hash) GLOB '*[^0-9a-f]*'
                   OR x.start_line<1
                   OR x.end_line<x.start_line""",
            (repo_id, target_commit_sha, target_commit_sha, EXTRACTOR),
        ).fetchone()[0]
        if bad:
            raise RuntimeError(f"database candidate provenance is invalid for {table}")
    for row in conn.execute(
        "SELECT table_name,fact_key,source_path,source_pointer FROM dbschema_tables WHERE repo_id=?",
        (repo_id,),
    ):
        expected_pointer = f"$kTables[{row[0]!r}]"
        expected_key = _key("table", "app/source/common/dbschema.inc", expected_pointer)
        if row[1] != expected_key or row[3] != expected_pointer:
            raise RuntimeError("database table fact identity is invalid")
    for row in conn.execute(
        """SELECT f.table_name,f.field_name,f.fact_key,f.source_path,f.source_pointer,
                         t.table_name
           FROM dbschema_fields f
           JOIN dbschema_tables t
             ON t.repo_id=f.repo_id AND t.id=f.table_id
           WHERE f.repo_id=?""",
        (repo_id,),
    ):
        expected_pointer = f"$kTables[{row[0]!r}]['db_fieldinfo'][{row[1]!r}]"
        expected_key = _key("field", "app/source/common/dbschema.inc", expected_pointer)
        if row[0] != row[5] or row[2] != expected_key or row[4] != expected_pointer:
            raise RuntimeError("database field fact identity is invalid")
    invalid = conn.execute(
        "SELECT COUNT(*) FROM entity_db_field_links WHERE (resolution_status='resolved' AND db_field_id IS NULL) OR resolution_status NOT IN ('resolved','unresolved','ambiguous','unsupported')"
    ).fetchone()[0]
    if invalid:
        raise RuntimeError("database candidate link status is invalid")
