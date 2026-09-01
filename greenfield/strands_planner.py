"""Native Strands/Bedrock planner for bounded Greenfield investigation."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from time import monotonic
from typing import Any

from pydantic import BaseModel

from greenfield.artifact_io import artifact_sha256
from greenfield.planning_contract import (
    MAX_QUESTION_LENGTH,
    MAX_TASK_ID_LENGTH,
    MAX_TASKS,
    REPOSITORY_REQUIRED_TASK_TYPES,
    TASK_TYPES,
    build_planning_report,
)
from greenfield.strands_agent import StrandsAgentError, run_strands_analysis
from greenfield.strands_tools import GreenfieldToolbox
from greenfield.telemetry import redact

_PLANNER_PROMPT_MAX_BYTES = 48_000
_PLANNER_MAX_ITEMS = 16
_PLANNER_MAX_TEXT_LENGTH = 240
_PLANNER_HANDOFF_MAX_CYCLES = 8


class StrandsPlannerError(ValueError):
    """Raised when the Strands planner cannot produce a safe lifecycle."""


class PlannerTaskResponse(BaseModel):
    """Typed response boundary for initial and replanning turns."""

    tasks: list[dict[str, Any]]


def _redacted_error(value: Any) -> str:
    return str(redact(str(value)))[:500]


def _bounded_text(value: Any) -> str:
    text = str(value)
    if len(text) <= _PLANNER_MAX_TEXT_LENGTH:
        return text
    return text[:_PLANNER_MAX_TEXT_LENGTH] + "..."


def _bounded_strings(values: Any) -> dict[str, Any]:
    rows = values if isinstance(values, list) else []
    return {
        "items": [_bounded_text(value) for value in rows[:_PLANNER_MAX_ITEMS]],
        "count": len(rows),
        "omitted_count": max(0, len(rows) - _PLANNER_MAX_ITEMS),
        "sha256": artifact_sha256(rows),
    }


def _record_brief(rows: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    values = rows if isinstance(rows, list) else []
    items = []
    for row in values[:_PLANNER_MAX_ITEMS]:
        if not isinstance(row, Mapping):
            continue
        item = {
            field: _bounded_text(row[field])
            if isinstance(row[field], str)
            else row[field]
            for field in fields
            if row.get(field) is not None
            and isinstance(row.get(field), (str, int, float, bool))
        }
        items.append(item)
    return {
        "items": items,
        "count": len(values),
        "omitted_count": max(0, len(values) - _PLANNER_MAX_ITEMS),
        "sha256": artifact_sha256(values),
    }


def _mapping_brief(value: Any) -> dict[str, Any]:
    mapping = value if isinstance(value, Mapping) else {}
    keys = sorted(_bounded_text(key) for key in mapping)
    return {
        "key_count": len(keys),
        "keys": keys[:_PLANNER_MAX_ITEMS],
        "omitted_key_count": max(0, len(keys) - _PLANNER_MAX_ITEMS),
        "sha256": artifact_sha256(mapping),
    }


def _planner_brief(
    run_context: Mapping[str, Any], summary: Mapping[str, Any]
) -> dict[str, Any]:
    """Project immutable evidence into bounded planner-routing context."""

    source = run_context.get("source", {})
    source_data = source if isinstance(source, Mapping) else {}
    candidates = run_context.get("candidate_repositories", [])
    handbooks = run_context.get("repository_handbooks", [])
    artifacts = run_context.get("evidence_artifacts", [])
    provenance = run_context.get("provenance", {})
    provenance_data = provenance if isinstance(provenance, Mapping) else {}
    return {
        "source": {
            field: source_data.get(field)
            for field in (
                "repository",
                "repo_key",
                "pr_number",
                "base_revision",
                "head_revision",
            )
            if source_data.get(field) is not None
        }
        | {"changed_paths": _bounded_strings(source_data.get("changed_paths"))},
        "context_sha256": run_context.get("context_sha256"),
        "provenance": {
            field: value
            for field, value in provenance_data.items()
            if field.endswith("sha256")
        },
        "candidate_repositories": _record_brief(
            candidates,
            (
                "repository",
                "repo_key",
                "priority",
                "inspected_revision",
                "discovery_eligible",
            ),
        ),
        "repository_handbooks": _record_brief(handbooks, ("repository", "sha256")),
        "evidence_artifacts": _record_brief(artifacts, ("sha256",)),
        "evidence_summary": {
            "step2_candidates": _record_brief(
                summary.get("step2_candidates"),
                ("repository", "priority", "evidence_state", "status"),
            ),
            "step3_repositories": _mapping_brief(summary.get("step3_repositories")),
            "step4_coverage": _mapping_brief(summary.get("step4_coverage")),
            "step4_obligations": _mapping_brief(summary.get("step4_obligations")),
            "step5_actions": _record_brief(
                summary.get("step5_actions"),
                (
                    "action_id",
                    "action_type",
                    "status",
                    "target_repository",
                    "evidence_state",
                    "draft_eligible",
                ),
            ),
            "gaps": _bounded_strings(summary.get("gaps")),
        },
    }


def _cycle_brief(cycles: list[Mapping[str, Any]]) -> dict[str, Any]:
    items = []
    for cycle in cycles[-_PLANNER_HANDOFF_MAX_CYCLES:]:
        task = cycle.get("task", {})
        if not isinstance(task, Mapping):
            continue
        evidence_refs = cycle.get("evidence_refs", [])
        if not isinstance(evidence_refs, list):
            evidence_refs = []
        item = {
            field: _bounded_text(task[field])
            if isinstance(task[field], str)
            else task[field]
            for field in ("task_id", "task_type", "repository")
            if task.get(field) is not None
        }
        item.update(
            {
                field: _bounded_text(cycle[field])
                if isinstance(cycle[field], str)
                else cycle[field]
                for field in ("decision", "status", "error")
                if cycle.get(field) is not None
            }
        )
        item.update(
            {
                "evidence_ref_count": len(evidence_refs),
                "evidence_refs_sha256": artifact_sha256(evidence_refs),
            }
        )
        items.append(item)
    return {
        "items": items,
        "count": len(cycles),
        "completed_task_ids": [
            _bounded_text(cycle["task"]["task_id"])
            for cycle in cycles[-_PLANNER_HANDOFF_MAX_CYCLES:]
            if isinstance(cycle.get("task"), Mapping)
            and cycle["task"].get("task_id") is not None
        ],
        "completed_task_ids_omitted_count": max(
            0, len(cycles) - _PLANNER_HANDOFF_MAX_CYCLES
        ),
        "omitted_count": max(0, len(cycles) - _PLANNER_HANDOFF_MAX_CYCLES),
        "sha256": artifact_sha256(cycles),
    }


def _findings_brief(findings: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "repository_impacts": _record_brief(
            findings.get("repository_impacts"),
            ("repository", "evidence_state", "rank", "challenge_task_id"),
        ),
        "actions": _record_brief(
            findings.get("actions"),
            (
                "action_id",
                "action_type",
                "target_repository",
                "evidence_state",
                "draft_eligible",
                "challenge_task_id",
            ),
        ),
        "coverage": _mapping_brief(findings.get("coverage")),
        "gaps": _bounded_strings(findings.get("gaps")),
        "recommendation": _bounded_text(findings.get("recommendation", "")),
    }


def _render_planner_prompt(instructions: str, sections: Mapping[str, Any]) -> str:
    prompt = (
        instructions
        + "\n\n"
        + "\n\n".join(
            f"{title}:\n```json\n{json.dumps(value, sort_keys=True)}\n```"
            for title, value in sections.items()
        )
    )
    if len(prompt.encode("utf-8")) > _PLANNER_PROMPT_MAX_BYTES:
        raise StrandsPlannerError(
            "Strands planner brief exceeds the bounded prompt budget after compaction"
        )
    return prompt


def _prompt(run_context: Mapping[str, Any], summary: Mapping[str, Any]) -> str:
    return _render_planner_prompt(
        """You are the Greenfield Strands planner. Create a read-only investigation plan.
