#!/usr/bin/env python3
"""Validate a separately materialized Step 1 JSON report."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

STATUSES = {"complete", "partial", "blocked"}
SCHEMA_VERSION = "0.3"
SURFACE_STATUSES = {"available", "empty", "unavailable", "unresolved", "ambiguous", "stale", "deferred"}
SUPPORTED_SURFACES = {
    "files", "symbols", "outgoing_relationships", "incoming_relationships", "entity_occurrences",
    "openapi_documents", "openapi_entity_links", "rest_endpoints", "actionui", "actionui_artifacts",
    "actionui_fields", "actionui_events", "actionui_includes", "nextgen", "nextgen_artifacts",
    "source_diagnostics", "database_consumers", "entity_metadata", "permissions", "workflows", "tests",
}
UNSUPPORTED_SURFACES: set[str] = set()
EXPECTED_SURFACES = SUPPORTED_SURFACES | UNSUPPORTED_SURFACES


def validate(report: object) -> list[str]:
    errors: list[str] = []
    if not isinstance(report, dict): return ["report must be a JSON object"]
    if report.get("schema_version") != SCHEMA_VERSION: errors.append(f"schema_version must be {SCHEMA_VERSION}")
    if report.get("analysis_kind") != "pr_impact_step_1": errors.append("invalid analysis_kind")
    if report.get("status") not in STATUSES: errors.append("invalid status")
    for key in ("input", "preflight", "changed_files", "direct_traces", "pr_metadata", "onboarding_feasibility", "impact_ranking", "gaps", "warnings", "provenance"):
        if key not in report: errors.append(f"missing section: {key}")
    if isinstance(report.get("input"), dict):
        for key in ("manifest", "repo_key", "repo_root", "base_revision", "target_revision"):
            if key not in report["input"] and report.get("status") != "blocked":
                errors.append(f"missing input field: {key}")
    metadata = report.get("pr_metadata")
    if not isinstance(metadata, dict) or metadata.get("status") not in {"not_provided", "available"}:
        errors.append("pr_metadata must have status not_provided or available")
    elif metadata.get("status") == "available":
        for key in ("repository", "repo_key", "number", "base_revision", "target_revision", "provider"):
            if key not in metadata:
                errors.append(f"available pr_metadata missing {key}")
    for trace in report.get("direct_traces", []):
        if not isinstance(trace, dict) or trace.get("status") not in SURFACE_STATUSES:
            errors.append("direct trace has invalid status")
        if isinstance(trace, dict) and trace.get("status") == "empty" and not trace.get("warning"):
            errors.append("empty trace must include a warning")
        if isinstance(trace, dict) and trace.get("status") in {"unresolved", "ambiguous", "stale"} and not trace.get("warning"):
            errors.append("classified trace must include a warning")
        if isinstance(trace, dict):
            for fact in trace.get("facts", []):
                if not isinstance(fact, dict):
                    errors.append("direct trace fact must be an object")
                elif fact.get("catalog_record_id") is None and not fact.get("fact_key"):
                    errors.append("direct trace fact needs catalog_record_id or fact_key")
            if trace.get("surface") == "database_consumers" and trace.get("status") == "available":
                if not trace.get("facts") or any(
                    not isinstance(fact, dict) or fact.get("catalog_record_id") is None
                    for fact in trace.get("facts", [])
                ):
                    errors.append("available database_consumers requires direct catalog facts")
            if trace.get("surface") == "entity_metadata" and trace.get("status") == "available":
                if not trace.get("facts") or any(
                    not isinstance(fact, dict) or fact.get("catalog_record_id") is None
                    for fact in trace.get("facts", [])
                ):
                    errors.append("available entity_metadata requires direct catalog facts")
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
            if not isinstance(item.get("distinct_surface_count"), int) or item["distinct_surface_count"] <= 0:
                errors.append("impact ranking distinct_surface_count must be positive")
            if not isinstance(item.get("fact_count"), int) or item["fact_count"] <= 0:
                errors.append("impact ranking fact_count must be positive")
            if not isinstance(item.get("changed_file"), bool):
                errors.append("impact ranking changed_file must be boolean")
            if not isinstance(item.get("surfaces"), list) or len(item["surfaces"]) != len(set(item["surfaces"])):
                errors.append("impact ranking surfaces must be a unique list")
            elif any(surface not in EXPECTED_SURFACES for surface in item["surfaces"]):
                errors.append("impact ranking contains an unexpected surface")
            if not isinstance(item.get("fact_keys"), list) or len(item["fact_keys"]) != len(set(item["fact_keys"])):
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
                errors.append(f"complete report is missing direct traces: {', '.join(missing)}")
            if unexpected:
                errors.append(f"complete report has unexpected direct traces: {', '.join(unexpected)}")
            for surface in sorted(SUPPORTED_SURFACES):
                if surface in by_surface and by_surface[surface].get("status") != "available":
                    errors.append("complete report contains a supported direct-trace gap")
            for surface in sorted(UNSUPPORTED_SURFACES):
                if surface in by_surface and by_surface[surface].get("status") != "unavailable":
                    errors.append("complete report has an invalid unsupported-surface status")
    if report.get("status") == "blocked":
        if not isinstance(report.get("error"), dict) or not report["error"].get("code"):
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
