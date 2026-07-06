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

import argparse
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional
from tqdm import tqdm

from config import CATALOG_DB
from catalog.db import get_connection


DEFAULT_DB = CATALOG_DB


REL_INHERITS = "INHERITS"
REL_IMPLEMENTS = "IMPLEMENTS"
REL_IMPORTS = "IMPORTS"
REL_USES = "USES"
REL_REFERENCES = "REFERENCES"
REL_CALLS = "CALLS"
REL_STATIC_CALLS = "STATIC_CALLS"

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
    parent_symbol: Optional[str]
    file_id: Optional[int]
    file_path: Optional[str]


@dataclass
class Relationship:
    source_symbol_id: Optional[int]
    source_name: Optional[str]
    source_kind: Optional[str]
    target_symbol_id: Optional[int]
    target_name: str
    target_kind: Optional[str]
    relationship_type: str
    file_id: Optional[int]
    file_path: str
    language: str
    confidence: float
    evidence: str
    resolution_class: str
    resolution_reason: str


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}


def pick_col(columns: set[str], candidates: list[str]) -> Optional[str]:
    for c in candidates:
        if c in columns:
            return c
    return None


def load_files(conn: sqlite3.Connection, limit: Optional[int], file_filter: Optional[str]) -> list[FileRow]:
    cols = table_columns(conn, "files")

    id_col = pick_col(cols, ["id", "file_id"])
    path_col = pick_col(cols, ["path", "file_path", "relative_path", "full_path"])
    lang_col = pick_col(cols, ["language", "lang", "file_type"])

    if not id_col or not path_col:
        raise RuntimeError(f"Cannot infer files table columns. Found: {sorted(cols)}")

    sql = f"SELECT {id_col} AS id, {path_col} AS path"
    if lang_col:
        sql += f", {lang_col} AS language"
    else:
        sql += ", NULL AS language"
    sql += " FROM files"

    params = []
    where = []

    if file_filter:
        where.append(f"{path_col} LIKE ?")
        params.append(f"%{file_filter}%")

    if where:
        sql += " WHERE " + " AND ".join(where)

    sql += f" ORDER BY {id_col}"

    if limit:
        sql += " LIMIT ?"
        params.append(limit)

    rows = conn.execute(sql, params).fetchall()

    out: list[FileRow] = []
    for r in rows:
        path = r["path"]
        ext = Path(path).suffix.lower()
        language = r["language"] or SUPPORTED_EXTENSIONS.get(ext, "unknown")
        out.append(FileRow(id=r["id"], path=path, language=language))

    return out


