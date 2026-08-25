"""Prepare and locally validate a Greenfield Step 8 request without GitHub writes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from greenfield.artifact_io import read_json_object, write_json_atomic
from greenfield.step8_contract import Step8Error, prepare_step8_request
from greenfield.step8_create import (
    NoWriteGitHubWriter,
    RejectingStep8Authorizer,
    create_step8,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step3-report", required=True, type=Path)
    parser.add_argument("--step4-report", required=True, type=Path)
    parser.add_argument("--step6-report", required=True, type=Path)
    parser.add_argument("--step7-report", required=True, type=Path)
    parser.add_argument("--base-branch", required=True)
    parser.add_argument("--request-output", required=True, type=Path)
    parser.add_argument("--result-output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        step3 = read_json_object(args.step3_report)
        step4 = read_json_object(args.step4_report)
        step6 = read_json_object(args.step6_report)
        step7 = read_json_object(args.step7_report)
        request = prepare_step8_request(
            step3, step4, step6, step7, base_branch=args.base_branch
        )
        result = create_step8(
            step3,
            step4,
            step6,
            step7,
            base_branch=args.base_branch,
            authorizer=RejectingStep8Authorizer(),
            github=NoWriteGitHubWriter(),
        )
        write_json_atomic(args.request_output, request)
        write_json_atomic(args.result_output, result)
    except (OSError, TypeError, ValueError, Step8Error) as exc:
        print(f"greenfield Step 8 preparation failed: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": result["status"],
                "authorization": "unavailable_local",
                "request_output": str(args.request_output),
                "result_output": str(args.result_output),
                "github_writes": False,
            },
            sort_keys=True,
        )
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
