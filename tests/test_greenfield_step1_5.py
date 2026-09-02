from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError
from strands.types.exceptions import MaxTokensReachedException

from greenfield.artifact_io import artifact_sha256
from greenfield.step1_5_trace import normalize_trace, validate_trace
from greenfield.step1_capture import evidence_fingerprint
from greenfield.strands_agent import (
    PromptBudgetError,
    Step1TraceFailure,
    StrandsAgentError,
    _prompt,
    _run_strands_json,
    _Step15StructuredOutput,
    _with_preceding_context,
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


def test_normalize_trace_derives_calls_from_behavior_edges() -> None:
    raw = _trace()
    raw.pop("calls")
    # Expected edges come from the raw fixture, not from an already-derived trace.
    source_edges = [
        edge
        for behavior in SOURCE_TRACE["behaviors"]
        for edge in behavior["edges"]
    ]
    expected_keys = sorted(
        (edge["source_symbol"], edge["target_symbol"]) for edge in source_edges
    )

    normalized = normalize_trace(
        STEP1,
        raw,
        agent_metadata={"name": "fixture", "model": "fixture", "timeout_seconds": 1},
        context_sha256="a" * 64,
    )

    assert expected_keys
    assert sorted(
        (call["source_symbol"], call["target_symbol"]) for call in normalized["calls"]
    ) == expected_keys
    assert all(call["resolution"] == "exact" for call in normalized["calls"])
    assert validate_trace(STEP1, normalized) == []


def test_normalize_trace_ignores_model_supplied_calls() -> None:
    raw = _trace()
    raw["calls"] = [{"source_symbol": "Fabricated", "target_symbol": "Edge"}]

    normalized = normalize_trace(
        STEP1,
        raw,
        agent_metadata={"name": "fixture", "model": "fixture", "timeout_seconds": 1},
        context_sha256="a" * 64,
    )

    assert all(call["source_symbol"] != "Fabricated" for call in normalized["calls"])
    assert validate_trace(STEP1, normalized) == []


def test_normalize_trace_excludes_edges_the_traversal_cannot_reach() -> None:
    raw = _trace()
    raw.pop("calls")
    orphan = copy.deepcopy(raw["behaviors"][0]["edges"][0])
    orphan["source_symbol"] = "Unreachable::caller"
    orphan["target_symbol"] = "Unreachable::callee"
    raw["behaviors"][0]["edges"].append(orphan)

    normalized = normalize_trace(
        STEP1,
        raw,
        agent_metadata={"name": "fixture", "model": "fixture", "timeout_seconds": 1},
        context_sha256="a" * 64,
    )

    assert all(
        call["source_symbol"] != "Unreachable::caller" for call in normalized["calls"]
    )
    assert validate_trace(STEP1, normalized) == []


def test_normalize_trace_rejects_conflicting_call_relation_keys() -> None:
    raw = _trace()
    raw["behaviors"][0]["edges"][0]["kind"] = "STATIC_CALLS"

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
    raw["behaviors"][0]["edges"][0]["source_line"] = None

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


def test_trace_accepts_truncated_partial_output() -> None:
    trace = _trace()
    trace["truncated"] = True
    trace["truncation_reason"] = "provider_output_budget"
    trace["omitted_counts"] = {"calls": 5, "behaviors": 1}
    unsigned = copy.deepcopy(trace)
    unsigned["provenance"].pop("trace_sha256", None)
    trace["provenance"]["trace_sha256"] = artifact_sha256(unsigned)

    assert validate_trace(STEP1, trace) == []


def test_trace_rejects_invalid_truncation_metadata() -> None:
    trace = _trace()
    trace["truncated"] = "yes"
    unsigned = copy.deepcopy(trace)
    unsigned["provenance"].pop("trace_sha256", None)
    trace["provenance"]["trace_sha256"] = artifact_sha256(unsigned)

    errors = validate_trace(STEP1, trace)
    assert any("truncated must be a boolean" in error for error in errors)


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
    assert diagnostic["request"]["prompt_bytes"] > 0
    assert len(diagnostic["request"]["prompt_sha256"]) == 64
    assert diagnostic["request"]["elapsed_milliseconds"] >= 0


def _changed_files_step1(base: str, head: str) -> dict[str, object]:
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
    return step1


class _TruncatingAgent:
    """Mimics Strands appending the partial assistant message before raising."""

    def __init__(self, fragments: list[str], *, always_truncate: bool = False) -> None:
        self._fragments = fragments
        self._always_truncate = always_truncate
        self.messages: list[dict[str, object]] = []
        self.calls = 0
        self.prompts: list[object] = []
        self.trailing_assistants_at_call: list[int] = []

    def __call__(self, prompt: object = None) -> str:
        fragment = self._fragments[min(self.calls, len(self._fragments) - 1)]
        self.calls += 1
        self.prompts.append(prompt)
        trailing = 0
        for row in reversed(self.messages):
            if row.get("role") != "assistant":
                break
            trailing += 1
        self.trailing_assistants_at_call.append(trailing)
        if self._always_truncate or self.calls < len(self._fragments):
            self.messages.append(
                {"role": "assistant", "content": [{"text": fragment}]}
            )
            raise MaxTokensReachedException("output token limit reached")
        self.messages.append({"role": "assistant", "content": [{"text": fragment}]})
        return fragment


def test_run_strands_trace_continues_after_max_tokens(tmp_path: Path) -> None:
    repo, base, head = _trace_repo(tmp_path)
    step1 = _changed_files_step1(base, head)
    payload = json.dumps(_trace()).replace(STEP1["input"]["head_sha"], head)
    split = len(payload) // 2
    agent = _TruncatingAgent([payload[:split], payload[split:]])

    def factory(_model: str | None, *, tools: list[object] | None = None):
        return agent

    trace, _context = run_strands_trace(
        step1,
        repo,
        model="test-model",
        agent_factory=factory,
    )

    assert agent.calls == 2
    assert agent.prompts[1] is None
    assert not validate_trace(step1, trace)


def test_continuation_records_boundary_observed_provenance(tmp_path: Path) -> None:
    repo, base, head = _trace_repo(tmp_path)
    step1 = _changed_files_step1(base, head)
    payload = json.dumps(_trace()).replace(STEP1["input"]["head_sha"], head)
    split = len(payload) // 2
    agent = _TruncatingAgent([payload[:split], payload[split:]])

    def factory(_model: str | None, *, tools: list[object] | None = None):
        return agent

    trace, _context = run_strands_trace(
        step1, repo, model="test-model", agent_factory=factory
    )

    assert trace["provenance"]["agent"]["continuation_attempts"] == 1
    assert trace["provenance"]["agent"]["join_whitespace_trimmed"] is False


def test_continuation_rejects_truncated_tool_use(tmp_path: Path) -> None:
    repo, base, head = _trace_repo(tmp_path)
    step1 = _changed_files_step1(base, head)
    agent = _TruncatingAgent(
        [
            (
                '{"behaviors": The selected tool read_source\'s tool use was '
                "incomplete due to maximum token limits being reached."
            ),
            "{}",
        ]
    )

    def factory(_model: str | None, *, tools: list[object] | None = None):
        return agent

    with pytest.raises(Step1TraceFailure, match="cannot be resumed as JSON"):
        run_strands_trace(step1, repo, model="test-model", agent_factory=factory)

    assert agent.calls == 1


def test_continuation_refuses_to_trim_inside_a_json_string(tmp_path: Path) -> None:
    repo, base, head = _trace_repo(tmp_path)
    step1 = _changed_files_step1(base, head)
    agent = _TruncatingAgent(['{"rationale": "Recomputes the allocation ', '"}'])

    def factory(_model: str | None, *, tools: list[object] | None = None):
        return agent

    with pytest.raises(Step1TraceFailure, match="silently alter the captured evidence"):
        run_strands_trace(step1, repo, model="test-model", agent_factory=factory)


def test_continuation_prefills_a_single_coalesced_assistant_turn(
    tmp_path: Path,
) -> None:
    repo, base, head = _trace_repo(tmp_path)
    step1 = _changed_files_step1(base, head)
    payload = json.dumps(_trace()).replace(STEP1["input"]["head_sha"], head)
    third = len(payload) // 3
    agent = _TruncatingAgent(
        [payload[:third] + "   ", payload[third : third * 2], payload[third * 2 :]]
    )

    def factory(_model: str | None, *, tools: list[object] | None = None):
        return agent

    trace, _context = run_strands_trace(
        step1,
        repo,
        model="test-model",
        max_continuations=2,
        agent_factory=factory,
    )

    assert agent.calls == 3
    assert agent.prompts[1] is None and agent.prompts[2] is None
    assert agent.trailing_assistants_at_call == [0, 1, 1], (
        "each continuation must prefill exactly one trailing assistant turn"
    )
    prefilled = str(agent.messages[0]["content"][0]["text"])  # type: ignore[index]
    assert prefilled == prefilled.rstrip(), "prefill must not end in whitespace"
    assert not validate_trace(step1, trace)


def test_run_strands_trace_fails_closed_after_continuation_budget(
    tmp_path: Path,
) -> None:
    repo, base, head = _trace_repo(tmp_path)
    step1 = _changed_files_step1(base, head)
    diagnostic_path = tmp_path / "step1.5.diagnostic.json"
    agent = _TruncatingAgent(['{"behaviors": '], always_truncate=True)

    def factory(_model: str | None, *, tools: list[object] | None = None):
        return agent

    with pytest.raises(Step1TraceFailure, match="continuation attempts"):
        run_strands_trace(
            step1,
            repo,
            model="test-model",
            max_continuations=1,
            diagnostic_output=diagnostic_path,
            agent_factory=factory,
        )

    assert agent.calls == 2
    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    assert diagnostic["stage"] == "provider_call"
    assert diagnostic["provider"]["continuation_attempts"] == 1


def test_run_strands_trace_reports_output_budget_rejection(tmp_path: Path) -> None:
    repo, base, head = _trace_repo(tmp_path)
    step1 = _changed_files_step1(base, head)
    diagnostic_path = tmp_path / "step1.5.diagnostic.json"

    def factory(_model: str | None, *, tools: list[object] | None = None):
        def agent(_prompt: str) -> str:
            raise RuntimeError(
                "An error occurred (ValidationException) when calling the Converse "
                "operation: The maximum tokens you requested exceeds the model "
                "limit of 10000."
            )

        return agent

    with pytest.raises(Step1TraceFailure, match="rejected max_tokens=32000"):
        run_strands_trace(
            step1,
            repo,
            model="us.amazon.nova-pro-v1:0",
            max_tokens=32000,
            diagnostic_output=diagnostic_path,
            agent_factory=factory,
        )

    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    assert "us.amazon.nova-pro-v1:0" in diagnostic["reason"]
    assert diagnostic["provider"]["max_tokens"] == 32000


def test_run_strands_json_reads_nested_agent_message() -> None:
    class FakeAgentResult:
        def __init__(self) -> None:
            self.message = {"content": [{"text": '{"status": "ok"}'}]}

        def __str__(self) -> str:
            return ""

    def factory(_model: str | None, *, tools: list[object] | None = None):
        def agent(_prompt: str) -> FakeAgentResult:
            return FakeAgentResult()

        return agent

    parsed, delivery = _run_strands_json(
        "prompt",
        model="test-model",
        timeout=1,
        agent_factory=factory,
    )

    assert parsed == {"status": "ok"}
    assert delivery["continuation_attempts"] == 0


def test_default_strands_agent_uses_structured_output_over_provider_prose() -> None:
    captured: dict[str, object] = {}

    class FakeStructuredOutput:
        def model_dump(self, *, mode: str) -> dict[str, str]:
            assert mode == "json"
            return {"status": "ok"}

    class FakeAgentResult:
        def __init__(self) -> None:
            self.stop_reason = "end_turn"
            self.message = {
                "content": [{"text": "Narrative output must not be parsed."}]
            }
            self.structured_output = FakeStructuredOutput()

    def default_factory(
        _model: str | None,
        *,
        tools: list[object] | None = None,
        max_tokens: int | None = None,
        boto_client_config: object | None = None,
        structured_output_model: object | None = None,
    ):
        del tools, max_tokens, structured_output_model
        captured["boto_client_config"] = boto_client_config

        def agent(_prompt: str) -> FakeAgentResult:
            return FakeAgentResult()

        return agent

    with patch("greenfield.strands_agent._default_agent_factory", default_factory):
        parsed, delivery = _run_strands_json("prompt", model="test-model", timeout=1)

    assert parsed == {"status": "ok"}
    assert delivery == {"continuation_attempts": 0, "join_whitespace_trimmed": False}
    client_config = captured["boto_client_config"]
    assert client_config is not None
    assert client_config.retries == {"mode": "standard", "total_max_attempts": 2}
    assert client_config.connect_timeout == 10
    assert client_config.read_timeout == 120


def test_explicit_structured_output_model_accepts_canonical_provider_shape() -> None:
    raw = _trace()
    for behavior in raw["behaviors"]:
        behavior["symbol_paths"] = {
            symbol: {"path": path}
            for symbol, path in behavior["symbol_paths"].items()
        }

    provider_output = _Step15StructuredOutput.model_validate(raw).model_dump(
        mode="json"
    )
    normalized = normalize_trace(
        STEP1,
        provider_output,
        agent_metadata={"name": "test", "model": "test", "timeout_seconds": 1},
        context_sha256="a" * 64,
    )

    assert validate_trace(STEP1, normalized) == []


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("entry_symbols", "Field required"),
        ("target_path", "Field required"),
    ],
)
def test_explicit_structured_output_model_rejects_required_provider_fields(
    field: str, message: str
) -> None:
    raw = _trace()
    raw["behaviors"] = copy.deepcopy(raw["behaviors"][:1])
    raw["behaviors"][0]["symbol_paths"] = {
        symbol: {"path": path}
        for symbol, path in raw["behaviors"][0]["symbol_paths"].items()
    }
    if field == "target_path":
        raw["behaviors"][0]["edges"] = copy.deepcopy(
            raw["behaviors"][0]["edges"][:1]
        )
        raw["behaviors"][0]["edges"][0].pop("target_path")
    else:
        raw["behaviors"][0].pop(field)

    with pytest.raises(ValidationError, match=message):
        _Step15StructuredOutput.model_validate(raw)


