"""V1 symbol extraction from an immutable source snapshot."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from tqdm import tqdm

from catalog.source_snapshot import SourceSnapshot
from parser.extractors import (
    java_extractor,
    javascript_extractor,
    php_extractor,
    sql_extractor,
    xslt_extractor,
    yaml_extractor,
)

EXTRACTORS = {
    "java": java_extractor,
    "javascript": javascript_extractor,
    "php": php_extractor,
    "sql": sql_extractor,
    "yaml": yaml_extractor,
    "xslt": xslt_extractor,
}
_PATH_AWARE_LANGUAGES = {"javascript", "php", "yaml"}


def _stable_key(
    *,
    kind: str | None,
    name: str | None,
    parent_symbol: str | None,
    signature: str | None,
    duplicate_ordinal: int,
) -> str:
    payload = json.dumps(
        [kind or "", name or "", parent_symbol or "", signature or "", duplicate_ordinal],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _diagnostic_key(file_id: int, code: str, message: str, source_sha: str) -> str:
    payload = json.dumps(
        [file_id, code, message, source_sha], ensure_ascii=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _record_diagnostic(
    conn: sqlite3.Connection,
    *,
    repo_id: int,
    file_id: int,
    source_sha: str,
    code: str,
    message: str,
) -> None:
    conn.execute(
        """INSERT INTO symbol_diagnostics(
               repo_id,file_id,diagnostic_key,severity,code,message,source_commit_sha
           ) VALUES(?,?,?,?,?,?,?)""",
        (
            repo_id,
            file_id,
            _diagnostic_key(file_id, code, message, source_sha),
            "error",
            code,
            message,
            source_sha,
        ),
    )


def _extract(extractor: object, source: bytes, path: str, language: str) -> list[object]:
    extract = extractor.extract
    if language in _PATH_AWARE_LANGUAGES:
        return list(extract(source, path))
    return list(extract(source))


def extract_snapshot_symbols(
    conn: sqlite3.Connection,
    *,
    repo_id: int,
    snapshot: SourceSnapshot,
    show_progress: bool = False,
) -> dict[str, int]:
    """Extract symbols using only bytes materialized from ``snapshot``."""

    conn.execute("DELETE FROM symbol_diagnostics WHERE repo_id=?", (repo_id,))
    conn.execute("DELETE FROM symbols WHERE repo_id=?", (repo_id,))
    file_rows = {
        str(row["path"]): row
        for row in conn.execute(
            "SELECT id,path,language,source_commit_sha FROM files WHERE repo_id=?",
            (repo_id,),
        ).fetchall()
    }
    symbols_count = 0
    diagnostic_count = 0
    for entry in tqdm(
        snapshot.entries,
        desc="Extracting V1 symbols",
        unit="file",
        disable=not show_progress,
    ):
        file_row = file_rows.get(entry.path)
        if file_row is None:
            raise RuntimeError(f"snapshot entry is not present in candidate files: {entry.path}")
        language = str(file_row["language"])
        extractor = EXTRACTORS.get(language)
        if extractor is None:
            continue
        file_id = int(file_row["id"])
        source_sha = str(file_row["source_commit_sha"])
        reset_stats = getattr(extractor, "reset_stats", None)
        get_failures = getattr(extractor, "get_parse_failures", None)
        if reset_stats is not None:
            reset_stats()
        savepoint = f"symbol_file_{file_id}"
        conn.execute(f"SAVEPOINT {savepoint}")
        try:
            source = (snapshot.snapshot_root / Path(entry.path)).read_bytes()
            extracted = _extract(extractor, source, entry.path, language)
            failures = list(get_failures()) if get_failures is not None else []
            if failures:
                conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                for failure in failures:
                    code = str(failure.get("reason") or "parser_failure")
                    message = json.dumps(failure, sort_keys=True, ensure_ascii=True)
                    _record_diagnostic(
                        conn,
                        repo_id=repo_id,
                        file_id=file_id,
                        source_sha=source_sha,
                        code=code,
                        message=message,
                    )
                    diagnostic_count += 1
                continue
            ordinals: dict[tuple[object, ...], int] = {}
            for symbol in extracted:
                identity = (
                    symbol.kind,
                    symbol.name,
                    symbol.parent_symbol,
                    symbol.signature,
                )
                ordinal = ordinals.get(identity, 0)
                ordinals[identity] = ordinal + 1
                conn.execute(
                    """INSERT INTO symbols(
                           repo_id,file_id,name,kind,parent_symbol,start_line,end_line,
                           signature,language,stable_key
                       ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        repo_id,
                        file_id,
                        symbol.name,
                        symbol.kind,
                        symbol.parent_symbol,
                        symbol.start_line,
                        symbol.end_line,
                        symbol.signature,
                        symbol.language,
                        _stable_key(
                            kind=symbol.kind,
                            name=symbol.name,
                            parent_symbol=symbol.parent_symbol,
                            signature=symbol.signature,
                            duplicate_ordinal=ordinal,
                        ),
                    ),
                )
                symbols_count += 1
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        except Exception as exc:  # noqa: BLE001
            conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            _record_diagnostic(
                conn,
                repo_id=repo_id,
                file_id=file_id,
                source_sha=source_sha,
                code="parser_exception",
                message=str(exc),
            )
            diagnostic_count += 1
            continue
    return {"symbols": symbols_count, "diagnostics": diagnostic_count}
