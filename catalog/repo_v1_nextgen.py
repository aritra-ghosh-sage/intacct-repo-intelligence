"""Immutable NextGen UI family, artifact, and diagnostic facts."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import PurePosixPath

from tqdm import tqdm

from catalog.source_snapshot import SourceSnapshot, SourceSnapshotError
from parser.ui.nextgen import (
    NextGenDiagnostic,
    NextGenFamilyFact,
    NextGenSource,
    extract_nextgen_families,
)

EXTRACTOR = "repo_v1_nextgen"
EXTRACTOR_VERSION = "1"
NEXTGEN_DIAGNOSTIC_CODES = frozenset(
    {
        "nextgen.yaml.invalid",
        "nextgen.yaml.document_not_mapping",
        "nextgen.family.invalid_object",
        "nextgen.family.unresolved",
    }
)
_UIMETA = re.compile(r"\.uimeta[^.]*\.ya?ml$", re.IGNORECASE)
_VIEWMETA = re.compile(r"\.viewmeta[^.]*\.ya?ml$", re.IGNORECASE)
_VIEW = re.compile(r"\.view\.ya?ml$", re.IGNORECASE)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class NextGenValidationError(RuntimeError):
    """A candidate violates the immutable NextGen fact contract."""


@dataclass(frozen=True)
class NextGenStats:
    family_count: int
    artifact_count: int
    diagnostic_count: int


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _artifact_kind(source_path: str) -> str | None:
    filename = PurePosixPath(source_path).name
    if _UIMETA.search(filename):
        return "uimeta"
    if _VIEWMETA.search(filename):
        return "viewmeta"
    if _VIEW.search(filename):
        return "view"
    return None


def _fact_evidence(
    *,
    fact_type: str,
    source_path: str,
    source_commit_sha: str,
    source_hash: str,
    start_line: int,
    end_line: int,
    parser_fact: dict[str, object],
) -> str:
    return _canonical(
        {
            "fact_type": fact_type,
            "parser_fact": parser_fact,
            "source_commit_sha": source_commit_sha,
            "source_hash": source_hash,
            "source_lines": {"start": start_line, "end": end_line},
            "source_path": source_path,
        }
    )


def _family_parser_fact(fact: NextGenFamilyFact) -> dict[str, object]:
    return {
        "family_key": fact.family_key,
        "source_file": fact.source_file,
        "start_line": fact.start_line,
        "end_line": fact.end_line,
        "evidence": fact.evidence,
    }


def _diagnostic_parser_fact(diagnostic: NextGenDiagnostic) -> dict[str, object]:
    return {
        "code": diagnostic.code,
        "message": diagnostic.message,
        "source_file": diagnostic.source_file,
        "start_line": diagnostic.start_line,
        "end_line": diagnostic.end_line,
        "evidence": diagnostic.evidence,
        "severity": diagnostic.severity,
    }


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise NextGenValidationError(message)


def _parse_canonical(value: str, message: str) -> object:
    try:
        parsed = json.loads(value)
        _require(_canonical(parsed) == value, message)
        return parsed
    except (TypeError, ValueError, NextGenValidationError) as exc:
        if isinstance(exc, NextGenValidationError):
            raise
        raise NextGenValidationError(message) from exc


def _read_snapshot_sources(
    snapshot: SourceSnapshot,
    conn: sqlite3.Connection,
    *,
    repo_id: int,
    show_progress: bool,
) -> tuple[
    list[NextGenSource],
    dict[str, tuple[int, str, str]],
]:
    file_rows = {
        str(row["path"]): (int(row["id"]), str(row["source_commit_sha"]))
        for row in conn.execute(
            "SELECT id,path,source_commit_sha FROM files WHERE repo_id=?", (repo_id,)
        )
    }
    sources: list[NextGenSource] = []
    metadata: dict[str, tuple[int, str, str]] = {}
    entries = tqdm(
        (entry for entry in snapshot.entries if _artifact_kind(entry.path)),
        desc="Reading NextGen YAML",
        unit="file",
        disable=not show_progress,
    )
    for entry in entries:
        source_path = entry.path
        file_id, file_commit_sha = file_rows.get(source_path, (None, None))
        if file_id is None:
            raise SourceSnapshotError(
                f"NextGen snapshot path is missing from files: {source_path}"
            )
        if file_commit_sha != snapshot.target_sha:
            raise SourceSnapshotError(
                f"NextGen file commit does not match snapshot: {source_path}"
            )
        raw = snapshot.snapshot_root.joinpath(
            *PurePosixPath(source_path).parts
        ).read_bytes()
        source_hash = hashlib.sha256(raw).hexdigest()
        metadata[source_path] = (file_id, source_hash, snapshot.target_sha)
        sources.append(
            NextGenSource(
                source_file=source_path,
                text=raw.decode("utf-8", errors="replace"),
            )
        )
    return sources, metadata


def extract_snapshot_nextgen(
    conn: sqlite3.Connection,
    *,
    repo_id: int,
    snapshot: SourceSnapshot,
    show_progress: bool = False,
) -> NextGenStats:
    """Extract NextGen facts exclusively from the retained source snapshot."""

    sources, metadata = _read_snapshot_sources(
        snapshot, conn, repo_id=repo_id, show_progress=show_progress
    )
    result = extract_nextgen_families(sources)

    family_ids: dict[str, int] = {}
    for fact in result.families:
        file_id, source_hash, source_commit_sha = metadata[fact.source_file]
        family_id = int(
            conn.execute(
                """INSERT INTO nextgen_families(
                       repo_id,family_key,source_file_id,source_path,
                       source_commit_sha,source_hash,start_line,end_line,evidence,
                       extractor,extractor_version
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    repo_id,
                    fact.family_key,
                    file_id,
                    fact.source_file,
                    source_commit_sha,
                    source_hash,
                    fact.start_line,
                    fact.end_line,
                    _fact_evidence(
                        fact_type="nextgen.family",
                        source_path=fact.source_file,
                        source_commit_sha=source_commit_sha,
                        source_hash=source_hash,
                        start_line=fact.start_line,
                        end_line=fact.end_line,
                        parser_fact=_family_parser_fact(fact),
                    ),
                    EXTRACTOR,
                    EXTRACTOR_VERSION,
                ),
            ).lastrowid
        )
        family_ids[fact.family_key] = family_id

    for fact in result.artifacts:
        family_id = family_ids.get(fact.family_key)
        if family_id is None:
            raise SourceSnapshotError(
                f"NextGen artifact has no valid family: {fact.family_key}"
            )
        file_id, source_hash, source_commit_sha = metadata[fact.source_file]
        artifact_key = _canonical(
            {
                "artifact_kind": fact.artifact_kind,
                "family_key": fact.family_key,
                "source_path": fact.source_file,
            }
        )
        parser_fact = {
            "family_key": fact.family_key,
            "source_file": fact.source_file,
            "artifact_kind": fact.artifact_kind,
            "start_line": fact.start_line,
            "end_line": fact.end_line,
            "evidence": fact.evidence,
        }
        conn.execute(
            """INSERT INTO nextgen_artifacts(
                   repo_id,family_id,artifact_key,artifact_kind,file_id,source_path,
                   source_commit_sha,source_hash,start_line,end_line,evidence,
                   extractor,extractor_version
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                repo_id,
                family_id,
                artifact_key,
                fact.artifact_kind,
                file_id,
                fact.source_file,
                source_commit_sha,
                source_hash,
                fact.start_line,
                fact.end_line,
                _fact_evidence(
                    fact_type="nextgen.artifact",
                    source_path=fact.source_file,
                    source_commit_sha=source_commit_sha,
                    source_hash=source_hash,
                    start_line=fact.start_line,
                    end_line=fact.end_line,
                    parser_fact=parser_fact,
                ),
                EXTRACTOR,
                EXTRACTOR_VERSION,
            ),
        )

    diagnostics = [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.code in NEXTGEN_DIAGNOSTIC_CODES
    ]
    grouped: dict[tuple[str, str, int, int], list[NextGenDiagnostic]] = defaultdict(
        list
    )
    for diagnostic in diagnostics:
        grouped[
            (
                diagnostic.source_file,
                diagnostic.code,
                diagnostic.start_line,
                diagnostic.end_line,
            )
        ].append(diagnostic)
    for group in sorted(grouped):
        source_path, code, start_line, end_line = group
        file_id, source_hash, source_commit_sha = metadata[source_path]
        ordered = sorted(
            grouped[group],
            key=lambda value: (
                value.message,
                value.severity,
                value.evidence or "",
            ),
        )
        for ordinal, diagnostic in enumerate(ordered):
            diagnostic_key = _canonical(
                {
                    "code": code,
                    "end_line": end_line,
                    "ordinal": ordinal,
                    "source_path": source_path,
                    "start_line": start_line,
                }
            )
            conn.execute(
                """INSERT INTO nextgen_diagnostics(
                       repo_id,file_id,diagnostic_key,severity,code,message,
                       source_commit_sha,source_hash,start_line,end_line,evidence,
                       extractor,extractor_version
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    repo_id,
                    file_id,
                    diagnostic_key,
                    diagnostic.severity,
                    diagnostic.code,
                    diagnostic.message,
                    source_commit_sha,
                    source_hash,
                    diagnostic.start_line,
                    diagnostic.end_line,
                    _fact_evidence(
                        fact_type="nextgen.diagnostic",
                        source_path=source_path,
                        source_commit_sha=source_commit_sha,
                        source_hash=source_hash,
                        start_line=start_line,
                        end_line=end_line,
                        parser_fact=_diagnostic_parser_fact(diagnostic),
                    ),
                    EXTRACTOR,
                    EXTRACTOR_VERSION,
                ),
            )

    return NextGenStats(
        family_count=len(result.families),
        artifact_count=len(result.artifacts),
        diagnostic_count=len(diagnostics),
    )


