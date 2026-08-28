from __future__ import annotations

import copy
import json
import re
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from greenfield.artifact_io import artifact_sha256
from greenfield.step1_5_trace import normalize_trace, validate_trace
from greenfield.step1_capture import evidence_fingerprint
from greenfield.strands_agent import (
    Step1TraceFailure,
    StrandsAgentError,
    _prompt,
    _run_strands_json,
    build_context,
    run_strands_trace,
)
from greenfield.strands_config import StrandsConfigError, load_strands_config
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


def _trace_repo(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "source"
    repo.mkdir()
    import subprocess

    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@example.test"],
        check=True,
    )
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    paths = {
        "app/source/company/AllocationTxnHelper.cls": "<?php echo 'base-alloc';\n",
        "app/source/gl/GLBatchManager.cls": "<?php echo 'base-gl';\n",
    }
    for path, content in paths.items():
        file_path = repo / path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
    base = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    for path, content in {
        "app/source/company/AllocationTxnHelper.cls": "<?php echo 'head-alloc';\n",
        "app/source/gl/GLBatchManager.cls": "<?php echo 'head-gl';\n",
    }.items():
        (repo / path).write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "commit", "-qam", "head"], check=True)
    head = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    return repo, base, head


def test_strands_trace_validation_preserves_exact_surface_states() -> None:
    trace = _trace()
    assert validate_trace(STEP1, trace) == []
    assert trace["surfaces"]["xml_api"] == "not_run"


def test_normalize_trace_accepts_provider_surface_records() -> None:
    raw = _trace()
    raw["surfaces"] = [
        {
            "surface": "http_qrequest",
            "status": "available",
            "path": "app/source/gl/GLMatchingManager.cls",
            "lines": {"start": 87, "end": 120},
            "notes": "provider metadata is not trusted",
        },
        {"surface": "rest_api", "status": "not_run"},
    ]
    normalized = normalize_trace(
        STEP1,
        raw,
        agent_metadata={"name": "fixture", "model": "fixture", "timeout_seconds": 1},
        context_sha256="a" * 64,
    )

    assert normalized["surfaces"] == {
        "http_qrequest": "available",
        "rest_api": "not_run",
    }
    assert validate_trace(STEP1, normalized) == []


def test_normalize_trace_accepts_legacy_call_kind() -> None:
    raw = _trace()
    call = raw["calls"][0]
    call["kind"] = call.pop("relationship_type")
    normalized = normalize_trace(
        STEP1,
        raw,
        agent_metadata={"name": "fixture", "model": "fixture", "timeout_seconds": 1},
        context_sha256="a" * 64,
    )

    assert normalized["calls"][0]["relationship_type"] == "CALLS"
    assert validate_trace(STEP1, normalized) == []


def test_normalize_trace_accepts_behavior_symbol_path_records() -> None:
    raw = _trace()
    raw["behaviors"][0]["symbol_paths"] = [
        {
            "symbol": "AllocationTxnHelper::applyAllocation",
            "path": "app/source/company/AllocationTxnHelper.cls",
            "line": 42,
            "revision": STEP1["input"]["head_sha"],
            "notes": "provider metadata is ignored",
        },
        {
            "symbol": "GLBatchManager::glTranslateApplyAllocation",
            "path": "app/source/gl/GLBatchManager.cls",
            "line": 18,
            "revision": STEP1["input"]["head_sha"],
        },
    ]
    edge = raw["behaviors"][0]["edges"][0]
    edge["kind"] = edge.pop("relationship_type")

    normalized = normalize_trace(
        STEP1,
        raw,
        agent_metadata={"name": "fixture", "model": "fixture", "timeout_seconds": 1},
        context_sha256="a" * 64,
    )

    assert normalized["behaviors"][0]["symbol_paths"] == {
        "AllocationTxnHelper::applyAllocation": "app/source/company/AllocationTxnHelper.cls",
        "GLBatchManager::glTranslateApplyAllocation": "app/source/gl/GLBatchManager.cls",
    }
    assert normalized["behaviors"][0]["edges"][0]["relationship_type"] == "CALLS"
    assert validate_trace(STEP1, normalized) == []


def test_normalize_trace_rejects_conflicting_call_relation_keys() -> None:
    raw = _trace()
    raw["calls"][0]["kind"] = "STATIC_CALLS"

    with pytest.raises(ValueError, match="conflicting relationship_type and kind"):
        normalize_trace(
            STEP1,
            raw,
            agent_metadata={"name": "fixture", "model": "fixture", "timeout_seconds": 1},
            context_sha256="a" * 64,
        )


