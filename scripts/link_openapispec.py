#!/usr/bin/env python3

from __future__ import annotations

import json
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import click

try:
    from catalog.db import get_connection
    from catalog.repositories import get_repository, resolve_repository_root
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from catalog.db import get_connection
    from catalog.repositories import get_repository, resolve_repository_root

DEFAULT_DB = "catalog/catalog.db"
MISSING_METADATA_LOG_PATH = (
    Path(__file__).resolve().parents[1] / "outputs" / "missing_metadata.jsonl"
)
OPENAPI_MAPPING_TYPES = [
    "openapispec_schema",
    "openapispec_operations",
    "openapispec_history",
    "openapispec_view",
    "openapispec_uimeta",
    "openapispec_viewmeta",
    "openapispec_paths",
    "openapispec_actions",
    "openapispec_events",
    "openapispec_resource",
    "openapispec_components",
    "openapispec_security",
    "openapispec_unknown",
]

LOW_SIGNAL_CANONICAL_SUFFIXES = {
    "ref",
    "reference",
    "history",
    "detail",
    "line",
    "entry",
    "status",
    "response",
    "request",
    "record",
    "map",
    "template",
    "preference",
    "provider",
    "log",
    "constraint",
}

MODULE_SCOPE_FALLBACKS: dict[str, list[str]] = {
    "ap": ["apar", "common", "company"],
    "ar": ["apar", "common", "company"],
    "co": ["company", "common"],
    "cm": ["cm", "common", "company"],
    "inv": ["inventory", "common"],
    "sales": ["sales", "common", "company"],
    "gl": ["gl", "common", "company"],
    "tax": ["tax", "common", "company"],
    "pa": ["pa", "common"],
    "contract": ["contract", "common"],
    "purchasing": ["purchasing", "common"],
    "core": ["common", "company"],
    "platform": ["platform", "common"],
    "reports": ["reports", "common"],
    "cre": ["cre", "common"],
    "ee": ["ee", "expenses", "common"],
}


