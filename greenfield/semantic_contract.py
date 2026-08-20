"""Typed, revision-pinned semantic-index contracts for the greenfield layer."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "0.1"
SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class SemanticIndexError(ValueError):
    """Raised when a semantic index is malformed or not revision-pinned."""


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def artifact_sha256(value: object) -> str:
    return sha256_bytes(canonical_json(value).encode("utf-8"))


def node_key(kind: str, identity: str) -> str:
    if not kind or not identity:
        raise SemanticIndexError("semantic node kind and identity are required")
    return f"{kind}:{identity}"


def source_evidence(
    *,
    path: str,
    source_hash: str,
    text: str,
    start_line: int,
    end_line: int | None = None,
    fact: str,
    **details: Any,
) -> dict[str, Any]:
    return {
        "fact": fact,
        "source_path": path,
        "source_hash": source_hash,
        "source_lines": {
            "start": max(1, int(start_line)),
            "end": max(1, int(end_line or start_line)),
        },
        "details": details,
    }


def validate_index(index: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if index.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    repository = index.get("repository")
    if not isinstance(repository, str) or not repository.strip():
        errors.append("repository must be a non-empty string")
    revision = index.get("revision")
    if not isinstance(revision, str) or not SHA.fullmatch(revision):
        errors.append("revision must be a lowercase 40-character SHA")
    for key in ("nodes", "edges", "diagnostics"):
        if not isinstance(index.get(key), list):
            errors.append(f"{key} must be a list")
    provenance = index.get("provenance")
    if not isinstance(provenance, dict) or provenance.get("read_only") is not True:
        errors.append("provenance.read_only must be true")
    if isinstance(provenance, dict):
        digest = provenance.get("index_sha256")
        if not isinstance(digest, str) or not SHA256.fullmatch(digest):
            errors.append("provenance.index_sha256 must be lowercase SHA-256")
        else:
            unsigned = dict(index)
            unsigned.pop("provenance", None)
            if artifact_sha256(unsigned) != digest:
                errors.append("provenance.index_sha256 does not match index contents")

    node_keys: set[str] = set()
    for node in index.get("nodes", []):
        if not isinstance(node, dict):
            errors.append("node must be an object")
            continue
        key = node.get("key")
        if not isinstance(key, str) or not key:
            errors.append("node.key must be non-empty")
        elif key in node_keys:
            errors.append(f"duplicate node: {key}")
        else:
            node_keys.add(key)
        if not isinstance(node.get("kind"), str) or not isinstance(
            node.get("identity"), str
        ):
            errors.append("node kind and identity must be strings")

    edge_keys: set[tuple[Any, ...]] = set()
    for edge in index.get("edges", []):
        if not isinstance(edge, dict):
            errors.append("edge must be an object")
            continue
        if not isinstance(edge.get("source"), str):
            errors.append("edge.source must be a string")
        target = edge.get("target")
        if target is not None and not isinstance(target, str):
            errors.append("edge.target must be a string or null")
        if isinstance(target, str) and target not in node_keys:
            errors.append(f"edge target does not exist: {target}")
        if edge.get("resolution") not in {
            "explicit_source",
            "resolved_exact",
            "framework_convention",
            "candidate_static",
            "ambiguous",
            "dynamic",
            "unresolved",
            "unavailable",
        }:
            errors.append("edge.resolution is invalid")
        evidence = edge.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            errors.append("edge.evidence must be non-empty")
        key = (
            edge.get("source"),
            edge.get("target"),
            edge.get("target_ref"),
            edge.get("kind"),
            edge.get("resolution"),
        )
        if key in edge_keys:
            errors.append(f"duplicate edge: {key}")
        edge_keys.add(key)
    return errors


def finalize_index(
    *,
    repository: str,
    revision: str,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    diagnostics: list[dict[str, Any]],
    extractor_versions: Mapping[str, str],
) -> dict[str, Any]:
    if not SHA.fullmatch(revision):
        raise SemanticIndexError("revision must be a lowercase 40-character SHA")
    body: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "repository": repository,
        "revision": revision,
        "nodes": sorted(nodes, key=lambda item: str(item["key"])),
        "edges": sorted(
            edges,
            key=lambda item: (
                str(item.get("source", "")),
                str(item.get("target", "")),
                str(item.get("kind", "")),
                str(item.get("resolution", "")),
            ),
        ),
        "diagnostics": sorted(
            diagnostics,
            key=lambda item: (
                str(item.get("source_path", "")),
                int(item.get("start_line", 0)),
                str(item.get("code", "")),
            ),
        ),
    }
    body["provenance"] = {
        "extractor_versions": dict(sorted(extractor_versions.items())),
        "read_only": True,
        "catalog_mutation": "none",
        "index_sha256": artifact_sha256(body),
    }
    errors = validate_index(body)
    if errors:
        raise SemanticIndexError("invalid semantic index: " + "; ".join(errors))
    return body


def load_index(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SemanticIndexError(
            f"semantic_index_read_failed: {source}: {exc}"
        ) from exc
    errors = validate_index(value)
    if errors:
        raise SemanticIndexError("invalid semantic index: " + "; ".join(errors))
    return value


def write_index(index: Mapping[str, Any], path: str | Path) -> None:
    errors = validate_index(index)
    if errors:
        raise SemanticIndexError("invalid semantic index: " + "; ".join(errors))
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(index, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
