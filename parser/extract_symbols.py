# parser/extract_symbols.py

from datetime import datetime, timezone
from pathlib import Path
from tqdm import tqdm

from config import REPO_PATH
from catalog.db import get_connection
from parser.extractors import (
    java_extractor,
    php_extractor,
    sql_extractor,
    xslt_extractor,
)

EXTRACTORS = {
    "java": java_extractor,
    "php":  php_extractor,
    "sql":  sql_extractor,
    "xslt": xslt_extractor,
}


def extract_all(only_changed: bool = True):
    conn = get_connection()
    cur = conn.cursor()

    started = datetime.now(timezone.utc).isoformat()

    placeholders = ",".join(["?"] * len(EXTRACTORS))
    lang_tuple = tuple(EXTRACTORS.keys())

    if only_changed:
        # A file needs (re-)extraction if:
        #   1. It has never been extracted, OR
        #   2. It's been re-scanned since the last extraction
        rows = cur.execute(f"""
            SELECT id, path, language
            FROM files
            WHERE language IN ({placeholders})
              AND (
                    last_symbols_extracted IS NULL
                 OR last_indexed > last_symbols_extracted
              )
        """, lang_tuple).fetchall()
    else:
        rows = cur.execute(f"""
            SELECT id, path, language
            FROM files
            WHERE language IN ({placeholders})
        """, lang_tuple).fetchall()

    print(f"🔎 Extracting symbols from {len(rows)} files")

    total_symbols = 0
    errors = 0

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
            if hasattr(extractor, 'extract') and extractor.extract.__code__.co_argcount > 1:
                symbols = extractor.extract(source, rel_path)
            else:
                symbols = extractor.extract(source)
            for s in symbols:
                cur.execute("""
                    INSERT INTO symbols
                    (file_id, name, kind, parent_symbol,
                     start_line, end_line, signature, language)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    file_id, s.name, s.kind, s.parent_symbol,
                    s.start_line, s.end_line, s.signature, s.language
                ))
                total_symbols += 1

            # ✅ Only stamp on success — failed files remain unmarked
            #     so they'll be retried on the next incremental run.
            cur.execute(
                "UPDATE files SET last_symbols_extracted = ? WHERE id = ?",
                (started, file_id)
            )

            if total_symbols % 5000 == 0:
                conn.commit()

        except Exception as e:
            errors += 1
            print(f"⚠️  {rel_path}: {e}")

    completed = datetime.now(timezone.utc).isoformat()

    cur.execute("""
        INSERT INTO symbol_extraction_runs
        (started_at, completed_at, files_processed,
         symbols_extracted, errors)
        VALUES (?, ?, ?, ?, ?)
    """, (started, completed, len(rows), total_symbols, errors))

    conn.commit()
    conn.close()

    print(f"\n📊 Symbols extracted: {total_symbols}")
    print(f"   Errors:            {errors}")


if __name__ == "__main__":
    import sys
    full = "--full" in sys.argv
    extract_all(only_changed=not full)
