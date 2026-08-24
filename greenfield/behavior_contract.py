"""Generate small, revision-pinned behavioral contracts from Greenfield evidence."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from greenfield.source_identity import source_identity
from greenfield.step1_capture import evidence_fingerprint
from greenfield.step2_contract import artifact_sha256

SCHEMA_VERSION = "0.1"
GENERATOR_VERSION = "0.1"
RULE_SET_VERSION = "0.1"
SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
MAX_HOPS = 3
MAX_NODES = 500
MAX_EDGES = 2000


class BehaviorContractError(ValueError):
    """Raised when source trace evidence cannot safely form a contract."""


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BehaviorContractError(f"{label} must be a non-empty string")
    return value.strip()


def _sha(value: Any, label: str) -> str:
    result = _text(value, label).lower()
    if not SHA.fullmatch(result):
        raise BehaviorContractError(f"{label} must be a lowercase 40-character SHA")
    return result


def _sha256(value: Any, label: str) -> str:
    result = _text(value, label).lower()
    if not SHA256.fullmatch(result):
        raise BehaviorContractError(f"{label} must be a lowercase 64-character SHA")
    return result


def _paths(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise BehaviorContractError(f"{label} must be a non-empty list")
    result = sorted({_text(item, f"{label} item") for item in value})
    if any("*" in item or "?" in item for item in result):
        raise BehaviorContractError(f"{label} must contain exact paths")
    return result


def _symbols(value: Any, label: str) -> list[str]:
    if not isinstance(value, list):
        raise BehaviorContractError(f"{label} must be a list")
    return sorted({_text(item, f"{label} item") for item in value})


def _read_object(path: str | Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BehaviorContractError(f"{label}_read_failed: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise BehaviorContractError(f"{label} must be an object")
    return value


def _stable_behavior_id(repository: str, behavior: Mapping[str, Any]) -> str:
    identity = {
        "repository": repository,
        "kind": behavior.get("kind", "behavior"),
        "entry_symbols": sorted(behavior["entry_symbols"]),
        "source_paths": sorted(behavior["source_paths"]),
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]
    return f"behavior:{digest}"


def _validate_step1(step1: Mapping[str, Any]) -> tuple[str, str, list[str]]:
    source = step1.get("input")
    if not isinstance(source, Mapping):
        raise BehaviorContractError("Step 1 input must be an object")
    try:
        canonical_repository, _repo_key = source_identity(source)
    except ValueError as exc:
        raise BehaviorContractError(str(exc)) from exc
    revision = _sha(
        source.get("target_revision") or source.get("head_sha"),
        "Step 1 target revision",
    )
    paths = source.get("changed_paths")
    if paths is None:
        paths = [row.get("path") for row in step1.get("changed_files", [])]
    paths = _paths(paths, "Step 1 changed paths")
    return canonical_repository, revision, paths


def _validate_trace(
    trace: Mapping[str, Any], repository: str, revision: str, changed_paths: list[str]
) -> list[dict[str, Any]]:
    if trace.get("schema_version") != SCHEMA_VERSION:
        raise BehaviorContractError("source trace schema_version is invalid")
    if trace.get("repository") not in {repository, "ia-main", "ia-app"}:
        raise BehaviorContractError("source trace repository does not match Step 1")
    if trace.get("revision") != revision:
        raise BehaviorContractError("source trace revision does not match Step 1")
    trace_paths = _paths(trace.get("changed_paths"), "source trace changed paths")
    if trace_paths != changed_paths:
        raise BehaviorContractError("source trace changed paths do not match Step 1")
    behaviors = trace.get("behaviors")
    if not isinstance(behaviors, list) or not behaviors:
        raise BehaviorContractError("source trace behaviors must be non-empty")
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(behaviors):
        if not isinstance(raw, Mapping):
            raise BehaviorContractError(
                f"source trace behavior {index} must be an object"
            )
        paths = _paths(raw.get("source_paths"), f"behavior {index}.source_paths")
        if not set(paths).issubset(changed_paths):
            raise BehaviorContractError(f"behavior {index} contains an unchanged path")
        entry_symbols = _symbols(
            raw.get("entry_symbols"), f"behavior {index}.entry_symbols"
        )
        if not entry_symbols:
            raise BehaviorContractError(
                f"behavior {index}.entry_symbols must be non-empty"
            )
        edges = raw.get("edges", [])
        if not isinstance(edges, list):
            raise BehaviorContractError(f"behavior {index}.edges must be a list")
        normalized_edges: list[dict[str, Any]] = []
        for edge in edges:
            if not isinstance(edge, Mapping):
                raise BehaviorContractError("source trace edges must be objects")
            relation = _text(edge.get("relationship_type"), "edge relationship_type")
            if relation not in {"CALLS", "STATIC_CALLS"}:
                raise BehaviorContractError(
                    "source trace edge relationship_type is invalid"
                )
            normalized_edges.append(
                {
                    "source_symbol": _text(
                        edge.get("source_symbol"), "edge source_symbol"
                    ),
                    "target_symbol": _text(
                        edge.get("target_symbol"), "edge target_symbol"
                    ),
                    "relationship_type": relation,
                    "source_path": _text(edge.get("source_path"), "edge source_path"),
                    "source_line": int(edge.get("source_line", 1)),
                    "resolution": _text(
                        edge.get("resolution", "exact"), "edge resolution"
                    ),
                }
            )
        normalized.append(
            {
                "kind": _text(raw.get("kind", "behavior"), "behavior kind"),
                "entry_symbols": entry_symbols,
                "source_paths": paths,
                "edges": sorted(
                    normalized_edges,
                    key=lambda item: (
                        item["source_symbol"],
                        item["target_symbol"],
                        item["relationship_type"],
                    ),
                ),
                "description": raw.get("description"),
                "surfaces": raw.get("surfaces", {}),
            }
        )
    return normalized


def generate_behavior_contract(
    step1: Mapping[str, Any],
    source_trace: Mapping[str, Any],
    *,
    source_trace_path: str = "<in-memory>",
    max_hops: int = MAX_HOPS,
    max_nodes: int = MAX_NODES,
    max_edges: int = MAX_EDGES,
) -> dict[str, Any]:
    """Generate the existing Step 2 contract shape from exact trace evidence."""

    repository, revision, changed_paths = _validate_step1(step1)
    behaviors = _validate_trace(source_trace, repository, revision, changed_paths)
    diagnostics: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    surfaces: dict[str, dict[str, Any]] = {}
    for behavior in behaviors:
        behavior_id = _stable_behavior_id(repository, behavior)
        for symbol in behavior["entry_symbols"]:
            nodes.append(
                {
                    "symbol": symbol,
                    "role": "entry",
                    "source_paths": behavior["source_paths"],
                }
            )
        edges.extend(behavior["edges"])
        if len(nodes) > max_nodes:
            diagnostics.append({"code": "node_budget_exceeded", "status": "unresolved"})
            nodes = nodes[:max_nodes]
        if len(edges) > max_edges:
            diagnostics.append({"code": "edge_budget_exceeded", "status": "unresolved"})
            edges = edges[:max_edges]
        for name, status in sorted((behavior.get("surfaces") or {}).items()):
            if isinstance(status, str):
                surfaces[name] = {"status": status, "evidence": "source_trace"}
        description = behavior.get("description")
        if not isinstance(description, str) or not description.strip():
            description = "Source behavior rooted at " + ", ".join(
                behavior["entry_symbols"]
            )
        relations.append(
            {
                "interface_id": behavior_id,
                "owner_repository": "ia-main"
                if repository == "intacct/ia-app"
                else repository,
                "consumer_repository": "ia-app"
                if repository == "intacct/ia-app"
                else repository,
                "relationship_type": "behavior_contract",
                "source_paths": behavior["source_paths"],
                "source_symbols": behavior["entry_symbols"],
                "protected_behavior": description.strip(),
                "entry_surfaces": sorted(surfaces),
                "status": "active",
                "owner": None,
                "test_obligations": [],
            }
        )
    body = {
        "schema_version": "0.1",
        "artifact_kind": "generated_behavior_contract",
        "repository": "ia-main" if repository == "intacct/ia-app" else repository,
        "revision": revision,
        "relations": relations,
        "generation": {
            "generator_version": GENERATOR_VERSION,
            "rule_set_version": RULE_SET_VERSION,
            "step1_evidence_sha256": evidence_fingerprint(step1),
            "source_trace_sha256": artifact_sha256(source_trace),
            "source_trace_path": source_trace_path,
            "bounds": {
                "max_hops": max_hops,
                "max_nodes": max_nodes,
                "max_edges": max_edges,
            },
            "status": "partial" if diagnostics else "complete",
            "diagnostics": diagnostics,
            "nodes": sorted(nodes, key=lambda item: item["symbol"]),
            "edges": sorted(
                edges, key=lambda item: (item["source_symbol"], item["target_symbol"])
            ),
            "surfaces": surfaces,
        },
        "evidence": {"path": "<generated>", "sha256": "0" * 64},
    }
    body["evidence"]["sha256"] = artifact_sha256(
        {key: value for key, value in body.items() if key != "evidence"}
    )
    return body


def load_source_trace(path: str | Path) -> dict[str, Any]:
    return _read_object(path, "source_trace")


def write_behavior_contract(contract: Mapping[str, Any], path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(contract, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
