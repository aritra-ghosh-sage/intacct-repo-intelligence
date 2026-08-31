from __future__ import annotations

from pathlib import Path

import pytest

from greenfield.nexau_planner import NexAUPlannerError, run_nexau_planner
from greenfield.planning_contract import build_planning_report, validate_planning_report
from greenfield.strands_tools import GreenfieldToolbox
from tests.test_greenfield_simplified_flow import _context


@pytest.fixture(autouse=True)
def _greenfield_llm_env(monkeypatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_BASE_URL", "https://test.example/v1")


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
    assert report["analysis"]["agent"]["name"] == "nexau"
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
