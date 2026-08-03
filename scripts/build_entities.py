# scripts/build_entities.py

from __future__ import annotations

import json
import re
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click
from tqdm import tqdm

try:
    from catalog.db import get_connection, require_foreign_key_integrity
    from catalog.mapping_ownership import BUILD_ENTITIES_MAPPING_TYPES, placeholders
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from catalog.db import get_connection, require_foreign_key_integrity
    from catalog.mapping_ownership import BUILD_ENTITIES_MAPPING_TYPES, placeholders

DEFAULT_DB = "catalog/catalog.db"
DEFAULT_ENTITIES = "config/entity_definitions.jsonl"

COMPANION_ROLES: list[str] = [
    "manager",
    "editor",
    "lister",
    "picker",
    "allowed_operations_handler",
    "approval_manager",
    "reverse_manager",
    "item_manager",
    "batch_manager",
    "batch_picker",
    "form_editor",
    "entity_manager",
    "entry_manager",
    "pick_manager",
    "pick_picker",
]

WORKFLOW_FILE_ROLES: list[str] = [
    "workflow_schema_file",
    "workflow_history_file",
    "workflow_api_files",
]

RELATED_FILE_ROLES: list[str] = ["yaml", "xslt", "inc", "xml", "sql", "rpt"]

MODULE_ALIASES: dict[str, str] = {
    "inventory": "inv",
    "company": "co",
    "expenses": "ee",
    "generalledger": "gl",
    "general-ledger": "gl",
}

_cache: defaultdict[str, dict[str, tuple]] = defaultdict(dict)
_DEFAULT_MISSING_SYMBOLS_PATH = object()


@dataclass
class BuildStats:
    entities_upserted: int = 0
    mappings_inserted: int = 0
    missing_symbols: int = 0
def _role_to_suffix(role: str) -> str:
    return "".join(part.capitalize() for part in role.split("_"))


def _camel_to_kebab(name: str) -> str:
    s1 = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", name)
    s2 = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1-\2", s1)
    return s2.replace("_", "-").lower()


def _strip_leading_acronym(name: str) -> str:
    match = re.match(r"^([A-Z]{2,})([A-Z].+)$", name)
    if match:
        return match.group(2)
    return name


def _build_yaml_slug_candidates(entity: dict[str, Any]) -> list[str]:
    candidates: list[str] = []

    def add(value: str | None) -> None:
        if not value:
            return
        normalized = value.strip().lower()
        if not normalized:
            return
        if normalized not in candidates:
            candidates.append(normalized)

    entity_name = str(entity.get("entity_name") or "")
    if entity_name:
        add(_camel_to_kebab(entity_name))
        add(_camel_to_kebab(_strip_leading_acronym(entity_name)))

    ent_file = str(entity.get("ent_file") or "")
    ent_stem = Path(ent_file).stem
    if ent_stem:
        add(ent_stem.replace("_", "-"))

    module = str(entity.get("module") or "").lower()
    module_prefix = module[:2] if len(module) >= 2 else ""
    if ent_stem and module_prefix and ent_stem.lower().startswith(module_prefix):
        add(ent_stem[len(module_prefix) :].replace("_", "-"))

    table = str(entity.get("table") or "")
    if table:
        add(table.replace("_", "-"))
        if module_prefix and table.lower().startswith(module_prefix):
            add(table[len(module_prefix) :].replace("_", "-"))

    return candidates


def classify_sql_mapping_type(path: str) -> str | None:
    """
    Option A (ISSUE-D1I): SQL mapping uses explicit path-level opt-outs.

    SQL files matching teardown/drop/cleanup naming patterns, files under
    migrations/, and files under platform/sql/ are excluded because they are
    typically schema-maintenance scripts with low entity provenance signal.
    """
    lowered = path.lower()
    basename = Path(lowered).name
    if basename.startswith(("drop_all", "teardown_", "cleanup_")):
        return None
    if "/migrations/" in lowered or "/platform/sql/" in lowered:
        return None
    if "/teardown/" in lowered:
        return None
    return "sql"


