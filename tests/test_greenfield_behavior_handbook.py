from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from greenfield.behavior_handbook import (
    BehaviorHandbookError,
    build_behavior_handbook,
    render_behavior_handbook_markdown,
    validate_behavior_handbook,
)
from scripts import render_greenfield_handbook

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "examples" / "greenfield" / "ia-app-pr-49137" / "replay"
BEHAVIOR_ID = "behavior:56d1ddedbff5f0e83a5d8650"
SHARED_INTERFACE_ID = "behavior:gl-allocation-recalculation"


def _read(name: str) -> dict:
    return json.loads((BUNDLE / name).read_text(encoding="utf-8"))


def _inputs() -> tuple[dict, dict, dict, dict, dict]:
    return (
        _read("step1.5.contract.json"),
        _read("step2.report.json"),
        _read("step3.report.json"),
        _read("step4.report.json"),
        _read("step5.report.json"),
    )


def _build() -> dict:
    return build_behavior_handbook(*_inputs())


def test_bundle_builds_exact_and_shared_anchor_behavior_links() -> None:
    report = _build()

    assert validate_behavior_handbook(report) == []
    assert report["status"] == "partial"
    assert len(report["behaviors"]) == 1
    behavior = report["behaviors"][0]
    assert behavior["behavior_id"] == BEHAVIOR_ID
    assert behavior["impact"]["related_interface_ids"] == [
        BEHAVIOR_ID,
        SHARED_INTERFACE_ID,
    ]
    impact_keys = {
        (row["target_repository"], row.get("interface_id"), row["classification"])
        for row in behavior["impact"]["items"]
    }
    assert ("ia-app", BEHAVIOR_ID, "confirmed") in impact_keys
    assert (
        "intacct/ia-gwdata-gl",
        SHARED_INTERFACE_ID,
        "candidate",
    ) in impact_keys


def test_unmatched_and_repository_only_evidence_remains_unassigned() -> None:
    contract, step2, step3, step4, step5 = _inputs()
    similar = {
        "scope": "interface",
        "target_repository": "intacct/similar-name",
        "interface_id": "behavior:apply-allocation-similar",
        "classification": "candidate",
        "relationship_type": "semantic_similarity",
        "reason": "name_only",
        "evidence": [{"kind": "fixture"}],
    }
    step3["impact"]["items"].append(similar)
    step3["impact"]["items"].sort(
        key=lambda row: (
            {"confirmed": 0, "candidate": 1, "unresolved": 2}[row["classification"]],
            row["target_repository"],
            row.get("interface_id") or "",
            row["relationship_type"],
            row["classification"],
            row["scope"],
        )
    )
    repository = step3["potentially_affected_repositories"]["items"][0]
    step3["related_pull_requests"] = {
        "status": "available",
        "source_pr_number": 49137,
        "source_repository": step3["input"]["source_repository"],
        "source_revision": step3["input"]["target_revision"],
        "items": [
            {
                "repository": "intacct/ia-rest-api-testing",
                "number": 49201,
                "state": "open",
                "head_sha": "b" * 40,
                "base_sha": "c" * 40,
                "relation_type": "declared_interface_follow_up",
                "evidence": {"id": "review:49137:49201"},
            }
        ],
    }

    report = build_behavior_handbook(contract, step2, step3, step4, step5)

    behavior_impact = report["behaviors"][0]["impact"]["items"]
    assert similar not in behavior_impact
    assert similar in report["unassigned_evidence"]["step3_impact"]
    assert any(
        row.get("scope") == "repository"
        for row in report["unassigned_evidence"]["step3_impact"]
    )
    assert repository in report["unassigned_evidence"]["step3_repositories"]
    assert (
        report["unassigned_evidence"]["step3_related_pull_requests"][0]["number"]
        == 49201
    )


def test_malformed_upstream_report_fails_closed_before_projection() -> None:
    contract, step2, step3, step4, step5 = _inputs()
    step2["candidates"][0].pop("evidence")

    with pytest.raises(BehaviorHandbookError, match="invalid step2 report"):
        build_behavior_handbook(contract, step2, step3, step4, step5)


