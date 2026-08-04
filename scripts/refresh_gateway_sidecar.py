#!/usr/bin/env python3
"""Build the isolated XML Gateway automation sidecar from raw Git blobs.

The sidecar intentionally stores only source provenance and approved semantic
facts.  It never copies request bodies, credentials, session data, company
identifiers, or parser error payloads into SQLite.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sqlite3
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import yaml

try:
    from catalog.delta import (
        DeltaUnavailable,
        collect_repository_change_set,
        verify_clean_committed_checkout,
    )
    from catalog.repositories import load_workspace_manifest
    from catalog.source_snapshot import SourceSnapshotError, resolve_commit_sha
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from catalog.delta import (
        DeltaUnavailable,
        collect_repository_change_set,
        verify_clean_committed_checkout,
    )
    from catalog.repositories import load_workspace_manifest
    from catalog.source_snapshot import SourceSnapshotError, resolve_commit_sha


INCLUSION_POLICY_VERSION = "gateway-sidecar-v3"
DEFINITION_DIRECTORY = "testdefinitions"
EXCLUDED_DEFINITION_FILES = frozenset({"testdefinitions/baselines.csv"})
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,127}\Z")
_QUOTED_METADATA = re.compile(
    r'^"; (?:functional_team|publish_dashboard|contact_email|removal_list|description|baseline_type)\s*='
)
_PAYLOAD_OBJECT_OPERATIONS = frozenset({"create", "update"})
_TEXT_OBJECT_OPERATIONS = frozenset(
    {"delete", "inspect", "lookup", "query", "read", "readByName", "readByQuery"}
)


class GatewaySidecarError(RuntimeError):
    pass


@dataclass(frozen=True)
class XmlEvidence:
    parse_status: str
    diagnostic_code: str | None
    operation: str | None = None
    object_name: str | None = None


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS gateway_sidecar_builds (
    id INTEGER PRIMARY KEY, target_sha TEXT NOT NULL, requested_mode TEXT NOT NULL,
    effective_mode TEXT NOT NULL, inclusion_policy_version TEXT NOT NULL,
    mapping_sha1 TEXT NOT NULL, dependency_revisions_json TEXT NOT NULL,
    completed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS gateway_definitions (
    id INTEGER PRIMARY KEY, build_id INTEGER NOT NULL, source_path TEXT NOT NULL,
    source_blob_sha TEXT NOT NULL, row_number INTEGER NOT NULL,
    gateway_operation TEXT, gateway_object TEXT, xml_reference TEXT,
    reference_state TEXT NOT NULL CHECK(reference_state IN ('resolved','missing','literal','absent')),
    resolved_xml_path TEXT, UNIQUE(build_id,source_path,row_number),
    FOREIGN KEY(build_id) REFERENCES gateway_sidecar_builds(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS gateway_xml_artifacts (
    id INTEGER PRIMARY KEY, build_id INTEGER NOT NULL, source_path TEXT NOT NULL,
    source_blob_sha TEXT NOT NULL, parse_status TEXT NOT NULL CHECK(parse_status IN ('parsed','rejected')),
    diagnostic_code TEXT, UNIQUE(build_id,source_path),
    FOREIGN KEY(build_id) REFERENCES gateway_sidecar_builds(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS gateway_entity_links (
    id INTEGER PRIMARY KEY, definition_id INTEGER NOT NULL, entity_name TEXT NOT NULL,
    mapping_key TEXT NOT NULL, UNIQUE(definition_id,entity_name,mapping_key),
    FOREIGN KEY(definition_id) REFERENCES gateway_definitions(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS gateway_diagnostics (
    id INTEGER PRIMARY KEY, build_id INTEGER NOT NULL, source_path TEXT, row_number INTEGER,
    code TEXT NOT NULL, FOREIGN KEY(build_id) REFERENCES gateway_sidecar_builds(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_gateway_definitions_build ON gateway_definitions(build_id);
CREATE INDEX IF NOT EXISTS idx_gateway_links_definition ON gateway_entity_links(definition_id);
"""


def _git(root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, check=False
    )
    if result.returncode:
        raise GatewaySidecarError(result.stderr.decode("utf-8", "replace").strip())
    return result.stdout


