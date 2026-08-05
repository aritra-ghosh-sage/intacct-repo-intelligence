#!/usr/bin/env python3

"""
Phase 2.1 Relationship Extraction MVP.

Extracts structural relationships from already-scanned files and symbol catalog.

Initial relationship types:
- PHP:
    INHERITS
    IMPLEMENTS
    IMPORTS
    USES
    REFERENCES
    STATIC_CALLS
- Java:
    INHERITS
    IMPLEMENTS
    IMPORTS
    REFERENCES
    CALLS
- XML:
    REFERENCES
- JS:
    IMPORTS
    REFERENCES
    CALLS

This script does not require perfect AST accuracy.
It creates a useful first graph for dependency and reverse-dependency queries.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tqdm import tqdm

try:
    import yaml
except ModuleNotFoundError:
    yaml = None

from catalog.db import get_connection
from parser.repo_context import require_repo_scoped_files, resolve_repo

RELATIONSHIP_EXTRACTOR = "phase2_regex_mvp"


REL_INHERITS = "INHERITS"
REL_IMPLEMENTS = "IMPLEMENTS"
REL_IMPORTS = "IMPORTS"
REL_USES = "USES"
REL_REFERENCES = "REFERENCES"
REL_CALLS = "CALLS"
REL_STATIC_CALLS = "STATIC_CALLS"
REL_DECLARES = "DECLARES"
REL_CONTAINS = "CONTAINS"
REL_IMPLEMENTS_OPERATION = "IMPLEMENTS_OPERATION"
REL_EXPOSES_PATH = "EXPOSES_PATH"

YAML_REL_STATS = {
    "files_seen": 0,
    "parse_failures": 0,
    "relationships_emitted": 0,
}


def reset_yaml_rel_stats() -> None:
    YAML_REL_STATS["files_seen"] = 0
    YAML_REL_STATS["parse_failures"] = 0
    YAML_REL_STATS["relationships_emitted"] = 0


def get_yaml_rel_stats() -> dict[str, int]:
    return {
        "files_seen": int(YAML_REL_STATS["files_seen"]),
        "parse_failures": int(YAML_REL_STATS["parse_failures"]),
        "relationships_emitted": int(YAML_REL_STATS["relationships_emitted"]),
    }


RESOLUTION_CLASS_PROJECT_RESOLVED = "project_resolved"
RESOLUTION_CLASS_PROJECT_UNRESOLVED = "project_unresolved"
RESOLUTION_CLASS_BUILTIN = "builtin"
RESOLUTION_CLASS_EXTERNAL = "external"
RESOLUTION_CLASS_HEURISTIC = "heuristic"

BASIC_REFERENCE_TOKENS = {"self", "parent", "static", "this", "super"}
BUILTIN_CALL_PREFIXES = (
    "Math.",
    "Object.",
    "JSON.",
    "Array.",
    "String.",
    "Number.",
    "Date.",
    "console.",
    "Promise.",
    "Reflect.",
)
BUILTIN_STATIC_PREFIXES = (
    "self::assert",
    "self::once",
    "self::exactly",
    "self::never",
)
BUILTIN_TYPES = {
    "Exception",
    "Throwable",
    "RuntimeException",
    "InvalidArgumentException",
}

SUPPORTED_EXTENSIONS = {
    ".php": "php",
    ".phtml": "php",
    ".cls": "php",
    ".inc": "php",
    ".ent": "php",
    ".cqry": "php",
    ".qry": "php",
    ".java": "java",
    ".xml": "xml",
    ".xsl": "xslt",
    ".yaml": "yaml",
    ".html": "html",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".sql": "sql",
    ".rpt": "php",
}


@dataclass
class FileRow:
    id: int
    path: str
    language: str


@dataclass
class SymbolRow:
    id: int
    name: str
    kind: str
    parent_symbol: str | None
    file_id: int | None
    file_path: str | None


@dataclass
class Relationship:
    source_symbol_id: int | None
    source_name: str | None
    source_kind: str | None
    target_symbol_id: int | None
    target_name: str
    target_kind: str | None
    relationship_type: str
    file_id: int | None
    file_path: str
    language: str
    confidence: float
    evidence: str
    resolution_class: str
    resolution_reason: str


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}


def pick_col(columns: set[str], candidates: list[str]) -> str | None:
    for c in candidates:
        if c in columns:
            return c
    return None


def load_files(
    conn: sqlite3.Connection,
    repo_id: int,
    only_changed: bool,
    languages: list[str],
    limit: int | None,
    file_filter: str | None,
    file_ids: Iterable[int] | None = None,
) -> list[FileRow]:
    placeholders = ",".join(["?"] * len(languages))
    params: list[object] = [repo_id, *languages]

    where = ["repo_id = ?", f"language IN ({placeholders})"]
    if only_changed:
        where.append(
            "(last_relationships_extracted IS NULL OR last_indexed > last_relationships_extracted)"
        )

    if file_filter:
        where.append("path LIKE ?")
        params.append(f"%{file_filter}%")
    if file_ids is not None:
        normalized_ids = sorted({int(file_id) for file_id in file_ids})
        if not normalized_ids:
            return []
        where.append("id IN (" + ",".join("?" for _ in normalized_ids) + ")")
        params.extend(normalized_ids)

    sql = f"""
        SELECT id, path, language
        FROM files
        WHERE {" AND ".join(where)}
        ORDER BY id
    """

    if limit:
        sql += " LIMIT ?"
        params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    out: list[FileRow] = []
    for row in rows:
        path = row["path"]
        ext = Path(path).suffix.lower()
        language = row["language"] or SUPPORTED_EXTENSIONS.get(ext, "unknown")
        out.append(FileRow(id=row["id"], path=path, language=language))

    return out


def load_symbols(
    conn: sqlite3.Connection,
    repo_id: int,
) -> tuple[
    dict[str, list[SymbolRow]], dict[int, list[SymbolRow]], dict[str, list[SymbolRow]]
]:
    cols = table_columns(conn, "symbols")

    id_col = pick_col(cols, ["id", "symbol_id"])
    name_col = pick_col(cols, ["name", "symbol_name"])
    kind_col = pick_col(cols, ["kind", "symbol_kind", "type"])
    parent_col = pick_col(cols, ["parent_symbol"])
    file_id_col = pick_col(cols, ["file_id"])
    file_path_col = pick_col(cols, ["file_path", "path", "relative_path"])

    if not id_col or not name_col:
        raise RuntimeError(f"Cannot infer symbols table columns. Found: {sorted(cols)}")

    sql = f"""
        SELECT
            s.{id_col} AS id,
            s.{name_col} AS name,
            {f"s.{kind_col}" if kind_col else "'unknown'"} AS kind,
            {f"s.{parent_col}" if parent_col else "NULL"} AS parent_symbol,
            {f"s.{file_id_col}" if file_id_col else "NULL"} AS file_id,
            {f"s.{file_path_col}" if file_path_col else "NULL"} AS file_path
        FROM symbols s
        JOIN files f ON f.id = s.{file_id_col}
        WHERE s.{name_col} IS NOT NULL
          AND f.repo_id = ?
    """

    by_name: dict[str, list[SymbolRow]] = {}
    by_file: dict[int, list[SymbolRow]] = {}
    by_qualified_name: dict[str, list[SymbolRow]] = {}

    for r in conn.execute(sql, (repo_id,)).fetchall():
        s = SymbolRow(
            id=r["id"],
            name=r["name"],
            kind=r["kind"] or "unknown",
            parent_symbol=r["parent_symbol"],
            file_id=r["file_id"],
            file_path=r["file_path"],
        )
        by_name.setdefault(s.name, []).append(s)
        if s.parent_symbol:
            by_qualified_name.setdefault(f"{s.parent_symbol}::{s.name}", []).append(s)
            by_qualified_name.setdefault(f"{s.parent_symbol}.{s.name}", []).append(s)
            short_parent = s.parent_symbol.split("\\")[-1].split(".")[-1]
            if short_parent != s.parent_symbol:
                by_qualified_name.setdefault(f"{short_parent}::{s.name}", []).append(s)
                by_qualified_name.setdefault(f"{short_parent}.{s.name}", []).append(s)
        if s.file_id is not None:
            by_file.setdefault(s.file_id, []).append(s)

    return by_name, by_file, by_qualified_name


def resolve_symbol(
    symbols_by_name: dict[str, list[SymbolRow]],
    symbols_by_qualified_name: dict[str, list[SymbolRow]],
    name: str,
    source_name: str | None = None,
) -> SymbolRow | None:
    if not name:
        return None

    candidates = symbols_by_name.get(name)
    if candidates:
        return prefer_symbol(candidates)

    qualified_candidates = symbols_by_qualified_name.get(name)
    if qualified_candidates:
        return prefer_symbol(qualified_candidates)

    for sep in ("::", "."):
        if sep not in name:
            continue

        class_name, member_name = name.split(sep, 1)
        class_name = class_name.strip()
        member_name = member_name.strip()
        if not class_name or not member_name:
            continue

        candidate_class_names = [class_name]
        short_class_name = class_name.split("\\")[-1].split(".")[-1]
        if short_class_name != class_name:
            candidate_class_names.append(short_class_name)
        if class_name in {"self", "static"} and source_name:
            candidate_class_names.append(source_name)

        for class_candidate in candidate_class_names:
            for candidate_sep in ("::", "."):
                qualified_name = f"{class_candidate}{candidate_sep}{member_name}"
                qualified_candidates = symbols_by_qualified_name.get(qualified_name)
                if qualified_candidates:
                    return prefer_symbol(qualified_candidates)

    short_name = name.split("\\")[-1].split(".")[-1]
    candidates = symbols_by_name.get(short_name)
    if candidates:
        return prefer_symbol(candidates)

    return None


def prefer_symbol(candidates: list[SymbolRow]) -> SymbolRow:
    # Prefer class/interface/table/entity-like symbols over methods for high-level edges.
    priority = {
        "class": 1,
        "interface": 2,
        "trait": 3,
        "enum": 4,
        "table": 5,
        "function": 6,
        "method": 7,
    }

    return min(candidates, key=lambda s: priority.get(str(s.kind).lower(), 99))


def pick_source_symbol(
    symbols_by_file: dict[int, list[SymbolRow]],
    file_id: int,
    preferred_name: str | None = None,
) -> tuple[int | None, str | None, str | None]:
    symbols = symbols_by_file.get(file_id, [])

    if preferred_name:
        for s in symbols:
            if s.name == preferred_name:
                return s.id, s.name, s.kind

    # Prefer class-like source symbol. If not found, use first symbol.
    class_like = [
        s
        for s in symbols
        if str(s.kind).lower() in {"class", "interface", "trait", "enum"}
    ]
    if class_like:
        s = class_like[0]
        return s.id, s.name, s.kind

    if symbols:
        s = symbols[0]
        return s.id, s.name, s.kind

    return None, None, None


def read_file_text(repo_root: Path, file_path: str) -> str | None:
    p = Path(file_path)
    if not p.is_absolute():
        p = repo_root / file_path

    try:
        return p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None


def normalize_target_name(name: str) -> str:
    name = name.strip()
    name = name.strip("\\")
    name = name.strip(";")
    return name


def looks_builtin(target_name: str, relationship_type: str) -> bool:
    if not target_name:
        return False

    if target_name in BUILTIN_TYPES:
        return True

    if relationship_type == REL_CALLS:
        return target_name.startswith(BUILTIN_CALL_PREFIXES)

    if relationship_type == REL_STATIC_CALLS:
        return target_name.startswith(BUILTIN_STATIC_PREFIXES)

    return False


def classify_relationship(
    relationship_type: str,
    target_name: str,
    target_symbol_id: int | None,
) -> tuple[str, str]:
    if target_symbol_id is not None:
        return RESOLUTION_CLASS_PROJECT_RESOLVED, "target_symbol_id_present"

    normalized_target = normalize_target_name(target_name)
    lower_target = normalized_target.lower()

    if relationship_type == REL_REFERENCES and lower_target in BASIC_REFERENCE_TOKENS:
        return RESOLUTION_CLASS_HEURISTIC, "generic_reference_token"

    if looks_builtin(normalized_target, relationship_type):
        return RESOLUTION_CLASS_BUILTIN, "builtin_symbol_pattern"

    if relationship_type == REL_IMPORTS and (
        "/" in normalized_target or normalized_target.startswith("@")
    ):
        return RESOLUTION_CLASS_EXTERNAL, "module_path_import"

    return RESOLUTION_CLASS_PROJECT_UNRESOLVED, "unresolved_project_symbol"


def ensure_relationship_classification_columns(conn: sqlite3.Connection) -> None:
    cols = table_columns(conn, "relationships")

    if "resolution_class" not in cols:
        conn.execute("ALTER TABLE relationships ADD COLUMN resolution_class TEXT")

    if "resolution_reason" not in cols:
        conn.execute("ALTER TABLE relationships ADD COLUMN resolution_reason TEXT")

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_relationships_resolution_class
        ON relationships(resolution_class)
    """)
    conn.commit()


