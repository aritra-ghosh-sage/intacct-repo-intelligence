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


def _value(tokens: list[Any], start: int = 0) -> tuple[Any, int, bool]:
    if start >= len(tokens):
        return None, start, False
    token = tokens[start]
    if token.kind == "string":
        return (
            (token.value if token.valid and not token.interpolation else None),
            start + 1,
            bool(token.valid and not token.interpolation),
        )
    if token.kind == "number":
        return (
            (float(token.value) if "." in str(token.value) else int(str(token.value))),
            start + 1,
            True,
        )
    if token.kind == "ident" and str(token.value).lower() in {"true", "false", "null"}:
        return (
            ({"true": True, "false": False, "null": None}[str(token.value).lower()]),
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
    static = True
    cursor = opener + 1
    index = 0
    while cursor < close:
        if tokens[cursor].value == ",":
            cursor += 1
            continue
        key: str | None = None
        value_start = cursor
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
        value, next_cursor, valid = _value(tokens, value_start)
        if next_cursor <= value_start:
            return None, close + 1, False
        static = static and valid
        if key is None:
            positional.append(value)
            index += 1
        else:
            result[key] = value
        cursor = next_cursor
        if cursor < close and tokens[cursor].value == ",":
            cursor += 1
    if result and positional:
        for i, value in enumerate(positional):
            result[str(i)] = value
        return result, close + 1, static
    return (result if result else positional), close + 1, static


def _assignments(text: str) -> list[tuple[list[str], Any, bool, int, int, str]]:
    tokens, lexical = _lex(text)
    states, delimiter = _delimiter_state(tokens)
    if lexical is not None or delimiter is not None:
        return []
    out: list[tuple[list[str], Any, bool, int, int, str]] = []
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
        value, _, valid = _value(rhs)
        start = token.start
        finish = (
            tokens[end].end
            if end < len(tokens)
            else (rhs[-1].end if rhs else token.end)
        )
        out.append((path, value, valid, start, finish, text[start:finish]))
    return out


def _walk(value: Any, prefix: str = "") -> list[tuple[str, Any]]:
    if isinstance(value, dict):
        result: list[tuple[str, Any]] = []
        for key in sorted(value):
            result.extend(_walk(value[key], f"{prefix}.{key}" if prefix else str(key)))
        return result
    return [(prefix, value)]


def _merge_entity_assignment(values: dict[str, Any], path: list[str], value: Any) -> None:
    """Apply a source-shaped kSchemas assignment to the normalized tree."""
    if not path:
        if isinstance(value, dict):
            values.update(value)
        return
    section = path[0]
    if len(path) == 1:
        if section in values and isinstance(values[section], dict) and isinstance(value, dict):
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
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    files = {
        str(r["path"]): r
        for r in conn.execute("SELECT * FROM files WHERE repo_id=?", (repo_id,))
    }
    assignments: dict[str, list[tuple[list[str], Any, bool, int, int, str]]] = {}
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
    entities: list[dict[str, Any]] = []
    for path, values in assignments.items():
        for lhs, value, valid, start, end, evidence in values:
            if lhs[0] == "kTables" and valid and isinstance(value, dict):
                tables.update(value)
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
                item["assignments"].append(
                    (lhs[2:], value, valid, start, end, evidence)
                )
                if valid:
                    _merge_entity_assignment(item["values"], lhs[2:], value)
    return tables, entities


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
    tables, entities = _facts(snapshot, conn, repo_id)
    file_rows = {
        str(r["path"]): r
        for r in conn.execute("SELECT * FROM files WHERE repo_id=?", (repo_id,))
    }
    table_ids: dict[str, int] = {}
    field_ids: dict[tuple[str, str], int] = {}
    dbpath = "app/source/common/dbschema.inc"
    dbfile = file_rows.get(dbpath)
    if dbfile is not None:
        raw = (snapshot.snapshot_root / dbpath).read_bytes()
        text = raw.decode("utf-8", errors="replace")
        sha = str(dbfile["source_commit_sha"])
        for name in sorted(tables):
            value = tables[name] if isinstance(tables[name], dict) else {}
            pointer = f"$kTables[{name!r}]"
            src = _source(
                dbpath, int(dbfile["id"]), sha, raw, text, 0, len(text), pointer
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
                _insert_diag(
                    conn,
                    repo_id,
                    int(dbfile["id"]),
                    dbpath,
                    sha,
                    raw,
                    text,
                    "dbschema_dynamic_field_map",
                    pointer,
                    0,
                )
            for field in sorted(fields if isinstance(fields, dict) else {}):
                info = fields[field] if isinstance(fields[field], dict) else {}
                fp = f"{pointer}['db_fieldinfo'][{field!r}]"
                fs = _source(
                    dbpath, int(dbfile["id"]), sha, raw, text, 0, len(text), fp
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
        for lhs, value, valid, start, end, evidence in entity["assignments"]:
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
            f"SELECT COUNT(*) FROM {table} x JOIN repos r ON r.id=x.repo_id WHERE x.repo_id<>? OR x.source_commit_sha<>? OR x.source_path='' OR x.evidence='' OR x.extractor<>?",
            (repo_id, target_commit_sha, EXTRACTOR),
        ).fetchone()[0]
        if bad:
            raise RuntimeError(f"database candidate provenance is invalid for {table}")
    invalid = conn.execute(
        "SELECT COUNT(*) FROM entity_db_field_links WHERE (resolution_status='resolved' AND db_field_id IS NULL) OR resolution_status NOT IN ('resolved','unresolved','ambiguous','unsupported')"
    ).fetchone()[0]
    if invalid:
        raise RuntimeError("database candidate link status is invalid")
