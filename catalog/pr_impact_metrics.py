"""Quality metrics for the end-to-end PR-impact analysis."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _pr_recorded_facets(step0: Mapping[str, Any]) -> set[str]:
    recorded: set[str] = set()
    surfaces = _mapping(step0.get("affected_surfaces"))
    names = {
        "entities": "entity",
        "api": "api",
        "ui": "ui",
        "database": "database",
        "permissions": "permissions",
    }
    for source, normalized in names.items():
        value = _mapping(surfaces.get(source))
        if value.get("status") in {"confirmed", "assessed"} or value.get("facts"):
            recorded.add(normalized)
    obligations = _mapping(step0.get("test_obligations"))
    explicit_obligations = [
        item
        for key in ("existing_or_expected", "recommended", "unresolved")
        for item in _list(obligations.get(key))
        if str(item).strip()
        != "Do not infer test coverage until the target-revision source and catalog are verified."
    ]
    if explicit_obligations:
        recorded.add("tests")
    related = [
        item
        for item in _list(step0.get("related_repositories"))
        if isinstance(item, Mapping)
    ]
    if any(item.get("repo_key") != "ia-main" for item in related):
        recorded.add("tests")
    return recorded


def _expected_facets(blast_radius: Mapping[str, Any]) -> set[str]:
    expected: set[str] = set()
    if _list(blast_radius.get("entities")):
        expected.add("entity")
    for flow in _list(blast_radius.get("flows")):
        if not isinstance(flow, Mapping):
            continue
        kind = str(flow.get("flow_kind") or "")
        if kind in {"openapi", "openapi_entity", "rest_endpoint"}:
            expected.add("api")
        elif kind == "workflow":
            expected.add("workflow")
        elif kind == "permission":
            expected.add("permissions")
        elif kind == "database":
            expected.add("database")
    coverage = _mapping(blast_radius.get("test_coverage"))
    if coverage:
        expected.add("tests")
    return expected


def build_metrics(
    step0: Mapping[str, Any],
    reports: Mapping[str, Any],
    blast_radius: Mapping[str, Any],
    *,
    run_id: str | None = None,
    pr_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return deterministic completeness and quality metrics for one run."""
    entities = _list(blast_radius.get("entities"))
    flows = _list(blast_radius.get("flows"))
    gaps = _list(blast_radius.get("gaps"))
    confirmed_entities = sum(
        isinstance(item, Mapping) and item.get("status") == "confirmed"
        for item in entities
    )
    confirmed_flows = sum(
        isinstance(item, Mapping) and item.get("status") == "confirmed"
        for item in flows
    )
    candidate_flows = sum(
        isinstance(item, Mapping) and item.get("status") == "candidate"
        for item in flows
    )
    coverage = _mapping(blast_radius.get("test_coverage"))
    coverage_entities = _list(coverage.get("entities"))
    confirmed_test_entities = sum(
        isinstance(item, Mapping) and item.get("status") == "confirmed"
        for item in coverage_entities
    )
    active_test_endpoints = sum(
        int(_mapping(item.get("summary")).get("active_covered_endpoint_count", 0))
        for item in coverage_entities
        if isinstance(item, Mapping)
    )
    uncovered_test_endpoints = sum(
        int(_mapping(item.get("summary")).get("uncovered_endpoint_count", 0))
        for item in coverage_entities
        if isinstance(item, Mapping)
    )
    conditional_test_endpoints = sum(
        int(_mapping(item.get("summary")).get("conditional_only_endpoint_count", 0))
        for item in coverage_entities
        if isinstance(item, Mapping)
    )
    statuses = {}
    for stage, report in reports.items():
        if isinstance(report, Mapping):
            statuses[stage] = report.get("status")
    gap_counts: dict[str, int] = {}
    for item in gaps:
        if isinstance(item, Mapping):
            code = str(item.get("gap_code") or "unknown")
            gap_counts[code] = gap_counts.get(code, 0) + 1
    expected = _expected_facets(blast_radius)
    recorded = _pr_recorded_facets(step0)
    not_recorded = sorted(expected - recorded)
    improvements = []
    improvement_by_gap = {
        "entity_mapping_missing": "Materialize reviewed exact symbol-to-entity links for changed symbols.",
        "entity_mapping_unresolved": "Resolve or review unresolved symbol-to-entity contract entries.",
        "test_coverage_not_run": "Build or provide exact downstream test-repository coverage evidence.",
        "test_catalog_unavailable": "Publish a revision-pinned downstream coverage catalog artifact.",
        "test_coverage_unscoped": "Provide reviewed entity mappings before querying downstream coverage.",
        "test_coverage_repository_missing": "Build production and test-suite repository identities into the same catalog.",
        "test_coverage_stale": "Rebuild downstream coverage against the exact PR target revision.",
        "test_endpoint_uncovered": "Add or update a REST scenario for the uncovered endpoint.",
        "test_endpoint_weak_coverage": "Review conditional, CI-only, or known-issue coverage for active regression needs.",
        "reported_gap": "Improve the upstream PR-impact surface or parser that produced this gap.",
    }
    for code in sorted(gap_counts):
        recommendation = improvement_by_gap.get(code)
        if recommendation and recommendation not in improvements:
            improvements.append(recommendation)
    return {
        "schema_version": "0.1",
        "analysis_kind": "pr_impact_metrics",
        "run_id": run_id,
        "recorded_at": datetime.now(UTC).isoformat(),
        "stage_statuses": statuses,
        "found_successfully": {
            "confirmed_entities": confirmed_entities,
            "confirmed_flows": confirmed_flows,
            "confirmed_test_entities": confirmed_test_entities,
            "active_covered_test_endpoints": active_test_endpoints,
            "catalog_and_report_facts": confirmed_entities + confirmed_flows,
        },
        "missed_or_unconfirmed": {
            "candidate_flows": candidate_flows,
            "uncovered_test_endpoints": uncovered_test_endpoints,
            "conditional_test_endpoints": conditional_test_endpoints,
            "unresolved_entities": sum(
                1
                for item in gaps
                if isinstance(item, Mapping) and str(item.get("surface")) == "entity"
            ),
            "gap_count": len(gaps),
        },
        "not_recorded_in_pr": {
            "basis": "explicit_structured_pr_fields_only; prose is not interpreted as a fact",
            "metadata_fields": {
                "title": bool(
                    _mapping(_mapping(pr_metadata).get("pull_request")).get("title")
                ),
                "body": bool(
                    _mapping(_mapping(pr_metadata).get("pull_request")).get("body")
                ),
                "labels": bool(
                    _mapping(_mapping(pr_metadata).get("pull_request")).get("labels")
                ),
            },
            "expected_facets": sorted(expected),
            "recorded_facets": sorted(recorded),
            "missing_facets": not_recorded,
            "count": len(not_recorded),
        },
        "gap_counts": dict(sorted(gap_counts.items())),
        "improvements": improvements,
    }


def write_metrics(path: str | Path, metrics: Mapping[str, Any]) -> None:
    """Write one JSON metrics artifact; callers choose retention and location."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(dict(metrics), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
