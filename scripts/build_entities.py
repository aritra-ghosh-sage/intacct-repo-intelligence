# scripts/build_entities.py

from __future__ import annotations

import json
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click
from tqdm import tqdm

try:
    from catalog.db import get_connection
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from catalog.db import get_connection

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

RELATED_FILE_ROLES: list[str] = [
    "yaml",
    "xslt",
    "inc",
    "xml",
]

OPENAPI_SCHEMA_MAPPING_TYPE = "openapispec_schema"
OPENAPI_OPERATIONS_MAPPING_TYPE = "openapispec_operations"
OPENAPI_HISTORY_MAPPING_TYPE = "openapispec_history"


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


def _build_openapi_module_candidates(module: str | None) -> list[str]:
    out: list[str] = []

    def add(value: str | None) -> None:
        if not value:
            return
        v = value.strip().lower()
        if v and v not in out:
            out.append(v)

    if module:
        add(module)
        add(module[:2])
        if "_" in module:
            add(module.split("_", 1)[0])

    return out


def _discover_openapi_yaml_path(
    conn: sqlite3.Connection,
    module_candidates: list[str],
    slug_candidates: list[str],
    subdir: str,
    tail_pattern: str,
) -> str | None:
    base_conditions = [f"LOWER(path) LIKE 'app/source/openapispec/%/{subdir}/%'"]
    params: list[str] = []
    if module_candidates:
        module_predicates = []
        for module_name in module_candidates:
            module_predicates.append("LOWER(path) LIKE ?")
            params.append(f"app/source/openapispec/{module_name}/{subdir}/%")
        base_conditions.append(f"({' OR '.join(module_predicates)})")

    for slug in slug_candidates:
        row = conn.execute(
            f"""
            SELECT path
            FROM files
            WHERE {' AND '.join(base_conditions)}
              AND LOWER(path) LIKE ?
            ORDER BY LENGTH(path), path
            LIMIT 1
            """,
            (*params, tail_pattern.format(slug=slug)),
        ).fetchone()
        if row and row["path"]:
            return str(row["path"])
    return None


def discover_related_yaml(conn: sqlite3.Connection, entity: dict[str, Any]) -> dict[str, str]:
    module = entity.get("module")
    module_candidates = _build_openapi_module_candidates(module if isinstance(module, str) else None)
    slug_candidates = _build_yaml_slug_candidates(entity)
    if not slug_candidates:
        return {}

    discovered: dict[str, str] = {}

    schema_path = _discover_openapi_yaml_path(
        conn=conn,
        module_candidates=module_candidates,
        slug_candidates=slug_candidates,
        subdir="models",
        tail_pattern="% .{slug}.s%.schema.yaml".replace(" ", ""),
    )
    if schema_path:
        discovered[OPENAPI_SCHEMA_MAPPING_TYPE] = schema_path

    operations_path = _discover_openapi_yaml_path(
        conn=conn,
        module_candidates=module_candidates,
        slug_candidates=slug_candidates,
        subdir="paths",
        tail_pattern="% .{slug}.s%.api.yaml".replace(" ", ""),
    )
    if operations_path:
        discovered[OPENAPI_OPERATIONS_MAPPING_TYPE] = operations_path

    history_path = _discover_openapi_yaml_path(
        conn=conn,
        module_candidates=module_candidates,
        slug_candidates=slug_candidates,
        subdir="history",
        tail_pattern="% .{slug}.schema.history.yaml".replace(" ", ""),
    )
    if history_path:
        discovered[OPENAPI_HISTORY_MAPPING_TYPE] = history_path

    return discovered


def classify_yaml_mapping_type(path: str) -> str:
    lowered = path.lower()
    if "/openapispec/" not in lowered:
        return "yaml"
    if "/models/" in lowered and lowered.endswith(".schema.yaml"):
        return OPENAPI_SCHEMA_MAPPING_TYPE
    if "/paths/" in lowered and lowered.endswith(".api.yaml"):
        return OPENAPI_OPERATIONS_MAPPING_TYPE
    if "/history/" in lowered and lowered.endswith(".schema.history.yaml"):
        return OPENAPI_HISTORY_MAPPING_TYPE
    return "yaml"