def _tree(root: Path, target_sha: str) -> dict[str, str]:
    raw = _git(root, "ls-tree", "-r", "-z", target_sha)
    out: dict[str, str] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        meta, path = record.split(b"\t", 1)
        mode, kind, blob = meta.split()
        if mode in {b"100644", b"100755"} and kind == b"blob":
            out[path.decode("utf-8", "strict")] = blob.decode("ascii")
    return out


def _blob(root: Path, sha: str) -> bytes:
    return _git(root, "cat-file", "blob", sha)


def _is_definition_csv_path(path: str) -> bool:
    return (
        path not in EXCLUDED_DEFINITION_FILES
        and PurePosixPath(path).parent.as_posix() == DEFINITION_DIRECTORY
        and path.lower().endswith(".csv")
    )


def _definition_csv_paths(tree: dict[str, str]) -> list[str]:
    """Return only the proven, direct test-definition CSV surface.

    At the pinned Gateway corpus, definition CSVs are direct children of
    ``testdefinitions/``.  CSVs under ``testscripts/`` are test data, while
    ``testdefinitions/baselines.csv`` is a two-column environment dataset and
    may contain credentials/company identifiers, so neither surface is read as
    definition evidence.
    """

    return sorted(
        path
        for path in tree
        if _is_definition_csv_path(path)
    )


def _classify_request_reference(
    reference: str | None, paths: set[str]
) -> tuple[str | None, str, str | None, str | None]:
    """Classify positional field 0 without retaining unsafe literal values."""

    if reference is None or not reference.strip():
        return None, "absent", None, "xml_reference_absent"
    raw = reference.strip()
    if raw.lstrip().startswith("<") or raw.startswith(
        ("http:", "https:", "${", "{{")
    ):
        return None, "literal", None, "unsupported_request_reference"
    candidate = PurePosixPath(raw.replace("\\", "/"))
    if candidate.is_absolute() or ".." in candidate.parts or not candidate.parts:
        return None, "literal", None, "unsupported_request_reference"
    if candidate.suffix.casefold() != ".xml":
        return None, "literal", None, "unsupported_request_reference"
    normalized = candidate.as_posix()
    resolved = (PurePosixPath("testscripts") / candidate).as_posix()
    if resolved in paths:
        return normalized, "resolved", resolved, None
    return normalized, "missing", None, "xml_reference_missing"


def _is_metadata_line(line: str) -> bool:
    """Recognize only metadata forms proven across the pinned CSV corpus."""

    return line.startswith(";") or _QUOTED_METADATA.match(line) is not None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _function_fact(frame: dict[str, object]) -> tuple[str | None, str | None]:
    operations = list(frame["operations"])
    if len(operations) != 1:
        return None, None
    operation = str(operations[0])
    if not _IDENTIFIER.fullmatch(operation):
        return None, None
    children = list(frame["children"])
    if operation in _PAYLOAD_OBJECT_OPERATIONS:
        unique_children = {str(value) for value in children}
        if len(unique_children) == 1:
            object_name = next(iter(unique_children))
            if _IDENTIFIER.fullmatch(object_name):
                return operation, object_name
        return operation, None
    if operation in _TEXT_OBJECT_OPERATIONS:
        object_values = {str(value) for value in frame["object_values"]}
        if len(object_values) == 1:
            return operation, next(iter(object_values))
        return operation, None
    if operation == "get_list":
        attribute_values = {str(value) for value in frame["object_attributes"]}
        if len(attribute_values) == 1:
            return operation, next(iter(attribute_values))
    return operation, None


