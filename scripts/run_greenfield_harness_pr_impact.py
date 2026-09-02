"""Run the small, read-only Greenfield Harness PR-impact experiment."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from greenfield.llm_env import load_greenfield_env
from greenfield_harness.pr_impact import PrImpactError, run_pr_impact


def _result_text(result: Any) -> str | None:
    if isinstance(result, str):
        return result
    for attribute in ("text", "content"):
        value = getattr(result, attribute, None)
        if isinstance(value, str):
            return value
    message = getattr(result, "message", None)
    if isinstance(message, Mapping):
        blocks = message.get("content")
        if isinstance(blocks, list):
            values = [block.get("text") for block in blocks if isinstance(block, Mapping)]
            text = "\n".join(value for value in values if isinstance(value, str))
            if text:
                return text
    rendered = str(result)
    return rendered if rendered else None


def _json_object(text: str) -> Mapping[str, Any]:
    candidates = [text]
    fenced = re.fullmatch(r"\s*```(?:json)?\s*(\{.*\})\s*```\s*", text, re.DOTALL)
    if fenced:
        candidates.append(fenced.group(1))
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, Mapping):
            return value
    raise PrImpactError("Strands did not return a JSON object")


class StrandsAwsImpactProvider:
    def _ask(self, instruction: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        model = (
            os.environ.get("STRANDS_HARNESS_MODEL")
            or os.environ.get("STRANDS_PLANNER_MODEL")
            or os.environ.get("STRANDS_MODEL")
        )
        if not model:
            raise PrImpactError("Strands model is not configured; set STRANDS_HARNESS_MODEL")
        try:
            from strands import Agent
            from strands.models.bedrock import BedrockModel
        except ImportError as exc:  # pragma: no cover - runtime dependent
            raise PrImpactError("Strands Bedrock runtime is not installed") from exc
        prompt = instruction + "\n" + json.dumps(payload, sort_keys=True, separators=(",", ":"))
        result = Agent(model=BedrockModel(model_id=model, max_tokens=2048), callback_handler=None)(prompt)
        text = _result_text(result)
        if not isinstance(text, str):
            raise PrImpactError("Strands did not return JSON text")
        return _json_object(text)

    def initial_plan(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._ask(
            "Return JSON only with behaviors and questions. behaviors entries require id, summary, evidence_ids. "
            "questions entries require id, type=source_flow, question, evidence_ids, source_terms. "
            "Use at most 4 questions and 3 source_terms per question. Every term must exactly equal a supplied extraction value and every evidence ID must be supplied. "
            "Plan read-only source-flow investigation only; do not claim coverage, CI execution, or discover repositories.", request)

    def replan(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._ask(
            "Return JSON only: {questions:[...]}. Each entry requires id, type=test_discovery, question, evidence_ids, source_terms, ai_terms. "
            "Use 1..6 questions, at most 3 source_terms and 3 ai_terms each. source_terms must exactly equal supplied extraction values; ai_terms are lexical candidate search terms only. "
            "Use retained source-ledger results; do not claim coverage, CI execution, or discover repositories.", request)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--base-revision", required=True)
    parser.add_argument("--target-revision", required=True)
    parser.add_argument("--candidates-json", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        load_greenfield_env()
        candidates = json.loads(args.candidates_json.read_text(encoding="utf-8"))
        if not isinstance(candidates, list):
            raise PrImpactError("candidates JSON must be a list")
        paths = run_pr_impact(
            source_root=args.source_root, output_dir=args.output_dir, pr=args.pr,
            base_revision=args.base_revision, target_revision=args.target_revision,
            candidates=candidates, provider=StrandsAwsImpactProvider(), input_paths=[args.candidates_json],
        )
    except (OSError, ValueError, PrImpactError, json.JSONDecodeError) as exc:
        print(f"greenfield harness PR-impact failed: {exc}", file=sys.stderr)
        return 2
    print(paths["analysis"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