def ensure_relationship_tracking_schema(conn: sqlite3.Connection) -> None:
    file_cols = table_columns(conn, "files")
    if "last_relationships_extracted" not in file_cols:
        conn.execute("ALTER TABLE files ADD COLUMN last_relationships_extracted TEXT")
    conn.commit()


def make_rel(
    rel_type: str,
    target_name: str,
    file_row: FileRow,
    source: tuple[int | None, str | None, str | None],
    symbols_by_name: dict[str, list[SymbolRow]],
    symbols_by_qualified_name: dict[str, list[SymbolRow]],
    evidence: str,
    confidence: float = 0.7,
    target_kind_hint: str | None = None,
) -> Relationship:
    target_name = normalize_target_name(target_name)
    target = resolve_symbol(
        symbols_by_name,
        symbols_by_qualified_name,
        target_name,
        source_name=source[1],
    )
    resolution_class, resolution_reason = classify_relationship(
        rel_type,
        target_name,
        target.id if target else None,
    )

    normalized_target_kind: str | None
    if target is not None:
        resolved_kind = str(target.kind or "").strip().lower()
        if resolved_kind in {"cqry", "qry"}:
            normalized_target_kind = "query"
        elif resolved_kind in {"unknown", ""}:
            normalized_target_kind = None
        else:
            normalized_target_kind = target.kind
    else:
        hint = str(target_kind_hint or "").strip().lower()
        if hint in {"unknown", ""}:
            normalized_target_kind = None
        elif hint in {"cqry", "qry"}:
            normalized_target_kind = "query"
        else:
            normalized_target_kind = target_kind_hint

    return Relationship(
        source_symbol_id=source[0],
        source_name=source[1],
        source_kind=source[2],
        target_symbol_id=target.id if target else None,
        target_name=target.name if target else target_name,
        target_kind=normalized_target_kind,
        relationship_type=rel_type,
        file_id=file_row.id,
        file_path=file_row.path,
        language=file_row.language,
        confidence=confidence,
        evidence=evidence[:500],
        resolution_class=resolution_class,
        resolution_reason=resolution_reason,
    )


