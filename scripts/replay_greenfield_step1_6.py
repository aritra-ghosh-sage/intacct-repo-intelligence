"""Replay a recorded Greenfield Step 1-6 bundle locally."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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
    load_contract_evidence,
    load_inventory_evidence,
    load_semantic_evidence,
    validate_step4_report,
)
from greenfield.step4_coverage import map_test_coverage
from greenfield.step5_actions import recommend_actions, validate_step5_report
from greenfield.step6_contract import load_json, validate_step6_report
from greenfield.step6_patch import generate_step6
from scripts.validate_greenfield_step1 import validate as validate_step1


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _evidence_path(bundle_dir: Path, name: str) -> Path:
    return bundle_dir / name


def _compare(
    bundle_dir: Path, name: str, report: dict[str, object], errors: list[str]
) -> None:
    expected_path = bundle_dir / name
    if not expected_path.exists():
        errors.append(f"missing golden artifact: {expected_path}")
        return
    expected = load_json(expected_path, name)
    if report != expected:
        errors.append(f"artifact mismatch: {name}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-dir", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    bundle = args.bundle_dir
    try:
        step1 = load_json(bundle / "step1.json", "step1 report")
        errors = validate_step1(step1)
        if errors:
            raise ValueError("invalid Step 1 report: " + "; ".join(errors))
        contract_path = _evidence_path(bundle, "step2.contract.yaml")
        ci_path = _evidence_path(bundle, "step2.ci.json")
        inventory_path = _evidence_path(bundle, "step2.inventory.json")
        contract = load_contract(contract_path)
        ci = load_ci_evidence(ci_path)
        inventory = load_repository_inventory(inventory_path)
        semantic = None
        semantic_path = bundle / "step2.semantic-index.json"
        if semantic_path.exists():
            semantic = load_semantic_evidence(semantic_path)
        related = None
        related_path = bundle / "step3.related-pr-evidence.json"
        if related_path.exists():
            related = load_related_pr_evidence(related_path)

        step2 = resolve_candidates(
            step1,
            contracts=[contract],
            ci_evidence=[ci],
            inventory_evidence=[inventory],
            semantic_indexes=[semantic] if semantic else [],
        )
        step3 = assemble_outcome(
            step2, semantic_index=semantic, related_pr_evidence=related
        )
        step4 = map_test_coverage(
            step3,
            contracts=[load_contract_evidence(contract_path)],
            ci_evidence=[load_ci_evidence_file(ci_path)],
            inventory_evidence=[load_inventory_evidence(inventory_path)],
            semantic_indexes=[semantic] if semantic else [],
        )
        step5 = recommend_actions(step3, step4)
        step6 = generate_step6(
            load_json(bundle / "step6.request.json", "Step 6 request"),
            step1,
            step3,
            step4,
            step5,
        )

        mismatches: list[str] = []
        for name, report in (
            ("step2.report.json", step2),
            ("step3.report.json", step3),
            ("step4.report.json", step4),
            ("step5.report.json", step5),
            ("step6.report.json", step6),
        ):
            _compare(bundle, name, report, mismatches)
        if args.output_dir:
            for name, report in (
                ("step2.report.json", step2),
                ("step3.report.json", step3),
                ("step4.report.json", step4),
                ("step5.report.json", step5),
                ("step6.report.json", step6),
            ):
                _write_json(args.output_dir / name, report)
        summary = {
            "bundle_dir": str(bundle),
            "output_dir": str(args.output_dir) if args.output_dir else None,
            "step1_evidence_sha256": evidence_fingerprint(step1),
            "steps": {
                "step1": step1["status"],
                "step2": step2["status"],
                "step3": step3["status"],
                "step4": step4["status"],
                "step5": step5["status"],
                "step6": step6["status"],
            },
            "validation": {
                "step4": validate_step4_report(step4),
                "step5": validate_step5_report(step5),
                "step6": validate_step6_report(step6),
            },
            "mismatches": mismatches,
        }
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 2 if mismatches else 0
    except (OSError, TypeError, json.JSONDecodeError, ValueError) as exc:
        print(f"greenfield replay failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
