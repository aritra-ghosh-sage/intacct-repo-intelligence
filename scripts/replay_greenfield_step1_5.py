#!/usr/bin/env python3
"""Replay a recorded greenfield Step 1-5 bundle locally."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from greenfield.step1_5_trace import validate_trace
from greenfield.step1_capture import evidence_fingerprint
from greenfield.step2_candidates import resolve_candidates
from greenfield.step2_contract import (
    load_ci_evidence,
    load_contract,
    load_repository_inventory,
)
from greenfield.step3_outcome import assemble_outcome, load_related_pr_evidence
from greenfield.step4_contract import (
    load_ci_evidence_file,
    load_inventory_evidence,
    load_semantic_evidence,
    validate_step4_report,
)
from greenfield.step4_coverage import map_test_coverage
from greenfield.step5_actions import recommend_actions, validate_step5_report
from scripts.validate_greenfield_step1 import validate as validate_step1


def _read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return value


def _write_json(path: Path, report: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _evidence_path(bundle_dir: Path, name: str) -> str | Path:
    """Use repository-relative paths so replay output is location-independent."""

    repo_root = Path(__file__).resolve().parents[1]
    try:
        return bundle_dir.resolve().relative_to(repo_root) / name
    except ValueError:
        return bundle_dir / name


def _compare(bundle_dir: Path, name: str, report: dict[str, object], errors: list[str]) -> None:
    golden = bundle_dir / name
    if not golden.exists():
        errors.append(f"missing golden artifact: {golden}")
        return
    expected = _read_json(golden)
    if report != expected:
        errors.append(f"artifact mismatch: {name}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bundle-dir",
        required=True,
        type=Path,
        help="Directory containing step1.json and the matching step2-5 goldens.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Optional directory to write regenerated step2-5 reports.",
    )
    args = parser.parse_args(argv)

    bundle_dir = args.bundle_dir
    try:
        step1 = _read_json(bundle_dir / "step1.json")
        step1_errors = validate_step1(step1)
        if step1_errors:
            raise ValueError("invalid step1 report: " + "; ".join(step1_errors))

        trace_path = bundle_dir / "step1.5.trace.json"
        contract_path = bundle_dir / "step1.5.contract.json"
        if trace_path.exists() != contract_path.exists():
            raise ValueError("Step 1.5 bundle must contain both step1.5.trace.json and step1.5.contract.json")
        contract_path = contract_path if contract_path.exists() else _evidence_path(bundle_dir, "step2.contract.yaml")
        if trace_path.exists():
            raw_contract = _read_json(contract_path)
            if raw_contract.get("generation", {}).get("step1_evidence_sha256") != evidence_fingerprint(step1):
                raise ValueError("retained Step 1.5 contract is not linked to Step 1 evidence")
        contract = load_contract(contract_path)
        if trace_path.exists():
            trace = _read_json(trace_path)
            trace_errors = validate_trace(step1, trace)
            if trace_errors:
                raise ValueError("invalid retained Step 1.5 trace: " + "; ".join(trace_errors))
        ci = load_ci_evidence(_evidence_path(bundle_dir, "step2.ci.json"))
        inventory = load_repository_inventory(_evidence_path(bundle_dir, "step2.inventory.json"))

        semantic_index = None
        semantic_path = bundle_dir / "step2.semantic-index.json"
        if semantic_path.exists():
            semantic_index = load_semantic_evidence(
                _evidence_path(bundle_dir, "step2.semantic-index.json")
            )

        related_pr_evidence = None
        related_path = bundle_dir / "step3.related-pr-evidence.json"
        if related_path.exists():
            related_pr_evidence = load_related_pr_evidence(
                _evidence_path(bundle_dir, "step3.related-pr-evidence.json")
            )

        step2 = resolve_candidates(step1, contracts=[contract], ci_evidence=[ci], inventory_evidence=[inventory], semantic_indexes=[semantic_index] if semantic_index else [])
        step3 = assemble_outcome(step2, semantic_index=semantic_index, related_pr_evidence=related_pr_evidence)
        step4 = map_test_coverage(
            step3,
            contracts=[
                contract
            ],
            ci_evidence=[
                load_ci_evidence_file(_evidence_path(bundle_dir, "step2.ci.json"))
            ],
            inventory_evidence=[
                load_inventory_evidence(_evidence_path(bundle_dir, "step2.inventory.json"))
            ],
            semantic_indexes=[semantic_index] if semantic_index else [],
        )
        step5 = recommend_actions(step3, step4)

        errors: list[str] = []
        retained_artifacts = []
        if trace_path.exists():
            retained_artifacts = [
                ("step1.5.trace.json", _read_json(trace_path)),
                ("step1.5.contract.json", raw_contract),
            ]
            for name, artifact in retained_artifacts:
                _compare(bundle_dir, name, artifact, errors)
        _compare(bundle_dir, "step2.report.json", step2, errors)
        _compare(bundle_dir, "step3.report.json", step3, errors)
        _compare(bundle_dir, "step4.report.json", step4, errors)
        _compare(bundle_dir, "step5.report.json", step5, errors)

        if args.output_dir:
            for name, artifact in retained_artifacts:
                _write_json(args.output_dir / name, artifact)
            _write_json(args.output_dir / "step2.report.json", step2)
            _write_json(args.output_dir / "step3.report.json", step3)
            _write_json(args.output_dir / "step4.report.json", step4)
            _write_json(args.output_dir / "step5.report.json", step5)

        summary = {
            "bundle_dir": str(bundle_dir),
            "output_dir": str(args.output_dir) if args.output_dir else None,
            "step1_evidence_sha256": evidence_fingerprint(step1),
            "steps": {
                "step1": step1["status"],
                "step2": step2["status"],
                "step3": step3["status"],
                "step4": step4["status"],
                "step5": step5["status"],
            },
            "validation": {
                "step4": validate_step4_report(step4),
                "step5": validate_step5_report(step5),
            },
            "mismatches": errors,
        }
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        if errors:
            return 2
        return 0
    except (OSError, TypeError, json.JSONDecodeError, ValueError) as exc:
        print(f"greenfield replay failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