def extract_php(
    text: str,
    file_row: FileRow,
    symbols_by_name: dict[str, list[SymbolRow]],
    symbols_by_file: dict[int, list[SymbolRow]],
    symbols_by_qualified_name: dict[str, list[SymbolRow]],
) -> list[Relationship]:

    rels: list[Relationship] = []
    class_declarations: list[
        tuple[int, str, str | None, tuple[int | None, str | None, str | None]]
    ] = []

    # class Foo extends Bar implements A, B
    class_pattern = re.compile(
        r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)"
        r"(?:\s+extends\s+([\\A-Za-z_][\\A-Za-z0-9_]*))?"
        r"(?:\s+implements\s+([\\A-Za-z_][\\A-Za-z0-9_,\s]*))?",
        re.MULTILINE,
    )

    for m in class_pattern.finditer(text):
        class_name = m.group(1)
        parent_class = normalize_target_name(m.group(2)) if m.group(2) else None
        source = pick_source_symbol(symbols_by_file, file_row.id, class_name)
        class_declarations.append((m.start(), class_name, parent_class, source))

        if m.group(2):
            rels.append(
                make_rel(
                    REL_INHERITS,
                    m.group(2),
                    file_row,
                    source,
                    symbols_by_name,
                    symbols_by_qualified_name,
                    evidence=m.group(0),
                    confidence=0.95,
                    target_kind_hint="class",
                )
            )

        if m.group(3):
            for iface in m.group(3).split(","):
                iface = iface.strip()
                if iface:
                    rels.append(
                        make_rel(
                            REL_IMPLEMENTS,
                            iface,
                            file_row,
                            source,
                            symbols_by_name,
                            symbols_by_qualified_name,
                            evidence=m.group(0),
                            confidence=0.95,
                            target_kind_hint="interface",
                        )
                    )

    source = pick_source_symbol(symbols_by_file, file_row.id)

    def source_for_offset(
        offset: int,
    ) -> tuple[tuple[int | None, str | None, str | None], str | None]:
        for start, class_name, parent_class, class_source in reversed(
            class_declarations
        ):
            if offset >= start:
                return class_source, parent_class
        return source, None

    # use Namespace\ClassName;
    for m in re.finditer(
        r"^\s*use\s+([\\A-Za-z_][\\A-Za-z0-9_]*(?:\s+as\s+[A-Za-z_][A-Za-z0-9_]*)?)\s*;",
        text,
        re.MULTILINE,
    ):
        raw = m.group(1)
        target = raw.split(" as ")[0].strip()
        rels.append(
            make_rel(
                REL_IMPORTS,
                target,
                file_row,
                source,
                symbols_by_name,
                symbols_by_qualified_name,
                evidence=m.group(0),
                confidence=0.9,
                target_kind_hint="class",
            )
        )

    # new ClassName(
    for m in re.finditer(r"\bnew\s+([\\A-Za-z_][\\A-Za-z0-9_]*)\s*\(", text):
        target = m.group(1)
        rels.append(
            make_rel(
                REL_USES,
                target,
                file_row,
                source,
                symbols_by_name,
                symbols_by_qualified_name,
                evidence=m.group(0),
                confidence=0.75,
                target_kind_hint="class",
            )
        )

    # ClassName::method(
    for m in re.finditer(
        r"\b([A-Za-z_][A-Za-z0-9_]*)::([A-Za-z_][A-Za-z0-9_]*)\s*\(", text
    ):
        target_class = m.group(1)
        method = m.group(2)
        call_source, parent_class = source_for_offset(m.start())
        static_target = f"{target_class}::{method}"
        reference_target = target_class

        # Conservative parent:: resolution: only remap when the parent method can be resolved.
        if target_class == "parent" and parent_class:
            parent_target = f"{parent_class}::{method}"
            parent_symbol = resolve_symbol(
                symbols_by_name,
                symbols_by_qualified_name,
                parent_target,
                source_name=call_source[1],
            )
            if parent_symbol:
                static_target = parent_target
                reference_target = parent_class

        rels.append(
            make_rel(
                REL_STATIC_CALLS,
                static_target,
                file_row,
                call_source,
                symbols_by_name,
                symbols_by_qualified_name,
                evidence=m.group(0),
                confidence=0.65,
                target_kind_hint="method",
            )
        )
        rels.append(
            make_rel(
                REL_REFERENCES,
                reference_target,
                file_row,
                call_source,
                symbols_by_name,
                symbols_by_qualified_name,
                evidence=m.group(0),
                confidence=0.7,
                target_kind_hint="class",
            )
        )

    return rels


