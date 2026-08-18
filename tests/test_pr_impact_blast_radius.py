from __future__ import annotations

from catalog.pr_impact_blast_radius import build_report
from catalog.pr_impact_metrics import build_metrics

TARGET = "b" * 40


def _inputs() -> tuple[dict, dict, dict, dict]:
    step0 = {
        "pull_request": {"target_revision": TARGET},
        "changed_files": [
            {"path": "app/source/gl/GLBatchManager.cls", "status": "modified"}
        ],
        "affected_surfaces": {
            name: {"status": "unresolved"}
            for name in ("entities", "api", "ui", "database", "permissions")
        },
        "test_obligations": {
            "existing_or_expected": [],
            "recommended": [],
            "unresolved": [],
        },
        "related_repositories": [{"repo_key": "ia-main"}],
    }
    step1 = {
        "status": "partial",
        "preflight": {"target_revision": TARGET, "catalog_revision": TARGET},
        "direct_traces": [
            {
                "surface": "workflows",
                "status": "available",
                "facts": [
                    {
                        "catalog_record_id": 7,
                        "source_path": "workflow.yaml",
                        "target_revision": TARGET,
                    }
                ],
            },
            {
                "surface": "rest_endpoints",
                "status": "available",
                "facts": [
                    {
                        "catalog_record_id": 8,
                        "source_path": "openapi.yaml",
                        "target_revision": TARGET,
                    }
                ],
            },
        ],
        "gaps": ["database_consumers: deferred"],
    }
    step2 = {"status": "partial", "gaps": ["tests: defer_missing_target_evidence"]}
    step3 = {
        "status": "partial",
        "seed_symbols": [
            {
                "symbol_id": 11,
                "name": "CreateBatch",
                "kind": "method",
                "file_path": "app/source/gl/GLBatchManager.cls",
                "stable_key": "method:CreateBatch",
                "declaration_range": {"start_line": 10, "end_line": 20},
                "fixture_target_revision": TARGET,
            }
        ],
        "reached_symbols": [
            {
                "symbol_id": 12,
                "name": "ImportJournal",
                "file_path": "app/source/gl/csvimport_journalentry.cls",
                "declaration_range": {"start_line": 30, "end_line": 40},
                "fixture_target_revision": TARGET,
            }
        ],
        "entity_context": {
            "status": "available",
            "mappings": [
                {
                    "symbol_id": 11,
                    "symbol_name": "CreateBatch",
                    "symbol_file_path": "app/source/gl/GLBatchManager.cls",
                    "symbol_stable_key": "method:CreateBatch",
                    "entity_name": "GLBatch",
                    "entity_id": 21,
                    "entity_occurrence_id": 31,
                    "entity_source_path": "app/source/gl/GLBatch.ent",
                    "entity_source_key": "GLBatch",
                    "mapping_type": "reviewed",
                    "resolution_status": "resolved",
                    "target_revision": TARGET,
                    "contract_entry_key": "mapping:1",
                    "mapping_contract_sha256": "c" * 64,
                    "evidence": "reviewed mapping",
                    "entity_impact_facts": {
                        "workflows": [{"id": 99, "source_path": "workflow.yaml"}]
                    },
                }
            ],
        },
        "gaps": ["caller_evidence: skipped below-confidence"],
    }
    return step0, step1, step2, step3


def test_build_report_preserves_exact_entity_and_candidate_flow_states() -> None:
    step0, step1, step2, step3 = _inputs()
    report = build_report(
        step0,
        step1,
        step2,
        step3,
        test_coverage={
            "status": "partial",
            "gaps": [{"gap_code": "test_catalog_unavailable"}],
        },
    )

    assert report["entities"][0]["ent_file"] == "app/source/gl/GLBatch.ent"
    assert {item["status"] for item in report["flows"]} == {"confirmed", "candidate"}
    assert any(
        item["gap_code"] == "test_catalog_unavailable" for item in report["gaps"]
    )
    assert report["provenance"]["catalog_revision"] == TARGET


def test_metrics_distinguish_unrecorded_pr_facets_from_analysis_gaps() -> None:
    step0, step1, step2, step3 = _inputs()
    report = build_report(
        step0, step1, step2, step3, test_coverage={"status": "deferred"}
    )
    metrics = build_metrics(
        step0, {"step1": step1, "step2": step2, "step3": step3, "step4": report}, report
    )

    assert metrics["found_successfully"]["confirmed_entities"] == 1
    assert metrics["not_recorded_in_pr"]["missing_facets"] == [
        "api",
        "entity",
        "tests",
        "workflow",
    ]
    assert metrics["gap_counts"]["reported_gap"] >= 1


def test_report_with_upstream_gaps_cannot_be_ready() -> None:
    step0, step1, step2, step3 = _inputs()
    step1["status"] = "complete"
    step1["gaps"] = []
    step2["status"] = "complete"
    step2["gaps"] = []
    step3["status"] = "complete"
    step3["gaps"] = []

    report = build_report(
        step0,
        step1,
        step2,
        step3,
        test_coverage={
            "status": "complete",
            "gaps": [{"gap_code": "coverage_limit_reached", "status": "missing"}],
        },
    )

    assert report["status"] == "partial"


def test_unavailable_coverage_cannot_be_ready_without_a_gap_row() -> None:
    step0, step1, step2, step3 = _inputs()
    step1["status"] = "complete"
    step1["gaps"] = []
    step2["status"] = "complete"
    step2["gaps"] = []
    step3["status"] = "complete"
    step3["gaps"] = []

    report = build_report(
        step0,
        step1,
        step2,
        step3,
        test_coverage={"status": "unavailable", "gaps": []},
    )

    assert report["status"] == "partial"