def _validate_common_source(
    row: sqlite3.Row,
    *,
    file_column: str,
    source_path_column: str | None,
    file_rows: dict[int, tuple[str, str]],
    repo_id: int,
    target_commit_sha: str,
) -> tuple[str, str]:
    path, file_commit_sha = file_rows.get(int(row[file_column]), (None, None))
    _require(
        path is not None
        and int(row["repo_id"]) == repo_id
        and (
            source_path_column is None or path == str(row[source_path_column])
        )
        and file_commit_sha == str(row["source_commit_sha"]) == target_commit_sha,
        "NextGen source ownership or commit provenance is invalid",
    )
    source_hash = str(row["source_hash"])
    _require(_SHA256.fullmatch(source_hash) is not None, "NextGen source hash is invalid")
    _require(
        str(row["extractor"]) == EXTRACTOR
        and str(row["extractor_version"]) == EXTRACTOR_VERSION,
        "NextGen extractor provenance is invalid",
    )
    _require(
        int(row["start_line"]) >= 1
        and int(row["end_line"]) >= int(row["start_line"]),
        "NextGen line range is invalid",
    )
    return str(path), source_hash


def _validate_evidence(
    row: sqlite3.Row,
    *,
    fact_type: str,
    source_path: str,
    source_hash: str,
    target_commit_sha: str,
    parser_fact: dict[str, object],
) -> None:
    expected = _fact_evidence(
        fact_type=fact_type,
        source_path=source_path,
        source_commit_sha=target_commit_sha,
        source_hash=source_hash,
        start_line=int(row["start_line"]),
        end_line=int(row["end_line"]),
        parser_fact=parser_fact,
    )
    _require(str(row["evidence"]) == expected, "NextGen evidence is invalid")
    _parse_canonical(str(row["evidence"]), "NextGen evidence is not canonical JSON")


