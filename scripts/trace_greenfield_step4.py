"""Map deterministic greenfield Step 4 test coverage."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from greenfield.step4_contract import (
    Step4Error,
    load_ci_evidence_file,
    load_contract_evidence,
    load_inventory_evidence,
    load_semantic_evidence,
    load_step3_report,
)
from greenfield.step4_coverage import map_test_coverage


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step3-report", required=True)
    parser.add_argument("--contract", action="append", default=[])
    parser.add_argument("--ci-evidence", action="append", default=[])
    parser.add_argument("--inventory", action="append", default=[])
    parser.add_argument("--semantic-index", action="append", default=[])
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    try:
        step3 = load_step3_report(args.step3_report)
        contracts = [load_contract_evidence(path) for path in args.contract]
        ci = [load_ci_evidence_file(path) for path in args.ci_evidence]
        inventory = [load_inventory_evidence(path) for path in args.inventory]
        semantic = [load_semantic_evidence(path) for path in args.semantic_index]
        report = map_test_coverage(step3, contracts, ci, inventory, semantic)
    except (OSError, json.JSONDecodeError, Step4Error, ValueError) as exc:
        print(f"greenfield Step 4 failed: {exc}", file=sys.stderr)
        return 2
    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
