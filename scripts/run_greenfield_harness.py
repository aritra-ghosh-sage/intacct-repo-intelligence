"""Run the isolated, analyze-only Greenfield Harness experiment."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from greenfield_harness.engine import HarnessError, run_harness


def _json(path: Path | None, default: object) -> object:
    return default if path is None else json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--base-revision", required=True)
    parser.add_argument("--target-revision", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--candidates-json", type=Path)
    parser.add_argument("--handbook-json", type=Path)
    parser.add_argument("--contracts-json", type=Path)
    parser.add_argument("--gap-requests-json", type=Path)
    parser.add_argument("--budgets-json", type=Path)
    args = parser.parse_args(argv)
    inputs = [
        path
        for path in (
            args.candidates_json,
            args.handbook_json,
            args.contracts_json,
            args.gap_requests_json,
            args.budgets_json,
        )
        if path
    ]
    try:
        paths = run_harness(
            source_root=args.source_root,
            output_dir=args.output_dir,
            pr=args.pr,
            base_revision=args.base_revision,
            target_revision=args.target_revision,
            candidates=_json(args.candidates_json, []),
            handbook=_json(args.handbook_json, None),
            contracts=_json(args.contracts_json, []),
            gap_requests=_json(args.gap_requests_json, []),
            budgets=_json(args.budgets_json, None),
            input_records=inputs,
        )
    except (HarnessError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"greenfield harness failed: {exc}", file=sys.stderr)
        return 2
    print(paths["analysis"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
