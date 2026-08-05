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
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click

try:
    from catalog.db import get_connection
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from catalog.db import get_connection

from catalog.rest_automation_contract import (
    STATIC_MAP_PATH,
    ContractV1Paths,
    StaticMapEntry,
    audit_static_entry,
    load_non_request_inventory_contract,
    load_object_mapping_contract,
    load_static_map,
    load_version_compatibility_contract,
    static_map_hashes,
)

try:
    from gherkin.parser import Parser
except ModuleNotFoundError:  # helpful error for operators rather than fallback parsing
    Parser = None


DEFAULT_DB = "catalog/catalog.db"
PRODUCTION_REST_REPO_KEY = "ia-main"
EXTRACTOR_VERSION = "gherkin-coverage-v2-workflow-action"
VERSION_TAG = re.compile(r"^@version:([^\s]+)$", re.IGNORECASE)
JIRA_TAG = re.compile(r"^@([A-Z][A-Z0-9]+-\d+)$")
STATUS = re.compile(
    r"\b(?:status(?:\s+code)?|response\s+code)(?:\s+is)?\s*[\"']?(\d{3})", re.IGNORECASE
)
QUOTED = re.compile(r'"([^"]*)"')
GENERIC_REQUEST = re.compile(
    r'"(?P<method>GET|POST|PUT|PATCH|DELETE)"\s+to\s+(?:(?:child\s+)?"(?P<object>[^"]+)")',
    re.IGNORECASE,
)
SIMPLE_REQUEST = re.compile(
    r"\bI\s+(?P<verb>read|create|update|delete|patch|post|put)\s+(?:object)?\s*\"(?P<object>[^\"]+)\"",
    re.IGNORECASE,
)
CONTRACT_QUOTED_REQUEST = re.compile(
    r'^"(?P<method>[A-Za-z]+)"\s+to\s+"(?P<token>[^"]+)"'
    r'(?:\s+for\s+version\s+"(?P<version>[^"]+)")?'
    r'(?:\s+with\s+key\s+"[^"]*")?'
    r'(?:\s+and\s+file\s+"[^"]*")?$',
    re.IGNORECASE,
)
STATIC_QUOTED_REQUEST = re.compile(
    r'^"(?P<method>[A-Za-z]+)"\s+to\s+"(?P<token>[^"]+)"'
    r'(?:\s+with\s+key\s+"[^"]*")?'
    r'(?:\s+and\s+file\s+"[^"]*")?'
    r'(?:\s+get\s+variable\s+"[^"]*")?$', re.IGNORECASE)
CONTRACT_SIMPLE_REQUEST = re.compile(
    r'^I\s+(?P<verb>read|create|update|delete|patch|post|put)'
    r'\s+(?:object\s+)?"(?P<token>[^"]+)"'
    r'(?:\s+for\s+version\s+"(?P<version>[^"]+)")?$',
    re.IGNORECASE,
)
CONTRACT_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
KEY = re.compile(r'\bwith\s+key\s+"([^"]*)"', re.IGNORECASE)
PARENT = re.compile(
    r'\bfor\s+parent\s+"([^"]+)"\s+with\s+key\s+"([^"]*)"', re.IGNORECASE
)
STEP_VERSION = re.compile(r'\bfor\s+version\s+"([^"]+)"', re.IGNORECASE)
ACTION = re.compile(r'\b(?:action|workflow)\s+"([^"]+)"', re.IGNORECASE)
FEATURE_LINE = re.compile(r"^\s*Feature\s*:", re.IGNORECASE | re.MULTILINE)
COMMENTED_FEATURE_LINE = re.compile(
    r"^\s*#\s*Feature\s*:", re.IGNORECASE | re.MULTILINE
)


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
    workflow_action: str | None = None
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


class ContractV1ExtractionError(ValueError):
    """Raised when Contract-V1 feature text cannot produce safe evidence."""


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


def _versioned_feature_evidence(
    features_root: Path, version: str
) -> tuple[Path, Path] | None:
    """Return a representative feature/properties pair that declares a version."""
    for feature_path in sorted(features_root.rglob("*.feature")):
        properties_path = feature_path.with_suffix(".properties")
        metadata, _lines, _diagnostics = _read_properties_metadata_with_lines(
            properties_path
        )
        if metadata.get("version") == version:
            return feature_path, properties_path
        try:
            feature_text = feature_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            feature_text = feature_path.read_text(encoding="utf-8", errors="replace")
        if re.search(
            rf"@version:{re.escape(version)}(?:\b|$)", feature_text, re.IGNORECASE
        ):
            return feature_path, properties_path
    return None


