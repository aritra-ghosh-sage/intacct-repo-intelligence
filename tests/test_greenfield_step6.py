from __future__ import annotations

import json
from pathlib import Path

import pytest

from greenfield.step1_capture import build_report
from greenfield.step2_candidates import resolve_candidates
from greenfield.step3_outcome import assemble_outcome
from greenfield.step4_contract import artifact_sha256 as step4_artifact_sha256
from greenfield.step4_coverage import map_test_coverage
from greenfield.step5_actions import artifact_sha256, recommend_actions
from greenfield.step6_contract import (
    Step6Error,
    sha256_bytes,
    validate_step6_report,
    validate_step6_request,
)
from greenfield.step6_patch import generate_step6
from scripts import trace_greenfield_step6, validate_greenfield_step6

BASE = "b" * 40
HEAD = "a" * 40


def _step1() -> dict:
    return build_report(
        {
            "schema_version": "0.2",
            "analysis_kind": "pr_impact_metadata",
            "repo_key": "ia-app",
            "repository": "intacct/ia-app",
            "pull_request": {
                "number": 49156,
                "url": "https://github.com/intacct/ia-app/pull/49156",
                "title": "Example",
                "base_revision": BASE,
                "target_revision": HEAD,
            },
            "changed_files": [
                {
                    "filename": "app/source/company/CompanyConfig.cls",
                    "status": "modified",
                }
            ],
            "linked_issues": [],
            "workflow_runs": [],
            "workflow_jobs": [],
            "check_runs": [],
            "evidence_status": {
                "linked_issues": "empty",
                "workflow_runs": "empty",
                "workflow_jobs": "empty",
                "check_runs": "empty",
            },
            "provenance": {"provider": "fixture", "endpoints": []},
        }
    )


def _contract(target_repository: str, test_path: str) -> dict:
    return {
        "schema_version": "0.1",
        "repository": "ia-app",
        "revision": HEAD,
        "relations": [
            {
                "interface_id": "company.config.preference",
                "consumer_repository": target_repository,
                "relationship_type": "api_contract",
                "owner_repository": "intacct/ia-app",
                "owner": "team-config",
                "source_paths": ["app/source/company/CompanyConfig.cls"],
                "status": "active",
                "test_obligations": [
                    {
                        "id": "required-test",
                        "path": test_path,
                        "required_change": "fixture",
                    }
                ],
            }
        ],
        "evidence": {"path": "contract.yaml", "sha256": "c" * 64},
    }


def _step3(target_repository: str, test_path: str) -> dict:
    contract = _contract(target_repository, test_path)
    return assemble_outcome(resolve_candidates(_step1(), contracts=[contract]))


def _step4(target_repository: str, test_path: str) -> dict:
    contract = _contract(target_repository, test_path)
    return map_test_coverage(
        _step3(target_repository, test_path),
        contracts=[contract],
        inventory_evidence=[
            {
                "schema_version": "0.1",
                "evidence_type": "repository_inventory",
                "repository": target_repository,
                "source_repository": "ia-app",
                "source_revision": HEAD,
                "inspected_revision": "c" * 40,
                "status": "available",
                "workflow_paths": [],
                "inventory_paths": [],
                "workflows": [],
                "workflow_runs": [],
                "check_runs": [],
                "artifacts": [],
                "gaps": [],
                "artifact_status": "empty",
                "ci_linkage": {
                    "status": "unavailable",
                    "reason": "no linked artifact",
                    "source_repository": "ia-app",
                    "source_revision": HEAD,
                },
                "provenance": {"response_sha256": "e" * 64},
            }
        ],
    )


