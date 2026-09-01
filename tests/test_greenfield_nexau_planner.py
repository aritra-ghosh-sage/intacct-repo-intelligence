from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import pytest

from greenfield.nexau_planner import (
    _PLANNER_PROMPT_MAX_BYTES,
    NexAUPlannerError,
    _cycle_brief,
    _default_planner_factory,
    _prompt,
    _replan_prompt,
    _response_text,
    run_nexau_planner,
)
from greenfield.planning_contract import build_planning_report, validate_planning_report
from greenfield.strands_tools import GreenfieldToolbox
from tests.test_greenfield_simplified_flow import _context


@pytest.fixture(autouse=True)
def _greenfield_llm_env(monkeypatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_BASE_URL", "https://test.example/v1")


def _install_fake_strands(monkeypatch, captured: dict[str, object]) -> None:
    strands = ModuleType("strands")
    models = ModuleType("strands.models")
    bedrock = ModuleType("strands.models.bedrock")

    class FakeBedrockModel:
        def __init__(self, **kwargs: object) -> None:
            captured["bedrock_model"] = kwargs

    class FakeAgent:
        def __init__(self, **kwargs: object) -> None:
            captured["agent"] = kwargs

        def __call__(self, prompt: str) -> str:
            captured["prompt"] = prompt
            return '{"tasks": []}'

    strands.Agent = FakeAgent
    bedrock.BedrockModel = FakeBedrockModel
    monkeypatch.setitem(sys.modules, "strands", strands)
    monkeypatch.setitem(sys.modules, "strands.models", models)
    monkeypatch.setitem(sys.modules, "strands.models.bedrock", bedrock)


def test_default_planner_factory_uses_native_strands_bedrock(
    tmp_path: Path, monkeypatch
) -> None:
    context, _ = _context(tmp_path)
    captured: dict[str, object] = {}
    _install_fake_strands(monkeypatch, captured)

    runner = _default_planner_factory(
        {"model": "us.anthropic.claude-sonnet-5"}, GreenfieldToolbox(context)
    )

    assert runner("create the plan") == '{"tasks": []}'
    assert captured["prompt"] == "create the plan"
    assert captured["bedrock_model"] == {
        "model_id": "us.anthropic.claude-sonnet-5",
        "max_tokens": 8192,
    }
    assert captured["agent"] == {
        "model": captured["agent"]["model"],
        "system_prompt": captured["agent"]["system_prompt"],
        "callback_handler": None,
    }


def test_default_planner_factory_rejects_missing_model(
    tmp_path: Path, monkeypatch
) -> None:
    context, _ = _context(tmp_path)
    monkeypatch.delenv("STRANDS_PLANNER_MODEL", raising=False)
    with pytest.raises(NexAUPlannerError, match="planner model is not configured"):
        _default_planner_factory({}, GreenfieldToolbox(context))


def test_default_planner_factory_uses_environment_model(
    tmp_path: Path, monkeypatch
) -> None:
    context, _ = _context(tmp_path)
    captured: dict[str, object] = {}
    _install_fake_strands(monkeypatch, captured)
    monkeypatch.setenv("STRANDS_PLANNER_MODEL", "us.anthropic.claude-sonnet-5")

    runner = _default_planner_factory({}, GreenfieldToolbox(context))

    runner("create the plan")
    assert captured["bedrock_model"]["model_id"] == "us.anthropic.claude-sonnet-5"


def test_response_text_reads_strands_agent_message() -> None:
    class Result:
        def __init__(self) -> None:
            self.message = {
                "role": "assistant",
                "content": [{"text": '{"tasks": []}'}],
            }

    assert _response_text(Result()) == '{"tasks": []}'


def test_strands_prompts_use_bounded_handbook_oriented_briefs(tmp_path: Path) -> None:
    context, _ = _context(tmp_path)
    oversized = "unbounded-stage-detail-" + ("x" * 200_000)
    summary = {
        "step2_candidates": [
            {"repository": "intacct/explicit-tests", "detail": oversized}
            for _ in range(50)
        ],
        "step3_repositories": {f"repository-{index}": oversized for index in range(50)},
        "step4_coverage": {f"surface-{index}": oversized for index in range(50)},
        "step4_obligations": {f"obligation-{index}": oversized for index in range(50)},
        "step5_actions": [
            {
                "action_id": f"action-{index}",
                "target_repository": "intacct/explicit-tests",
                "detail": oversized,
            }
            for index in range(50)
        ],
        "gaps": [oversized for _ in range(50)],
    }
    cycles = [
        {
            "task": {
                "task_id": f"task-{index}",
                "task_type": "screen_repository",
                "repository": "intacct/explicit-tests",
            },
            "decision": "replan",
            "error": oversized,
            "evidence_refs": [{"tool_call_id": f"call-{index}"}],
        }
        for index in range(50)
    ]
    findings = {
        "repository_impacts": [
            {
                "repository": "intacct/explicit-tests",
                "evidence_state": "candidate",
                "rationale": oversized,
            }
        ],
        "actions": [],
        "coverage": {"detail": oversized},
        "gaps": [oversized],
        "recommendation": oversized,
    }

    initial = _prompt(context, summary)
    replanned = _replan_prompt(context, summary, cycles, findings)

    assert len(initial.encode("utf-8")) <= _PLANNER_PROMPT_MAX_BYTES
    assert len(replanned.encode("utf-8")) <= _PLANNER_PROMPT_MAX_BYTES
    assert oversized not in initial
    assert oversized not in replanned
    assert context["source"]["head_revision"] in initial
    assert "Use progressive disclosure" in initial
    assert "read-only evidence tool" not in initial
    assert "read-only evidence tool" not in replanned
    assert "CodeGraph" not in initial
    assert "action-0" in initial
    assert "task-49" in replanned
    cycle_brief = _cycle_brief(cycles)
    assert len(cycle_brief["items"]) == 8
    assert cycle_brief["completed_task_ids_omitted_count"] == 42


def test_nexau_planner_retains_bounded_lifecycle(tmp_path: Path) -> None:
    context, _ = _context(tmp_path)

    def planner_factory(_config: dict):
        return lambda _prompt: (
            '{"tasks": [{"task_id": "screen", "task_type": "screen_repository", '
            '"repository": "intacct/explicit-tests", "question": "Screen the explicit test repository."}]}'
        )

    def strands_factory(_model: str | None, *, tools: list[object]):
        def agent(_prompt: str) -> str:
            tools[0]()
            return '{"repository_impacts": [], "actions": [], "coverage": {}, "recommendation": "none", "gaps": [], "agent": {"status": "complete"}}'

        return agent

    report = run_nexau_planner(
        context,
        {"gaps": []},
        GreenfieldToolbox(context),
        mode="default",
        planner_factory=planner_factory,
        strands_factory=strands_factory,
    )
    assert validate_planning_report(report) == []
    assert report["cycles"][0]["task"]["task_type"] == "screen_repository"
    assert report["cycles"][0]["evidence_refs"]
    assert report["cycles"][-1]["task"]["task_type"] == "synthesize_review"


def test_nexau_planner_rejects_out_of_scope_task(tmp_path: Path) -> None:
    context, _ = _context(tmp_path)

    def planner_factory(_config: dict):
        return lambda _prompt: (
            '{"tasks": [{"task_type": "screen_repository", "repository": "intacct/outside", "question": "bad"}]}'
        )

    with pytest.raises(NexAUPlannerError, match="outside captured scope"):
        run_nexau_planner(
            context,
            {},
            GreenfieldToolbox(context),
            mode="default",
            planner_factory=planner_factory,
        )


def test_load_planner_config_rejects_secret_like_keys(tmp_path: Path) -> None:
    config_path = tmp_path / "planner.yaml"
    config_path.write_text(
        "apiKey: demo\nclient_secret: demo\nallowed: true\n",
        encoding="utf-8",
    )

    with pytest.raises(NexAUPlannerError, match="secret fields"):
        from greenfield.nexau_planner import load_planner_config

        load_planner_config(config_path)


def test_nexau_planner_replans_before_synthesis(tmp_path: Path) -> None:
    context, _ = _context(tmp_path)
    planner_responses = iter(
        [
            (
                '{"tasks": [{"task_id": "screen", "task_type": "screen_repository", '
                '"repository": "intacct/explicit-tests", "question": "Screen it."}]}'
            ),
            (
                '{"tasks": [{"task_id": "challenge", "task_type": "challenge_claim", '
                '"repository": "intacct/explicit-tests", "question": "Challenge it."}]}'
            ),
            '{"tasks": []}',
        ]
    )

    def planner_factory(_config: dict):
        return lambda _prompt: next(planner_responses)

    def strands_factory(_model: str | None, *, tools: list[object]):
        def agent(_prompt: str) -> str:
            tools[0]()
            return '{"repository_impacts": [], "actions": [], "coverage": {}, "gaps": [], "agent": {"status": "complete"}}'

        return agent

    report = run_nexau_planner(
        context,
        {"gaps": []},
        GreenfieldToolbox(context),
        mode="default",
        planner_factory=planner_factory,
        strands_factory=strands_factory,
    )

    task_types = [cycle["task"]["task_type"] for cycle in report["cycles"]]
    assert task_types == ["screen_repository", "challenge_claim", "synthesize_review"]
    assert report["analysis"]["agent"]["name"] == "strands-bedrock"
    assert validate_planning_report(report) == []


@pytest.mark.parametrize("mode", ["active", "shadow", "off"])
def test_nexau_planner_rejects_legacy_modes(tmp_path: Path, mode: str) -> None:
    context, _ = _context(tmp_path)

    with pytest.raises(NexAUPlannerError, match="planner mode must be default"):
        run_nexau_planner(
            context,
            {},
            GreenfieldToolbox(context),
            mode=mode,
        )


@pytest.mark.parametrize("mode", ["active", "shadow"])
def test_planning_report_rejects_legacy_modes(tmp_path: Path, mode: str) -> None:
    context, _ = _context(tmp_path)
    report = build_planning_report(
        context,
        mode="default",
        planner={"name": "nexau", "status": "unavailable"},
        cycles=[],
        status="unavailable",
        stop_reason="planner_runtime_unavailable",
    )
    report["mode"] = mode

    assert "mode is invalid" in validate_planning_report(report)


def test_incomplete_planner_downgrades_automatic_claims(tmp_path: Path) -> None:
    context, _ = _context(tmp_path)

    def planner_factory(_config: dict):
        return lambda _prompt: (
            '{"tasks": [{"task_id": "screen", "task_type": "screen_repository", '
            '"repository": "intacct/explicit-tests", "question": "Screen it."}]}'
        )

    def strands_factory(_model: str | None, *, tools: list[object]):
        def agent(_prompt: str) -> str:
            tools[0]()
            return (
                '{"repository_impacts": [{"repository": "intacct/explicit-tests", '
                '"evidence_state": "strong_candidate", "rank": 1, '
                '"rationale": "claim", "evidence": []}], "actions": [], '
                '"coverage": {}, "gaps": [], "agent": {"status": "complete"}}'
            )

        return agent

    report = run_nexau_planner(
        context,
        {"gaps": []},
        GreenfieldToolbox(context),
        mode="default",
        config={"max_cycles": 1},
        planner_factory=planner_factory,
        strands_factory=strands_factory,
    )
    assert report["status"] == "blocked"
    assert report["analysis"]["repository_impacts"][0]["evidence_state"] == "candidate"
    assert validate_planning_report(report) == []
