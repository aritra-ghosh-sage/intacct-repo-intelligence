"""Validate a greenfield Step 5 action report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from greenfield.step5_actions import validate_step5_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report")
    args = parser.parse_args(argv)
    try:
        report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"greenfield Step 5 validation failed: {exc}", file=sys.stderr)
        return 2
    errors = validate_step5_report(report)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("greenfield Step 5 report is valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