def _stream_xml_structure(
    data: bytes, *, wrapped_fragment: bool
) -> tuple[str, list[tuple[str | None, str | None]]]:
    payload = (
        b"<gateway-fragment>" + data + b"</gateway-fragment>"
        if wrapped_fragment
        else data
    )
    stack: list[str] = []
    root_name = ""
    fragment_children: list[str] = []
    frames: list[tuple[int, dict[str, object]]] = []
    facts: list[tuple[str | None, str | None]] = []
    for event, element in ET.iterparse(io.BytesIO(payload), events=("start", "end")):
        tag = _local_name(element.tag)
        if event == "start":
            stack.append(tag)
            if len(stack) == 1:
                root_name = tag
            if wrapped_fragment and len(stack) == 2:
                fragment_children.append(tag)
            function_path = tuple(stack)
            if function_path in {
                ("function",),
                ("request", "operation", "content", "function"),
                ("gateway-fragment", "function"),
            }:
                frames.append(
                    (
                        len(stack),
                        {
                            "operations": [],
                            "children": [],
                            "object_values": [],
                            "object_attributes": [],
                        },
                    )
                )
            if frames:
                depth, frame = frames[-1]
                if len(stack) == depth + 1:
                    frame["operations"].append(tag)
                    if tag == "get_list":
                        raw_object = element.attrib.get("object")
                        if raw_object and _IDENTIFIER.fullmatch(raw_object.strip()):
                            frame["object_attributes"].append(raw_object.strip())
                elif len(stack) == depth + 2:
                    frame["children"].append(tag)
            continue

        if frames:
            depth, frame = frames[-1]
            if len(stack) == depth + 2 and tag == "object":
                value = (element.text or "").strip()
                if _IDENTIFIER.fullmatch(value):
                    frame["object_values"].append(value)
            if len(stack) == depth and tag == "function":
                facts.append(_function_fact(frame))
                frames.pop()
        element.clear()
        stack.pop()
    if wrapped_fragment:
        if not fragment_children or any(tag != "function" for tag in fragment_children):
            return "unsupported", []
    elif root_name not in {"request", "function"}:
        return "unsupported", []
    return "supported", facts


def _parse_xml(data: bytes) -> XmlEvidence:
    """Securely classify one resolved request blob and retain tag facts only."""

    # The pinned request corpus is strict UTF-8 (with optional BOM), including
    # documents whose declaration names a legacy encoding.  Decode before the
    # declaration scan so UTF-16 or other byte encodings cannot conceal a DTD
    # or entity declaration from the safety gate.
    try:
        decoded = data.decode("utf-8-sig", errors="strict")
    except UnicodeError:
        return XmlEvidence("rejected", "invalid_xml")
    upper_text = decoded.upper()
    if "<!DOCTYPE" in upper_text or "<!ENTITY" in upper_text:
        return XmlEvidence("rejected", "unsafe_xml_declaration")
    try:
        shape, facts = _stream_xml_structure(data, wrapped_fragment=False)
    except (ET.ParseError, UnicodeError, ValueError):
        # A declaration is legal only for a complete document.  Never move it
        # under a synthetic wrapper to make an invalid declared fragment parse.
        if "<?XML" in upper_text:
            return XmlEvidence("rejected", "invalid_xml")
        try:
            shape, facts = _stream_xml_structure(data, wrapped_fragment=True)
        except (ET.ParseError, UnicodeError, ValueError):
            return XmlEvidence("rejected", "invalid_xml")
    if shape != "supported" or not facts:
        return XmlEvidence("parsed", "unsupported_request_shape")
    if any(operation is None for operation, _object in facts):
        return XmlEvidence("parsed", "unsupported_request_shape")
    pairs = {(operation, object_name) for operation, object_name in facts}
    if len(pairs) != 1:
        return XmlEvidence("parsed", "ambiguous_gateway_request")
    operation, object_name = next(iter(pairs))
    if object_name is None:
        return XmlEvidence(
            "parsed", "unsupported_gateway_object", str(operation), None
        )
    return XmlEvidence("parsed", None, str(operation), str(object_name))


def _mappings(path: Path) -> tuple[dict[tuple[str, str], tuple[str, str]], str]:
    data = path.read_bytes()
    document = yaml.safe_load(data)
    if not isinstance(document, dict) or document.get("version") != 1 or not isinstance(document.get("mappings"), list):
        raise GatewaySidecarError("gateway mapping file must have version: 1 and mappings list")
    mappings: dict[tuple[str, str], tuple[str, str]] = {}
    for item in document["mappings"]:
        if not isinstance(item, dict) or set(item) != {"operation", "object", "entity"}:
            raise GatewaySidecarError("each gateway mapping must contain exactly operation, object, entity")
        values = tuple(item[key].strip() for key in ("operation", "object", "entity"))
        if not all(values):
            raise GatewaySidecarError("gateway mappings cannot contain empty values")
        key = (values[0].casefold(), values[1].casefold())
        if key in mappings:
            raise GatewaySidecarError(f"duplicate gateway mapping: {key}")
        mappings[key] = (values[2], f"{values[0]}|{values[1]}")
    return mappings, hashlib.sha1(data).hexdigest()