def validate_nextgen_candidate(
    conn: sqlite3.Connection, *, repo_id: int, target_commit_sha: str
) -> None:
    """Fail closed on NextGen ownership, provenance, facts, and stable keys."""

    repo = conn.execute(
        "SELECT id,target_commit_sha FROM repos WHERE id=?", (repo_id,)
    ).fetchone()
    _require(
        repo is not None and str(repo["target_commit_sha"]) == target_commit_sha,
        "NextGen repository provenance is invalid",
    )
    file_rows = {
        int(row["id"]): (str(row["path"]), str(row["source_commit_sha"]))
        for row in conn.execute(
            "SELECT id,path,source_commit_sha FROM files WHERE repo_id=?", (repo_id,)
        )
    }

    families = conn.execute(
        "SELECT * FROM nextgen_families WHERE repo_id=? ORDER BY id", (repo_id,)
    ).fetchall()
    family_by_id: dict[int, sqlite3.Row] = {}
    family_by_key: dict[str, sqlite3.Row] = {}
    file_hashes: dict[int, str] = {}
    for row in families:
        family_id = int(row["id"])
        source_path, source_hash = _validate_common_source(
            row,
            file_column="source_file_id",
            source_path_column="source_path",
            file_rows=file_rows,
            repo_id=repo_id,
            target_commit_sha=target_commit_sha,
        )
        family_key = str(row["family_key"])
        _require(
            family_key == family_key.strip() and "/" in family_key,
            "NextGen family key is invalid",
        )
        _require(
            _artifact_kind(source_path) is not None,
            "NextGen family provenance path is invalid",
        )
        parser_fact = {
            "family_key": family_key,
            "source_file": source_path,
            "start_line": int(row["start_line"]),
            "end_line": int(row["end_line"]),
            "evidence": json.loads(str(row["evidence"]))["parser_fact"]["evidence"],
        }
        _validate_evidence(
            row,
            fact_type="nextgen.family",
            source_path=source_path,
            source_hash=source_hash,
            target_commit_sha=target_commit_sha,
            parser_fact=parser_fact,
        )
        _require(family_id not in family_by_id, "Duplicate NextGen family ID")
        _require(family_key not in family_by_key, "Duplicate NextGen family key")
        family_by_id[family_id] = row
        family_by_key[family_key] = row
        file_hashes.setdefault(int(row["source_file_id"]), source_hash)
        _require(
            file_hashes[int(row["source_file_id"])] == source_hash,
            "NextGen source hashes disagree for one file",
        )

    artifacts = conn.execute(
        "SELECT * FROM nextgen_artifacts WHERE repo_id=? ORDER BY id", (repo_id,)
    ).fetchall()
    artifact_keys: set[tuple[int, str]] = set()
    for row in artifacts:
        source_path, source_hash = _validate_common_source(
            row,
            file_column="file_id",
            source_path_column="source_path",
            file_rows=file_rows,
            repo_id=repo_id,
            target_commit_sha=target_commit_sha,
        )
        family_id = int(row["family_id"])
        family = family_by_id.get(family_id)
        _require(family is not None, "NextGen artifact has an orphaned family")
        family_key = str(family["family_key"])
        _require(
            str(row["artifact_kind"]) in {"uimeta", "viewmeta", "view"}
            and str(row["artifact_kind"]) == _artifact_kind(source_path),
            "NextGen artifact kind is invalid",
        )
        expected_key = _canonical(
            {
                "artifact_kind": str(row["artifact_kind"]),
                "family_key": family_key,
                "source_path": source_path,
            }
        )
        _require(str(row["artifact_key"]) == expected_key, "NextGen artifact key is invalid")
        key = (family_id, expected_key)
        _require(key not in artifact_keys, "Duplicate NextGen artifact key")
        artifact_keys.add(key)
        parser_fact = {
            "family_key": family_key,
            "source_file": source_path,
            "artifact_kind": str(row["artifact_kind"]),
            "start_line": int(row["start_line"]),
            "end_line": int(row["end_line"]),
            "evidence": json.loads(str(row["evidence"]))["parser_fact"]["evidence"],
        }
        _validate_evidence(
            row,
            fact_type="nextgen.artifact",
            source_path=source_path,
            source_hash=source_hash,
            target_commit_sha=target_commit_sha,
            parser_fact=parser_fact,
        )
        file_hashes.setdefault(int(row["file_id"]), source_hash)
        _require(
            file_hashes[int(row["file_id"])] == source_hash,
            "NextGen source hashes disagree for one file",
        )

    diagnostics = conn.execute(
        "SELECT * FROM nextgen_diagnostics WHERE repo_id=? ORDER BY id", (repo_id,)
    ).fetchall()
    diagnostic_groups: dict[tuple[str, str, int, int], list[sqlite3.Row]] = defaultdict(
        list
    )
    for row in diagnostics:
        source_path, source_hash = _validate_common_source(
            row,
            file_column="file_id",
            source_path_column=None,
            file_rows=file_rows,
            repo_id=repo_id,
            target_commit_sha=target_commit_sha,
        )
        code = str(row["code"])
        severity = str(row["severity"])
        _require(code in NEXTGEN_DIAGNOSTIC_CODES, "NextGen diagnostic code is invalid")
        _require(severity in {"warning", "error"}, "NextGen diagnostic severity is invalid")
        evidence = _parse_canonical(str(row["evidence"]), "NextGen diagnostic evidence is invalid")
        _require(isinstance(evidence, dict), "NextGen diagnostic evidence is not an object")
        parser_fact = evidence.get("parser_fact")
        _require(isinstance(parser_fact, dict), "NextGen diagnostic parser evidence is invalid")
        expected_parser_fact = {
            "code": code,
            "message": str(row["message"]),
            "source_file": source_path,
            "start_line": int(row["start_line"]),
            "end_line": int(row["end_line"]),
            "evidence": parser_fact.get("evidence"),
            "severity": severity,
        }
        _require(parser_fact == expected_parser_fact, "NextGen diagnostic evidence fields are invalid")
        _validate_evidence(
            row,
            fact_type="nextgen.diagnostic",
            source_path=source_path,
            source_hash=source_hash,
            target_commit_sha=target_commit_sha,
            parser_fact=expected_parser_fact,
        )
        group = (source_path, code, int(row["start_line"]), int(row["end_line"]))
        diagnostic_groups[group].append(row)
        file_hashes.setdefault(int(row["file_id"]), source_hash)
        _require(
            file_hashes[int(row["file_id"])] == source_hash,
            "NextGen source hashes disagree for one file",
        )

    for group, rows in diagnostic_groups.items():
        source_path, code, start_line, end_line = group
        ordered = sorted(
            rows,
            key=lambda row: (
                str(row["message"]),
                str(row["severity"]),
                json.loads(str(row["evidence"]))["parser_fact"].get("evidence") or "",
            ),
        )
        for ordinal, row in enumerate(ordered):
            expected_key = _canonical(
                {
                    "code": code,
                    "end_line": end_line,
                    "ordinal": ordinal,
                    "source_path": source_path,
                    "start_line": start_line,
                }
            )
            _require(
                str(row["diagnostic_key"]) == expected_key,
                "NextGen diagnostic key is invalid",
            )