Return JSON only with a `tasks` list. Each task requires `task_id`, `task_type`,
`question`, and optional `repository`. Use only captured repositories. Inspect
explicit-contract candidates before discovery-screen candidates. Plan a final
challenge_claim task for any strong claim or draft-eligible action, and finish
with synthesize_review. Do not claim impact, coverage, or execute writes.

Use progressive disclosure. The brief is an index, not source authority. For a
repository with a handbook, read its `index`, then only relevant behavior sections,
and verify any candidate locator with the host-dispatched evidence tasks. Do not
request broad evidence or source dumps.""",
        {"Planner brief": _planner_brief(run_context, summary)},
    )


def _replan_prompt(
    run_context: Mapping[str, Any],
    summary: Mapping[str, Any],
    cycles: list[Mapping[str, Any]],
    findings: Mapping[str, Any],
) -> str:
    return _render_planner_prompt(
        """You are replanning the Greenfield Strands read-only investigation.
Review the completed task results and choose only the next bounded tasks needed to
resolve material evidence gaps. Return JSON only with a `tasks` list. Do not repeat
completed task IDs. Use only captured repositories and finish with a challenge_claim
task for any proposed confirmed or strong_candidate impact/action. Do not claim
impact or coverage yourself.

Use the evidence hashes and bounded task records to choose the next task. If more
context is necessary, schedule a bounded host-dispatched evidence task.""",
        {
            "Planner brief": _planner_brief(run_context, summary),
            "Completed lifecycle": _cycle_brief(cycles),
            "Accumulated findings": _findings_brief(findings),
        },
    )


def _default_planner_factory(
    config: Mapping[str, Any], toolbox: GreenfieldToolbox
) -> Callable[[str], Any]:
    """Create a narrow Strands agent backed by Bedrock Runtime.

    The planner deliberately has no model-facing tools.  It selects bounded
    task descriptors; the host remains responsible for dispatching the
    revision-bound evidence tools and validating every task result.
    """

    del toolbox
    model = str(
        config.get("model") or os.environ.get("STRANDS_PLANNER_MODEL") or ""
    ).strip()
    if not model:
        raise StrandsPlannerError(
            "Strands planner model is not configured; set STRANDS_PLANNER_MODEL "
            "or planner model in the runtime configuration"
        )
    max_tokens = config.get("max_tokens", 8192)
    if (
        isinstance(max_tokens, bool)
        or not isinstance(max_tokens, int)
        or max_tokens <= 0
    ):
        raise StrandsPlannerError(
            "Strands planner max_tokens must be a positive integer"
        )
    try:
        from strands import Agent
        from strands.models.bedrock import BedrockModel
    except ImportError as exc:  # pragma: no cover - depends on optional runtime
        raise StrandsPlannerError(
            "Strands Bedrock runtime is not installed; install strands-agents before enabling planner mode"
        ) from exc

    bedrock_model = BedrockModel(model_id=model, max_tokens=max_tokens)

    def new_agent() -> Any:
        """Create a stateless turn agent so replan history cannot grow unbounded."""

        return Agent(
            model=bedrock_model,
            system_prompt=(
                "You plan bounded, read-only Greenfield investigations. Return only "
                "the requested JSON task plan. Never claim evidence or execute writes."
            ),
            callback_handler=None,
            structured_output_model=PlannerTaskResponse,
        )

    def run_planner(prompt: str) -> Any:
        """Adapt the host prompt runner to Strands' callable Agent API."""

        return new_agent()(prompt)

    return run_planner