def test_default_strands_agent_rejects_missing_structured_output(tmp_path: Path) -> None:
    repo, base, head = _trace_repo(tmp_path)
    step1 = _changed_files_step1(base, head)
    diagnostic_path = tmp_path / "step1.5.diagnostic.json"

    class FakeAgentResult:
        def __init__(self) -> None:
            self.stop_reason = "end_turn"
            self.message = {"content": [{"text": "secret provider prose"}]}
            self.structured_output = None

    def default_factory(
        _model: str | None,
        *,
        tools: list[object] | None = None,
        max_tokens: int | None = None,
    ):
        del tools, max_tokens

        def agent(_prompt: str) -> FakeAgentResult:
            return FakeAgentResult()

        return agent

    with patch("greenfield.strands_agent._default_agent_factory", default_factory), pytest.raises(
        Step1TraceFailure, match="completed without structured output"
    ):
        run_strands_trace(
            step1,
            repo,
            model="test-model",
            diagnostic_output=diagnostic_path,
        )

    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    metadata = diagnostic["provider"]["response"]
    assert metadata["extraction_strategy"] == "structured_output_missing"
    assert metadata["stop_reason"] == "end_turn"
    assert "secret provider prose" not in json.dumps(diagnostic)


def test_run_strands_json_rejects_empty_output() -> None:
    def factory(_model: str | None, *, tools: list[object] | None = None):
        def agent(_prompt: str) -> str:
            return "  \n"

        return agent

    with pytest.raises(StrandsAgentError, match="empty output"):
        _run_strands_json(
            "prompt",
            model="test-model",
            timeout=1,
            agent_factory=factory,
        )


