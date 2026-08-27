"""Validate a Greenfield test-proposal artifact."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from greenfield.artifact_io import read_json_object

SHA = re.compile(r"^[0-9a-f]{40}$")


def validate(report: Any) -> list[str]:
    if not isinstance(report, dict):
        return ["proposal must be an object"]
    errors: list[str] = []
    if report.get("schema_version") != "0.1":
        errors.append("schema_version must be 0.1")
    if report.get("analysis_kind") != "greenfield_pr_test_proposal":
        errors.append("analysis_kind is invalid")
    if report.get("status") not in {"complete", "partial", "blocked"}:
        errors.append("status is invalid")
    source = report.get("input")
    if not isinstance(source, dict):
        errors.append("input must be an object")
    else:
        if not isinstance(source.get("source_repository"), str) or not source["source_repository"].strip():
            errors.append("input.source_repository is required")
        if not isinstance(source.get("source_revision"), str) or not SHA.fullmatch(source["source_revision"]):
            errors.append("input.source_revision must be a lowercase SHA")
        if not isinstance(source.get("changed_paths"), list) or not source["changed_paths"]:
            errors.append("input.changed_paths must be non-empty")
    proposals = report.get("proposals")
    if not isinstance(proposals, list):
        errors.append("proposals must be a list")
        proposals = []
    for index, proposal in enumerate(proposals):
        if not isinstance(proposal, dict):
            errors.append(f"proposals[{index}] must be an object")
            continue
        for field in ("target_repository", "target_base_revision", "paths", "operation", "test_area", "rationale", "evidence", "validation_commands"):
            if field not in proposal:
                errors.append(f"proposals[{index}].{field} is required")
        if not isinstance(proposal.get("target_repository"), str) or not proposal["target_repository"].strip():
            errors.append(f"proposals[{index}].target_repository is invalid")
        if not isinstance(proposal.get("target_base_revision"), str) or not SHA.fullmatch(proposal["target_base_revision"]):
            errors.append(f"proposals[{index}].target_base_revision is invalid")
        paths = proposal.get("paths")
        if not isinstance(paths, list) or not paths or any(not isinstance(path, str) or not path.strip() or "*" in path or "?" in path for path in paths):
            errors.append(f"proposals[{index}].paths must contain exact paths")
        if proposal.get("operation") not in {"update", "add"}:
            errors.append(f"proposals[{index}].operation is invalid")
        if not isinstance(proposal.get("evidence"), list) or not proposal["evidence"]:
            errors.append(f"proposals[{index}].evidence must be non-empty")
        if not isinstance(proposal.get("validation_commands"), list) or not proposal["validation_commands"]:
            errors.append(f"proposals[{index}].validation_commands must be non-empty")
    if not isinstance(report.get("findings"), list) or any(not isinstance(item, str) for item in report["findings"]):
        errors.append("findings must be a list of strings")
    provenance = report.get("provenance")
    if not isinstance(provenance, dict) or provenance.get("read_only") is not True:
        errors.append("provenance.read_only must be true")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        errors = validate(read_json_object(args.report))
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"test proposal validation failed: {exc}", file=sys.stderr)
        return 2
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
