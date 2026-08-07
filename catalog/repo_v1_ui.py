"""Immutable ActionUI XML facts for the repo-v1 full snapshot build."""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath

from catalog.source_snapshot import SourceSnapshot, SourceSnapshotError
from parser.actionui.model import Diagnostic, EventFact, FieldFact, IncludeFact
from parser.actionui.xml_extractor import extract_actionui_xml_facts

EXTRACTOR = "repo_v1_ui"
EXTRACTOR_VERSION = "1"
UI_DIAGNOSTIC_CODES = frozenset(
    {
        "actionui.xml.parse_error",
        "actionui.xml.field_identity_missing",
        "actionui.xml.xinclude_href_missing",
        "actionui.include.unresolved",
        "actionui.include.invalid",
    }
)
_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")


@dataclass(frozen=True)
class UiStats:
    surface_count: int
    artifact_count: int
    field_count: int
    event_count: int
    include_count: int
    diagnostic_count: int


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def normalize_include_path(
    raw_include_path: str,
    including_source_path: str,
    committed_paths: Iterable[str],
) -> tuple[str, str | None]:
    """Normalize one static XInclude path against the repository root.

    The result is ``(resolution_status, normalized_path)``.  Invalid paths
    have no normalized value; valid paths absent from the committed inventory
    remain visible as unresolved values.
    """

    raw = raw_include_path.strip().replace("\\", "/")
    if not raw or "\x00" in raw or raw.startswith("/") or _DRIVE_PREFIX.match(raw):
        return "invalid", None
    parent = PurePosixPath(including_source_path).parent.as_posix()
    normalized = posixpath.normpath(posixpath.join(parent, raw))
    if normalized in {"", "."} or normalized == ".." or normalized.startswith("../"):
        return "invalid", None
    committed = set(committed_paths)
    return (
        ("resolved", normalized)
        if normalized in committed
        else ("unresolved", normalized)
    )


def _fact_evidence(
    *,
    source_path: str,
    source_line: int,
    start_line: int,
    end_line: int,
    identity: dict[str, object],
    parser_fact: dict[str, object],
) -> str:
    return _canonical(
        {
            "identity": identity,
            "parser_fact": parser_fact,
            "source_line": source_line,
            "source_lines": {"start": start_line, "end": end_line},
            "source_path": source_path,
        }
    )


def _source_diagnostic_evidence(
    *,
    code: str,
    message: str,
    severity: str,
    source_commit_sha: str,
    source_path: str,
) -> str:
    return _canonical(
        {
            "code": code,
            "message": message,
            "severity": severity,
            "source_commit_sha": source_commit_sha,
            "source_path": source_path,
            "source_line": None,
            "evidence": None,
        }
    )


def _fact_ordinals(
    facts: Iterable[object], identity: object
) -> list[tuple[object, int]]:
    ordered = sorted(
        facts,
        key=lambda fact: (
            tuple(identity(fact)),
            str(fact.evidence),
        ),
    )
    counts: dict[tuple[object, ...], int] = {}
    result: list[tuple[object, int]] = []
    for fact in ordered:
        base = tuple(identity(fact))
        ordinal = counts.get(base, 0)
        counts[base] = ordinal + 1
        result.append((fact, ordinal))
    return result


def _read_snapshot_source(snapshot: SourceSnapshot, source_path: str) -> bytes:
    return snapshot.snapshot_root.joinpath(
        *PurePosixPath(source_path).parts
    ).read_bytes()


