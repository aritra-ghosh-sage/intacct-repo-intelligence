#!/usr/bin/env python3
"""Validate a separately materialized Step 1 JSON report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

STATUSES = {"complete", "partial", "blocked"}
SCHEMA_VERSION = "0.4"
TOP_LEVEL_KEYS = {
    "schema_version",
    "analysis_kind",
    "status",
    "error",
    "input",
    "preflight",
    "changed_files",
    "direct_traces",
    "pr_metadata",
    "downstream_repositories",
    "impact_ranking",
    "gaps",
    "warnings",
    "confidence",
    "provenance",
}
SURFACE_STATUSES = {
    "available",
    "empty",
    "unavailable",
    "unresolved",
    "ambiguous",
    "stale",
    "deferred",
}
SUPPORTED_SURFACES = {
    "files",
    "symbols",
    "entity_symbol_links",
    "outgoing_relationships",
    "incoming_relationships",
    "entity_occurrences",
    "openapi_documents",
    "openapi_entity_links",
    "rest_endpoints",
    "actionui",
    "actionui_artifacts",
    "actionui_fields",
    "actionui_events",
    "actionui_includes",
    "nextgen",
    "nextgen_artifacts",
    "source_diagnostics",
    "database_consumers",
    "entity_metadata",
    "permissions",
    "workflows",
    "tests",
}
UNSUPPORTED_SURFACES: set[str] = set()
EXPECTED_SURFACES = SUPPORTED_SURFACES | UNSUPPORTED_SURFACES


def validate(report: object) -> list[str]:
    errors: list[str] = []
    if not isinstance(report, dict):
        return ["report must be a JSON object"]
    unexpected = sorted(set(report) - TOP_LEVEL_KEYS)
    if unexpected:
        errors.append("unexpected top-level sections: " + ", ".join(unexpected))
    if report.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    if report.get("analysis_kind") != "pr_impact_step_1":
        errors.append("invalid analysis_kind")
    if report.get("status") not in STATUSES:
        errors.append("invalid status")
    for key in (
        "input",
        "preflight",
        "changed_files",
        "direct_traces",
        "pr_metadata",
        "downstream_repositories",
        "impact_ranking",
        "gaps",
        "warnings",
        "confidence",
        "provenance",
    ):
        if key not in report:
            errors.append(f"missing section: {key}")
    changed_files = report.get("changed_files")
    if not isinstance(changed_files, list):
        errors.append("changed_files must be a list")
    elif report.get("status") != "blocked" and not changed_files:
        errors.append("changed_files must be non-empty unless blocked")
    elif isinstance(changed_files, list):
        for item in changed_files:
            if not isinstance(item, dict):
                errors.append("changed file must be an object")
            elif not isinstance(item.get("path"), str) or not item["path"]:
                errors.append("changed file requires path")
            elif not isinstance(item.get("status"), str) or not item["status"]:
                errors.append("changed file requires status")
    if isinstance(report.get("input"), dict):
        for key in (
            "manifest",
            "repo_key",
            "repo_root",
            "base_revision",
            "target_revision",
        ):
            if key not in report["input"] and report.get("status") != "blocked":
                errors.append(f"missing input field: {key}")
    preflight = report.get("preflight")
    if report.get("status") != "blocked":
        if not isinstance(preflight, dict):
            errors.append("preflight must be an object")
        else:
            for key in (
                "target_revision",
                "catalog_revision",
                "revision_relation",
                "compatibility_evidence",
            ):
                if key not in preflight:
                    errors.append(f"missing preflight field: {key}")
            if preflight.get("revision_relation") != "exact":
                errors.append("invalid preflight revision_relation")
            if preflight.get("catalog_revision") != preflight.get("target_revision"):
                errors.append("exact preflight relation requires matching revisions")
    confidence = report.get("confidence")
    if not isinstance(confidence, dict):
        errors.append("confidence must be an object")
    elif confidence.get("status") not in {"computed", "not_computed"}:
        errors.append("confidence status must be computed or not_computed")
    elif confidence.get("status") == "computed":
        score = confidence.get("score")
        if (
            isinstance(score, bool)
            or not isinstance(score, int)
            or not 0 <= score <= 100
        ):
            errors.append("computed confidence score must be an integer from 0 to 100")
        components = confidence.get("components")
        if not isinstance(components, dict):
            errors.append("computed confidence requires components")
        else:
            for key in (
                "evidence_availability",
                "evidence_freshness",
                "unresolved_gaps",
            ):
                if not isinstance(components.get(key), dict):
                    errors.append(f"confidence component missing: {key}")
    elif confidence.get("score") is not None:
        errors.append("not_computed confidence score must be null")
    if (
        isinstance(confidence, dict)
        and confidence.get("status") == "not_computed"
        and not isinstance(confidence.get("reason"), str)
    ):
        errors.append("not_computed confidence requires reason")
    downstream = report.get("downstream_repositories")
    if downstream != []:
        errors.append("downstream_repositories must be an empty list")
    metadata = report.get("pr_metadata")
    if not isinstance(metadata, dict) or metadata.get("status") not in {
        "not_provided",
        "available",
    }:
        errors.append("pr_metadata must have status not_provided or available")
    elif metadata.get("status") == "available":
        for key in (
            "repository",
            "repo_key",
            "number",
            "base_revision",
            "target_revision",
            "provider",
        ):
            if key not in metadata:
                errors.append(f"available pr_metadata missing {key}")
    traces = report.get("direct_traces")
    if not isinstance(traces, list):
        errors.append("direct_traces must be a list")
        traces = []
    seen_surfaces: set[str] = set()
    for trace in traces:
        if not isinstance(trace, dict):
            errors.append("direct trace must be an object")
            continue
        surface = trace.get("surface")
        if not isinstance(surface, str) or surface not in EXPECTED_SURFACES:
            errors.append("direct trace has unexpected surface")
        elif surface in seen_surfaces:
            errors.append("direct traces must contain unique surfaces")
        else:
            seen_surfaces.add(surface)
        if trace.get("status") not in SURFACE_STATUSES:
            errors.append("direct trace has invalid status")
        if not isinstance(trace.get("facts"), list):
            errors.append("direct trace facts must be a list")
        if (
            isinstance(trace, dict)
            and trace.get("status") == "empty"
            and not trace.get("warning")
        ):
            errors.append("empty trace must include a warning")
        if (
            isinstance(trace, dict)
            and trace.get("status") in {"unresolved", "ambiguous", "stale"}
            and not trace.get("warning")
        ):
            errors.append("classified trace must include a warning")
        if isinstance(trace, dict):
            for fact in trace.get("facts", []):
                if not isinstance(fact, dict):
                    errors.append("direct trace fact must be an object")
                elif fact.get("catalog_record_id") is None and not fact.get("fact_key"):
                    errors.append(
                        "direct trace fact needs catalog_record_id or fact_key"
                    )
            if (
                trace.get("surface") == "database_consumers"
                and trace.get("status") == "available"
            ) and (not trace.get("facts") or any(
                not isinstance(fact, dict) or fact.get("catalog_record_id") is None
                for fact in trace.get("facts", [])
            )):
                errors.append(
                    "available database_consumers requires direct catalog facts"
                )
            if (
                trace.get("surface") == "entity_metadata"
                and trace.get("status") == "available"
            ) and (not trace.get("facts") or any(
                not isinstance(fact, dict) or fact.get("catalog_record_id") is None
                for fact in trace.get("facts", [])
            )):
                errors.append(
                    "available entity_metadata requires direct catalog facts"
                )
    ranking = report.get("impact_ranking")
    if not isinstance(ranking, list):
        errors.append("impact_ranking must be a list")
    else:
        for expected_rank, item in enumerate(ranking, start=1):
            if not isinstance(item, dict):
                errors.append("impact ranking item must be an object")
                continue
            if item.get("rank") != expected_rank:
                errors.append("impact ranking ranks must be contiguous")
            if not isinstance(item.get("source_path"), str) or not item["source_path"]:
                errors.append("impact ranking source_path must be non-empty")
            if (
                not isinstance(item.get("distinct_surface_count"), int)
                or item["distinct_surface_count"] <= 0
            ):
                errors.append("impact ranking distinct_surface_count must be positive")
            if not isinstance(item.get("fact_count"), int) or item["fact_count"] <= 0:
                errors.append("impact ranking fact_count must be positive")
            if not isinstance(item.get("changed_file"), bool):
                errors.append("impact ranking changed_file must be boolean")
            if not isinstance(item.get("surfaces"), list) or len(
                item["surfaces"]
            ) != len(set(item["surfaces"])):
                errors.append("impact ranking surfaces must be a unique list")
            elif any(surface not in EXPECTED_SURFACES for surface in item["surfaces"]):
                errors.append("impact ranking contains an unexpected surface")
            if not isinstance(item.get("fact_keys"), list) or len(
                item["fact_keys"]
            ) != len(set(item["fact_keys"])):
                errors.append("impact ranking fact_keys must be a unique list")
    if report.get("status") == "complete":
        traces = report.get("direct_traces")
        if not isinstance(traces, list):
            errors.append("complete report requires direct_traces")
        else:
            by_surface = {
                trace.get("surface"): trace
                for trace in traces
                if isinstance(trace, dict) and isinstance(trace.get("surface"), str)
            }
            missing = sorted(EXPECTED_SURFACES - set(by_surface))
            unexpected = sorted(set(by_surface) - EXPECTED_SURFACES)
            if missing:
                errors.append(
                    f"complete report is missing direct traces: {', '.join(missing)}"
                )
            if unexpected:
                errors.append(
                    f"complete report has unexpected direct traces: {', '.join(unexpected)}"
                )
            for surface in sorted(SUPPORTED_SURFACES):
                if (
                    surface in by_surface
                    and by_surface[surface].get("status") != "available"
                ):
                    errors.append(
                        "complete report contains a supported direct-trace gap"
                    )
            for surface in sorted(UNSUPPORTED_SURFACES):
                if (
                    surface in by_surface
                    and by_surface[surface].get("status") != "unavailable"
                ):
                    errors.append(
                        "complete report has an invalid unsupported-surface status"
                    )
    if report.get("status") == "blocked" and (not isinstance(report.get("error"), dict) or not report["error"].get("code")):
        errors.append("blocked report requires error.code")
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
        print("\n".join(errors), file=sys.stderr)
        return 2
    print("valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
