"""Deterministic per-PR behavior impact projection over Greenfield evidence."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from typing import Any

from greenfield.artifact_io import artifact_sha256
from greenfield.step4_contract import validate_step4_report
from greenfield.step5_actions import validate_step5_report
from scripts.validate_greenfield_step2 import validate as validate_step2_report
from scripts.validate_greenfield_step3 import validate as validate_step3_report

SCHEMA_VERSION = "0.1"
ANALYSIS_KIND = "greenfield_behavior_impact_report"
RULE_SET_VERSION = "0.1"
SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
EXPECTED_KINDS = {
    "step2": "greenfield_pr_impact_step_2",
    "step3": "greenfield_pr_impact_step_3",
    "step4": "greenfield_pr_impact_step_4",
    "step5": "greenfield_pr_impact_step_5",
}
UNCERTAIN_STATES = {
    "unresolved",
    "stale",
    "unavailable",
    "unknown",
    "missing",
    "not_modelled",
    "not_run",
}


class BehaviorHandbookError(ValueError):
    """Raised when Greenfield evidence cannot form a trustworthy impact report."""


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BehaviorHandbookError(f"{label} must be an object")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BehaviorHandbookError(f"{label} must be a non-empty string")
    return value.strip()


def _rows(value: Any, label: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        raise BehaviorHandbookError(f"{label} must be a list")
    if any(not isinstance(item, Mapping) for item in value):
        raise BehaviorHandbookError(f"{label} must contain objects")
    return list(value)


def _sorted_strings(value: Any, label: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise BehaviorHandbookError(f"{label} must be a list")
    result = [_text(item, f"{label} item") for item in value]
    if result != sorted(set(result)):
        raise BehaviorHandbookError(f"{label} must be sorted and unique")
    return result


def _validate_upstream_reports(reports: Mapping[str, Mapping[str, Any]]) -> None:
    validators = {
        "step2": validate_step2_report,
        "step3": validate_step3_report,
        "step4": validate_step4_report,
        "step5": validate_step5_report,
    }
    for name, validator in validators.items():
        errors = validator(reports[name])
        if errors:
            raise BehaviorHandbookError(f"invalid {name} report: {'; '.join(errors)}")


def _validate_inputs(
    contract: Mapping[str, Any], reports: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    if contract.get("schema_version") != "0.1":
        raise BehaviorHandbookError("contract schema_version must be 0.1")
    if contract.get("artifact_kind") != "generated_behavior_contract":
        raise BehaviorHandbookError("contract must be a generated behavior contract")
    revision = _text(contract.get("revision"), "contract.revision")
    if not SHA.fullmatch(revision):
        raise BehaviorHandbookError(
            "contract.revision must be a lowercase 40-character SHA"
        )
    contract_input = _object(contract.get("input"), "contract.input")
    if contract_input.get("head_sha") != revision:
        raise BehaviorHandbookError("contract input head_sha does not match revision")
    changed_paths = _sorted_strings(
        contract_input.get("changed_paths"), "contract.input.changed_paths"
    )
    canonical_repository = _text(
        contract_input.get("repository"), "contract.input.repository"
    )
    repo_key = _text(contract_input.get("repo_key"), "contract.input.repo_key")
    contract_repository = _text(contract.get("repository"), "contract.repository")
    if contract_repository not in {repo_key, canonical_repository}:
        raise BehaviorHandbookError("contract repository identity does not match input")
    generation = _object(contract.get("generation"), "contract.generation")
    if generation.get("status") not in {"complete", "partial"}:
        raise BehaviorHandbookError("contract generation status is invalid")

    for name, report in reports.items():
        if report.get("schema_version") != "0.1":
            raise BehaviorHandbookError(f"{name} schema_version must be 0.1")
        if report.get("analysis_kind") != EXPECTED_KINDS[name]:
            raise BehaviorHandbookError(f"{name} analysis_kind is invalid")
        if report.get("status") not in {"complete", "partial"}:
            raise BehaviorHandbookError(f"{name} status is invalid")
        report_input = _object(report.get("input"), f"{name}.input")
        if report_input.get("source_repository") != repo_key:
            raise BehaviorHandbookError(
                f"{name} source repository does not match contract"
            )
        if report_input.get("source_repo_key", repo_key) != repo_key:
            raise BehaviorHandbookError(
                f"{name} source repo key does not match contract"
            )
        if (
            report_input.get("canonical_repository", canonical_repository)
            != canonical_repository
        ):
            raise BehaviorHandbookError(
                f"{name} canonical repository does not match contract"
            )
        if report_input.get("target_revision") != revision:
            raise BehaviorHandbookError(
                f"{name} target revision does not match contract"
            )
        if report_input.get("changed_paths") != changed_paths:
            raise BehaviorHandbookError(f"{name} changed paths do not match contract")

    source = {
        "source_repository": canonical_repository,
        "source_repo_key": repo_key,
        "target_revision": revision,
        "changed_paths": changed_paths,
    }
    optional = {
        "source_pr_number": contract_input.get("pr_number"),
        "base_revision": contract_input.get("base_sha"),
    }
    source.update({key: value for key, value in optional.items() if value is not None})
    return source


def _active_behaviors(
    contract: Mapping[str, Any],
) -> list[tuple[int, Mapping[str, Any]]]:
    relations = _rows(contract.get("relations"), "contract.relations")
    behaviors = [
        (index, relation)
        for index, relation in enumerate(relations)
        if relation.get("relationship_type") == "behavior_contract"
        and relation.get("status") == "active"
    ]
    if not behaviors:
        raise BehaviorHandbookError("contract has no active behavior relations")
    ids = [
        _text(row.get("interface_id"), "behavior interface_id") for _, row in behaviors
    ]
    if len(ids) != len(set(ids)):
        raise BehaviorHandbookError("contract contains duplicate behavior IDs")
    return sorted(behaviors, key=lambda item: str(item[1].get("interface_id")))


def _behavior_edges(
    relation: Mapping[str, Any], contract: Mapping[str, Any], revision: str
) -> list[dict[str, Any]]:
    generation = _object(contract.get("generation"), "contract.generation")
    edges = _rows(generation.get("edges"), "contract.generation.edges")
    reachable = set(_sorted_strings(relation.get("source_symbols"), "source_symbols"))
    selected: dict[str, dict[str, Any]] = {}
    changed = True
    while changed:
        changed = False
        for edge in edges:
            source = edge.get("source_symbol")
            target = edge.get("target_symbol")
            if source not in reachable:
                continue
            if edge.get("source_revision") != revision:
                raise BehaviorHandbookError("contract edge source revision is stale")
            line = edge.get("source_line")
            if isinstance(line, bool) or not isinstance(line, int) or line < 1:
                raise BehaviorHandbookError("contract edge source line is invalid")
            normalized = dict(edge)
            selected[_canonical(normalized)] = normalized
            if isinstance(target, str) and target and target not in reachable:
                reachable.add(target)
                changed = True
    return [selected[key] for key in sorted(selected)]


def _implementation(
    relation: Mapping[str, Any], contract: Mapping[str, Any], revision: str
) -> dict[str, Any]:
    source_paths = _sorted_strings(relation.get("source_paths"), "source_paths")
    source_symbols = _sorted_strings(relation.get("source_symbols"), "source_symbols")
    edges = _behavior_edges(relation, contract, revision)
    locators: dict[str, dict[str, Any]] = {}
    line_paths: set[str] = set()
    for edge in edges:
        path = _text(edge.get("source_path"), "edge.source_path")
        line_paths.add(path)
        locator = {
            "kind": "line",
            "path": path,
            "line": edge["source_line"],
            "symbol": _text(edge.get("source_symbol"), "edge.source_symbol"),
            "source_revision": revision,
        }
        locators[_canonical(locator)] = locator
    for path in source_paths:
        if path in line_paths:
            continue
        locator = {
            "kind": "path_only",
            "path": path,
            "source_revision": revision,
        }
        locators[_canonical(locator)] = locator

    entry_surfaces = contract.get("entry_surfaces", {})
    if not isinstance(entry_surfaces, Mapping):
        entry_surfaces = {}
    surfaces = {
        name: dict(entry_surfaces.get(name, {"status": "unknown"}))
        if isinstance(entry_surfaces.get(name), Mapping)
        else {"status": "unknown"}
        for name in _sorted_strings(
            relation.get("entry_surfaces", []),
            "entry_surfaces",
            allow_empty=True,
        )
    }
    return {
        "source_paths": source_paths,
        "entry_symbols": source_symbols,
        "locators": [locators[key] for key in sorted(locators)],
        "call_edges": edges,
        "entry_surfaces": surfaces,
    }


def _interface_id(row: Mapping[str, Any], section: str) -> str | None:
    if section == "step5_actions":
        scope = row.get("scope")
        value = scope.get("interface_id") if isinstance(scope, Mapping) else None
    else:
        value = row.get("interface_id")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _records(rows: Iterable[Mapping[str, Any]], pointer: str) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows):
        value = dict(row)
        key = _canonical(value)
        unique.setdefault(key, {"row": value, "pointer": f"{pointer}/{index}"})
    return [unique[key] for key in sorted(unique)]


def _sections(
    step2: Mapping[str, Any],
    step3: Mapping[str, Any],
    step4: Mapping[str, Any],
    step5: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    interfaces = _object(step3.get("interfaces"), "step3.interfaces")
    owners = _object(step3.get("owners"), "step3.owners")
    tests = _object(step3.get("test_suites"), "step3.test_suites")
    impact = _object(step3.get("impact"), "step3.impact")
    repositories = _object(
        step3.get("potentially_affected_repositories"),
        "step3.potentially_affected_repositories",
    )
    related_pull_requests = _object(
        step3.get("related_pull_requests"), "step3.related_pull_requests"
    )
    coverage = _object(step4.get("coverage"), "step4.coverage")
    obligations = _object(step4.get("obligations"), "step4.obligations")
    return {
        "step2_candidates": {
            "artifact": "step2",
            "records": _records(
                _rows(step2.get("candidates"), "step2.candidates"), "/candidates"
            ),
        },
        "step3_impact": {
            "artifact": "step3",
            "records": _records(
                _rows(impact.get("items"), "step3.impact.items"), "/impact/items"
            ),
        },
        "step3_repositories": {
            "artifact": "step3",
            "records": _records(
                _rows(
                    repositories.get("items"),
                    "step3.potentially_affected_repositories.items",
                ),
                "/potentially_affected_repositories/items",
            ),
        },
        "step3_interfaces": {
            "artifact": "step3",
            "records": _records(
                _rows(interfaces.get("items"), "step3.interfaces.items"),
                "/interfaces/items",
            ),
        },
        "step3_owners": {
            "artifact": "step3",
            "records": _records(
                _rows(owners.get("items"), "step3.owners.items"), "/owners/items"
            ),
        },
        "step3_test_suites": {
            "artifact": "step3",
            "records": _records(
                _rows(tests.get("items"), "step3.test_suites.items"),
                "/test_suites/items",
            ),
        },
        "step3_related_pull_requests": {
            "artifact": "step3",
            "records": _records(
                _rows(
                    related_pull_requests.get("items"),
                    "step3.related_pull_requests.items",
                ),
                "/related_pull_requests/items",
            ),
        },
        "step4_coverage": {
            "artifact": "step4",
            "records": _records(
                _rows(coverage.get("items"), "step4.coverage.items"),
                "/coverage/items",
            ),
        },
        "step4_obligations": {
            "artifact": "step4",
            "records": _records(
                _rows(obligations.get("items"), "step4.obligations.items"),
                "/obligations/items",
            ),
        },
        "step5_actions": {
            "artifact": "step5",
            "records": _records(
                _rows(step5.get("actions"), "step5.actions"), "/actions"
            ),
        },
    }


def _shared_interfaces(
    behavior_id: str, step2_records: Iterable[Mapping[str, Any]], revision: str
) -> set[str]:
    related = {behavior_id}
    for record in step2_records:
        row = record.get("row")
        if not isinstance(row, Mapping):
            continue
        anchors = row.get("source_anchors", [])
        if not isinstance(anchors, list):
            continue
        for anchor in anchors:
            if (
                not isinstance(anchor, Mapping)
                or anchor.get("source_revision") != revision
            ):
                continue
            interface_rows = anchor.get("interfaces", [])
            if not isinstance(interface_rows, list):
                continue
            interface_ids = {
                item.get("interface_id")
                for item in interface_rows
                if isinstance(item, Mapping)
                and item.get("source_revision") == revision
                and isinstance(item.get("interface_id"), str)
            }
            if behavior_id in interface_ids:
                related.update(interface_ids)
    return {value for value in related if isinstance(value, str) and value}


def _has_uncertainty(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(_has_uncertainty(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_uncertainty(item) for item in value)
    return isinstance(value, str) and value in UNCERTAIN_STATES


def _behavior_gaps(
    implementation: Mapping[str, Any], attached: Mapping[str, list[dict[str, Any]]]
) -> list[str]:
    gaps: set[str] = set()
    for name, surface in implementation.get("entry_surfaces", {}).items():
        if isinstance(surface, Mapping) and surface.get("status") in UNCERTAIN_STATES:
            gaps.add(f"entry_surface:{name}:{surface.get('status')}")
    for section, rows in attached.items():
        for row in rows:
            interface = _interface_id(row, section) or "unassigned"
            state = row.get("classification", row.get("status"))
            if state in UNCERTAIN_STATES:
                gaps.add(f"{section}:{interface}:{state}")
            test = row.get("test")
            if (
                isinstance(test, Mapping)
                and test.get("execution_result") in UNCERTAIN_STATES
            ):
                gaps.add(
                    f"{section}:{interface}:execution_{test.get('execution_result')}"
                )
    return sorted(gaps)


def build_behavior_handbook(
    contract: Mapping[str, Any],
    step2: Mapping[str, Any],
    step3: Mapping[str, Any],
    step4: Mapping[str, Any],
    step5: Mapping[str, Any],
) -> dict[str, Any]:
    """Project exact Greenfield evidence into a behavior-centric index."""

    reports = {"step2": step2, "step3": step3, "step4": step4, "step5": step5}
    _validate_upstream_reports(reports)
    source = _validate_inputs(contract, reports)
    revision = source["target_revision"]
    generation = _object(contract.get("generation"), "contract.generation")
    artifacts = {"contract": contract, **reports}
    digests = {name: artifact_sha256(value) for name, value in artifacts.items()}
    sections = _sections(step2, step3, step4, step5)
    assigned: dict[str, set[str]] = {name: set() for name in sections}
    behaviors: list[dict[str, Any]] = []

    for relation_index, relation in _active_behaviors(contract):
        behavior_id = _text(relation.get("interface_id"), "behavior.interface_id")
        description = _text(
            relation.get("protected_behavior"), "behavior.protected_behavior"
        )
        related_interfaces = _shared_interfaces(
            behavior_id, sections["step2_candidates"]["records"], revision
        )
        attached: dict[str, list[dict[str, Any]]] = {}
        evidence_references = [
            {
                "artifact": "contract",
                "sha256": digests["contract"],
                "pointer": f"/relations/{relation_index}",
            }
        ]
        for section, data in sections.items():
            matches = []
            for record in data["records"]:
                row = record["row"]
                if _interface_id(row, section) not in related_interfaces:
                    continue
                matches.append(row)
                assigned[section].add(_canonical(row))
                evidence_references.append(
                    {
                        "artifact": data["artifact"],
                        "sha256": digests[data["artifact"]],
                        "pointer": record["pointer"],
                    }
                )
            attached[section] = sorted(matches, key=_canonical)

        implementation = _implementation(relation, contract, revision)
        gaps = _behavior_gaps(implementation, attached)
        if generation.get("status") != "complete":
            gaps.append(f"contract_generation:{generation.get('status')}")
            gaps = sorted(set(gaps))
        behavior = {
            "behavior_id": behavior_id,
            "description": description,
            "status": "partial"
            if gaps or _has_uncertainty(implementation) or _has_uncertainty(attached)
            else "complete",
            "implementation": implementation,
            "impact": {
                "related_interface_ids": sorted(related_interfaces),
                "candidates": attached["step2_candidates"],
                "items": attached["step3_impact"],
                "interfaces": attached["step3_interfaces"],
                "owners": attached["step3_owners"],
            },
            "coverage": {
                "test_suites": attached["step3_test_suites"],
                "items": attached["step4_coverage"],
                "obligations": attached["step4_obligations"],
            },
            "actions": attached["step5_actions"],
            "gaps": gaps,
            "evidence_references": sorted(
                {_canonical(item): item for item in evidence_references}.values(),
                key=lambda item: (item["artifact"], item["pointer"]),
            ),
        }
        behaviors.append(behavior)

    unassigned = {
        section: [
            record["row"]
            for record in data["records"]
            if _canonical(record["row"]) not in assigned[section]
        ]
        for section, data in sections.items()
    }
    global_gaps = sorted(
        {
            str(gap)
            for report in reports.values()
            for gap in report.get("gaps", [])
            if isinstance(gap, str) and gap
        }
    )
    if generation.get("status") != "complete":
        global_gaps.append(f"contract_generation:{generation.get('status')}")
        global_gaps = sorted(set(global_gaps))
    warnings = sorted(
        {
            str(warning)
            for report in reports.values()
            for warning in report.get("warnings", [])
            if isinstance(warning, str) and warning
        }
    )
    unassigned_count = sum(len(rows) for rows in unassigned.values())
    upstream_statuses = {
        "contract": generation["status"],
        **{name: value["status"] for name, value in sorted(reports.items())},
    }
    upstream_complete = all(
        status == "complete" for status in upstream_statuses.values()
    )
    complete = (
        upstream_complete
        and not global_gaps
        and not unassigned_count
        and all(behavior["status"] == "complete" for behavior in behaviors)
    )
    register = [
        {
            "behavior_id": behavior["behavior_id"],
            "description": behavior["description"],
            "status": behavior["status"],
            "source_path_count": len(behavior["implementation"]["source_paths"]),
            "impact_count": len(behavior["impact"]["items"]),
            "test_count": len(behavior["coverage"]["test_suites"])
            + len(behavior["coverage"]["items"]),
            "action_count": len(behavior["actions"]),
        }
        for behavior in behaviors
    ]
    report = {
        "schema_version": SCHEMA_VERSION,
        "analysis_kind": ANALYSIS_KIND,
        "status": "complete" if complete else "partial",
        "input": source,
        "summary": {
            "behavior_count": len(behaviors),
            "complete_behavior_count": sum(
                behavior["status"] == "complete" for behavior in behaviors
            ),
            "partial_behavior_count": sum(
                behavior["status"] == "partial" for behavior in behaviors
            ),
            "unassigned_evidence_count": unassigned_count,
            "upstream_statuses": upstream_statuses,
            "confidence": dict(step2["confidence"])
            if isinstance(step2.get("confidence"), Mapping)
            else None,
            "surface_statuses": dict(step3["surface_statuses"])
            if isinstance(step3.get("surface_statuses"), Mapping)
            else {},
            "direct_components": dict(step3["direct_components"])
            if isinstance(step3.get("direct_components"), Mapping)
            else {},
        },
        "register": register,
        "behaviors": behaviors,
        "unassigned_evidence": unassigned,
        "gaps": global_gaps,
        "warnings": warnings,
        "provenance": {
            "contract_sha256": digests["contract"],
            "step2_report_sha256": digests["step2"],
            "step3_report_sha256": digests["step3"],
            "step4_report_sha256": digests["step4"],
            "step5_report_sha256": digests["step5"],
            "rule_set_version": RULE_SET_VERSION,
            "read_only": True,
            "catalog_mutation": "none",
            "github_writes": "none",
        },
    }
    errors = validate_behavior_handbook(report)
    if errors:
        raise BehaviorHandbookError(
            "generated invalid behavior handbook: " + "; ".join(errors)
        )
    return report


def validate_behavior_handbook(report: Any) -> list[str]:
    """Validate the public V1 handbook contract without consulting ambient state."""

    if not isinstance(report, Mapping):
        return ["report must be an object"]
    errors: list[str] = []
    if report.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    if report.get("analysis_kind") != ANALYSIS_KIND:
        errors.append(f"analysis_kind must be {ANALYSIS_KIND}")
    if report.get("status") not in {"complete", "partial"}:
        errors.append("status must be complete or partial")
    source = report.get("input")
    if not isinstance(source, Mapping):
        errors.append("input must be an object")
        source = {}
    revision = source.get("target_revision")
    if not isinstance(revision, str) or not SHA.fullmatch(revision):
        errors.append("input.target_revision must be a lowercase 40-character SHA")
    paths = source.get("changed_paths")
    if not isinstance(paths, list) or not paths or paths != sorted(set(paths)):
        errors.append("input.changed_paths must be sorted and unique")
    for field in ("source_repository", "source_repo_key"):
        if not isinstance(source.get(field), str) or not source[field].strip():
            errors.append(f"input.{field} is required")

    provenance = report.get("provenance")
    if not isinstance(provenance, Mapping):
        errors.append("provenance must be an object")
        provenance = {}
    for field in (
        "contract_sha256",
        "step2_report_sha256",
        "step3_report_sha256",
        "step4_report_sha256",
        "step5_report_sha256",
    ):
        value = provenance.get(field)
        if not isinstance(value, str) or not SHA256.fullmatch(value):
            errors.append(f"provenance.{field} must be SHA-256")
    if provenance.get("read_only") is not True:
        errors.append("provenance.read_only must be true")
    if provenance.get("catalog_mutation") != "none":
        errors.append("provenance.catalog_mutation must be none")
    if provenance.get("github_writes") != "none":
        errors.append("provenance.github_writes must be none")

    behaviors = report.get("behaviors")
    if not isinstance(behaviors, list) or not behaviors:
        errors.append("behaviors must be a non-empty list")
        behaviors = []
    behavior_ids: list[str] = []
    for index, behavior in enumerate(behaviors):
        label = f"behaviors[{index}]"
        if not isinstance(behavior, Mapping):
            errors.append(f"{label} must be an object")
            continue
        behavior_id = behavior.get("behavior_id")
        if not isinstance(behavior_id, str) or not behavior_id.strip():
            errors.append(f"{label}.behavior_id is required")
        else:
            behavior_ids.append(behavior_id)
        if (
            not isinstance(behavior.get("description"), str)
            or not behavior["description"].strip()
        ):
            errors.append(f"{label}.description is required")
        if behavior.get("status") not in {"complete", "partial"}:
            errors.append(f"{label}.status is invalid")
        implementation = behavior.get("implementation")
        if not isinstance(implementation, Mapping):
            errors.append(f"{label}.implementation must be an object")
            continue
        locators = implementation.get("locators")
        if not isinstance(locators, list):
            errors.append(f"{label}.implementation.locators must be a list")
            locators = []
        locator_keys = []
        for locator_index, locator in enumerate(locators):
            locator_label = f"{label}.implementation.locators[{locator_index}]"
            if not isinstance(locator, Mapping):
                errors.append(f"{locator_label} must be an object")
                continue
            if locator.get("source_revision") != revision:
                errors.append(f"{locator_label}.source_revision is stale")
            if locator.get("kind") not in {"line", "path_only"}:
                errors.append(f"{locator_label}.kind is invalid")
            if not isinstance(locator.get("path"), str) or not locator["path"].strip():
                errors.append(f"{locator_label}.path is required")
            if locator.get("kind") == "line":
                line = locator.get("line")
                if isinstance(line, bool) or not isinstance(line, int) or line < 1:
                    errors.append(f"{locator_label}.line must be positive")
            elif "line" in locator:
                errors.append(
                    f"{locator_label} path-only locator must not contain line"
                )
            locator_keys.append(_canonical(locator))
        if locator_keys != sorted(set(locator_keys)):
            errors.append(f"{label}.implementation.locators must be sorted and unique")
        if behavior.get("status") == "complete" and (
            behavior.get("gaps")
            or _has_uncertainty(implementation)
            or _has_uncertainty(behavior.get("impact"))
            or _has_uncertainty(behavior.get("coverage"))
            or _has_uncertainty(behavior.get("actions"))
        ):
            errors.append(f"{label} complete status contains uncertain evidence")
        references = behavior.get("evidence_references")
        if not isinstance(references, list) or not references:
            errors.append(f"{label}.evidence_references must be a non-empty list")
        else:
            for ref_index, reference in enumerate(references):
                ref_label = f"{label}.evidence_references[{ref_index}]"
                if not isinstance(reference, Mapping):
                    errors.append(f"{ref_label} must be an object")
                    continue
                artifact = reference.get("artifact")
                digest_field = (
                    "contract_sha256"
                    if artifact == "contract"
                    else f"{artifact}_report_sha256"
                )
                if artifact not in {"contract", "step2", "step3", "step4", "step5"}:
                    errors.append(f"{ref_label}.artifact is invalid")
                elif reference.get("sha256") != provenance.get(digest_field):
                    errors.append(f"{ref_label}.sha256 does not match provenance")
                if not isinstance(reference.get("pointer"), str) or not reference[
                    "pointer"
                ].startswith("/"):
                    errors.append(f"{ref_label}.pointer is invalid")
    if behavior_ids != sorted(set(behavior_ids)):
        errors.append("behaviors must be sorted by unique behavior_id")

    register = report.get("register")
    if not isinstance(register, list) or len(register) != len(behaviors):
        errors.append("register must contain one row per behavior")
    elif [
        row.get("behavior_id") for row in register if isinstance(row, Mapping)
    ] != behavior_ids:
        errors.append("register behavior IDs must match behaviors")
    unassigned = report.get("unassigned_evidence")
    if not isinstance(unassigned, Mapping):
        errors.append("unassigned_evidence must be an object")
        unassigned_count = 0
    else:
        unassigned_count = 0
        for name, value in unassigned.items():
            if not isinstance(name, str) or not isinstance(value, list):
                errors.append("unassigned_evidence values must be lists")
            else:
                unassigned_count += len(value)
    summary = report.get("summary")
    upstream_complete = False
    if not isinstance(summary, Mapping):
        errors.append("summary must be an object")
    else:
        if summary.get("behavior_count") != len(behaviors):
            errors.append("summary.behavior_count does not match behaviors")
        if summary.get("unassigned_evidence_count") != unassigned_count:
            errors.append("summary.unassigned_evidence_count does not match evidence")
        upstream_statuses = summary.get("upstream_statuses")
        if not isinstance(upstream_statuses, Mapping) or set(upstream_statuses) != {
            "contract",
            "step2",
            "step3",
            "step4",
            "step5",
        }:
            errors.append("summary.upstream_statuses is invalid")
            upstream_complete = False
        else:
            upstream_complete = all(
                status == "complete" for status in upstream_statuses.values()
            )
    if report.get("status") == "complete" and (
        report.get("gaps")
        or unassigned_count
        or not upstream_complete
        or any(
            isinstance(behavior, Mapping) and behavior.get("status") != "complete"
            for behavior in behaviors
        )
    ):
        errors.append("complete handbook contains incomplete evidence")
    return errors


def _cell(value: Any) -> str:
    return (
        str(value if value is not None else "-").replace("|", "\\|").replace("\n", " ")
    )


def _row_label(row: Mapping[str, Any]) -> str:
    interface = row.get("interface_id")
    if not interface and isinstance(row.get("scope"), Mapping):
        interface = row["scope"].get("interface_id")
    test = row.get("test")
    test_label = ""
    if isinstance(test, Mapping):
        test_label = f"; test={test.get('id')} ({test.get('path')})"
    return (
        f"{row.get('target_repository', '-')} / {interface or '-'} / "
        f"{row.get('classification', row.get('status', '-'))}{test_label}"
    )


def render_behavior_handbook_markdown(report: Mapping[str, Any]) -> str:
    """Render a compact progressive view of a validated handbook."""

    errors = validate_behavior_handbook(report)
    if errors:
        raise BehaviorHandbookError(
            "cannot render invalid behavior handbook: " + "; ".join(errors)
        )
    source = report["input"]
    summary = report["summary"]
    lines = [
        "# Greenfield Behavior Handbook",
        "",
        "## Run Summary",
        "",
        f"- Repository: `{source['source_repository']}` (`{source['source_repo_key']}`)",
        f"- Revision: `{source['target_revision']}`",
        f"- Status: **{report['status']}**",
        f"- Behaviors: {summary['behavior_count']}",
        f"- Unassigned evidence rows: {summary['unassigned_evidence_count']}",
        "",
        "## Behavior Register",
        "",
        "| Behavior | Status | Sources | Impact | Tests | Actions |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in report["register"]:
        lines.append(
            "| "
            + " | ".join(
                _cell(value)
                for value in (
                    row["behavior_id"],
                    row["status"],
                    row["source_path_count"],
                    row["impact_count"],
                    row["test_count"],
                    row["action_count"],
                )
            )
            + " |"
        )
    if report["gaps"]:
        lines.extend(["", "## Global Gaps", ""])
        lines.extend(f"- `{gap}`" for gap in report["gaps"])

    for behavior in report["behaviors"]:
        lines.extend(
            [
                "",
                f"## {behavior['behavior_id']}",
                "",
                behavior["description"],
                "",
                f"Status: **{behavior['status']}**",
                "",
                "### Implementation",
                "",
            ]
        )
        for locator in behavior["implementation"]["locators"]:
            if locator["kind"] == "line":
                lines.append(
                    f"- `{locator['path']}:{locator['line']}` "
                    f"(`{locator['symbol']}`, revision `{locator['source_revision']}`)"
                )
            else:
                lines.append(
                    f"- `{locator['path']}` (path-only, revision "
                    f"`{locator['source_revision']}`)"
                )
        lines.extend(["", "### Impact", ""])
        impact_rows = behavior["impact"]["items"]
        lines.extend(
            [f"- {_row_label(row)}" for row in impact_rows]
            or ["- No exactly joined impact rows."]
        )
        lines.extend(["", "### Coverage", ""])
        coverage_rows = [
            *behavior["coverage"]["test_suites"],
            *behavior["coverage"]["items"],
            *behavior["coverage"]["obligations"],
        ]
        lines.extend(
            [f"- {_row_label(row)}" for row in coverage_rows]
            or ["- No exactly joined coverage rows."]
        )
        lines.extend(["", "### Actions", ""])
        lines.extend(
            [
                f"- `{row.get('action_type')}` for `{row.get('target_repository')}` "
                f"({row.get('status')})"
                for row in behavior["actions"]
            ]
            or ["- No exactly joined actions."]
        )
        if behavior["gaps"]:
            lines.extend(["", "### Behavior Gaps", ""])
            lines.extend(f"- `{gap}`" for gap in behavior["gaps"])

    lines.extend(["", "## Unassigned Evidence", ""])
    for section, rows in report["unassigned_evidence"].items():
        lines.append(f"- `{section}`: {len(rows)} row(s)")
    lines.extend(
        [
            "",
            (
                "This handbook is a derived location index. The retained Greenfield "
                "artifacts and revision-bound source remain authoritative."
            ),
            "",
        ]
    )
    return "\n".join(lines)


__all__ = [
    "ANALYSIS_KIND",
    "RULE_SET_VERSION",
    "SCHEMA_VERSION",
    "BehaviorHandbookError",
    "build_behavior_handbook",
    "render_behavior_handbook_markdown",
    "validate_behavior_handbook",
]