def _response_text(raw: Any) -> str:
    if isinstance(raw, str):
        return raw
    model_dump = getattr(raw, "model_dump", None)
    if callable(model_dump):
        return json.dumps(model_dump(), sort_keys=True)
    for attribute in ("content", "text"):
        value = getattr(raw, attribute, None)
        if isinstance(value, str):
            return value
    message = getattr(raw, "message", None)
    if isinstance(message, Mapping):
        content = message.get("content")
        if isinstance(content, list):
            text = "\n".join(
                str(block["text"])
                for block in content
                if isinstance(block, Mapping) and isinstance(block.get("text"), str)
            )
            if text:
                return text
        if isinstance(message.get("text"), str):
            return str(message["text"])
    if isinstance(raw, Mapping):
        for key in ("content", "message", "text", "output"):
            value = raw.get(key)
            if isinstance(value, str):
                return value
            if key in {"content", "message", "output"} and isinstance(value, Mapping):
                content = value.get("content")
                if isinstance(content, list):
                    text = "\n".join(
                        str(block["text"])
                        for block in content
                        if isinstance(block, Mapping)
                        and isinstance(block.get("text"), str)
                    )
                    if text:
                        return text
    return str(raw)


def _run_planner_call(
    runner: Callable[[str], Any], prompt: str, *, timeout: float
) -> Any:
    if timeout <= 0:
        raise StrandsPlannerError("Strands planner deadline exceeded")
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(runner, prompt)
    try:
        return future.result(timeout=timeout)
    except FutureTimeoutError as exc:
        future.cancel()
        raise StrandsPlannerError("Strands planner deadline exceeded") from exc
    except StrandsPlannerError:
        raise
    except Exception as exc:  # pragma: no cover - provider-specific failure shape
        raise StrandsPlannerError(
            f"Strands planner execution failed: {_redacted_error(exc)}"
        ) from exc
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _remaining_deadline(deadline: float) -> float:
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise StrandsPlannerError("Strands planner deadline exceeded")
    return remaining


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
        raise StrandsPlannerError("Strands planner did not return JSON") from exc
    rows = value.get("tasks") if isinstance(value, Mapping) else None
    if not isinstance(rows, list):
        raise StrandsPlannerError("Strands planner response requires tasks")
    if len(rows) > MAX_TASKS:
        raise StrandsPlannerError(f"Strands planner returned more than {MAX_TASKS} tasks")
    allowed = {str(run_context["source"]["repository"])} | {
        str(row["repository"]) for row in run_context["candidate_repositories"]
    }
    tasks: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping) or row.get("task_type") not in TASK_TYPES:
            raise StrandsPlannerError(f"planner task {index} has an invalid task_type")
        repository = row.get("repository")
        if repository is not None and str(repository) not in allowed:
            raise StrandsPlannerError(f"planner task {index} is outside captured scope")
        task_type = str(row["task_type"])
        if task_type in REPOSITORY_REQUIRED_TASK_TYPES and not repository:
            raise StrandsPlannerError(f"planner task {index} requires a captured repository")
        if task_type == "synthesize_review" and repository is not None:
            raise StrandsPlannerError("synthesis task cannot name a repository")
        question = str(row.get("question") or "").strip()
        if not question:
            raise StrandsPlannerError(f"planner task {index} requires a question")
        if len(question) > MAX_QUESTION_LENGTH:
            raise StrandsPlannerError(f"planner task {index} question is too long")
        task_id = str(row.get("task_id") or f"planner-{index + 1:02d}")
        if len(task_id) > MAX_TASK_ID_LENGTH:
            raise StrandsPlannerError(f"planner task {index} task_id is too long")
        if task_id in seen_ids:
            raise StrandsPlannerError(f"planner task IDs must be unique: {task_id}")
        seen_ids.add(task_id)
        tasks.append(
            {
                "task_id": task_id,
                "task_type": task_type,
                "question": question,
                **({"repository": str(repository)} if repository is not None else {}),
                **(
                    {"claim_id": str(row["claim_id"])}
                    if row.get("claim_id") is not None
                    else {}
                ),
            }
        )
        if task_type == "challenge_claim" and not tasks[-1].get("claim_id"):
            raise StrandsPlannerError(f"planner task {index} challenge requires claim_id")
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
    timeout: float,
    strands_factory: Callable[[str | None], Callable[[str], Any]] | None,
) -> dict[str, Any]:
    """Give Strands a single scoped question; all evidence stays in the shared ledger."""

    if task["task_type"] == "synthesize_review":
        # Synthesis is a typed planner turn, not a host-side empty sentinel.
        pass
    scoped = {**summary, "planner_task": dict(task)}
    previous_ids = {str(row["tool_call_id"]) for row in toolbox.ledger()}
    tool_scope = toolbox.open_tool_scope()
    try:
        findings, _ = run_strands_analysis(
            run_context,
            scoped,
            toolbox,
            model=model,
            timeout=timeout,
            agent_factory=strands_factory,
            tool_scope=tool_scope,
        )
    except (StrandsAgentError, RuntimeError, ValueError, OSError) as exc:
        toolbox.close_tool_scope(tool_scope)
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
    toolbox.close_tool_scope(tool_scope)
    result: dict[str, Any] = {
        "evidence_refs": [
            {"tool_call_id": row["tool_call_id"], "result_sha256": row["result_sha256"]}
            for row in toolbox.ledger()
            if str(row["tool_call_id"]) not in previous_ids
        ],
        "findings": findings,
        "status": "complete",
    }
    if task["task_type"] == "challenge_claim":
        challenge = findings.get("challenge") if isinstance(findings, Mapping) else None
        if isinstance(challenge, Mapping):
            result["result"] = dict(challenge)
        else:
            result["status"] = "failed"
            result["error"] = "challenge response did not contain a typed verdict"
    elif task["task_type"] == "synthesize_review":
        result["result"] = {"findings": dict(findings)}
    return result


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
                "claim_id": f"impact:{repository}",
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
                "claim_id": str(action_id),
                "question": f"Challenge draft eligibility for action {action_id} against its cited evidence.",
            }
        )
    return tasks


