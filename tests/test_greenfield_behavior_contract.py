from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from greenfield.behavior_contract import (
    BehaviorContractError,
    generate_behavior_contract,
)
from greenfield.step2_candidates import resolve_candidates
from greenfield.step2_contract import artifact_sha256, load_contract

ROOT = Path(__file__).resolve().parents[1]
STEP1 = json.loads(
    (ROOT / "examples/greenfield/ia-app-pr-49137/replay/step1.json").read_text()
)
TRACE = json.loads(
    (
        ROOT / "examples/greenfield/ia-app-pr-49137/replay/step1.source-trace.json"
    ).read_text()
)


def test_generated_contract_is_revision_pinned_and_step2_compatible() -> None:
    contract = generate_behavior_contract(STEP1, TRACE)
    relation = contract["relations"][0]
    assert contract["artifact_kind"] == "generated_behavior_contract"
    assert contract["revision"] == STEP1["input"]["head_sha"]
    assert relation["consumer_repository"] == "ia-app"
    assert relation["relationship_type"] == "behavior_contract"
    assert relation["source_symbols"] == [
        "AllocationTxnHelper::applyAllocation",
        "GLBatchManager::glTranslateApplyAllocation",
    ]
    assert contract["generation"]["status"] == "complete"


def test_generated_contract_is_byte_deterministic() -> None:
    first = generate_behavior_contract(STEP1, TRACE)
    second = generate_behavior_contract(STEP1, copy.deepcopy(TRACE))
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_stale_trace_is_rejected() -> None:
    stale = copy.deepcopy(TRACE)
    stale["revision"] = "0" * 40
    with pytest.raises(BehaviorContractError, match="revision does not match"):
        generate_behavior_contract(STEP1, stale)


def test_changed_path_mismatch_is_rejected() -> None:
    mismatch = copy.deepcopy(TRACE)
    mismatch["changed_paths"] = ["app/source/gl/Other.cls"]
    with pytest.raises(BehaviorContractError, match="changed paths"):
        generate_behavior_contract(STEP1, mismatch)


def test_edge_budget_produces_partial_diagnostics() -> None:
    contract = generate_behavior_contract(STEP1, TRACE, max_edges=0)
    assert contract["generation"]["status"] == "partial"
    assert contract["generation"]["diagnostics"] == [
        {"code": "edge_budget_exceeded", "status": "unresolved"}
    ]
    assert contract["generation"]["edges"] == []


def test_generated_artifact_can_be_loaded_by_existing_contract_loader(
    tmp_path: Path,
) -> None:
    contract = generate_behavior_contract(STEP1, TRACE)
    path = tmp_path / "generated-contract.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    loaded = load_contract(path)
    assert loaded["revision"] == STEP1["input"]["head_sha"]
    assert (
        loaded["relations"][0]["interface_id"]
        == contract["relations"][0]["interface_id"]
    )


def test_loader_rejects_tampered_edge_fact_hash(tmp_path: Path) -> None:
    contract = generate_behavior_contract(STEP1, TRACE)
    contract["generation"]["edges"][0]["evidence_sha256"] = "0" * 64
    contract["evidence"]["sha256"] = artifact_sha256(
        {key: value for key, value in contract.items() if key != "evidence"}
    )
    path = tmp_path / "tampered-contract.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    with pytest.raises(ValueError, match="evidence hash does not match fact"):
        load_contract(path)


def test_artifact_carries_identity_fact_provenance_and_surface_statuses() -> None:
    contract = generate_behavior_contract(STEP1, TRACE)
    assert contract["input"] == {
        "repository": "intacct/ia-app",
        "repo_key": "ia-main",
        "pr_number": 49137,
        "base_sha": STEP1["input"]["base_sha"],
        "head_sha": STEP1["input"]["head_sha"],
        "changed_paths": STEP1["input"]["changed_paths"],
    }
    edge = contract["generation"]["flow"]["edges"][0]
    assert edge["source_revision"] == STEP1["input"]["head_sha"]
    assert len(edge["evidence_sha256"]) == 64
    assert contract["entry_surfaces"]["xml_api"]["status"] == "not_run"
    assert contract["impacted_files"]


def test_cycle_and_hop_budget_are_explicit() -> None:
    trace = copy.deepcopy(TRACE)
    trace["behaviors"][0]["edges"].append(
        {
            "source_symbol": "AllocationTxnHelper::applyAllocation",
            "target_symbol": "GLBatchManager::glTranslateApplyAllocation",
            "relationship_type": "STATIC_CALLS",
            "source_path": "app/source/company/AllocationTxnHelper.cls",
            "target_path": "app/source/gl/GLBatchManager.cls",
            "source_line": 2,
        }
    )
    cycle = generate_behavior_contract(STEP1, trace, max_hops=2)
    cycle_codes = {item["code"] for item in cycle["generation"]["diagnostics"]}
    assert "cycle_detected" in cycle_codes
    limited = generate_behavior_contract(STEP1, trace, max_hops=1)
    limited_codes = {item["code"] for item in limited["generation"]["diagnostics"]}
    assert "hop_budget_exceeded" in limited_codes
    assert limited["generation"]["status"] == "partial"


def test_trace_repository_mismatch_fails_closed() -> None:
    trace = copy.deepcopy(TRACE)
    trace["repository"] = "intacct/other-repository"
    with pytest.raises(BehaviorContractError, match="repository does not match"):
        generate_behavior_contract(STEP1, trace)


def test_missing_target_path_fails_closed() -> None:
    trace = copy.deepcopy(TRACE)
    trace["behaviors"][0].pop("symbol_paths")
    trace["behaviors"][0]["edges"][0].pop("target_path")
    with pytest.raises(BehaviorContractError, match="edge target_path"):
        generate_behavior_contract(STEP1, trace)


def test_step2_rejects_generated_contract_with_mismatched_changed_paths() -> None:
    contract = generate_behavior_contract(STEP1, TRACE)
    contract["input"]["changed_paths"] = ["app/source/gl/Other.cls"]
    report = resolve_candidates(STEP1, contracts=[contract])
    assert report["candidates"] == []
    assert any("generated_input_mismatch" in gap for gap in report["gaps"])