def seed_api_version_compatibility(
    conn: sqlite3.Connection,
    repo_id: int,
    suite_root: Path,
    features_root: Path,
) -> int:
    """Seed explicit version compatibility evidence for v1-beta2 REST coverage."""
    evidence_pair = _versioned_feature_evidence(features_root, "v1-beta2")
    if evidence_pair is None:
        return 0

    feature_path, properties_path = evidence_pair
    conn.execute(
        """
        DELETE FROM api_version_compatibility
        WHERE repo_id = ? AND test_version = 'beta' AND endpoint_version = 's1'
        """,
        (repo_id,),
    )
    evidence = json.dumps(
        {
            "suite_root": str(suite_root),
            "features_root": str(features_root),
            "feature": str(feature_path),
            "properties": str(properties_path),
            "test_version": "v1-beta2",
            "endpoint_version": "s1",
            "scope": "rest_automation",
        },
        sort_keys=True,
    )
    conn.execute(
        """
        INSERT INTO api_version_compatibility(
            repo_id, test_version, endpoint_version, status, rationale, evidence
        )
        VALUES(?, ?, ?, 'active', ?, ?)
        ON CONFLICT(repo_id, test_version, endpoint_version) DO UPDATE SET
            status=excluded.status,
            rationale=excluded.rationale,
            evidence=excluded.evidence
        """,
        (
            repo_id,
            "v1-beta2",
            "s1",
            "REST automation coverage standardizes v1-beta2 requests onto s1 endpoints",
            evidence,
        ),
    )
    return 1


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
    path = re.sub(r"^/services/(?:v[^/]+|s\d+)(?=/)", "", path, flags=re.IGNORECASE)
    path = re.sub(r"//+", "/", path)
    return path.rstrip("/") or "/"


def workflow_action_for_request(operation_kind: str, normalized_path: str | None) -> str | None:
    """Return only a proven canonical Gateway workflow action.

    This deliberately rejects aliases, extra segments, and ordinary object
    routes.  A NULL is evidence of "not proven", not an extraction failure.
    """

    if operation_kind != "workflow" or not normalized_path:
        return None
    match = re.fullmatch(r"/workflows/[^/]+/[^/]+/([^/]+)", normalized_path)
    return match.group(1) if match else None


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
        workflow_action_for_request(operation, path),
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


def _contract_request_from_step(
    text: str,
    line: int,
    ordinal: int,
    mapping: dict[str, dict[str, str]],
) -> RequestEvidence | None:
    """Parse only the two Contract-V1 request grammars after substitution."""
    quoted = CONTRACT_QUOTED_REQUEST.fullmatch(text)
    simple = CONTRACT_SIMPLE_REQUEST.fullmatch(text)
    if quoted:
        method = quoted.group("method")
        token = quoted.group("token")
        version = quoted.group("version")
    elif simple:
        verb = simple.group("verb").lower()
        method = {
            "read": "GET",
            "create": "POST",
            "update": "PATCH",
            "delete": "DELETE",
            "patch": "PATCH",
            "post": "POST",
            "put": "PUT",
        }[verb]
        token = simple.group("token")
        version = simple.group("version")
    else:
        return None
    if not method.isascii():
        raise ContractV1ExtractionError(f"line {line}: request method must be ASCII")
    method = method.upper()
    if method not in CONTRACT_HTTP_METHODS:
        raise ContractV1ExtractionError(f"line {line}: unsupported HTTP method '{method}'")
    if "<" in token or ">" in token:
        raise ContractV1ExtractionError(
            f"line {line}: unresolved Examples token in request token '{token}'"
        )
    target = mapping.get(token)
    if target is None:
        raise ContractV1ExtractionError(
            f"line {line}: request token has no exact Contract-V1 mapping: '{token}'"
        )
    return RequestEvidence(
        ordinal=ordinal,
        line=line,
        method=method,
        object_token=token,
        raw_path=target["path"],
        normalized_path=target["path"],
        version=version,
        expected_status=None,
        operation_kind=(
            "collection" if target["coverage_scope"] == "endpoint" else "custom"
        ),
        workflow_action=None,
        explicit_version=version is not None,
    )


