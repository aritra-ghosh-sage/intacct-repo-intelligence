"""Prepare a deterministic profile-backed Greenfield Step 7 request."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from greenfield.artifact_io import write_json_atomic
from greenfield.step6_contract import Step6Error, load_json
from greenfield.step7_prepare import prepare_step7
from greenfield.step7_profiles import Step7ProfileError, load_profile_registry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step6-report", required=True, type=Path)
    parser.add_argument("--profiles", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = load_json(args.step6_report, "Step 6 report")
        registry = load_profile_registry(args.profiles)
        artifact = prepare_step7(report, registry)
        write_json_atomic(args.output, artifact)
    except (OSError, Step6Error, Step7ProfileError) as exc:
        print(f"greenfield Step 7 preparation failed: {exc}", file=sys.stderr)
        return 2
    ready = artifact.get("analysis_kind") == "greenfield_pr_impact_step_7_request"
    print(
        json.dumps(
            {
                "status": "ready" if ready else "blocked",
                "output": str(args.output),
            },
            sort_keys=True,
        )
    )
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
