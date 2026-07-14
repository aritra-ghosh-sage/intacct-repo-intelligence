import json

from datetime import datetime, timezone
from pathlib import Path
from tqdm import tqdm

from config import REPO_PATH
from catalog.db import get_connection
from parser.extractors import (
    java_extractor,
    php_extractor,
    sql_extractor,
    yaml_extractor,
    xslt_extractor,
)

EXTRACTORS = {
    "java": java_extractor,
    "php": php_extractor,
    "sql": sql_extractor,
    "yaml": yaml_extractor,
    "xslt": xslt_extractor,
}

OUTPUT_DIR = Path("outputs")
YAML_PARSE_FAILURES_LOG = OUTPUT_DIR / "yaml_parse_failures.jsonl"


def write_jsonl(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")


def extract_all(only_changed: bool = True, languages: list[str] | None = None):
    conn = get_connection()
    cur = conn.cursor()

    started = datetime.now(timezone.utc).isoformat()

    selected_languages = [
        lang for lang in (languages or list(EXTRACTORS.keys())) if lang in EXTRACTORS
    ]
    if not selected_languages:
        print("⚠️  No valid extractors selected.")
        conn.close()
        return

    placeholders = ",".join(["?"] * len(selected_languages))
    lang_tuple = tuple(selected_languages)

    if only_changed:
        # A file needs (re-)extraction if:
        #   1. It has never been extracted, OR
        #   2. It's been re-scanned since the last extraction
        rows = cur.execute(
            f"""
            SELECT id, path, language
            FROM files
            WHERE language IN ({placeholders})
              AND (
                    last_symbols_extracted IS NULL
                 OR last_indexed > last_symbols_extracted
              )
        """,
            lang_tuple,
        ).fetchall()
    else:
        rows = cur.execute(
            f"""
            SELECT id, path, language
            FROM files
            WHERE language IN ({placeholders})
        """,
            lang_tuple,
        ).fetchall()

    print(f"🔎 Extracting symbols from {len(rows)} files")

    total_symbols = 0
    errors = 0

    if "yaml" in selected_languages and hasattr(yaml_extractor, "reset_stats"):
        yaml_extractor.reset_stats()

    for row in tqdm(rows, desc="Extracting"):
        file_id = row["id"]
        rel_path = row["path"]
        language = row["language"]
        abs_path = Path(REPO_PATH) / rel_path

        extractor = EXTRACTORS.get(language)
        if not extractor:
            continue

        try:
            with open(abs_path, "rb") as f:
                source = f.read()

            # Remove old symbols for this file (idempotent re-extraction)
            cur.execute("DELETE FROM symbols WHERE file_id = ?", (file_id,))

            # Pass file path to extractor for format-specific delegation (e.g., .cqry -> cqry_extractor)
            if (
                hasattr(extractor, "extract")
                and extractor.extract.__code__.co_argcount > 1
            ):
                symbols = extractor.extract(source, rel_path)
            else:
                symbols = extractor.extract(source)
            for s in symbols:
                cur.execute(
                    """
                    INSERT INTO symbols
                    (file_id, name, kind, parent_symbol,
                     start_line, end_line, signature, language)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        file_id,
                        s.name,
                        s.kind,
                        s.parent_symbol,
                        s.start_line,
                        s.end_line,
                        s.signature,
                        s.language,
                    ),
                )
                total_symbols += 1

            # ✅ Only stamp on success — failed files remain unmarked
            #     so they'll be retried on the next incremental run.
            cur.execute(
                "UPDATE files SET last_symbols_extracted = ? WHERE id = ?",
                (started, file_id),
            )

            if total_symbols % 5000 == 0:
                conn.commit()

        except Exception as e:
            errors += 1
            print(f"⚠️  {rel_path}: {e}")

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
        write_jsonl(YAML_PARSE_FAILURES_LOG, parse_failures)
        print(f"   YAML parse fail log: {YAML_PARSE_FAILURES_LOG.as_posix()}")


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
    args = parser.parse_args()

    extract_all(only_changed=not args.full, languages=args.language)
