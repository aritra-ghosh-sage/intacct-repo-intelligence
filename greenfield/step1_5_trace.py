"""Validation and deterministic normalization for the Codex Step 1.5 trace."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
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


class TraceError(ValueError):
    """Raised when the Codex trace cannot safely become evidence."""


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


def validate_trace(step1: Mapping[str, Any], trace: Mapping[str, Any]) -> list[str]:
    """Return stable validation errors for the Codex-generated trace."""

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
            if not isinstance(call, Mapping):
                raise TraceError(f"calls[{index}] must be an object")
            if call.get("relationship_type") not in {"CALLS", "STATIC_CALLS"}:
                raise TraceError(f"calls[{index}].relationship_type is invalid")
            if (
                isinstance(call.get("source_line"), bool)
                or not isinstance(call.get("source_line"), int)
                or call["source_line"] < 1
            ):
                raise TraceError(f"calls[{index}].source_line must be positive")
            if (
                _sha(call.get("source_revision"), f"calls[{index}].source_revision")
                != revision
            ):
                raise TraceError(
                    f"calls[{index}].source_revision does not match Step 1"
                )
            if call.get("resolution") != "exact":
                raise TraceError(f"calls[{index}].resolution must be exact")
        behaviors = trace.get("behaviors")
        if not isinstance(behaviors, list) or not behaviors:
            raise TraceError("behaviors must be a non-empty list")
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
    """Attach trusted Step 1/Codex provenance without trusting model metadata."""

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
    trace["surfaces"] = _validate_surfaces(trace.get("surfaces", {}))
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
