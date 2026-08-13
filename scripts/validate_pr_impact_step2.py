#!/usr/bin/env python3
"""Validate a separately materialized Step 2 PR impact audit report."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from catalog.pr_impact_step2 import (
    EXPECTED_SURFACES,
    REPORT_SCHEMA_VERSION,
    STATUSES,
    SURFACE_DISPOSITIONS,
)

SHA256 = re.compile(r"^[0-9a-f]{64}$")
TOP_LEVEL_KEYS = {
    "schema_version",
    "analysis_kind",
    "status",
    "error",
    "input",
    "preflight",
    "step1_summary",
    "surface_audit",
    "gaps",
    "warnings",
    "provenance",
}


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate(report: object) -> list[str]:
    errors: list[str] = []
    if not isinstance(report, dict):
        return ["report must be a JSON object"]
    unexpected = sorted(set(report) - TOP_LEVEL_KEYS)
    if unexpected:
        errors.append("unexpected top-level sections: " + ", ".join(unexpected))
    if report.get("schema_version") != REPORT_SCHEMA_VERSION:
        errors.append(f"schema_version must be {REPORT_SCHEMA_VERSION}")
    if report.get("analysis_kind") != "pr_impact_step_2":
        errors.append("invalid analysis_kind")
    if report.get("status") not in STATUSES:
        errors.append("invalid status")
    for key in (
        "input",
        "preflight",
        "step1_summary",
        "surface_audit",
        "gaps",
        "warnings",
        "provenance",
    ):
        if key not in report:
            errors.append(f"missing section: {key}")

    status = report.get("status")
    if status == "blocked":
        error = report.get("error")
        if not isinstance(error, dict) or not isinstance(error.get("code"), str):
            errors.append("blocked report requires error.code")
    elif "error" in report:
        errors.append("non-blocked report must not contain error")

    input_data = report.get("input")
    if not isinstance(input_data, dict):
        errors.append("input must be an object")
    elif status != "blocked":
        for key in ("manifest", "repo_key", "base_revision", "target_revision"):
            if key not in input_data:
                errors.append(f"missing input field: {key}")

    preflight = report.get("preflight")
    if status != "blocked":
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
            if (
                isinstance(input_data, dict)
                and input_data.get("target_revision") is not None
                and input_data.get("target_revision")
                != preflight.get("target_revision")
            ):
                errors.append(
                    "preflight target revision differs from input target revision"
                )

    summary = report.get("step1_summary")
    if not isinstance(summary, dict):
        errors.append("step1_summary must be an object")
        summary = {}
    if summary.get("status") not in STATUSES:
        errors.append("step1_summary status is invalid")
    digest = summary.get("step1_report_sha256")
    if not isinstance(digest, str) or not SHA256.fullmatch(digest):
        errors.append("step1_summary step1_report_sha256 must be lowercase SHA-256")
    for key in ("surface_count", "fact_count", "available_surface_count"):
        if not _is_int(summary.get(key)) or summary[key] < 0:
            errors.append(f"step1_summary {key} must be a non-negative integer")

    audit = report.get("surface_audit")
    if not isinstance(audit, list):
        errors.append("surface_audit must be a list")
        audit = []
    if status == "blocked":
        if audit:
            errors.append("blocked report must not contain surface_audit rows")
    else:
        surfaces = [row.get("surface") for row in audit if isinstance(row, dict)]
        if surfaces != list(EXPECTED_SURFACES):
            errors.append(
                "surface_audit must contain exactly one row per expected surface in order"
            )
        if len(surfaces) != len(set(surfaces)):
            errors.append("surface_audit surfaces must be unique")
        for row in audit:
            if not isinstance(row, dict):
                errors.append("surface audit row must be an object")
                continue
            surface = row.get("surface")
            if surface not in EXPECTED_SURFACES:
                errors.append("surface audit has unexpected surface")
            row_status = row.get("status")
            if row_status not in SURFACE_DISPOSITIONS:
                errors.append("surface audit has invalid Step 1 status")
            elif row.get("disposition") != SURFACE_DISPOSITIONS[row_status]:
                errors.append("surface audit has invalid disposition")
            if not _is_int(row.get("fact_count")) or row["fact_count"] < 0:
                errors.append("surface audit fact_count must be a non-negative integer")
            if "facts" in row:
                errors.append("surface audit must not duplicate Step 1 facts")
            if surface == "entity_symbol_links":
                counts = row.get("link_status_counts")
                if not isinstance(counts, dict) or any(
                    not isinstance(key, str) or not isinstance(value, int) or value < 0
                    for key, value in counts.items()
                ):
                    errors.append(
                        "entity_symbol_links audit requires link_status_counts"
                    )

        if isinstance(summary, dict):
            if summary.get("surface_count") != len(EXPECTED_SURFACES):
                errors.append(
                    "step1_summary surface_count does not match expected surfaces"
                )
            if summary.get("fact_count") != sum(
                row.get("fact_count", 0) for row in audit if isinstance(row, dict)
            ):
                errors.append("step1_summary fact_count does not match surface audit")
            if summary.get("available_surface_count") != sum(
                row.get("status") == "available"
                for row in audit
                if isinstance(row, dict)
            ):
                errors.append(
                    "step1_summary available_surface_count does not match surface audit"
                )
        expected_status = (
            "complete"
            if all(
                isinstance(row, dict) and row.get("status") == "available"
                for row in audit
            )
            else "partial"
        )
        if status != expected_status:
            errors.append("status does not match audited surface availability")
        if summary.get("status") not in {"complete", "partial"}:
            errors.append(
                "non-blocked report requires non-blocked Step 1 summary status"
            )

    for key in ("gaps", "warnings"):
        value = report.get(key)
        if not isinstance(value, list) or any(
            not isinstance(item, str) for item in value
        ):
            errors.append(f"{key} must be a list of strings")
    provenance = report.get("provenance")
    if not isinstance(provenance, dict):
        errors.append("provenance must be an object")
    elif provenance.get("read_only") is not True:
        errors.append("provenance.read_only must be true")
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
