#!/usr/bin/env python3
"""Extract evidence-backed REST use-case coverage from Gherkin feature files.

Only Gherkin, its same-stem properties metadata, and object-mapping.json are
read.  In particular this module never opens fixture files or Java sources.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import click

try:
    from catalog.db import get_connection
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from catalog.db import get_connection

try:
    from gherkin.parser import Parser
except ModuleNotFoundError:  # helpful error for operators rather than fallback parsing
    Parser = None


DEFAULT_DB = "catalog/catalog.db"
VERSION_TAG = re.compile(r"^@version:([^\s]+)$", re.I)
JIRA_TAG = re.compile(r"^@([A-Z][A-Z0-9]+-\d+)$")
STATUS = re.compile(
    r"\b(?:status(?:\s+code)?|response\s+code)(?:\s+is)?\s*[\"']?(\d{3})", re.I
)
QUOTED = re.compile(r'"([^"]*)"')
GENERIC_REQUEST = re.compile(
    r'"(?P<method>GET|POST|PUT|PATCH|DELETE)"\s+to\s+(?:(?:child\s+)?"(?P<object>[^"]+)")',
    re.I,
)
SIMPLE_REQUEST = re.compile(
    r"\bI\s+(?P<verb>read|create|update|delete|patch|post|put)\s+(?:object)?\s*\"(?P<object>[^\"]+)\"",
    re.I,
)
KEY = re.compile(r'\bwith\s+key\s+"([^"]*)"', re.I)
PARENT = re.compile(r'\bfor\s+parent\s+"([^"]+)"\s+with\s+key\s+"([^"]*)"', re.I)
STEP_VERSION = re.compile(r'\bfor\s+version\s+"([^"]+)"', re.I)
ACTION = re.compile(r'\b(?:action|workflow)\s+"([^"]+)"', re.I)


@dataclass(frozen=True)
class Diagnostic:
    kind: str
    message: str
    line: int | None = None


@dataclass(frozen=True)
class RequestEvidence:
    ordinal: int
    line: int
    method: str
    object_token: str | None
    raw_path: str | None
    normalized_path: str | None
    version: str | None
    expected_status: int | None
    operation_kind: str
    explicit_version: bool = False


@dataclass(frozen=True)
class CaseEvidence:
    feature_name: str
    scenario_name: str
    case_name: str
    example_row: int | None
    feature_line: int
    scenario_line: int
    tags: tuple[str, ...]
    jira_refs: tuple[str, ...]
    eligibility: str
    version_conflicted: bool
    versions: tuple[str, ...]
    version_sources: tuple[tuple[str, str, int], ...]
    requests: tuple[RequestEvidence, ...]
    diagnostics: tuple[Diagnostic, ...]


def _line(node: dict[str, Any]) -> int:
    return int(node.get("location", {}).get("line", 0))


def _tags(nodes: Iterable[dict[str, Any]]) -> tuple[str, ...]:
    return tuple(tag["name"] for tag in nodes if tag.get("name"))


def _read_properties_metadata_with_lines(
    path: Path,
) -> tuple[dict[str, str], dict[str, int], list[Diagnostic]]:
    """Read only explicitly allowlisted properties, without retaining secrets."""
    metadata: dict[str, str] = {}
    lines: dict[str, int] = {}
    diagnostics: list[Diagnostic] = []
    if not path.exists():
        return (
            metadata,
            lines,
            [
                Diagnostic(
                    "missing_properties", f"Missing paired properties file: {path.name}"
                )
            ],
        )
    # Iterate rather than reading the whole file: properties commonly contain credentials.
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for line_no, raw in enumerate(stream, 1):
            stripped = raw.strip()
            if not stripped or stripped.startswith(("#", "!")) or "=" not in stripped:
                continue
            key, value = (part.strip() for part in stripped.split("=", 1))
            if key in {"version", "testObject"}:
                metadata[key] = value
                lines[key] = line_no
    return metadata, lines, diagnostics


def read_properties_metadata(path: Path) -> tuple[dict[str, str], list[Diagnostic]]:
    metadata, _lines, diagnostics = _read_properties_metadata_with_lines(path)
    return metadata, diagnostics


def load_object_mapping(path: Path) -> tuple[dict[str, str], list[Diagnostic]]:
    """Flatten mapping sections and reject aliases that disagree across sections."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    targets: dict[str, set[str]] = {}
    for section in payload.values():
        if not isinstance(section, dict):
            continue
        for alias, target in section.items():
            if isinstance(alias, str) and isinstance(target, str):
                targets.setdefault(alias, set()).add(target)
    ambiguous = {alias for alias, values in targets.items() if len(values) != 1}
    mapping = {
        alias: next(iter(values))
        for alias, values in targets.items()
        if alias not in ambiguous
    }
    diagnostics = [
        Diagnostic(
            "duplicate_object_alias",
            f"Alias '{alias}' maps to multiple routes: {sorted(targets[alias])}",
        )
        for alias in sorted(ambiguous)
    ]
    return mapping, diagnostics


