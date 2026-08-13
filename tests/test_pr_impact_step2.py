from __future__ import annotations

import copy
import json
from pathlib import Path

from catalog import pr_impact_step2
from scripts import trace_pr_impact_step2
from scripts.validate_pr_impact_step2 import validate


def step1_report(*, status: str = "partial") -> dict:
    surfaces = []
    for index, surface in enumerate(pr_impact_step2.EXPECTED_SURFACES):
        row = {"surface": surface, "status": "available", "facts": []}
        if surface == "symbols":
            row["facts"] = [{"catalog_record_id": 7, "source_path": "a.php"}]
        if surface in {"database_consumers", "entity_metadata"}:
            row["facts"] = [{"catalog_record_id": index + 1}]
        if surface == "tests":
            row = {
                "surface": surface,
                "status": "deferred",
                "facts": [{"fact_key": "step0:test_obligations.unresolved:0"}],
                "warning": "Exact target-revision test evidence is unavailable",
            }
        if index == 0:
            row["warning"] = row.get("warning", "Step 1 warning")
        surfaces.append(row)
    if status == "complete":
        for row in surfaces:
            row["status"] = "available"
            row["facts"] = []
            row.pop("warning", None)
        for row in surfaces:
            if row["surface"] in {"database_consumers", "entity_metadata"}:
                row["facts"] = [{"catalog_record_id": 1}]
    return {
        "schema_version": "0.4",
        "analysis_kind": "pr_impact_step_1",
        "status": status,
        "input": {
            "fixture": "fixture.yaml",
            "manifest": "manifest.yaml",
            "repo_root": "repo",
            "active_db": "catalog.db",
            "repo_key": "ia-main",
            "base_revision": "b" * 40,
            "target_revision": "t" * 40,
        },
        "preflight": {
            "target_revision": "t" * 40,
            "catalog_revision": "t" * 40,
            "revision_relation": "exact",
            "compatibility_evidence": "catalog target equals fixture target",
        },
        "changed_files": [{"path": "a.php", "status": "modified"}],
        "direct_traces": surfaces,
        "pr_metadata": {"status": "not_provided"},
        "downstream_repositories": [],
        "impact_ranking": [],
        "gaps": ["tests: deferred"] if status == "partial" else [],
        "warnings": ["Exact target-revision test evidence is unavailable"]
        if status == "partial"
        else [],
        "confidence": {
            "status": "computed",
            "score": 80,
            "components": {
                "evidence_availability": {},
                "evidence_freshness": {},
                "unresolved_gaps": {},
            },
        },
        "provenance": {"read_only": True},
    }


def test_exact_target_partial_audits_all_surfaces_without_facts(monkeypatch) -> None:
    source = step1_report()
    monkeypatch.setattr(
        pr_impact_step2.pr_impact_step1, "analyze_fixture", lambda *args: source
    )
    report = pr_impact_step2.analyze_fixture("fixture", "manifest", "db", "ia-main")

    assert report["status"] == "partial"
    assert len(report["surface_audit"]) == len(pr_impact_step2.EXPECTED_SURFACES)
    assert report["step1_summary"]["status"] == "partial"
    assert report["step1_summary"]["fact_count"] == 4
    assert all("facts" not in row for row in report["surface_audit"])
    tests_row = next(
        row for row in report["surface_audit"] if row["surface"] == "tests"
    )
    assert tests_row["disposition"] == "defer_missing_target_evidence"
    assert validate(report) == []


def test_exact_target_complete_report_is_complete(monkeypatch) -> None:
    source = step1_report(status="complete")
    monkeypatch.setattr(
        pr_impact_step2.pr_impact_step1, "analyze_fixture", lambda *args: source
    )
    report = pr_impact_step2.analyze_fixture("fixture", "manifest", "db", "ia-main")

    assert report["status"] == "complete"
    assert all(row["disposition"] == "covered" for row in report["surface_audit"])
    assert validate(report) == []


def test_blocked_step1_propagates_exact_error(monkeypatch) -> None:
    source = copy.deepcopy(step1_report())
    source["status"] = "blocked"
    source["error"] = {
        "code": "catalog_revision_mismatch",
        "message": "catalog target SHA must equal the fixture target revision",
    }
    monkeypatch.setattr(
        pr_impact_step2.pr_impact_step1, "analyze_fixture", lambda *args: source
    )
    report = pr_impact_step2.analyze_fixture("fixture", "manifest", "db", "ia-main")

    assert report["status"] == "blocked"
    assert report["error"]["code"] == "catalog_revision_mismatch"
    assert report["surface_audit"] == []
    assert validate(report) == []


