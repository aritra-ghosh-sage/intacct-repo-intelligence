from __future__ import annotations

import json

import pytest

from greenfield.step2_candidates import resolve_candidates
from greenfield.step3_outcome import assemble_outcome
from greenfield.step4_coverage import map_test_coverage
from greenfield.step5_actions import (
    Step5Error,
    artifact_sha256,
    recommend_actions,
    validate_step5_report,
)

TARGET = "a" * 40
SUITE_SHA = "b" * 40


def _step1() -> dict:
    return {
        "schema_version": "0.5",
        "analysis_kind": "pr_impact_step_1",
        "status": "partial",
        "input": {"repo_key": "ia-app", "target_revision": TARGET},
        "changed_files": [{"path": "app/source/company/CompanyConfig.cls", "status": "modified"}],
    }


def _contract(*, obligations: list[dict] | None = None, owner: str | None = "team-config", owner_repository: str | None = "intacct/ia-app") -> dict:
    return {
        "schema_version": "0.1",
        "repository": "ia-app",
        "revision": TARGET,
        "relations": [{
            "interface_id": "company.config.preference",
            "consumer_repository": "intacct/ia-restapi-automation-tests",
            "relationship_type": "api_contract",
            "owner_repository": owner_repository,
            "owner": owner,
            "source_paths": ["app/source/company/CompanyConfig.cls"],
            "status": "active",
            "test_obligations": obligations or [],
        }],
        "evidence": {"path": "contract.yaml", "sha256": "c" * 64},
    }


def _step3(contract: dict | None = None) -> dict:
    return assemble_outcome(resolve_candidates(_step1(), contracts=[contract or _contract()]))


def _ci(tests: list[dict], *, status: str = "available") -> dict:
    return {
        "schema_version": "0.1",
        "evidence_id": "run-1",
        "repository": "intacct/ia-restapi-automation-tests",
        "commit_sha": SUITE_SHA,
        "inspected_revision": SUITE_SHA,
        "workflow_run_id": 10,
        "workflow_job_id": 20,
        "source_repository": "ia-app",
        "source_revision": TARGET,
        "interface_id": "company.config.preference",
        "status": status,
        "tests": [dict(test, execution_result=test.get("execution_result", "passed")) for test in tests],
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
        "workflow_paths": [], "inventory_paths": [], "workflows": [],
        "workflow_runs": [], "check_runs": [], "artifacts": [],
        "artifact_status": "empty",
        "ci_linkage": {"status": "unavailable", "reason": "no linked artifact", "source_repository": "ia-app", "source_revision": TARGET},
        "gaps": [],
        "provenance": {"response_sha256": "e" * 64, "read_only": True},
    }


def _step4(*, obligations: list[dict] | None = None, ci: list[dict] | None = None, owner: str | None = "team-config", owner_repository: str | None = "intacct/ia-app") -> dict:
    contract = _contract(obligations=obligations, owner=owner, owner_repository=owner_repository)
    return map_test_coverage(
        _step3(contract), contracts=[contract], ci_evidence=ci or [], inventory_evidence=[_inventory()]
    )


def test_named_test_and_owner_review_actions_are_emitted() -> None:
    report = recommend_actions(_step3(), _step4(ci=[_ci([{"id": "direct", "path": "tests/direct.feature"}])]))
    types = [item["action_type"] for item in report["actions"]]
    assert "run_test_suite" in types
    assert "request_owner_review" in types
    assert all(item["owner"]["status"] == "available" for item in report["actions"])
    assert validate_step5_report(report) == []


def test_missing_fixture_obligation_emits_update_action() -> None:
    obligation = {"id": "required", "path": "tests/required.feature", "required_change": "fixture"}
    report = recommend_actions(_step3(_contract(obligations=[obligation])), _step4(obligations=[obligation], ci=[_ci([{"id": "other", "path": "tests/other.feature"}])]))
    actions = [item for item in report["actions"] if item["action_type"] == "update_test_obligation"]
    assert len(actions) == 1
    assert actions[0]["scope"]["test_id"] == "required"


def test_missing_integration_obligation_emits_add_action() -> None:
    obligation = {"id": "integration", "path": "tests/integration.feature", "required_change": "integration"}
    report = recommend_actions(_step3(_contract(obligations=[obligation])), _step4(obligations=[obligation], ci=[_ci([])]))
    assert any(item["action_type"] == "add_integration_test" for item in report["actions"])


def test_stale_or_unavailable_test_evidence_blocks_propagation() -> None:
    stale = _ci([], status="stale")
    stale["source_revision"] = "f" * 40
    report = recommend_actions(_step3(), _step4(ci=[stale]))
    blocked = [item for item in report["actions"] if item["action_type"] == "block_propagation"]
    assert blocked and all(item["status"] == "blocked" for item in blocked)


