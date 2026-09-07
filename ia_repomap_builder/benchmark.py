"""Known-answer bakeoff scoring for the three context engines."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .builder import build
from .config import BuildRequest, BuildResult


@dataclass(frozen=True)
class EvaluationTask:
    task_id: str
    query: str
    gold_files: frozenset[str]
    gold_symbols: frozenset[str] = frozenset()


def load_tasks(path: Path) -> list[EvaluationTask]:
    """Load a JSON task list without accepting malformed or empty gold sets."""

    try:
        raw_tasks = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read evaluation tasks: {path}: {exc}") from exc
    if not isinstance(raw_tasks, list):
        raise TypeError("evaluation tasks must be a JSON array")
    tasks: list[EvaluationTask] = []
    for index, raw in enumerate(raw_tasks):
        if not isinstance(raw, dict) or not raw.get("id") or not raw.get("query"):
            raise ValueError(f"task {index} requires non-empty id and query")
        gold_files = raw.get("gold_files")
        if not isinstance(gold_files, list) or not gold_files:
            raise ValueError(f"task {index} requires a non-empty gold_files list")
        gold_symbols = raw.get("gold_symbols", [])
        if not isinstance(gold_symbols, list):
            raise TypeError(f"task {index} gold_symbols must be a list")
        tasks.append(
            EvaluationTask(
                task_id=str(raw["id"]),
                query=str(raw["query"]),
                gold_files=frozenset(str(item) for item in gold_files),
                gold_symbols=frozenset(str(item) for item in gold_symbols),
            )
        )
    return tasks


def _candidate_files(result: BuildResult, task: EvaluationTask) -> list[str]:
    paths = [item.path for item in result.items]
    if result.engine == "aider" and result.context:
        # Aider is text-only.  Match against the eligible-file inventory from
        # the adapter and preserve first appearance order in its map. This
        # keeps MRR honest without treating arbitrary prose as a file path.
        present = []
        for path in result.metrics.get("files", []):
            position = result.context.find(path)
            if position >= 0:
                present.append((position, path))
        paths.extend(path for _, path in sorted(present))
    return list(dict.fromkeys(paths))


def _mrr(ranked: list[str], gold: frozenset[str]) -> float:
    for index, path in enumerate(ranked, start=1):
        if path in gold:
            return 1.0 / index
    return 0.0


def score_task(result: BuildResult, task: EvaluationTask, k_values: Iterable[int] = (5, 10)) -> dict:
    ranked_files = _candidate_files(result, task)
    ranked_symbols = {item.symbol for item in result.items if item.symbol}
    row = {
        "task_id": task.task_id,
        "engine": result.engine,
        "status": result.status,
        "ranked_files": ranked_files,
        "mrr": _mrr(ranked_files, task.gold_files),
        "symbol_recall": (
            len(ranked_symbols & task.gold_symbols) / len(task.gold_symbols)
            if task.gold_symbols
            else None
        ),
    }
    for k in k_values:
        row[f"strict_file_recall_at_{k}"] = int(
            task.gold_files.issubset(set(ranked_files[:k]))
        )
        row[f"any_file_recall_at_{k}"] = int(bool(task.gold_files & set(ranked_files[:k])))
    return row


def evaluate(
    repo_root: Path,
    tasks: Iterable[EvaluationTask],
    engine: str,
    scope: tuple[str, ...] = ("app/source",),
    token_budget: int = 4000,
) -> dict:
    """Run one engine and return rows plus aggregate metrics.

    The caller can run this function once per engine and compare the returned
    aggregate values.  No source or context is persisted.
    """

    task_list = list(tasks)
    rows = []
    for task in task_list:
        result = build(
            BuildRequest(
                repo_root=repo_root,
                scope=scope,
                query=task.query,
                engine=engine,
                token_budget=token_budget,
            )
        )
        rows.append(score_task(result, task))

    def mean(name: str) -> float:
        values = [row[name] for row in rows if row["status"] == "ok" and row[name] is not None]
        return sum(values) / len(values) if values else 0.0

    return {
        "engine": engine,
        "tasks": len(task_list),
        "strict_file_recall_at_5": mean("strict_file_recall_at_5"),
        "strict_file_recall_at_10": mean("strict_file_recall_at_10"),
        "any_file_recall_at_5": mean("any_file_recall_at_5"),
        "any_file_recall_at_10": mean("any_file_recall_at_10"),
        "mrr": mean("mrr"),
        "symbol_recall": mean("symbol_recall"),
        "rows": rows,
    }
