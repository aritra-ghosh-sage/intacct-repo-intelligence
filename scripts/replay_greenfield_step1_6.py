"""Replay a recorded Greenfield Step 1-7 bundle locally."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from greenfield.artifact_io import artifact_sha256
from greenfield.replay_validation import validation_summary
from greenfield.step1_capture import evidence_fingerprint
from greenfield.step2_candidates import resolve_candidates
from greenfield.step2_contract import (
    load_ci_evidence,
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
from greenfield.step6_contract import (
    load_json,
    validate_step6_report,
    validate_step6_request,
)
from greenfield.step6_patch import generate_step6
from greenfield.step7_contract import validate_step7_report
from greenfield.step7_validate import validate_step7
from scripts.replay_greenfield_step1_5 import _load_contracts
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


def _load_contract_for_replay(
    bundle: Path, step1: dict[str, object]
) -> tuple[dict[str, object], str]:
    """Load the retained Step 1.5 contract and verify its paired trace."""
    contracts, names, _retained = _load_contracts(bundle, step1)
    if (bundle / "step1.5.contract.json").exists():
        return contracts[0], "step1.5.contract.json"
    if names:
        return contracts[0], names[0]
    if (bundle / "generated_behavior_contract.json").exists():
        raise ValueError(
            "legacy generated_behavior_contract.json is not a standardized Step 1.5 artifact"
        )
    raise ValueError("missing Step 2 contract evidence")


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
    parser.add_argument(
        "--target-checkout",
        type=Path,
        help="Optional exact target checkout for conditional Step 7 replay.",
    )
    args = parser.parse_args(argv)
    bundle = args.bundle_dir
    try:
        step1 = load_json(bundle / "step1.json", "step1 report")
        errors = validate_step1(step1)
        if errors:
            raise ValueError("invalid Step 1 report: " + "; ".join(errors))
        contracts, contract_names, retained_artifacts = _load_contracts(bundle, step1)
        contract_name = (
            "step1.5.contract.json"
            if (bundle / "step1.5.contract.json").exists()
            else contract_names[0]
        )
        ci_paths = sorted(bundle.glob("step2.ci*.json"))
        inventory_paths = sorted(bundle.glob("step2.inventory*.json"))
        if not ci_paths:
            raise ValueError("missing Step 2 CI evidence")
        if not inventory_paths:
            raise ValueError("missing Step 2 inventory evidence")
        ci_rows = [load_ci_evidence(path) for path in ci_paths]
        inventory_rows = [load_repository_inventory(path) for path in inventory_paths]
        for ci_row, ci_file in zip(ci_rows, ci_paths):
            _normalize_evidence_paths(
                contracts[0],
                ci_row,
                inventory_rows[0],
                bundle_dir=bundle,
                contract_name=(contract_name),
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
            semantic["evidence_path"] = _evidence_label(
                bundle, "step2.semantic-index.json"
            )
        related = None
        related_path = bundle / "step3.related-pr-evidence.json"
        if related_path.exists():
            related = load_related_pr_evidence(related_path)
            related["evidence_path"] = _evidence_label(
                bundle, "step3.related-pr-evidence.json"
            )

        step2 = resolve_candidates(
            step1,
            contracts=contracts,
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

        contract_evidence = []
        for contract, contract_name in zip(contracts, contract_names):
            evidence_contract = dict(contract)
            if isinstance(evidence_contract.get("evidence"), dict):
                evidence_contract["evidence"] = dict(evidence_contract["evidence"])
                evidence_contract["evidence"]["path"] = _evidence_label(
                    bundle, contract_name
                )
            contract_evidence.append(evidence_contract)
        ci_evidence = [load_ci_evidence_file(path) for path in ci_paths]
        for row, path in zip(ci_evidence, ci_paths):
            row["evidence"]["path"] = _evidence_label(bundle, path.name)
        inventory_evidence = [load_inventory_evidence(path) for path in inventory_paths]
        for row, path in zip(inventory_evidence, inventory_paths):
            row["evidence_path"] = _evidence_label(bundle, path.name)
        step4 = map_test_coverage(
            step3,
            contracts=contract_evidence,
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

        step7 = None
        step7_handoff: dict[str, object] = {
            "status": "unavailable",
            "reason": "step7_request_not_supplied",
            "pr_eligible": False,
        }
        step7_request_path = bundle / "step7.request.json"
        if step7_request_path.exists():
            if step6 is None:
                step7_handoff = {
                    "status": "blocked",
                    "reason": "step6_report_not_supplied",
                    "pr_eligible": False,
                }
            else:
                strict_errors = validate_step6_report(
                    step6,
                    strict_target_evidence=True,
                    require_approvals=True,
                    require_step7_eligibility=True,
                )
                if strict_errors:
                    step7_handoff = {
                        "status": "blocked",
                        "reason": "step6_strict_evidence_unavailable",
                        "details": strict_errors,
                        "pr_eligible": False,
                    }
                elif args.target_checkout is None:
                    step7_handoff = {
                        "status": "unavailable",
                        "reason": "target_checkout_not_supplied",
                        "pr_eligible": False,
                    }
                else:
                    step7_request = load_json(step7_request_path, "Step 7 request")
                    step7 = validate_step7(step6, step7_request, args.target_checkout)
                    step7_errors = validate_step7_report(step7)
                    if step7_errors:
                        raise ValueError(
                            "generated invalid Step 7 report: "
                            + "; ".join(step7_errors)
                        )
                    step7_handoff = {
                        "status": step7["status"],
                        "reason": "step7_replayed",
                        "pr_eligible": step7["pr_eligible"],
                    }

        mismatches: list[str] = []
        reports_to_compare = [
            ("step2.report.json", step2),
            ("step3.report.json", step3),
            ("step4.report.json", step4),
            ("step5.report.json", step5),
        ]
        for name, artifact in reversed(retained_artifacts):
            reports_to_compare.insert(0, (name, artifact))
        if step6 is not None:
            reports_to_compare.append(("step6.report.json", step6))
        if step7 is not None:
            reports_to_compare.append(("step7.report.json", step7))
        for name, report in reports_to_compare:
            _compare(bundle, name, report, mismatches)
        if args.output_dir:
            for name, report in reports_to_compare:
                _write_json(args.output_dir / name, report)
            _write_json(args.output_dir / "step7.handoff.json", step7_handoff)
        reports = {
            "step1.json": step1,
            "step2.report.json": step2,
            "step3.report.json": step3,
            "step4.report.json": step4,
            "step5.report.json": step5,
        }
        reports.update({name: artifact for name, artifact in retained_artifacts})
        if step6 is not None:
            reports["step6.report.json"] = step6
        if step7 is not None:
            reports["step7.report.json"] = step7
        categories = validation_summary(
            artifact_integrity="passed" if not mismatches else "failed",
            provenance_revision_consistency="passed" if not mismatches else "failed",
            step3=step3,
            step4=step4,
            step7=step7,
            runtime_status=step7_handoff["status"] if step7 is None else None,
            runtime_reason=str(step7_handoff.get("reason")),
        )
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
            "step7": step7_handoff,
            "validation": categories,
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
                "categories": categories,
            },
            "step7": step7_handoff,
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