def _substitute(value: str, values: dict[str, str]) -> str:
    return re.sub(
        r"<([^>]+)>", lambda match: values.get(match.group(1), match.group(0)), value
    )


def _eligible(tags: tuple[str, ...]) -> str:
    lowered = {tag.lower() for tag in tags}
    if "@knownissue" in lowered:
        return "known_issue"
    if "@ci_only" in lowered:
        return "ci_only"
    return "active"


def _parent_alias_for_lookup(token: str) -> str:
    """Match the runtime's narrow unwrapping of a {{parent-alias}} token."""
    normalized = token.strip()
    if normalized.startswith("{{") and normalized.endswith("}}"):
        return normalized[2:-2].strip()
    return normalized


def _path_for_request(
    text: str, object_token: str | None, mapping: dict[str, str], fallback: str | None
) -> tuple[str | None, str | None, str, list[Diagnostic]]:
    diagnostics: list[Diagnostic] = []
    if not object_token:
        object_token = fallback
    if not object_token:
        return (
            None,
            None,
            "unknown",
            [Diagnostic("unresolved_object", "Request does not identify an object")],
        )
    if object_token in {"query", "model"}:
        return (
            object_token,
            None,
            "unknown",
            [
                Diagnostic(
                    "generic_request",
                    f"Generic {object_token} request is not entity coverage",
                )
            ],
        )
    target = object_token if object_token.startswith("/") else mapping.get(object_token)
    if not target:
        return (
            object_token,
            None,
            "unknown",
            [
                Diagnostic(
                    "unresolved_object", f"No unambiguous mapping for '{object_token}'"
                )
            ],
        )
    if target.startswith("/"):
        path = target
    else:
        path = "/objects/" + target.strip("/")
    parent = PARENT.search(text)
    if parent:
        parent_token, parent_key = parent.groups()
        parent_target = mapping.get(_parent_alias_for_lookup(parent_token))
        if not parent_target:
            return (
                object_token,
                None,
                "child",
                [
                    Diagnostic(
                        "unresolved_parent",
                        f"No unambiguous mapping for parent '{parent_token}'",
                    )
                ],
            )
        child = target.strip("/")
        path = f"/objects/{parent_target.strip('/')}/{('{key}' if parent_key else '')}/{child}".replace(
            "//", "/"
        )
        operation = "child"
    else:
        operation = (
            "workflow"
            if "workflow" in target or ACTION.search(text)
            else "item"
            if (match := KEY.search(text)) and match.group(1)
            else "collection"
        )
    if operation == "item":
        path += "/{key}"
    action = ACTION.search(text)
    if action:
        path += "/" + action.group(1).strip("/")
        operation = "workflow"
    return object_token, canonicalize_path(path), operation, diagnostics


def canonicalize_path(path: str) -> str:
    path = "/" + path.strip().strip("/")
    path = re.sub(r"^/services/(?:v[^/]+|s\d+)(?=/)", "", path, flags=re.I)
    path = re.sub(r"//+", "/", path)
    return path.rstrip("/") or "/"