def _contract_request_shaped(text: str) -> bool:
    """Recognize malformed attempts at either closed Contract-V1 grammar."""
    stripped = text.strip()
    return bool(
        stripped.startswith('"')
        or re.match(
            r"^[A-Z][A-Z0-9_-]*\s+to\b",
            stripped,
        )
        or re.match(
            r"^I\s+[A-Za-z][A-Za-z-]*\s+(?:object\s+)?(?:\"[^\"]+\"|[A-Za-z][A-Za-z0-9_-]*)$",
            stripped,
            re.IGNORECASE,
        )
    )


def _contract_versions(
    feature: dict[str, Any],
    scenario: dict[str, Any],
    property_version: str | None,
    property_line: int | None,
) -> tuple[tuple[str, ...], tuple[tuple[str, str, int], ...]]:
    sources: list[tuple[str, str, int]] = []
    for tag in feature.get("tags", []):
        if match := VERSION_TAG.match(tag.get("name", "")):
            sources.append((match.group(1), "feature_tag", _line(tag)))
    for tag in scenario.get("tags", []):
        if match := VERSION_TAG.match(tag.get("name", "")):
            sources.append((match.group(1), "scenario_tag", _line(tag)))
    if property_version:
        sources.append((property_version, "properties", property_line or 0))
    versions = tuple(sorted({version for version, _kind, _line_no in sources}))
    return versions, tuple(sources)


def _contract_expected_statuses(
    steps: list[dict[str, Any]], values: dict[str, str], request_count: int
) -> dict[int, int]:
    expected: dict[int, int] = {}
    request_index = -1
    for step in steps:
        text = _substitute(step.get("text", ""), values)
        if CONTRACT_QUOTED_REQUEST.fullmatch(text) or CONTRACT_SIMPLE_REQUEST.fullmatch(text):
            request_index += 1
            continue
        if request_index >= 0 and (status := STATUS.search(text)):
            expected[request_index] = int(status.group(1))
    return {index: value for index, value in expected.items() if index < request_count}


def parse_feature_contract_v1(
    path: Path,
    mapping_entries: list[dict[str, str]],
    inventory_entries: list[dict[str, str]],
) -> list[CaseEvidence]:
    """Extract Contract-V1 cases with no route, alias, or version inference."""
    if Parser is None:
        raise RuntimeError("gherkin-official is required; run uv sync")
    mapping = {entry["token"]: entry for entry in mapping_entries}
    inventory: dict[str, str] = {}
    for entry in inventory_entries:
        step_text = entry["text"]
        digest = entry["sha256"]
        if hashlib.sha256(step_text.encode("utf-8")).hexdigest() != digest:
            raise ContractV1ExtractionError(
                "non-request inventory entry sha256 does not bind text"
            )
        inventory[step_text] = digest
    text = path.read_text(encoding="utf-8")
    if text.startswith("\ufeff"):
        text = text.lstrip("\ufeff")
    if not FEATURE_LINE.search(text) and COMMENTED_FEATURE_LINE.search(text):
        raise ContractV1ExtractionError(
            "Feature file has no parseable Feature declaration; ensure the Feature line is not commented out"
        )
    document = Parser().parse(text)
    feature = document.get("feature")
    if not feature:
        raise ContractV1ExtractionError("Feature file has no parseable Feature declaration")
    for child in feature.get("children", []):
        background = child.get("background")
        if not background:
            continue
        for step in background.get("steps", []):
            step_text = step.get("text", "")
            line = _line(step)
            if "<" in step_text or ">" in step_text:
                raise ContractV1ExtractionError(
                    f"line {line}: Background cannot contain Examples placeholders"
                )
            if _contract_request_shaped(step_text):
                raise ContractV1ExtractionError(
                    f"line {line}: Background cannot contain request-shaped or malformed steps"
                )
            if step_text in inventory:
                raise ContractV1ExtractionError(
                    f"line {line}: Background cannot reference non-request inventory"
                )
    feature_tags = _tags(feature.get("tags", []))
    metadata, property_lines, property_diagnostics = _read_properties_metadata_with_lines(
        path.with_suffix(".properties")
    )
    out: list[CaseEvidence] = []
    for child in feature.get("children", []):
        scenario = child.get("scenario")
        if not scenario:
            continue
        scenario_tags = feature_tags + _tags(scenario.get("tags", []))
        jira = tuple(tag[1:] for tag in scenario_tags if JIRA_TAG.match(tag))
        case_versions, version_sources = _contract_versions(
            feature, scenario, metadata.get("version"), property_lines.get("version")
        )
        for example_row, values in _example_rows(scenario):
            scenario_name = _substitute(scenario.get("name", ""), values)
            case_name = values.get("testCaseID") or scenario_name
            requests: list[RequestEvidence] = []
            diagnostics = list(property_diagnostics)
            for step in scenario.get("steps", []):
                step_text = _substitute(step.get("text", ""), values)
                line = _line(step)
                request = _contract_request_from_step(
                    step_text, line, len(requests) + 1, mapping
                )
                if request is not None:
                    versions = set(case_versions)
                    if request.version:
                        versions.add(request.version)
                    if len(versions) != 1:
                        reason = "missing" if not versions else "conflicting"
                        raise ContractV1ExtractionError(
                            f"line {line}: request has {reason} version evidence"
                        )
                    requests.append(
                        RequestEvidence(
                            **{
                                **request.__dict__,
                                "version": next(iter(versions)),
                            }
                        )
                    )
                    continue
                if _contract_request_shaped(step_text):
                    raise ContractV1ExtractionError(
                        f"line {line}: malformed or unsupported request-shaped step"
                    )
                if step_text not in inventory:
                    raise ContractV1ExtractionError(
                        f"line {line}: executable scenario step is absent from non-request inventory"
                    )
            expected = _contract_expected_statuses(scenario.get("steps", []), values, len(requests))
            requests = [
                RequestEvidence(
                    **{**request.__dict__, "expected_status": expected.get(index)}
                )
                for index, request in enumerate(requests)
            ]
            out.append(
                CaseEvidence(
                    feature_name=feature.get("name", ""),
                    scenario_name=scenario_name,
                    case_name=case_name,
                    example_row=example_row,
                    feature_line=_line(feature),
                    scenario_line=_line(scenario),
                    tags=scenario_tags,
                    jira_refs=jira,
                    eligibility=_eligible(scenario_tags),
                    version_conflicted=False,
                    versions=case_versions,
                    version_sources=version_sources,
                    requests=tuple(requests),
                    diagnostics=tuple(diagnostics),
                )
            )
    return out