def test_unknown_owner_is_not_invented() -> None:
    contract = _contract(owner=None, owner_repository=None)
    report = recommend_actions(_step3(contract), _step4(ci=[_ci([{"id": "direct", "path": "tests/direct.feature"}])], owner=None, owner_repository=None))
    review = [item for item in report["actions"] if item["action_type"] == "request_owner_review"]
    assert not review
    assert all(item["owner"]["identity"] != "team-config" for item in report["actions"])


def test_candidate_semantic_rows_do_not_create_run_actions() -> None:
    report = recommend_actions(_step3(), _step4())
    assert not any(item["action_type"] == "run_test_suite" for item in report["actions"])


def test_no_matching_contract_scope_does_not_emit_run_actions() -> None:
    step3 = _step3()
    step3["input"]["changed_paths"] = ["app/source/company/AnotherFile.cls"]
    step4 = _step4()
    step4["input"]["changed_paths"] = ["app/source/company/AnotherFile.cls"]
    step4["coverage"] = {"status": "unavailable", "items": []}
    step4["obligations"] = {"status": "not_modelled", "items": []}
    step4["warnings"] = ["no_matching_test_obligation_for_change"]
    step4["provenance"]["step3_report_sha256"] = artifact_sha256(step3)
    report = recommend_actions(step3, step4)
    assert not any(item["action_type"] == "run_test_suite" for item in report["actions"])


def test_source_ranked_candidate_test_creates_validation_action() -> None:
    step3 = _step3()
    step3["test_suites"] = {
        "status": "partial",
        "items": [{
            "target_repository": "intacct/ia-restapi-automation-tests",
            "interface_id": "company.config.preference",
            "status": "available",
            "reason": "source_ranked_test_without_execution_proof",
            "test": {"id": "likely", "path": "features/company.feature"},
            "evidence": [{"kind": "repository_inventory", "sha256": "e" * 64}],
        }],
    }
    step4 = _step4()
    step4["coverage"]["items"] = [{
        "target_repository": "intacct/ia-restapi-automation-tests",
        "interface_id": "company.config.preference",
        "classification": "candidate",
        "reason": "source_ranked_test_without_execution_proof",
        "test": {"id": "likely", "path": "features/company.feature"},
        "source_repository": "ia-app",
        "source_revision": TARGET,
        "evidence": [{"kind": "repository_inventory", "digest": "e" * 64}],
    }]
    step4["provenance"]["step3_report_sha256"] = artifact_sha256(step3)
    report = recommend_actions(step3, step4)
    actions = [item for item in report["actions"] if item["action_type"] == "run_test_suite"]
    assert len(actions) == 1
    assert actions[0]["reason"] == "source_ranked_test_without_execution_proof"
    assert actions[0]["scope"]["test_path"] == "features/company.feature"


def test_reports_must_match_exact_source_context() -> None:
    step4 = _step4()
    step4["input"]["target_revision"] = "f" * 40
    with pytest.raises(Step5Error, match="target revisions do not match"):
        recommend_actions(_step3(), step4)


def test_step4_must_be_derived_from_supplied_step3_report() -> None:
    step3 = _step3()
    step4 = _step4()
    step4["provenance"]["step3_report_sha256"] = "0" * 64
    with pytest.raises(Step5Error, match="provenance does not match"):
        recommend_actions(step3, step4)


def test_unavailable_target_repository_blocks_propagation() -> None:
    step3 = _step3()
    step3["gaps"] = ["repository_access_unavailable:intacct/target-tests"]
    step4 = _step4()
    step4["provenance"]["step3_report_sha256"] = artifact_sha256(step3)
    report = recommend_actions(step3, step4)
    blocked = [item for item in report["actions"] if item["action_type"] == "block_propagation"]
    assert any(item["target_repository"] == "intacct/target-tests" for item in blocked)
    assert all(item["status"] == "blocked" for item in blocked)


def test_action_output_is_deterministic_and_deduplicated() -> None:
    step3 = _step3()
    step4 = _step4(ci=[_ci([{"id": "direct", "path": "tests/direct.feature"}]), _ci([{"id": "direct", "path": "tests/direct.feature"}])])
    first = recommend_actions(step3, step4)
    second = recommend_actions(step3, step4)
    assert json.dumps(first, sort_keys=True, separators=(",", ":")) == json.dumps(second, sort_keys=True, separators=(",", ":"))
    ids = [item["action_id"] for item in first["actions"]]
    assert len(ids) == len(set(ids))


def test_validator_rejects_missing_completion_condition() -> None:
    report = recommend_actions(_step3(), _step4())
    del report["actions"][0]["completion_condition"]
    assert any("completion_condition" in error for error in validate_step5_report(report))


def test_validator_rejects_tampered_action_id() -> None:
    report = recommend_actions(_step3(), _step4(ci=[_ci([{"id": "direct", "path": "tests/direct.feature"}])]))
    report["actions"][0]["action_id"] = "0" * 64
    assert any("action_id does not match" in error for error in validate_step5_report(report))