def _request_from_step(
    text: str,
    line: int,
    ordinal: int,
    version: str | None,
    mapping: dict[str, str],
    fallback: str | None,
) -> tuple[RequestEvidence | None, list[Diagnostic]]:
    generic = GENERIC_REQUEST.search(text)
    simple = SIMPLE_REQUEST.search(text)
    if generic:
        method, object_token = generic.group("method").upper(), generic.group("object")
    elif simple:
        verb, object_token = simple.group("verb").lower(), simple.group("object")
        method = {
            "read": "GET",
            "create": "POST",
            "update": "PATCH",
            "delete": "DELETE",
            "patch": "PATCH",
            "post": "POST",
            "put": "PUT",
        }[verb]
    else:
        return None, []
    explicit = STEP_VERSION.search(text)
    effective_version = explicit.group(1) if explicit else version
    object_token, path, operation, diagnostics = _path_for_request(
        text, object_token, mapping, fallback
    )
    return RequestEvidence(
        ordinal,
        line,
        method,
        object_token,
        path,
        path,
        effective_version,
        None,
        operation,
        bool(explicit),
    ), diagnostics


def _example_rows(scenario: dict[str, Any]) -> list[tuple[int | None, dict[str, str]]]:
    examples = scenario.get("examples", [])
    if not examples:
        return [(None, {})]
    rows: list[tuple[int | None, dict[str, str]]] = []
    next_row = 1
    for example in examples:
        header = [
            cell["value"] for cell in example.get("tableHeader", {}).get("cells", [])
        ]
        for row in example.get("tableBody", []):
            values = {
                key: cell.get("value", "")
                for key, cell in zip(header, row.get("cells", []))
            }
            rows.append((next_row, values))
            next_row += 1
    return rows or [(None, {})]


