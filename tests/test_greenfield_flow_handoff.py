from __future__ import annotations

import json
from pathlib import Path

import pytest

from greenfield.flow_handoff import FlowHandoffError, GreenfieldFlowHandoff


def _artifact(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_handoff_binds_stage_inputs_outputs_and_hashes(tmp_path: Path) -> None:
    step1 = _artifact(tmp_path / "step1.json", {"step": 1})
    trace = _artifact(tmp_path / "step1.5.trace.json", {"step": "1.5"})
    handoff = GreenfieldFlowHandoff(
        tmp_path,
        source={"repository": "intacct/ia-app", "head_sha": "a" * 40},
    )

    handoff.complete_stage(
        "step1_5",
        inputs={"step1": step1},
        outputs={"trace": trace},
    )
    result = handoff.finish()

    assert result["status"] == "complete"
    assert result["stages"][0]["inputs"]["step1"]["path"] == "step1.json"
    assert len(result["stages"][0]["outputs"]["trace"]["sha256"]) == 64
    persisted = json.loads((tmp_path / "flow.handoff.json").read_text())
    assert persisted == result


def test_handoff_fails_closed_for_missing_artifacts(tmp_path: Path) -> None:
    handoff = GreenfieldFlowHandoff(tmp_path, source={"repository": "ia-main"})

    with pytest.raises(FlowHandoffError, match="missing"):
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
    handoff.finish()
    with pytest.raises(FlowHandoffError, match="already complete"):
        GreenfieldFlowHandoff(tmp_path, source=source)
    with pytest.raises(FlowHandoffError, match="identity"):
        GreenfieldFlowHandoff(
            tmp_path,
            source={"repository": "intacct/ia-app", "target_revision": "b" * 40},
        )