def test_run_strands_trace_records_malformed_response_metadata(
    tmp_path: Path,
) -> None:
    repo, base, head = _trace_repo(tmp_path)
    step1 = _changed_files_step1(base, head)
    diagnostic_path = tmp_path / "step1.5.diagnostic.json"
    response = "This is not JSON."

    def factory(_model: str | None, *, tools: list[object] | None = None):
        def agent(_prompt: str) -> str:
            return response

        return agent

    with pytest.raises(Step1TraceFailure, match="did not produce JSON"):
        run_strands_trace(
            step1,
            repo,
            model="test-model",
            diagnostic_output=diagnostic_path,
            agent_factory=factory,
        )

    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    metadata = diagnostic["provider"]["response"]
    assert metadata == {
        "content_block_keys": [],
        "extraction_strategy": "direct_string",
        "first_non_whitespace_character": "T",
        "result_type": "builtins.str",
        "stop_reason": None,
        "text_bytes": len(response.encode("utf-8")),
        "text_sha256": hashlib.sha256(response.encode("utf-8")).hexdigest(),
    }
    assert "raw_provider_response" not in diagnostic
    assert response not in json.dumps(diagnostic)


def test_run_strands_trace_rejects_unknown_response_shape_without_stringifying(
    tmp_path: Path,
) -> None:
    repo, base, head = _trace_repo(tmp_path)
    step1 = _changed_files_step1(base, head)
    diagnostic_path = tmp_path / "step1.5.diagnostic.json"

    class UnsupportedAgentResult:
        def __init__(self) -> None:
            self.stop_reason = "end_turn"
            self.message = {
                "content": [{"reasoningContent": {"text": "not retained"}}]
            }

        def __str__(self) -> str:
            return "secret string fallback must not be used"

    def factory(_model: str | None, *, tools: list[object] | None = None):
        def agent(_prompt: str) -> UnsupportedAgentResult:
            return UnsupportedAgentResult()

        return agent

    with pytest.raises(Step1TraceFailure, match="unsupported response shape"):
        run_strands_trace(
            step1,
            repo,
            model="test-model",
            diagnostic_output=diagnostic_path,
            agent_factory=factory,
        )

    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    metadata = diagnostic["provider"]["response"]
    assert metadata["extraction_strategy"] == "unsupported"
    assert metadata["stop_reason"] == "end_turn"
    assert metadata["content_block_keys"] == [["reasoningContent"]]
    assert "text_sha256" not in metadata
    assert "secret string fallback" not in json.dumps(diagnostic)