def extract_snapshot_ui(
    conn: sqlite3.Connection,
    *,
    repo_id: int,
    snapshot: SourceSnapshot,
    show_progress: bool = False,
) -> UiStats:
    """Extract only committed ``*_form.xml`` ActionUI facts."""

    del show_progress  # The UI slice is intentionally small and snapshot-local.
    snapshot_entries = {entry.path: entry for entry in snapshot.entries}
    form_paths = sorted(path for path in snapshot_entries if path.endswith("_form.xml"))
    file_rows = {
        str(row["path"]): int(row["id"])
        for row in conn.execute("SELECT id,path FROM files WHERE repo_id=?", (repo_id,))
    }
    committed_paths = set(snapshot_entries)
    counts = [0, 0, 0, 0, 0, 0]

    for source_path in form_paths:
        if source_path not in snapshot_entries:
            raise SourceSnapshotError(
                f"ActionUI form is missing from the source snapshot: {source_path}"
            )
        file_id = file_rows.get(source_path)
        if file_id is None:
            raise SourceSnapshotError(
                f"ActionUI form is missing from the committed inventory: {source_path}"
            )
        raw_source = _read_snapshot_source(snapshot, source_path)
        decoded_source = raw_source.decode("utf-8", errors="replace")
        source_hash = hashlib.sha256(raw_source).hexdigest()
        end_line = max(1, decoded_source.count("\n") + 1)
        result = extract_actionui_xml_facts(raw_source, source_path)
        parser_diagnostics = list(result.diagnostics)
        if not raw_source.strip():
            parser_diagnostics = [
                Diagnostic(
                    code="actionui.xml.parse_error",
                    message="ActionUI XML source is empty or whitespace-only.",
                    source_file=source_path,
                    start_line=1,
                    end_line=1,
                    severity="error",
                )
            ]

        surface_id: int | None = None
        artifact_id: int | None = None
        if result.artifacts:
            surface_id = int(
                conn.execute(
                    """INSERT INTO ui_surfaces(
                           repo_id,surface_key,surface_kind,display_name,
                           source_file_id,source_path,source_commit_sha,extractor,
                           extractor_version,source_hash
                       ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        repo_id,
                        f"actionui:{source_path}",
                        "actionui_form",
                        PurePosixPath(source_path).stem,
                        file_id,
                        source_path,
                        snapshot.target_sha,
                        EXTRACTOR,
                        EXTRACTOR_VERSION,
                        source_hash,
                    ),
                ).lastrowid
            )
            artifact_evidence = _canonical(
                {
                    "source_commit_sha": snapshot.target_sha,
                    "source_hash": source_hash,
                    "source_path": source_path,
                    "start_line": 1,
                    "end_line": end_line,
                }
            )
            artifact_id = int(
                conn.execute(
                    """INSERT INTO ui_artifacts(
                           repo_id,surface_id,artifact_key,artifact_kind,file_id,
                           source_path,source_commit_sha,start_line,end_line,evidence
                       ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        repo_id,
                        surface_id,
                        f"form:{source_path}",
                        "actionui_form",
                        file_id,
                        source_path,
                        snapshot.target_sha,
                        1,
                        end_line,
                        artifact_evidence,
                    ),
                ).lastrowid
            )
            counts[0] += 1
            counts[1] += 1

            for fact, ordinal in _fact_ordinals(
                result.fields,
                lambda item: (item.start_line, item.field_name, item.field_path or ""),
            ):
                assert isinstance(fact, FieldFact)
                field_path = fact.field_path
                field_key = (
                    f"field:{source_path}:{fact.start_line}:{fact.field_name}:"
                    f"{field_path or ''}:{ordinal}"
                )
                evidence = _fact_evidence(
                    source_path=source_path,
                    source_line=fact.start_line,
                    start_line=fact.start_line,
                    end_line=fact.end_line,
                    identity={
                        "field_key": field_key,
                        "field_name": fact.field_name,
                        "field_path": field_path,
                    },
                    parser_fact={
                        "source_file": fact.source_file,
                        "field_name": fact.field_name,
                        "field_path": field_path,
                        "start_line": fact.start_line,
                        "end_line": fact.end_line,
                        "evidence": fact.evidence,
                    },
                )
                conn.execute(
                    """INSERT INTO ui_fields(
                           repo_id,artifact_id,field_key,field_name,field_path,
                           source_line,evidence
                       ) VALUES(?,?,?,?,?,?,?)""",
                    (
                        repo_id,
                        artifact_id,
                        field_key,
                        fact.field_name,
                        field_path,
                        fact.start_line,
                        evidence,
                    ),
                )
                counts[2] += 1

            for fact, ordinal in _fact_ordinals(
                result.events, lambda item: (item.start_line, item.event_name)
            ):
                assert isinstance(fact, EventFact)
                event_key = (
                    f"event:{source_path}:{fact.start_line}:{fact.event_name}:{ordinal}"
                )
                evidence = _fact_evidence(
                    source_path=source_path,
                    source_line=fact.start_line,
                    start_line=fact.start_line,
                    end_line=fact.end_line,
                    identity={"event_key": event_key, "event_name": fact.event_name},
                    parser_fact={
                        "source_file": fact.source_file,
                        "event_name": fact.event_name,
                        "start_line": fact.start_line,
                        "end_line": fact.end_line,
                        "evidence": fact.evidence,
                    },
                )
                conn.execute(
                    """INSERT INTO ui_events(
                           repo_id,artifact_id,event_key,event_name,source_line,evidence
                       ) VALUES(?,?,?,?,?,?)""",
                    (
                        repo_id,
                        artifact_id,
                        event_key,
                        fact.event_name,
                        fact.start_line,
                        evidence,
                    ),
                )
                counts[3] += 1

            include_rows: list[tuple[IncludeFact, int, str, str | None]] = []
            for fact, ordinal in _fact_ordinals(
                result.includes, lambda item: (item.start_line, item.included_path)
            ):
                assert isinstance(fact, IncludeFact)
                status, resolved = normalize_include_path(
                    fact.included_path, source_path, committed_paths
                )
                include_rows.append((fact, ordinal, status, resolved))
                include_key = f"include:{source_path}:{fact.start_line}:{fact.included_path}:{ordinal}"
                evidence = _fact_evidence(
                    source_path=source_path,
                    source_line=fact.start_line,
                    start_line=fact.start_line,
                    end_line=fact.end_line,
                    identity={
                        "include_key": include_key,
                        "raw_include_path": fact.included_path,
                        "resolved_path": resolved,
                        "resolution_status": status,
                    },
                    parser_fact={
                        "source_file": fact.source_file,
                        "included_path": fact.included_path,
                        "start_line": fact.start_line,
                        "end_line": fact.end_line,
                        "evidence": fact.evidence,
                    },
                )
                conn.execute(
                    """INSERT INTO ui_includes(
                           repo_id,artifact_id,include_key,raw_include_path,resolved_path,
                           resolution_status,source_line,evidence
                       ) VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        repo_id,
                        artifact_id,
                        include_key,
                        fact.included_path,
                        resolved,
                        status,
                        fact.start_line,
                        evidence,
                    ),
                )
                counts[4] += 1
                if status in {"unresolved", "invalid"}:
                    parser_diagnostics.append(
                        Diagnostic(
                            code=f"actionui.include.{status}",
                            message=(
                                f"XInclude target is {status}: {fact.included_path}"
                                + (f" -> {resolved}" if resolved else "")
                            ),
                            source_file=source_path,
                            start_line=fact.start_line,
                            end_line=fact.end_line,
                            severity="warning",
                        )
                    )

        diagnostics = sorted(
            parser_diagnostics,
            key=lambda diagnostic: (
                diagnostic.code,
                diagnostic.start_line,
                diagnostic.end_line,
                diagnostic.message,
            ),
        )
        diagnostic_ordinals: dict[tuple[str, int, int], int] = {}
        for diagnostic in diagnostics:
            base = (diagnostic.code, diagnostic.start_line, diagnostic.end_line)
            ordinal = diagnostic_ordinals.get(base, 0)
            diagnostic_ordinals[base] = ordinal + 1
            severity = (
                "error" if diagnostic.code == "actionui.xml.parse_error" else "warning"
            )
            diagnostic_key = (
                f"diagnostic:{source_path}:{diagnostic.code}:{diagnostic.start_line}:"
                f"{diagnostic.end_line}:{ordinal}"
            )
            evidence = _source_diagnostic_evidence(
                code=diagnostic.code,
                message=diagnostic.message,
                severity=severity,
                source_commit_sha=snapshot.target_sha,
                source_path=source_path,
            )
            conn.execute(
                """INSERT INTO ui_diagnostics(
                       repo_id,file_id,surface_id,diagnostic_key,severity,code,message,
                       source_commit_sha,evidence,extractor
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    repo_id,
                    file_id,
                    surface_id,
                    diagnostic_key,
                    severity,
                    diagnostic.code,
                    diagnostic.message,
                    snapshot.target_sha,
                    evidence,
                    EXTRACTOR,
                ),
            )
            counts[5] += 1

    return UiStats(*counts)