@dataclass
class LinkStats:
    mappings_inserted: int = 0
    unmatched_rows: int = 0
    mapped_to_matches: int = 0
    mapped_to_unresolved: int = 0
    mapped_to_suppressed: int = 0
    mapped_to_invalid: int = 0
    heuristic_total: int = 0
    heuristic_suppressed_expected_missing_mapped_to: int = 0
    heuristic_logged: int = 0
    heuristic_suppressed_by_class: dict[str, int] = field(default_factory=dict)


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        LIMIT 1
        """,
        (table_name,),
    ).fetchone()
    return bool(row)


def _get_entities_by_name(
    conn: sqlite3.Connection, repo_id: int
) -> dict[str, dict[str, list[int]]]:
    rows = conn.execute(
        """
        SELECT DISTINCT en.id, en.name, eo.module
        FROM entity_nodes en
        JOIN entity_occurrences eo
          ON eo.entity_id = en.id
        JOIN entity_mappings em
          ON em.repo_id = eo.repo_id
         AND em.entity_id = en.id
        WHERE em.repo_id = ?
        """,
        (repo_id,),
    ).fetchall()
    entities_by_module: dict[str, dict[str, list[int]]] = {}
    for row in rows:
        name = str(row["name"] or "").strip()
        if not name:
            continue
        module = _normalize_module(str(row["module"] or ""))
        key = _normalize_name(name)
        entities_by_module.setdefault(module, {}).setdefault(key, []).append(
            int(row["id"])
        )
    return entities_by_module


def _get_entities_across_modules(
    entities_by_module: dict[str, dict[str, list[int]]],
) -> dict[str, list[tuple[int, str]]]:
    by_name: dict[str, list[tuple[int, str]]] = {}
    for module, names in entities_by_module.items():
        for normalized_name, entity_ids in names.items():
            for entity_id in entity_ids:
                by_name.setdefault(normalized_name, []).append((entity_id, module))
    return by_name


def _normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _normalize_module(value: str) -> str:
    return value.strip().lower().replace("_", "-")


def _module_candidates(module: str) -> list[str]:
    module_key = _normalize_module(module)
    candidates = [module_key]
    aliases = {
        "ap": "apar",
        "ar": "apar",
        "co": "company",
        "inv": "inventory",
    }
    alias_target = aliases.get(module_key)
    if alias_target and alias_target not in candidates:
        candidates.append(alias_target)
    for fallback in MODULE_SCOPE_FALLBACKS.get(module_key, []):
        normalized = _normalize_module(fallback)
        if normalized and normalized not in candidates:
            candidates.append(normalized)
    return [candidate for candidate in candidates if candidate]


def _split_slug_parts(value: str) -> list[str]:
    return [part for part in re.split(r"[./]", value.lower()) if part]


def _segment_acronym(value: str) -> str:
    parts = [part for part in re.split(r"[-_]", value.lower()) if part]
    if len(parts) < 2:
        return ""
    return "".join(part[0] for part in parts if part)


def _openapi_name_candidates(
    canonical_name: str,
    slug: str,
    resource_path: str,
) -> list[str]:
    out: list[str] = []

    def add(raw: str | None) -> None:
        if not raw:
            return
        key = _normalize_name(raw)
        if key and key not in out:
            out.append(key)

    # Rule 1: canonical_name normalized case-insensitively.
    # Example: "billing-group-period" -> "billinggroupperiod".
    add(canonical_name)
    for variant in _canonical_name_variants(canonical_name):
        add(variant)

    # Rule 2: slug segment normalization.
    # Example: "accounts-payable.ap-bill.s1.api.yaml" -> "apbill".
    slug_parts = _split_slug_parts(slug)
    metadata_parts = {
        "api",
        "schema",
        "history",
        "yaml",
        "view",
        "uimeta",
        "s1",
        "s2",
        "systemfw1",
        "systemfw2",
        "objects",
        "services",
        "workflows",
        "actions",
        "events",
        "components",
    }
    if len(slug_parts) >= 2:
        for part in slug_parts[1:]:
            if part in metadata_parts:
                continue
            add(part)
        # Rule 2b: domain+object condensed key.
        # Example: accounts-payable.bill -> apbill
        for idx in range(len(slug_parts) - 1):
            left = slug_parts[idx]
            right = slug_parts[idx + 1]
            if left in metadata_parts or right in metadata_parts:
                continue
            if "-" not in left:
                continue
            acronym = _segment_acronym(left)
            if acronym:
                add(f"{acronym}-{right}")
    for part in slug_parts:
        if part in metadata_parts:
            continue
        add(part)

    # Rule 3: resource path entity extraction.
    # Example: "/services/v3/objects/ap-bill" -> "apbill".
    path_parts = [part for part in resource_path.lower().split("/") if part]
    if "objects" in path_parts:
        idx = path_parts.index("objects")
        if idx + 1 < len(path_parts):
            add(path_parts[idx + 1])
        if idx + 2 < len(path_parts):
            add(path_parts[idx + 2])
            # Rule 3b: object path condensed key.
            # Example: /objects/accounts-payable/bill -> apbill
            domain = path_parts[idx + 1]
            obj = path_parts[idx + 2]
            acronym = _segment_acronym(domain)
            if acronym:
                add(f"{acronym}-{obj}")
    for part in path_parts:
        if part in metadata_parts:
            continue
        add(part)

    return out


def _canonical_name_variants(canonical_name: str) -> list[str]:
    variants: list[str] = []
    parts = [part for part in re.split(r"[-_/]", canonical_name.lower()) if part]
    if len(parts) < 2:
        return variants

    # Rule 4: canonical suffix stripping for synthetic descriptor suffixes.
    # Example: "payment-provider-bank-account" -> "payment-provider-bank".
    trimmed = parts[:]
    while len(trimmed) > 1 and trimmed[-1] in LOW_SIGNAL_CANONICAL_SUFFIXES:
        trimmed = trimmed[:-1]
        variants.append("-".join(trimmed))

    # Rule 5: canonical prefix collapse for hierarchical names.
    # Example: "document-line-detail" -> "document-line", then "document".
    for idx in range(len(parts) - 1, 1, -1):
        variants.append("-".join(parts[:idx]))

    # Rule 6: singularization for plural nouns.
    # Example: "documents" -> "document".
    if parts[-1].endswith("s") and len(parts[-1]) > 4:
        singular = parts[:-1] + [parts[-1][:-1]]
        variants.append("-".join(singular))

    deduped: list[str] = []
    for value in variants:
        key = _normalize_name(value)
        if key and key not in deduped:
            deduped.append(value)
    return deduped


def _insert_mapping(
    conn: sqlite3.Connection,
    repo_id: int,
    entity_id: int,
    file_id: int,
    mapping_type: str,
    source_text: str,
) -> bool:
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
        SELECT ?, ?, NULL, ?, ?, 1.0, ?
        WHERE NOT EXISTS (
            SELECT 1
            FROM entity_mappings
            WHERE repo_id = ?
              AND entity_id = ?
              AND symbol_id IS NULL
              AND file_id = ?
              AND mapping_type = ?
              AND source_text = ?
        )
        """,
        (
            repo_id,
            entity_id,
            file_id,
            mapping_type,
            source_text,
            repo_id,
            entity_id,
            file_id,
            mapping_type,
            source_text,
        ),
    )
    return cur.rowcount > 0


