"""Generate a deterministic Greenfield Step 6 patch handoff."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from greenfield.step6_contract import Step6Error, load_json
from greenfield.step6_patch import generate_step6


def _write_atomic(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--step1-report", required=True, type=Path)
    parser.add_argument("--step3-report", required=True, type=Path)
    parser.add_argument("--step4-report", required=True, type=Path)
    parser.add_argument("--step5-report", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = generate_step6(
            load_json(args.request, "Step 6 request"),
            load_json(args.step1_report, "Step 1 report"),
            load_json(args.step3_report, "Step 3 report"),
            load_json(args.step4_report, "Step 4 report"),
            load_json(args.step5_report, "Step 5 report"),
        )
        _write_atomic(args.output, report)
    except (OSError, Step6Error, ValueError) as exc:
        print(f"greenfield Step 6 failed: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {"status": report["status"], "output": str(args.output)}, sort_keys=True
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