def _invalid_text(value: object) -> bool:
    return value is None or not str(value).strip()


def _parse_json(value: str) -> dict[str, object]:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise TypeError("evidence is not an object")
    return parsed


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def validate_ui_candidate(
    conn: sqlite3.Connection, *, repo_id: int, target_commit_sha: str
) -> None:
    """Fail closed on UI ownership, provenance, and deterministic-key errors."""

    repo = conn.execute(
        "SELECT id,target_commit_sha FROM repos WHERE id=?", (repo_id,)
    ).fetchone()
    _require(
        repo is not None and str(repo["target_commit_sha"]) == target_commit_sha,
        "UI repository provenance is invalid",
    )
    file_rows = {
        int(row["id"]): (str(row["path"]), str(row["source_commit_sha"]))
        for row in conn.execute(
            "SELECT id,path,source_commit_sha FROM files WHERE repo_id=?", (repo_id,)
        )
    }
    path_set = {path for path, _sha in file_rows.values()}
    surfaces = conn.execute(
        "SELECT * FROM ui_surfaces WHERE repo_id=? ORDER BY id", (repo_id,)
    ).fetchall()
    for row in surfaces:
        path, file_sha = file_rows.get(int(row["source_file_id"]), (None, None))
        _require(
            path == row["source_path"] and file_sha == row["source_commit_sha"],
            "UI surface ownership or provenance is invalid",
        )
        _require(
            row["surface_key"] == f"actionui:{row['source_path']}"
            and row["surface_kind"] == "actionui_form",
            "UI surface identity is invalid",
        )
        _require(
            row["display_name"] == PurePosixPath(str(row["source_path"])).stem,
            "UI surface display name is invalid",
        )
        _require(
            row["extractor"] == EXTRACTOR
            and row["extractor_version"] == EXTRACTOR_VERSION
            and not _invalid_text(row["source_hash"]),
            "UI surface extractor provenance is invalid",
        )

    artifacts = conn.execute(
        "SELECT * FROM ui_artifacts WHERE repo_id=? ORDER BY id", (repo_id,)
    ).fetchall()
    artifact_ids = {int(row["id"]) for row in artifacts}
    artifact_paths: dict[int, str] = {}
    for row in artifacts:
        surface = conn.execute(
            "SELECT * FROM ui_surfaces WHERE id=?", (row["surface_id"],)
        ).fetchone()
        path, file_sha = file_rows.get(int(row["file_id"]), (None, None))
        _require(
            surface is not None and int(surface["repo_id"]) == repo_id,
            "UI artifact has an invalid parent surface",
        )
        _require(
            path == row["source_path"] == surface["source_path"]
            and file_sha == row["source_commit_sha"] == surface["source_commit_sha"],
            "UI artifact ownership or provenance is invalid",
        )
        _require(
            row["artifact_key"] == f"form:{row['source_path']}"
            and row["artifact_kind"] == "actionui_form",
            "UI artifact identity is invalid",
        )
        _require(
            int(row["start_line"]) == 1 and int(row["end_line"]) >= 1,
            "UI artifact line range is invalid",
        )
        evidence = _parse_json(str(row["evidence"]))
        _require(
            evidence
            == {
                "end_line": int(row["end_line"]),
                "source_commit_sha": str(row["source_commit_sha"]),
                "source_hash": str(surface["source_hash"]),
                "source_path": str(row["source_path"]),
                "start_line": 1,
            },
            "UI artifact evidence is invalid",
        )
        artifact_paths[int(row["id"])] = str(row["source_path"])

    fields = conn.execute(
        "SELECT * FROM ui_fields WHERE repo_id=? ORDER BY artifact_id,id", (repo_id,)
    ).fetchall()
    _validate_child_keys(fields, "field", artifact_paths)
    for row in fields:
        path = artifact_paths.get(int(row["artifact_id"]))
        _require(
            path is not None and int(row["artifact_id"]) in artifact_ids,
            "UI field has an invalid parent",
        )
        _require(
            not _invalid_text(row["field_name"]) and int(row["source_line"]) >= 1,
            "UI field fact is invalid",
        )
        evidence = _parse_json(str(row["evidence"]))
        _require(
            evidence.get("source_path") == path
            and evidence.get("source_line") == int(row["source_line"]),
            "UI field evidence provenance is invalid",
        )
        _require(
            evidence.get("identity", {}).get("field_key") == row["field_key"]
            and evidence.get("identity", {}).get("field_name") == row["field_name"]
            and evidence.get("identity", {}).get("field_path") == row["field_path"],
            "UI field evidence identity is invalid",
        )

    events = conn.execute(
        "SELECT * FROM ui_events WHERE repo_id=? ORDER BY artifact_id,id", (repo_id,)
    ).fetchall()
    _validate_child_keys(events, "event", artifact_paths)
    for row in events:
        path = artifact_paths.get(int(row["artifact_id"]))
        _require(
            path is not None
            and not _invalid_text(row["event_name"])
            and int(row["source_line"]) >= 1,
            "UI event fact is invalid",
        )
        evidence = _parse_json(str(row["evidence"]))
        _require(
            evidence.get("source_path") == path
            and evidence.get("source_line") == int(row["source_line"]),
            "UI event evidence provenance is invalid",
        )
        _require(
            evidence.get("identity", {}).get("event_key") == row["event_key"]
            and evidence.get("identity", {}).get("event_name") == row["event_name"],
            "UI event evidence identity is invalid",
        )

    includes = conn.execute(
        "SELECT * FROM ui_includes WHERE repo_id=? ORDER BY artifact_id,id", (repo_id,)
    ).fetchall()
    _validate_child_keys(includes, "include", artifact_paths)
    for row in includes:
        path = artifact_paths.get(int(row["artifact_id"]))
        _require(
            path is not None
            and not _invalid_text(row["raw_include_path"])
            and int(row["source_line"]) >= 1,
            "UI include fact is invalid",
        )
        status, normalized = normalize_include_path(
            str(row["raw_include_path"]), path, path_set
        )
        _require(
            status == row["resolution_status"] and normalized == row["resolved_path"],
            "UI include resolution is invalid",
        )
        evidence = _parse_json(str(row["evidence"]))
        _require(
            evidence.get("source_path") == path
            and evidence.get("source_line") == int(row["source_line"]),
            "UI include evidence provenance is invalid",
        )
        identity = evidence.get("identity", {})
        _require(
            identity.get("include_key") == row["include_key"]
            and identity.get("raw_include_path") == row["raw_include_path"],
            "UI include evidence identity is invalid",
        )

    diagnostics = conn.execute(
        "SELECT * FROM ui_diagnostics WHERE repo_id=? ORDER BY id", (repo_id,)
    ).fetchall()
    _require(
        conn.execute(
            """SELECT COUNT(*)
               FROM ui_diagnostics d
               JOIN ui_artifacts a ON a.repo_id=d.repo_id AND a.file_id=d.file_id
               WHERE d.repo_id=? AND d.code='actionui.xml.parse_error'""",
            (repo_id,),
        ).fetchone()[0]
        == 0,
        "parse-failed UI files contain facts",
    )
    _validate_diagnostic_keys(
        diagnostics,
        file_rows,
        {int(row["id"]): int(row["source_file_id"]) for row in surfaces},
        target_commit_sha,
    )