@pytest.mark.parametrize(
    ("surfaces", "message"),
    [
        (["not-an-object"], "surfaces[0] must be an object"),
        ([{"status": "available"}], "surfaces[0].surface must be a non-empty string"),
        ([{"surface": "http_qrequest"}], "surfaces[0].status must be a non-empty string"),
        ([{"surface": "http_qrequest", "status": "unknown"}], "unsupported status"),
        (
            [
                {"surface": "http_qrequest", "status": "available"},
                {"surface": "http_qrequest", "status": "empty"},
            ],
            "duplicate surface",
        ),
    ],
)
def test_normalize_trace_rejects_malformed_surface_records(
    surfaces: list[object], message: str
) -> None:
    raw = _trace()
    raw["surfaces"] = surfaces

    with pytest.raises(ValueError, match=re.escape(message)):
        normalize_trace(
            STEP1,
            raw,
            agent_metadata={"name": "fixture", "model": "fixture", "timeout_seconds": 1},
            context_sha256="a" * 64,
        )


def test_normalize_trace_rejects_malformed_line_evidence() -> None:
    raw = _trace()
    raw["calls"][0]["source_line"] = None

    with pytest.raises(ValueError, match="source_line must be a positive integer"):
        normalize_trace(
            STEP1,
            raw,
            agent_metadata={"name": "fixture", "model": "fixture", "timeout_seconds": 1},
            context_sha256="a" * 64,
        )


def test_normalize_trace_rejects_unbound_symbol_path() -> None:
    raw = _trace()
    raw["behaviors"][0]["symbol_paths"][
        "GLBatchManager::glTranslateApplyAllocation"
    ] = "app/source/not-captured.cls"

    with pytest.raises(ValueError, match="unbound path"):
        normalize_trace(
            STEP1,
            raw,
            agent_metadata={"name": "fixture", "model": "fixture", "timeout_seconds": 1},
            context_sha256="a" * 64,
        )


def test_normalize_trace_rejects_wildcard_target_path() -> None:
    raw = _trace()
    raw["behaviors"][0]["edges"][0]["target_path"] = "app/source/**/*.cls"

    with pytest.raises(ValueError, match="target_path must be an exact path"):
        normalize_trace(
            STEP1,
            raw,
            agent_metadata={"name": "fixture", "model": "fixture", "timeout_seconds": 1},
            context_sha256="a" * 64,
        )


def test_validate_trace_rejects_nested_edge_revision_and_line() -> None:
    trace = _trace()
    edge = trace["behaviors"][0]["edges"][0]
    edge["target_revision"] = "0" * 40
    unsigned = copy.deepcopy(trace)
    unsigned["provenance"].pop("trace_sha256", None)
    trace["provenance"]["trace_sha256"] = artifact_sha256(unsigned)

    errors = validate_trace(STEP1, trace)

    assert any("target_revision" in error for error in errors)

    trace = _trace()
    trace["behaviors"][0]["edges"][0]["target_line"] = 0
    unsigned = copy.deepcopy(trace)
    unsigned["provenance"].pop("trace_sha256", None)
    trace["provenance"]["trace_sha256"] = artifact_sha256(unsigned)
    errors = validate_trace(STEP1, trace)
    assert any("target_line" in error for error in errors)


def test_run_strands_trace_persists_failure_diagnostic(tmp_path: Path) -> None:
    repo, base, head = _trace_repo(tmp_path)
    step1 = copy.deepcopy(STEP1)
    step1["input"].update(
        {
            "target_revision": head,
            "head_sha": head,
            "base_revision": base,
            "base_sha": base,
            "changed_paths": [
                "app/source/company/AllocationTxnHelper.cls",
                "app/source/gl/GLBatchManager.cls",
            ],
        }
    )
    step1["changed_files"] = [
        {
            "path": "app/source/company/AllocationTxnHelper.cls",
            "filename": "app/source/company/AllocationTxnHelper.cls",
            "status": "modified",
        },
        {
            "path": "app/source/gl/GLBatchManager.cls",
            "filename": "app/source/gl/GLBatchManager.cls",
            "status": "modified",
        },
    ]
    step1["pr_metadata"]["base_revision"] = base
    step1["pr_metadata"]["target_revision"] = head
    step1["provenance"]["evidence_sha256"] = evidence_fingerprint(step1)
    raw = _trace()
    raw["surfaces"] = ["not-an-object"]
    diagnostic_path = tmp_path / "step1.5.diagnostic.json"
    contract_path = tmp_path / "step1.5.contract.json"

    def factory(_model: str | None, *, tools: list[object] | None = None):
        def agent(_prompt: str) -> str:
            return json.dumps(raw)

        return agent

    with pytest.raises(Step1TraceFailure, match="surfaces\\[0\\] must be an object"):
        run_strands_trace(
            step1,
            repo,
            model="test-model",
            contract_path=contract_path,
            diagnostic_output=diagnostic_path,
            agent_factory=factory,
        )

    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    assert diagnostic["analysis_kind"] == "greenfield_pr_impact_step_1_5_diagnostic"
    assert diagnostic["stage"] == "normalize_trace"
    assert diagnostic["contract_path"] == str(contract_path)
    assert diagnostic["raw_provider_response"]["surfaces"] == ["not-an-object"]
    assert diagnostic["source"]["target_revision"] == head