def _request(
    target_repository: str,
    template_id: str,
    files: list[dict[str, str]],
    operations: list[dict[str, object]],
    *,
    trigger: str = "fixture_contract_mismatch",
) -> tuple[dict, dict, dict, dict, dict]:
    test_path = files[0]["path"]
    step1 = _step1()
    step3 = _step3(target_repository, test_path)
    step4 = _step4(target_repository, test_path)
    step5 = recommend_actions(step3, step4)
    action = next(
        item
        for item in step5["actions"]
        if item["action_type"] == "update_test_obligation"
    )
    diff = "diff --git a/app/source/company/CompanyConfig.cls b/app/source/company/CompanyConfig.cls\n"
    request = {
        "schema_version": "0.1",
        "analysis_kind": "greenfield_pr_impact_step_6_request",
        "source": {
            "repository": "ia-app",
            "pr_number": 49156,
            "pr_url": "https://github.com/intacct/ia-app/pull/49156",
            "base_revision": BASE,
            "head_revision": HEAD,
            "changed_paths": ["app/source/company/CompanyConfig.cls"],
            "diff": diff,
            "diff_sha256": __import__("hashlib").sha256(diff.encode()).hexdigest(),
        },
        "upstream": {
            "step1_report_sha256": artifact_sha256(step1),
            "step3_report_sha256": artifact_sha256(step3),
            "step4_report_sha256": step4_artifact_sha256(step4),
            "step5_report_sha256": artifact_sha256(step5),
        },
        "action": {
            "action_id": action["action_id"],
            "action_type": action["action_type"],
            "status": action["status"],
            "target_repository": target_repository,
            "interface_id": action["scope"]["interface_id"],
            "test_id": action["scope"]["test_id"],
            "test_path": action["scope"]["test_path"],
        },
        "trigger": {
            "kind": trigger,
            "evidence": [{"kind": "contract_delta", "path": "contract.yaml"}],
        },
        "target": {
            "repository": target_repository,
            "base_revision": "d" * 40,
            "files": files,
            "allowed_paths": sorted(item["path"] for item in files),
        },
        "template": {"id": template_id, "version": "0.1"},
        "edit_operations": operations,
        "validation_plan": ["run the targeted test suite"],
    }
    return request, step1, step3, step4, step5


def _file(path: str, content: str) -> dict[str, str]:
    import hashlib

    return {
        "path": path,
        "content": content,
        "sha256": hashlib.sha256(content.encode()).hexdigest(),
    }


def _fixture(relative_path: str, target_path: str) -> dict[str, str]:
    content = (
        Path(__file__).parent / "fixtures" / "greenfield" / "step6" / relative_path
    ).read_text(encoding="utf-8")
    return _file(target_path, content)


def test_gwdata_template_generates_csv_xml_patch() -> None:
    files = sorted(
        [
            _fixture(
                "ia-gwdata-gl/testdefinitions/ActivateProdEntities.csv",
                "testdefinitions/ActivateProdEntities.csv",
            ),
            _fixture(
                "ia-gwdata-gl/testscripts/ActivateProdEntity/test_1.xml",
                "testscripts/ActivateProdEntity/test_1.xml",
            ),
            _fixture(
                "ia-gwdata-gl/testscripts/ActivateProdEntity/res_test_1.xml",
                "testscripts/ActivateProdEntity/res_test_1.xml",
            ),
        ],
        key=lambda item: item["path"],
    )
    request, step1, step3, step4, step5 = _request(
        "intacct/ia-gwdata-gl",
        "gwdata_gl_existing_case_update_v1",
        files,
        [
            {
                "path": files[0]["path"],
                "old_text": "old,row",
                "new_text": "new,row",
                "expected_occurrences": 1,
            },
            {
                "path": files[1]["path"],
                "old_text": ">old<",
                "new_text": ">new<",
                "expected_occurrences": 1,
            },
        ],
    )
    report = generate_step6(request, step1, step3, step4, step5)
    assert report["status"] == "ready_for_ai_pr"
    assert {item["path"] for item in report["patch"]["files"]} == {
        files[0]["path"],
        files[1]["path"],
    }
    assert validate_step6_report(report) == []


def test_rest_template_generates_feature_json_patch() -> None:
    files = [
        _fixture(
            "ia-restapi-automation-tests/features/gl/v1-beta2/example.feature",
            "features/gl/v1-beta2/example.feature",
        ),
        _fixture(
            "ia-restapi-automation-tests/features/gl/v1-beta2/input/request.json",
            "features/gl/v1-beta2/input/request.json",
        ),
    ]
    request, step1, step3, step4, step5 = _request(
        "intacct/ia-restapi-automation-tests",
        "restapi_existing_case_update_v1",
        files,
        [
            {
                "path": files[0]["path"],
                "old_text": "Then old",
                "new_text": "Then new",
                "expected_occurrences": 1,
            },
            {
                "path": files[1]["path"],
                "old_text": '"old"',
                "new_text": '"new"',
                "expected_occurrences": 1,
            },
        ],
    )
    report = generate_step6(request, step1, step3, step4, step5)
    assert report["status"] == "ready_for_ai_pr"
    assert validate_step6_report(report) == []


def test_missing_target_evidence_fails_closed() -> None:
    files = [_file("features/example.feature", "Then old\n")]
    request, _step1, _step3, _step4, _step5 = _request(
        "intacct/ia-restapi-automation-tests",
        "restapi_existing_case_update_v1",
        files,
        [
            {
                "path": files[0]["path"],
                "old_text": "old",
                "new_text": "new",
                "expected_occurrences": 1,
            }
        ],
    )
    request["target"]["files"][0]["content"] = "tampered\n"
    assert validate_step6_request(request)