def _validate_child_keys(
    rows: Iterable[sqlite3.Row], kind: str, paths: dict[int, str]
) -> None:
    grouped: dict[tuple[int, tuple[object, ...]], list[sqlite3.Row]] = {}
    for row in rows:
        artifact_id = int(row["artifact_id"])
        path = paths.get(artifact_id)
        _require(path is not None, f"UI {kind} has an orphaned artifact")
        if kind == "field":
            base = (
                int(row["source_line"]),
                str(row["field_name"]),
                str(row["field_path"] or ""),
            )
            prefix = f"field:{path}:{base[0]}:{base[1]}:{base[2]}:"
            key = str(row["field_key"])
        elif kind == "event":
            base = (int(row["source_line"]), str(row["event_name"]))
            prefix = f"event:{path}:{base[0]}:{base[1]}:"
            key = str(row["event_key"])
        else:
            base = (int(row["source_line"]), str(row["raw_include_path"]))
            prefix = f"include:{path}:{base[0]}:{base[1]}:"
            key = str(row["include_key"])
        grouped.setdefault((artifact_id, base), []).append(row)
        _require(
            key.startswith(prefix) and key[len(prefix) :].isdigit(),
            f"UI {kind} key is invalid",
        )
    for group in grouped.values():
        key_name = {
            "field": "field_key",
            "event": "event_key",
            "include": "include_key",
        }[kind]
        ordered = sorted(
            group,
            key=lambda row: int(str(row[key_name]).rsplit(":", 1)[-1]),
        )
        for ordinal, row in enumerate(ordered):
            _require(
                int(str(row[key_name]).rsplit(":", 1)[-1]) == ordinal,
                f"UI {kind} collision ordinals are not deterministic",
            )


