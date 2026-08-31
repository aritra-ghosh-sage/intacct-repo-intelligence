"""Validation and deterministic normalization for the Greenfield Step 1.5 trace."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from greenfield.artifact_io import artifact_sha256
from greenfield.behavior_contract import (
    BehaviorContractError,
    generate_behavior_contract,
)
from greenfield.source_identity import source_identity
from greenfield.step1_capture import evidence_fingerprint

SCHEMA_VERSION = "0.1"
ANALYSIS_KIND = "greenfield_pr_impact_step_1_5"
SHA = re.compile(r"^[0-9a-f]{40}$")
SURFACE_STATES = {
    "available",
    "empty",
    "unavailable",
    "not_run",
    "unresolved",
    "ambiguous",
    "dynamic",
}
CALL_RELATION_TYPES = {"CALLS", "STATIC_CALLS"}


class TraceError(ValueError):
    """Raised when the Step 1.5 trace cannot safely become evidence."""


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TraceError(f"{label} must be a non-empty string")
    return value.strip()


def _sha(value: Any, label: str) -> str:
    result = _text(value, label).lower()
    if not SHA.fullmatch(result):
        raise TraceError(f"{label} must be a lowercase 40-character SHA")
    return result


def _paths(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise TraceError(f"{label} must be a non-empty list")
    result = sorted({_text(item, f"{label} item") for item in value})
    if any("*" in item or "?" in item for item in result):
        raise TraceError(f"{label} must contain exact paths")
    return result


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise TraceError(f"{label} must be a positive integer")
    return value


def _validate_symbol_rows(value: Any, changed_paths: set[str]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise TraceError("affected_symbols must be a list")
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise TraceError(f"affected_symbols[{index}] must be an object")
        symbol = _text(item.get("symbol"), f"affected_symbols[{index}].symbol")
        path = _text(item.get("path"), f"affected_symbols[{index}].path")
        if path not in changed_paths:
            raise TraceError(f"affected_symbols[{index}] must reference a changed path")
        line = item.get("line", 1)
        if isinstance(line, bool) or not isinstance(line, int) or line < 1:
            raise TraceError(f"affected_symbols[{index}].line must be positive")
        key = (symbol, path)
        if key in seen:
            raise TraceError(f"duplicate affected symbol: {symbol} at {path}")
        seen.add(key)
        rows.append(
            {
                "symbol": symbol,
                "path": path,
                "line": line,
                "role": item.get("role", "affected"),
            }
        )
    return sorted(rows, key=lambda item: (item["path"], item["line"], item["symbol"]))


def _validate_surfaces(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise TraceError("surfaces must be an object")
    result: dict[str, str] = {}
    for name, raw in value.items():
        key = _text(name, "surface name")
        status = raw.get("status") if isinstance(raw, Mapping) else raw
        status = _text(status, f"surface {key} status")
        if status not in SURFACE_STATES:
            raise TraceError(f"surface {key} has unsupported status: {status}")
        result[key] = status
    return dict(sorted(result.items()))


def _validate_truncation_metadata(trace: Mapping[str, Any]) -> None:
    """Validate optional partial-output metadata when a provider truncates a trace."""

    if "truncated" not in trace:
        return
    truncated = trace.get("truncated")
    if not isinstance(truncated, bool):
        raise TraceError("truncated must be a boolean")
    if not truncated:
        return
    reason = trace.get("truncation_reason")
    if reason is not None and not isinstance(reason, str):
        raise TraceError("truncation_reason must be a string")
    omitted_counts = trace.get("omitted_counts")
    if omitted_counts is None:
        return
    if not isinstance(omitted_counts, Mapping):
        raise TraceError("omitted_counts must be an object")
    for key, count in omitted_counts.items():
        if not isinstance(key, str) or isinstance(count, bool) or not isinstance(count, int):
            raise TraceError("omitted_counts entries must map string keys to integers")


def _normalize_surfaces(value: Any) -> dict[str, str]:
    """Normalize provider surface records into the persisted status map."""

    if not isinstance(value, list):
        return _validate_surfaces(value)

    normalized: dict[str, str] = {}
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise TraceError(f"surfaces[{index}] must be an object")
        name = _text(item.get("surface"), f"surfaces[{index}].surface")
        if name in normalized:
            raise TraceError(f"duplicate surface: {name}")
        status = _text(item.get("status"), f"surfaces[{index}].status")
        normalized[name] = status
    return _validate_surfaces(normalized)


def _normalize_symbol_paths(value: Any, label: str) -> dict[str, str]:
    if isinstance(value, list):
        normalized: dict[str, str] = {}
        for index, item in enumerate(value):
            if not isinstance(item, Mapping):
                raise TraceError(f"{label}[{index}] must be an object")
            symbol = _text(item.get("symbol"), f"{label}[{index}].symbol")
            if symbol in normalized:
                raise TraceError(f"duplicate symbol path: {symbol}")
            path = _text(item.get("path"), f"{label}[{index}].path")
            if "*" in path or "?" in path:
                raise TraceError(f"{label} must contain exact paths")
            normalized[symbol] = path
        return dict(sorted(normalized.items()))
    if not isinstance(value, Mapping):
        raise TraceError(f"{label} must be an object")
    normalized = {}
    for symbol, raw in value.items():
        key = _text(symbol, f"{label} key")
        if key in normalized:
            raise TraceError(f"duplicate symbol path: {key}")
        if isinstance(raw, Mapping):
            path = _text(raw.get("path"), f"{label}[{key}].path")
        else:
            path = _text(raw, f"{label}[{key}]")
        if "*" in path or "?" in path:
            raise TraceError(f"{label} must contain exact paths")
        normalized[key] = path
    return dict(sorted(normalized.items()))


def _symbols(value: Any, label: str) -> list[str]:
    if not isinstance(value, list):
        raise TraceError(f"{label} must be a list")
    return sorted({_text(item, f"{label} item") for item in value})


def _normalize_call(
    value: Any,
    *,
    label: str,
    revision: str,
    changed_paths: set[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TraceError(f"{label} must be an object")
    call = dict(value)
    legacy_kind = call.get("kind")
    relationship_type = call.get("relationship_type")
    if relationship_type is None and legacy_kind is not None:
        call["relationship_type"] = legacy_kind
    elif (
        relationship_type is not None
        and legacy_kind is not None
        and relationship_type != legacy_kind
    ):
        raise TraceError(f"{label} has conflicting relationship_type and kind")
    call.pop("kind", None)
    call["source_symbol"] = _text(call.get("source_symbol"), f"{label}.source_symbol")
    call["target_symbol"] = _text(call.get("target_symbol"), f"{label}.target_symbol")
    call["relationship_type"] = _text(
        call.get("relationship_type"), f"{label}.relationship_type"
    )
    if call["relationship_type"] not in CALL_RELATION_TYPES:
        raise TraceError(f"{label}.relationship_type is invalid")
    call["source_path"] = _text(call.get("source_path"), f"{label}.source_path")
    if changed_paths is not None and call["source_path"] not in changed_paths:
        raise TraceError(f"{label}.source_path must reference a changed path")
    call["source_line"] = _positive_int(call.get("source_line"), f"{label}.source_line")
    source_revision = call.get("source_revision", revision)
    call["source_revision"] = _sha(source_revision, f"{label}.source_revision")
    if call["source_revision"] != revision:
        raise TraceError(f"{label}.source_revision does not match Step 1")
    call["target_path"] = _text(call.get("target_path"), f"{label}.target_path")
    if "*" in call["target_path"] or "?" in call["target_path"]:
        raise TraceError(f"{label}.target_path must be an exact path")
    if "target_line" in call and call["target_line"] is not None:
        call["target_line"] = _positive_int(call.get("target_line"), f"{label}.target_line")
    if "target_revision" in call and call["target_revision"] is not None:
        call["target_revision"] = _sha(
            call.get("target_revision"), f"{label}.target_revision"
        )
        if call["target_revision"] != revision:
            raise TraceError(f"{label}.target_revision does not match Step 1")
    call["resolution"] = _text(call.get("resolution"), f"{label}.resolution")
    if call["resolution"] != "exact":
        raise TraceError(f"{label}.resolution must be exact")
    return call


def _normalize_calls(
    value: Any,
    *,
    revision: str,
    changed_paths: set[str],
    label: str = "calls",
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise TraceError(f"{label} must be a list")
    normalized = [
        _normalize_call(
            item,
            label=f"{label}[{index}]",
            revision=revision,
            changed_paths=changed_paths,
        )
        for index, item in enumerate(value)
    ]
    return normalized


def _normalize_behavior(
    value: Any,
    *,
    index: int,
    revision: str,
    changed_paths: set[str],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TraceError(f"behaviors[{index}] must be an object")
    behavior = dict(value)
    behavior["kind"] = _text(behavior.get("kind", "behavior"), f"behaviors[{index}].kind")
    behavior["entry_symbols"] = _symbols(
        behavior.get("entry_symbols"), f"behaviors[{index}].entry_symbols"
    )
    if not behavior["entry_symbols"]:
        raise TraceError(f"behaviors[{index}].entry_symbols must be non-empty")
    behavior["source_paths"] = _paths(
        behavior.get("source_paths"), f"behaviors[{index}].source_paths"
    )
    if not set(behavior["source_paths"]).issubset(changed_paths):
        raise TraceError(f"behaviors[{index}].source_paths contains an unchanged path")
    behavior["symbol_paths"] = _normalize_symbol_paths(
        behavior.get("symbol_paths", {}), f"behaviors[{index}].symbol_paths"
    )
    behavior["edges"] = _normalize_calls(
        behavior.get("edges", []),
        revision=revision,
        changed_paths=set(behavior["source_paths"]),
        label=f"behaviors[{index}].edges",
    )
    edge_target_paths = {edge["target_path"] for edge in behavior["edges"]}
    if any(
        path not in changed_paths and path not in edge_target_paths
        for path in behavior["symbol_paths"].values()
    ):
        raise TraceError(
            f"behaviors[{index}].symbol_paths contains an unbound path"
        )
    behavior["surfaces"] = _normalize_surfaces(behavior.get("surfaces", {}))
    if "description" in behavior and behavior["description"] is not None:
        behavior["description"] = _text(
            behavior["description"], f"behaviors[{index}].description"
        )
    return behavior


def _normalize_behaviors(
    value: Any,
    *,
    revision: str,
    changed_paths: set[str],
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise TraceError("behaviors must be a non-empty list")
    return [
        _normalize_behavior(
            item,
            index=index,
            revision=revision,
            changed_paths=changed_paths,
        )
        for index, item in enumerate(value)
    ]


def _call_key(
    value: Mapping[str, Any],
) -> tuple[str, str, str, str, int, str, str, str]:
    return (
        _text(value.get("source_symbol"), "call source_symbol"),
        _text(value.get("target_symbol"), "call target_symbol"),
        _text(value.get("relationship_type"), "call relationship_type"),
        _text(value.get("source_path"), "call source_path"),
        value["source_line"],
        _sha(value.get("source_revision"), "call source_revision"),
        _text(value.get("target_path"), "call target_path"),
        _text(value.get("resolution"), "call resolution"),
    )


def _validate_call(
    value: Any,
    *,
    label: str,
    revision: str,
    changed_paths: set[str],
) -> None:
    if not isinstance(value, Mapping):
        raise TraceError(f"{label} must be an object")
    _text(value.get("source_symbol"), f"{label}.source_symbol")
    _text(value.get("target_symbol"), f"{label}.target_symbol")
    if value.get("relationship_type") not in CALL_RELATION_TYPES:
        raise TraceError(f"{label}.relationship_type is invalid")
    source_path = _text(value.get("source_path"), f"{label}.source_path")
    if source_path not in changed_paths:
        raise TraceError(f"{label}.source_path must reference a changed path")
    _positive_int(value.get("source_line"), f"{label}.source_line")
    if _sha(value.get("source_revision"), f"{label}.source_revision") != revision:
        raise TraceError(f"{label}.source_revision does not match Step 1")
    target_path = _text(value.get("target_path"), f"{label}.target_path")
    if "*" in target_path or "?" in target_path:
        raise TraceError(f"{label}.target_path must be an exact path")
    target_line = value.get("target_line")
    if target_line is not None:
        _positive_int(target_line, f"{label}.target_line")
    target_revision = value.get("target_revision")
    if target_revision is not None and _sha(
        target_revision, f"{label}.target_revision"
    ) != revision:
        raise TraceError(f"{label}.target_revision does not match Step 1")
    if value.get("resolution") != "exact":
        raise TraceError(f"{label}.resolution must be exact")


def validate_trace(step1: Mapping[str, Any], trace: Mapping[str, Any]) -> list[str]:
    """Return stable validation errors for the generated trace."""

    errors: list[str] = []
    try:
        source = step1.get("input")
        if not isinstance(source, Mapping):
            raise TraceError("Step 1 input must be an object")
        canonical, repo_key = source_identity(source)
        revision = _sha(
            source.get("target_revision") or source.get("head_sha"),
            "Step 1 target revision",
        )
        changed_paths = set(
            _paths(
                source.get("changed_paths")
                or [row.get("path") for row in step1.get("changed_files", [])],
                "Step 1 changed paths",
            )
        )
        if trace.get("schema_version") != SCHEMA_VERSION:
            raise TraceError("schema_version is invalid")
        if trace.get("analysis_kind") != ANALYSIS_KIND:
            raise TraceError("analysis_kind is invalid")
        if trace.get("repository") not in {canonical, repo_key}:
            raise TraceError("trace repository does not match Step 1")
        if _sha(trace.get("revision"), "trace revision") != revision:
            raise TraceError("trace revision does not match Step 1")
        if _paths(trace.get("changed_paths"), "trace changed paths") != sorted(
            changed_paths
        ):
            raise TraceError("trace changed paths do not match Step 1")
        _validate_symbol_rows(trace.get("affected_symbols"), changed_paths)
        _validate_surfaces(trace.get("surfaces", {}))
        _validate_truncation_metadata(trace)
        identity = trace.get("input")
        if not isinstance(identity, Mapping):
            raise TraceError("trace input must be an object")
        if identity.get("repository") not in {canonical, repo_key}:
            raise TraceError("trace input repository does not match Step 1")
        if identity.get("repo_key") != repo_key:
            raise TraceError("trace input repo_key does not match Step 1")
        if identity.get("pr_number") != source.get("pr_number"):
            raise TraceError("trace input PR number does not match Step 1")
        if _sha(identity.get("base_sha"), "trace input base_sha") != _sha(
            source.get("base_sha") or source.get("base_revision"),
            "Step 1 base revision",
        ):
            raise TraceError("trace input base SHA does not match Step 1")
        if _sha(identity.get("head_sha"), "trace input head_sha") != revision:
            raise TraceError("trace input head SHA does not match Step 1")
        if _paths(identity.get("changed_paths"), "trace input changed paths") != sorted(
            changed_paths
        ):
            raise TraceError("trace input changed paths do not match Step 1")
        calls = trace.get("calls")
        if not isinstance(calls, list):
            raise TraceError("calls must be a list")
        for index, call in enumerate(calls):
            _validate_call(
                call,
                label=f"calls[{index}]",
                revision=revision,
                changed_paths=changed_paths,
            )
        behaviors = trace.get("behaviors")
        if not isinstance(behaviors, list) or not behaviors:
            raise TraceError("behaviors must be a non-empty list")
        for behavior_index, behavior in enumerate(behaviors):
            if not isinstance(behavior, Mapping):
                raise TraceError(f"behaviors[{behavior_index}] must be an object")
            behavior_paths = set(
                _paths(
                    behavior.get("source_paths"),
                    f"behaviors[{behavior_index}].source_paths",
                )
            )
            if not behavior_paths.issubset(changed_paths):
                raise TraceError(
                    f"behaviors[{behavior_index}].source_paths contains an unchanged path"
                )
            symbol_paths = behavior.get("symbol_paths", {})
            if not isinstance(symbol_paths, Mapping):
                raise TraceError(
                    f"behaviors[{behavior_index}].symbol_paths must be an object"
                )
            edges = behavior.get("edges", [])
            if not isinstance(edges, list):
                raise TraceError(f"behaviors[{behavior_index}].edges must be a list")
            edge_target_paths: set[str] = set()
            for edge_index, edge in enumerate(edges):
                _validate_call(
                    edge,
                    label=f"behaviors[{behavior_index}].edges[{edge_index}]",
                    revision=revision,
                    changed_paths=behavior_paths,
                )
                edge_target_paths.add(str(edge["target_path"]))
            for symbol, path in symbol_paths.items():
                _text(symbol, f"behaviors[{behavior_index}].symbol_paths key")
                normalized_path = _text(
                    path, f"behaviors[{behavior_index}].symbol_paths value"
                )
                if (
                    normalized_path not in changed_paths
                    and normalized_path not in edge_target_paths
                ):
                    raise TraceError(
                        f"behaviors[{behavior_index}].symbol_paths contains an unbound path"
                    )
        contract = generate_behavior_contract(step1, trace)
        expected_calls = {_call_key(edge) for edge in contract["generation"]["edges"]}
        actual_calls = {_call_key(call) for call in calls}
        if actual_calls != expected_calls or len(actual_calls) != len(calls):
            raise TraceError("trace calls must exactly match validated behavior edges")
        provenance = trace.get("provenance")
        if not isinstance(provenance, Mapping):
            raise TraceError("trace provenance must be an object")
        if provenance.get("source_revision") != revision:
            raise TraceError("trace provenance source_revision does not match Step 1")
        if provenance.get("step1_evidence_sha256") != evidence_fingerprint(step1):
            raise TraceError(
                "trace provenance Step 1 evidence fingerprint does not match"
            )
        unsigned = copy.deepcopy(dict(trace))
        unsigned_provenance = unsigned.get("provenance")
        if not isinstance(unsigned_provenance, dict):
            raise TraceError("trace provenance must be an object")
        unsigned_provenance.pop("trace_sha256", None)
        if provenance.get("trace_sha256") != artifact_sha256(unsigned):
            raise TraceError("trace provenance trace_sha256 does not match contents")
    except (TraceError, BehaviorContractError, TypeError, ValueError) as exc:
        errors.append(str(exc))
    return errors


def normalize_trace(
    step1: Mapping[str, Any],
    raw: Mapping[str, Any],
    *,
    agent_metadata: Mapping[str, Any],
    context_sha256: str,
) -> dict[str, Any]:
    """Attach trusted Step 1 provenance without trusting model metadata."""

    trace = copy.deepcopy(dict(raw))
    source = step1["input"]
    canonical, _repo_key = source_identity(source)
    trace["schema_version"] = SCHEMA_VERSION
    trace["analysis_kind"] = ANALYSIS_KIND
    trace["repository"] = canonical
    trace["revision"] = str(
        source.get("target_revision") or source.get("head_sha")
    ).lower()
    trace["changed_paths"] = sorted(
        {str(row.get("path") or row.get("filename")) for row in step1["changed_files"]}
    )
    trace["input"] = {
        "repository": canonical,
        "repo_key": source.get("repo_key") or source.get("source_repo_key"),
        "pr_number": source.get("pr_number"),
        "base_sha": source.get("base_sha") or source.get("base_revision"),
        "head_sha": trace["revision"],
        "changed_paths": trace["changed_paths"],
    }
    trace["affected_symbols"] = _validate_symbol_rows(
        trace.get("affected_symbols", []), set(trace["changed_paths"])
    )
    trace["surfaces"] = _normalize_surfaces(trace.get("surfaces", {}))
    trace["behaviors"] = _normalize_behaviors(
        trace.get("behaviors", []),
        revision=trace["revision"],
        changed_paths=set(trace["changed_paths"]),
    )
    trace["calls"] = _normalize_calls(
        trace.get("calls", []),
        revision=trace["revision"],
        changed_paths=set(trace["changed_paths"]),
    )
    trace["provenance"] = {
        "read_only": True,
        "catalog_mutation": "none",
        "github_writes": "none",
        "source_revision": trace["revision"],
        "step1_evidence_sha256": evidence_fingerprint(step1),
        "context_sha256": context_sha256,
        "agent": dict(agent_metadata),
    }
    unsigned = copy.deepcopy(trace)
    unsigned["provenance"].pop("trace_sha256", None)
    trace["provenance"]["trace_sha256"] = artifact_sha256(unsigned)
    return trace


def build_trace_rejection_diagnostic(
    step1: Mapping[str, Any],
    *,
    stage: str,
    reason: str,
    contract_path: str | Path | None,
    provider_name: str,
    provider_model: str | None,
    raw_provider_response: Any | None = None,
    normalized_trace: Mapping[str, Any] | None = None,
    context_sha256: str | None = None,
    provider_error: Mapping[str, Any] | None = None,
    aws_credential_status: Mapping[str, object] | None = None,
    provider_max_tokens: int | None = None,
) -> dict[str, Any]:
    source = step1.get("input") if isinstance(step1, Mapping) else {}
    if not isinstance(source, Mapping):
        source = {}
    diagnostic: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "analysis_kind": "greenfield_pr_impact_step_1_5_diagnostic",
        "status": "rejected",
        "stage": stage,
        "reason": reason,
        "contract_path": str(contract_path) if contract_path is not None else None,
        "provider": {
            "name": provider_name,
            "model": provider_model,
            "max_tokens": provider_max_tokens,
        },
        "source": {
            "repository": source.get("repository") or source.get("repo_key"),
            "repo_key": source.get("repo_key") or source.get("source_repo_key"),
            "pr_number": source.get("pr_number"),
            "base_revision": source.get("base_sha") or source.get("base_revision"),
            "target_revision": source.get("target_revision") or source.get("head_sha"),
            "changed_paths": source.get("changed_paths")
            or [row.get("path") for row in step1.get("changed_files", [])],
        },
        "provenance": {
            "read_only": True,
            "catalog_mutation": "none",
            "github_writes": "none",
            "captured_at": datetime.now(UTC).isoformat(),
            "step1_evidence_sha256": evidence_fingerprint(step1),
            "context_sha256": context_sha256,
        },
    }
    if raw_provider_response is not None:
        diagnostic["raw_provider_response"] = raw_provider_response
    if normalized_trace is not None:
        diagnostic["normalized_trace"] = dict(normalized_trace)
    if provider_error is not None:
        diagnostic["provider"]["error"] = dict(provider_error)
    if aws_credential_status is not None:
        diagnostic["aws_credential_status"] = dict(aws_credential_status)
    unsigned = copy.deepcopy(diagnostic)
    diagnostic["diagnostic_sha256"] = artifact_sha256(unsigned)
    return diagnostic