def parse_feature(path: Path, mapping: dict[str, str]) -> list[CaseEvidence]:
    if Parser is None:
        raise RuntimeError("gherkin-official is required; run uv sync")
    document = Parser().parse(path.read_text(encoding="utf-8"))
    feature = document.get("feature")
    if not feature:
        raise ValueError("Feature file has no parseable Feature declaration")
    feature_tags = _tags(feature.get("tags", []))
    metadata, property_lines, property_diagnostics = (
        _read_properties_metadata_with_lines(path.with_suffix(".properties"))
    )
    feature_version_tags = tuple(
        (match.group(1), _line(tag))
        for tag in feature.get("tags", [])
        if (match := VERSION_TAG.match(tag.get("name", "")))
    )
    feature_versions = tuple(version for version, _line_no in feature_version_tags)
    property_version = metadata.get("version")
    common_diagnostics = list(property_diagnostics)
    version_conflicted = False
    if (
        property_version
        and feature_versions
        and property_version not in feature_versions
    ):
        version_conflicted = True
        common_diagnostics.append(
            Diagnostic(
                "version_conflict",
                f"Feature versions {feature_versions} conflict with properties version '{property_version}'",
            )
        )
        declared_versions = ()
        version_sources = tuple(
            [(version, "feature_tag", line) for version, line in feature_version_tags]
            + [(property_version, "properties", property_lines.get("version", 0))]
        )
    elif property_version:
        declared_versions = (property_version,)
        version_sources = (
            (property_version, "properties", property_lines.get("version", 0)),
        )
    else:
        declared_versions = feature_versions
        version_sources = tuple(
            (version, "feature_tag", line) for version, line in feature_version_tags
        )
    out: list[CaseEvidence] = []
    for child in feature.get("children", []):
        scenario = child.get("scenario")
        if not scenario:
            continue
        scenario_tags = feature_tags + _tags(scenario.get("tags", []))
        jira = tuple(tag[1:] for tag in scenario_tags if JIRA_TAG.match(tag))
        for example_row, values in _example_rows(scenario):
            name = _substitute(scenario.get("name", ""), values)
            case_name = values.get("testCaseID") or name
            requests: list[RequestEvidence] = []
            diagnostics = list(common_diagnostics)
            state_version = (
                declared_versions[0] if len(declared_versions) == 1 else None
            )
            for step in scenario.get("steps", []):
                text, line = _substitute(step.get("text", ""), values), _line(step)
                request, request_diagnostics = _request_from_step(
                    text,
                    line,
                    len(requests) + 1,
                    state_version,
                    mapping,
                    metadata.get("testObject"),
                )
                if request:
                    requests.append(request)
                    diagnostics.extend(
                        Diagnostic(d.kind, d.message, line) for d in request_diagnostics
                    )
                    if STEP_VERSION.search(text):
                        state_version = request.version
                elif override := STEP_VERSION.search(text):
                    state_version = override.group(1)
            # Build status associations in a second small pass (only following assertions count).
            expected: dict[int, int] = {}
            request_positions = [
                (_line(step), i)
                for i, step in enumerate(scenario.get("steps", []))
                if _request_from_step(
                    _substitute(step.get("text", ""), values),
                    _line(step),
                    0,
                    None,
                    mapping,
                    metadata.get("testObject"),
                )[0]
            ]
            for index, step in enumerate(scenario.get("steps", [])):
                status = STATUS.search(_substitute(step.get("text", ""), values))
                if status:
                    prior = [
                        request_index
                        for request_index, original_index in enumerate(
                            [p[1] for p in request_positions]
                        )
                        if original_index < index
                    ]
                    if prior:
                        expected[prior[-1]] = int(status.group(1))
            requests = [
                RequestEvidence(
                    r.ordinal,
                    r.line,
                    r.method,
                    r.object_token,
                    r.raw_path,
                    r.normalized_path,
                    r.version,
                    expected.get(i),
                    r.operation_kind,
                    r.explicit_version,
                )
                for i, r in enumerate(requests)
            ]
            out.append(
                CaseEvidence(
                    feature.get("name", ""),
                    name,
                    case_name,
                    example_row,
                    _line(feature),
                    _line(scenario),
                    scenario_tags,
                    jira,
                    _eligible(scenario_tags),
                    version_conflicted,
                    declared_versions,
                    version_sources,
                    tuple(requests),
                    tuple(diagnostics),
                )
            )
    return out


def _file_id(
    conn: sqlite3.Connection,
    repo_id: int,
    root: Path,
    path: Path,
    *,
    read_contents: bool = True,
) -> int:
    relative = str(path.relative_to(root))
    sha1 = hashlib.sha1(path.read_bytes()).hexdigest() if read_contents else None
    language = {
        ".feature": "gherkin",
        ".properties": "properties",
        ".json": "json",
    }.get(path.suffix, "unknown")
    conn.execute(
        "INSERT INTO files(repo_id,path,language,size_bytes,sha1) VALUES(?,?,?,?,?) ON CONFLICT(repo_id,path) DO UPDATE SET language=excluded.language,size_bytes=excluded.size_bytes,sha1=excluded.sha1",
        (repo_id, relative, language, path.stat().st_size, sha1),
    )
    return int(
        conn.execute(
            "SELECT id FROM files WHERE repo_id=? AND path=?", (repo_id, relative)
        ).fetchone()[0]
    )


def _endpoint_matches(
    conn: sqlite3.Connection,
    production_repo_id: int,
    method: str,
    path: str,
    test_versions: tuple[str, ...],
) -> list[sqlite3.Row]:
    rows = conn.execute(
        "SELECT id,entity_id,source_version,path FROM rest_endpoints WHERE repo_id=? AND method=?",
        (production_repo_id, method),
    ).fetchall()
    matches = []
    for row in rows:
        if canonicalize_path(row["path"]) != path or not row["source_version"]:
            continue
        if row["source_version"] in test_versions:
            matches.append((row, None, "exact"))
            continue
        compatibility = (
            conn.execute(
                "SELECT id FROM api_version_compatibility WHERE test_version IN ({}) AND endpoint_version=? AND status='active'".format(
                    ",".join("?" * len(test_versions))
                ),
                (*test_versions, row["source_version"]),
            ).fetchall()
            if test_versions
            else []
        )
        matches.extend((row, item["id"], "compatible") for item in compatibility)
    return matches