def _validate_diagnostic_keys(
    rows: Iterable[sqlite3.Row],
    file_rows: dict[int, tuple[str, str]],
    surface_file_ids: dict[int, int],
    target_commit_sha: str,
) -> None:
    grouped: dict[tuple[str, str, int, int], list[sqlite3.Row]] = {}
    for row in rows:
        path, file_sha = file_rows.get(int(row["file_id"]), (None, None))
        _require(
            path is not None
            and file_sha == row["source_commit_sha"] == target_commit_sha,
            "UI diagnostic ownership or provenance is invalid",
        )
        code = str(row["code"])
        _require(
            code in UI_DIAGNOSTIC_CODES and str(row["extractor"]) == EXTRACTOR,
            "UI diagnostic code or extractor is invalid",
        )
        _require(
            not _invalid_text(row["message"])
            and str(row["severity"]) in {"warning", "error"},
            "UI diagnostic fact is invalid",
        )
        if code == "actionui.xml.parse_error":
            _require(
                row["surface_id"] is None and row["severity"] == "error",
                "parse diagnostic attachment is invalid",
            )
        else:
            _require(
                row["surface_id"] is not None
                and int(row["surface_id"]) in surface_file_ids
                and surface_file_ids[int(row["surface_id"])] == int(row["file_id"])
                and row["severity"] == "warning",
                "UI diagnostic attachment is invalid",
            )
        evidence = _parse_json(str(row["evidence"]))
        _require(
            evidence
            == {
                "code": code,
                "message": str(row["message"]),
                "severity": str(row["severity"]),
                "source_commit_sha": target_commit_sha,
                "source_path": path,
                "source_line": None,
                "evidence": None,
            },
            "UI diagnostic evidence is invalid",
        )
        match = re.fullmatch(
            rf"diagnostic:{re.escape(path)}:{re.escape(code)}:(\d+):(\d+):(\d+)",
            str(row["diagnostic_key"]),
        )
        _require(match is not None, "UI diagnostic key is invalid")
        start_line, end_line, _ordinal = (int(value) for value in match.groups())
        grouped.setdefault((path, code, start_line, end_line), []).append(row)
    for group in grouped.values():
        ordered = sorted(group, key=lambda row: str(row["evidence"]))
        for ordinal, row in enumerate(ordered):
            _require(
                str(row["diagnostic_key"]).rsplit(":", 1)[-1] == str(ordinal),
                "UI diagnostic collision ordinals are not deterministic",
            )
