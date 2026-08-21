"""Capture exact target file evidence from a GitHub commit without writes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from greenfield.artifact_io import write_json_atomic
from greenfield.github_repository_evidence import (
    RepositoryEvidenceError,
    collect_target_evidence,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--path", action="append", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = collect_target_evidence(
            args.repository,
            revision=args.revision,
            paths=sorted(set(args.path)),
        )
        write_json_atomic(args.output, report)
    except (OSError, RepositoryEvidenceError) as exc:
        print(f"greenfield target evidence failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"output": str(args.output), "status": "available"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