def build(
    conn: sqlite3.Connection,
    repo_key: str,
    suite_root: Path,
    object_mapping_path: Path,
    features_root: Path | None = None,
) -> dict[str, int]:
    repo = conn.execute(
        "SELECT id FROM repos WHERE repo_key=? AND enabled=1", (repo_key,)
    ).fetchone()
    if not repo:
        raise ValueError(
            f"No enabled repos record for REST automation repository '{repo_key}'"
        )
    production_repo = conn.execute(
        "SELECT id FROM repos WHERE repo_key='ia-main' AND enabled=1"
    ).fetchone()
    if not production_repo:
        raise ValueError(
            "No enabled repos record for production REST repository 'ia-main'"
        )
    suite_root = suite_root.resolve()
    features_root = (
        features_root or suite_root / "src/test/resources/features/rest-api"
    ).resolve()
    if not features_root.is_relative_to(suite_root):
        raise ValueError("features_root must be located inside suite_root")
    repo_id = int(repo[0])
    production_repo_id = int(production_repo[0])
    mapping, mapping_diagnostics = load_object_mapping(object_mapping_path)
    mapping_file_id = (
        _file_id(conn, repo_id, suite_root, object_mapping_path)
        if object_mapping_path.is_relative_to(suite_root)
        else None
    )
    stats = {
        "features": 0,
        "cases": 0,
        "requests": 0,
        "links": 0,
        "diagnostics": len(mapping_diagnostics),
    }
    conn.execute("DELETE FROM test_diagnostics WHERE repo_id=?", (repo_id,))
    conn.execute("DELETE FROM test_cases WHERE repo_id=?", (repo_id,))
    for diagnostic in mapping_diagnostics:
        conn.execute(
            "INSERT INTO test_diagnostics(repo_id,file_id,kind,message) VALUES(?,?,?,?)",
            (repo_id, mapping_file_id, diagnostic.kind, diagnostic.message),
        )
    feature_paths = sorted(features_root.rglob("*.feature"))
    paired_properties = {
        path.with_suffix(".properties").resolve() for path in feature_paths
    }
    for properties_path in sorted(features_root.rglob("*.properties")):
        if properties_path.resolve() in paired_properties:
            continue
        properties_file_id = _file_id(
            conn,
            repo_id,
            suite_root,
            properties_path,
            read_contents=False,
        )
        conn.execute(
            "INSERT INTO test_diagnostics(repo_id,file_id,kind,message) VALUES(?,?,?,?)",
            (
                repo_id,
                properties_file_id,
                "orphan_properties",
                f"No same-stem feature file for: {properties_path.name}",
            ),
        )
        stats["diagnostics"] += 1
    for feature_path in feature_paths:
        file_id = _file_id(conn, repo_id, suite_root, feature_path)
        stats["features"] += 1
        try:
            cases = parse_feature(feature_path, mapping)
        except Exception as exc:
            stats["diagnostics"] += 1
            conn.execute(
                "INSERT INTO test_diagnostics(repo_id,file_id,kind,message) VALUES(?,?,?,?)",
                (repo_id, file_id, "feature_parse_error", str(exc)),
            )
            continue
        feature_hash = hashlib.sha1(feature_path.read_bytes()).hexdigest()
        for case in cases:
            cursor = conn.execute(
                "INSERT INTO test_cases(repo_id,file_id,feature_name,scenario_name,case_name,example_row,feature_line,scenario_line,eligibility,tags_json,jira_refs_json,source_hash) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    repo_id,
                    file_id,
                    case.feature_name,
                    case.scenario_name,
                    case.case_name,
                    case.example_row,
                    case.feature_line,
                    case.scenario_line,
                    case.eligibility,
                    json.dumps(case.tags),
                    json.dumps(case.jira_refs),
                    feature_hash,
                ),
            )
            case_id = int(cursor.lastrowid)
            stats["cases"] += 1
            properties_path = feature_path.with_suffix(".properties")
            properties_file_id = (
                _file_id(conn, repo_id, suite_root, properties_path)
                if properties_path.exists()
                else None
            )
            for version, source_kind, source_line in case.version_sources:
                conn.execute(
                    "INSERT INTO test_case_versions(test_case_id,version_label,source_kind,source_file_id,source_line,raw_value) VALUES(?,?,?,?,?,?)",
                    (
                        case_id,
                        version,
                        source_kind,
                        properties_file_id if source_kind == "properties" else file_id,
                        source_line,
                        version,
                    ),
                )
            for diagnostic in case.diagnostics:
                conn.execute(
                    "INSERT INTO test_diagnostics(repo_id,file_id,test_case_id,kind,message,source_line) VALUES(?,?,?,?,?,?)",
                    (
                        repo_id,
                        file_id,
                        case_id,
                        diagnostic.kind,
                        diagnostic.message,
                        diagnostic.line,
                    ),
                )
            for request in case.requests:
                cursor = conn.execute(
                    "INSERT INTO test_requests(test_case_id,ordinal,step_line,method,object_token,raw_path,normalized_path,request_version,expected_status,operation_kind) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        case_id,
                        request.ordinal,
                        request.line,
                        request.method,
                        request.object_token,
                        request.raw_path,
                        request.normalized_path,
                        request.version,
                        request.expected_status,
                        request.operation_kind,
                    ),
                )
                request_id = int(cursor.lastrowid)
                stats["requests"] += 1
                if request.explicit_version and request.version:
                    conn.execute(
                        "INSERT INTO test_case_versions(test_case_id,version_label,source_kind,source_file_id,source_line,raw_value) VALUES(?,?,?,?,?,?)",
                        (
                            case_id,
                            request.version,
                            "request_override",
                            file_id,
                            request.line,
                            request.version,
                        ),
                    )
                if request.normalized_path and not case.version_conflicted:
                    versions = (request.version,) if request.version else case.versions
                    for endpoint, compatibility_id, kind in _endpoint_matches(
                        conn,
                        production_repo_id,
                        request.method,
                        request.normalized_path,
                        versions,
                    ):
                        conn.execute(
                            "INSERT INTO test_endpoint_links(test_request_id,rest_endpoint_id,compatibility_id,resolution_kind) VALUES(?,?,?,?)",
                            (
                                request_id,
                                endpoint["id"],
                                compatibility_id,
                                "exact_version"
                                if kind == "exact"
                                else "compatible_version",
                            ),
                        )
                        if endpoint["entity_id"] is not None:
                            conn.execute(
                                "INSERT INTO test_entity_links(test_request_id,entity_id,rest_endpoint_id) VALUES(?,?,?)",
                                (request_id, endpoint["entity_id"], endpoint["id"]),
                            )
                        stats["links"] += 1
    conn.commit()
    return stats


@click.command()
@click.option("--db", "db_path", default=DEFAULT_DB, show_default=True)
@click.option(
    "--repo", "repo_key", required=True, help="Registered workspace repo_key."
)
@click.option(
    "--suite-root", type=click.Path(path_type=Path, exists=True), required=True
)
@click.option(
    "--features-root",
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    help="Feature directory; defaults to src/test/resources/features/rest-api inside suite-root.",
)
@click.option(
    "--object-mapping",
    "object_mapping",
    type=click.Path(path_type=Path, exists=True),
    required=True,
)
def main(
    db_path: str,
    repo_key: str,
    suite_root: Path,
    features_root: Path | None,
    object_mapping: Path,
) -> None:
    """Build deterministic Gherkin REST coverage evidence for one suite."""
    try:
        stats = build(
            get_connection(db_path),
            repo_key,
            suite_root,
            object_mapping,
            features_root,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(stats, sort_keys=True))


if __name__ == "__main__":
    main()