def test_run_strands_trace_persists_sanitized_provider_error(
    tmp_path: Path,
) -> None:
    repo, base, head = _trace_repo(tmp_path)
    step1 = copy.deepcopy(STEP1)
    step1["input"].update(
        {
            "target_revision": head,
            "head_sha": head,
            "base_revision": base,
            "base_sha": base,
            "changed_paths": ["app/source/company/AllocationTxnHelper.cls"],
        }
    )
    step1["changed_files"] = [
        {
            "path": "app/source/company/AllocationTxnHelper.cls",
            "filename": "app/source/company/AllocationTxnHelper.cls",
            "status": "modified",
        }
    ]
    step1["pr_metadata"]["base_revision"] = base
    step1["pr_metadata"]["target_revision"] = head
    step1["provenance"]["evidence_sha256"] = evidence_fingerprint(step1)
    diagnostic_path = tmp_path / "step1.5.diagnostic.json"

    class FakeProviderError(RuntimeError):
        pass

    def factory(_model: str | None, *, tools: list[object] | None = None):
        def agent(_prompt: str) -> str:
            raise FakeProviderError(
                "MaxTokensReachedException with AWS_SECRET_ACCESS_KEY=abc123, "
                "AWS_SESSION_TOKEN=tok123, access key ASIAABCDEFGHIJKLMNOP"
            )

        return agent

    with pytest.raises(Step1TraceFailure, match="FakeProviderError"):
        run_strands_trace(
            step1,
            repo,
            model="test-model",
            diagnostic_output=diagnostic_path,
            agent_factory=factory,
        )

    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    serialized = json.dumps(diagnostic)
    assert diagnostic["stage"] == "provider_call"
    assert diagnostic["provider"]["error"]["type"] == "FakeProviderError"
    assert "MaxTokensReachedException" in diagnostic["provider"]["error"]["message"]
    assert "FakeProviderError" in diagnostic["reason"]
    assert "aws_credential_status" in diagnostic
    assert "abc123" not in serialized
    assert "tok123" not in serialized
    assert "ASIAABCDEFGHIJKLMNOP" not in serialized


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
        {"path": "changed.php", "filename": "changed.php", "status": "added"}
    ]
    step1["pr_metadata"]["base_revision"] = revision
    step1["pr_metadata"]["target_revision"] = revision
    step1["provenance"]["evidence_sha256"] = evidence_fingerprint(step1)
    context = build_context(step1, repo)
    assert context["changed_files"][0]["content"] == "<?php echo 'target';\n"
    assert context["changed_files"][0]["full_blob_sha256"] == hashlib.sha256(
        b"<?php echo 'target';\n"
    ).hexdigest()