def ensure_entity_columns(conn: sqlite3.Connection) -> None:
    cols = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(entity_nodes)").fetchall()
    }

    for col, ddl in [
        ("ent_file", "ALTER TABLE entity_nodes ADD COLUMN ent_file TEXT"),
        ("module", "ALTER TABLE entity_nodes ADD COLUMN module TEXT"),
        ("table_name", "ALTER TABLE entity_nodes ADD COLUMN table_name TEXT"),
        ("view_name", "ALTER TABLE entity_nodes ADD COLUMN view_name TEXT"),
        ("dummy", "ALTER TABLE entity_nodes ADD COLUMN dummy INTEGER"),
    ]:
        if col not in cols:
            conn.execute(ddl)

    conn.commit()


def get_or_create_entity(conn: sqlite3.Connection, entity: dict[str, Any]) -> int:
    row = conn.execute(
        "SELECT id FROM entity_nodes WHERE name = ?",
        (entity["entity_name"],),
    ).fetchone()

    if row:
        conn.execute(
            """
            UPDATE entity_nodes
            SET ent_file = ?,
                module = ?,
                table_name = ?,
                view_name = ?,
                dummy = ?,
                entity_type = 'business_entity',
                confidence = 1.0
            WHERE id = ?
            """,
            (
                entity.get("ent_file"),
                entity.get("module"),
                entity.get("table"),
                entity.get("view"),
                1 if entity.get("dummy") else 0,
                row["id"],
            ),
        )
        return row["id"]

    cur = conn.execute(
        """
        INSERT INTO entity_nodes(
            name,
            entity_type,
            confidence,
            ent_file,
            module,
            table_name,
            view_name,
            dummy
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entity["entity_name"],
            "business_entity",
            1.0,
            entity.get("ent_file"),
            entity.get("module"),
            entity.get("table"),
            entity.get("view"),
            1 if entity.get("dummy") else 0,
        ),
    )
    lastrowid = cur.lastrowid
    assert lastrowid is not None
    return int(lastrowid)


def resolve_companion_symbol(
    conn: sqlite3.Connection,
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

    file_row = conn.execute(
        """
        SELECT id
        FROM files
        WHERE LOWER(path) = LOWER(?)
        LIMIT 1
        """,
        (class_file_path,),
    ).fetchone()
    file_id = int(file_row["id"]) if file_row else None

    row = conn.execute(
        """
        SELECT s.id
        FROM symbols s
        WHERE s.kind = 'class'
          AND LOWER(s.name) = LOWER(?)
        ORDER BY s.id
        LIMIT 1
        """,
        (expected_class_name,),
    ).fetchone()
    if row:
        return int(row["id"]), "class_name_match"

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
            return int(row["id"]), "class_in_file"

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
            return int(row["id"]), "function_stem_match"

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
            return int(row["id"]), "object_stem_match"

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
            return int(row["id"]), "any_symbol_in_file"

    return None, "file_only_companion"


def insert_mapping(
    conn: sqlite3.Connection,
    entity_id: int,
    symbol_id: int | None,
    mapping_type: str,
    confidence: float,
    source_text: str,
) -> bool:
    file_id: int | None = None
    if symbol_id is not None:
        symbol_row = conn.execute(
            "SELECT file_id FROM symbols WHERE id = ? LIMIT 1",
            (symbol_id,),
        ).fetchone()
        file_id = int(symbol_row["file_id"]) if symbol_row and symbol_row["file_id"] is not None else None
    else:
        file_row = conn.execute(
            """
            SELECT id
            FROM files
            WHERE LOWER(path) = LOWER(?)
            LIMIT 1
            """,
            (source_text,),
        ).fetchone()
        file_id = int(file_row["id"]) if file_row else None

    source_key = source_text if symbol_id is None else None
    cur = conn.execute(
        """
        INSERT INTO entity_mappings(
            entity_id,
            symbol_id,
            file_id,
            mapping_type,
            confidence,
            source_text
        )
        SELECT ?, ?, ?, ?, ?, ?
        WHERE NOT EXISTS (
            SELECT 1
            FROM entity_mappings
            WHERE entity_id = ?
              AND (
                    (symbol_id = ?)
                 OR (symbol_id IS NULL AND ? IS NULL AND source_text = ?)
              )
              AND mapping_type = ?
        )
        """,
        (
            entity_id,
            symbol_id,
            file_id,
            mapping_type,
            confidence,
            source_text,
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
                WHERE entity_id = ?
                  AND mapping_type = ?
                  AND symbol_id IS NULL
                  AND source_text = ?
                """,
                (file_id, entity_id, mapping_type, source_text),
            )
        else:
            conn.execute(
                """
                UPDATE entity_mappings
                SET file_id = COALESCE(file_id, ?)
                WHERE entity_id = ?
                  AND mapping_type = ?
                  AND symbol_id = ?
                """,
                (file_id, entity_id, mapping_type, symbol_id),
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


def build(db: str, entities: Path, reset: bool) -> BuildStats:
    rows = _read_entities_jsonl(entities)
    missing_symbols: list[dict[str, str]] = []
    stats = BuildStats()

    conn = get_connection(db)
    try:
        ensure_entity_columns(conn)

        if reset:
            conn.execute("DELETE FROM entity_mappings")
            conn.execute("DELETE FROM entity_nodes")
            conn.commit()

        for entity in tqdm(rows, desc="Building entity mappings", unit="entity"):
            entity_name = entity["entity_name"]
            entity_id = get_or_create_entity(conn, entity)
            stats.entities_upserted += 1

            companion_classes = entity.get("companion_classes", {})
            for role in COMPANION_ROLES:
                class_file = companion_classes.get(role)
                if not class_file:
                    continue

                expected_class_name = f"{entity_name}{_role_to_suffix(role)}"
                symbol_id, resolution_reason = resolve_companion_symbol(
                    conn=conn,
                    class_file_path=class_file,
                    expected_class_name=expected_class_name,
                )
                if symbol_id is None:
                    if resolution_reason == "file_only_companion":
                        inserted = insert_mapping(
                            conn=conn,
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
                    entity_id=entity_id,
                    symbol_id=symbol_id,
                    mapping_type=role,
                    confidence=1.0,
                    source_text=class_file,
                )
                if inserted:
                    stats.mappings_inserted += 1
        
            # ---- Phase 2C.1 enhancement ----
            # Ingest related files (yaml, xslt, inc, xml) as file-only mappings.

            related = entity.get("related_files") or {}
            has_yaml_related = False

            for related_role in RELATED_FILE_ROLES:
                related_path = related.get(related_role) if isinstance(related, dict) else None
                if not related_path:
                    continue
                mapping_type = related_role
                if related_role == "yaml":
                    has_yaml_related = True
                    mapping_type = classify_yaml_mapping_type(str(related_path))

                inserted = insert_mapping(
                    conn,
                    entity_id,
                    symbol_id=None,
                    mapping_type=mapping_type,
                    confidence=0.9,
                    source_text=related_path,
                )
                if inserted:
                    stats.mappings_inserted += 1

            if not has_yaml_related:
                discovered_yaml = discover_related_yaml(conn, entity)
                for mapping_type, discovered_path in discovered_yaml.items():
                    inserted = insert_mapping(
                        conn,
                        entity_id,
                        symbol_id=None,
                        mapping_type=mapping_type,
                        confidence=0.9,
                        source_text=discovered_path,
                    )
                    if inserted:
                        stats.mappings_inserted += 1

        conn.commit()
    finally:
        conn.close()

    stats.missing_symbols = len(missing_symbols)
    out_path = Path("validation/missing_symbols.json")
    if missing_symbols:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(missing_symbols, indent=2), encoding="utf-8")
        click.echo(f"Missing symbols written to {out_path}")
    elif out_path.exists():
        out_path.unlink()

    return stats


@click.group()
def cli() -> None:
    pass


@cli.command("build")
@click.option("--db", default=DEFAULT_DB, show_default=True, help="Path to SQLite catalog database.")
@click.option(
    "--entities",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    default=Path(DEFAULT_ENTITIES),
    show_default=True,
    help="Path to entity definitions JSONL file.",
)
@click.option("--reset", is_flag=True, help="Delete entity tables before rebuilding.")
def build_command(db: str, entities: Path, reset: bool) -> None:
    stats = build(db=db, entities=entities, reset=reset)
    click.echo(f"Entities upserted:   {stats.entities_upserted}")
    click.echo(f"Mappings inserted:   {stats.mappings_inserted}")
    click.echo(f"Missing symbols:     {stats.missing_symbols}")


if __name__ == "__main__":
    cli()
