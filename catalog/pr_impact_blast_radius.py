"""Compose an evidence-bounded PR blast-radius report.

This module is deliberately provider-neutral.  It consumes the JSON contracts
from PR-impact Steps 0--3 and an optional test-coverage report; it does not
call a model, inspect a checkout, or mutate a catalog.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from typing import Any

REPORT_SCHEMA_VERSION = "0.2"
ANALYSIS_KIND = "pr_impact_blast_radius"

_SURFACE_FLOW_NAMES = {
    "openapi_documents": "openapi",
    "openapi_entity_links": "openapi_entity",
    "rest_endpoints": "rest_endpoint",
    "workflows": "workflow",
    "permissions": "permission",
    "database_consumers": "database",
    "entity_metadata": "entity_metadata",
    "actionui": "actionui",
    "actionui_artifacts": "actionui",
    "actionui_fields": "actionui",
    "actionui_events": "actionui",
    "nextgen": "nextgen",
    "incoming_relationships": "caller_chain",
    "outgoing_relationships": "dependency_chain",
}
_NON_FLOW_SURFACES = {
    "files",
    "symbols",
    "entity_symbol_links",
    "entity_occurrences",
    "source_diagnostics",
    "tests",
}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _report_status(
    *reports: Mapping[str, Any], gaps: list[Mapping[str, Any]] | None = None
) -> str:
    statuses = {str(report.get("status")) for report in reports}
    if "blocked" in statuses:
        return "blocked"
    if any(str(item.get("status")) == "blocked" for item in gaps or []):
        return "blocked"
    if gaps or statuses & {
        "partial",
        "empty",
        "deferred",
        "unavailable",
        "stale",
        "ambiguous",
        "unknown",
    }:
        return "partial"
    return "ready"


def _gap(
    code: str, stage: str, surface: str, subject: Any, **extra: Any
) -> dict[str, Any]:
    result = {
        "gap_code": code,
        "stage": stage,
        "surface": surface,
        "subject": subject,
    }
    result.update(extra)
    return result


def _report_gaps(report: Mapping[str, Any], stage: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in _list(report.get("gaps")):
        if isinstance(item, Mapping):
            result.append({"stage": stage, **dict(item)})
        else:
            result.append(
                _gap(
                    "reported_gap",
                    stage,
                    "analysis",
                    str(item),
                    status="unresolved",
                    consequence="the upstream report did not establish a positive claim",
                )
            )
    if report.get("status") == "blocked":
        error = _mapping(report.get("error"))
        result.append(
            _gap(
                f"{stage}_blocked",
                stage,
                "analysis",
                stage,
                status="blocked",
                consequence=str(
                    error.get("message") or "upstream analysis was blocked"
                ),
                remediation="rerun after the upstream prerequisite is restored",
            )
        )
    return result


def _changed_symbols(step3: Mapping[str, Any]) -> list[dict[str, Any]]:
    symbols: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any]] = set()
    for item in _list(step3.get("seed_symbols")):
        if not isinstance(item, Mapping):
            continue
        key = (item.get("symbol_id"), item.get("file_path"))
        if key in seen:
            continue
        seen.add(key)
        symbols.append(
            {
                "symbol_id": item.get("symbol_id"),
                "name": item.get("name"),
                "kind": item.get("kind"),
                "file_path": item.get("file_path"),
                "stable_key": item.get("stable_key"),
                "declaration_range": item.get("declaration_range"),
                "target_revision": item.get("fixture_target_revision"),
            }
        )
    return symbols


def _entities(
    step3: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    context = _mapping(step3.get("entity_context"))
    entities: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
    gaps: list[dict[str, Any]] = []
    mappings = _list(context.get("mappings"))
    if not mappings:
        gaps.append(
            _gap(
                "entity_mapping_missing",
                "blast_radius",
                "entity",
                "changed_symbols",
                status="missing",
                consequence="no literal .ent file can be confirmed from the catalog",
                remediation="materialize an exact reviewed symbol_entity_links contract for the target revision",
            )
        )
    for mapping in mappings:
        if not isinstance(mapping, Mapping):
            continue
        status = str(mapping.get("resolution_status") or "missing")
        source_path = mapping.get("entity_source_path")
        if status != "resolved":
            gaps.append(
                _gap(
                    "entity_mapping_unresolved",
                    "blast_radius",
                    "entity",
                    mapping.get("symbol_name") or mapping.get("symbol_id"),
                    status=status,
                    evidence={
                        "symbol_id": mapping.get("symbol_id"),
                        "resolution_reason": mapping.get("resolution_reason"),
                        "mapping_contract_sha256": mapping.get(
                            "mapping_contract_sha256"
                        ),
                    },
                    consequence="entity and downstream flow claims remain unconfirmed",
                    remediation="review the exact symbol-to-entity mapping",
                )
            )
            continue
        if not isinstance(source_path, str) or not source_path.lower().endswith(".ent"):
            gaps.append(
                _gap(
                    "entity_path_not_ent",
                    "blast_radius",
                    "entity",
                    mapping.get("symbol_name") or mapping.get("symbol_id"),
                    status="invalid",
                    evidence={"entity_source_path": source_path},
                    consequence="the resolved mapping does not identify a literal .ent file",
                    remediation="repair the catalog mapping contract",
                )
            )
            continue
        key = (
            mapping.get("entity_name"),
            source_path,
            mapping.get("entity_source_key"),
        )
        row = entities.setdefault(
            key,
            {
                "entity_name": mapping.get("entity_name"),
                "ent_file": source_path,
                "source_key": mapping.get("entity_source_key"),
                "entity_occurrence_id": mapping.get("entity_occurrence_id"),
                "entity_id": mapping.get("entity_id"),
                "status": "confirmed",
                "target_revision": mapping.get("target_revision"),
                "symbols": [],
                "evidence": [],
                "impact_facts": defaultdict(list),
            },
        )
        symbol = {
            "symbol_id": mapping.get("symbol_id"),
            "name": mapping.get("symbol_name"),
            "file_path": mapping.get("symbol_file_path"),
            "stable_key": mapping.get("symbol_stable_key"),
        }
        if symbol not in row["symbols"]:
            row["symbols"].append(symbol)
        row["evidence"].append(
            {
                "mapping_type": mapping.get("mapping_type"),
                "contract_entry_key": mapping.get("contract_entry_key"),
                "mapping_contract_sha256": mapping.get("mapping_contract_sha256"),
                "evidence": mapping.get("evidence"),
            }
        )
        for family, facts in _mapping(mapping.get("entity_impact_facts")).items():
            for fact in _list(facts):
                if isinstance(fact, Mapping):
                    row["impact_facts"][family].append(fact)
    normalized: list[dict[str, Any]] = []
    for row in entities.values():
        row["impact_facts"] = {
            key: value for key, value in sorted(row["impact_facts"].items())
        }
        normalized.append(dict(row))
    normalized.sort(
        key=lambda item: (str(item.get("ent_file")), str(item.get("source_key")))
    )
    return normalized, gaps


def _flows(
    step1: Mapping[str, Any],
    step3: Mapping[str, Any],
    entities: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for trace in _list(step1.get("direct_traces")):
        if not isinstance(trace, Mapping):
            continue
        surface = str(trace.get("surface") or "unknown")
        if surface in _NON_FLOW_SURFACES:
            continue
        flow_kind = _SURFACE_FLOW_NAMES.get(surface, surface)
        trace_status = str(trace.get("status") or "unresolved")
        flow_status = "confirmed" if trace_status == "available" else "candidate"
        if surface in {"incoming_relationships", "outgoing_relationships"}:
            flow_status = "candidate"
        if trace_status in {"deferred", "unavailable"}:
            flow_status = "deferred"
        for fact in _list(trace.get("facts")):
            if not isinstance(fact, Mapping):
                continue
            source_path = str(fact.get("source_path") or "<unknown>")
            key = (flow_kind, source_path, flow_status)
            row = grouped.setdefault(
                key,
                {
                    "flow_kind": flow_kind,
                    "status": flow_status,
                    "source_path": source_path,
                    "surface": surface,
                    "fact_count": 0,
                    "catalog_record_ids": [],
                    "evidence": [],
                },
            )
            row["fact_count"] += 1
            if fact.get("catalog_record_id") is not None:
                row["catalog_record_ids"].append(fact["catalog_record_id"])
            if len(row["evidence"]) < 3:
                row["evidence"].append(
                    {
                        "catalog_record_id": fact.get("catalog_record_id"),
                        "source_location": fact.get("source_location"),
                        "target_revision": fact.get("target_revision"),
                    }
                )
    for entity in entities:
        for family, facts in _mapping(entity.get("impact_facts")).items():
            flow_kind = _SURFACE_FLOW_NAMES.get(family, family)
            for fact in _list(facts):
                if not isinstance(fact, Mapping):
                    continue
                source_path = str(
                    fact.get("source_path")
                    or fact.get("source_path_resolved")
                    or fact.get("path")
                    or "<unknown>"
                )
                key = (flow_kind, source_path, "confirmed")
                row = grouped.setdefault(
                    key,
                    {
                        "flow_kind": flow_kind,
                        "status": "confirmed",
                        "source_path": source_path,
                        "surface": family,
                        "fact_count": 0,
                        "catalog_record_ids": [],
                        "evidence": [],
                    },
                )
                row["fact_count"] += 1
                if fact.get("id") is not None:
                    row["catalog_record_ids"].append(fact["id"])
                if len(row["evidence"]) < 3:
                    row["evidence"].append(
                        {
                            "catalog_record_id": fact.get("id"),
                            "source_location": fact.get("source_pointer"),
                            "target_revision": fact.get("source_commit_sha"),
                        }
                    )
    for symbol in _list(step3.get("reached_symbols")):
        if not isinstance(symbol, Mapping):
            continue
        source_path = str(symbol.get("file_path") or "<unknown>")
        key = ("caller_chain", source_path, "candidate")
        row = grouped.setdefault(
            key,
            {
                "flow_kind": "caller_chain",
                "status": "candidate",
                "source_path": source_path,
                "surface": "incoming_relationships",
                "fact_count": 0,
                "catalog_record_ids": [],
                "evidence": [],
            },
        )
        row["fact_count"] += 1
        row["evidence"].append(
            {
                "symbol_id": symbol.get("symbol_id"),
                "name": symbol.get("name"),
                "declaration_range": symbol.get("declaration_range"),
                "target_revision": symbol.get("fixture_target_revision"),
            }
        )
    return sorted(
        grouped.values(),
        key=lambda item: (
            str(item["flow_kind"]),
            str(item["source_path"]),
            str(item["status"]),
        ),
    )


def build_report(
    step0: Mapping[str, Any],
    step1: Mapping[str, Any],
    step2: Mapping[str, Any],
    step3: Mapping[str, Any],
    *,
    test_coverage: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the additive Step 4 report from existing evidence contracts."""
    entities, entity_gaps = _entities(step3)
    flows = _flows(step1, step3, entities)
    gaps = (
        _report_gaps(step1, "step1")
        + _report_gaps(step2, "step2")
        + _report_gaps(step3, "step3")
        + entity_gaps
    )
    coverage = dict(test_coverage or {})
    if not test_coverage:
        gaps.append(
            _gap(
                "test_coverage_not_run",
                "test_coverage",
                "tests",
                "downstream_repositories",
                status="deferred",
                consequence="existing test suites cannot be confirmed from this run",
                remediation="provide an exact downstream coverage catalog",
            )
        )
    for item in _list(coverage.get("gaps")):
        if isinstance(item, Mapping):
            gaps.append(dict(item))
    changed_files = _list(step0.get("changed_files"))
    ordered_gaps = sorted(
        gaps,
        key=lambda item: (
            str(item.get("stage", "")),
            str(item.get("gap_code", "")),
            str(item.get("subject", "")),
        ),
    )
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "analysis_kind": ANALYSIS_KIND,
        "status": _report_status(step1, step2, step3, coverage, gaps=ordered_gaps),
        "changed_scope": {
            "files": changed_files,
            "symbols": _changed_symbols(step3),
        },
        "entities": entities,
        "flows": flows,
        "test_coverage": coverage,
        "gaps": ordered_gaps,
        "provenance": {
            "source": "repo-v1 PR-impact Steps 0--3 and optional downstream coverage evidence",
            "read_only": True,
            "target_revision": _mapping(step1.get("preflight")).get("target_revision")
            or _mapping(step0.get("pull_request")).get("target_revision"),
            "catalog_revision": _mapping(step1.get("preflight")).get(
                "catalog_revision"
            ),
        },
    }
    return report