def parse_feature_static_v1(path: Path) -> list[CaseEvidence]:
    """Parse V1 requests without treating ordinary steps as executable evidence.

    Static V1 deliberately records diagnostics rather than rejecting an entire
    feature for an unsupported method, malformed request, or version gap.
    """
    if Parser is None:
        raise RuntimeError("gherkin-official is required; run uv sync")
    text = path.read_text(encoding="utf-8").lstrip("\ufeff")
    feature = Parser().parse(text).get("feature")
    if not feature:
        raise ContractV1ExtractionError("Feature file has no parseable Feature declaration")
    metadata, property_lines, property_diagnostics = _read_properties_metadata_with_lines(path.with_suffix(".properties"))
    feature_tags = _tags(feature.get("tags", []))
    cases: list[CaseEvidence] = []
    for child in feature.get("children", []):
        if child.get("background"):
            for step in child["background"].get("steps", []):
                if _contract_request_shaped(step.get("text", "")):
                    raise ContractV1ExtractionError(f"line {_line(step)}: Background cannot contain request-shaped steps")
        scenario = child.get("scenario")
        if not scenario:
            continue
        tags = feature_tags + _tags(scenario.get("tags", []))
        versions, sources = _contract_versions(feature, scenario, metadata.get("version"), property_lines.get("version"))
        for example_row, values in _example_rows(scenario):
            requests: list[RequestEvidence] = []
            diagnostics = list(property_diagnostics)
            for step in scenario.get("steps", []):
                step_text = _substitute(step.get("text", ""), values)
                match = STATIC_QUOTED_REQUEST.fullmatch(step_text)
                if not match:
                    if _contract_request_shaped(step_text):
                        diagnostics.append(Diagnostic("malformed_request", "Malformed or unsupported request-shaped step", _line(step)))
                    continue
                method = match.group("method")
                token = match.group("token")
                if not method.isascii() or method.upper() not in CONTRACT_HTTP_METHODS:
                    diagnostics.append(Diagnostic("unsupported_method", f"Unsupported HTTP method '{method}'", _line(step)))
                    continue
                if "<" in token or ">" in token:
                    diagnostics.append(Diagnostic("unresolved_token", f"Unresolved request token '{token}'", _line(step)))
                    continue
                requests.append(RequestEvidence(len(requests)+1, _line(step), method.upper(), token, None, None,
                                                next(iter(versions)) if len(versions) == 1 else None,
                                                None, "collection", None, False))
            cases.append(CaseEvidence(feature.get("name", ""), _substitute(scenario.get("name", ""), values),
                values.get("testCaseID") or _substitute(scenario.get("name", ""), values), example_row,
                _line(feature), _line(scenario), tags, tuple(tag[1:] for tag in tags if JIRA_TAG.match(tag)),
                _eligible(tags), len(versions) != 1, versions, sources, tuple(requests), tuple(diagnostics)))
    return cases


