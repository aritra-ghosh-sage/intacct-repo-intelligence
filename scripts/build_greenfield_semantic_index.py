"""Build a revision-pinned greenfield semantic sidecar from committed Git blobs."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from greenfield.semantic_contract import SemanticIndexError
from greenfield.semantic_index import build_semantic_index


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--repository", default="ia-main")
    parser.add_argument("--revision", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        index = build_semantic_index(
            args.repo_root,
            repository=args.repository,
            revision=args.revision,
            output=args.output,
        )
    except (OSError, SemanticIndexError) as exc:
        print(f"greenfield semantic index failed: {exc}", file=sys.stderr)
        return 2
    print(
        f"built {args.output}: {len(index['nodes'])} nodes, "
        f"{len(index['edges'])} edges, {len(index['diagnostics'])} diagnostics"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
