"""Optional NexAU planning adapter for bounded Greenfield investigation."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from time import monotonic
from typing import Any

import yaml

from greenfield.artifact_io import artifact_sha256
from greenfield.llm_env import (
    GreenfieldEnvError,
    load_greenfield_env,
    validate_greenfield_llm_env,
)
from greenfield.planning_contract import TASK_TYPES, build_planning_report
from greenfield.strands_agent import StrandsAgentError, run_strands_analysis
from greenfield.strands_tools import GreenfieldToolbox
from greenfield.telemetry import redact


class NexAUPlannerError(ValueError):
    """Raised when the optional planner cannot produce a safe lifecycle."""


def _redacted_error(value: Any) -> str:
    return str(redact(str(value)))[:500]


def load_planner_config(path: str | Path | None) -> dict[str, Any]:
    """Load non-secret planner configuration from an operator-supplied YAML file."""

    if path is None:
        return {}
    value = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise NexAUPlannerError("planner config must be an object")
    forbidden = {"api_key", "secret", "token", "password"}

    def contains_secret(value: Any) -> bool:
        if isinstance(value, Mapping):
            return any(
                str(key).lower() in forbidden or contains_secret(child)
                for key, child in value.items()
            )
        if isinstance(value, list):
            return any(contains_secret(child) for child in value)
        return False

    if contains_secret(value):
        raise NexAUPlannerError("planner config must not contain secret fields")
    return dict(value)


def _prompt(run_context: Mapping[str, Any], summary: Mapping[str, Any]) -> str:
    return f"""You are the Greenfield NexAU planner. Create a read-only investigation plan.
Return JSON only with a `tasks` list. Each task requires `task_id`, `task_type`,
`question`, and optional `repository`. Use only captured repositories. Inspect
explicit-contract candidates before discovery-screen candidates. Plan a final
challenge_claim task for any strong claim or draft-eligible action, and finish
with synthesize_review. Do not claim impact, coverage, or execute writes.

Run context:
```json
{json.dumps(run_context, sort_keys=True)}
```
Evidence summary:
```json
{json.dumps(summary, sort_keys=True)}
```
    """


def _replan_prompt(
    run_context: Mapping[str, Any],
    summary: Mapping[str, Any],
    cycles: list[Mapping[str, Any]],
    findings: Mapping[str, Any],
) -> str:
    return f"""You are replanning the Greenfield NexAU read-only investigation.
Review the completed task results and choose only the next bounded tasks needed to
resolve material evidence gaps. Return JSON only with a `tasks` list. Do not repeat
completed task IDs. Use only captured repositories and finish with a challenge_claim
task for any proposed confirmed or strong_candidate impact/action. Do not claim
impact or coverage yourself.

