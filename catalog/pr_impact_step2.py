"""Read-only Step 2 evidence-availability audit for PR impact reports.

Step 2 is deliberately a thin layer over :mod:`catalog.pr_impact_step1`.
It runs Step 1 in-process, retains only report-level summaries, and never
re-queries or mutates the catalog.  The Step 1 report remains the source of
all direct-surface facts.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from catalog import pr_impact_step1
from scripts.validate_pr_impact_step1 import validate as validate_step1_report

REPORT_SCHEMA_VERSION = "0.1"
ANALYSIS_KIND = "pr_impact_step_2"
STATUSES = {"complete", "partial", "blocked"}
SURFACE_DISPOSITIONS = {
    "available": "covered",
    "empty": "defer_no_direct_rows",
    "deferred": "defer_missing_target_evidence",
    "unresolved": "needs_review",
    "ambiguous": "needs_review",
    "stale": "needs_review",
    "unavailable": "not_modelled",
}
EXPECTED_SURFACES = tuple(sorted(pr_impact_step1.SUPPORTED_SURFACES))


class Step2Error(Exception):
    """Raised for a malformed Step 1 result or invalid Step 2 input."""

    def __init__(self, code: str, message: str, **extra: Any) -> None:
        self.code, self.message, self.extra = code, message, extra
        super().__init__(message)


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def step1_report_sha256(report: Mapping[str, Any]) -> str:
    """Return the deterministic hash of the generated Step 1 JSON value."""

    return hashlib.sha256(_canonical_json(report).encode("utf-8")).hexdigest()


def _error_report(error: Step2Error) -> dict[str, Any]:
    return {"code": error.code, "message": error.message, **error.extra}


def _blocked_report(
    step1_report: Mapping[str, Any], error: Step2Error
) -> dict[str, Any]:
    report_hash = step1_report_sha256(step1_report)
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "analysis_kind": ANALYSIS_KIND,
        "status": "blocked",
        "error": _error_report(error),
        "input": dict(step1_report.get("input", {}))
        if isinstance(step1_report.get("input"), Mapping)
        else {},
        "preflight": dict(step1_report.get("preflight", {}))
        if isinstance(step1_report.get("preflight"), Mapping)
        else {},
        "step1_summary": {
            "status": "blocked",
            "step1_report_sha256": report_hash,
            "surface_count": 0,
            "fact_count": 0,
            "available_surface_count": 0,
        },
        "surface_audit": [],
        "gaps": [],
        "warnings": [],
        "provenance": {
            "source": "generated Step 1 report",
            "step1_report_sha256": report_hash,
            "read_only": True,
            "catalog_mutation": "none",
            "scope": "Step 1 direct-surface evidence availability only",
        },
    }


def blocked_report(error: Step2Error) -> dict[str, Any]:
    """Return a stable blocked envelope when Step 2 cannot run Step 1."""

    empty_step1 = {
        "schema_version": pr_impact_step1.REPORT_SCHEMA_VERSION,
        "analysis_kind": "pr_impact_step_1",
        "status": "blocked",
        "error": {"code": error.code, "message": error.message, **error.extra},
        "input": {},
        "preflight": {},
        "changed_files": [],
        "direct_traces": [],
        "pr_metadata": {"status": "not_provided"},
        "downstream_repositories": [],
        "impact_ranking": [],
        "gaps": [],
        "warnings": [],
        "confidence": {
            "status": "not_computed",
            "score": None,
            "reason": "analysis blocked before direct evidence collection",
        },
        "provenance": {"read_only": True},
    }
    return _blocked_report(empty_step1, error)


def _step1_error(errors: list[str]) -> Step2Error:
    return Step2Error(
        "step1_report_invalid",
        "generated Step 1 report failed its contract validation",
        validation_errors=errors,
    )


def _require_exact_preflight(report: Mapping[str, Any]) -> None:
    preflight = report.get("preflight")
    if not isinstance(preflight, Mapping):
        raise Step2Error("step1_report_invalid", "Step 1 preflight must be an object")
    required = (
        "target_revision",
        "catalog_revision",
        "revision_relation",
        "compatibility_evidence",
    )
    if any(key not in preflight for key in required):
        raise Step2Error(
            "step1_report_invalid", "Step 1 preflight is missing exact-target fields"
        )
    if preflight.get("revision_relation") != "exact":
        raise Step2Error(
            "catalog_revision_mismatch",
            "Step 2 requires exact Step 1 catalog preflight",
            target_revision=preflight.get("target_revision"),
            catalog_revision=preflight.get("catalog_revision"),
        )
    if preflight.get("catalog_revision") != preflight.get("target_revision"):
        raise Step2Error(
            "catalog_revision_mismatch",
            "Step 1 catalog revision must equal the fixture target revision",
            target_revision=preflight.get("target_revision"),
            catalog_revision=preflight.get("catalog_revision"),
        )
    input_data = report.get("input")
    if (
        isinstance(input_data, Mapping)
        and input_data.get("target_revision") is not None
        and input_data.get("target_revision") != preflight.get("target_revision")
    ):
        raise Step2Error(
            "catalog_revision_mismatch",
            "Step 1 input target revision differs from its preflight target",
            target_revision=input_data.get("target_revision"),
            catalog_revision=preflight.get("catalog_revision"),
        )


def _audit_rows(step1_report: Mapping[str, Any]) -> list[dict[str, Any]]:
    traces = step1_report.get("direct_traces")
    if not isinstance(traces, list):
        raise Step2Error("step1_report_invalid", "Step 1 direct_traces must be a list")
    by_surface: dict[str, Mapping[str, Any]] = {}
    for trace in traces:
        if not isinstance(trace, Mapping):
            raise Step2Error(
                "step1_report_invalid", "Step 1 direct trace must be an object"
            )
        surface = trace.get("surface")
        if surface in by_surface:
            raise Step2Error(
                "step1_report_invalid", "Step 1 direct traces must be unique"
            )
        if surface not in EXPECTED_SURFACES:
            raise Step2Error(
                "step1_report_invalid", "Step 1 direct trace has an unexpected surface"
            )
        by_surface[str(surface)] = trace
    missing = sorted(set(EXPECTED_SURFACES) - set(by_surface))
    if missing:
        raise Step2Error(
            "step1_report_invalid",
            "Step 1 report does not contain all expected direct surfaces",
            missing_surfaces=missing,
        )

    rows: list[dict[str, Any]] = []
    for surface in EXPECTED_SURFACES:
        trace = by_surface[surface]
        status = trace.get("status")
        facts = trace.get("facts")
        if status not in SURFACE_DISPOSITIONS:
            raise Step2Error(
                "step1_report_invalid",
                f"Step 1 surface {surface} has an invalid status",
            )
        if not isinstance(facts, list):
            raise Step2Error(
                "step1_report_invalid",
                f"Step 1 surface {surface} facts must be a list",
            )
        row: dict[str, Any] = {
            "surface": surface,
            "status": status,
            "disposition": SURFACE_DISPOSITIONS[status],
            "fact_count": len(facts),
        }
        if surface == "entity_symbol_links":
            counts: dict[str, int] = {}
            for fact in facts:
                if isinstance(fact, Mapping):
                    key = str(fact.get("resolution_status") or "missing")
                    counts[key] = counts.get(key, 0) + 1
            row["link_status_counts"] = dict(sorted(counts.items()))
        if trace.get("warning") is not None:
            row["warning"] = trace["warning"]
        rows.append(row)
    return rows


def _successful_report(step1_report: Mapping[str, Any]) -> dict[str, Any]:
    if step1_report.get("status") not in {"complete", "partial"}:
        raise Step2Error(
            "step1_report_invalid", "Step 1 status must be complete or partial"
        )
    _require_exact_preflight(step1_report)
    errors = validate_step1_report(step1_report)
    if errors:
        raise _step1_error(errors)
    audit = _audit_rows(step1_report)
    report_hash = step1_report_sha256(step1_report)
    fact_count = sum(row["fact_count"] for row in audit)
    available_count = sum(row["status"] == "available" for row in audit)
    covered = available_count == len(EXPECTED_SURFACES)
    gaps = [
        f"{row['surface']}: {row['disposition']}"
        for row in audit
        if row["disposition"] != "covered"
    ]
    warnings = step1_report.get("warnings", [])
    if not isinstance(warnings, list):
        raise Step2Error("step1_report_invalid", "Step 1 warnings must be a list")
    input_data = step1_report.get("input")
    preflight = step1_report.get("preflight")
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "analysis_kind": ANALYSIS_KIND,
        "status": "complete" if covered else "partial",
        "input": dict(input_data),
        "preflight": dict(preflight),
        "step1_summary": {
            "status": step1_report["status"],
            "step1_report_sha256": report_hash,
            "surface_count": len(audit),
            "fact_count": fact_count,
            "available_surface_count": available_count,
        },
        "surface_audit": audit,
        "gaps": gaps,
        "warnings": sorted(str(item) for item in warnings),
        "provenance": {
            "source": "generated Step 1 report",
            "step1_report_sha256": report_hash,
            "read_only": True,
            "catalog_mutation": "none",
            "scope": "Step 1 direct-surface evidence availability only",
            "deferred": [
                "MCP-specific tracing",
                "automated-test discovery",
                "downstream repositories",
                "graph",
                "delta",
                "multi-repository analysis",
            ],
        },
    }


def analyze_fixture(
    fixture: str | Path,
    manifest: str | Path,
    active_db: str | Path,
    repo_key: str,
) -> dict[str, Any]:
    """Run Step 1 in-process and produce the Step 2 audit report."""

    try:
        step1_report = pr_impact_step1.analyze_fixture(
            fixture, manifest, active_db, repo_key
        )
    except pr_impact_step1.Step1Error as exc:
        step1_report = pr_impact_step1.blocked_report(exc)
    except Exception as exc:  # noqa: BLE001 - stable operator report envelope
        step1_report = pr_impact_step1.blocked_report(
            pr_impact_step1.Step1Error("step1_failure", str(exc))
        )
    if not isinstance(step1_report, Mapping):
        return blocked_report(
            Step2Error(
                "step1_report_invalid", "Step 1 analyzer did not return an object"
            )
        )
    if step1_report.get("status") == "blocked":
        error = step1_report.get("error")
        if isinstance(error, Mapping) and isinstance(error.get("code"), str):
            return _blocked_report(
                step1_report,
                Step2Error(
                    error["code"],
                    str(error.get("message", "Step 1 analysis blocked")),
                    **{
                        str(key): value
                        for key, value in error.items()
                        if key not in {"code", "message"}
                    },
                ),
            )
        return _blocked_report(
            step1_report,
            Step2Error(
                "step1_report_invalid", "blocked Step 1 report requires error.code"
            ),
        )
    try:
        return _successful_report(step1_report)
    except Step2Error as exc:
        return _blocked_report(step1_report, exc)


def _text(value: Any, fallback: str = "Not available") -> str:
    if value is None or value == "":
        return fallback
    if isinstance(value, (dict, list)):
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    return str(value)


def render_review_markdown(report: Mapping[str, Any]) -> str:
    """Render only facts present in a Step 2 report."""

    if not isinstance(report, Mapping):
        report = {}
    input_data = report.get("input") if isinstance(report.get("input"), Mapping) else {}
    summary = (
        report.get("step1_summary")
        if isinstance(report.get("step1_summary"), Mapping)
        else {}
    )
    lines = [
        "# PR Impact Step 2 Audit",
        "",
        f"- Status: {_text(report.get('status'))}",
        f"- Repository key: {_text(input_data.get('repo_key'), 'Unknown')}",
        f"- Base revision: {_text(input_data.get('base_revision'), 'Unknown')}",
        f"- Target revision: {_text(input_data.get('target_revision'), 'Unknown')}",
        f"- Step 1 report SHA-256: {_text(summary.get('step1_report_sha256'))}",
        f"- Step 1 status: {_text(summary.get('status'))}",
        f"- Step 1 fact count: {_text(summary.get('fact_count'))}",
        "",
        "## Surface audit",
        "",
        "| Surface | Step 1 status | Disposition | Fact count | Note |",
        "| --- | --- | --- | ---: | --- |",
    ]
    audit = report.get("surface_audit")
    rows = audit if isinstance(audit, list) else []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        note = _text(row.get("warning"), "")
        lines.append(
            f"| `{_text(row.get('surface'))}` | {_text(row.get('status'))} | "
            f"{_text(row.get('disposition'))} | {_text(row.get('fact_count'))} | {note} |"
        )
    if not rows:
        lines.append(
            "| Not available | Not available | Not available | Not available | Not available |"
        )
    lines.extend(["", "## Gaps", ""])
    gaps = report.get("gaps")
    if isinstance(gaps, list) and gaps:
        lines.extend(f"- {_text(item)}" for item in gaps)
    else:
        lines.append("- None reported")
    error = report.get("error")
    if isinstance(error, Mapping):
        lines.extend(
            [
                "",
                "## Error",
                "",
                f"- Code: {_text(error.get('code'))}",
                f"- Message: {_text(error.get('message'))}",
            ]
        )
    return "\n".join(lines) + "\n"