def _collect_related_file_mappings(entity: dict[str, Any]) -> list[tuple[str, str]]:
    """
    Collect related-file mappings from both legacy and flattened JSONL shapes.

    Supported inputs:
    - related_files.{yaml,xslt,inc,xml,sql, rpt}
    - top-level {yaml,xslt,inc,xml,sql, rpt}
    - top-level aliases such as {yaml_file,xslt_file,inc_file,xml_file,sql_file, rpt_file}
    """
    alias_map: dict[str, list[str]] = {
        "yaml": ["yaml_file", "yaml_files"],
        "xslt": ["xslt_file", "xslt_files"],
        "inc": ["inc_file", "inc_files"],
        "xml": ["xml_file", "xml_files"],
        "sql": ["sql_file", "sql_files"],
        "rpt": ["rpt_file", "rpt_files"],
    }

    role_to_paths: defaultdict[str, list[str]] = defaultdict(list)

    def _append(role: str, value: Any) -> None:
        if role not in RELATED_FILE_ROLES:
            return
        if isinstance(value, str):
            path = value.strip()
            if path:
                role_to_paths[role].append(path)
            return
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    path = item.strip()
                    if path:
                        role_to_paths[role].append(path)

    related = entity.get("related_files") or {}
    if isinstance(related, dict):
        for role in RELATED_FILE_ROLES:
            _append(role, related.get(role))

    for role in RELATED_FILE_ROLES:
        _append(role, entity.get(role))
        for alias in alias_map.get(role, []):
            _append(role, entity.get(alias))

    results: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for role in RELATED_FILE_ROLES:
        for related_path in role_to_paths.get(role, []):
            mapping_type = role
            if role == "sql":
                mapping_type = classify_sql_mapping_type(related_path)
                if mapping_type is None:
                    continue

            dedupe_key = (mapping_type, related_path.lower())
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            results.append((mapping_type, related_path))

    return results


def ensure_entity_occurrences_table(conn: sqlite3.Connection) -> None:
    """Fail clearly when the repo-scoped entity schema has not been migrated."""
    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(entity_occurrences)").fetchall()
    }
    required = {
        "repo_id",
        "entity_id",
        "ent_file",
        "module",
        "table_name",
        "view_name",
        "dummy",
        "source_file_id",
        "extractor",
        "confidence",
    }
    missing = sorted(required - columns)
    if missing:
        raise click.ClickException(
            "entity_occurrences is missing required columns; apply the multi-repo migration: "
            + ", ".join(missing)
        )


def _normalize_entity_module(entity: dict[str, Any]) -> str | None:
    raw_module = str(entity.get("module") or "").strip().lower()
    if not raw_module:
        return None

    if raw_module in MODULE_ALIASES:
        return MODULE_ALIASES[raw_module]

    if raw_module == "apar":
        candidates: set[str] = set()

        module_path_hint = str(entity.get("module_path_hint") or "").strip().lower()
        if module_path_hint in {"ap", "ar"}:
            candidates.add(module_path_hint)

        for source_key in ("ent_file", "table", "entity_name"):
            value = str(entity.get(source_key) or "").strip().lower()
            if value.startswith("ap"):
                candidates.add("ap")
            if value.startswith("ar"):
                candidates.add("ar")

        if len(candidates) == 1:
            return next(iter(candidates))

        # Preserve ambiguous module rather than forcing a potentially wrong mapping.
        return raw_module

    return raw_module


def get_or_create_entity(conn: sqlite3.Connection, entity: dict[str, Any]) -> int:
    """Return a source-neutral canonical entity identity.

    Repository declaration metadata intentionally belongs in entity_occurrences,
    because equal names in different repositories are not evidence of equivalent
    source declarations.
    """
    row = conn.execute(
        "SELECT id FROM entity_nodes WHERE name = ?",
        (entity["entity_name"],),
    ).fetchone()

    if row:
        conn.execute(
            """
            UPDATE entity_nodes
            SET entity_type = 'business_entity',
                confidence = 1.0
            WHERE id = ?
            """,
            (row["id"],),
        )
        return row["id"]

    cur = conn.execute(
        """
        INSERT INTO entity_nodes(
            name,
            entity_type,
            confidence
        )
        VALUES (?, ?, ?)
        """,
        (
            entity["entity_name"],
            "business_entity",
            1.0,
        ),
    )
    lastrowid = cur.lastrowid
    assert lastrowid is not None
    return int(lastrowid)