def _is_full_object_id(value: str) -> bool:
    return len(value) in {40, 64} and all(
        character in "0123456789abcdef" for character in value
    )


def _insert_definition(
    connection: sqlite3.Connection,
    *,
    build_id: int,
    source_path: str,
    source_blob_sha: str,
    row_number: int,
    operation: str | None,
    object_name: str | None,
    reference: str | None,
    reference_state: str,
    resolved_xml_path: str | None,
    diagnostic_code: str | None,
) -> None:
    connection.execute(
        "INSERT INTO gateway_definitions(build_id,source_path,source_blob_sha,row_number,gateway_operation,gateway_object,xml_reference,reference_state,resolved_xml_path) VALUES(?,?,?,?,?,?,?,?,?)",
        (
            build_id,
            source_path,
            source_blob_sha,
            row_number,
            operation,
            object_name,
            reference,
            reference_state,
            resolved_xml_path,
        ),
    )
    if diagnostic_code:
        connection.execute(
            "INSERT INTO gateway_diagnostics(build_id,source_path,row_number,code) VALUES(?,?,?,?)",
            (build_id, source_path, row_number, diagnostic_code),
        )


def _parse_definition_csv(
    connection: sqlite3.Connection,
    *,
    build_id: int,
    repo_root: Path,
    source_path: str,
    source_blob_sha: str,
    tree_paths: set[str],
) -> None:
    data = _blob(repo_root, source_blob_sha)
    try:
        text = data.decode("utf-8-sig", errors="strict")
        physical_lines: list[str] = []
        source_line_numbers: list[int] = []
        for line_number, line in enumerate(text.splitlines(keepends=True), 1):
            # The corpus contains column-zero ';' metadata and 27 quoted
            # variants using the allowlisted keys above.  Fields 2-5 can hold
            # contact/company data, so metadata is classified but never stored.
            if _is_metadata_line(line) or not line.strip():
                continue
            physical_lines.append(line)
            source_line_numbers.append(line_number)
        reader = csv.reader(physical_lines, strict=True)
        parsed_rows: list[tuple[int, list[str]]] = []
        for row in reader:
            parsed_rows.append((source_line_numbers[reader.line_num - 1], row))
    except (csv.Error, UnicodeError):
        connection.execute(
            "INSERT INTO gateway_diagnostics(build_id,source_path,code) VALUES(?,?,?)",
            (build_id, source_path, "invalid_csv"),
        )
        return

    for row_number, row in parsed_rows:
        if len(row) != 6:
            connection.execute(
                "INSERT INTO gateway_diagnostics(build_id,source_path,row_number,code) VALUES(?,?,?,?)",
                (build_id, source_path, row_number, "unsupported_csv_shape"),
            )
            continue
        # Proven positional contract at b159255...:
        #   0 request/directive, 1 response/output, 2 description,
        #   3 expected result, 4 company/baseline, 5 boolean.
        # Only field 0 is request evidence.  Fields 1-5 are never persisted.
        reference, state, resolved, diagnostic = _classify_request_reference(
            row[0], tree_paths
        )
        _insert_definition(
            connection,
            build_id=build_id,
            source_path=source_path,
            source_blob_sha=source_blob_sha,
            row_number=row_number,
            operation=None,
            object_name=None,
            reference=reference,
            reference_state=state,
            resolved_xml_path=resolved,
            diagnostic_code=diagnostic,
        )