def test_hunk_context_adds_bounded_preceding_exact_lines() -> None:
    windows, added, limited = _with_preceding_context(
        [(300, 340)], total_lines=500
    )

    assert windows == [(44, 340)]
    assert added is True
    assert limited is False


def test_hunk_context_preserves_budget_limit_as_explicit_state() -> None:
    windows, added, limited = _with_preceding_context(
        [(300, 340), (700, 740), (1_100, 1_140)], total_lines=1_200
    )

    assert windows == [(44, 340), (444, 740), (1_100, 1_140)]
    assert added is True
    assert limited is True


def test_prompt_budget_failure_persists_source_context_and_diagnostic(tmp_path: Path) -> None:
    repo, base, head = _trace_repo(tmp_path)
    step1 = _changed_files_step1(base, head)
    context_path = tmp_path / "step1.5.source-context.json"
    diagnostic_path = tmp_path / "step1.5.diagnostic.json"

    with pytest.raises(Step1TraceFailure, match="max_prompt_bytes"):
        run_strands_trace(
            step1,
            repo,
            model="test-model",
            max_prompt_bytes=1,
            context_output=context_path,
            diagnostic_output=diagnostic_path,
        )

    context = json.loads(context_path.read_text(encoding="utf-8"))
    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    assert context["changed_files"][0]["full_content"]
    assert context["changed_files"][0]["full_blob_sha256"]
    assert diagnostic["stage"] == "prompt_budget"
    assert diagnostic["request"]["agent_invocation_count"] == 0
    assert diagnostic["request"]["prompt_bytes"] > 1
    assert len(diagnostic["request"]["prompt_sha256"]) == 64
    assert diagnostic["request"]["prompt_max_bytes"] == 1