def test_step1_5_prompt_requires_canonical_surface_map() -> None:
    prompt = _prompt({"changed_files": []}, ROOT)

    assert "surfaces must be an object/map, not a list" in prompt
    assert "relationship_type, not kind" in prompt
    assert "symbol_paths as an object/map, not a list" in prompt


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
    with pytest.raises(StrandsAgentError, match="max_file_bytes"):
        build_context(step1, repo, max_file_bytes=5)


def test_runner_writes_trace_and_contract(tmp_path: Path) -> None:
    trace = _trace()
    trace_path = tmp_path / "trace.json"
    contract_path = tmp_path / "contract.json"
    config_path = tmp_path / "strands.yaml"
    config_path.write_text(
        "region: us-east-1\nmodel: test-model\ntimeout_seconds: 12\n",
        encoding="utf-8",
    )
    with patch(
        "scripts.trace_greenfield_step1_5.run_strands_trace",
        return_value=(trace, {"context_sha256": "a" * 64}),
    ) as run_trace:
        result = trace_greenfield_step1_5.main(
            [
                "--step1-report",
                str(ROOT / "examples/greenfield/ia-app-pr-49137/replay/step1.json"),
                "--source-root",
                str(tmp_path),
                "--strands-config",
                str(config_path),
                "--trace-output",
                str(trace_path),
                "--contract-output",
                str(contract_path),
            ]
        )
    assert result == 0
    assert run_trace.call_args.kwargs["model"] == "test-model"
    assert run_trace.call_args.kwargs["timeout"] == 12
    assert (
        json.loads(trace_path.read_text())["analysis_kind"]
        == "greenfield_pr_impact_step_1_5"
    )
    assert (
        json.loads(contract_path.read_text())["artifact_kind"]
        == "generated_behavior_contract"
    )


def test_strands_failure_is_explicit() -> None:
    with pytest.raises(StrandsAgentError):
        run_strands_trace(STEP1, "/path/that/does/not/exist", timeout=1)


def test_strands_timeout_returns_without_waiting_for_blocked_agent() -> None:
    release = threading.Event()

    def factory(_model: str | None):
        def agent(_prompt: str) -> str:
            release.wait(5)
            return "{}"

        return agent

    started = time.monotonic()
    try:
        with pytest.raises(StrandsAgentError, match="timed out"):
            _run_strands_json("prompt", model=None, timeout=0.01, agent_factory=factory)
    finally:
        release.set()
    assert time.monotonic() - started < 1


def test_strands_config_rejects_repo_secrets(tmp_path: Path) -> None:
    config_path = tmp_path / "strands.yaml"
    config_path.write_text(
        "region: us-east-1\naws_secret_access_key: should-not-be-here\n",
        encoding="utf-8",
    )
    with pytest.raises(StrandsConfigError, match="must not contain secret fields"):
        load_strands_config(config_path)


def test_strands_config_rejects_nested_repo_secrets(tmp_path: Path) -> None:
    config_path = tmp_path / "strands.yaml"
    config_path.write_text(
        "providers:\n  - name: bedrock\n    token: should-not-be-here\n"
        "aws:\n  secret_key: also-not-here\n",
        encoding="utf-8",
    )
    with pytest.raises(StrandsConfigError) as excinfo:
        load_strands_config(config_path)
    message = str(excinfo.value)
    assert "<root>.providers[0].token" in message
    assert "<root>.aws.secret_key" in message
    assert "should-not-be-here" not in message


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