def test_candidate_unavailable_and_not_run_states_are_preserved() -> None:
    behavior = _build()["behaviors"][0]

    assert any(
        row.get("classification") == "candidate" for row in behavior["impact"]["items"]
    )
    assert any(
        row.get("status") == "unavailable"
        for row in behavior["coverage"]["test_suites"]
    )
    assert any(
        row.get("test", {}).get("execution_result") == "not_run"
        for row in behavior["coverage"]["test_suites"]
        if isinstance(row.get("test"), dict)
    )
    assert behavior["status"] == "partial"


@pytest.mark.parametrize(
    ("report_index", "field", "value", "message"),
    [
        (1, "target_revision", "0" * 40, "target revision"),
        (2, "source_repository", "wrong-repo", "source_repository"),
        (3, "changed_paths", ["wrong/path.cls"], "changed paths"),
    ],
)
def test_identity_mismatches_fail_closed(
    report_index: int, field: str, value: object, message: str
) -> None:
    inputs = [deepcopy(value) for value in _inputs()]
    inputs[report_index]["input"][field] = value

    with pytest.raises(BehaviorHandbookError, match=message):
        build_behavior_handbook(*inputs)


def test_output_and_markdown_are_deterministic_with_exact_line_locators() -> None:
    first = _build()
    second = _build()

    assert first == second
    locators = first["behaviors"][0]["implementation"]["locators"]
    assert {
        "kind": "line",
        "path": "app/source/gl/GLBatchManager.cls",
        "line": 1,
        "symbol": "GLBatchManager::glTranslateApplyAllocation",
        "source_revision": "42942af6221c1e974a7e266e96b3199cb95aa448",
    } in locators
    markdown = render_behavior_handbook_markdown(first)
    assert markdown == render_behavior_handbook_markdown(second)
    assert "app/source/gl/GLBatchManager.cls:1" in markdown
    assert "path-only" in markdown


def test_validator_rejects_promoted_complete_status() -> None:
    report = _build()
    report["status"] = "complete"
    report["behaviors"][0]["status"] = "complete"

    errors = validate_behavior_handbook(report)

    assert any("uncertain evidence" in error for error in errors)
    assert "complete handbook contains incomplete evidence" in errors


def test_cli_writes_valid_artifacts_and_fails_before_writing_for_stale_input(
    tmp_path: Path,
) -> None:
    output_json = tmp_path / "behavior-handbook.json"
    output_markdown = tmp_path / "behavior-handbook.md"
    args = [
        "--contract",
        str(BUNDLE / "step1.5.contract.json"),
        "--step2",
        str(BUNDLE / "step2.report.json"),
        "--step3",
        str(BUNDLE / "step3.report.json"),
        "--step4",
        str(BUNDLE / "step4.report.json"),
        "--step5",
        str(BUNDLE / "step5.report.json"),
        "--output-json",
        str(output_json),
        "--output-markdown",
        str(output_markdown),
    ]

    assert render_greenfield_handbook.main(args) == 0
    assert validate_behavior_handbook(json.loads(output_json.read_text())) == []
    assert output_markdown.read_text(encoding="utf-8").startswith(
        "# Greenfield Behavior Handbook"
    )

    stale_contract = _read("step1.5.contract.json")
    stale_contract["revision"] = "0" * 40
    stale_path = tmp_path / "stale-contract.json"
    stale_path.write_text(json.dumps(stale_contract), encoding="utf-8")
    stale_json = tmp_path / "stale-output.json"
    stale_markdown = tmp_path / "stale-output.md"
    stale_args = list(args)
    stale_args[1] = str(stale_path)
    stale_args[-3] = str(stale_json)
    stale_args[-1] = str(stale_markdown)

    assert render_greenfield_handbook.main(stale_args) == 2
    assert not stale_json.exists()
    assert not stale_markdown.exists()
