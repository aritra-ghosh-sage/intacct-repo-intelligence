"""Replay a recorded Greenfield Step 1-6 bundle locally."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from greenfield.artifact_io import artifact_sha256
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
from greenfield.step6_contract import (
    load_json,
    validate_step6_request,
)
from greenfield.step6_patch import generate_step6
from scripts.validate_greenfield_step1 import validate as validate_step1
from scripts.validate_greenfield_step2 import validate as validate_step2
from scripts.validate_greenfield_step3 import validate as validate_step3


def _diff_paths(expected: object, actual: object, path: str = "") -> list[str]:
    if type(expected) is not type(actual):
        return [path or "<root>"]
    if isinstance(expected, dict) and isinstance(actual, dict):
        paths: list[str] = []
        for key in sorted(set(expected) | set(actual)):
            child = f"{path}.{key}" if path else str(key)
            if key not in expected or key not in actual:
                paths.append(child)
            else:
                paths.extend(_diff_paths(expected[key], actual[key], child))
        return paths
    if isinstance(expected, list) and isinstance(actual, list):
        paths = []
        for index in range(max(len(expected), len(actual))):
            child = f"{path}[{index}]"
            if index >= len(expected) or index >= len(actual):
                paths.append(child)
            else:
                paths.extend(_diff_paths(expected[index], actual[index], child))
        return paths
    return [] if expected == actual else [path or "<root>"]


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _evidence_path(bundle_dir: Path, name: str) -> Path:
    repo_root = Path(__file__).resolve().parents[1]
    try:
        relative = bundle_dir.resolve().relative_to(repo_root)
        return repo_root / relative / name
    except ValueError:
        return bundle_dir / name


def _generated_contract_path(step1: dict[str, object]) -> Path:
    revision = step1.get("input", {}).get("target_revision")
    if not isinstance(revision, str):
        raise TypeError(
            "Step 1 target revision is required for generated contract lookup"
        )
    return (
        Path(__file__).resolve().parents[1]
        / "artifacts"
        / "greenfield"
        / "behavior-contracts"
        / "ia-app"
        / revision
        / "contract.json"
    )


def _evidence_label(bundle_dir: Path, name: str) -> str:
    repo_root = Path(__file__).resolve().parents[1]
    try:
        relative = bundle_dir.resolve().relative_to(repo_root)
        return (relative / name).as_posix()
    except ValueError:
        return (bundle_dir / name).as_posix()


def _normalize_evidence_paths(
    contract: dict[str, object],
    ci: dict[str, object],
    inventory: dict[str, object],
    *,
    bundle_dir: Path,
    contract_name: str = "step2.contract.yaml",
) -> None:
    contract_evidence = contract.get("evidence")
    if isinstance(contract_evidence, dict):
        contract_evidence["path"] = _evidence_label(bundle_dir, contract_name)
    ci_evidence = ci.get("evidence")
    if isinstance(ci_evidence, dict):
        ci_evidence["path"] = _evidence_label(bundle_dir, "step2.ci.json")
    inventory["evidence_path"] = _evidence_label(bundle_dir, "step2.inventory.json")


def _compare(
    bundle_dir: Path, name: str, report: dict[str, object], errors: list[str]
) -> None:
    expected_path = bundle_dir / name
    if not expected_path.exists():
        errors.append(f"missing golden artifact: {expected_path}")
        return
    expected = load_json(expected_path, name)
    if report != expected:
        paths = _diff_paths(expected, report)
        suffix = "; fields: " + ", ".join(paths[:20]) if paths else ""
        errors.append(f"artifact mismatch: {name}{suffix}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-dir", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--manifest-output",
        type=Path,
        help="Optionally write a deterministic replay manifest.",
    )
    args = parser.parse_args(argv)
    bundle = args.bundle_dir
    try:
        step1 = load_json(bundle / "step1.json", "step1 report")
        errors = validate_step1(step1)
        if errors:
            raise ValueError("invalid Step 1 report: " + "; ".join(errors))
        generated_contract = _generated_contract_path(step1)
        contract_path = (
            generated_contract
            if generated_contract.exists()
            else _evidence_path(bundle, "step2.contract.yaml")
        )
        ci_paths = sorted(bundle.glob("step2.ci*.json"))
        inventory_paths = sorted(bundle.glob("step2.inventory*.json"))
        if not ci_paths:
            raise ValueError("missing Step 2 CI evidence")
        if not inventory_paths:
            raise ValueError("missing Step 2 inventory evidence")
        contract = load_contract(contract_path)
        ci_rows = [load_ci_evidence(path) for path in ci_paths]
        inventory_rows = [load_repository_inventory(path) for path in inventory_paths]
        for ci_row, ci_file in zip(ci_rows, ci_paths):
            _normalize_evidence_paths(
                contract,
                ci_row,
                inventory_rows[0],
                bundle_dir=bundle,
                contract_name=(
                    "generated_behavior_contract.json"
                    if generated_contract.exists()
                    else "step2.contract.yaml"
                ),
            )
            ci_row["evidence"]["path"] = _evidence_label(bundle, ci_file.name)
        for inventory_row, inventory_file in zip(inventory_rows, inventory_paths):
            inventory_row["evidence_path"] = _evidence_label(
                bundle, inventory_file.name
            )
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
            ci_evidence=ci_rows,
            inventory_evidence=inventory_rows,
            semantic_indexes=[semantic] if semantic else [],
        )
        step2_errors = validate_step2(step2)
        if step2_errors:
            raise ValueError("invalid Step 2 report: " + "; ".join(step2_errors))

        step3 = assemble_outcome(
            step2, semantic_index=semantic, related_pr_evidence=related
        )
        step3_errors = validate_step3(step3)
        if step3_errors:
            raise ValueError("invalid Step 3 report: " + "; ".join(step3_errors))

        contract_evidence = load_contract_evidence(contract_path)
        contract_evidence["evidence"]["path"] = _evidence_label(
            bundle,
            "generated_behavior_contract.json"
            if generated_contract.exists()
            else "step2.contract.yaml",
        )
        ci_evidence = [load_ci_evidence_file(path) for path in ci_paths]
        for row, path in zip(ci_evidence, ci_paths):
            row["evidence"]["path"] = _evidence_label(bundle, path.name)
        inventory_evidence = [load_inventory_evidence(path) for path in inventory_paths]
        for row, path in zip(inventory_evidence, inventory_paths):
            row["evidence_path"] = _evidence_label(bundle, path.name)
        step4 = map_test_coverage(
            step3,
            contracts=[contract_evidence],
            ci_evidence=ci_evidence,
            inventory_evidence=inventory_evidence,
            semantic_indexes=[semantic] if semantic else [],
        )
        step5 = recommend_actions(step3, step4)
        step6_request_path = bundle / "step6.request.json"
        step6 = None
        if step6_request_path.exists():
            step6_request = load_json(step6_request_path, "Step 6 request")
            request_errors = validate_step6_request(step6_request)
            if request_errors:
                raise ValueError("invalid Step 6 request: " + "; ".join(request_errors))
            step6 = generate_step6(
                step6_request,
                step1,
                step3,
                step4,
                step5,
            )

        mismatches: list[str] = []
        reports_to_compare = [
            ("step2.report.json", step2),
            ("step3.report.json", step3),
            ("step4.report.json", step4),
            ("step5.report.json", step5),
        ]
        if step6 is not None:
            reports_to_compare.append(("step6.report.json", step6))
        for name, report in reports_to_compare:
            _compare(bundle, name, report, mismatches)
        if args.output_dir:
            for name, report in reports_to_compare:
                _write_json(args.output_dir / name, report)
        reports = {
            "step1.json": step1,
            "step2.report.json": step2,
            "step3.report.json": step3,
            "step4.report.json": step4,
            "step5.report.json": step5,
        }
        if step6 is not None:
            reports["step6.report.json"] = step6
        manifest = {
            "schema_version": "0.1",
            "bundle": str(bundle),
            "artifacts": [
                {
                    "name": name,
                    "sha256": artifact_sha256(value),
                    "status": value.get("status"),
                    "rule_set_version": value.get("provenance", {}).get(
                        "rule_set_version"
                    )
                    if isinstance(value.get("provenance"), dict)
                    else None,
                }
                for name, value in sorted(reports.items())
            ],
            "source": step1.get("input", {}),
            "target": step6.get("target", {}) if step6 is not None else None,
            "mismatches": mismatches,
        }
        if args.manifest_output:
            _write_json(args.manifest_output, manifest)
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
            },
            "validation": {
                "step4": validate_step4_report(step4),
                "step5": validate_step5_report(step5),
            },
            "mismatches": mismatches,
            "manifest": str(args.manifest_output) if args.manifest_output else None,
        }
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 2 if mismatches else 0
    except (OSError, TypeError, json.JSONDecodeError, ValueError) as exc:
        print(f"greenfield replay failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
