"""Run the small, read-only Greenfield Harness PR-impact experiment."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from greenfield_harness.pr_impact import PrImpactError, run_pr_impact


class StrandsAwsImpactProvider:
    def summarize(self, case_summary: Mapping[str, Any]) -> Mapping[str, Any]:
        model = os.environ.get("STRANDS_HARNESS_MODEL") or os.environ.get("STRANDS_PLANNER_MODEL")
        if not model:
            raise PrImpactError("Strands model is not configured; set STRANDS_HARNESS_MODEL")
        try:
            from strands import Agent
            from strands.models.bedrock import BedrockModel
        except ImportError as exc:  # pragma: no cover - runtime dependent
            raise PrImpactError("Strands Bedrock runtime is not installed") from exc
        prompt = (
            "Return JSON only: {behaviors:[{id,summary,evidence_ids}]}. "
            "Summarize possible changed behavior from the supplied deterministic evidence. "
            "Every behavior must cite one or more supplied evidence IDs. Do not claim test coverage or runtime behavior.\n"
            + json.dumps(case_summary, sort_keys=True, separators=(",", ":"))
        )
        result = Agent(model=BedrockModel(model_id=model, max_tokens=2048), callback_handler=None)(prompt)
        text = result if isinstance(result, str) else getattr(result, "text", None) or getattr(result, "content", None)
        if not isinstance(text, str):
            raise PrImpactError("Strands did not return JSON text")
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise PrImpactError("Strands did not return valid JSON") from exc
        if not isinstance(value, Mapping):
            raise PrImpactError("Strands response must be an object")
        return value


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
