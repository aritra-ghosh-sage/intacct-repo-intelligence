"""Contracts and normalized CI evidence for greenfield PR-impact Step 2."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from greenfield.semantic_contract import load_index

CONTRACT_SCHEMA_VERSION = "0.1"
CI_SCHEMA_VERSION = "0.1"
INVENTORY_SCHEMA_VERSION = "0.1"
SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class EvidenceError(ValueError):
    """Raised when a contract or CI evidence artifact is invalid."""


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvidenceError(f"{label} must be an object")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvidenceError(f"{label} must be a non-empty string")
    return value.strip()


def _sha(value: Any, label: str) -> str:
    result = _text(value, label).lower()
    if not SHA.fullmatch(result):
        raise EvidenceError(f"{label} must be a 40-character lowercase SHA")
    return result


def _sha256(value: Any, label: str) -> str:
    result = _text(value, label).lower()
    if not SHA256.fullmatch(result):
        raise EvidenceError(f"{label} must be a 64-character lowercase SHA-256")
    return result


def _paths(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise EvidenceError(f"{label} must be a non-empty list")
    result = sorted({_text(item, f"{label} item") for item in value})
    if any("*" in path or "?" in path for path in result):
        raise EvidenceError(f"{label} must contain exact paths, not patterns")
    return result


def _optional_symbols(value: Any, label: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise EvidenceError(f"{label} must be a list")
    result = sorted({_text(item, f"{label} item") for item in value})
    if any("*" in symbol or "?" in symbol for symbol in result):
        raise EvidenceError(f"{label} must contain exact symbols, not patterns")
    return result


def _optional_text(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return _text(value, label)


def _validate_test_command(value: Any, label: str, *, strict: bool) -> None:
    if value == "unavailable":
        return
    if isinstance(value, str):
        if strict:
            raise EvidenceError(f"{label} must use structured argv or unavailable")
        if not value.strip():
            raise EvidenceError(f"{label} must be non-empty")
        return
    if not isinstance(value, dict):
        raise EvidenceError(f"{label} must be an object or unavailable")
    argv = value.get("argv")
    if not isinstance(argv, list) or not argv or any(
        not isinstance(item, str) or not item.strip() for item in argv
    ):
        raise EvidenceError(f"{label}.argv must be a non-empty list of strings")
    if not isinstance(value.get("cwd", "."), str) or not value.get("cwd", ".").strip():
        raise EvidenceError(f"{label}.cwd must be a non-empty string")


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def artifact_sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _edge_evidence_sha256(edge: Mapping[str, Any]) -> str:
    fact = {key: value for key, value in edge.items() if key != "evidence_sha256"}
    return artifact_sha256(fact)


def load_contract(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise EvidenceError(f"contract_read_failed: {source}: {exc}") from exc
    data = _object(raw, "contract")
    if data.get("schema_version") != CONTRACT_SCHEMA_VERSION:
        raise EvidenceError(
            f"contract schema_version must be {CONTRACT_SCHEMA_VERSION}"
        )
    repository = _text(data.get("repository"), "contract.repository")
    revision = _sha(data.get("revision"), "contract.revision")
    if data.get("artifact_kind") == "generated_behavior_contract":
        input_data = _object(data.get("input"), "contract.input")
        for field in ("repository", "repo_key", "base_sha", "head_sha"):
            _text(input_data.get(field), f"contract.input.{field}")
        pr_number = input_data.get("pr_number")
        if isinstance(pr_number, bool) or not isinstance(pr_number, int) or pr_number < 1:
            raise EvidenceError("contract.input.pr_number must be positive")
        if input_data["head_sha"] != revision:
            raise EvidenceError("generated contract input.head_sha does not match revision")
        input_paths = _paths(input_data.get("changed_paths"), "contract.input.changed_paths")
        if input_paths != sorted(input_paths):
            raise EvidenceError("generated contract changed paths must be sorted")
        generation = _object(data.get("generation"), "contract.generation")
        for field in ("generator_version", "rule_set_version", "step1_evidence_sha256", "source_trace_sha256"):
            _text(generation.get(field), f"contract.generation.{field}")
        _sha256(generation.get("step1_evidence_sha256"), "contract.generation.step1_evidence_sha256")
        _sha256(generation.get("source_trace_sha256"), "contract.generation.source_trace_sha256")
        bounds = _object(generation.get("bounds"), "contract.generation.bounds")
        for field in ("max_hops", "max_nodes", "max_edges"):
            value = bounds.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise EvidenceError(f"contract.generation.bounds.{field} must be non-negative")
        if generation.get("status") not in {"complete", "partial"}:
            raise EvidenceError("contract.generation.status is invalid")
        if not isinstance(generation.get("diagnostics"), list):
            raise EvidenceError("contract.generation.diagnostics must be a list")
        if not isinstance(generation.get("nodes"), list) or not isinstance(generation.get("edges"), list):
            raise EvidenceError("contract.generation nodes and edges must be lists")
        flow = _object(generation.get("flow"), "contract.generation.flow")
        if not isinstance(flow.get("edges"), list):
            raise EvidenceError("contract.generation.flow.edges must be a list")
        for edge in [*generation["edges"], *flow["edges"]]:
            if not isinstance(edge, dict) or edge.get("source_revision") != revision:
                raise EvidenceError("generated contract edge source revision is stale")
            if edge.get("relationship_type") not in {"CALLS", "STATIC_CALLS"}:
                raise EvidenceError("generated contract edge relationship type is invalid")
            edge_hash = _sha256(edge.get("evidence_sha256"), "edge evidence_sha256")
            if edge_hash != _edge_evidence_sha256(edge):
                raise EvidenceError("generated contract edge evidence hash does not match fact")
        stored_digest = data.get("evidence", {}).get("sha256") if isinstance(data.get("evidence"), dict) else None
        unsigned = {key: value for key, value in data.items() if key != "evidence"}
        if stored_digest != artifact_sha256(unsigned):
            raise EvidenceError("generated contract evidence.sha256 does not match contents")
    relations = data.get("relations")
    if not isinstance(relations, list) or not relations:
        raise EvidenceError("contract.relations must be a non-empty list")

    normalized: list[dict[str, Any]] = []
    keys: set[tuple[str, str, str]] = set()
    for index, relation in enumerate(relations):
        item = _object(relation, f"contract.relations[{index}]")
        interface_id = _text(item.get("interface_id"), "contract.interface_id")
        owner = _text(item.get("owner_repository", repository), "owner_repository")
        consumer = _text(item.get("consumer_repository"), "consumer_repository")
        relationship_type = _text(item.get("relationship_type"), "relationship_type")
        status = _text(item.get("status"), "status")
        if status not in {"active", "inactive"}:
            raise EvidenceError("contract relation status must be active or inactive")
        source_paths = _paths(item.get("source_paths"), "source_paths")
        source_symbols = _optional_symbols(
            item.get("source_symbols"), "source_symbols"
        )
        protected_behavior = _optional_text(
            item.get("protected_behavior"), "protected_behavior"
        )
        entry_surfaces = _optional_symbols(
            item.get("entry_surfaces"), "entry_surfaces"
        )
        if relationship_type == "behavior_contract" and not protected_behavior:
            raise EvidenceError(
                "behavior_contract relations require protected_behavior"
            )
        test_obligations = item.get("test_obligations", [])
        if not isinstance(test_obligations, list):
            raise EvidenceError("test_obligations must be a list")
        normalized_obligations: list[dict[str, Any]] = []
        for obligation_index, obligation in enumerate(test_obligations):
            if not isinstance(obligation, dict):
                raise EvidenceError(
                    f"test_obligations[{obligation_index}] must be an object"
                )
            obligation_id = _text(
                obligation.get("id"),
                f"test_obligations[{obligation_index}].id",
            )
            obligation_path = _text(
                obligation.get("path"),
                f"test_obligations[{obligation_index}].path",
            )
            required_change = obligation.get("required_change")
            if required_change is not None:
                required_change = _text(
                    required_change,
                    f"test_obligations[{obligation_index}].required_change",
                )
                if required_change not in {
                    "fixture",
                    "assertion",
                    "schema",
                    "setup",
                    "integration",
                }:
                    raise EvidenceError(
                        "test_obligations.required_change is invalid"
                    )
            normalized_obligations.append(
                {
                    "id": obligation_id,
                    "path": obligation_path,
                    "required_change": required_change,
                    "behavior_id": obligation.get("behavior_id"),
                    "test_owner": obligation.get("test_owner"),
                    "test_command": obligation.get("test_command"),
                }
            )
        normalized_obligations.sort(
            key=lambda value: (value["id"], value["path"])
        )
        key = (interface_id, consumer, relationship_type)
        if key in keys:
            raise EvidenceError(f"duplicate contract relation: {key}")
        keys.add(key)
        normalized.append(
            {
                "interface_id": interface_id,
                "owner_repository": owner,
                "consumer_repository": consumer,
                "relationship_type": relationship_type,
                "source_paths": source_paths,
                "source_symbols": source_symbols,
                "protected_behavior": protected_behavior,
                "entry_surfaces": entry_surfaces,
                "status": status,
                "owner": item.get("owner"),
                "test_obligations": normalized_obligations,
            }
        )
    normalized.sort(
        key=lambda item: (
            item["interface_id"],
            item["consumer_repository"],
            item["relationship_type"],
        )
    )
    raw_bytes = source.read_bytes()
    return {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "repository": repository,
        "revision": revision,
        "relations": normalized,
        "evidence": {
            "path": source.as_posix(),
            "sha256": hashlib.sha256(raw_bytes).hexdigest(),
        },
    }


def load_ci_evidence(path: str | Path, *, strict: bool = False) -> dict[str, Any]:
    source = Path(path)
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"ci_evidence_read_failed: {source}: {exc}") from exc
    data = _object(raw, "ci evidence")
    if data.get("schema_version") != CI_SCHEMA_VERSION:
        raise EvidenceError(f"ci evidence schema_version must be {CI_SCHEMA_VERSION}")
    raw_tests = data.get("tests", [])
    if not isinstance(raw_tests, list):
        raise EvidenceError("ci evidence tests must be a list")
    tests: list[Any] = []
    for test in raw_tests:
        if not isinstance(test, dict):
            tests.append(test)
            continue
        normalized_test = dict(test)
        if "test_command" in test:
            _validate_test_command(test["test_command"], "ci test_command", strict=strict)
        for field in (
            "test_owner",
            "test_command",
            "execution_result",
            "workflow_run_id",
            "workflow_job_id",
            "check_run_id",
            "artifact_id",
            "coverage",
            "relationship",
            "required_change",
            "behavior_id",
        ):
            if field in test:
                normalized_test[field] = test[field]
        tests.append(normalized_test)
    normalized = {
        "schema_version": CI_SCHEMA_VERSION,
        "evidence_id": _text(data.get("evidence_id"), "evidence_id"),
        "repository": _text(data.get("repository"), "repository"),
        "commit_sha": _sha(data.get("commit_sha"), "commit_sha"),
        "source_repository": _text(data.get("source_repository"), "source_repository"),
        "source_revision": _sha(data.get("source_revision"), "source_revision"),
        "interface_id": _text(data.get("interface_id"), "interface_id"),
        "status": _text(data.get("status"), "status"),
        "tests": tests,
        "evidence": {
            "path": source.as_posix(),
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        },
    }
    if normalized["status"] not in {"available", "empty", "unavailable", "stale"}:
        raise EvidenceError("ci evidence status is invalid")
    if strict and normalized["status"] == "available":
        if not tests:
            raise EvidenceError("strict CI evidence requires tests")
        if not isinstance(data.get("workflow_run_id"), int):
            raise EvidenceError("strict CI evidence requires workflow_run_id")
        if not isinstance(data.get("workflow_job_id"), int):
            raise EvidenceError("strict CI evidence requires workflow_job_id")
        for index, test in enumerate(tests):
            if not isinstance(test, dict):
                raise EvidenceError(f"strict CI test {index} must be an object")
            if not isinstance(test.get("id"), str) or not test["id"].strip():
                raise EvidenceError(f"strict CI test {index} requires id")
            if not isinstance(test.get("path"), str) or not test["path"].strip():
                raise EvidenceError(f"strict CI test {index} requires path")
            if "execution_result" not in test:
                raise EvidenceError(f"strict CI test {index} requires execution_result")
            if "test_command" not in test:
                raise EvidenceError(f"strict CI test {index} requires test_command or unavailable")
    for field in (
        "source_pr_number",
        "inspected_revision",
        "workflow_run_id",
        "workflow_job_id",
        "check_run_id",
        "artifact_id",
        "execution_result",
        "test_command",
        "test_owner",
    ):
        if field in data:
            normalized[field] = data[field]
    if "source_pr_number" in normalized and (
        isinstance(normalized["source_pr_number"], bool)
        or not isinstance(normalized["source_pr_number"], int)
        or normalized["source_pr_number"] < 1
    ):
        raise EvidenceError("source_pr_number must be a positive integer")
    if "inspected_revision" in normalized:
        normalized["inspected_revision"] = _sha(
            normalized["inspected_revision"], "inspected_revision"
        )
        if normalized["inspected_revision"] != normalized["commit_sha"]:
            raise EvidenceError("inspected_revision must equal commit_sha")
    return normalized


def ci_execution_binding_status(
    evidence: Mapping[str, Any],
    *,
    source_revision: str,
    source_pr_number: int | None = None,
) -> str:
    """Return the deterministic coverage binding state for CI evidence."""

    if evidence.get("source_revision") != source_revision:
        return "stale"
    if not isinstance(evidence.get("inspected_revision"), str):
        return "unavailable"
    if evidence.get("inspected_revision") != evidence.get("commit_sha"):
        return "unavailable"
    if source_pr_number is None or evidence.get("source_pr_number") != source_pr_number:
        return "unavailable"
    if isinstance(evidence.get("workflow_run_id"), bool) or not isinstance(evidence.get("workflow_run_id"), int) or evidence.get("workflow_run_id") < 1:
        return "unavailable"
    if isinstance(evidence.get("workflow_job_id"), bool) or not isinstance(evidence.get("workflow_job_id"), int) or evidence.get("workflow_job_id") < 1:
        return "unavailable"
    provenance = evidence.get("evidence")
    if not isinstance(provenance, Mapping) or not isinstance(provenance.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", provenance["sha256"]):
        return "unavailable"
    tests = evidence.get("tests")
    if not isinstance(tests, list) or not tests:
        return "unavailable"
    for test in tests:
        if not isinstance(test, Mapping):
            return "unavailable"
        if not isinstance(test.get("id"), str) or not test["id"].strip():
            return "unavailable"
        if not isinstance(test.get("path"), str) or not test["path"].strip():
            return "unavailable"
        if not isinstance(test.get("execution_result"), str) or not test["execution_result"].strip() or test["execution_result"] in {"not_run", "unavailable"}:
            return "unavailable"
    return "bound"


def normalize_repository_inventory(
    value: Any, *, path: str = "<in-memory>"
) -> dict[str, Any]:
    """Validate captured read-only repository/workflow inventory evidence."""

    data = _object(value, "repository inventory")
    if data.get("schema_version") != INVENTORY_SCHEMA_VERSION:
        raise EvidenceError(
            f"repository inventory schema_version must be {INVENTORY_SCHEMA_VERSION}"
        )
    if data.get("evidence_type") != "repository_inventory":
        raise EvidenceError("repository inventory evidence_type is invalid")
    _text(data.get("repository"), "repository inventory.repository")
    _text(data.get("source_repository"), "repository inventory.source_repository")
    _sha(data.get("source_revision"), "repository inventory.source_revision")
    _sha(data.get("inspected_revision"), "repository inventory.inspected_revision")
    status = _text(data.get("status"), "repository inventory.status")
    if status not in {"available", "unavailable", "empty"}:
        raise EvidenceError("repository inventory status is invalid")
    for key in (
        "workflow_paths",
        "inventory_paths",
        "workflows",
        "workflow_runs",
        "check_runs",
        "artifacts",
        "gaps",
    ):
        if not isinstance(data.get(key), list):
            raise EvidenceError(f"repository inventory.{key} must be a list")
    artifact_status = _text(
        data.get("artifact_status"), "repository inventory.artifact_status"
    )
    if artifact_status not in {"available", "empty", "not_linked_to_source_revision"}:
        raise EvidenceError("repository inventory artifact_status is invalid")
    linkage = data.get("ci_linkage")
    if linkage is None:
        linkage = {
            "status": "unavailable",
            "reason": "target_repository_has_no_source_revision",
            "source_repository": data["source_repository"],
            "source_revision": data["source_revision"],
        }
    linkage = _object(linkage, "repository inventory.ci_linkage")
    linkage_status = _text(
        linkage.get("status"), "repository inventory.ci_linkage.status"
    )
    if linkage_status not in {"available", "unavailable"}:
        raise EvidenceError("repository inventory ci_linkage status is invalid")
    linkage_repository = _text(
        linkage.get("source_repository"),
        "repository inventory.ci_linkage.source_repository",
    )
    linkage_revision = _sha(
        linkage.get("source_revision"),
        "repository inventory.ci_linkage.source_revision",
    )
    if linkage_repository != data["source_repository"]:
        raise EvidenceError(
            "repository inventory ci_linkage source repository mismatches input"
        )
    if linkage_revision != data["source_revision"]:
        raise EvidenceError(
            "repository inventory ci_linkage source revision mismatches input"
        )
    if linkage_status == "unavailable" and not _text(
        linkage.get("reason"), "repository inventory.ci_linkage.reason"
    ):
        raise EvidenceError("repository inventory ci_linkage reason is required")
    if artifact_status == "available" and linkage_status != "available":
        raise EvidenceError(
            "repository inventory artifact cannot be available without ci linkage"
        )
    provenance = _object(data.get("provenance"), "repository inventory.provenance")
    if provenance.get("read_only") is not True:
        raise EvidenceError("repository inventory provenance.read_only must be true")
    response_sha = _text(provenance.get("response_sha256"), "response_sha256")
    if len(response_sha) != 64 or any(
        char not in "0123456789abcdef" for char in response_sha
    ):
        raise EvidenceError(
            "repository inventory response_sha256 must be lowercase SHA-256"
        )
    normalized = dict(data)
    normalized["ci_linkage"] = dict(linkage)
    normalized["evidence_path"] = path
    return normalized


def load_repository_inventory(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceError(
            f"repository_inventory_read_failed: {source}: {exc}"
        ) from exc
    return normalize_repository_inventory(value, path=source.as_posix())


def load_semantic_index(path: str | Path) -> dict[str, Any]:
    """Load a revision-pinned semantic sidecar for Step 2 candidate evidence."""

    try:
        value = load_index(path)
    except ValueError as exc:
        raise EvidenceError(str(exc)) from exc
    normalized = dict(value)
    normalized["evidence_path"] = Path(path).as_posix()
    return normalized