def upsert_entity_occurrence(
    conn: sqlite3.Connection,
    repo_id: int,
    entity_id: int,
    entity: dict[str, Any],
) -> None:
    """Persist deterministic declaration facts for one repo/entity occurrence."""
    ent_file = entity.get("ent_file")
    source_file_id: int | None = None
    if isinstance(ent_file, str) and ent_file.strip():
        row = conn.execute(
            """
            SELECT id FROM files
            WHERE repo_id = ? AND path = ?
            LIMIT 1
            """,
            (repo_id, ent_file.strip()),
        ).fetchone()
        source_file_id = int(row["id"]) if row else None

    conn.execute(
        """
        INSERT INTO entity_occurrences(
            repo_id, entity_id, ent_file, module, table_name, view_name, dummy,
            source_file_id, extractor, confidence
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'build_entities', 1.0)
        ON CONFLICT(repo_id, entity_id) DO UPDATE SET
            ent_file = excluded.ent_file,
            module = excluded.module,
            table_name = excluded.table_name,
            view_name = excluded.view_name,
            dummy = excluded.dummy,
            source_file_id = excluded.source_file_id,
            extractor = excluded.extractor,
            confidence = excluded.confidence
        """,
        (
            repo_id,
            entity_id,
            ent_file.strip()
            if isinstance(ent_file, str) and ent_file.strip()
            else None,
            _normalize_entity_module(entity),
            entity.get("table"),
            entity.get("view"),
            1 if entity.get("dummy") else 0,
            source_file_id,
        ),
    )


def resolve_companion_symbol(
    conn: sqlite3.Connection,
    repo_id: int,
    class_file_path: str | None,
    expected_class_name: str,
) -> tuple[int | None, str]:
    """
    Deterministic resolver with fallback chain.

    Order:
    1. class symbol matching expected class name (case-insensitive)
    2. class symbol in the same file
    3. function-like symbol matching file basename
    4. object-like symbol matching file basename
    5. any symbol declared in the file (best-effort)
    6. return None
    """
    if not class_file_path:
        return None, "no_path"

    key = f"{repo_id}::{class_file_path.lower()}::{expected_class_name.lower()}"
    if key in _cache:
        cached_symbol_id, cached_reason = _cache[key]
        return cached_symbol_id, cached_reason

    file_row = conn.execute(
        """
        SELECT id
        FROM files
        WHERE repo_id = ?
          AND LOWER(path) = LOWER(?)
        LIMIT 1
        """,
        (repo_id, class_file_path),
    ).fetchone()
    file_id = int(file_row["id"]) if file_row else None

    symbol_id, resolution_reason = None, "file_only_companion"
    row = conn.execute(
        """
        SELECT s.id
        FROM symbols s
        JOIN files f ON f.id = s.file_id
        WHERE s.kind = 'class'
          AND LOWER(s.name) = LOWER(?)
          AND f.repo_id = ?
        ORDER BY s.id
        LIMIT 1
        """,
        (expected_class_name, repo_id),
    ).fetchone()
    if row:
        symbol_id, resolution_reason = int(row["id"]), "class_name_match"
        _cache[key] = (symbol_id, resolution_reason)
        return symbol_id, resolution_reason

    if file_id is not None:
        row = conn.execute(
            """
            SELECT id
            FROM symbols
            WHERE kind = 'class'
              AND file_id = ?
            ORDER BY id
            LIMIT 1
            """,
            (file_id,),
        ).fetchone()
        if row:
            symbol_id, resolution_reason = int(row["id"]), "class_in_file"
            _cache[key] = (symbol_id, resolution_reason)
            return symbol_id, resolution_reason

    file_stem = Path(class_file_path).stem
    file_stem_lower = file_stem.lower()

    if file_id is not None:
        row = conn.execute(
            """
            SELECT id
            FROM symbols
            WHERE kind IN ('function', 'method', 'arrow_function', 'declaration_function')
              AND LOWER(name) = ?
              AND file_id = ?
            ORDER BY id
            LIMIT 1
            """,
            (file_stem_lower, file_id),
        ).fetchone()
        if row:
            symbol_id, resolution_reason = int(row["id"]), "function_stem_match"
            _cache[key] = (symbol_id, resolution_reason)
            return symbol_id, resolution_reason

        row = conn.execute(
            """
            SELECT id
            FROM symbols
            WHERE kind IN ('object', 'variable', 'const', 'let', 'module', 'namespace')
              AND LOWER(name) = ?
              AND file_id = ?
            ORDER BY id
            LIMIT 1
            """,
            (file_stem_lower, file_id),
        ).fetchone()
        if row:
            symbol_id, resolution_reason = int(row["id"]), "object_stem_match"
            _cache[key] = (symbol_id, resolution_reason)
            return symbol_id, resolution_reason

        row = conn.execute(
            """
            SELECT id
            FROM symbols
            WHERE file_id = ?
            ORDER BY start_line, id
            LIMIT 1
            """,
            (file_id,),
        ).fetchone()
        if row:
            symbol_id, resolution_reason = int(row["id"]), "any_symbol_in_file"
            _cache[key] = (symbol_id, resolution_reason)
            return symbol_id, resolution_reason

    return symbol_id, resolution_reason