def test_strict_target_evidence_requires_real_revision_and_file_identity() -> None:
    files = [_file("features/example.feature", "Then old\n")]
    request, step1, step3, step4, step5 = _request(
        "intacct/ia-restapi-automation-tests",
        "restapi_existing_case_update_v1",
        files,
        [
            {
                "path": files[0]["path"],
                "old_text": "old",
                "new_text": "new",
                "expected_occurrences": 1,
            }
        ],
    )
    assert validate_step6_request(request, strict_target_evidence=True)

    target_revision = "0123456789abcdef0123456789abcdef01234567"
    request["target"]["base_revision"] = target_revision
    evidence = {
        "provider": "github_git_api",
        "repository": request["target"]["repository"],
        "revision": target_revision,
        "files": [
            {
                "path": files[0]["path"],
                "content_sha256": files[0]["sha256"],
                "blob_or_response_id": "blob:example-feature",
            }
        ],
    }
    evidence["evidence_sha256"] = artifact_sha256(evidence)
    request["target_evidence"] = evidence
    assert validate_step6_request(request, strict_target_evidence=True) == []
    report = generate_step6(
        request,
        step1,
        step3,
        step4,
        step5,
        strict_target_evidence=True,
    )
    assert report["status"] == "ready_for_ai_pr"
    assert validate_step6_report(report, strict_target_evidence=True) == []


def test_strict_step6_requires_both_owner_approvals_for_pr_output() -> None:
    files = [_file("features/example.feature", "Then old\n")]
    request, step1, step3, step4, step5 = _request(
        "intacct/ia-restapi-automation-tests",
        "restapi_existing_case_update_v1",
        files,
        [
            {
                "path": files[0]["path"],
                "old_text": "old",
                "new_text": "new",
                "expected_occurrences": 1,
            }
        ],
    )
    target_revision = "0123456789abcdef0123456789abcdef01234567"
    request["target"]["base_revision"] = target_revision
    evidence = {
        "provider": "github_git_api",
        "repository": request["target"]["repository"],
        "revision": target_revision,
        "files": [
            {
                "path": files[0]["path"],
                "content_sha256": files[0]["sha256"],
                "blob_or_response_id": "blob:example-feature",
            }
        ],
    }
    evidence["evidence_sha256"] = artifact_sha256(evidence)
    request["target_evidence"] = evidence
    blocked = generate_step6(
        request,
        step1,
        step3,
        step4,
        step5,
        strict_target_evidence=True,
        require_approvals=True,
    )
    assert blocked["status"] == "blocked"
    assert "owner_approval_pending" in blocked["reason"]
    assert validate_step6_report(blocked, strict_target_evidence=True) == []

    request["approvals"] = [
        {
            "role": "source_interface_owner",
            "status": "approved",
            "approver": "source-owner",
            "approval_evidence": {
                "provider": "approval-service",
                "record_id": "approval-source-1",
                "sha256": "1" * 64,
            },
        },
        {
            "role": "consumer_test_owner",
            "status": "approved",
            "approver": "test-owner",
            "approval_evidence": {
                "provider": "approval-service",
                "record_id": "approval-test-1",
                "sha256": "2" * 64,
            },
        },
    ]
    for approval in request["approvals"]:
        approval["approval_sha256"] = artifact_sha256(
            {
                "role": approval["role"],
                "status": approval["status"],
                "approver": approval["approver"],
                "approval_evidence": approval["approval_evidence"],
            }
        )
    ready = generate_step6(
        request,
        step1,
        step3,
        step4,
        step5,
        strict_target_evidence=True,
        require_approvals=True,
    )
    assert ready["status"] == "ready_for_ai_pr"
    assert validate_step6_report(
        ready, strict_target_evidence=True, require_approvals=True
    ) == []


def test_owner_approval_gate_is_not_bypassed_without_strict_evidence() -> None:
    files = [_file("features/example.feature", "Then old\n")]
    request, step1, step3, step4, step5 = _request(
        "intacct/ia-restapi-automation-tests",
        "restapi_existing_case_update_v1",
        files,
        [{"path": files[0]["path"], "old_text": "old", "new_text": "new", "expected_occurrences": 1}],
    )
    blocked = generate_step6(
        request, step1, step3, step4, step5, require_approvals=True
    )
    assert blocked["status"] == "blocked"