def parse_feature(path: Path, mapping: dict[str, str]) -> list[CaseEvidence]:
    if Parser is None:
        raise RuntimeError("gherkin-official is required; run uv sync")
    text = path.read_text(encoding="utf-8")
    if text.startswith("\ufeff"):
        text = text.lstrip("\ufeff")
    if not FEATURE_LINE.search(text) and COMMENTED_FEATURE_LINE.search(text):
        raise ValueError(
            "Feature file has no parseable Feature declaration; "
            "ensure the Feature line is not commented out"
        )
    document = Parser().parse(text)
    feature = document.get("feature")
    if not feature:
        raise ValueError(
            "Feature file has no parseable Feature declaration; "
            "ensure the Feature line is not commented out"
        )
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
                    r.workflow_action,
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
) -> int:
    relative = str(path.relative_to(root))
    sha1 = hashlib.sha1(path.read_bytes()).hexdigest()
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


def _contract_input_hashes(suite_root: Path, paths: ContractV1Paths) -> list[dict[str, str]]:
    """Return the exact configured Contract-V1 input identity in stable order."""
    return [
        {
            "field": field,
            "path": path.relative_to(suite_root).as_posix(),
            "sha1": hashlib.sha1(path.read_bytes()).hexdigest(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for field, path in (
            ("object_mapping", paths.object_mapping),
            ("version_compatibility", paths.version_compatibility),
            ("non_request_inventory", paths.non_request_inventory),
        )
    ]


def _endpoint_matches(
    conn: sqlite3.Connection,
    production_repo_id: int,
    compatibility_repo_id: int,
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
                "SELECT id FROM api_version_compatibility WHERE repo_id=? AND test_version IN ({}) AND endpoint_version=? AND status='active'".format(
                    ",".join("?" * len(test_versions))
                ),
                (compatibility_repo_id, *test_versions, row["source_version"]),
            ).fetchall()
            if test_versions
            else []
        )
        matches.extend((row, item["id"], "compatible") for item in compatibility)
    return matches


def contract_v1_endpoint_match(
    conn: sqlite3.Connection,
    production_repo_id: int,
    method: str,
    path: str,
    request_version: str,
    bridges: list[dict[str, str]],
) -> tuple[sqlite3.Row, str | None] | None:
    """Return one exact Contract-V1 endpoint, or fail closed on ambiguity.

    Contract-V1 neither normalizes paths nor reads compatibility rows from the
    catalog.  A non-exact endpoint version is admissible only when the parsed,
    target-owned bridge document names that exact version pair.
    """
    if not path.startswith("/objects/"):
        return None
    rows = conn.execute(
        """SELECT id,entity_id,source_version,path FROM rest_endpoints
           WHERE repo_id=? AND method=? AND path=? AND source_version IS NOT NULL
           ORDER BY id""",
        (production_repo_id, method, path),
    ).fetchall()
    allowed: list[tuple[sqlite3.Row, str | None]] = []
    for row in rows:
        if row["source_version"] == request_version:
            allowed.append((row, None))
            continue
        if any(
            bridge["test_version"] == request_version
            and bridge["endpoint_version"] == row["source_version"]
            for bridge in bridges
        ):
            allowed.append((row, "compatible"))
    if len(allowed) != 1:
        return None
    return allowed[0]


def _stable_mapping_provenance(
    request: RequestEvidence, mapping_entries: list[dict[str, str]]
) -> str:
    """Persist only the exact Contract-V1 mapping entry used by a request."""
    entry = next(
        item for item in mapping_entries if item["token"] == request.object_token
    )
    return json.dumps(
        {
            "coverage_scope": entry["coverage_scope"],
            "path": entry["path"],
            "token": entry["token"],
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _contract_coverage_scope(
    request: RequestEvidence, mapping_entries: list[dict[str, str]]
) -> str:
    return next(
        item["coverage_scope"]
        for item in mapping_entries
        if item["token"] == request.object_token
    )


def build(
    conn: sqlite3.Connection,
    repo_key: str,
    suite_root: Path,
    object_mapping_path: Path,
    features_root: Path | None = None,
    *,
    contract_v1_paths: ContractV1Paths | None = None,
    static_contract_v1: bool = False,
    candidate_build_token: str | None = None,
    indexed_suite_target_sha: str | None = None,
    dependency_revisions: dict[str, str] | None = None,
) -> dict[str, int]:
    from catalog.repository_lifecycle import require_repository_extractable

    repo = require_repository_extractable(conn, repo_key)
    production_repo = require_repository_extractable(conn, PRODUCTION_REST_REPO_KEY)
    if not repo["enabled"]:
        raise ValueError(f"REST automation repository is disabled: {repo_key}")
    if not production_repo["enabled"]:
        raise ValueError(f"production REST repository is disabled: {PRODUCTION_REST_REPO_KEY}")
    suite_root = suite_root.resolve()
    if contract_v1_paths is not None:
        object_mapping_path = contract_v1_paths.object_mapping.resolve()
        features_root = contract_v1_paths.features_root.resolve()
    else:
        object_mapping_path = object_mapping_path.resolve()
    features_root = (
        features_root or suite_root / "src/test/resources/features/rest-api"
    ).resolve()
    if not features_root.is_relative_to(suite_root):
        raise ValueError("features_root must be located inside suite_root")
    repo_id = int(repo[0])
    production_repo_id = int(production_repo[0])
    if static_contract_v1:
        static_entries = load_static_map(STATIC_MAP_PATH)
        mapping = {}
        mapping_diagnostics = []
        compatibility_rows = 0
        contract_mapping_entries = None
        contract_bridges = []
        contract_inventory = []
        contract_input_hashes = static_map_hashes(STATIC_MAP_PATH)
        object_mapping_path = STATIC_MAP_PATH
    elif contract_v1_paths is None:
        mapping, mapping_diagnostics = load_object_mapping(object_mapping_path)
        compatibility_rows = seed_api_version_compatibility(
            conn, repo_id, suite_root, features_root
        )
        contract_mapping_entries: list[dict[str, str]] | None = None
        contract_bridges: list[dict[str, str]] = []
        contract_inventory: list[dict[str, str]] = []
        contract_input_hashes: list[dict[str, str]] = []
    else:
        contract_mapping_entries = load_object_mapping_contract(object_mapping_path)
        contract_bridges = load_version_compatibility_contract(
            contract_v1_paths.version_compatibility
        )
        contract_inventory = load_non_request_inventory_contract(
            contract_v1_paths.non_request_inventory
        )
        mapping = {}
        mapping_diagnostics = []
        compatibility_rows = 0
        contract_input_hashes = _contract_input_hashes(suite_root, contract_v1_paths)
    mapping_relative = (
        object_mapping_path.resolve().relative_to(suite_root).as_posix()
        if object_mapping_path.resolve().is_relative_to(suite_root)
        else None
    )
    # A refresh candidate already contains scan evidence.  Verify that the
    # manifest-owned mapping has exactly one matching source row and the blob
    # hash agrees with the bytes parsed below.  Standalone legacy invocation
    # retains the narrow upsert for operator compatibility.
    mapping_sha1 = hashlib.sha1(object_mapping_path.read_bytes()).hexdigest()
    mapping_rows = (
        conn.execute(
            "SELECT id,sha1 FROM files WHERE repo_id=? AND path=?",
            (repo_id, mapping_relative),
        ).fetchall()
        if mapping_relative is not None
        else []
    )
    if candidate_build_token is not None and not static_contract_v1:
        if len(mapping_rows) != 1 or mapping_rows[0]["sha1"] != mapping_sha1:
            raise ValueError(
                "manifest object_mapping must have exactly one candidate files row with matching sha1"
            )
        if contract_v1_paths is not None:
            for artifact in contract_input_hashes:
                rows = conn.execute(
                    "SELECT sha1 FROM files WHERE repo_id=? AND path=?",
                    (repo_id, artifact["path"]),
                ).fetchall()
                expected_sha1 = hashlib.sha1(
                    (suite_root / artifact["path"]).read_bytes()
                ).hexdigest()
                if len(rows) != 1 or rows[0]["sha1"] != expected_sha1:
                    raise ValueError(
                        "Contract-V1 input must have exactly one candidate files row with matching sha1: "
                        + artifact["path"]
                    )
        mapping_file_id = int(mapping_rows[0]["id"])
    else:
        mapping_file_id = (
            _file_id(conn, repo_id, suite_root, object_mapping_path)
            if mapping_relative is not None
            else None
        )
    stats = {
        "features": 0,
        "cases": 0,
        "requests": 0,
        "links": 0,
        "diagnostics": len(mapping_diagnostics),
        "compatibility_rows": compatibility_rows,
    }
    conn.execute("DELETE FROM test_diagnostics WHERE repo_id=?", (repo_id,))
    conn.execute(
        """
        DELETE FROM test_endpoint_links
        WHERE test_request_id IN (
            SELECT tr.id
            FROM test_requests tr
            JOIN test_cases tc ON tc.id = tr.test_case_id
            WHERE tc.repo_id = ?
        )
        """,
        (repo_id,),
    )
    conn.execute(
        """
        DELETE FROM test_entity_links
        WHERE test_request_id IN (
            SELECT tr.id
            FROM test_requests tr
            JOIN test_cases tc ON tc.id = tr.test_case_id
            WHERE tc.repo_id = ?
        )
        """,
        (repo_id,),
    )
    conn.execute(
        """
        DELETE FROM test_case_versions
        WHERE test_case_id IN (
            SELECT id FROM test_cases WHERE repo_id = ?
        )
        """,
        (repo_id,),
    )
    conn.execute(
        """
        DELETE FROM test_requests
        WHERE test_case_id IN (
            SELECT id FROM test_cases WHERE repo_id = ?
        )
        """,
        (repo_id,),
    )
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
            cases = (
                parse_feature_contract_v1(
                    feature_path, contract_mapping_entries, contract_inventory
                )
                if contract_mapping_entries is not None
                else parse_feature_static_v1(feature_path)
                if static_contract_v1
                else parse_feature(feature_path, mapping)
            )
        except Exception as exc:
            if contract_v1_paths is not None:
                raise ContractV1ExtractionError(
                    f"Contract-V1 feature {feature_path.relative_to(suite_root)} failed: {exc}"
                ) from exc
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
                static_entry: StaticMapEntry | None = None
                if static_contract_v1 and request.version:
                    candidates = [entry for entry in static_entries if entry.target_repo == PRODUCTION_REST_REPO_KEY
                                  and entry.token == request.object_token and entry.revision == request.version
                                  and entry.method == request.method]
                    if len(candidates) == 1:
                        static_entry = candidates[0]
                        request = RequestEvidence(**{**request.__dict__, "raw_path": static_entry.route,
                                                      "normalized_path": static_entry.route})
                    else:
                        conn.execute("INSERT INTO test_diagnostics(repo_id,file_id,test_case_id,kind,message,source_line) VALUES(?,?,?,?,?,?)",
                                     (repo_id, file_id, case_id, "static_map_unresolved",
                                      f"No unique static map entry for token '{request.object_token}'", request.line))
                        stats["diagnostics"] += 1
                coverage_scope = (
                    "endpoint" if static_entry is not None else "unknown"
                    if static_contract_v1
                    else
                    _contract_coverage_scope(request, contract_mapping_entries)
                    if contract_mapping_entries is not None
                    else "unknown"
                )
                mapping_provenance = (
                    json.dumps(static_entry.provenance(), sort_keys=True, separators=(",", ":"))
                    if static_entry is not None
                    else
                    _stable_mapping_provenance(request, contract_mapping_entries)
                    if contract_mapping_entries is not None
                    else None
                )
                cursor = conn.execute(
                    "INSERT INTO test_requests(test_case_id,ordinal,step_line,method,object_token,raw_path,normalized_path,request_version,expected_status,operation_kind,coverage_scope,mapping_provenance_json,workflow_action) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
                        coverage_scope,
                        mapping_provenance,
                        request.workflow_action,
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
                    if static_contract_v1:
                        if static_entry is None:
                            continue
                        try:
                            entity_id, endpoint_id, cited = audit_static_entry(
                                conn, static_entry, production_repo_id=production_repo_id
                            )
                        except Exception as exc:
                            raise ContractV1ExtractionError(f"Contract-V1 static map audit failed: {exc}") from exc
                        for item in cited:
                            if item not in contract_input_hashes:
                                # The indexed SHA-1 remains authoritative.  When the
                                # immutable builder snapshot exposes the same blob we
                                # retain SHA-256 as an additional drift witness.
                                source = Path(str(production_repo["local_root"])) / item["path"]
                                source_bytes = source.read_bytes() if source.is_file() else b""
                                sha256 = hashlib.sha256(source_bytes).hexdigest() if source_bytes and hashlib.sha1(source_bytes).hexdigest() == item["sha1"] else ""
                                contract_input_hashes.append({**item, "sha256": sha256})
                        conn.execute("INSERT INTO test_endpoint_links(test_request_id,rest_endpoint_id,compatibility_id,resolution_kind) VALUES(?,?,?,?)",
                                     (request_id, endpoint_id, None, "exact_version"))
                        if entity_id is not None:
                            conn.execute("INSERT INTO test_entity_links(test_request_id,entity_id,rest_endpoint_id) VALUES(?,?,?)",
                                         (request_id, entity_id, endpoint_id))
                        stats["links"] += 1
                        continue
                    if contract_v1_paths is not None:
                        if coverage_scope != "endpoint" or not request.version:
                            continue
                        match = contract_v1_endpoint_match(
                            conn,
                            production_repo_id,
                            request.method,
                            request.normalized_path,
                            request.version,
                            contract_bridges,
                        )
                        if match is None:
                            raise ContractV1ExtractionError(
                                "Contract-V1 hard diagnostic: endpoint_match_unresolved "
                                f"line={request.line} method={request.method} "
                                f"path={request.normalized_path} version={request.version}"
                            )
                        endpoint, compatibility_kind = match
                        conn.execute(
                            "INSERT INTO test_endpoint_links(test_request_id,rest_endpoint_id,compatibility_id,resolution_kind) VALUES(?,?,?,?)",
                            (
                                request_id,
                                endpoint["id"],
                                None,
                                "exact_version"
                                if compatibility_kind is None
                                else "compatible_version",
                            ),
                        )
                        if endpoint["entity_id"] is not None:
                            conn.execute(
                                "INSERT INTO test_entity_links(test_request_id,entity_id,rest_endpoint_id) VALUES(?,?,?)",
                                (request_id, endpoint["entity_id"], endpoint["id"]),
                            )
                        stats["links"] += 1
                        continue
                    versions = (request.version,) if request.version else case.versions
                    for endpoint, compatibility_id, kind in _endpoint_matches(
                        conn,
                        production_repo_id,
                        repo_id,
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
    if candidate_build_token is not None:
        if not indexed_suite_target_sha or dependency_revisions is None:
            raise ValueError("candidate coverage requires target SHA and dependency revisions")
        revisions = dict(sorted(dependency_revisions.items()))
        if revisions.get(repo_key) != indexed_suite_target_sha:
            raise ValueError("candidate coverage target SHA must match dependency revisions")
        fingerprint_payload = {
            "extractor_version": EXTRACTOR_VERSION,
            "dependency_revisions": revisions,
            "entity_mapping_sha1": mapping_sha1,
        }
        if contract_v1_paths is not None or static_contract_v1:
            fingerprint_payload.update(
                {
                    "coverage_contract_version": 1,
                    "contract_input_hashes": contract_input_hashes,
                }
            )
        dependency_fingerprint = hashlib.sha256(
            json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        conn.execute(
            """INSERT INTO test_coverage_build_state(
                   repo_id,extractor_version,candidate_build_token,indexed_suite_target_sha,
                   dependency_revisions_json,entity_mapping_sha1,coverage_contract_version,
                   contract_input_hashes_json,coverage_dependency_fingerprint
               ) VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(repo_id) DO UPDATE SET
                   extractor_version=excluded.extractor_version,
                   candidate_build_token=excluded.candidate_build_token,
                   indexed_suite_target_sha=excluded.indexed_suite_target_sha,
                   dependency_revisions_json=excluded.dependency_revisions_json,
                   entity_mapping_sha1=excluded.entity_mapping_sha1,
                   coverage_contract_version=excluded.coverage_contract_version,
                   contract_input_hashes_json=excluded.contract_input_hashes_json,
                   coverage_dependency_fingerprint=excluded.coverage_dependency_fingerprint,
                   built_at=CURRENT_TIMESTAMP""",
            (
                repo_id,
                EXTRACTOR_VERSION,
                candidate_build_token,
                indexed_suite_target_sha,
                json.dumps(revisions, sort_keys=True, separators=(",", ":")),
                mapping_sha1,
                1 if (contract_v1_paths is not None or static_contract_v1) else 0,
                json.dumps(contract_input_hashes, separators=(",", ":")),
                dependency_fingerprint,
            ),
        )
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
