from __future__ import annotations

import json
from pathlib import Path

import pytest

from greenfield.flow_handoff import (
    FlowHandoffError,
    GreenfieldFlowHandoff,
    validate_legacy_handoff,
)


def _artifact(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _complete_bundle(handoff: GreenfieldFlowHandoff, root: Path, *, outcomes: dict[str, str] | None = None) -> dict:
    outcomes = outcomes or {}
    previous = _artifact(root / "step1.json", {"stage": "step1"})
    handoff.complete_stage("step1", inputs={}, outputs={"artifact": previous}, status=outcomes.get("step1", "succeeded"))
    for name in ("request", "capture", "step1_5", "repository_context", "impact_discovery", "inventory", "step2", "step3", "step4", "step5", "strands_planning", "analyze", "behavior_impact_report", "test_assessment", "test_proposal", "pr_review"):
        current = _artifact(root / f"{name}.json", {"stage": name})
        handoff.complete_stage(name, inputs={"previous": previous}, outputs={"artifact": current}, status=outcomes.get(name, "succeeded"))
        previous = current
    names = (
        "step6_handoff" if "step6_handoff" in outcomes else "step6",
        "step7_handoff" if "step7_handoff" in outcomes else "step7",
        "step8_handoff" if "step8_handoff" in outcomes else "step8_preparation",
        "publish",
    )
    for name in names:
        current = _artifact(root / f"{name}.json", {"stage": name})
        handoff.complete_stage(name, inputs={"previous": previous}, outputs={"artifact": current}, status=outcomes.get(name, "succeeded"))
        previous = current
    return handoff.finish()


def test_handoff_binds_stage_inputs_outputs_and_hashes(tmp_path: Path) -> None:
    step1 = _artifact(tmp_path / "step1.json", {"step": 1})
    trace = _artifact(tmp_path / "step1.5.trace.json", {"step": "1.5"})
    handoff = GreenfieldFlowHandoff(
        tmp_path,
        source={"repository": "intacct/ia-app", "head_sha": "a" * 40},
    )

    handoff.complete_stage("step1", inputs={}, outputs={"step1": step1})
    handoff.complete_stage("request", inputs={"step1": step1}, outputs={"request": _artifact(tmp_path / "request.json", {})})
    handoff.complete_stage("capture", inputs={"request": tmp_path / "request.json"}, outputs={"capture": _artifact(tmp_path / "capture.json", {})})
    handoff.complete_stage(
        "step1_5",
        inputs={"step1": step1},
        outputs={"trace": trace},
    )
    for name in ("repository_context", "impact_discovery", "inventory", "step2", "step3", "step4", "step5", "strands_planning", "analyze", "behavior_impact_report", "test_assessment", "test_proposal", "pr_review"):
        output = _artifact(tmp_path / f"{name}.json", {"stage": name})
        handoff.complete_stage(name, inputs={"trace": trace}, outputs={"artifact": output})
        trace = output
    for name in ("step6_handoff", "step7_handoff", "step8_handoff", "publish"):
        output = _artifact(tmp_path / f"{name}.json", {"stage": name})
        handoff.complete_stage(name, inputs={"previous": trace}, outputs={"artifact": output})
        trace = output
    result = handoff.finish()

    assert result["status"] == "complete"
    assert result["stages"][3]["inputs"]["step1"]["path"] == "step1.json"
    assert len(result["stages"][3]["outputs"]["trace"]["sha256"]) == 64
    persisted = json.loads((tmp_path / "flow.handoff.json").read_text())
    assert persisted == result


def test_handoff_fails_closed_for_missing_artifacts(tmp_path: Path) -> None:
    handoff = GreenfieldFlowHandoff(tmp_path, source={"repository": "ia-main"})

    with pytest.raises(FlowHandoffError, match="output is missing"):
        handoff.complete_stage(
            "step2", inputs={"step1": tmp_path / "missing.json"}, outputs={}
        )


def test_handoff_persists_terminal_failure(tmp_path: Path) -> None:
    handoff = GreenfieldFlowHandoff(tmp_path, source={"repository": "ia-main"})
    diagnostic = _artifact(
        tmp_path / "step1.5.diagnostic.json",
        {"analysis_kind": "greenfield_pr_impact_step_1_5_diagnostic"},
    )
    handoff.fail(
        "step4",
        ValueError("invalid evidence"),
        contract_path=tmp_path / "step1.5.contract.json",
        diagnostics={"step1_5_diagnostic": diagnostic},
    )

    persisted = json.loads((tmp_path / "flow.handoff.json").read_text())
    assert persisted["status"] == "failed"
    assert persisted["failure"]["stage"] == "step4"
    assert persisted["failure"]["reason"] == "invalid evidence"
    assert persisted["failure"]["contract_path"] == str(tmp_path / "step1.5.contract.json")
    assert persisted["failure"]["diagnostics"]["step1_5_diagnostic"]["path"] == "step1.5.diagnostic.json"


def test_complete_bundle_rejects_rerun_or_identity_change(tmp_path: Path) -> None:
    source = {"repository": "intacct/ia-app", "target_revision": "a" * 40}
    handoff = GreenfieldFlowHandoff(tmp_path, source=source)
    _complete_bundle(handoff, tmp_path)
    with pytest.raises(FlowHandoffError, match="already complete"):
        GreenfieldFlowHandoff(tmp_path, source=source)
    with pytest.raises(FlowHandoffError, match="identity"):
        GreenfieldFlowHandoff(
            tmp_path,
            source={"repository": "intacct/ia-app", "target_revision": "b" * 40},
        )


def test_flow_status_is_derived_from_terminal_stage_outcomes(tmp_path: Path) -> None:
    handoff = GreenfieldFlowHandoff(tmp_path, source={"repository": "ia-main"})
    result = _complete_bundle(handoff, tmp_path, outcomes={"step7_handoff": "blocked"})
    assert result["status"] == "blocked"


def test_running_bundle_rejects_tampered_resume_artifact(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path / "step1.json", {"stage": 1})
    handoff = GreenfieldFlowHandoff(tmp_path, source={"repository": "ia-main"})
    handoff.complete_stage("step1", inputs={}, outputs={"step1": artifact})
    artifact.write_text('{"stage": 2}', encoding="utf-8")
    with pytest.raises(FlowHandoffError, match="changed"):
        GreenfieldFlowHandoff(tmp_path, source={"repository": "ia-main"})


def test_running_bundle_accepts_retained_source_field_aliases(tmp_path: Path) -> None:
    GreenfieldFlowHandoff(
        tmp_path,
        source={
            "repository": "intacct/ia-app",
            "repo_key": "ia-main",
            "pr_number": 1,
            "base_sha": "a" * 40,
            "head_sha": "b" * 40,
            "changed_paths": ["app/source/example.cls"],
        },
    )
    resumed = GreenfieldFlowHandoff(
        tmp_path,
        source={
            "repository": "intacct/ia-app",
            "repo_key": "ia-main",
            "pr_number": 1,
            "base_revision": "a" * 40,
            "head_revision": "b" * 40,
            "changed_paths": ["app/source/example.cls"],
            "local_root": "/not-an-identity-field",
        },
    )
    assert resumed.path.exists()


def test_legacy_handoff_is_inspectable_but_cannot_resume(tmp_path: Path) -> None:
    source = {"repository": "ia-main", "head_sha": "a" * 40}
    handoff = GreenfieldFlowHandoff(tmp_path, source=source)
    handoff._body["stages"] = [
        {"name": "step1", "status": "complete", "inputs": {}, "outputs": {}}
    ]
    handoff._write()
    assert validate_legacy_handoff(tmp_path, source=source) == []
    with pytest.raises(FlowHandoffError, match="output is missing"):
        GreenfieldFlowHandoff(tmp_path, source=source)