def insert_mapping(
    conn: sqlite3.Connection,
    repo_id: int,
    entity_id: int,
    symbol_id: int | None,
    mapping_type: str,
    confidence: float,
    source_text: str,
) -> bool:
    if mapping_type not in BUILD_ENTITIES_MAPPING_TYPES:
        raise ValueError(
            f"build_entities does not own mapping type: {mapping_type}"
        )

    file_id: int | None = None
    key_file_id = f"file_id::{repo_id}::{source_text.lower()}"
    key_symbol_id = (
        f"symbol_id::{repo_id}::{symbol_id}"
        if symbol_id is not None
        else f"symbol_id::{repo_id}::None"
    )

    if symbol_id is not None:
        if key_symbol_id in _cache:
            file_id = _cache[key_symbol_id]
        else:
            symbol_row = conn.execute(
                """
                SELECT s.file_id
                FROM symbols s
                JOIN files f ON f.id = s.file_id
                WHERE s.id = ? AND f.repo_id = ?
                LIMIT 1
                """,
                (symbol_id, repo_id),
            ).fetchone()
            file_id = (
                int(symbol_row["file_id"])
                if symbol_row and symbol_row["file_id"] is not None
                else None
            )
            _cache[key_symbol_id] = file_id
    else:
        if key_file_id in _cache:
            file_id = _cache[key_file_id]
        else:
            file_row = conn.execute(
                """
                SELECT id
                FROM files
                WHERE repo_id = ?
                  AND LOWER(path) = LOWER(?)
                LIMIT 1
                """,
                (repo_id, source_text),
            ).fetchone()
            file_id = int(file_row["id"]) if file_row else None
            _cache[key_file_id] = file_id

    source_key = source_text if symbol_id is None else None
    cur = conn.execute(
        """
        INSERT INTO entity_mappings(
            repo_id,
            entity_id,
            symbol_id,
            file_id,
            mapping_type,
            confidence,
            source_text
        )
        SELECT ?, ?, ?, ?, ?, ?, ?
        WHERE NOT EXISTS (
            SELECT 1
            FROM entity_mappings
            WHERE repo_id = ?
              AND entity_id = ?
              AND (
                    (symbol_id = ?)
                 OR (symbol_id IS NULL AND ? IS NULL AND source_text = ?)
              )
              AND mapping_type = ?
        )
        """,
        (
            repo_id,
            entity_id,
            symbol_id,
            file_id,
            mapping_type,
            confidence,
            source_text,
            repo_id,
            entity_id,
            symbol_id,
            symbol_id,
            source_key,
            mapping_type,
        ),
    )

    if file_id is not None:
        if symbol_id is None:
            conn.execute(
                """
                UPDATE entity_mappings
                SET file_id = COALESCE(file_id, ?)
                WHERE repo_id = ?
                  AND entity_id = ?
                  AND mapping_type = ?
                  AND symbol_id IS NULL
                  AND source_text = ?
                """,
                (file_id, repo_id, entity_id, mapping_type, source_text),
            )
        else:
            conn.execute(
                """
                UPDATE entity_mappings
                SET file_id = COALESCE(file_id, ?)
                WHERE repo_id = ?
                  AND entity_id = ?
                  AND mapping_type = ?
                  AND symbol_id = ?
                """,
                (file_id, repo_id, entity_id, mapping_type, symbol_id),
            )

    return cur.rowcount > 0