Run context:
```json
{json.dumps(run_context, sort_keys=True)}
```
Original evidence summary:
```json
{json.dumps(summary, sort_keys=True)}
```
Completed lifecycle:
```json
{json.dumps(cycles, sort_keys=True)}
```
Findings accumulated from bounded Strands tasks:
```json
{json.dumps(findings, sort_keys=True)}
```
"""


def _default_planner_factory(
    config: Mapping[str, Any], toolbox: GreenfieldToolbox
) -> Callable[[str], Any]:
    """Create a programmatic NexAU agent when the optional runtime is installed."""

    env_path = load_greenfield_env()
    model = str(config.get("model") or os.environ.get("LLM_MODEL") or "").strip()
    base_url = str(
        config.get("base_url") or os.environ.get("LLM_BASE_URL") or ""
    ).strip()
    try:
        validate_greenfield_llm_env(
            model=model or None,
            base_url=base_url or None,
            env_path=env_path,
        )
    except GreenfieldEnvError as exc:
        raise NexAUPlannerError(str(exc)) from exc
    api_key = os.environ.get("LLM_API_KEY")
    try:
        from nexau import Agent, AgentConfig, LLMConfig, Tool
    except ImportError as exc:  # pragma: no cover - depends on optional runtime
        raise NexAUPlannerError(
            "NexAU is not installed; install the pinned project dependency before enabling planner mode"
        ) from exc
    llm = LLMConfig(
        model=model,
        base_url=base_url,
        api_key=api_key,
        api_type=config.get("api_type", "openai_chat_completion"),
    )

    def greenfield_evidence(
        operation: str,
        repository: str | None = None,
        path: str | None = None,
        query: str | None = None,
        section: str | None = None,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> dict[str, Any]:
        """Expose only the shared revision-bound Greenfield evidence ledger."""

        if operation == "list_candidate_repositories":
            return toolbox.list_candidate_repositories()
        if operation == "repository_metadata" and repository:
            return toolbox.repository_metadata(repository)
        if operation == "read_handbook" and repository:
            return toolbox.read_handbook(repository, section or "index")
        if operation == "read_source" and repository and path:
            return toolbox.read_source(
                repository, path, start_line or 1, end_line or 200
            )
        if operation == "search_source" and repository and query:
            return toolbox.search_source(repository, query, path or "")
        if operation == "read_evidence_artifact" and path:
            return toolbox.read_evidence_artifact(path)
        if operation == "codegraph_explore" and repository and query:
            return toolbox.codegraph_explore(repository, query)
        raise NexAUPlannerError(
            "planner evidence tool requires valid captured-scope arguments"
        )

    tool_path = (
        Path(__file__).with_name("nexau_tools") / "greenfield_evidence.tool.yaml"
    )
    evidence_tool = Tool.from_yaml(tool_path, binding=greenfield_evidence)
    agent = Agent(
        config=AgentConfig(
            name="greenfield_nexau_planner",
            system_prompt="You plan bounded, read-only Greenfield investigations.",
            llm_config=llm,
            tools=[evidence_tool],
        )
    )
    return agent.run


def _response_text(raw: Any) -> str:
    if isinstance(raw, str):
        return raw
    for attribute in ("content", "message", "text"):
        value = getattr(raw, attribute, None)
        if isinstance(value, str):
            return value
    if isinstance(raw, Mapping):
        for key in ("content", "message", "text", "output"):
            value = raw.get(key)
            if isinstance(value, str):
                return value
    return str(raw)


def _run_planner_call(runner: Callable[[str], Any], prompt: str) -> Any:
    try:
        return runner(prompt)
    except NexAUPlannerError:
        raise
    except Exception as exc:  # pragma: no cover - provider-specific failure shape
        raise NexAUPlannerError(
            f"NexAU planner execution failed: {_redacted_error(exc)}"
        ) from exc


def _parse_tasks(
    raw: Any, run_context: Mapping[str, Any], *, ensure_synthesis: bool = False
) -> list[dict[str, Any]]:
    text = _response_text(raw).strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise NexAUPlannerError("NexAU planner did not return JSON") from exc
    rows = value.get("tasks") if isinstance(value, Mapping) else None
    if not isinstance(rows, list):
        raise NexAUPlannerError("NexAU planner response requires tasks")
    allowed = {str(run_context["source"]["repository"])} | {
        str(row["repository"]) for row in run_context["candidate_repositories"]
    }
    tasks: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping) or row.get("task_type") not in TASK_TYPES:
            raise NexAUPlannerError(f"planner task {index} has an invalid task_type")
        repository = row.get("repository")
        if repository is not None and str(repository) not in allowed:
            raise NexAUPlannerError(f"planner task {index} is outside captured scope")
        question = str(row.get("question") or "").strip()
        if not question:
            raise NexAUPlannerError(f"planner task {index} requires a question")
        task_id = str(row.get("task_id") or f"planner-{index + 1:02d}")
        if task_id in seen_ids:
            raise NexAUPlannerError(f"planner task IDs must be unique: {task_id}")
        seen_ids.add(task_id)
        tasks.append(
            {
                "task_id": task_id,
                "task_type": str(row["task_type"]),
                "question": question,
                **({"repository": str(repository)} if repository is not None else {}),
            }
        )
    if ensure_synthesis and not any(
        row["task_type"] == "synthesize_review" for row in tasks
    ):
        tasks.append(
            {
                "task_id": "planner-synthesis",
                "task_type": "synthesize_review",
                "question": "Summarize only evidence-backed review results.",
            }
        )
    return tasks


def _dispatch_task(
    task: Mapping[str, Any],
    run_context: Mapping[str, Any],
    summary: Mapping[str, Any],
    toolbox: GreenfieldToolbox,
    *,
    model: str | None,
    timeout: int,
    strands_factory: Callable[[str | None], Callable[[str], Any]] | None,
) -> dict[str, Any]:
    """Give Strands a single scoped question; all evidence stays in the shared ledger."""

    if task["task_type"] == "synthesize_review":
        return {"evidence_refs": [], "findings": {}, "status": "complete"}
    scoped = {**summary, "planner_task": dict(task)}
    previous_ids = {str(row["tool_call_id"]) for row in toolbox.ledger()}
    try:
        findings, _ = run_strands_analysis(
            run_context,
            scoped,
            toolbox,
            model=model,
            timeout=timeout,
            agent_factory=strands_factory,
        )
    except (StrandsAgentError, RuntimeError, ValueError, OSError) as exc:
        return {
            "evidence_refs": [
                {
                    "tool_call_id": row["tool_call_id"],
                    "result_sha256": row["result_sha256"],
                }
                for row in toolbox.ledger()
                if str(row["tool_call_id"]) not in previous_ids
            ],
            "findings": {},
            "status": "unavailable",
            "error": _redacted_error(exc),
        }
    return {
        "evidence_refs": [
            {"tool_call_id": row["tool_call_id"], "result_sha256": row["result_sha256"]}
            for row in toolbox.ledger()
            if str(row["tool_call_id"]) not in previous_ids
        ],
        "findings": findings,
        "status": "complete",
    }


def _append_unique(rows: list[dict[str, Any]], additions: Any) -> None:
    if not isinstance(additions, list):
        return
    seen = {artifact_sha256(row) for row in rows}
    for row in additions:
        if not isinstance(row, Mapping):
            continue
        value = dict(row)
        digest = artifact_sha256(value)
        if digest not in seen:
            rows.append(value)
            seen.add(digest)


def _merge_findings(target: dict[str, Any], findings: Mapping[str, Any]) -> None:
    _append_unique(target["repository_impacts"], findings.get("repository_impacts"))
    _append_unique(target["actions"], findings.get("actions"))
    gaps = findings.get("gaps")
    if isinstance(gaps, list):
        target["gaps"].update(str(value) for value in gaps)
    coverage = findings.get("coverage")
    if isinstance(coverage, Mapping) and coverage:
        target["coverage"] = dict(coverage)
    recommendation = findings.get("recommendation")
    if isinstance(recommendation, str) and recommendation.strip():
        target["recommendation"] = recommendation


def _required_challenge_tasks(findings: Mapping[str, Any]) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    impacts = findings.get("repository_impacts", [])
    for row in impacts if isinstance(impacts, list) else []:
        if not isinstance(row, Mapping) or row.get("evidence_state") not in {
            "confirmed",
            "strong_candidate",
        }:
            continue
        repository = row.get("repository")
        if not repository:
            continue
        tasks.append(
            {
                "task_id": f"challenge-impact-{repository}",
                "task_type": "challenge_claim",
                "repository": str(repository),
                "question": f"Challenge the impact claim for {repository} against its cited evidence.",
            }
        )
    actions = findings.get("actions", [])
    for row in actions if isinstance(actions, list) else []:
        if not isinstance(row, Mapping) or row.get("draft_eligible") is not True:
            continue
        action_id = row.get("action_id")
        repository = row.get("target_repository")
        if not action_id or not repository:
            continue
        tasks.append(
            {
                "task_id": f"challenge-action-{action_id}",
                "task_type": "challenge_claim",
                "repository": str(repository),
                "question": f"Challenge draft eligibility for action {action_id} against its cited evidence.",
            }
        )
    return tasks


def _downgrade_incomplete_findings(findings: dict[str, Any], *, reason: str) -> None:
    """Keep incomplete planner lifecycles from retaining automatic claims."""
    for row in findings.get("repository_impacts", []):
        if isinstance(row, Mapping) and row.get("evidence_state") in {
            "confirmed",
            "strong_candidate",
        }:
            row["evidence_state"] = "candidate"
            row.pop("challenge_task_id", None)
    for row in findings.get("actions", []):
        if not isinstance(row, Mapping):
            continue
        row["draft_eligible"] = False
        if row.get("evidence_state") in {"confirmed", "strong_candidate"}:
            row["evidence_state"] = "candidate"
        row.pop("challenge_task_id", None)
    findings["gaps"].add(reason)


def run_nexau_planner(
    run_context: Mapping[str, Any],
    summary: Mapping[str, Any],
    toolbox: GreenfieldToolbox,
    *,
    mode: str,
    config: Mapping[str, Any] | None = None,
    planner_factory: Callable[[Mapping[str, Any]], Callable[[str], Any]] | None = None,
    strands_factory: Callable[[str | None], Callable[[str], Any]] | None = None,
    model: str | None = None,
    timeout: int = 300,
) -> dict[str, Any]:
    """Run NexAU as the default bounded orchestrator for Analyze."""

    if mode not in {"default", "shadow", "active"}:
        raise NexAUPlannerError("planner mode must be default")
    settings = dict(config or {})
    started = monotonic()
    runner = (
        planner_factory(settings)
        if planner_factory is not None
        else _default_planner_factory(settings, toolbox)
    )
    tasks = _parse_tasks(
        _run_planner_call(runner, _prompt(run_context, summary)),
        run_context,
        ensure_synthesis=True,
    )
    cycles: list[dict[str, Any]] = []
    findings: dict[str, Any] = {
        "repository_impacts": [],
        "actions": [],
        "coverage": {},
        "recommendation": "Review only evidence-backed planner findings.",
        "gaps": set(),
    }
    completed_ids: set[str] = set()
    all_task_ids: set[str] = set()
    try:
        max_cycles = int(settings.get("max_cycles", 32))
    except (TypeError, ValueError) as exc:
        raise NexAUPlannerError("planner max_cycles must be an integer") from exc
    if max_cycles < 1:
        raise NexAUPlannerError("planner max_cycles must be positive")
    queue = list(tasks)
    terminal = False
    stop_reason = "planner_completed_captured_scope"
    while queue and len(cycles) < max_cycles:
        task = queue.pop(0)
        task_id = str(task["task_id"])
        if task_id in completed_ids:
            continue
        if task_id in all_task_ids:
            raise NexAUPlannerError(
                f"planner task IDs must be globally unique: {task_id}"
            )
        all_task_ids.add(task_id)
        completed_ids.add(task_id)
        result = _dispatch_task(
            task,
            run_context,
            summary,
            toolbox,
            model=model,
            timeout=timeout,
            strands_factory=strands_factory,
        )
        task_findings = result.get("findings", {})
        if isinstance(task_findings, Mapping):
            _merge_findings(findings, task_findings)
        if result.get("status") == "unavailable":
            findings["gaps"].add(f"planner_task_unavailable:{task_id}")
        if task["task_type"] == "challenge_claim" and not result.get("evidence_refs"):
            result["status"] = "failed"
            result["error"] = "challenge task produced no toolbox evidence"
            findings["gaps"].add(f"planner_challenge_evidence_missing:{task_id}")
        if task["task_type"] == "synthesize_review":
            terminal = True
            decision = "complete"
        else:
            new_tasks = _parse_tasks(
                _run_planner_call(
                    runner,
                    _replan_prompt(
                        run_context,
                        summary,
                        [*cycles, {"task": task, **result}],
                        {
                            **findings,
                            "gaps": sorted(findings["gaps"]),
                        },
                    ),
                ),
                run_context,
            )
            appended = [
                candidate
                for candidate in new_tasks
                if str(candidate["task_id"]) not in completed_ids
                and not any(
                    str(existing["task_id"]) == str(candidate["task_id"])
                    for existing in queue
                )
            ]
            appended.extend(
                candidate
                for candidate in _required_challenge_tasks(findings)
                if str(candidate["task_id"]) not in completed_ids
                and not any(
                    str(existing["task_id"]) == str(candidate["task_id"])
                    for existing in [*queue, *appended]
                )
            )
            synthesis_tasks = [
                existing
                for existing in queue
                if existing["task_type"] == "synthesize_review"
            ]
            queue = [
                *[
                    existing
                    for existing in queue
                    if existing["task_type"] != "synthesize_review"
                ],
                *appended,
                *synthesis_tasks,
            ]
            decision = "replan" if appended else "continue"
        cycles.append(
            {
                "task": task,
                "evidence_refs": result.get("evidence_refs", []),
                "decision": decision,
                **(
                    {"status": result["status"], "error": result["error"]}
                    if result.get("status") in {"unavailable", "failed", "partial"}
                    else {}
                ),
            }
        )
        if terminal:
            break
    if not terminal:
        stop_reason = (
            "planner_cycle_budget_exhausted"
            if queue
            else "planner_stopped_without_synthesis"
        )
        status = "blocked" if queue else "partial"
    else:
        status = "partial" if findings["gaps"] else "complete"
    if status != "complete":
        _downgrade_incomplete_findings(findings, reason="nexau_planner_incomplete")
    findings["gaps"] = sorted(findings["gaps"])
    for row in findings["repository_impacts"]:
        if isinstance(row, Mapping) and row.get("evidence_state") in {
            "confirmed",
            "strong_candidate",
        }:
            row["challenge_task_id"] = f"challenge-impact-{row.get('repository')}"
    for row in findings["actions"]:
        if isinstance(row, Mapping) and row.get("draft_eligible") is True:
            row["challenge_task_id"] = f"challenge-action-{row.get('action_id')}"
    findings["agent"] = {
        "status": "complete" if status == "complete" else "partial",
        "name": "nexau",
        "mode": mode,
    }
    report = build_planning_report(
        run_context,
        mode=mode,
        planner={
            "name": "nexau",
            "model": settings.get("model") or "configured",
            "elapsed_ms": int((monotonic() - started) * 1000),
        },
        cycles=cycles,
        status=status,
        stop_reason=stop_reason,
        gaps=findings["gaps"],
        analysis=findings,
    )
    return report


__all__ = ["NexAUPlannerError", "load_planner_config", "run_nexau_planner"]