def test_strict_target_evidence_requires_all_target_files() -> None:
    files = sorted(
        [_file("features/a.feature", "Then old\n"), _file("features/b.feature", "Then other\n")],
        key=lambda row: row["path"],
    )
    request, *_ = _request(
        "intacct/ia-restapi-automation-tests",
        "restapi_existing_case_update_v1",
        files,
        [{"path": files[0]["path"], "old_text": "old", "new_text": "new", "expected_occurrences": 1}],
    )
    revision = "0123456789abcdef0123456789abcdef01234567"
    request["target"]["base_revision"] = revision
    evidence = {
        "provider": "github_git_api",
        "repository": request["target"]["repository"],
        "revision": revision,
        "files": [{
            "path": files[0]["path"],
            "content_sha256": files[0]["sha256"],
            "blob_or_response_id": "blob:example",
        }],
    }
    evidence["evidence_sha256"] = artifact_sha256(evidence)
    request["target_evidence"] = evidence
    assert any(
        "exactly match target.files" in error
        for error in validate_step6_request(request, strict_target_evidence=True)
    )


def test_multiple_old_fragments_fail_closed() -> None:
    files = [_file("features/example.feature", "Then old\nThen old\n")]
    request, step1, step3, step4, step5 = _request(
        "intacct/ia-restapi-automation-tests",
        "restapi_existing_case_update_v1",
        files,
        [
            {
                "path": files[0]["path"],
                "old_text": "old",
                "new_text": "new",
                "expected_occurrences": 1,
            }
        ],
    )
    with pytest.raises(Step6Error, match="occur exactly once"):
        generate_step6(request, step1, step3, step4, step5)


def test_unsupported_trigger_is_not_generated() -> None:
    files = [_file("features/example.feature", "Then old\n")]
    request, step1, step3, step4, step5 = _request(
        "intacct/ia-restapi-automation-tests",
        "restapi_existing_case_update_v1",
        files,
        [
            {
                "path": files[0]["path"],
                "old_text": "old",
                "new_text": "new",
                "expected_occurrences": 1,
            }
        ],
    )
    request["trigger"]["kind"] = "required_test_category_missing"
    report = generate_step6(request, step1, step3, step4, step5)
    assert report["status"] == "not_generated"
    assert not report["patch"]["files"]
    assert validate_step6_report(report) == []


def test_repeated_generation_is_deterministic() -> None:
    files = [_file("features/example.feature", "Then old\n")]
    request, step1, step3, step4, step5 = _request(
        "intacct/ia-restapi-automation-tests",
        "restapi_existing_case_update_v1",
        files,
        [
            {
                "path": files[0]["path"],
                "old_text": "old",
                "new_text": "new",
                "expected_occurrences": 1,
            }
        ],
    )
    first = generate_step6(request, step1, step3, step4, step5)
    second = generate_step6(request, step1, step3, step4, step5)
    assert first == second


def test_source_mismatch_and_unsafe_path_fail_closed() -> None:
    files = [_file("features/example.feature", "Then old\n")]
    request, step1, step3, step4, step5 = _request(
        "intacct/ia-restapi-automation-tests",
        "restapi_existing_case_update_v1",
        files,
        [
            {
                "path": files[0]["path"],
                "old_text": "old",
                "new_text": "new",
                "expected_occurrences": 1,
            }
        ],
    )
    request["source"]["repository"] = "intacct/other"
    with pytest.raises(Step6Error):
        generate_step6(request, step1, step3, step4, step5)

    unsafe_request, _, _, _, _ = _request(
        "intacct/ia-restapi-automation-tests",
        "restapi_existing_case_update_v1",
        files,
        [
            {
                "path": files[0]["path"],
                "old_text": "old",
                "new_text": "new",
                "expected_occurrences": 1,
            }
        ],
    )
    unsafe_request["edit_operations"][0]["path"] = "../outside.feature"
    assert validate_step6_request(unsafe_request)


def test_unsupported_action_without_edit_package_is_not_generated() -> None:
    files = [_file("features/example.feature", "Then old\n")]
    request, step1, step3, step4, step5 = _request(
        "intacct/ia-restapi-automation-tests",
        "restapi_existing_case_update_v1",
        files,
        [
            {
                "path": files[0]["path"],
                "old_text": "old",
                "new_text": "new",
                "expected_occurrences": 1,
            }
        ],
    )
    action = next(
        item
        for item in step5["actions"]
        if item["action_type"] == "update_test_obligation"
    )
    action["action_type"] = "add_integration_test"
    action_payload = dict(action)
    action_payload.pop("action_id")
    action["action_id"] = artifact_sha256(action_payload)
    step5["actions"].sort(
        key=lambda item: (
            0 if item["status"] == "blocked" else 1,
            item["target_repository"],
            item["scope"].get("interface_id", ""),
            item["action_type"],
            item["scope"].get("test_id", ""),
            item["action_id"],
        )
    )
    request["action"]["action_type"] = "add_integration_test"
    request["action"]["action_id"] = action["action_id"]
    request["target"]["files"] = []
    request["target"]["allowed_paths"] = []
    request["edit_operations"] = []
    request["upstream"]["step5_report_sha256"] = artifact_sha256(step5)
    assert validate_step6_request(request) == []
    report = generate_step6(request, step1, step3, step4, step5)
    assert report["status"] == "not_generated"