def test_build_context_handles_mode_only_changed_file(tmp_path: Path) -> None:
    repo, _base, base = _trace_repo(tmp_path)
    import subprocess

    path = "app/source/gl/GLBatchManager.cls"
    subprocess.run(
        ["git", "-C", str(repo), "update-index", "--chmod=+x", path], check=True
    )
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "mode-only"], check=True)
    head = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    step1 = _changed_files_step1(base, head)
    step1["input"]["changed_paths"] = [path]
    step1["changed_files"] = [{"path": path, "filename": path, "status": "modified"}]
    step1["provenance"]["evidence_sha256"] = evidence_fingerprint(step1)

    context = build_context(step1, repo)

    entry = context["changed_files"][0]
    assert entry["context_mode"] == "full_no_text_hunk"
    assert entry["content"] == entry["full_content"]
    assert entry["omitted_ranges"] == []


def test_build_context_hunk_centers_large_modified_file(tmp_path: Path) -> None:
    repo = tmp_path / "source"
    repo.mkdir()
    import subprocess

    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@example.test"],
        check=True,
    )
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    path = "app/source/gl/Big.cls"
    file_path = repo / path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"line {i}\n" for i in range(1, 301)]
    file_path.write_text("".join(lines), encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
    base = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    lines[199] = "line 200 changed\n"
    file_path.write_text("".join(lines), encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "commit", "-qam", "head"], check=True)
    head = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()

    step1 = copy.deepcopy(STEP1)
    step1["input"].update(
        {
            "target_revision": head,
            "head_sha": head,
            "base_revision": base,
            "base_sha": base,
            "changed_paths": [path],
        }
    )
    step1["changed_files"] = [{"path": path, "filename": path, "status": "modified"}]
    step1["pr_metadata"]["base_revision"] = base
    step1["pr_metadata"]["target_revision"] = head
    step1["provenance"]["evidence_sha256"] = evidence_fingerprint(step1)

    context = build_context(step1, repo)

    entry = context["changed_files"][0]
    assert entry["context_mode"] == "hunk_with_preceding_context"
    assert entry["truncated"] is True
    assert "--- lines" in entry["content"]
    assert "line 200 changed\n" in entry["content"]
    assert "line 1\n" in entry["content"]
    assert "line 300\n" not in entry["content"]
    prompt = _prompt(context, repo)
    assert "line 1\\n" in prompt
    assert "line 200 changed\\n" in prompt