def load_symbols(
    conn: sqlite3.Connection,
) -> tuple[dict[str, list[SymbolRow]], dict[int, list[SymbolRow]], dict[str, list[SymbolRow]]]:
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
            {id_col} AS id,
            {name_col} AS name,
            {kind_col if kind_col else "'unknown'"} AS kind,
            {parent_col if parent_col else "NULL"} AS parent_symbol,
            {file_id_col if file_id_col else "NULL"} AS file_id,
            {file_path_col if file_path_col else "NULL"} AS file_path
        FROM symbols
        WHERE {name_col} IS NOT NULL
    """

    by_name: dict[str, list[SymbolRow]] = {}
    by_file: dict[int, list[SymbolRow]] = {}
    by_qualified_name: dict[str, list[SymbolRow]] = {}

    for r in conn.execute(sql).fetchall():
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
    source_name: Optional[str] = None,
) -> Optional[SymbolRow]:
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

    return sorted(candidates, key=lambda s: priority.get(str(s.kind).lower(), 99))[0]


def pick_source_symbol(symbols_by_file: dict[int, list[SymbolRow]], file_id: int, preferred_name: Optional[str] = None) -> tuple[Optional[int], Optional[str], Optional[str]]:
    symbols = symbols_by_file.get(file_id, [])

    if preferred_name:
        for s in symbols:
            if s.name == preferred_name:
                return s.id, s.name, s.kind

    # Prefer class-like source symbol. If not found, use first symbol.
    class_like = [s for s in symbols if str(s.kind).lower() in {"class", "interface", "trait", "enum"}]
    if class_like:
        s = class_like[0]
        return s.id, s.name, s.kind

    if symbols:
        s = symbols[0]
        return s.id, s.name, s.kind

    return None, None, None


def read_file_text(repo_root: Path, file_path: str) -> Optional[str]:
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
    target_symbol_id: Optional[int],
) -> tuple[str, str]:
    if target_symbol_id is not None:
        return RESOLUTION_CLASS_PROJECT_RESOLVED, "target_symbol_id_present"

    normalized_target = normalize_target_name(target_name)
    lower_target = normalized_target.lower()

    if relationship_type == REL_REFERENCES and lower_target in BASIC_REFERENCE_TOKENS:
        return RESOLUTION_CLASS_HEURISTIC, "generic_reference_token"

    if looks_builtin(normalized_target, relationship_type):
        return RESOLUTION_CLASS_BUILTIN, "builtin_symbol_pattern"

    if relationship_type == REL_IMPORTS and ("/" in normalized_target or normalized_target.startswith("@")):
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


def make_rel(
    rel_type: str,
    target_name: str,
    file_row: FileRow,
    source: tuple[Optional[int], Optional[str], Optional[str]],
    symbols_by_name: dict[str, list[SymbolRow]],
    symbols_by_qualified_name: dict[str, list[SymbolRow]],
    evidence: str,
    confidence: float = 0.7,
    target_kind_hint: Optional[str] = None,
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

    return Relationship(
        source_symbol_id=source[0],
        source_name=source[1],
        source_kind=source[2],
        target_symbol_id=target.id if target else None,
        target_name=target.name if target else target_name,
        target_kind=target.kind if target else target_kind_hint,
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
    class_declarations: list[tuple[int, str, Optional[str], tuple[Optional[int], Optional[str], Optional[str]]]] = []

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
            rels.append(make_rel(
                REL_INHERITS,
                m.group(2),
                file_row,
                source,
                symbols_by_name,
                symbols_by_qualified_name,
                evidence=m.group(0),
                confidence=0.95,
                target_kind_hint="class",
            ))

        if m.group(3):
            for iface in m.group(3).split(","):
                iface = iface.strip()
                if iface:
                    rels.append(make_rel(
                        REL_IMPLEMENTS,
                        iface,
                        file_row,
                        source,
                        symbols_by_name,
                        symbols_by_qualified_name,
                        evidence=m.group(0),
                        confidence=0.95,
                        target_kind_hint="interface",
                    ))

    source = pick_source_symbol(symbols_by_file, file_row.id)

    def source_for_offset(offset: int) -> tuple[tuple[Optional[int], Optional[str], Optional[str]], Optional[str]]:
        for start, class_name, parent_class, class_source in reversed(class_declarations):
            if offset >= start:
                return class_source, parent_class
        return source, None

    # use Namespace\ClassName;
    for m in re.finditer(r"^\s*use\s+([\\A-Za-z_][\\A-Za-z0-9_]*(?:\s+as\s+[A-Za-z_][A-Za-z0-9_]*)?)\s*;", text, re.MULTILINE):
        raw = m.group(1)
        target = raw.split(" as ")[0].strip()
        rels.append(make_rel(
            REL_IMPORTS,
            target,
            file_row,
            source,
            symbols_by_name,
            symbols_by_qualified_name,
            evidence=m.group(0),
            confidence=0.9,
            target_kind_hint="class",
        ))

    # new ClassName(
    for m in re.finditer(r"\bnew\s+([\\A-Za-z_][\\A-Za-z0-9_]*)\s*\(", text):
        target = m.group(1)
        rels.append(make_rel(
            REL_USES,
            target,
            file_row,
            source,
            symbols_by_name,
            symbols_by_qualified_name,
            evidence=m.group(0),
            confidence=0.75,
            target_kind_hint="class",
        ))

    # ClassName::method(
    for m in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]*)::([A-Za-z_][A-Za-z0-9_]*)\s*\(", text):
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

        rels.append(make_rel(
            REL_STATIC_CALLS,
            static_target,
            file_row,
            call_source,
            symbols_by_name,
            symbols_by_qualified_name,
            evidence=m.group(0),
            confidence=0.65,
            target_kind_hint="method",
        ))
        rels.append(make_rel(
            REL_REFERENCES,
            reference_target,
            file_row,
            call_source,
            symbols_by_name,
            symbols_by_qualified_name,
            evidence=m.group(0),
            confidence=0.7,
            target_kind_hint="class",
        ))

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
            rels.append(make_rel(
                REL_INHERITS,
                m.group(3),
                file_row,
                source,
                symbols_by_name,
                symbols_by_qualified_name,
                evidence=m.group(0),
                confidence=0.95,
                target_kind_hint="class",
            ))

        if m.group(4):
            for iface in m.group(4).split(","):
                iface = iface.strip()
                if iface:
                    rels.append(make_rel(
                        REL_IMPLEMENTS,
                        iface,
                        file_row,
                        source,
                        symbols_by_name,
                        symbols_by_qualified_name,
                        evidence=m.group(0),
                        confidence=0.95,
                        target_kind_hint="interface",
                    ))

    source = pick_source_symbol(symbols_by_file, file_row.id)

    # import com.foo.Bar;
    for m in re.finditer(r"^\s*import\s+([A-Za-z_][A-Za-z0-9_\.]*)(?:\.\*)?\s*;", text, re.MULTILINE):
        target = m.group(1).split(".")[-1]
        rels.append(make_rel(
            REL_IMPORTS,
            target,
            file_row,
            source,
            symbols_by_name,
            symbols_by_qualified_name,
            evidence=m.group(0),
            confidence=0.85,
            target_kind_hint="class",
        ))

    # new ClassName(
    for m in re.finditer(r"\bnew\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", text):
        target = m.group(1)
        rels.append(make_rel(
            REL_USES,
            target,
            file_row,
            source,
            symbols_by_name,
            symbols_by_qualified_name,
            evidence=m.group(0),
            confidence=0.75,
            target_kind_hint="class",
        ))

    # ClassName.method(
    for m in re.finditer(r"\b([A-Z][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\s*\(", text):
        target_class = m.group(1)
        method = m.group(2)
        rels.append(make_rel(
            REL_CALLS,
            f"{target_class}.{method}",
            file_row,
            source,
            symbols_by_name,
            symbols_by_qualified_name,
            evidence=m.group(0),
            confidence=0.6,
            target_kind_hint="method",
        ))
        rels.append(make_rel(
            REL_REFERENCES,
            target_class,
            file_row,
            source,
            symbols_by_name,
            symbols_by_qualified_name,
            evidence=m.group(0),
            confidence=0.65,
            target_kind_hint="class",
        ))

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
        r'\b([A-Za-z_][A-Za-z0-9_]*(?:Manager|Controller|Service|Validator|Factory))\b',
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
                rels.append(make_rel(
                    REL_REFERENCES,
                    target,
                    file_row,
                    source,
                    symbols_by_name,
                    symbols_by_qualified_name,
                    evidence=m.group(0),
                    confidence=0.55,
                    target_kind_hint="unknown",
                ))

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
        rels.append(make_rel(
            REL_IMPORTS,
            imported,
            file_row,
            source,
            symbols_by_name,
            symbols_by_qualified_name,
            evidence=m.group(0),
            confidence=0.65,
            target_kind_hint="module",
        ))

    # require('...')
    for m in re.finditer(r"\brequire\s*\(\s*['\"]([^'\"]+)['\"]\s*\)", text):
        rels.append(make_rel(
            REL_IMPORTS,
            m.group(1),
            file_row,
            source,
            symbols_by_name,
            symbols_by_qualified_name,
            evidence=m.group(0),
            confidence=0.65,
            target_kind_hint="module",
        ))

    # ClassName.method(
    for m in re.finditer(r"\b([A-Z][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\s*\(", text):
        rels.append(make_rel(
            REL_CALLS,
            f"{m.group(1)}.{m.group(2)}",
            file_row,
            source,
            symbols_by_name,
            symbols_by_qualified_name,
            evidence=m.group(0),
            confidence=0.55,
            target_kind_hint="method",
        ))

    return rels


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

    if ext == ".php" or language == "php":
        return extract_php(text, file_row, symbols_by_name, symbols_by_file, symbols_by_qualified_name)

    if ext == ".java" or language == "java":
        return extract_java(text, file_row, symbols_by_name, symbols_by_file, symbols_by_qualified_name)

    if ext == ".xml" or language == "xml":
        return extract_xml(text, file_row, symbols_by_name, symbols_by_file, symbols_by_qualified_name)

    if ext in {".js", ".jsx", ".ts", ".tsx"} or language in {"javascript", "typescript"}:
        return extract_js_ts(text, file_row, symbols_by_name, symbols_by_file, symbols_by_qualified_name)

    return []


def insert_relationships(conn: sqlite3.Connection, rels: Iterable[Relationship]) -> int:
    inserted = 0

    sql = """
        INSERT OR IGNORE INTO relationships (
            source_symbol_id,
            source_name,
            source_kind,
            target_symbol_id,
            target_name,
            target_kind,
            relationship_type,
            file_id,
            file_path,
            language,
            confidence,
            evidence,
            resolution_class,
            resolution_reason,
            extractor
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    for r in rels:
        cur = conn.execute(sql, (
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
            "phase2_regex_mvp",
        ))
        if cur.rowcount > 0:
            inserted += 1

    return inserted


def reset_relationships(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM relationships WHERE extractor = 'phase2_regex_mvp'")
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--file", help="Only process files whose path contains this string")
    parser.add_argument("--reset", action="store_true", help="Delete previous phase2_regex_mvp relationships")
    parser.add_argument("--commit-every", type=int, default=1000)
    args = parser.parse_args()

    conn = get_connection(args.db)
    ensure_relationship_classification_columns(conn)

    if args.reset:
        reset_relationships(conn)

    symbols_by_name, symbols_by_file, symbols_by_qualified_name = load_symbols(conn)
    files = load_files(conn, args.limit, args.file)
    print(f"🔎 Extracting relationships from {len(files)} files")

    repo_root = Path(args.repo_root).resolve()

    total_inserted = 0
    processed = 0

    for f in tqdm(files, desc="Extracting"):
        rels = extract_relationships_for_file(
            repo_root,
            f,
            symbols_by_name,
            symbols_by_file,
            symbols_by_qualified_name,
        )
        total_inserted += insert_relationships(conn, rels)
        processed += 1

        if processed % args.commit_every == 0:
            conn.commit()
            tqdm.write(f"Processed={processed}, inserted={total_inserted}")

    conn.commit()

    print(f"Done. Processed files={processed}, inserted relationships={total_inserted}")


if __name__ == "__main__":
    main()
