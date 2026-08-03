"""Deterministic semantic quality contract for catalog refresh candidates."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

QUALITY_BASELINE_SCHEMA = "catalog-quality-baseline"
QUALITY_BASELINE_VERSION = 1
QUALITY_RUN_SCHEMA = "catalog-quality-run"
QUALITY_RUN_VERSION = 1
_DIAGNOSTIC_FIELDS = {
    "builder",
    "code",
    "severity",
    "source_path",
    "source_blob_sha",
    "identity",
    "diagnostic_key",
}


class RefreshQualityError(RuntimeError):
    """A candidate violates or cannot prove the semantic quality contract."""


QUALITY_QUERIES: dict[str, str] = {
    "entity_occurrences": "SELECT COUNT(*) FROM entity_occurrences WHERE repo_id=:repo_id",
    "entity_roots": "SELECT COUNT(*) FROM entity_roots WHERE repo_id=:repo_id",
    "openapispec_index": "SELECT COUNT(*) FROM openapispec_index WHERE repo_id=:repo_id",
    "openapi_entity_mappings": "SELECT COUNT(*) FROM entity_mappings WHERE repo_id=:repo_id AND mapping_type LIKE 'openapispec_%'",
    "rest_endpoints": "SELECT COUNT(*) FROM rest_endpoints WHERE repo_id=:repo_id",
    "entity_access_links": "SELECT COUNT(*) FROM entity_access_links WHERE repo_id=:repo_id",
    "workflows": "SELECT COUNT(*) FROM workflows WHERE repo_id=:repo_id",
    "workflow_nodes": "SELECT COUNT(*) FROM workflow_nodes wn JOIN workflows w ON w.id=wn.workflow_id WHERE w.repo_id=:repo_id",
    "workflow_edges": "SELECT COUNT(*) FROM workflow_edges we JOIN workflows w ON w.id=we.workflow_id WHERE w.repo_id=:repo_id",
    "openapi_file_ref_edges": "SELECT COUNT(*) FROM openapi_file_ref_edges WHERE repo_id=:repo_id",
    "security_operations": "SELECT COUNT(*) FROM security_operations WHERE repo_id=:repo_id",
    "security_operation_allowops": "SELECT COUNT(*) FROM security_operation_allowops soa JOIN security_operations so ON so.id=soa.operation_id WHERE so.repo_id=:repo_id",
    "security_policies": "SELECT COUNT(*) FROM security_policies WHERE repo_id=:repo_id",
    "security_policy_values": "SELECT COUNT(*) FROM security_policy_values spv JOIN security_policies sp ON sp.id=spv.policy_id WHERE sp.repo_id=:repo_id",
    "security_policy_eops": "SELECT COUNT(*) FROM security_policy_eops spe JOIN security_policy_values spv ON spv.id=spe.policy_value_id JOIN security_policies sp ON sp.id=spv.policy_id WHERE sp.repo_id=:repo_id",
    "security_menus": "SELECT COUNT(*) FROM security_menus WHERE repo_id=:repo_id",
    "security_menu_items": "SELECT COUNT(*) FROM security_menu_items smi JOIN security_menus sm ON sm.id=smi.menu_id WHERE sm.repo_id=:repo_id",
    "security_menu_op_links": "SELECT COUNT(*) FROM security_menu_op_links sml JOIN security_menu_items smi ON smi.id=sml.menu_item_id JOIN security_menus sm ON sm.id=smi.menu_id WHERE sm.repo_id=:repo_id",
    "dbschema_tables": "SELECT COUNT(*) FROM dbschema_tables WHERE repo_id=:repo_id",
    "dbschema_fields": "SELECT COUNT(*) FROM dbschema_fields df JOIN dbschema_tables dt ON dt.id=df.dbschema_table_id WHERE dt.repo_id=:repo_id",
    "entity_schema_components": "SELECT COUNT(*) FROM entity_schema_components WHERE repo_id=:repo_id",
    "entity_relationship_facts": "SELECT COUNT(*) FROM entity_relationship_facts WHERE repo_id=:repo_id",
    "entity_operation_facts": "SELECT COUNT(*) FROM entity_operation_facts WHERE repo_id=:repo_id",
    "entity_extraction_coverage": "SELECT COUNT(*) FROM entity_extraction_coverage WHERE repo_id=:repo_id",
    "entity_semantic_conflicts": "SELECT COUNT(*) FROM entity_semantic_conflicts WHERE repo_id=:repo_id",
    "test_cases": "SELECT COUNT(*) FROM test_cases WHERE repo_id=:repo_id",
    "test_requests": "SELECT COUNT(*) FROM test_requests tr JOIN test_cases tc ON tc.id=tr.test_case_id WHERE tc.repo_id=:repo_id",
    "test_endpoint_links": "SELECT COUNT(*) FROM test_endpoint_links tel JOIN test_requests tr ON tr.id=tel.test_request_id JOIN test_cases tc ON tc.id=tr.test_case_id WHERE tc.repo_id=:repo_id",
    "test_entity_links": "SELECT COUNT(*) FROM test_entity_links ten JOIN test_requests tr ON tr.id=ten.test_request_id JOIN test_cases tc ON tc.id=tr.test_case_id WHERE tc.repo_id=:repo_id",
}

BUILDER_METRICS: dict[str, frozenset[str]] = {
    "entities": frozenset({"entity_occurrences"}),
    "entity_roots": frozenset({"entity_roots"}),
    "openapi_scan": frozenset({"openapispec_index"}),
    "openapi_link": frozenset({"openapi_entity_mappings"}),
    "rest_endpoints": frozenset({"rest_endpoints"}),
    "entity_access_links": frozenset({"entity_access_links"}),
    "workflows": frozenset(
        {"workflows", "workflow_nodes", "workflow_edges", "openapi_file_ref_edges"}
    ),
    "security": frozenset(
        {
            "security_operations",
            "security_operation_allowops",
            "security_policies",
            "security_policy_values",
            "security_policy_eops",
            "security_menus",
            "security_menu_items",
            "security_menu_op_links",
            "dbschema_tables",
            "dbschema_fields",
        }
    ),
    "entity_semantics": frozenset(
        {
            "entity_schema_components",
            "entity_relationship_facts",
            "entity_operation_facts",
            "entity_extraction_coverage",
            "entity_semantic_conflicts",
        }
    ),
    "gherkin_coverage": frozenset(
        {"test_cases", "test_requests", "test_endpoint_links", "test_entity_links"}
    ),
}

NONDECREASING_METRICS = frozenset(
    {"test_endpoint_links", "test_entity_links", "entity_access_links"}
)


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def approval_sha256(payload: Mapping[str, object]) -> str:
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def collect_repository_counts(conn, repo_id: int) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name, sql in QUALITY_QUERIES.items():
        value = conn.execute(sql, {"repo_id": repo_id}).fetchone()[0]
        count = int(value)
        if count < 0:
            raise RefreshQualityError(f"negative quality count for {name}")
        counts[name] = count
    return counts


def collect_global_counts(conn) -> dict[str, int]:
    value = int(
        conn.execute(
            "SELECT COUNT(*) FROM api_version_compatibility WHERE status='active'"
        ).fetchone()[0]
    )
    if value < 0:
        raise RefreshQualityError("negative active API-version compatibility count")
    return {"active_api_version_compatibility": value}


def evaluated_metrics(ran_builders: Iterable[str]) -> tuple[str, ...]:
    metrics: set[str] = set()
    for builder in ran_builders:
        metrics.update(BUILDER_METRICS.get(builder, ()))
    return tuple(sorted(metrics))


def compare_repository_quality(
    *,
    parent_counts: Mapping[str, int],
    candidate_counts: Mapping[str, int],
    ran_builders: Iterable[str],
    parent_diagnostic_keys: Iterable[str] = (),
    candidate_diagnostics: Sequence[Mapping[str, object]] = (),
    changed_paths: Iterable[str] = (),
) -> tuple[str, ...]:
    """Return stable rejection reasons; an empty tuple approves the candidate."""

    failures: list[str] = []
    for metric in evaluated_metrics(ran_builders):
        if metric not in parent_counts or metric not in candidate_counts:
            raise RefreshQualityError(f"quality metric missing: {metric}")
        parent = parent_counts[metric]
        candidate = candidate_counts[metric]
        if isinstance(parent, bool) or isinstance(candidate, bool):
            raise RefreshQualityError(f"quality metric has wrong type: {metric}")
        if parent < 0 or candidate < 0:
            raise RefreshQualityError(f"quality metric is negative: {metric}")
        if parent > 0 and candidate == 0:
            failures.append(f"{metric}: parent={parent} candidate=0")
        elif metric in NONDECREASING_METRICS and candidate < parent:
            failures.append(f"{metric}: parent={parent} candidate={candidate}")

    parent_keys = set(parent_diagnostic_keys)
    changed = set(changed_paths)
    seen: set[str] = set()
    for diagnostic in candidate_diagnostics:
        key = diagnostic.get("diagnostic_key")
        if not isinstance(key, str) or len(key) != 64:
            raise RefreshQualityError("candidate diagnostic has invalid key")
        if key in seen:
            raise RefreshQualityError(f"duplicate candidate diagnostic key: {key}")
        seen.add(key)
        if key not in parent_keys:
            failures.append(f"new diagnostic: {key}")
        if (
            diagnostic.get("severity") == "error"
            and diagnostic.get("source_path") in changed
        ):
            failures.append(f"changed-path error diagnostic: {key}")
    return tuple(sorted(set(failures)))


def build_quality_payload(
    *,
    parent: Mapping[str, object],
    delta_contract_version: int,
    runtime_fingerprint: str,
    repositories: Sequence[Mapping[str, object]],
    global_counts: Mapping[str, int],
) -> dict[str, object]:
    normalized_repositories = sorted(
        (dict(repository) for repository in repositories),
        key=lambda repository: str(repository["repo_key"]),
    )
    for repository in normalized_repositories:
        diagnostics = list(repository.get("diagnostics", []))
        diagnostics.sort(key=lambda item: str(item["diagnostic_key"]))
        repository["diagnostics"] = diagnostics
        repository["counts"] = dict(sorted(dict(repository.get("counts", {})).items()))
    payload: dict[str, object] = {
        "schema": QUALITY_BASELINE_SCHEMA,
        "version": QUALITY_BASELINE_VERSION,
        "parent": dict(parent),
        "contract": {
            "delta_contract_version": delta_contract_version,
            "runtime_fingerprint": runtime_fingerprint,
        },
        "repositories": normalized_repositories,
        "global_counts": dict(sorted(global_counts.items())),
    }
    validate_quality_payload(payload)
    return payload


def quality_report(payload: Mapping[str, object]) -> dict[str, object]:
    validate_quality_payload(payload)
    return {"approval_sha256": approval_sha256(payload), "payload": dict(payload)}


def _require_exact_keys(
    value: Mapping[str, object], expected: set[str], context: str
) -> None:
    actual = set(value)
    if actual != expected:
        raise RefreshQualityError(
            f"{context} fields mismatch: missing={sorted(expected - actual)} unknown={sorted(actual - expected)}"
        )


def _require_hash(value: object, context: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise RefreshQualityError(f"{context} must be a lowercase SHA-256")
    return value


def _validate_counts(counts: object, context: str) -> None:
    if not isinstance(counts, dict):
        raise RefreshQualityError(f"{context} must be an object")
    for name, value in counts.items():
        if (
            not isinstance(name, str)
            or isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
        ):
            raise RefreshQualityError(f"{context} contains an invalid count")


def _validate_diagnostics(diagnostics: object, context: str) -> None:
    if not isinstance(diagnostics, list):
        raise RefreshQualityError(f"{context} must be an array")
    keys: list[str] = []
    for diagnostic in diagnostics:
        if not isinstance(diagnostic, dict):
            raise RefreshQualityError(f"{context} entry must be an object")
        _require_exact_keys(diagnostic, _DIAGNOSTIC_FIELDS, f"{context} entry")
        key = _require_hash(diagnostic["diagnostic_key"], "diagnostic key")
        expected_key = hashlib.sha256(
            canonical_json(
                {
                    name: value
                    for name, value in diagnostic.items()
                    if name != "diagnostic_key"
                }
            )
        ).hexdigest()
        if key != expected_key:
            raise RefreshQualityError("diagnostic key does not match its payload")
        keys.append(key)
        if not isinstance(diagnostic["builder"], str) or not diagnostic["builder"]:
            raise RefreshQualityError("diagnostic builder must be non-empty")
        if not isinstance(diagnostic["code"], str) or not diagnostic["code"]:
            raise RefreshQualityError("diagnostic code must be non-empty")
        if diagnostic["severity"] not in {"error", "warning"}:
            raise RefreshQualityError("diagnostic severity is invalid")
        source_path = diagnostic["source_path"]
        if source_path is not None and (
            not isinstance(source_path, str)
            or not source_path
            or source_path == "<unknown>"
            or source_path.startswith("/")
        ):
            raise RefreshQualityError("diagnostic source path is invalid")
        source_blob_sha = diagnostic["source_blob_sha"]
        if source_blob_sha is not None and (
            not isinstance(source_blob_sha, str)
            or len(source_blob_sha) not in {40, 64}
            or any(character not in "0123456789abcdef" for character in source_blob_sha)
        ):
            raise RefreshQualityError("diagnostic source blob SHA is invalid")
        identity = diagnostic["identity"]
        if not isinstance(identity, dict) or any(
            not isinstance(name, str) or not name or not isinstance(value, str)
            for name, value in identity.items()
        ):
            raise RefreshQualityError("diagnostic identity is invalid")
    if keys != sorted(keys) or len(set(keys)) != len(keys):
        raise RefreshQualityError(f"{context} must be unique and sorted")


def validate_quality_payload(payload: Mapping[str, object]) -> None:
    if not isinstance(payload, dict):
        raise RefreshQualityError("quality payload must be an object")
    _require_exact_keys(
        payload,
        {"schema", "version", "parent", "contract", "repositories", "global_counts"},
        "quality payload",
    )
    if payload["schema"] != QUALITY_BASELINE_SCHEMA or payload["version"] != 1:
        raise RefreshQualityError("unsupported quality payload schema/version")
    parent = payload["parent"]
    if not isinstance(parent, dict):
        raise RefreshQualityError("quality parent must be an object")
    _require_exact_keys(
        parent,
        {"catalog_build_id", "build_token", "content_fingerprint"},
        "quality parent",
    )
    if (
        isinstance(parent["catalog_build_id"], bool)
        or not isinstance(parent["catalog_build_id"], int)
        or parent["catalog_build_id"] <= 0
    ):
        raise RefreshQualityError("quality parent build id must be positive")
    if not isinstance(parent["build_token"], str) or not parent["build_token"]:
        raise RefreshQualityError("quality parent build token must be non-empty")
    _require_hash(parent["content_fingerprint"], "quality parent content fingerprint")
    contract = payload["contract"]
    if not isinstance(contract, dict):
        raise RefreshQualityError("quality contract must be an object")
    _require_exact_keys(
        contract, {"delta_contract_version", "runtime_fingerprint"}, "quality contract"
    )
    if (
        isinstance(contract["delta_contract_version"], bool)
        or not isinstance(contract["delta_contract_version"], int)
        or contract["delta_contract_version"] <= 0
    ):
        raise RefreshQualityError("delta contract version must be positive")
    _require_hash(contract["runtime_fingerprint"], "runtime fingerprint")
    repositories = payload["repositories"]
    if not isinstance(repositories, list):
        raise RefreshQualityError("quality repositories must be an array")
    keys: list[str] = []
    for repository in repositories:
        if not isinstance(repository, dict):
            raise RefreshQualityError("quality repository must be an object")
        _require_exact_keys(
            repository,
            {
                "repo_key",
                "commit_sha",
                "manifest_hash",
                "builder_plan_hash",
                "diagnostics",
                "counts",
            },
            "quality repository",
        )
        repo_key = repository["repo_key"]
        if not isinstance(repo_key, str) or not repo_key:
            raise RefreshQualityError("quality repo_key must be non-empty")
        keys.append(repo_key)
        for field in ("manifest_hash", "builder_plan_hash"):
            _require_hash(repository[field], f"repository {field}")
        commit = repository["commit_sha"]
        if (
            not isinstance(commit, str)
            or len(commit) not in {40, 64}
            or any(character not in "0123456789abcdef" for character in commit)
        ):
            raise RefreshQualityError("repository commit SHA is invalid")
        _validate_counts(repository["counts"], "repository counts")
        _validate_diagnostics(repository["diagnostics"], "repository diagnostics")
    if keys != sorted(keys) or len(set(keys)) != len(keys):
        raise RefreshQualityError("quality repositories must be unique and sorted")
    _validate_counts(payload["global_counts"], "global counts")


def validate_quality_report(report: object) -> dict[str, object]:
    if not isinstance(report, dict):
        raise RefreshQualityError("quality report must be an object")
    _require_exact_keys(report, {"approval_sha256", "payload"}, "quality report")
    payload = report["payload"]
    if not isinstance(payload, dict):
        raise RefreshQualityError("quality report payload must be an object")
    validate_quality_payload(payload)
    expected = approval_sha256(payload)
    actual = _require_hash(report["approval_sha256"], "quality approval")
    if actual != expected:
        raise RefreshQualityError(
            f"quality report approval hash mismatch: expected={expected} actual={actual}"
        )
    return report


def write_quality_report_atomic(path: Path, report: Mapping[str, object]) -> None:
    validate_quality_report(dict(report))
    destination = path.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(canonical_json(report))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def load_quality_report(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RefreshQualityError(f"cannot load quality report {path}: {exc}") from exc
    return validate_quality_report(value)


def materialized_quality_run(
    *,
    approval: str,
    runtime_fingerprint: str,
    source_commit_sha: str,
    diagnostics: Sequence[Mapping[str, object]],
    counts: Mapping[str, int],
    status: str = "approved",
) -> dict[str, object]:
    if status not in {"approved", "enforced"}:
        raise RefreshQualityError(f"invalid materialized quality status: {status}")
    summary = {
        "schema": QUALITY_RUN_SCHEMA,
        "version": QUALITY_RUN_VERSION,
        "kind": "materialized",
        "status": status,
        "approval_sha256": _require_hash(approval, "quality approval"),
        "runtime_fingerprint": _require_hash(
            runtime_fingerprint, "runtime fingerprint"
        ),
        "source_commit_sha": source_commit_sha,
        "diagnostics": sorted(
            (dict(item) for item in diagnostics),
            key=lambda item: str(item["diagnostic_key"]),
        ),
        "counts": dict(sorted(counts.items())),
    }
    validate_quality_run(summary)
    return summary


def reference_quality_run(*, approval: str, baseline_run_id: int) -> dict[str, object]:
    if isinstance(baseline_run_id, bool) or baseline_run_id <= 0:
        raise RefreshQualityError("quality baseline run id must be positive")
    summary = {
        "schema": QUALITY_RUN_SCHEMA,
        "version": QUALITY_RUN_VERSION,
        "kind": "reference",
        "status": "inherited",
        "approval_sha256": _require_hash(approval, "quality approval"),
        "baseline_run_id": baseline_run_id,
    }
    validate_quality_run(summary)
    return summary


def validate_quality_run(summary: object) -> dict[str, object]:
    if not isinstance(summary, dict):
        raise RefreshQualityError("quality run must be an object")
    kind = summary.get("kind")
    if kind == "materialized":
        _require_exact_keys(
            summary,
            {
                "schema",
                "version",
                "kind",
                "status",
                "approval_sha256",
                "runtime_fingerprint",
                "source_commit_sha",
                "diagnostics",
                "counts",
            },
            "materialized quality run",
        )
        if summary["status"] not in {"approved", "enforced"}:
            raise RefreshQualityError("materialized quality status is invalid")
        _require_hash(summary["approval_sha256"], "quality approval")
        _require_hash(summary["runtime_fingerprint"], "runtime fingerprint")
        source_sha = summary["source_commit_sha"]
        if (
            not isinstance(source_sha, str)
            or len(source_sha) not in {40, 64}
            or any(character not in "0123456789abcdef" for character in source_sha)
        ):
            raise RefreshQualityError("quality source commit SHA is invalid")
        _validate_diagnostics(summary["diagnostics"], "quality run diagnostics")
        _validate_counts(summary["counts"], "quality run counts")
    elif kind == "reference":
        _require_exact_keys(
            summary,
            {
                "schema",
                "version",
                "kind",
                "status",
                "approval_sha256",
                "baseline_run_id",
            },
            "reference quality run",
        )
        if summary["status"] != "inherited":
            raise RefreshQualityError("reference quality status is invalid")
        _require_hash(summary["approval_sha256"], "quality approval")
        run_id = summary["baseline_run_id"]
        if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id <= 0:
            raise RefreshQualityError("quality reference run id is invalid")
    else:
        raise RefreshQualityError("quality run kind is invalid")
    if summary["schema"] != QUALITY_RUN_SCHEMA or summary["version"] != 1:
        raise RefreshQualityError("unsupported quality run schema/version")
    return summary


def resolve_reference_quality_run(
    conn, repo_id: int, current_run_id: int, summary: Mapping[str, object]
) -> tuple[int, dict[str, object]]:
    validate_quality_run(dict(summary))
    if summary.get("kind") != "reference":
        raise RefreshQualityError("quality summary is not a reference")
    run_id = summary.get("baseline_run_id")
    if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id <= 0:
        raise RefreshQualityError("quality reference run id is invalid")
    if run_id >= current_run_id:
        raise RefreshQualityError("quality reference must point backward")
    row = conn.execute(
        "SELECT validation_summary FROM repo_index_runs WHERE id=? AND repo_id=?",
        (run_id, repo_id),
    ).fetchone()
    if row is None or row[0] is None:
        raise RefreshQualityError("quality reference target is unavailable")
    try:
        materialized = json.loads(str(row[0]))
    except json.JSONDecodeError as exc:
        raise RefreshQualityError("quality reference target is malformed") from exc
    if materialized.get("kind") != "materialized":
        raise RefreshQualityError("quality reference chains are forbidden")
    validate_quality_run(materialized)
    if materialized.get("approval_sha256") != summary.get("approval_sha256"):
        raise RefreshQualityError("quality reference approval hash mismatch")
    return run_id, materialized