def extract_java(
    text: str,
    file_row: FileRow,
    symbols_by_name: dict[str, list[SymbolRow]],
    symbols_by_file: dict[int, list[SymbolRow]],
    symbols_by_qualified_name: dict[str, list[SymbolRow]],
) -> list[Relationship]:

    rels: list[Relationship] = []

    class_pattern = re.compile(
        r"\b(?:public|private|protected|abstract|final|\s)*\s*"
        r"(class|interface|enum)\s+([A-Za-z_][A-Za-z0-9_]*)"
        r"(?:\s+extends\s+([A-Za-z_][A-Za-z0-9_\.]*))?"
        r"(?:\s+implements\s+([A-Za-z_][A-Za-z0-9_\.,\s]*))?",
        re.MULTILINE,
    )

    for m in class_pattern.finditer(text):
        class_name = m.group(2)
        source = pick_source_symbol(symbols_by_file, file_row.id, class_name)

        if m.group(3):
            rels.append(
                make_rel(
                    REL_INHERITS,
                    m.group(3),
                    file_row,
                    source,
                    symbols_by_name,
                    symbols_by_qualified_name,
                    evidence=m.group(0),
                    confidence=0.95,
                    target_kind_hint="class",
                )
            )

        if m.group(4):
            for iface in m.group(4).split(","):
                iface = iface.strip()
                if iface:
                    rels.append(
                        make_rel(
                            REL_IMPLEMENTS,
                            iface,
                            file_row,
                            source,
                            symbols_by_name,
                            symbols_by_qualified_name,
                            evidence=m.group(0),
                            confidence=0.95,
                            target_kind_hint="interface",
                        )
                    )

    source = pick_source_symbol(symbols_by_file, file_row.id)

    # import com.foo.Bar;
    for m in re.finditer(
        r"^\s*import\s+([A-Za-z_][A-Za-z0-9_\.]*)(?:\.\*)?\s*;", text, re.MULTILINE
    ):
        target = m.group(1).split(".")[-1]
        rels.append(
            make_rel(
                REL_IMPORTS,
                target,
                file_row,
                source,
                symbols_by_name,
                symbols_by_qualified_name,
                evidence=m.group(0),
                confidence=0.85,
                target_kind_hint="class",
            )
        )

    # new ClassName(
    for m in re.finditer(r"\bnew\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", text):
        target = m.group(1)
        rels.append(
            make_rel(
                REL_USES,
                target,
                file_row,
                source,
                symbols_by_name,
                symbols_by_qualified_name,
                evidence=m.group(0),
                confidence=0.75,
                target_kind_hint="class",
            )
        )

    # ClassName.method(
    for m in re.finditer(
        r"\b([A-Z][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\s*\(", text
    ):
        target_class = m.group(1)
        method = m.group(2)
        rels.append(
            make_rel(
                REL_CALLS,
                f"{target_class}.{method}",
                file_row,
                source,
                symbols_by_name,
                symbols_by_qualified_name,
                evidence=m.group(0),
                confidence=0.6,
                target_kind_hint="method",
            )
        )
        rels.append(
            make_rel(
                REL_REFERENCES,
                target_class,
                file_row,
                source,
                symbols_by_name,
                symbols_by_qualified_name,
                evidence=m.group(0),
                confidence=0.65,
                target_kind_hint="class",
            )
        )

    return rels


