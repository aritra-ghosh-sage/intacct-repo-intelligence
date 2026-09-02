"""Create a read-only eligible-candidates input for Greenfield Harness PR impact."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from greenfield_harness.artifacts import write_json
from greenfield_harness.candidate_eligibility import (
    EligibilityError,
    build_eligible_candidates,
    default_seeds,
    validate_fixed_seeds,
)


def _github_metadata(repository: str) -> Mapping[str, Any]:
    result = subprocess.run(
        ["gh", "api", f"repos/{repository}"], check=False, capture_output=True, text=True, timeout=30
    )
    if result.returncode:
        raise EligibilityError(result.stderr.strip() or "github_metadata_unavailable")
    value = json.loads(result.stdout)
    if not isinstance(value, Mapping):
        raise EligibilityError("github_metadata_invalid")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seeds-json", type=Path)
    args = parser.parse_args(argv)
    try:
        seeds: list[Mapping[str, Any]] = default_seeds()
        if args.seeds_json:
            value = json.loads(args.seeds_json.read_text(encoding="utf-8"))
            if not isinstance(value, list) or any(not isinstance(row, Mapping) for row in value):
                raise EligibilityError("seeds JSON must be a list of objects")
            seeds = validate_fixed_seeds(value)
        rows = build_eligible_candidates(seeds, _github_metadata)
        write_json(args.output, rows)
    except (EligibilityError, OSError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        print(f"greenfield harness candidate preflight failed: {exc}", file=sys.stderr)
        return 2
    print(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
