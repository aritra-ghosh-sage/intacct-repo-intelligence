#!/usr/bin/env python3
"""Capture a read-only repository-neutral greenfield PR evidence report."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from greenfield.step1_capture import CaptureError, blocked_report, capture_pr


def _write_atomic(path: Path, report: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    os.close(descriptor)
    temporary_path = Path(temporary)
    try:
        temporary_path.write_text(
            json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default="config/workspace_repos.yaml")
    parser.add_argument("--repo-key", required=True)
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = capture_pr(
            repo_key=args.repo_key,
            manifest_path=args.manifest,
            pr_number=args.pr,
        )
    except (CaptureError, OSError) as exc:
        report = blocked_report(exc)
    try:
        _write_atomic(args.output, report)
    except OSError as exc:
        print(f"greenfield_step1_failed: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {"output": str(args.output), "status": report["status"]},
            sort_keys=True,
        )
    )
    return 0 if report["status"] != "blocked" else 2


if __name__ == "__main__":
    raise SystemExit(main())