def extract_xml(
    text: str,
    file_row: FileRow,
    symbols_by_name: dict[str, list[SymbolRow]],
    symbols_by_file: dict[int, list[SymbolRow]],
    symbols_by_qualified_name: dict[str, list[SymbolRow]],
) -> list[Relationship]:

    rels: list[Relationship] = []
    source = pick_source_symbol(symbols_by_file, file_row.id)

    # Conservative: capture class-like / manager-like references in XML attributes/text.
    patterns = [
        r"\b([A-Za-z_][A-Za-z0-9_]*(?:Manager|Controller|Service|Validator|Factory))\b",
        r'\b(entity|object|table|class|model|handler|manager)\s*=\s*"([^"]+)"',
        r"\b(entity|object|table|class|model|handler|manager)\s*=\s*'([^']+)'",
    ]

    for pat in patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            if len(m.groups()) >= 2:
                target = m.group(2)
            else:
                target = m.group(1)

            if target and len(target) > 2:
                rels.append(
                    make_rel(
                        REL_REFERENCES,
                        target,
                        file_row,
                        source,
                        symbols_by_name,
                        symbols_by_qualified_name,
                        evidence=m.group(0),
                        confidence=0.55,
                        target_kind_hint="unknown",
                    )
                )

    return rels


def extract_js_ts(
    text: str,
    file_row: FileRow,
    symbols_by_name: dict[str, list[SymbolRow]],
    symbols_by_file: dict[int, list[SymbolRow]],
    symbols_by_qualified_name: dict[str, list[SymbolRow]],
) -> list[Relationship]:

    rels: list[Relationship] = []
    source = pick_source_symbol(symbols_by_file, file_row.id)

    # import X from '...'
    for m in re.finditer(r"^\s*import\s+(.+?)\s+from\s+.+?['\"]", text, re.MULTILINE):
        imported = m.group(1).strip()
        rels.append(
            make_rel(
                REL_IMPORTS,
                imported,
                file_row,
                source,
                symbols_by_name,
                symbols_by_qualified_name,
                evidence=m.group(0),
                confidence=0.65,
                target_kind_hint="module",
            )
        )

    # require('...')
    for m in re.finditer(r"\brequire\s*\(\s*['\"]([^'\"]+)['\"]\s*\)", text):
        rels.append(
            make_rel(
                REL_IMPORTS,
                m.group(1),
                file_row,
                source,
                symbols_by_name,
                symbols_by_qualified_name,
                evidence=m.group(0),
                confidence=0.65,
                target_kind_hint="module",
            )
        )

    # ClassName.method(
    for m in re.finditer(
        r"\b([A-Z][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\s*\(", text
    ):
        rels.append(
            make_rel(
                REL_CALLS,
                f"{m.group(1)}.{m.group(2)}",
                file_row,
                source,
                symbols_by_name,
                symbols_by_qualified_name,
                evidence=m.group(0),
                confidence=0.55,
                target_kind_hint="method",
            )
        )

    return rels


