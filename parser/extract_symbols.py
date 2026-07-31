import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tqdm import tqdm

from catalog.db import get_connection, require_foreign_key_integrity
from catalog.migrations import symbol_stable_key
from parser.extractors import (
    java_extractor,
    php_extractor,
    sql_extractor,
    xslt_extractor,
    yaml_extractor,
)
from parser.repo_context import require_repo_scoped_files, resolve_repo

EXTRACTORS = {
    "java": java_extractor,
    "php": php_extractor,
    "sql": sql_extractor,
    "yaml": yaml_extractor,
    "xslt": xslt_extractor,
}

OUTPUT_DIR = Path("outputs")
YAML_PARSE_FAILURES_LOG = OUTPUT_DIR / "yaml_parse_failures.jsonl"


@dataclass(frozen=True)
class SymbolChangeSummary:
    added_ids: tuple[int, ...] = ()
    changed_ids: tuple[int, ...] = ()
    deleted_ids: tuple[int, ...] = ()
    added_names: tuple[str, ...] = ()
    changed_names: tuple[str, ...] = ()
    deleted_names: tuple[str, ...] = ()

    @property
    def affected_count(self) -> int:
        return len(self.added_ids) + len(self.changed_ids) + len(self.deleted_ids)


def write_jsonl(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")


def extract_all(
    only_changed: bool = True,
    languages: list[str] | None = None,
    repo_key: str | None = None,
    db_path: str | None = None,
    write_logs: bool = True,
    file_ids: list[int] | tuple[int, ...] | set[int] | None = None,
) -> SymbolChangeSummary:
    conn = get_connection(db_path)
    cur = conn.cursor()
    require_repo_scoped_files(conn)
    repo = resolve_repo(conn, repo_key)

    started = datetime.now(UTC).isoformat()

    selected_languages = [
        lang for lang in (languages or list(EXTRACTORS.keys())) if lang in EXTRACTORS
    ]
    if not selected_languages:
        print("⚠️  No valid extractors selected.")
        conn.close()
        return SymbolChangeSummary()

    placeholders = ",".join(["?"] * len(selected_languages))
    lang_tuple = tuple(selected_languages)

    if file_ids is not None:
        normalized_ids = sorted({int(file_id) for file_id in file_ids})
        if normalized_ids:
            id_placeholders = ",".join("?" for _ in normalized_ids)
            rows = cur.execute(
                f"""SELECT id,path,language FROM files
                    WHERE repo_id=? AND id IN ({id_placeholders})
                      AND language IN ({placeholders}) ORDER BY id""",
                (repo.id, *normalized_ids, *lang_tuple),
            ).fetchall()
        else:
            rows = []
    elif only_changed:
        # A file needs (re-)extraction if:
        #   1. It has never been extracted, OR
        #   2. It's been re-scanned since the last extraction
        rows = cur.execute(
            f"""
            SELECT id, path, language
            FROM files
            WHERE repo_id = ?
              AND language IN ({placeholders})
              AND (
                    last_symbols_extracted IS NULL
                 OR last_indexed > last_symbols_extracted
              )
        """,
            (repo.id, *lang_tuple),
        ).fetchall()
    else:
        rows = cur.execute(
            f"""
            SELECT id, path, language
            FROM files
            WHERE repo_id = ?
              AND language IN ({placeholders})
        """,
            (repo.id, *lang_tuple),
        ).fetchall()

    print(f"🔎 Extracting symbols from {len(rows)} files")

    total_symbols = 0
    errors = 0
    error_messages: list[str] = []
    added_ids: list[int] = []
    changed_ids: list[int] = []
    deleted_ids: list[int] = []
    added_names: list[str] = []
    changed_names: list[str] = []
    deleted_names: list[str] = []

    if "yaml" in selected_languages and hasattr(yaml_extractor, "reset_stats"):
        yaml_extractor.reset_stats()

    for row in tqdm(rows, desc="Extracting"):
        file_id = row["id"]
        rel_path = row["path"]
        language = row["language"]
        abs_path = repo.local_root / rel_path

        extractor = EXTRACTORS.get(language)
        if not extractor:
            continue

        try:
            cur.execute("SAVEPOINT symbol_file")
            with open(abs_path, "rb") as f:
                source = f.read()

            # Pass file path to extractor for format-specific delegation (e.g., .cqry -> cqry_extractor)
            if (
                hasattr(extractor, "extract")
                and extractor.extract.__code__.co_argcount > 1
            ):
                symbols = extractor.extract(source, rel_path)
            else:
                symbols = extractor.extract(source)
            staged: list[tuple[str, object]] = []
            ordinals: dict[tuple[object, ...], int] = {}
            for symbol in symbols:
                identity = (
                    symbol.kind,
                    symbol.name,
                    symbol.parent_symbol,
                    symbol.signature,
                )
                ordinal = ordinals.get(identity, 0)
                ordinals[identity] = ordinal + 1
                staged.append(
                    (
                        symbol_stable_key(
                            kind=symbol.kind,
                            name=symbol.name,
                            parent_symbol=symbol.parent_symbol,
                            signature=symbol.signature,
                            duplicate_ordinal=ordinal,
                        ),
                        symbol,
                    )
                )

            existing = {
                str(existing_row["stable_key"]): existing_row
                for existing_row in cur.execute(
                    """SELECT id,name,kind,parent_symbol,start_line,end_line,
                              signature,language,stable_key
                       FROM symbols WHERE file_id=?""",
                    (file_id,),
                ).fetchall()
                if existing_row["stable_key"] is not None
            }
            staged_keys: set[str] = set()
            for stable_key, symbol in staged:
                staged_keys.add(stable_key)
                old = existing.get(stable_key)
                values = (
                    symbol.name,
                    symbol.kind,
                    symbol.parent_symbol,
                    symbol.start_line,
                    symbol.end_line,
                    symbol.signature,
                    symbol.language,
                )
                if old is None:
                    inserted = cur.execute(
                        """INSERT INTO symbols(
                               file_id,name,kind,parent_symbol,start_line,end_line,
                               signature,language,stable_key
                           ) VALUES (?,?,?,?,?,?,?,?,?)""",
                        (file_id, *values, stable_key),
                    )
                    added_ids.append(int(inserted.lastrowid))
                    added_names.append(str(symbol.name))
                else:
                    old_values = tuple(
                        old[column]
                        for column in (
                            "name",
                            "kind",
                            "parent_symbol",
                            "start_line",
                            "end_line",
                            "signature",
                            "language",
                        )
                    )
                    cur.execute(
                        """UPDATE symbols SET name=?,kind=?,parent_symbol=?,
                               start_line=?,end_line=?,signature=?,language=?
                           WHERE id=?""",
                        (*values, int(old["id"])),
                    )
                    if old_values != values:
                        changed_ids.append(int(old["id"]))
                        changed_names.append(str(symbol.name))
                total_symbols += 1

            stale = [row for key, row in existing.items() if key not in staged_keys]
            if stale:
                stale_ids = [int(old["id"]) for old in stale]
                deleted_ids.extend(stale_ids)
                deleted_names.extend(str(old["name"]) for old in stale)
                delete_placeholders = ",".join("?" for _ in stale_ids)
                cur.execute(
                    f"DELETE FROM symbols WHERE id IN ({delete_placeholders})",
                    stale_ids,
                )

            # ✅ Only stamp on success — failed files remain unmarked
            #     so they'll be retried on the next incremental run.
            cur.execute(
                "UPDATE files SET last_symbols_extracted = ? WHERE id = ?",
                (started, file_id),
            )
            cur.execute("RELEASE SAVEPOINT symbol_file")

            if total_symbols % 5000 == 0:
                conn.commit()

        except Exception as e:
            cur.execute("ROLLBACK TO SAVEPOINT symbol_file")
            cur.execute("RELEASE SAVEPOINT symbol_file")
            errors += 1
            message = f"{rel_path}: {e}"
            error_messages.append(message)
            print(f"⚠️  {message}")

    require_foreign_key_integrity(conn, context="symbol extraction")
    conn.commit()
    conn.close()

    print(f"\n📊 Symbols extracted: {total_symbols}")
    print(f"   Errors:            {errors}")
    if "yaml" in selected_languages and hasattr(yaml_extractor, "get_stats"):
        yaml_stats = yaml_extractor.get_stats()
        print(f"   YAML files seen:   {yaml_stats.get('files_seen', 0)}")
        print(f"   YAML parse fail:   {yaml_stats.get('parse_failures', 0)}")
        print(f"   YAML emitted:      {yaml_stats.get('symbols_emitted', 0)}")

        parse_failures: list[dict[str, str]] = []
        if hasattr(yaml_extractor, "get_parse_failures"):
            parse_failures = yaml_extractor.get_parse_failures()
        if write_logs:
            write_jsonl(YAML_PARSE_FAILURES_LOG, parse_failures)
            print(f"   YAML parse fail log: {YAML_PARSE_FAILURES_LOG.as_posix()}")

    if error_messages:
        raise RuntimeError(
            f"symbol extraction failed for {len(error_messages)} file(s): "
            + "; ".join(error_messages[:3])
        )
    return SymbolChangeSummary(
        tuple(added_ids),
        tuple(changed_ids),
        tuple(deleted_ids),
        tuple(sorted(set(added_names))),
        tuple(sorted(set(changed_names))),
        tuple(sorted(set(deleted_names))),
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--full",
        action="store_true",
        help="Extract symbols for all files, not only changed files.",
    )
    parser.add_argument(
        "--language",
        action="append",
        choices=sorted(EXTRACTORS.keys()),
        help="Limit extraction to one or more languages (repeat flag to pass multiple).",
    )
    parser.add_argument("--repo", help="Registered repo_key to extract")
    parser.add_argument("--db", help="Catalog database path")
    args = parser.parse_args()

    extract_all(
        only_changed=not args.full,
        languages=args.language,
        repo_key=args.repo,
        db_path=args.db,
    )
