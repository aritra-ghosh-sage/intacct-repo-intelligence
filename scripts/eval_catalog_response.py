"""Evaluate catalog answers with deterministic evidence checks and Deepeval.

The response adapter is intentionally external to this module. It can wrap the
production MCP path, query scripts, or a model API without making the evaluator
depend on one provider.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from deepeval.metrics import (  # type: ignore
        AnswerRelevancyMetric,
        FaithfulnessMetric,
        HallucinationMetric,
    )
    from deepeval.test_case import LLMTestCase  # type: ignore

    DEEPEVAL_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - environment-specific
    AnswerRelevancyMetric = None
    FaithfulnessMetric = None
    HallucinationMetric = None
    LLMTestCase = None
    DEEPEVAL_IMPORT_ERROR = exc


DEFAULT_CASES = ROOT / "evals/catalog_eval_cases.jsonl"
NUMBER_RE = re.compile(r"(?<![A-Za-z])[-+]?\d[\d,]*(?:\.\d+)?(?![A-Za-z])")
PATH_RE = re.compile(r"(?<![\w])(?:[\w.-]+/)+[\w.-]+\.[A-Za-z]{2,5}(?![\w])")
UNCERTAINTY_TERMS = (
    "cannot tell",
    "can't tell",
    "uncertain",
    "incomplete",
    "not enough evidence",
    "appears incomplete",
    "cannot confirm",
)
NUMBER_WORDS = {
    0: "zero",
    1: "one",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
    10: "ten",
    11: "eleven",
    12: "twelve",
    13: "thirteen",
}


@dataclass
class CaseScore:
    case_id: str
    expected_behavior: str
    grounding_score: float
    completeness_score: float
    uncertainty_score: float
    format_score: float
    tool_path_score: float = 1.0
    hallucination_flags: list[str] = field(default_factory=list)
    tool_path_notes: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    deepeval: dict[str, Any] | None = None
    trace_summary: dict[str, Any] | None = None

    @property
    def hard_fail(self) -> bool:
        return bool(self.hallucination_flags)

    @property
    def semantic_score(self) -> float | None:
        if not self.deepeval or self.deepeval.get("skipped"):
            return None
        faithfulness = self.deepeval.get("faithfulness", {}).get("score")
        relevance = self.deepeval.get("relevance", {}).get("score")
        hallucination = self.deepeval.get("hallucination", {}).get("score")
        if not all(isinstance(value, (int, float)) for value in (faithfulness, relevance, hallucination)):
            return None
        return round((faithfulness + relevance + (1 - hallucination)) / 3, 3)

    @property
    def overall(self) -> float:
        if self.hard_fail:
            return 0.0
        deterministic = round(
            0.30 * self.grounding_score
            + 0.25 * self.completeness_score
            + 0.20 * self.uncertainty_score
            + 0.10 * self.format_score
            + 0.15 * self.tool_path_score,
            3,
        )
        semantic = self.semantic_score
        return round(0.7 * deterministic + 0.3 * semantic, 3) if semantic is not None else deterministic

    @property
    def verdict(self) -> str:
        if self.hard_fail:
            return "hard_fail"
        if self.tool_path_score < 1.0:
            return "quality_fail"
        if "missing_primary_signal" in self.notes:
            return "quality_fail"
        if self.deepeval and self.deepeval.get("skipped"):
            return "indeterminate"
        if self.deepeval:
            hallucination = self.deepeval.get("hallucination", {}).get("score")
            relevance = self.deepeval.get("relevance", {}).get("score")
            faithfulness = self.deepeval.get("faithfulness", {}).get("score")
            if (
                isinstance(hallucination, (int, float))
                and hallucination > 0.15
                or isinstance(relevance, (int, float))
                and relevance < 0.70
                or isinstance(faithfulness, (int, float))
                and faithfulness < 0.85
            ):
                return "quality_fail"
        semantic = self.semantic_score
        if semantic is not None and semantic < 0.75:
            return "quality_fail"
        if self.overall < 0.75:
            return "quality_fail"
        return "pass"


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    ids = [case.get("case_id") for case in cases]
    if any(not case_id for case_id in ids) or len(ids) != len(set(ids)):
        raise ValueError("Dataset case_id values must be present and unique")
    return cases


def flatten_payload(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def _leaf_values(value: Any) -> list[Any]:
    if isinstance(value, dict):
        return [item for child in value.values() for item in _leaf_values(child)]
    if isinstance(value, list):
        return [item for child in value for item in _leaf_values(child)]
    return [value]


def _number(value: str) -> float | int:
    normalized = value.replace(",", "")
    parsed = float(normalized)
    return int(parsed) if parsed.is_integer() else parsed


def _value_mentioned(answer: str, value: str) -> bool:
    normalized_answer = answer.lower().replace(",", "")
    if value in normalized_answer:
        return True
    try:
        parsed = _number(value)
    except ValueError:
        return False
    return isinstance(parsed, int) and NUMBER_WORDS.get(parsed) in normalized_answer


def _allowed_numbers(payload: dict[str, Any]) -> set[float | int]:
    return {
        value
        for leaf in _leaf_values(payload)
        if isinstance(leaf, (int, float)) and not isinstance(leaf, bool)
        for value in (leaf,)
    }


def _ranked_top_values(payload: dict[str, Any]) -> list[str]:
    data = payload.get("data", {})
    values: list[str] = []
    for key, label in (("languages", "language"), ("directories", "top_dir")):
        rows = data.get(key, [])
        ranked = sorted(rows, key=lambda row: row.get("file_count", 0), reverse=True)
        if ranked and ranked[0].get(label):
            values.append(str(ranked[0][label]).lower())
    return values


def _case_text(case: dict[str, Any]) -> str:
    return " ".join(
        [
            str(case.get("prompt", "")),
            str(case.get("reference", "")),
            " ".join(str(value) for value in case.get("constraints", [])),
        ]
    ).lower()


def _needs_ranked_primary_signal(case: dict[str, Any]) -> bool:
    if case.get("expected_behavior") not in {"summary", "hard_summary"}:
        return False
    text = _case_text(case)
    return any(
        keyword in text
        for keyword in (
            "top",
            "largest",
            "dominant",
            "leading",
            "strongest",
            "highest",
            "rank",
            "ranking",
            "order",
        )
    )


def _needs_row_examples(case: dict[str, Any]) -> bool:
    if case.get("expected_behavior") not in {"lookup", "hard_lookup"}:
        return False
    text = _case_text(case)
    return any(keyword in text for keyword in ("list", "match", "matches", "path", "paths", "strongest", "top"))


def _primary_row_terms(payload: dict[str, Any]) -> list[str]:
    data = payload.get("data", {})
    if data.get("matches"):
        first = data["matches"][0]
        return [str(first.get("path", "")).lower(), str(first.get("language", "")).lower()]
    if data.get("mapped_symbols"):
        first = data["mapped_symbols"][0]
        return [str(first.get("name", "")).lower(), str(first.get("mapping_type", "")).lower()]
    if data.get("endpoint_coverage"):
        first = data["endpoint_coverage"][0]
        return [str(first.get("path", "")).lower(), str(first.get("coverage", "")).lower()]
    if data.get("directories"):
        first = data["directories"][0]
        return [str(first.get("top_dir", "")).lower()]
    if data.get("languages"):
        first = data["languages"][0]
        return [str(first.get("language", "")).lower()]
    return []


def _summary_numeric_terms(payload: dict[str, Any]) -> list[str]:
    summary = payload.get("summary", {})
    return [str(value) for value in summary.values() if isinstance(value, (int, float))]


def _tool_requirement(case: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    required_tool = case.get("required_tool")
    required_args = case.get("required_tool_args") or {}
    if required_tool is not None:
        required_tool = str(required_tool)
    if not isinstance(required_args, dict):
        required_args = {}
    return required_tool, required_args


def _trace_steps(trace: Any) -> list[dict[str, Any]]:
    if trace is None:
        return []
    if isinstance(trace, list):
        return [step for step in trace if isinstance(step, dict)]
    if isinstance(trace, dict):
        for key in ("tool_calls", "steps", "trace", "events"):
            value = trace.get(key)
            if isinstance(value, list):
                return [step for step in value if isinstance(step, dict)]
        if "tool" in trace or "name" in trace or "tool_name" in trace:
            return [trace]
    return []


def _trace_tool_name(step: dict[str, Any]) -> str | None:
    for key in ("tool", "tool_name", "name"):
        value = step.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    function = step.get("function")
    if isinstance(function, dict):
        value = function.get("name")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _trace_tool_arguments(step: dict[str, Any]) -> dict[str, Any]:
    for key in ("arguments", "args", "parameters", "input"):
        value = step.get(key)
        if isinstance(value, dict):
            return value
    function = step.get("function")
    if isinstance(function, dict):
        value = function.get("arguments")
        if isinstance(value, dict):
            return value
    return {}


def _contains_subset(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        for key, value in expected.items():
            if key not in actual or not _contains_subset(actual[key], value):
                return False
        return True
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) < len(expected):
            return False
        return all(any(_contains_subset(candidate, item) for candidate in actual) for item in expected)
    return actual == expected


def summarize_trace(trace: Any, case: dict[str, Any]) -> dict[str, Any]:
    steps = _trace_steps(trace)
    tool_names = [name for step in steps if (name := _trace_tool_name(step))]
    required_tool, required_args = _tool_requirement(case)
    required_present = required_tool in tool_names if required_tool else True
    required_args_match = True
    if required_tool and required_args:
        required_args_match = any(
            _contains_subset(_trace_tool_arguments(step), required_args)
            for step in steps
            if _trace_tool_name(step) == required_tool
        )
    return {
        "tool_count": len(steps),
        "tool_names": tool_names,
        "required_tool": required_tool,
        "required_tool_present": required_present,
        "required_tool_args": required_args or None,
        "required_tool_args_match": required_args_match,
    }


def score_tool_path(
    case: dict[str, Any], trace: Any, *, require_trace: bool = False
) -> tuple[float, list[str], dict[str, Any] | None]:
    required_tool, required_args = _tool_requirement(case)
    if required_tool is None:
        return 1.0, [], summarize_trace(trace, case) if trace is not None else None
    if trace is None:
        if require_trace:
            return 0.0, ["missing_trace"], {"required_tool": required_tool, "required_tool_present": False, "tool_count": 0, "tool_names": []}
        return 1.0, [], None

    summary = summarize_trace(trace, case)
    if summary["tool_count"] == 0:
        return 0.0, ["malformed_trace"], summary
    if not summary["required_tool_present"]:
        return 0.0, [f"missing_required_tool:{required_tool}"], summary
    if required_args and not summary["required_tool_args_match"]:
        return 0.0, [f"wrong_tool_arguments:{required_tool}"], summary
    return 1.0, [], summary


def required_evidence_terms(case: dict[str, Any]) -> list[str]:
    payload = case["payload"]
    terms = {str(value).lower() for value in _summary_numeric_terms(payload)}
    terms.update(_ranked_top_values(payload))
    if _needs_row_examples(case):
        terms.update(_primary_row_terms(payload))
    data = payload.get("data", {})
    for key, fields in (
        ("matches", ("path", "language")),
        ("mapped_symbols", ("name", "mapping_type")),
        ("endpoint_coverage", ("path", "coverage")),
    ):
        if data.get(key):
            terms.update(str(data[key][0].get(field, "")).lower() for field in fields)
    return sorted(term for term in terms if term)


def hallucination_flags(answer: str, payload: dict[str, Any]) -> list[str]:
    """Return hard factual violations, not merely diagnostic warnings."""
    flags: list[str] = []
    allowed_numbers = _allowed_numbers(payload)
    for token in NUMBER_RE.findall(answer):
        try:
            value = _number(token)
        except ValueError:
            continue
        if value not in allowed_numbers:
            flags.append(f"unsupported_number:{token}")

    payload_text = flatten_payload(payload).lower()
    for path_like in sorted(set(PATH_RE.findall(answer))):
        if path_like.lower() not in payload_text:
            flags.append(f"unsupported_path:{path_like}")

    answer_l = answer.lower()
    for top in _ranked_top_values(payload):
        if re.search(r"\b(?:dominant|largest|most|top)\b", answer_l):
            claimed = re.search(
                r"([a-z][a-z0-9_-]*)\s+(?:is|has|with)\s+(?:the\s+)?(?:dominant|largest|most|top)",
                answer_l,
            )
            if claimed and claimed.group(1) != top:
                flags.append(f"wrong_top_value:{claimed.group(1)}")
    return sorted(set(flags))


def score_grounding(answer: str, case: dict[str, Any]) -> tuple[float, list[str]]:
    if case.get("expected_behavior") == "uncertainty":
        return 1.0, []
    terms = required_evidence_terms(case)
    answer_l = answer.lower()
    matched = sum(_value_mentioned(answer_l, term) if term.replace(".", "", 1).isdigit() else term in answer_l for term in terms if term)
    score = 1.0 if not terms else matched / len(terms)
    notes = ["low_signal_overlap"] if score < 0.5 else []
    return round(score, 3), notes


def score_completeness(answer: str, case: dict[str, Any]) -> tuple[float, list[str]]:
    expected = case.get("expected_behavior", "")
    answer_l = answer.lower()
    if expected == "uncertainty":
        ok = any(term in answer_l for term in UNCERTAINTY_TERMS)
        return (1.0 if ok else 0.0), ([] if ok else ["missing_uncertainty_language"])

    payload = case["payload"]
    required_values = [(term, 1) for term in _summary_numeric_terms(payload)]
    if _needs_ranked_primary_signal(case):
        required_values.extend((term, 2) for term in _ranked_top_values(payload))
    if _needs_row_examples(case):
        required_values.extend((term, 2) for term in _primary_row_terms(payload))
    if not required_values:
        return 1.0, []
    hits = sum(weight for term, weight in required_values if _value_mentioned(answer_l, term))
    score = hits / sum(weight for _, weight in required_values)
    notes = []
    if any(not _value_mentioned(answer_l, term) for term, weight in required_values if weight > 1):
        notes.append("missing_primary_signal")
    if score < 0.5:
        notes.append("missing_key_counts")
    return round(score, 3), notes


def score_uncertainty(answer: str, case: dict[str, Any]) -> tuple[float, list[str]]:
    answer_l = answer.lower()
    if case.get("expected_behavior") == "uncertainty":
        ok = any(term in answer_l for term in UNCERTAINTY_TERMS)
        return (1.0 if ok else 0.0), ([] if ok else ["uncertainty_not_explicit"])
    return (0.8, ["unnecessary_uncertainty"]) if any(term in answer_l for term in UNCERTAINTY_TERMS) else (1.0, [])


def score_format(answer: str, case: dict[str, Any]) -> tuple[float, list[str]]:
    expected = case.get("expected_behavior", "")
    words = len(answer.split())
    if expected in {"lookup", "hard_lookup"} and words > 180:
        return 0.6, ["too_verbose_for_lookup"]
    if expected in {"summary", "hard_summary", "uncertainty"} and words < 8:
        return 0.5, ["too_short"]
    return 1.0, []


def score_case(
    answer: str,
    case: dict[str, Any],
    *,
    trace: Any = None,
    require_trace: bool = False,
) -> CaseScore:
    grounding, grounding_notes = score_grounding(answer, case)
    completeness, completeness_notes = score_completeness(answer, case)
    uncertainty, uncertainty_notes = score_uncertainty(answer, case)
    response_format, format_notes = score_format(answer, case)
    tool_path, tool_path_notes, trace_summary = score_tool_path(case, trace, require_trace=require_trace)
    flags = hallucination_flags(answer, case["payload"])
    notes = grounding_notes + completeness_notes + uncertainty_notes + format_notes + tool_path_notes
    if flags:
        notes.append("hard_evidence_violation")
    return CaseScore(
        case_id=case["case_id"],
        expected_behavior=case["expected_behavior"],
        grounding_score=grounding,
        completeness_score=completeness,
        uncertainty_score=uncertainty,
        format_score=response_format,
        tool_path_score=tool_path,
        hallucination_flags=flags,
        tool_path_notes=tool_path_notes,
        notes=notes,
        trace_summary=trace_summary,
    )


def run_deepeval(case: dict[str, Any], answer: str) -> dict[str, Any] | None:
    if LLMTestCase is None:
        return None
    if not (os.getenv("OPENAI_API_KEY") or os.getenv("DEEPEVAL_API_KEY")):
        return {"skipped": True, "reason": "missing_api_key"}
    context = [flatten_payload(case["payload"])]
    test_case = LLMTestCase(
        input=case["prompt"],
        actual_output=answer,
        context=context,
        retrieval_context=context,
        expected_output=case.get("reference"),
    )
    metric_specs = (
        ("hallucination", HallucinationMetric(threshold=0.15, async_mode=False, verbose_mode=False)),
        ("relevance", AnswerRelevancyMetric(threshold=0.70, async_mode=False, verbose_mode=False)),
        ("faithfulness", FaithfulnessMetric(threshold=0.85, async_mode=False, verbose_mode=False)),
    )
    scores: dict[str, Any] = {}
    try:
        for name, metric in metric_specs:
            scores[name] = {
                "score": metric.measure(test_case),
                "success": metric.success,
                "reason": metric.reason,
            }
    except Exception as exc:  # pragma: no cover - runtime/network dependent
        return {"skipped": True, "reason": str(exc)}
    return scores


def _adapter_input(case: dict[str, Any]) -> dict[str, Any]:
    # Gold references and evaluator-only constraints must never reach the model.
    return {
        "case_id": case["case_id"],
        "prompt": case["prompt"],
        "payload": case["payload"],
        "response_contract": case.get("response_contract", "concise grounded answer"),
    }


def run_adapter(command: list[str], case: dict[str, Any], timeout: int) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        input=json.dumps(_adapter_input(case)),
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(f"adapter failed for {case['case_id']}: {completed.stderr.strip()}")
    try:
        output = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"adapter returned invalid JSON for {case['case_id']}") from exc
    if output.get("case_id") != case["case_id"] or not isinstance(output.get("answer"), str):
        raise RuntimeError(f"adapter response must contain matching case_id and string answer for {case['case_id']}")
    return {
        "answer": output["answer"].strip(),
        "trace": output.get("trace"),
    }


def load_recorded_runs(path: Path) -> dict[str, dict[str, Any]]:
    answers: dict[str, dict[str, Any]] = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("case_id") in answers:
            raise ValueError(f"duplicate recorded answer: {record['case_id']}")
        if not isinstance(record.get("answer"), str):
            raise ValueError(f"recorded answer must be a string for {record.get('case_id')}")
        answers[record["case_id"]] = {
            "answer": record["answer"].strip(),
            "trace": record.get("trace"),
        }
    return answers


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--case-id", action="append")
    parser.add_argument("--actual-output")
    parser.add_argument("--actual-output-file")
    parser.add_argument("--responses-jsonl", type=Path)
    parser.add_argument("--adapter-command-json", help="JSON argv for a per-case response adapter.")
    parser.add_argument("--adapter-timeout", type=int, default=120)
    parser.add_argument("--require-trace", action="store_true", help="Require a trace for cases that declare a required tool.")
    parser.add_argument("--run-deepeval", action="store_true")
    parser.add_argument("--require-deepeval", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.require_deepeval and not args.run_deepeval:
        parser.error("--require-deepeval requires --run-deepeval")
    sources = sum(bool(value) for value in (args.actual_output, args.actual_output_file, args.responses_jsonl, args.adapter_command_json))
    if sources != 1:
        parser.error("choose exactly one answer source")
    cases = load_cases(args.cases)
    if args.case_id:
        wanted = set(args.case_id)
        cases = [case for case in cases if case["case_id"] in wanted]
    if not cases:
        parser.error("no evaluation cases selected")

    if args.adapter_command_json:
        command = json.loads(args.adapter_command_json)
        if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
            parser.error("--adapter-command-json must be a JSON string array")
        answers = {case["case_id"]: run_adapter(command, case, args.adapter_timeout) for case in cases}
    elif args.responses_jsonl:
        answers = load_recorded_runs(args.responses_jsonl)
    else:
        answer = args.actual_output if args.actual_output is not None else Path(args.actual_output_file).read_text()
        if len(cases) != 1:
            parser.error("--actual-output and --actual-output-file require exactly one case")
        answers = {cases[0]["case_id"]: {"answer": answer.strip(), "trace": None}}

    results: list[CaseScore] = []
    for case in cases:
        if case["case_id"] not in answers:
            raise ValueError(f"missing answer for {case['case_id']}")
        run = answers[case["case_id"]]
        result = score_case(
            run["answer"],
            case,
            trace=run.get("trace"),
            require_trace=args.require_trace,
        )
        if args.run_deepeval:
            result.deepeval = run_deepeval(case, run["answer"])
        results.append(result)

    deepeval_skipped = any(result.deepeval and result.deepeval.get("skipped") for result in results)
    if args.require_deepeval and (DEEPEVAL_IMPORT_ERROR is not None or deepeval_skipped):
        raise SystemExit("Deepeval was required but could not produce metrics for every case")
    summary = {
        "case_count": len(results),
        "pass_count": sum(result.verdict == "pass" for result in results),
        "hard_fail_count": sum(result.verdict == "hard_fail" for result in results),
        "quality_fail_count": sum(result.verdict == "quality_fail" for result in results),
        "overall_mean": round(sum(result.overall for result in results) / len(results), 3),
        "grounding_mean": round(sum(result.grounding_score for result in results) / len(results), 3),
        "completeness_mean": round(sum(result.completeness_score for result in results) / len(results), 3),
        "uncertainty_mean": round(sum(result.uncertainty_score for result in results) / len(results), 3),
        "format_mean": round(sum(result.format_score for result in results) / len(results), 3),
        "tool_path_mean": round(sum(result.tool_path_score for result in results) / len(results), 3),
        "deepeval_available": DEEPEVAL_IMPORT_ERROR is None,
        "deepeval_error": None if DEEPEVAL_IMPORT_ERROR is None else str(DEEPEVAL_IMPORT_ERROR),
    }
    report = {
        "summary": summary,
        "results": [
            {
                **asdict(result),
                "overall": result.overall,
                "verdict": result.verdict,
            }
            for result in results
        ],
    }
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(json.dumps(summary, indent=2))
        for result in results:
            print(f"- {result.verdict.upper()} {result.case_id}: {result.overall} ({', '.join(result.notes) or 'none'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
