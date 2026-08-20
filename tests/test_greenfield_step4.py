from __future__ import annotations

import json

from greenfield.semantic_contract import finalize_index
from greenfield.step2_candidates import resolve_candidates
from greenfield.step4_contract import validate_step4_report
from greenfield.step4_coverage import map_test_coverage

TARGET = "a" * 40
SUITE_SHA = "b" * 40


def _step1() -> dict:
    return {
        "schema_version": "0.5",
        "analysis_kind": "pr_impact_step_1",
        "status": "partial",
        "input": {"repo_key": "ia-app", "target_revision": TARGET},
        "changed_files": [
            {"path": "app/source/company/CompanyConfig.cls", "status": "modified"}
        ],
    }


def _contract(*, obligations: list[dict] | None = None) -> dict:
    return {
        "schema_version": "0.1",
        "repository": "ia-app",
        "revision": TARGET,
        "relations": [
            {
                "interface_id": "company.config.preference",
                "consumer_repository": "intacct/ia-restapi-automation-tests",
                "relationship_type": "api_contract",
                "source_paths": ["app/source/company/CompanyConfig.cls"],
                "status": "active",
                "test_obligations": obligations or [],
            }
        ],
        "evidence": {"path": "contract.yaml", "sha256": "c" * 64},
    }


def _step3(contract: dict | None = None) -> dict:
    contract = contract or _contract()
    step2 = resolve_candidates(_step1(), contracts=[contract])
    from greenfield.step3_outcome import assemble_outcome

    return assemble_outcome(step2)


def _ci(
    tests: list[dict],
    *,
    source_revision: str = TARGET,
    status: str = "available",
) -> dict:
    return {
        "schema_version": "0.1",
        "evidence_id": "run-1",
        "repository": "intacct/ia-restapi-automation-tests",
        "commit_sha": SUITE_SHA,
        "source_repository": "ia-app",
        "source_revision": source_revision,
        "interface_id": "company.config.preference",
        "status": status,
        "tests": tests,
        "evidence": {"path": "ci.json", "sha256": "d" * 64},
    }


def _inventory(status: str = "available") -> dict:
    return {
        "schema_version": "0.1",
        "evidence_type": "repository_inventory",
        "repository": "intacct/ia-restapi-automation-tests",
        "source_repository": "ia-app",
        "source_revision": TARGET,
        "inspected_revision": SUITE_SHA,
        "status": status,
        "workflow_paths": [],
        "inventory_paths": [],
        "workflows": [],
        "workflow_runs": [],
        "check_runs": [],
        "artifacts": [],
        "artifact_status": "empty",
        "ci_linkage": {
            "status": "unavailable",
            "reason": "no linked artifact",
            "source_repository": "ia-app",
            "source_revision": TARGET,
        },
        "gaps": [],
        "provenance": {"response_sha256": "e" * 64, "read_only": True},
    }


def test_exact_ci_evidence_is_covered_and_indirect_is_preserved() -> None:
    report = map_test_coverage(
        _step3(),
        contracts=[_contract()],
        ci_evidence=[
            _ci(
                [
                    {"id": "direct", "path": "tests/direct.feature"},
                    {
                        "id": "dependent",
                        "path": "tests/dependent.feature",
                        "coverage": "indirect",
                    },
                ]
            )
        ],
        inventory_evidence=[_inventory()],
    )
    classifications = {
        item["test"]["id"]: item["classification"]
        for item in report["coverage"]["items"]
        if item.get("test")
    }
    assert classifications == {"direct": "covered", "dependent": "indirectly_covered"}
    assert validate_step4_report(report) == []


def test_contract_without_ci_is_candidate_and_is_still_partial() -> None:
    report = map_test_coverage(
        _step3(), contracts=[_contract()], inventory_evidence=[_inventory()]
    )
    assert report["coverage"]["items"][0]["classification"] == "candidate"
    assert "ci_evidence_not_provided" in report["gaps"]
    assert report["status"] == "partial"


def test_explicit_obligation_without_matching_test_is_missing() -> None:
    report = map_test_coverage(
        _step3(_contract(obligations=[{"id": "required", "path": "tests/required.feature", "required_change": "fixture"}])),
        contracts=[_contract(obligations=[{"id": "required", "path": "tests/required.feature", "required_change": "fixture"}])],
        ci_evidence=[_ci([{ "id": "other", "path": "tests/other.feature" }])],
        inventory_evidence=[_inventory()],
    )
    assert report["obligations"]["items"][0]["status"] == "missing"
    assert report["obligations"]["items"][0]["required_change"] == "fixture"
    assert any(gap.startswith("test_obligation_missing:") for gap in report["gaps"])


def test_stale_and_unavailable_evidence_remain_explicit() -> None:
    stale = map_test_coverage(
        _step3(),
        contracts=[_contract()],
        ci_evidence=[_ci([], source_revision="f" * 40)],
        inventory_evidence=[_inventory()],
    )
    assert any(item["classification"] == "stale" for item in stale["coverage"]["items"])

    unavailable = map_test_coverage(
        _step3(),
        contracts=[_contract()],
        ci_evidence=[_ci([], status="unavailable")],
        inventory_evidence=[_inventory("unavailable")],
    )
    assert any(item["classification"] == "unavailable" for item in unavailable["coverage"]["items"])


def test_ci_evidence_for_another_interface_is_not_attributed() -> None:
    other = _ci([{ "id": "wrong", "path": "tests/wrong.feature" }])
    other["interface_id"] = "another.interface"
    report = map_test_coverage(
        _step3(),
        contracts=[_contract()],
        ci_evidence=[other],
        inventory_evidence=[_inventory()],
    )
    assert not any(
        item.get("test", {}).get("id") == "wrong"
        for item in report["coverage"]["items"]
    )
    assert "ci:interface_mismatch:intacct/ia-restapi-automation-tests" in report["gaps"]


def test_semantic_evidence_never_becomes_executed_coverage() -> None:
    semantic = finalize_index(
        repository="ia-app",
        revision=TARGET,
        nodes=[{"key": "api_object:company/preference", "kind": "api_object", "identity": "company/preference"}],
        edges=[
            {
                "source": "api_object:company/preference",
                "target": None,
                "kind": "changed_surface",
                "resolution": "resolved_exact",
                "evidence": [{"source_path": "app/source/company/CompanyConfig.cls"}],
            }
        ],
        diagnostics=[],
        extractor_versions={"test": "1"},
    )
    report = map_test_coverage(
        _step3(), contracts=[_contract()], inventory_evidence=[_inventory()], semantic_indexes=[semantic]
    )
    semantic_rows = [
        item for item in report["coverage"]["items"] if item.get("behavior_id") == "api_object:company/preference"
    ]
    assert semantic_rows and semantic_rows[0]["classification"] == "candidate"


def test_report_is_deterministic_and_duplicate_tests_are_deduplicated() -> None:
    inputs = {
        "contracts": [_contract()],
        "ci_evidence": [
            _ci([{ "id": "same", "path": "tests/same.feature" }]),
            _ci([{ "id": "same", "path": "tests/same.feature" }]),
        ],
        "inventory_evidence": [_inventory()],
    }
    first = map_test_coverage(_step3(), **inputs)
    second = map_test_coverage(_step3(), **inputs)
    assert json.dumps(first, sort_keys=True, separators=(",", ":")) == json.dumps(second, sort_keys=True, separators=(",", ":"))
    same = [item for item in first["coverage"]["items"] if item.get("test", {}).get("id") == "same"]
    assert len(same) == 1
    assert validate_step4_report(first) == []