def _resolve_mapped_to_entity(
    mapped_to: str,
    module_keys: list[str],
    entities_across_modules: dict[str, list[tuple[int, str]]],
) -> int | None:
    key = _normalize_name(mapped_to)
    if not key:
        return None

    candidates = entities_across_modules.get(key, [])
    if len(candidates) == 1:
        return candidates[0][0]

    if len(candidates) > 1:
        scoped = [
            entity_id for entity_id, module in candidates if module in module_keys
        ]
        if len(scoped) == 1:
            return scoped[0]

    return None


def _append_missing_metadata_records(
    records: list[dict], log_path: Path | None = MISSING_METADATA_LOG_PATH
) -> None:
    if not records or log_path is None:
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _load_valid_ent_stems(repo_path: Path) -> set[str]:
    app_source_dir = repo_path / "app" / "source"
    if not app_source_dir.exists():
        return set()

    return {
        _normalize_name(path.stem)
        for path in app_source_dir.rglob("*.ent")
        if path.stem
    }


def _expected_missing_mapped_to_class(file_path: str) -> str | None:
    file_name = Path(file_path or "").name.lower()
    if not file_name:
        return None
    if file_name.endswith(".uimeta.yaml"):
        return "uimeta"
    if file_name.endswith(".history.yaml"):
        return "history"
    if file_name.endswith(".api.yaml"):
        return "api"
    if file_name.endswith(".view.schema.yaml"):
        return "view_schema"
    if file_name.endswith(".view.yaml"):
        return "view"
    if file_name.startswith("workflows.") and file_name.endswith(".yaml"):
        return "workflows"
    if file_name.startswith("services.") and file_name.endswith(".yaml"):
        return "services"
    return None


def _should_suppress_mapped_to_log(mapped_to: str, valid_ent_stems: set[str]) -> bool:
    normalized_mapped_to = _normalize_name(mapped_to)
    if mapped_to.strip().lower() == "__custom__":
        return True
    if not normalized_mapped_to:
        return True
    return normalized_mapped_to not in valid_ent_stems


def _read_entity_definitions_jsonl(jsonl_path: Path) -> list[dict]:
    rows: list[dict] = []
    if not jsonl_path.exists():
        return rows

    with jsonl_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                rows.append(data)
    return rows


def _reconcile_with_entity_definitions(
    conn: sqlite3.Connection,
    jsonl_path: Path,
    repo_id: int,
) -> list[dict]:
    diagnostics: list[dict] = []
    now = datetime.now(UTC).isoformat()
    rows = _read_entity_definitions_jsonl(jsonl_path)

    by_mapped_to_jsonl: dict[str, dict] = {}
    for row in rows:
        mapped_to = str(row.get("x_mapped_to") or "").strip().lower()
        if mapped_to and mapped_to not in by_mapped_to_jsonl:
            by_mapped_to_jsonl[mapped_to] = row

    openapi_rows = conn.execute(
        """
        SELECT id, file_path, module, x_mapped_to
        FROM openapispec_index
        WHERE repo_id = ? AND state = 'active' AND COALESCE(TRIM(x_mapped_to), '') <> ''
        """,
        (repo_id,),
    ).fetchall()

    seen_db_mapped_to: set[str] = set()
    for row in openapi_rows:
        mapped_to = str(row["x_mapped_to"] or "").strip().lower()
        if not mapped_to:
            continue
        seen_db_mapped_to.add(mapped_to)

        jsonl_row = by_mapped_to_jsonl.get(mapped_to)
        if not jsonl_row:
            diagnostics.append(
                {
                    "context": {
                        "jsonl_path": jsonl_path.as_posix(),
                        "openapispec_file": row["file_path"],
                        "openapispec_id": row["id"],
                    },
                    "entity_name": None,
                    "file_path": row["file_path"],
                    "reason": f"x_mapped_to '{mapped_to}' present in openapispec_index but missing in entity_definitions",
                    "source": "link_openapispec",
                    "stage": "reconcile_jsonl",
                    "timestamp": now,
                }
            )
            continue

        jsonl_module = str(jsonl_row.get("openapi_module") or "").strip().lower()
        db_module = str(row["module"] or "").strip().lower()
        if jsonl_module and db_module and jsonl_module != db_module:
            diagnostics.append(
                {
                    "context": {
                        "jsonl_openapi_module": jsonl_module,
                        "jsonl_path": jsonl_path.as_posix(),
                        "openapispec_id": row["id"],
                    },
                    "entity_name": jsonl_row.get("entity_name"),
                    "file_path": row["file_path"],
                    "reason": f"module mismatch for x_mapped_to '{mapped_to}': db='{db_module}' jsonl='{jsonl_module}'",
                    "source": "link_openapispec",
                    "stage": "reconcile_jsonl",
                    "timestamp": now,
                }
            )

    for mapped_to, row in by_mapped_to_jsonl.items():
        if mapped_to in seen_db_mapped_to:
            continue
        diagnostics.append(
            {
                "context": {
                    "jsonl_path": jsonl_path.as_posix(),
                },
                "entity_name": row.get("entity_name"),
                "file_path": row.get("ent_file"),
                "reason": f"x_mapped_to '{mapped_to}' present in entity_definitions but missing in openapispec_index",
                "source": "link_openapispec",
                "stage": "reconcile_jsonl",
                "timestamp": now,
            }
        )

    return diagnostics