def extract_yaml(
    text: str,
    file_row: FileRow,
    symbols_by_name: dict[str, list[SymbolRow]],
    symbols_by_file: dict[int, list[SymbolRow]],
    symbols_by_qualified_name: dict[str, list[SymbolRow]],
) -> list[Relationship]:
    YAML_REL_STATS["files_seen"] += 1

    if yaml is None:
        YAML_REL_STATS["parse_failures"] += 1
        return []

    try:
        doc = yaml.safe_load(text)
    except Exception:
        YAML_REL_STATS["parse_failures"] += 1
        return []

    if not isinstance(doc, dict):
        return []

    rels: list[Relationship] = []
    source = pick_source_symbol(symbols_by_file, file_row.id)

    seen: set[tuple[str, str, str]] = set()

    def add_rel(
        rel_type: str,
        target_name: str,
        evidence: str,
        confidence: float,
        target_kind_hint: str | None = None,
    ) -> None:
        key = (rel_type, normalize_target_name(target_name), evidence)
        if key in seen:
            return
        seen.add(key)
        rels.append(
            make_rel(
                rel_type,
                target_name,
                file_row,
                source,
                symbols_by_name,
                symbols_by_qualified_name,
                evidence=evidence,
                confidence=confidence,
                target_kind_hint=target_kind_hint,
            )
        )

    for top_key in doc.keys():
        add_rel(
            REL_DECLARES,
            str(top_key),
            evidence=f"top-level:{top_key}",
            confidence=0.8,
            target_kind_hint="yaml_keyspace",
        )

    paths = doc.get("paths")
    if isinstance(paths, dict):
        for endpoint, methods in paths.items():
            if not isinstance(methods, dict):
                continue
            for method in methods.keys():
                method_name = str(method).lower()
                if method_name not in {
                    "get",
                    "post",
                    "patch",
                    "delete",
                    "put",
                    "head",
                    "options",
                    "trace",
                    "connect",
                }:
                    continue
                op_name = f"{method_name.upper()} {endpoint}"
                add_rel(
                    REL_EXPOSES_PATH,
                    op_name,
                    evidence=f"paths.{endpoint}.{method_name}",
                    confidence=0.92,
                    target_kind_hint="yaml_operation",
                )

    operations = doc.get("operations")
    if isinstance(operations, dict):
        for op_name in operations.keys():
            add_rel(
                REL_IMPLEMENTS_OPERATION,
                str(op_name),
                evidence=f"operations.{op_name}",
                confidence=0.85,
                target_kind_hint="yaml_action",
            )

    allowed_ops = doc.get("allowed_operations") or doc.get("allowedOperations")
    if isinstance(allowed_ops, list):
        for op_name in allowed_ops:
            add_rel(
                REL_IMPLEMENTS_OPERATION,
                str(op_name),
                evidence=f"allowed_operations.{op_name}",
                confidence=0.85,
                target_kind_hint="yaml_action",
            )

    actions = doc.get("actions")
    if isinstance(actions, list):
        for action in actions:
            if not isinstance(action, dict):
                continue
            action_name = action.get("name")
            if not action_name:
                continue
            add_rel(
                REL_IMPLEMENTS_OPERATION,
                str(action_name),
                evidence=f"actions.name.{action_name}",
                confidence=0.85,
                target_kind_hint="yaml_action",
            )

    def walk_refs(node: object, path_parts: list[str]) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                key_str = str(key)
                next_path = path_parts + [key_str]

                if key_str == "$ref" and isinstance(value, str):
                    add_rel(
                        REL_REFERENCES,
                        value,
                        evidence=".".join(next_path),
                        confidence=0.95,
                        target_kind_hint="yaml_schema",
                    )

                if key_str in {"x-mappedTo", "mappedTo"} and isinstance(value, str):
                    add_rel(
                        REL_REFERENCES,
                        value,
                        evidence=".".join(next_path),
                        confidence=0.9,
                        target_kind_hint="entity",
                    )

                if (
                    key_str in {"path", "resource"}
                    and isinstance(value, str)
                    and value.startswith("/")
                ):
                    add_rel(
                        REL_CONTAINS,
                        value,
                        evidence=".".join(next_path),
                        confidence=0.75,
                        target_kind_hint="resource",
                    )

                walk_refs(value, next_path)
            return

        if isinstance(node, list):
            for idx, item in enumerate(node):
                walk_refs(item, path_parts + [str(idx)])

    walk_refs(doc, [])

    YAML_REL_STATS["relationships_emitted"] += len(rels)
    return rels