def _read_entities_jsonl(entities_path: Path) -> list[dict[str, Any]]:
    entities: list[dict[str, Any]] = []
    with entities_path.open("r", encoding="utf-8") as handle:
        for idx, line in enumerate(handle, start=1):
            payload = line.strip()
            if not payload:
                continue
            try:
                entity = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise click.ClickException(
                    f"Invalid JSON at line {idx} in {entities_path}: {exc}"
                ) from exc
            entities.append(entity)
    return entities


def _resolve_repo_id(conn: sqlite3.Connection, repo_key: str) -> int:
    row = conn.execute(
        "SELECT id FROM repos WHERE repo_key = ?",
        (repo_key,),
    ).fetchone()
    if row is None:
        raise click.ClickException(f"Unknown repository key: {repo_key}")
    return int(row["id"])


def build(
    db: str,
    entities: Path,
    reset: bool,
    repo_key: str,
    missing_symbols_path: Path | None | object = _DEFAULT_MISSING_SYMBOLS_PATH,
) -> BuildStats:
    """Build entity nodes and mappings idempotently using INSERT...WHERE NOT EXISTS."""
    rows = _read_entities_jsonl(entities)
    missing_symbols: list[dict[str, str]] = []
    stats = BuildStats()

    conn = get_connection(db)
    try:
        ensure_entity_occurrences_table(conn)
        repo_id = _resolve_repo_id(conn, repo_key)
        conn.execute("BEGIN IMMEDIATE")

        if reset:
            # Roots are a projection of mappings, not independent evidence.
            # Clear them before their source family so no stale root survives.
            conn.execute("DELETE FROM entity_roots WHERE repo_id = ?", (repo_id,))
            conn.execute(
                f"DELETE FROM entity_mappings WHERE repo_id = ? "
                f"AND mapping_type IN ({placeholders(BUILD_ENTITIES_MAPPING_TYPES)})",
                (repo_id, *BUILD_ENTITIES_MAPPING_TYPES),
            )
            # An occurrence is a snapshot of declarations from this repository.
            # Canonical entity_nodes intentionally remain shared and are not deleted.
            conn.execute("DELETE FROM entity_occurrences WHERE repo_id = ?", (repo_id,))
            require_foreign_key_integrity(conn, context="entity mapping reset")

        workflow_mappings = [
            ("workflow_schema_file", WORKFLOW_FILE_ROLES[0]),
            ("workflow_history_file", WORKFLOW_FILE_ROLES[1]),
        ]

        for entity in tqdm(rows, desc="Building entity mappings", unit="entity"):
            entity_name = entity["entity_name"]
            entity_id = get_or_create_entity(conn, entity)
            upsert_entity_occurrence(conn, repo_id, entity_id, entity)
            stats.entities_upserted += 1

            companion_classes = entity.get("companion_classes", {})
            for role in COMPANION_ROLES:
                class_file = companion_classes.get(role)
                if not class_file:
                    continue

                expected_class_name = f"{entity_name}{_role_to_suffix(role)}"
                symbol_id, resolution_reason = resolve_companion_symbol(
                    conn=conn,
                    repo_id=repo_id,
                    class_file_path=class_file,
                    expected_class_name=expected_class_name,
                )
                if symbol_id is None:
                    if resolution_reason == "file_only_companion":
                        inserted = insert_mapping(
                            conn=conn,
                            repo_id=repo_id,
                            entity_id=entity_id,
                            symbol_id=None,
                            mapping_type=role,
                            confidence=0.9,
                            source_text=class_file,
                        )
                        if inserted:
                            stats.mappings_inserted += 1
                        continue

                    missing_symbols.append(
                        {
                            "entity": entity_name,
                            "role": role,
                            "expected_class_name": expected_class_name,
                            "class_name_from_file": Path(class_file).stem,
                            "class_file": class_file,
                            "reason": resolution_reason,
                        }
                    )
                    continue

                inserted = insert_mapping(
                    conn=conn,
                    repo_id=repo_id,
                    entity_id=entity_id,
                    symbol_id=symbol_id,
                    mapping_type=role,
                    confidence=1.0,
                    source_text=class_file,
                )
                if inserted:
                    stats.mappings_inserted += 1

            # Ingest workflow files from top-level fields in entity_definitions.jsonl.
            # These are file-backed mappings, not companion class symbols.

            for field_name, mapping_type in workflow_mappings:
                file_path = entity.get(field_name)
                if isinstance(file_path, str) and file_path.strip():
                    inserted = insert_mapping(
                        conn=conn,
                        repo_id=repo_id,
                        entity_id=entity_id,
                        symbol_id=None,
                        mapping_type=mapping_type,
                        confidence=0.9,
                        source_text=file_path.strip(),
                    )
                    if inserted:
                        stats.mappings_inserted += 1

            workflow_api_files = entity.get("workflow_api_files")
            if isinstance(workflow_api_files, list):
                for workflow_api_path in workflow_api_files:
                    if (
                        not isinstance(workflow_api_path, str)
                        or not workflow_api_path.strip()
                    ):
                        continue
                    inserted = insert_mapping(
                        conn=conn,
                        repo_id=repo_id,
                        entity_id=entity_id,
                        symbol_id=None,
                        mapping_type=WORKFLOW_FILE_ROLES[2],
                        confidence=0.9,
                        source_text=workflow_api_path.strip(),
                    )
                    if inserted:
                        stats.mappings_inserted += 1

            # Ingest related files (yaml, xslt, inc, xml, sql, rpt) as file-backed mappings.
            # Accept both legacy related_files and flattened top-level fields.
            for mapping_type, related_path in _collect_related_file_mappings(entity):
                inserted = insert_mapping(
                    conn=conn,
                    repo_id=repo_id,
                    entity_id=entity_id,
                    symbol_id=None,
                    mapping_type=mapping_type,
                    confidence=0.9,
                    source_text=related_path,
                )
                if inserted:
                    stats.mappings_inserted += 1

        require_foreign_key_integrity(conn, context="entity mapping build")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    stats.missing_symbols = len(missing_symbols)
    out_path = (
        Path(f"validation/missing_symbols_{repo_key}.json")
        if missing_symbols_path is _DEFAULT_MISSING_SYMBOLS_PATH
        else missing_symbols_path
    )
    if missing_symbols and out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(missing_symbols, indent=2), encoding="utf-8")
        click.echo(f"Missing symbols written to {out_path}")
    elif out_path is not None and out_path.exists():
        out_path.unlink()

    return stats


@click.group()
def cli() -> None:
    pass


@cli.command("build")
@click.option(
    "--db",
    default=DEFAULT_DB,
    show_default=True,
    help="Path to SQLite catalog database.",
)
@click.option(
    "--entities",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    default=Path(DEFAULT_ENTITIES),
    show_default=True,
    help="Path to entity definitions JSONL file.",
)
@click.option("--reset", is_flag=True, help="Delete entity tables before rebuilding.")
@click.option("--repo", "repo_key", required=True, help="Registered repository key.")
def build_command(db: str, entities: Path, reset: bool, repo_key: str) -> None:
    stats = build(db=db, entities=entities, reset=reset, repo_key=repo_key)
    click.echo(f"Entities upserted:   {stats.entities_upserted}")
    click.echo(f"Mappings inserted:   {stats.mappings_inserted}")
    click.echo(f"Missing symbols:     {stats.missing_symbols}")


if __name__ == "__main__":
    cli()