def test_build_context_added_file_ignores_hunk_mode(tmp_path: Path) -> None:
    repo = tmp_path / "source"
    repo.mkdir()
    import subprocess

    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@example.test"],
        check=True,
    )
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    path = "app/source/gl/New.cls"
    file_path = repo / path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text("x" * 1000, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "head"], check=True)
    head = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()

    step1 = copy.deepcopy(STEP1)
    step1["input"].update(
        {
            "target_revision": head,
            "head_sha": head,
            "base_revision": head,
            "base_sha": head,
            "changed_paths": [path],
        }
    )
    step1["changed_files"] = [{"path": path, "filename": path, "status": "added"}]
    step1["pr_metadata"]["base_revision"] = head
    step1["pr_metadata"]["target_revision"] = head
    step1["provenance"]["evidence_sha256"] = evidence_fingerprint(step1)

    context = build_context(step1, repo)
    entry = context["changed_files"][0]
    assert entry["context_mode"] == "full"
    assert entry["full_blob_bytes"] == 1000


def test_prompt_budget_rejects_large_hunk_context_without_losing_source(tmp_path: Path) -> None:
    repo = tmp_path / "source"
    repo.mkdir()
    import subprocess

    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@example.test"],
        check=True,
    )
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    path = "app/source/gl/Scattered.cls"
    file_path = repo / path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"line {i}\n" for i in range(1, 2001)]
    file_path.write_text("".join(lines), encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
    base = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    # Scatter many small edits across the file so hunk windows (each ± 40
    # lines) still merge into a large aggregate excerpt.
    for offset in range(100, 2000, 100):
        lines[offset - 1] = f"line {offset} changed\n"
    file_path.write_text("".join(lines), encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "commit", "-qam", "head"], check=True)
    head = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()

    step1 = copy.deepcopy(STEP1)
    step1["input"].update(
        {
            "target_revision": head,
            "head_sha": head,
            "base_revision": base,
            "base_sha": base,
            "changed_paths": [path],
        }
    )
    step1["changed_files"] = [{"path": path, "filename": path, "status": "modified"}]
    step1["pr_metadata"]["base_revision"] = base
    step1["pr_metadata"]["target_revision"] = head
    step1["provenance"]["evidence_sha256"] = evidence_fingerprint(step1)

    context = build_context(step1, repo)
    entry = context["changed_files"][0]
    assert entry["full_blob_lines"] == 2000
    assert entry["omitted_ranges"], "omitted source must be explicit"
    with pytest.raises(PromptBudgetError, match="max_prompt_bytes"):
        _prompt(context, repo, max_prompt_bytes=1_000)


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