EXTRACTORS = {
    "php": extract_php,
    "java": extract_java,
    "xml": extract_xml,
    "javascript": extract_js_ts,
    "typescript": extract_js_ts,
    "yaml": extract_yaml,
}


def extract_relationships_for_file(
    repo_root: Path,
    file_row: FileRow,
    symbols_by_name: dict[str, list[SymbolRow]],
    symbols_by_file: dict[int, list[SymbolRow]],
    symbols_by_qualified_name: dict[str, list[SymbolRow]],
) -> list[Relationship]:
    text = read_file_text(repo_root, file_row.path)
    if text is None:
        return []

    ext = Path(file_row.path).suffix.lower()
    language = (file_row.language or SUPPORTED_EXTENSIONS.get(ext, "unknown")).lower()
    file_row.language = language

    extractor = EXTRACTORS.get(language)
    if not extractor:
        return []

    return extractor(
        text, file_row, symbols_by_name, symbols_by_file, symbols_by_qualified_name
    )


def insert_relationships(
    conn: sqlite3.Connection, rels: Iterable[Relationship], repo_id: int
) -> int:
    inserted = 0

    columns = [
        "source_symbol_id",
        "source_name",
        "source_kind",
        "target_symbol_id",
        "target_name",
        "target_kind",
        "relationship_type",
        "file_id",
        "file_path",
        "language",
        "confidence",
        "evidence",
        "resolution_class",
        "resolution_reason",
        "extractor",
    ]
    has_repo_id = "repo_id" in table_columns(conn, "relationships")
    if has_repo_id:
        columns.insert(0, "repo_id")
    sql = f"""
        INSERT OR IGNORE INTO relationships (
            {", ".join(columns)}
        )
        VALUES ({", ".join("?" for _ in columns)})
    """

    for r in rels:
        cur = conn.execute(
            sql,
            ((repo_id,) if has_repo_id else ())
            + (
                r.source_symbol_id,
                r.source_name,
                r.source_kind,
                r.target_symbol_id,
                r.target_name,
                r.target_kind,
                r.relationship_type,
                r.file_id,
                r.file_path,
                r.language,
                r.confidence,
                r.evidence,
                r.resolution_class,
                r.resolution_reason,
                RELATIONSHIP_EXTRACTOR,
            ),
        )
        if cur.rowcount > 0:
            inserted += 1

    return inserted


def reset_relationships(conn: sqlite3.Connection, repo_id: int) -> None:
    if "repo_id" in table_columns(conn, "relationships"):
        conn.execute(
            "DELETE FROM relationships WHERE extractor = ? AND repo_id = ?",
            (RELATIONSHIP_EXTRACTOR, repo_id),
        )
    else:
        conn.execute(
            "DELETE FROM relationships WHERE extractor = ? "
            "AND file_id IN (SELECT id FROM files WHERE repo_id = ?)",
            (RELATIONSHIP_EXTRACTOR, repo_id),
        )
    conn.commit()


def relationship_file_closure(
    conn: sqlite3.Connection,
    *,
    repo_id: int,
    direct_file_ids: Iterable[int],
    changed_symbol_ids: Iterable[int] = (),
    changed_symbol_names: Iterable[str] = (),
) -> set[int]:
    """Return source files requiring re-resolution after symbol changes."""

    closure = {int(file_id) for file_id in direct_file_ids}
    clauses: list[str] = []
    params: list[object] = [repo_id, RELATIONSHIP_EXTRACTOR]
    symbol_ids = sorted({int(value) for value in changed_symbol_ids})
    names = sorted({str(value) for value in changed_symbol_names if value})
    if symbol_ids:
        placeholders = ",".join("?" for _ in symbol_ids)
        clauses.append(
            f"(source_symbol_id IN ({placeholders}) OR target_symbol_id IN ({placeholders}))"
        )
        params.extend(symbol_ids)
        params.extend(symbol_ids)
    if names:
        placeholders = ",".join("?" for _ in names)
        clauses.append(f"target_name IN ({placeholders})")
        params.extend(names)
    if clauses:
        rows = conn.execute(
            "SELECT DISTINCT file_id FROM relationships "
            "WHERE repo_id=? AND extractor=? AND file_id IS NOT NULL AND ("
            + " OR ".join(clauses)
            + ")",
            params,
        ).fetchall()
        closure.update(int(row[0]) for row in rows)
    return closure