def _build_xml_artifacts(
    connection: sqlite3.Connection,
    *,
    build_id: int,
    base_build_id: int | None,
    repo_root: Path,
    tree: dict[str, str],
) -> tuple[int, dict[str, tuple[str | None, str | None]]]:
    xml_paths = {
        str(row[0])
        for row in connection.execute(
            "SELECT DISTINCT resolved_xml_path FROM gateway_definitions WHERE build_id=? AND reference_state='resolved'",
            (build_id,),
        )
    }
    previous: dict[str, sqlite3.Row] = {}
    previous_semantics: dict[str, tuple[str | None, str | None]] = {}
    if base_build_id is not None:
        previous = {
            str(row["source_path"]): row
            for row in connection.execute(
                "SELECT source_path,source_blob_sha,parse_status,diagnostic_code FROM gateway_xml_artifacts WHERE build_id=?",
                (base_build_id,),
            )
        }
        for row in connection.execute(
            "SELECT resolved_xml_path,gateway_operation,gateway_object FROM gateway_definitions WHERE build_id=? AND reference_state='resolved' GROUP BY resolved_xml_path,gateway_operation,gateway_object ORDER BY resolved_xml_path",
            (base_build_id,),
        ):
            path = str(row["resolved_xml_path"])
            semantic = (row["gateway_operation"], row["gateway_object"])
            if path not in previous_semantics:
                previous_semantics[path] = semantic
            elif previous_semantics[path] != semantic:
                previous_semantics[path] = (None, None)
    semantics: dict[str, tuple[str | None, str | None]] = {}
    for xml_path in sorted(xml_paths):
        prior = previous.get(xml_path)
        if prior is not None and str(prior["source_blob_sha"]) == tree[xml_path]:
            status = str(prior["parse_status"])
            diagnostic = prior["diagnostic_code"]
            operation, object_name = previous_semantics.get(xml_path, (None, None))
        else:
            evidence = _parse_xml(_blob(repo_root, tree[xml_path]))
            status = evidence.parse_status
            diagnostic = evidence.diagnostic_code
            operation = evidence.operation
            object_name = evidence.object_name
        semantics[xml_path] = (operation, object_name)
        connection.execute(
            "INSERT INTO gateway_xml_artifacts(build_id,source_path,source_blob_sha,parse_status,diagnostic_code) VALUES(?,?,?,?,?)",
            (build_id, xml_path, tree[xml_path], status, diagnostic),
        )
        if diagnostic:
            connection.execute(
                "INSERT INTO gateway_diagnostics(build_id,source_path,code) VALUES(?,?,?)",
                (build_id, xml_path, diagnostic),
            )
    return len(xml_paths), semantics


def _link_definition_semantics(
    connection: sqlite3.Connection,
    *,
    build_id: int,
    semantics: dict[str, tuple[str | None, str | None]],
    mappings: dict[tuple[str, str], tuple[str, str]],
) -> None:
    for xml_path, (operation, object_name) in semantics.items():
        connection.execute(
            "UPDATE gateway_definitions SET gateway_operation=?,gateway_object=? WHERE build_id=? AND resolved_xml_path=?",
            (operation, object_name, build_id, xml_path),
        )
    for row in connection.execute(
        "SELECT id,source_path,row_number,gateway_operation,gateway_object FROM gateway_definitions WHERE build_id=? AND gateway_operation IS NOT NULL AND gateway_object IS NOT NULL ORDER BY id",
        (build_id,),
    ):
        operation = str(row["gateway_operation"])
        object_name = str(row["gateway_object"])
        mapping = mappings.get((operation.casefold(), object_name.casefold()))
        if mapping:
            connection.execute(
                "INSERT INTO gateway_entity_links(definition_id,entity_name,mapping_key) VALUES(?,?,?)",
                (int(row["id"]), mapping[0], mapping[1]),
            )
        else:
            connection.execute(
                "INSERT INTO gateway_diagnostics(build_id,source_path,row_number,code) VALUES(?,?,?,?)",
                (
                    build_id,
                    str(row["source_path"]),
                    int(row["row_number"]),
                    "unmapped_gateway_operation",
                ),
            )


def _existing_build_result(
    connection: sqlite3.Connection, build_id: int, target_sha: str
) -> dict[str, int | str]:
    definitions = int(
        connection.execute(
            "SELECT COUNT(*) FROM gateway_definitions WHERE build_id=?", (build_id,)
        ).fetchone()[0]
    )
    xml = int(
        connection.execute(
            "SELECT COUNT(*) FROM gateway_xml_artifacts WHERE build_id=?", (build_id,)
        ).fetchone()[0]
    )
    return {
        "build_id": build_id,
        "target_sha": target_sha,
        "definitions": definitions,
        "xml": xml,
        "effective_mode": "noop",
    }