def _apply_challenge(findings: dict[str, Any], result: Mapping[str, Any]) -> None:
    challenge = result.get("result")
    if not isinstance(challenge, Mapping):
        return
    if challenge.get("verdict") == "upheld":
        return
    claim_id = str(challenge.get("claim_id") or "")
    for row in findings.get("repository_impacts", []):
        if isinstance(row, dict) and claim_id == f"impact:{row.get('repository')}":
            row["evidence_state"] = "candidate"
            row.pop("challenge_task_id", None)
    for row in findings.get("actions", []):
        if isinstance(row, dict) and claim_id == str(row.get("action_id")):
            row["evidence_state"] = "candidate"
            row["draft_eligible"] = False
            row.pop("challenge_task_id", None)


def run_strands_planner(
    run_context: Mapping[str, Any],
    summary: Mapping[str, Any],
    toolbox: GreenfieldToolbox,
    *,
    mode: str,
    config: Mapping[str, Any] | None = None,
    planner_factory: Callable[[Mapping[str, Any]], Callable[[str], Any]] | None = None,
    strands_factory: Callable[[str | None], Callable[[str], Any]] | None = None,
    model: str | None = None,
    timeout: float = 300,
    input_artifacts: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Run Strands as the default bounded orchestrator for Analyze."""

    if mode != "default":
        raise StrandsPlannerError("planner mode must be default")
    settings = dict(config or {})
    started = monotonic()
    runner = (
        planner_factory(settings)
        if planner_factory is not None
        else _default_planner_factory(settings, toolbox)
    )
    deadline = started + max(0.001, float(timeout))
    tasks = _parse_tasks(
        _run_planner_call(
            runner,
            _prompt(run_context, summary),
            timeout=_remaining_deadline(deadline),
        ),
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
        raise StrandsPlannerError("planner max_cycles must be an integer") from exc
    if max_cycles < 1 or max_cycles > MAX_TASKS:
        raise StrandsPlannerError("planner max_cycles must be positive")
    queue = list(tasks)
    terminal = False
    stop_reason = "planner_completed_captured_scope"
    while queue and len(cycles) < max_cycles:
        task = queue.pop(0)
        task_id = str(task["task_id"])
        if task_id in completed_ids:
            continue
        if task_id in all_task_ids:
            raise StrandsPlannerError(
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
            timeout=_remaining_deadline(deadline),
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
        if task["task_type"] == "challenge_claim" and result.get("status") == "complete":
            challenge = result.get("result")
            if not isinstance(challenge, Mapping) or challenge.get("claim_id") != task.get("claim_id"):
                result["status"] = "failed"
                result["error"] = "challenge verdict is not bound to the requested claim"
                findings["gaps"].add(f"planner_challenge_unbound:{task_id}")
            elif challenge.get("verdict") not in {"upheld", "downgraded", "rejected"}:
                result["status"] = "failed"
                result["error"] = "challenge verdict is invalid"
                findings["gaps"].add(f"planner_challenge_invalid:{task_id}")
            else:
                _apply_challenge(findings, result)
        elif task["task_type"] == "challenge_claim":
            findings["gaps"].add(f"planner_challenge_failed:{task_id}")
        if task["task_type"] == "synthesize_review":
            terminal = result.get("status") == "complete" and isinstance(
                result.get("result"), Mapping
            )
            decision = "complete" if terminal else "block"
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
                    timeout=_remaining_deadline(deadline),
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
                **({"result": result["result"]} if result.get("result") is not None else {}),
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
        from greenfield.planning_contract import downgrade_incomplete_analysis

        findings = downgrade_incomplete_analysis(
            findings, reason="strands_planner_incomplete"
        )
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
        "name": "strands-bedrock",
        "mode": mode,
    }
    report = build_planning_report(
        run_context,
        mode=mode,
        planner={
            "name": "strands-bedrock",
            "model": settings.get("model") or "configured",
            "elapsed_ms": int((monotonic() - started) * 1000),
        },
        cycles=cycles,
        status=status,
        stop_reason=stop_reason,
        gaps=findings["gaps"],
        analysis=findings,
        input_artifacts=input_artifacts,
    )
    return report


__all__ = [
    "StrandsPlannerError",
    "run_strands_planner",
]