def test_template_pairing_and_fixture_reference_are_required() -> None:
    gateway_files = [
        _file("testdefinitions/cases.csv", "old,row\n"),
        _file("testscripts/case/test_1.xml", "<request/>\n"),
        _file("testscripts/case/res_test_2.xml", "<response/>\n"),
    ]
    request, step1, step3, step4, step5 = _request(
        "intacct/ia-gwdata-gl",
        "gwdata_gl_existing_case_update_v1",
        sorted(gateway_files, key=lambda item: item["path"]),
        [
            {
                "path": gateway_files[0]["path"],
                "old_text": "old",
                "new_text": "new",
                "expected_occurrences": 1,
            }
        ],
    )
    with pytest.raises(Step6Error, match="not paired"):
        generate_step6(request, step1, step3, step4, step5)

    rest_files = [
        _fixture(
            "ia-restapi-automation-tests/features/gl/v1-beta2/example.feature",
            "features/gl/v1-beta2/example.feature",
        ),
        _file("features/gl/v1-beta2/input/unreferenced.json", '{"field": "old"}\n'),
    ]
    request, step1, step3, step4, step5 = _request(
        "intacct/ia-restapi-automation-tests",
        "restapi_existing_case_update_v1",
        rest_files,
        [
            {
                "path": rest_files[1]["path"],
                "old_text": "old",
                "new_text": "new",
                "expected_occurrences": 1,
            }
        ],
    )
    with pytest.raises(Step6Error, match="not referenced"):
        generate_step6(request, step1, step3, step4, step5)


def test_tampered_report_fingerprint_is_rejected() -> None:
    files = [_file("features/example.feature", "Then old\n")]
    request, step1, step3, step4, step5 = _request(
        "intacct/ia-restapi-automation-tests",
        "restapi_existing_case_update_v1",
        files,
        [
            {
                "path": files[0]["path"],
                "old_text": "old",
                "new_text": "new",
                "expected_occurrences": 1,
            }
        ],
    )
    report = generate_step6(request, step1, step3, step4, step5)
    report["patch"]["files"][0]["after"] = "tampered"
    assert any(
        "after_sha256" in error or "proposal_id" in error
        for error in validate_step6_report(report)
    )


def test_tampered_unified_diff_is_rejected_even_with_new_fingerprint() -> None:
    files = [_file("features/example.feature", "Then old\n")]
    request, step1, step3, step4, step5 = _request(
        "intacct/ia-restapi-automation-tests",
        "restapi_existing_case_update_v1",
        files,
        [
            {
                "path": files[0]["path"],
                "old_text": "old",
                "new_text": "new",
                "expected_occurrences": 1,
            }
        ],
    )
    report = generate_step6(request, step1, step3, step4, step5)
    report["patch"]["unified_diff"] = "unrelated diff\n"
    report["patch"]["patch_sha256"] = sha256_bytes(
        report["patch"]["unified_diff"].encode()
    )
    unsigned = dict(report)
    unsigned.pop("proposal_id")
    report["proposal_id"] = artifact_sha256(unsigned)
    assert any("unified_diff" in error for error in validate_step6_report(report))


def test_cli_and_validator_round_trip(tmp_path: Path) -> None:
    files = [_file("features/example.feature", "Then old\n")]
    request, step1, step3, step4, step5 = _request(
        "intacct/ia-restapi-automation-tests",
        "restapi_existing_case_update_v1",
        files,
        [
            {
                "path": files[0]["path"],
                "old_text": "old",
                "new_text": "new",
                "expected_occurrences": 1,
            }
        ],
    )
    paths = {}
    for name, value in (
        ("request", request),
        ("step1", step1),
        ("step3", step3),
        ("step4", step4),
        ("step5", step5),
    ):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        paths[name] = path
    output = tmp_path / "step6.json"
    assert (
        trace_greenfield_step6.main(
            [
                "--request",
                str(paths["request"]),
                "--step1-report",
                str(paths["step1"]),
                "--step3-report",
                str(paths["step3"]),
                "--step4-report",
                str(paths["step4"]),
                "--step5-report",
                str(paths["step5"]),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    assert validate_greenfield_step6.main([str(output)]) == 0
