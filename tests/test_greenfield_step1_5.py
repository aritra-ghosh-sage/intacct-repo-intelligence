from __future__ import annotations

import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from greenfield.codex_agent import CodexAgentError, build_context, run_codex_trace
from greenfield.step1_5_trace import normalize_trace, validate_trace
from greenfield.step1_capture import evidence_fingerprint
from scripts import trace_greenfield_step1_5, trace_greenfield_step2
from scripts.validate_greenfield_test_proposal import validate as validate_test_proposal

ROOT = Path(__file__).resolve().parents[1]
STEP1 = json.loads(
    (ROOT / "examples/greenfield/ia-app-pr-49137/replay/step1.json").read_text()
)
SOURCE_TRACE = json.loads(
    (
        ROOT / "examples/greenfield/ia-app-pr-49137/replay/step1.source-trace.json"
    ).read_text()
)


def _trace() -> dict[str, object]:
    trace = copy.deepcopy(SOURCE_TRACE)
    trace.update(
        {
            "analysis_kind": "greenfield_pr_impact_step_1_5",
            "affected_symbols": [
                {"symbol": symbol, "path": path, "line": 1, "role": "entry"}
                for symbol, path in trace["behaviors"][0]["symbol_paths"].items()
            ],
            "calls": [
                {
                    **trace["behaviors"][0]["edges"][0],
                    "source_revision": STEP1["input"]["head_sha"],
                    "target_path": "app/source/company/AllocationTxnHelper.cls",
                    "resolution": "exact",
                }
            ],
            "input": {
                "repository": STEP1["input"]["repository"],
                "repo_key": STEP1["input"]["repo_key"],
                "pr_number": STEP1["input"]["pr_number"],
                "base_sha": STEP1["input"]["base_sha"],
                "head_sha": STEP1["input"]["head_sha"],
                "changed_paths": STEP1["input"]["changed_paths"],
            },
            "surfaces": {
                name: {"status": status}
                for name, status in trace["behaviors"][0]["surfaces"].items()
            },
        }
    )
    return normalize_trace(
        STEP1,
        trace,
        agent_metadata={"name": "fixture", "model": "fixture", "timeout_seconds": 1},
        context_sha256="a" * 64,
    )


def test_codex_trace_validation_preserves_exact_surface_states() -> None:
    trace = _trace()
    assert validate_trace(STEP1, trace) == []
    assert trace["surfaces"]["xml_api"] == "not_run"


def test_trace_provenance_bindings_are_fail_closed() -> None:
    trace = _trace()
    trace["provenance"]["source_revision"] = "0" * 40
    assert any("source_revision" in error for error in validate_trace(STEP1, trace))

    trace = _trace()
    trace["provenance"]["step1_evidence_sha256"] = "0" * 64
    assert any("fingerprint" in error for error in validate_trace(STEP1, trace))

    trace = _trace()
    trace["provenance"]["trace_sha256"] = "0" * 64
    assert any("trace_sha256" in error for error in validate_trace(STEP1, trace))


def test_trace_rejects_unchanged_affected_symbol() -> None:
    trace = _trace()
    trace["affected_symbols"].append(
        {"symbol": "Other::run", "path": "app/source/other.cls", "line": 1}
    )
    assert any("changed path" in error for error in validate_trace(STEP1, trace))


def test_trace_rejects_prose_or_missing_calls() -> None:
    trace = _trace()
    del trace["calls"]
    assert any(
        "calls must be a list" in error for error in validate_trace(STEP1, trace)
    )


def test_trace_requires_pr_and_base_identity() -> None:
    trace = _trace()
    del trace["input"]["base_sha"]
    assert any("base_sha" in error for error in validate_trace(STEP1, trace))


def test_trace_rejects_call_not_backed_by_behavior_edge() -> None:
    trace = _trace()
    trace["calls"][0]["target_symbol"] = "Unbacked::call"
    assert any("exactly match" in error for error in validate_trace(STEP1, trace))


def test_context_reads_target_revision_only(tmp_path: Path) -> None:
    repo = tmp_path / "source"
    repo.mkdir()
    import subprocess

    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@example.test"],
        check=True,
    )
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    (repo / "changed.php").write_text("<?php echo 'target';\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "target"], check=True)
    revision = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    step1 = copy.deepcopy(STEP1)
    step1["input"].update(
        {
            "target_revision": revision,
            "head_sha": revision,
            "base_revision": revision,
            "base_sha": revision,
            "changed_paths": ["changed.php"],
        }
    )
    step1["changed_files"] = [
        {"path": "changed.php", "filename": "changed.php", "status": "modified"}
    ]
    step1["pr_metadata"]["base_revision"] = revision
    step1["pr_metadata"]["target_revision"] = revision
    step1["provenance"]["evidence_sha256"] = evidence_fingerprint(step1)
    context = build_context(step1, repo)
    assert context["changed_files"][0]["content"] == "<?php echo 'target';\n"
    with pytest.raises(CodexAgentError, match="max_file_bytes"):
        build_context(step1, repo, max_file_bytes=5)


def test_runner_writes_trace_and_contract(tmp_path: Path) -> None:
    trace = _trace()
    trace_path = tmp_path / "trace.json"
    contract_path = tmp_path / "contract.json"
    with patch(
        "scripts.trace_greenfield_step1_5.run_codex_trace",
        return_value=(trace, {"context_sha256": "a" * 64}),
    ):
        result = trace_greenfield_step1_5.main(
            [
                "--step1-report",
                str(ROOT / "examples/greenfield/ia-app-pr-49137/replay/step1.json"),
                "--source-root",
                str(tmp_path),
                "--trace-output",
                str(trace_path),
                "--contract-output",
                str(contract_path),
            ]
        )
    assert result == 0
    assert (
        json.loads(trace_path.read_text())["analysis_kind"]
        == "greenfield_pr_impact_step_1_5"
    )
    assert (
        json.loads(contract_path.read_text())["artifact_kind"]
        == "generated_behavior_contract"
    )


def test_codex_failure_is_explicit() -> None:
    with pytest.raises(CodexAgentError):
        run_codex_trace(STEP1, "/path/that/does/not/exist", timeout=1)


def test_test_proposal_requires_exact_target_and_evidence() -> None:
    proposal = {
        "schema_version": "0.1",
        "analysis_kind": "greenfield_pr_test_proposal",
        "status": "complete",
        "input": {
            "source_repository": "ia-main",
            "source_revision": "a" * 40,
            "changed_paths": ["app/source/a.php"],
        },
        "proposals": [
            {
                "target_repository": "intacct/tests",
                "target_base_revision": "b" * 40,
                "paths": ["tests/a.feature"],
                "operation": "update",
                "test_area": "a",
                "rationale": "declared obligation",
                "evidence": [{"kind": "step4_report", "sha256": "c" * 64}],
                "validation_commands": [{"argv": ["pytest", "tests/a.py"], "cwd": "."}],
            }
        ],
        "findings": [],
        "provenance": {"read_only": True},
    }
    assert validate_test_proposal(proposal) == []
    proposal["proposals"][0]["paths"] = ["tests/*.feature"]
    assert validate_test_proposal(proposal)


def test_step2_does_not_refetch_supplied_inventory() -> None:
    supplied = [{"repository": "intacct/tests"}]
    assert trace_greenfield_step2._supplied_inventory_repositories(supplied) == {
        "intacct/tests"
    }
