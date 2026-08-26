"""Capture read-only GitHub repository and CI inventory evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from greenfield.artifact_io import write_json_atomic
from greenfield.github_repository_evidence import (
    RepositoryEvidenceError,
    collect_repository_evidence,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--source-repository", required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = collect_repository_evidence(
            args.repository,
            source_repository=args.source_repository,
            source_revision=args.source_revision,
        )
        write_json_atomic(args.output, report)
    except (OSError, RepositoryEvidenceError) as exc:
        print(f"greenfield repository evidence failed: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "artifact_status": report["artifact_status"],
                "execution_status": report["execution_status"],
                "output": str(args.output),
                "repository": args.repository,
                "status": report["status"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())