def extract_all(
    only_changed: bool = True,
    languages: list[str] | None = None,
    repo_root: str | None = None,
    repo_key: str | None = None,
    reset: bool = False,
    commit_every: int = 1000,
    limit: int | None = None,
    file_filter: str | None = None,
    db_path: str | None = None,
    file_ids: Iterable[int] | None = None,
) -> int:
    conn = get_connection(db_path)
    cur = conn.cursor()
    require_repo_scoped_files(conn)
    repo = resolve_repo(conn, repo_key)
    started = datetime.now(UTC).isoformat()

    ensure_relationship_tracking_schema(conn)
    ensure_relationship_classification_columns(conn)

    selected_languages = [
        lang for lang in (languages or list(EXTRACTORS.keys())) if lang in EXTRACTORS
    ]
    if not selected_languages:
        print("⚠️  No valid extractors selected.")
        conn.close()
        return 0

    if "yaml" in selected_languages:
        reset_yaml_rel_stats()

    if reset:
        reset_relationships(conn, repo.id)

    symbols_by_name, symbols_by_file, symbols_by_qualified_name = load_symbols(
        conn, repo.id
    )
    files = load_files(
        conn=conn,
        repo_id=repo.id,
        only_changed=only_changed,
        languages=selected_languages,
        limit=limit,
        file_filter=file_filter,
        file_ids=file_ids,
    )
    print(f"🔎 Extracting relationships from {len(files)} files")

    if repo_root and Path(repo_root).resolve() != repo.local_root:
        raise RuntimeError(
            "--repo-root must match the registered repository local_root"
        )
    repo_root_path = repo.local_root
    total_inserted = 0
    errors = 0
    processed = 0

    for file_row in tqdm(files, desc="Extracting"):
        try:
            cur.execute("SAVEPOINT relationship_file")
            cur.execute(
                "DELETE FROM relationships WHERE extractor=? AND repo_id=? AND file_id=?",
                (RELATIONSHIP_EXTRACTOR, repo.id, file_row.id),
            )
            rels = extract_relationships_for_file(
                repo_root_path,
                file_row,
                symbols_by_name,
                symbols_by_file,
                symbols_by_qualified_name,
            )
            total_inserted += insert_relationships(conn, rels, repo.id)
            processed += 1
            cur.execute(
                "UPDATE files SET last_relationships_extracted = ? WHERE id = ?",
                (started, file_row.id),
            )
            cur.execute("RELEASE SAVEPOINT relationship_file")

            if commit_every > 0 and processed % commit_every == 0:
                conn.commit()
                tqdm.write(f"Processed={processed}, inserted={total_inserted}")
        except Exception as e:
            cur.execute("ROLLBACK TO SAVEPOINT relationship_file")
            cur.execute("RELEASE SAVEPOINT relationship_file")
            errors += 1
            print(f"⚠️  {file_row.path}: {e}")

    conn.commit()
    conn.close()

    print(f"\n📊 Relationships extracted: {total_inserted}")
    print(f"   Errors:                  {errors}")
    if "yaml" in selected_languages:
        yaml_stats = get_yaml_rel_stats()
        print(f"   YAML files seen:         {yaml_stats.get('files_seen', 0)}")
        print(f"   YAML parse fail:         {yaml_stats.get('parse_failures', 0)}")
        print(
            f"   YAML rels emitted:       {yaml_stats.get('relationships_emitted', 0)}"
        )
    if errors:
        raise RuntimeError(f"relationship extraction failed for {errors} file(s)")
    return total_inserted


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--full",
        action="store_true",
        help="Extract relationships for all files, not only changed files.",
    )
    parser.add_argument(
        "--language",
        action="append",
        choices=sorted(EXTRACTORS.keys()),
        help="Limit extraction to one or more languages (repeat flag to pass multiple).",
    )
    parser.add_argument("--repo", help="Registered repo_key to extract")
    parser.add_argument("--repo-root", help="Must match registered local_root")
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--file", help="Only process files whose path contains this string"
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help=f"Delete previous {RELATIONSHIP_EXTRACTOR} relationships",
    )
    parser.add_argument("--commit-every", type=int, default=1000)
    parser.add_argument("--db", help="Catalog database path")
    args = parser.parse_args()

    extract_all(
        only_changed=not args.full,
        languages=args.language,
        repo_root=args.repo_root,
        repo_key=args.repo,
        reset=args.reset,
        commit_every=args.commit_every,
        limit=args.limit,
        file_filter=args.file,
        db_path=args.db,
    )
