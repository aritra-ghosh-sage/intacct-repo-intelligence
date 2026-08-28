from __future__ import annotations

from pathlib import Path

import pytest

from greenfield.nexau_planner import NexAUPlannerError, run_nexau_planner
from greenfield.planning_contract import validate_planning_report
from greenfield.strands_tools import GreenfieldToolbox
from tests.test_greenfield_simplified_flow import _context


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
        mode="shadow",
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
            mode="shadow",
            planner_factory=planner_factory,
        )


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
        mode="active",
        planner_factory=planner_factory,
        strands_factory=strands_factory,
    )

    task_types = [cycle["task"]["task_type"] for cycle in report["cycles"]]
    assert task_types == ["screen_repository", "challenge_claim", "synthesize_review"]
    assert report["analysis"]["agent"]["name"] == "nexau"
    assert validate_planning_report(report) == []
