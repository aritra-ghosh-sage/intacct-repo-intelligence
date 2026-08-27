"""Build a revision-bound repository behavior handbook."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from greenfield.artifact_io import read_json_object, write_json_atomic
from greenfield.repository_handbook import (
    RepositoryHandbookError,
    build_repository_handbook,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        handbook = build_repository_handbook(
            read_json_object(args.contract), args.source_root
        )
        write_json_atomic(args.output, handbook)
    except (OSError, TypeError, ValueError, RepositoryHandbookError) as exc:
        print(f"repository handbook build failed: {exc}", file=sys.stderr)
        return 2
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