def test_stale_forward_and_diverged_preflight_block(monkeypatch) -> None:
    for revision in ("s" * 40, "f" * 40, "d" * 40):
        source = step1_report(status="complete")
        source["preflight"]["catalog_revision"] = revision
        source["preflight"]["revision_relation"] = "stale"
        monkeypatch.setattr(
            pr_impact_step2.pr_impact_step1,
            "analyze_fixture",
            lambda *args, source=source: source,
        )
        report = pr_impact_step2.analyze_fixture("fixture", "manifest", "db", "ia-main")
        assert report["status"] == "blocked"
        assert report["error"]["code"] == "catalog_revision_mismatch"


def test_missing_step1_sections_block(monkeypatch) -> None:
    source = step1_report(status="complete")
    del source["direct_traces"]
    monkeypatch.setattr(
        pr_impact_step2.pr_impact_step1, "analyze_fixture", lambda *args: source
    )
    report = pr_impact_step2.analyze_fixture("fixture", "manifest", "db", "ia-main")
    assert report["status"] == "blocked"
    assert report["error"]["code"] == "step1_report_invalid"


def test_all_disposition_mappings_are_preserved() -> None:
    source = step1_report(status="partial")
    for row, status in zip(
        source["direct_traces"],
        (
            "available",
            "empty",
            "deferred",
            "unresolved",
            "ambiguous",
            "stale",
            "unavailable",
        )
        * 3,
    ):
        row["status"] = status
        row["facts"] = []
        if status in {"empty", "deferred", "unresolved", "ambiguous", "stale"}:
            row["warning"] = "reported warning"
    report = pr_impact_step2._successful_report(source)
    by_surface = {row["surface"]: row for row in report["surface_audit"]}
    assert by_surface[pr_impact_step2.EXPECTED_SURFACES[0]]["disposition"] == "covered"
    assert {row["disposition"] for row in report["surface_audit"]} == {
        "covered",
        "defer_no_direct_rows",
        "defer_missing_target_evidence",
        "needs_review",
        "not_modelled",
    }


def test_repeated_json_and_markdown_are_deterministic(monkeypatch) -> None:
    source = step1_report()
    monkeypatch.setattr(
        pr_impact_step2.pr_impact_step1, "analyze_fixture", lambda *args: source
    )
    first = pr_impact_step2.analyze_fixture("fixture", "manifest", "db", "ia-main")
    second = pr_impact_step2.analyze_fixture("fixture", "manifest", "db", "ia-main")
    assert json.dumps(first, sort_keys=True, indent=2) == json.dumps(
        second, sort_keys=True, indent=2
    )
    assert pr_impact_step2.render_review_markdown(
        first
    ) == pr_impact_step2.render_review_markdown(second)
    assert "catalog_record_id" not in pr_impact_step2.render_review_markdown(first)


def test_validator_rejects_unexpected_duplicate_invalid_and_nonexact_rows() -> None:
    source = step1_report(status="complete")
    report = pr_impact_step2._successful_report(source)
    report["surface_audit"].append(dict(report["surface_audit"][0]))
    assert any("exactly one row" in error for error in validate(report))

    report = pr_impact_step2._successful_report(source)
    report["surface_audit"][0]["surface"] = "unexpected"
    assert any("unexpected surface" in error for error in validate(report))

    report = pr_impact_step2._successful_report(source)
    report["surface_audit"][0]["disposition"] = "no_impact"
    assert any("invalid disposition" in error for error in validate(report))

    report = pr_impact_step2._successful_report(source)
    report["preflight"]["catalog_revision"] = "x" * 40
    assert any("exact preflight relation" in error for error in validate(report))


def test_cli_markdown_uses_only_the_step2_report(monkeypatch, capsys) -> None:
    report = pr_impact_step2._successful_report(step1_report(status="complete"))
    monkeypatch.setattr(trace_pr_impact_step2, "analyze_fixture", lambda *args: report)
    assert (
        trace_pr_impact_step2.main(
            [
                "--fixture",
                "fixture.yaml",
                "--manifest",
                "manifest.yaml",
                "--active-db",
                "catalog.db",
                "--repo-key",
                "ia-main",
                "--markdown",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert output.startswith("# PR Impact Step 2 Audit\n")
    assert "catalog_record_id" not in output
    assert "unexpected semantic impact" not in output


def test_read_only_contract_does_not_change_inputs(tmp_path: Path, monkeypatch) -> None:
    source = step1_report(status="complete")
    fixture = tmp_path / "fixture.yaml"
    fixture.write_text("fixture", encoding="utf-8")
    before = fixture.read_bytes()
    monkeypatch.setattr(
        pr_impact_step2.pr_impact_step1, "analyze_fixture", lambda *args: source
    )
    pr_impact_step2.analyze_fixture(
        fixture, "manifest", tmp_path / "catalog.db", "ia-main"
    )
    assert fixture.read_bytes() == before