def test_default_agent_factory_configures_bedrock_max_tokens() -> None:
    from greenfield.strands_agent import _default_agent_factory

    captured: dict[str, object] = {}

    class FakeBedrockModel:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    class FakeAgent:
        def __init__(self, **kwargs: object) -> None:
            captured["agent_options"] = kwargs

    with patch("strands.models.bedrock.BedrockModel", FakeBedrockModel), patch(
        "strands.Agent", FakeAgent
    ):
        _default_agent_factory("us.amazon.nova-pro-v1:0", max_tokens=5000)

    assert captured["model_id"] == "us.amazon.nova-pro-v1:0"
    assert captured["max_tokens"] == 5000
    assert captured["agent_options"]["structured_output_model"].__name__ == (
        "_Step15StructuredOutput"
    )


def test_default_agent_factory_without_max_tokens_passes_bare_model_string() -> None:
    from greenfield.strands_agent import _default_agent_factory

    captured: dict[str, object] = {}

    class FakeAgent:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    with patch("strands.Agent", FakeAgent):
        _default_agent_factory("us.amazon.nova-pro-v1:0")

    assert captured["model"] == "us.amazon.nova-pro-v1:0"
    assert "max_tokens" not in captured
    assert captured["structured_output_model"].__name__ == "_Step15StructuredOutput"


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


def test_strands_config_defaults_to_large_output_budget(tmp_path: Path) -> None:
    config_path = tmp_path / "strands.yaml"
    config_path.write_text("region: us-east-1\n", encoding="utf-8")
    config = load_strands_config(config_path)
    assert config.max_tokens == 32000
    assert config.max_continuations == 2
    assert config.max_prompt_bytes == 96_000


def test_strands_config_accepts_dedicated_planner_model(tmp_path: Path) -> None:
    config_path = tmp_path / "strands.yaml"
    config_path.write_text(
        "region: us-east-1\nplanner_model: us.anthropic.claude-sonnet-5\n",
        encoding="utf-8",
    )
    assert load_strands_config(config_path).planner_model == "us.anthropic.claude-sonnet-5"


def test_strands_config_accepts_configured_max_continuations(tmp_path: Path) -> None:
    config_path = tmp_path / "strands.yaml"
    config_path.write_text(
        "region: us-east-1\nmax_continuations: 5\n", encoding="utf-8"
    )
    assert load_strands_config(config_path).max_continuations == 5


def test_strands_config_rejects_non_positive_max_continuations(tmp_path: Path) -> None:
    config_path = tmp_path / "strands.yaml"
    config_path.write_text(
        "region: us-east-1\nmax_continuations: 0\n", encoding="utf-8"
    )
    with pytest.raises(StrandsConfigError, match="max_continuations"):
        load_strands_config(config_path)


def test_strands_config_accepts_configured_max_tokens(tmp_path: Path) -> None:
    config_path = tmp_path / "strands.yaml"
    config_path.write_text("region: us-east-1\nmax_tokens: 8000\n", encoding="utf-8")
    assert load_strands_config(config_path).max_tokens == 8000


def test_strands_config_rejects_non_positive_max_tokens(tmp_path: Path) -> None:
    config_path = tmp_path / "strands.yaml"
    config_path.write_text("region: us-east-1\nmax_tokens: 0\n", encoding="utf-8")
    with pytest.raises(StrandsConfigError, match="max_tokens must be a positive integer"):
        load_strands_config(config_path)


def test_strands_config_accepts_and_validates_prompt_budget(tmp_path: Path) -> None:
    config_path = tmp_path / "strands.yaml"
    config_path.write_text("max_prompt_bytes: 8000\n", encoding="utf-8")
    assert load_strands_config(config_path).max_prompt_bytes == 8000
    config_path.write_text("max_prompt_bytes: 0\n", encoding="utf-8")
    with pytest.raises(StrandsConfigError, match="max_prompt_bytes"):
        load_strands_config(config_path)


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
            "provenance": {"read_only": True, "analysis_report_sha256": "d" * 64},
    }
    assert validate_test_proposal(proposal) == []
    proposal["proposals"][0]["paths"] = ["tests/*.feature"]
    assert validate_test_proposal(proposal)


def test_step2_does_not_refetch_supplied_inventory() -> None:
    supplied = [{"repository": "intacct/tests"}]
    assert trace_greenfield_step2._supplied_inventory_repositories(supplied) == {
        "intacct/tests"
    }
