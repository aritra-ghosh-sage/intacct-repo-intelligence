"""Validate a materialized greenfield Step 3 report."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from greenfield.artifact_io import artifact_sha256
from greenfield.source_identity import validate_identity_fields
from greenfield.step3_outcome import ANALYSIS_KIND, BLAST_RADIUS, REPORT_SCHEMA_VERSION

SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SURFACE_STATUSES = {
    "available",
    "partial",
    "unavailable",
    "not_modelled",
    "not_run",
    "unresolved",
    "empty",
    "unknown",
}
CLASSIFICATION_ORDER = {
    "confirmed": 0,
    "candidate": 1,
    "unresolved": 2,
    "stale": 3,
    "unavailable": 4,
    "unknown": 5,
}
INVENTORY_OBSERVATION_FIELDS = {
    "inspected_revision",
    "source_revision",
    "inventory_path_count",
    "workflow_path_count",
    "workflow_count",
    "artifact_status",
    "ci_linkage_status",
    "response_sha256",
}


def _surface(report: dict[str, Any], name: str, errors: list[str]) -> dict[str, Any]:
    value = report.get(name)
    if not isinstance(value, dict):
        errors.append(f"{name} must be an object")
        return {}
    if value.get("status") not in SURFACE_STATUSES:
        errors.append(f"{name}.status is invalid")
    items = value.get("items")
    if not isinstance(items, list):
        errors.append(f"{name}.items must be a list")
    return value


def _evidence_items(item: dict[str, Any], label: str, errors: list[str]) -> None:
    evidence = item.get("evidence")
    if (
        not isinstance(evidence, list)
        or not evidence
        or any(not isinstance(value, dict) for value in evidence)
    ):
        errors.append(f"{label}.evidence must be a non-empty list of objects")


def _required_strings(
    item: dict[str, Any], fields: tuple[str, ...], label: str, errors: list[str]
) -> None:
    for field in fields:
        if not isinstance(item.get(field), str) or not item[field].strip():
            errors.append(f"{label}.{field} must be a non-empty string")


def _validate_inventory_observation(value: Any, label: str, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{label} must be an object")
        return
    missing = sorted(INVENTORY_OBSERVATION_FIELDS - set(value))
    if missing:
        errors.append(f"{label} missing fields: {', '.join(missing)}")
    for field in ("inspected_revision", "source_revision"):
        if not isinstance(value.get(field), str) or not SHA.fullmatch(value[field]):
            errors.append(f"{label}.{field} must be a lowercase 40-character SHA")
    for field in ("inventory_path_count", "workflow_path_count", "workflow_count"):
        if (
            isinstance(value.get(field), bool)
            or not isinstance(value.get(field), int)
            or value[field] < 0
        ):
            errors.append(f"{label}.{field} must be a non-negative integer")
    if value.get("artifact_status") not in {
        "available",
        "empty",
        "not_linked_to_source_revision",
    }:
        errors.append(f"{label}.artifact_status is invalid")
    if value.get("ci_linkage_status") not in {"available", "unavailable"}:
        errors.append(f"{label}.ci_linkage_status is invalid")
    if not isinstance(value.get("response_sha256"), str) or not SHA256.fullmatch(
        value["response_sha256"]
    ):
        errors.append(f"{label}.response_sha256 must be a lowercase SHA-256")


def _validate_observations(item: dict[str, Any], label: str, errors: list[str]) -> None:
    observations = item.get("observations", [])
    if not isinstance(observations, list) or any(
        not isinstance(value, dict) for value in observations
    ):
        errors.append(f"{label}.observations must be a list of objects")
        return
    for index, observation in enumerate(observations):
        _validate_inventory_observation(
            observation, f"{label}.observations[{index}]", errors
        )
    if observations != sorted(
        observations,
        key=lambda value: json.dumps(value, sort_keys=True, separators=(",", ":")),
    ):
        errors.append(f"{label}.observations must be deterministically ordered")


def validate(report: Any) -> list[str]:
    if not isinstance(report, dict):
        return ["report must be an object"]
    errors: list[str] = []
    required = {
        "schema_version",
        "analysis_kind",
        "status",
        "input",
        "blast_radius",
        "direct_components",
        "potentially_affected_repositories",
        "interfaces",
        "owners",
        "test_suites",
        "related_pull_requests",
        "impact",
        "gaps",
        "warnings",
        "provenance",
    }
    errors.extend(f"missing field: {key}" for key in sorted(required - set(report)))
    if report.get("schema_version") != REPORT_SCHEMA_VERSION:
        errors.append(f"schema_version must be {REPORT_SCHEMA_VERSION}")
    if report.get("analysis_kind") != ANALYSIS_KIND:
        errors.append("invalid analysis_kind")
    if report.get("status") not in {"complete", "partial"}:
        errors.append("status must be complete or partial")
    data = report.get("input")
    if not isinstance(data, dict):
        errors.append("input must be an object")
    else:
        if (
            not isinstance(data.get("source_repository"), str)
            or not data["source_repository"]
        ):
            errors.append("input.source_repository is required")
        revision = data.get("target_revision")
        if not isinstance(revision, str) or not SHA.fullmatch(revision):
            errors.append("input.target_revision must be a lowercase 40-character SHA")
        if not isinstance(data.get("changed_paths"), list) or not data["changed_paths"]:
            errors.append("input.changed_paths must be a non-empty list")
        errors.extend(validate_identity_fields(data))
    if report.get("blast_radius") not in BLAST_RADIUS:
        errors.append("blast_radius is invalid")
    surface_statuses = report.get("surface_statuses")
    if surface_statuses is not None and not isinstance(surface_statuses, dict):
        errors.append("surface_statuses must be an object")
    elif isinstance(surface_statuses, dict):
        expected = {
            "xml_api", "csv_import", "actionui", "nextgen_ui", "direct_gl_service",
            "cross_module_callers", "database_schema", "permissions_workflows",
        }
        missing_surfaces = sorted(expected - set(surface_statuses))
        if missing_surfaces:
            errors.append("surface_statuses missing: " + ", ".join(missing_surfaces))
        for name, value in surface_statuses.items():
            if not isinstance(value, dict) or value.get("status") not in {
                "available", "empty", "not_run", "unresolved", "unavailable", "unknown"
            }:
                errors.append(f"surface_statuses.{name} is invalid")
    impact_surface = _surface(report, "impact", errors)
    impact = impact_surface.get("items", [])
    if not isinstance(impact, list):
        impact = []
    keys: list[tuple[int, str, str, str, str, str]] = []
    raw_inventory_fields = {"inventory_paths", "workflow_paths", "workflows"}
    for item in impact:
        if not isinstance(item, dict):
            errors.append("impact item must be an object")
            continue
        classification = item.get("classification")
        if classification not in CLASSIFICATION_ORDER:
            errors.append("impact classification is invalid")
        scope = item.get("scope")
        if scope not in {"repository", "interface"}:
            errors.append("impact scope is invalid")
        interface_id = item.get("interface_id")
        if scope == "repository":
            if interface_id is not None:
                errors.append("repository-scoped impact must not have interface_id")
            if item.get("relationship_type") != "repository_inventory":
                errors.append("repository-scoped impact must be repository_inventory")
        elif scope == "interface":
            if not isinstance(interface_id, str) or not interface_id.strip():
                errors.append("interface-scoped impact requires interface_id")
            elif interface_id.startswith("repository:"):
                errors.append("synthetic repository interface is not allowed")
        keys.append(
            (
                CLASSIFICATION_ORDER.get(classification, 99),
                str(item.get("target_repository")),
                str(interface_id or ""),
                str(item.get("relationship_type")),
                str(classification),
                str(scope),
            )
        )
        if not isinstance(item.get("evidence"), list) or not item["evidence"]:
            errors.append("impact evidence must be non-empty")
        unexpected_inventory = sorted(raw_inventory_fields & set(item))
        if unexpected_inventory:
            errors.append(
                "impact must not contain raw inventory fields: "
                + ", ".join(unexpected_inventory)
            )
        if item.get("relationship_type") == "repository_inventory":
            _validate_inventory_observation(
                item.get("observation"), "impact.observation", errors
            )
    if keys != sorted(keys):
        errors.append("impact must be deterministically ordered")
    if len(keys) != len(set(keys)):
        errors.append("impact items must be unique")
    components_surface = _surface(report, "direct_components", errors)
    components = components_surface.get("items", [])
    if not isinstance(components, list):
        components = []
    component_keys: list[tuple[str, str]] = []
    for item in components:
        if not isinstance(item, dict):
            errors.append("direct component must be an object")
            continue
        if not isinstance(item.get("kind"), str) or not isinstance(
            item.get("identity"), str
        ):
            errors.append("direct component kind and identity are required")
        if not isinstance(item.get("evidence"), list) or not item["evidence"]:
            errors.append("direct component evidence must be non-empty")
        component_keys.append((str(item.get("kind")), str(item.get("identity"))))
    if component_keys != sorted(component_keys):
        errors.append("direct components must be deterministically ordered")
    if len(component_keys) != len(set(component_keys)):
        errors.append("direct components must be unique")
    repositories = _surface(report, "potentially_affected_repositories", errors).get(
        "items", []
    )
    interfaces = _surface(report, "interfaces", errors).get("items", [])
    owners = _surface(report, "owners", errors).get("items", [])
    tests = _surface(report, "test_suites", errors).get("items", [])
    related_surface = _surface(report, "related_pull_requests", errors)
    related = related_surface.get("items", [])
    for label, items in (
        ("potentially_affected_repositories", repositories),
        ("interfaces", interfaces),
        ("owners", owners),
        ("test_suites", tests),
        ("related_pull_requests", related),
    ):
        if not isinstance(items, list):
            continue
        for index, item in enumerate(items):
            item_label = f"{label}[{index}]"
            if not isinstance(item, dict):
                errors.append(f"{item_label} must be an object")
                continue
            if label == "potentially_affected_repositories":
                _required_strings(
                    item, ("repository", "classification"), item_label, errors
                )
                if item.get("classification") not in CLASSIFICATION_ORDER:
                    errors.append(f"{item_label}.classification is invalid")
                _evidence_items(item, item_label, errors)
                _validate_observations(item, item_label, errors)
            elif label == "interfaces":
                _required_strings(
                    item,
                    (
                        "interface_id",
                        "target_repository",
                        "relationship_type",
                        "classification",
                        "reason",
                    ),
                    item_label,
                    errors,
                )
                if item.get("classification") not in CLASSIFICATION_ORDER:
                    errors.append(f"{item_label}.classification is invalid")
                if item.get("interface_id", "").startswith("repository:"):
                    errors.append(
                        f"{item_label}.synthetic repository interface is not allowed"
                    )
                _evidence_items(item, item_label, errors)
            elif label == "owners":
                _required_strings(
                    item,
                    ("interface_id", "target_repository", "status"),
                    item_label,
                    errors,
                )
                if item.get("status") not in {"available", "unavailable", "unknown"}:
                    errors.append(f"{item_label}.status is invalid")
                if item.get("interface_id", "").startswith("repository:"):
                    errors.append(
                        f"{item_label}.synthetic repository interface is not allowed"
                    )
                _evidence_items(item, item_label, errors)
            elif label == "test_suites":
                _required_strings(
                    item,
                    ("target_repository", "interface_id", "status"),
                    item_label,
                    errors,
                )
                if item.get("status") == "available":
                    test = item.get("test")
                    if (
                        not isinstance(test, dict)
                        or not isinstance(test.get("id"), str)
                        or not test["id"].strip()
                        or not isinstance(test.get("path"), str)
                        or not test["path"].strip()
                    ):
                        errors.append(f"{item_label}.test must contain id and path")
                elif item.get("status") != "unavailable":
                    errors.append(f"{item_label}.status is invalid")
                if item.get("interface_id", "").startswith("repository:"):
                    errors.append(
                        f"{item_label}.synthetic repository interface is not allowed"
                    )
                _evidence_items(item, item_label, errors)
            else:
                _required_strings(
                    item, ("repository", "state", "relation_type"), item_label, errors
                )
                number = item.get("number")
                if not isinstance(number, int) or number < 1:
                    errors.append(f"{item_label}.number must be positive")
                if item.get("state") not in {"open", "merged"}:
                    errors.append(f"{item_label}.state is invalid")
                for field in ("head_sha", "base_sha"):
                    if not isinstance(item.get(field), str) or not SHA.fullmatch(
                        item[field]
                    ):
                        errors.append(
                            f"{item_label}.{field} must be a lowercase 40-character SHA"
                        )
                evidence = item.get("evidence")
                if (
                    not isinstance(evidence, dict)
                    or not isinstance(evidence.get("id"), str)
                    or not evidence["id"].strip()
                ):
                    errors.append(f"{item_label}.evidence.id is required")
                elif evidence.get("sha256") is not None and (
                    not isinstance(evidence.get("sha256"), str)
                    or not SHA256.fullmatch(evidence["sha256"])
                ):
                    errors.append(f"{item_label}.evidence.sha256 must be SHA-256")
                elif evidence.get("sha256") is not None:
                    payload = evidence.get("payload")
                    if not isinstance(payload, dict):
                        errors.append(
                            f"{item_label}.evidence.payload is required with sha256"
                        )
                    elif artifact_sha256(payload) != evidence["sha256"]:
                        errors.append(
                            f"{item_label}.evidence.sha256 does not match payload"
                        )
    if isinstance(repositories, list):
        repository_keys = [
            (
                CLASSIFICATION_ORDER.get(item.get("classification"), 99),
                str(item.get("repository")),
            )
            for item in repositories
            if isinstance(item, dict)
        ]
        if repository_keys != sorted(repository_keys):
            errors.append(
                "potentially_affected_repositories must be deterministically ordered"
            )
    if isinstance(interfaces, list):
        interface_keys = [
            (
                CLASSIFICATION_ORDER.get(item.get("classification"), 99),
                str(item.get("target_repository")),
                str(item.get("interface_id")),
            )
            for item in interfaces
            if isinstance(item, dict)
        ]
        if interface_keys != sorted(interface_keys):
            errors.append("interfaces must be deterministically ordered")
    if isinstance(owners, list):
        owner_keys = [
            (str(item.get("target_repository")), str(item.get("interface_id")))
            for item in owners
            if isinstance(item, dict)
        ]
        if owner_keys != sorted(owner_keys):
            errors.append("owners must be deterministically ordered")
    if isinstance(tests, list):
        test_keys = [
            (
                str(item.get("target_repository")),
                str(item.get("interface_id")),
                json.dumps(item.get("test", {}), sort_keys=True, separators=(",", ":")),
            )
            for item in tests
            if isinstance(item, dict)
        ]
        if test_keys != sorted(test_keys):
            errors.append("test_suites must be deterministically ordered")
    if related_surface.get("status") == "available":
        source_pr = related_surface.get("source_pr_number")
        if not isinstance(source_pr, int) or source_pr < 1:
            errors.append("related_pull_requests.source_pr_number must be positive")
        _required_strings(
            related_surface,
            ("source_repository", "source_revision"),
            "related_pull_requests",
            errors,
        )
        if not isinstance(
            related_surface.get("source_revision"), str
        ) or not SHA.fullmatch(related_surface["source_revision"]):
            errors.append(
                "related_pull_requests.source_revision must be a lowercase 40-character SHA"
            )
    for key in ("gaps", "warnings"):
        if not isinstance(report.get(key), list) or any(
            not isinstance(item, str) for item in report[key]
        ):
            errors.append(f"{key} must be a list of strings")
    provenance = report.get("provenance")
    if not isinstance(provenance, dict):
        errors.append("provenance must be an object")
    else:
        if provenance.get("read_only") is not True:
            errors.append("provenance.read_only must be true")
        if provenance.get("catalog_mutation") != "none":
            errors.append("provenance.catalog_mutation must be none")
        if provenance.get("github_writes") != "none":
            errors.append("provenance.github_writes must be none")
        if not isinstance(
            provenance.get("step2_report_sha256"), str
        ) or not SHA256.fullmatch(provenance["step2_report_sha256"]):
            errors.append("provenance.step2_report_sha256 must be lowercase SHA-256")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True)
    args = parser.parse_args(argv)
    try:
        report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"invalid report: {exc}", file=sys.stderr)
        return 2
    errors = validate(report)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 2
    print("valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
