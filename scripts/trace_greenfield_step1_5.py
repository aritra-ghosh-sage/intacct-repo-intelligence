"""Generate and validate a Strands-agent Greenfield Step 1.5 trace."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from greenfield.artifact_io import read_json_object, write_json_atomic
from greenfield.behavior_contract import BehaviorContractError
from greenfield.llm_env import load_greenfield_env
from greenfield.step1_5_trace import TraceError, validate_trace
from greenfield.strands_agent import (
    Step1TraceFailure,
    StrandsAgentError,
    generate_contract,
    run_strands_trace,
)
from greenfield.strands_config import apply_strands_environment, load_strands_config
from scripts.validate_greenfield_step1 import validate as validate_step1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step1-report", required=True, type=Path)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--trace-output", required=True, type=Path)
    parser.add_argument("--contract-output", required=True, type=Path)
    parser.add_argument("--strands-config", type=Path)
    parser.add_argument("--model")
    parser.add_argument("--timeout", type=int)
    parser.add_argument("--max-file-bytes", type=int, default=500_000)
    parser.add_argument("--context-output", type=Path)
    args = parser.parse_args(argv)
    try:
        load_greenfield_env()
        strands_config = load_strands_config(args.strands_config)
        apply_strands_environment(strands_config)
        model = args.model or strands_config.model
        timeout = args.timeout or strands_config.timeout_seconds
        step1 = read_json_object(args.step1_report)
        errors = validate_step1(step1)
        if errors:
            raise TraceError("invalid Step 1 report: " + "; ".join(errors))
        trace, context = run_strands_trace(
            step1,
            args.source_root,
            model=model,
            timeout=timeout,
            max_file_bytes=args.max_file_bytes,
            max_prompt_bytes=strands_config.max_prompt_bytes,
            max_tokens=strands_config.max_tokens,
            max_continuations=strands_config.max_continuations,
            contract_path=args.contract_output,
            diagnostic_output=args.trace_output.parent / "step1.5.diagnostic.json",
            context_output=args.context_output
            or args.trace_output.parent / "step1.5.source-context.json",
        )
        if validate_trace(step1, trace):
            raise TraceError("generated trace failed validation")
        contract = generate_contract(step1, trace, args.trace_output.as_posix())
        write_json_atomic(args.trace_output, trace)
        write_json_atomic(args.contract_output, contract)
        print(json.dumps({
            "status": "complete",
            "context_sha256": context["context_sha256"],
            "trace": str(args.trace_output),
            "contract": str(args.contract_output),
        }, sort_keys=True))
        return 0
    except (
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        StrandsAgentError,
        Step1TraceFailure,
        TraceError,
        BehaviorContractError,
    ) as exc:
        print(f"greenfield Step 1.5 failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
