from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from greenfield.behavior_contract import (
    BehaviorContractError,
    generate_behavior_contract,
)
from greenfield.step2_contract import load_contract

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