def _link_openapispec(
    conn: sqlite3.Connection,
    repo_root: Path,
    repo_id: int,
    reconcile_jsonl_path: Path | None,
    missing_metadata_log: Path | None = MISSING_METADATA_LOG_PATH,
) -> LinkStats:
    if not _table_exists(conn, "openapispec_index"):
        raise click.ClickException(
            "Required table openapispec_index is missing. Run scan_openapispec.py first."
        )

    rows = conn.execute(
        """
        SELECT
            id,
            file_id,
            file_path,
            canonical_name,
            kind,
            slug,
            module,
            resource_path,
            x_mapped_to
        FROM openapispec_index
        WHERE repo_id = ? AND state = 'active'
        """,
        (repo_id,),
    ).fetchall()

    stats = LinkStats()
    missing_records: list[dict] = []
    now = datetime.now(UTC).isoformat()
    entities_by_name = _get_entities_by_name(conn, repo_id)
    entities_across_modules = _get_entities_across_modules(entities_by_name)
    valid_ent_stems = _load_valid_ent_stems(repo_root)
    for row in rows:
        module_keys = _module_candidates(str(row["module"] or ""))
        mapped_to = str(row["x_mapped_to"] or "").strip()
        entity_id = None
        mapped_to_normalized = _normalize_name(mapped_to) if mapped_to else ""

        if mapped_to:
            # Hard gate: x_mapped_to must reference a known .ent stem.
            if mapped_to_normalized not in valid_ent_stems:
                stats.mapped_to_invalid += 1
                stats.mapped_to_suppressed += 1
                stats.unmatched_rows += 1
                missing_records.append(
                    {
                        "context": {
                            "module_candidates": module_keys,
                            "openapispec_id": row["id"],
                        },
                        "entity_name": None,
                        "file_path": row["file_path"],
                        "reason": f"invalid x_mapped_to '{mapped_to}' (not a valid .ent stem)",
                        "source": "link_openapispec",
                        "stage": "mapped_to_validation",
                        "timestamp": now,
                    }
                )
                # Do not allow heuristic fallback when explicit mappedTo is invalid.
                continue

            entity_id = _resolve_mapped_to_entity(
                mapped_to=mapped_to,
                module_keys=module_keys,
                entities_across_modules=entities_across_modules,
            )
            if entity_id is not None:
                stats.mapped_to_matches += 1
            else:
                stats.mapped_to_unresolved += 1
                if _should_suppress_mapped_to_log(mapped_to, valid_ent_stems):
                    stats.mapped_to_suppressed += 1
                else:
                    missing_records.append(
                        {
                            "context": {
                                "module_candidates": module_keys,
                                "openapispec_id": row["id"],
                            },
                            "entity_name": None,
                            "file_path": row["file_path"],
                            "reason": f"x_mapped_to '{mapped_to}' did not resolve to a unique entity",
                            "source": "link_openapispec",
                            "stage": "mapped_to_resolution",
                            "timestamp": now,
                        }
                    )

        # Fallback to heuristic matching only when mappedTo is absent or unresolved.
        candidate_names = _openapi_name_candidates(
            canonical_name=str(row["canonical_name"] or ""),
            slug=str(row["slug"] or ""),
            resource_path=str(row["resource_path"] or ""),
        )
        if entity_id is None:
            for module_key in module_keys:
                module_entities = entities_by_name.get(module_key, {})
                for candidate in candidate_names:
                    matches = module_entities.get(candidate, [])
                    if len(matches) == 1:
                        entity_id = matches[0]
                        break
                if entity_id is not None:
                    break

        if entity_id is None:
            stats.unmatched_rows += 1
            if not mapped_to:
                stats.heuristic_total += 1
                expected_class = _expected_missing_mapped_to_class(
                    str(row["file_path"] or "")
                )
                if expected_class is not None:
                    stats.heuristic_suppressed_expected_missing_mapped_to += 1
                    stats.heuristic_suppressed_by_class[expected_class] = (
                        stats.heuristic_suppressed_by_class.get(expected_class, 0) + 1
                    )
                else:
                    stats.heuristic_logged += 1
                    missing_records.append(
                        {
                            "context": {
                                "candidate_names": candidate_names,
                                "module_candidates": module_keys,
                                "openapispec_id": row["id"],
                            },
                            "entity_name": None,
                            "file_path": row["file_path"],
                            "reason": "unable to match openapispec row via heuristic candidates",
                            "source": "link_openapispec",
                            "stage": "heuristic_resolution",
                            "timestamp": now,
                        }
                    )
            continue

        file_id = row["file_id"]
        if file_id is None:
            stats.unmatched_rows += 1
            continue

        mapping_type = f"openapispec_{row['kind']}"
        inserted = _insert_mapping(
            conn=conn,
            repo_id=repo_id,
            entity_id=entity_id,
            file_id=int(file_id),
            mapping_type=mapping_type,
            source_text=str(row["file_path"] or ""),
        )
        if inserted:
            stats.mappings_inserted += 1

    if reconcile_jsonl_path is not None:
        missing_records.extend(
            _reconcile_with_entity_definitions(conn, reconcile_jsonl_path, repo_id)
        )

    _append_missing_metadata_records(
        records=missing_records, log_path=missing_metadata_log
    )

    return stats