def build(
    *,
    repo_root: Path,
    target_sha: str,
    sidecar_db: Path,
    mapping_file: Path,
    ia_main_sha: str,
    requested_mode: str = "full",
    tracked_branch: str = "main",
) -> dict[str, int | str]:
    """Replace the active sidecar build atomically from verified raw blobs."""

    if requested_mode not in {"full", "delta"}:
        raise GatewaySidecarError(f"unsupported refresh mode: {requested_mode}")
    if not _is_full_object_id(ia_main_sha):
        raise GatewaySidecarError("ia-main revision must be a full Git object ID")
    try:
        resolved_target_sha = resolve_commit_sha(repo_root, target_sha)
    except SourceSnapshotError as exc:
        raise GatewaySidecarError(str(exc)) from exc
    tree = _tree(repo_root, resolved_target_sha)
    tree_paths = set(tree)
    mappings, mapping_sha1 = _mappings(mapping_file)
    dependency_revisions_json = json.dumps(
        {"ia-gwdata-gl": resolved_target_sha, "ia-main": ia_main_sha},
        sort_keys=True,
        separators=(",", ":"),
    )
    connection = sqlite3.connect(sidecar_db)
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript(SCHEMA)
        connection.execute("BEGIN IMMEDIATE")
        effective_mode = "full"
        base = connection.execute(
            "SELECT * FROM gateway_sidecar_builds ORDER BY id DESC LIMIT 1"
        ).fetchone()
        base_build_id: int | None = None
        changes = ()
        delta_fallback = False
        if requested_mode == "delta":
            compatible_base = (
                base is not None
                and str(base["inclusion_policy_version"])
                == INCLUSION_POLICY_VERSION
                and _is_full_object_id(str(base["target_sha"]))
            )
            if compatible_base:
                base_build_id = int(base["id"])
                if (
                    str(base["target_sha"]) == resolved_target_sha
                    and str(base["mapping_sha1"]) == mapping_sha1
                    and str(base["dependency_revisions_json"])
                    == dependency_revisions_json
                ):
                    result = _existing_build_result(
                        connection, base_build_id, resolved_target_sha
                    )
                    connection.rollback()
                    return result
                try:
                    change_set = collect_repository_change_set(
                        repo_key="ia-gwdata-gl",
                        root=repo_root,
                        tracked_branch=tracked_branch,
                        base_commit_sha=str(base["target_sha"]),
                        requested_mode="auto",
                        target_commit_sha=resolved_target_sha,
                    )
                    if change_set.effective_mode in {"delta", "noop"}:
                        effective_mode = "delta"
                        changes = change_set.changed_paths
                    else:
                        delta_fallback = True
                        base_build_id = None
                except DeltaUnavailable:
                    delta_fallback = True
                    base_build_id = None
            else:
                delta_fallback = True
        build_id = int(
            connection.execute(
                "INSERT INTO gateway_sidecar_builds(target_sha,requested_mode,effective_mode,inclusion_policy_version,mapping_sha1,dependency_revisions_json) VALUES(?,?,?,?,?,?)",
                (
                    resolved_target_sha,
                    requested_mode,
                    effective_mode,
                    INCLUSION_POLICY_VERSION,
                    mapping_sha1,
                    dependency_revisions_json,
                ),
            ).lastrowid
        )
        csv_paths = _definition_csv_paths(tree)
        if delta_fallback:
            connection.execute(
                "INSERT INTO gateway_diagnostics(build_id,code) VALUES(?,?)",
                (build_id, "delta_full_fallback"),
            )
        for excluded_path in sorted(EXCLUDED_DEFINITION_FILES.intersection(tree)):
            connection.execute(
                "INSERT INTO gateway_diagnostics(build_id,source_path,code) VALUES(?,?,?)",
                (build_id, excluded_path, "excluded_non_definition_csv"),
            )

        csv_to_parse = set(csv_paths)
        if effective_mode == "delta" and base_build_id is not None:
            changed_old_csv = {
                str(change.old_path)
                for change in changes
                if change.old_path and _is_definition_csv_path(change.old_path)
            }
            changed_new_csv = {
                str(change.new_path)
                for change in changes
                if change.new_path and _is_definition_csv_path(change.new_path)
            }
            csv_to_parse = changed_new_csv
            unchanged_csv = set(csv_paths) - changed_old_csv - changed_new_csv
            for row in connection.execute(
                "SELECT source_path,source_blob_sha,row_number,xml_reference,reference_state FROM gateway_definitions WHERE build_id=? ORDER BY source_path,row_number",
                (base_build_id,),
            ):
                source_path = str(row["source_path"])
                if source_path not in unchanged_csv:
                    continue
                if tree.get(source_path) != str(row["source_blob_sha"]):
                    csv_to_parse.add(source_path)
                    unchanged_csv.discard(source_path)
                    continue
                if str(row["reference_state"]) == "literal":
                    reference, state, resolved, diagnostic = (
                        None,
                        "literal",
                        None,
                        "unsupported_request_reference",
                    )
                else:
                    reference, state, resolved, diagnostic = (
                        _classify_request_reference(row["xml_reference"], tree_paths)
                    )
                _insert_definition(
                    connection,
                    build_id=build_id,
                    source_path=source_path,
                    source_blob_sha=str(row["source_blob_sha"]),
                    row_number=int(row["row_number"]),
                    operation=None,
                    object_name=None,
                    reference=reference,
                    reference_state=state,
                    resolved_xml_path=resolved,
                    diagnostic_code=diagnostic,
                )
            for row in connection.execute(
                "SELECT source_path,row_number,code FROM gateway_diagnostics WHERE build_id=? AND code IN ('invalid_csv','unsupported_csv_shape') ORDER BY source_path,row_number,code",
                (base_build_id,),
            ):
                source_path = str(row["source_path"])
                if source_path in unchanged_csv:
                    connection.execute(
                        "INSERT INTO gateway_diagnostics(build_id,source_path,row_number,code) VALUES(?,?,?,?)",
                        (build_id, source_path, row["row_number"], str(row["code"])),
                    )

        for csv_path in sorted(csv_to_parse):
            _parse_definition_csv(
                connection,
                build_id=build_id,
                repo_root=repo_root,
                source_path=csv_path,
                source_blob_sha=tree[csv_path],
                tree_paths=tree_paths,
            )
        definitions = int(
            connection.execute(
                "SELECT COUNT(*) FROM gateway_definitions WHERE build_id=?",
                (build_id,),
            ).fetchone()[0]
        )
        xml_count, semantics = _build_xml_artifacts(
            connection,
            build_id=build_id,
            base_build_id=base_build_id if effective_mode == "delta" else None,
            repo_root=repo_root,
            tree=tree,
        )
        _link_definition_semantics(
            connection,
            build_id=build_id,
            semantics=semantics,
            mappings=mappings,
        )
        connection.commit()
        return {
            "build_id": build_id,
            "target_sha": resolved_target_sha,
            "definitions": definitions,
            "xml": xml_count,
            "effective_mode": effective_mode,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--target-sha", required=True)
    parser.add_argument("--db", type=Path, default=Path("catalog/sidecars/ia-gwdata-gl.db"))
    parser.add_argument("--mapping-file", type=Path, default=Path("config/gateway_entity_mappings.yaml"))
    parser.add_argument("--manifest", type=Path, default=Path("config/workspace_repos.yaml"))
    parser.add_argument("--mode", choices=("full", "delta"), default="full")
    args = parser.parse_args()
    manifest = load_workspace_manifest(args.manifest)
    entries = {
        str(entry["repo_key"]): entry for entry in manifest["repositories"]
    }
    gateway = entries.get("ia-gwdata-gl")
    if gateway is None or gateway.get("storage") != "sidecar":
        raise GatewaySidecarError(
            "manifest must define ia-gwdata-gl with sidecar storage"
        )
    repo_root = args.repo_root.expanduser().resolve()
    manifest_root = Path(str(gateway["local_root"])).expanduser().resolve()
    if repo_root != manifest_root:
        raise GatewaySidecarError(
            f"--repo-root does not match manifest ia-gwdata-gl.local_root: {manifest_root}"
        )
    if "ia-main" not in (gateway.get("depends_on") or []):
        raise GatewaySidecarError("ia-gwdata-gl manifest entry must depend on ia-main")
    ia_main = entries.get("ia-main")
    if ia_main is None:
        raise GatewaySidecarError("manifest does not define ia-main")
    ia_main_root = Path(str(ia_main["local_root"])).expanduser().resolve()
    try:
        ia_main_sha = verify_clean_committed_checkout(
            ia_main_root, str(ia_main["tracked_branch"])
        )
    except DeltaUnavailable as exc:
        raise GatewaySidecarError(str(exc)) from exc
    args.db.parent.mkdir(parents=True, exist_ok=True)
    print(
        build(
            repo_root=repo_root,
            target_sha=args.target_sha,
            sidecar_db=args.db,
            mapping_file=args.mapping_file,
            ia_main_sha=ia_main_sha,
            requested_mode=args.mode,
            tracked_branch=str(gateway["tracked_branch"]),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
