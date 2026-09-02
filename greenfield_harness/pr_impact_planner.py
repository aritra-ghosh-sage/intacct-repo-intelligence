"""Strict, bounded planning contract for the harness PR-impact experiment."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from .artifacts import sha256

MAX_INITIAL_QUESTIONS = 4
MAX_TEST_QUESTIONS = 6
MAX_SOURCE_READS = 12
MAX_TERMS = 3
MAX_TEXT = 240


class PlannerError(ValueError):
    """Raised when a planner response exceeds the harness evidence boundary."""


class PlannerProvider(Protocol):
    def initial_plan(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def replan(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > MAX_TEXT:
        raise PlannerError(f"{label} must be non-empty text up to {MAX_TEXT} characters")
    return value.strip()


def _ids(value: Any, allowed: set[str], label: str) -> list[str]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or item not in allowed for item in value):
        raise PlannerError(f"{label} must contain known extraction IDs")
    return sorted(set(value))


def _terms(value: Any, label: str, *, allowed: set[str] | None = None) -> list[str]:
    if not isinstance(value, list) or not value or len(value) > MAX_TERMS:
        raise PlannerError(f"{label} must contain 1..{MAX_TERMS} terms")
    result = [_text(item, label) for item in value]
    if len(set(result)) != len(result) or (allowed is not None and not set(result) <= allowed):
        raise PlannerError(f"{label} contains duplicate or unsupported terms")
    return sorted(result)


def _question(
    row: Any, *, allowed_ids: set[str], allowed_terms: set[str], kind: str, allow_ai_terms: bool
) -> dict[str, Any]:
    if not isinstance(row, Mapping) or row.get("type") != kind:
        raise PlannerError(f"planner question must have type {kind}")
    question_id = _text(row.get("id"), "question id")
    evidence_ids = _ids(row.get("evidence_ids"), allowed_ids, "question evidence_ids")
    source_terms = _terms(row.get("source_terms"), "source_terms", allowed=allowed_terms)
    result = {
        "id": question_id,
        "type": kind,
        "question": _text(row.get("question"), "question"),
        "evidence_ids": evidence_ids,
        "source_terms": source_terms,
    }
    if allow_ai_terms:
        value = row.get("ai_terms", [])
        result["ai_terms"] = [] if value == [] else _terms(value, "ai_terms")
    return result


def _validate_anchoring(questions: list[dict[str, Any]], extraction: Mapping[str, Any]) -> None:
    value_by_id = {str(item["id"]): str(item["value"]) for item in extraction["items"]}
    for question in questions:
        anchored = {value_by_id[item] for item in question["evidence_ids"]}
        if not set(question["source_terms"]) <= anchored:
            raise PlannerError("source_terms must be tied to the question evidence_ids")


def initial_plan(value: Mapping[str, Any], extraction: Mapping[str, Any]) -> dict[str, Any]:
    allowed_ids = {str(item["id"]) for item in extraction["items"]}
    allowed_terms = {str(item["value"]) for item in extraction["items"]}
    raw_behaviors = value.get("behaviors")
    raw_questions = value.get("questions")
    if not isinstance(raw_behaviors, list) or not raw_behaviors or not isinstance(raw_questions, list) or not raw_questions or len(raw_questions) > MAX_INITIAL_QUESTIONS:
        raise PlannerError("initial plan requires behaviors and 1..4 questions")
    behaviors = []
    for index, row in enumerate(raw_behaviors):
        if not isinstance(row, Mapping):
            raise PlannerError("behavior must be an object")
        behaviors.append({"id": _text(row.get("id") or f"behavior:{index + 1}", "behavior id"), "summary": _text(row.get("summary"), "behavior summary"), "evidence_ids": _ids(row.get("evidence_ids"), allowed_ids, "behavior evidence_ids"), "status": "candidate"})
    if len({row["id"] for row in behaviors}) != len(behaviors):
        raise PlannerError("behavior IDs must be unique")
    questions = [_question(row, allowed_ids=allowed_ids, allowed_terms=allowed_terms, kind="source_flow", allow_ai_terms=False) for row in raw_questions]
    if len({row["id"] for row in questions}) != len(questions):
        raise PlannerError("question IDs must be unique")
    _validate_anchoring(questions, extraction)
    return {"behaviors": sorted(behaviors, key=lambda row: row["id"]), "questions": sorted(questions, key=lambda row: row["id"])}


def test_plan(value: Mapping[str, Any], extraction: Mapping[str, Any]) -> dict[str, Any]:
    allowed_ids = {str(item["id"]) for item in extraction["items"]}
    allowed_terms = {str(item["value"]) for item in extraction["items"]}
    raw = value.get("questions")
    if not isinstance(raw, list) or not raw or len(raw) > MAX_TEST_QUESTIONS:
        raise PlannerError("replan requires 1..6 test questions")
    questions = [_question(row, allowed_ids=allowed_ids, allowed_terms=allowed_terms, kind="test_discovery", allow_ai_terms=True) for row in raw]
    if len({row["id"] for row in questions}) != len(questions):
        raise PlannerError("question IDs must be unique")
    _validate_anchoring(questions, extraction)
    return {"questions": sorted(questions, key=lambda row: row["id"])}


def report(*, extraction: Mapping[str, Any], initial: Mapping[str, Any], source_ledger: Mapping[str, Any], replan: Mapping[str, Any]) -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema_version": "0.1",
        "artifact_kind": "greenfield_harness_planning_report",
        "extraction_sha256": extraction["extraction_sha256"],
        "budget": {"planner_invocations": 2, "initial_questions": MAX_INITIAL_QUESTIONS, "test_questions": MAX_TEST_QUESTIONS, "source_reads": MAX_SOURCE_READS},
        "initial": initial,
        "source_ledger_sha256": source_ledger["tool_ledger_sha256"],
        "replan": replan,
        "status": "complete",
        "stop_reason": "budget_exhausted_after_replan",
    }
    value["planning_sha256"] = sha256(value)
    return value