@click.group()
def cli() -> None:
    pass


@cli.command("link")
@click.option("--repo", required=True, help="Registered repository key to link.")
@click.option(
    "--db",
    default=DEFAULT_DB,
    show_default=True,
    help="Path to SQLite catalog database.",
)
@click.option(
    "--reset", is_flag=True, help="Delete OpenAPI-derived mappings before relinking."
)
@click.option(
    "--repo-root",
    type=click.Path(path_type=Path, file_okay=False),
    default=None,
    help="Repository root used for diagnostics log output.",
)
@click.option(
    "--reconcile-jsonl",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    help="Optional path to entity_definitions.jsonl for drift diagnostics only.",
)
def link_command(
    db: str,
    repo: str,
    reset: bool,
    repo_root: Path | None,
    reconcile_jsonl: Path | None,
) -> None:
    conn = get_connection(db)
    try:
        repository = get_repository(conn, repo)
        resolved_root = (
            repo_root.resolve()
            if repo_root is not None
            else resolve_repository_root(conn, repo)
        )
        # OpenAPI mappings are a materialized projection of the current index.
        # Rebuild them on every run so stale mappings cannot survive metadata
        # changes when callers omit the compatibility --reset flag.
        placeholders = ", ".join(["?"] * len(OPENAPI_MAPPING_TYPES))
        conn.execute(
            f"DELETE FROM entity_mappings WHERE repo_id = ? AND mapping_type IN ({placeholders})",
            (int(repository["id"]), *OPENAPI_MAPPING_TYPES),
        )

        stats = _link_openapispec(
            conn=conn,
            repo_root=resolved_root,
            repo_id=int(repository["id"]),
            reconcile_jsonl_path=reconcile_jsonl.resolve()
            if reconcile_jsonl is not None
            else None,
        )
        conn.commit()
    finally:
        conn.close()

    click.echo(f"OpenAPI mappings inserted:   {stats.mappings_inserted}")
    click.echo(f"Unmatched openapispec rows: {stats.unmatched_rows}")
    click.echo(f"x_mapped_to matches:        {stats.mapped_to_matches}")
    click.echo(f"x_mapped_to unresolved:     {stats.mapped_to_unresolved}")
    click.echo(f"x_mapped_to suppressed:     {stats.mapped_to_suppressed}")
    click.echo(f"x_mapped_to invalid:        {stats.mapped_to_invalid}")
    click.echo(f"heuristic_total:            {stats.heuristic_total}")
    click.echo(
        "heuristic_suppressed_expected_missing_mapped_to: "
        f"{stats.heuristic_suppressed_expected_missing_mapped_to}"
    )
    click.echo(f"heuristic_logged:           {stats.heuristic_logged}")
    if stats.heuristic_suppressed_by_class:
        click.echo("heuristic_suppressed_by_class:")
        for key in sorted(stats.heuristic_suppressed_by_class):
            click.echo(f"  {key}: {stats.heuristic_suppressed_by_class[key]}")


if __name__ == "__main__":
    cli()
