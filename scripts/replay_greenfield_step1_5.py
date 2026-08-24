#!/usr/bin/env python3
"""Replay a recorded greenfield Step 1-5 bundle locally."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from greenfield.replay_validation import validation_summary
from greenfield.step1_5_trace import validate_trace
from greenfield.step1_capture import evidence_fingerprint
from greenfield.step2_candidates import resolve_candidates
from greenfield.step2_contract import (
    artifact_sha256,
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


def _evidence_file_path(bundle_dir: Path, name: str) -> Path:
    """Resolve a bundle file independently of the process working directory."""

    repo_root = Path(__file__).resolve().parents[1]
    try:
        relative = bundle_dir.resolve().relative_to(repo_root)
        return repo_root / relative / name
    except ValueError:
        return bundle_dir / name


def _relative_evidence_path(bundle_dir: Path, name: str) -> str:
    return str(_evidence_path(bundle_dir, name))


def _compare(
    bundle_dir: Path, name: str, report: dict[str, object], errors: list[str]
) -> None:
    golden = bundle_dir / name
    if not golden.exists():
        errors.append(f"missing golden artifact: {golden}")
        return
    expected = _read_json(golden)
    if report != expected:
        errors.append(f"artifact mismatch: {name}")


def _relation_key(relation: Mapping[str, object]) -> tuple[object, ...]:
    return (
        relation.get("interface_id"),
        relation.get("consumer_repository"),
        relation.get("relationship_type"),
        tuple(sorted(relation.get("source_paths", [])))
        if isinstance(relation.get("source_paths"), list)
        else (),
    )


def _is_redundant_contract(
    generated: Mapping[str, object], declared: Mapping[str, object]
) -> bool:
    generated_relations = generated.get("relations", [])
    declared_relations = declared.get("relations", [])
    if not isinstance(generated_relations, list) or not isinstance(
        declared_relations, list
    ):
        return False
    generated_keys = {
        _relation_key(item) for item in generated_relations if isinstance(item, Mapping)
    }
    declared_keys = {
        _relation_key(item) for item in declared_relations if isinstance(item, Mapping)
    }
    return bool(declared_keys) and declared_keys <= generated_keys


def _load_contracts(
    bundle_dir: Path, step1: Mapping[str, object]
) -> tuple[list[dict[str, object]], list[str], list[tuple[str, dict[str, object]]]]:
    trace_path = bundle_dir / "step1.5.trace.json"
    standardized_path = bundle_dir / "step1.5.contract.json"
    legacy_path = _evidence_file_path(bundle_dir, "step2.contract.yaml")
    if trace_path.exists() != standardized_path.exists():
        raise ValueError(
            "Step 1.5 bundle must contain both step1.5.trace.json and step1.5.contract.json"
        )
    retained: list[tuple[str, dict[str, object]]] = []
    if standardized_path.exists():
        trace = _read_json(trace_path)
        trace_errors = validate_trace(step1, trace)
        if trace_errors:
            raise ValueError(
                "invalid retained Step 1.5 trace: " + "; ".join(trace_errors)
            )
        raw_contract = _read_json(standardized_path)
        generation = raw_contract.get("generation")
        if not isinstance(generation, Mapping):
            raise ValueError("retained Step 1.5 contract has no generation provenance")
        if generation.get("step1_evidence_sha256") != evidence_fingerprint(step1):
            raise ValueError(
                "retained Step 1.5 contract is not linked to Step 1 evidence"
            )
        if raw_contract.get("revision") != trace.get("revision"):
            raise ValueError("retained Step 1.5 contract revision does not match trace")
        if generation.get("source_trace_sha256") != artifact_sha256(trace):
            raise ValueError(
                "retained Step 1.5 contract trace fingerprint does not match trace"
            )
        generated = load_contract(standardized_path)
        generated.setdefault("evidence", {})["path"] = _relative_evidence_path(
            bundle_dir, "step1.5.contract.json"
        )
        contracts = [generated]
        contract_names = ["step1.5.contract.json"]
        retained.extend(
            [
                ("step1.5.trace.json", trace),
                ("step1.5.contract.json", raw_contract),
            ]
        )
        if legacy_path.exists():
            declared = load_contract(legacy_path)
            declared.setdefault("evidence", {})["path"] = _relative_evidence_path(
                bundle_dir, "step2.contract.yaml"
            )
            if not _is_redundant_contract(generated, declared):
                contracts.append(declared)
                contract_names.append("step2.contract.yaml")
        return contracts, contract_names, retained
    if not legacy_path.exists():
        raise ValueError("missing Step 2 contract evidence")
    legacy = load_contract(legacy_path)
    legacy.setdefault("evidence", {})["path"] = _relative_evidence_path(
        bundle_dir, "step2.contract.yaml"
    )
    return [legacy], ["step2.contract.yaml"], retained


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

        contracts, _contract_names, retained_artifacts = _load_contracts(
            bundle_dir, step1
        )
        ci = load_ci_evidence(_evidence_file_path(bundle_dir, "step2.ci.json"))
        ci["evidence"]["path"] = _relative_evidence_path(bundle_dir, "step2.ci.json")
        inventory = load_repository_inventory(
            _evidence_file_path(bundle_dir, "step2.inventory.json")
        )
        inventory["evidence_path"] = _relative_evidence_path(
            bundle_dir, "step2.inventory.json"
        )

        semantic_index = None
        semantic_path = bundle_dir / "step2.semantic-index.json"
        if semantic_path.exists():
            semantic_index = load_semantic_evidence(
                _evidence_file_path(bundle_dir, "step2.semantic-index.json")
            )
            semantic_index["evidence_path"] = _relative_evidence_path(
                bundle_dir, "step2.semantic-index.json"
            )

        related_pr_evidence = None
        related_path = bundle_dir / "step3.related-pr-evidence.json"
        if related_path.exists():
            related_pr_evidence = load_related_pr_evidence(
                _evidence_file_path(bundle_dir, "step3.related-pr-evidence.json")
            )
            related_pr_evidence["evidence_path"] = _relative_evidence_path(
                bundle_dir, "step3.related-pr-evidence.json"
            )

        step2 = resolve_candidates(
            step1,
            contracts=contracts,
            ci_evidence=[ci],
            inventory_evidence=[inventory],
            semantic_indexes=[semantic_index] if semantic_index else [],
        )
        step3 = assemble_outcome(
            step2,
            semantic_index=semantic_index,
            related_pr_evidence=related_pr_evidence,
        )
        ci_for_step4 = load_ci_evidence_file(
            _evidence_file_path(bundle_dir, "step2.ci.json")
        )
        ci_for_step4["evidence"]["path"] = _relative_evidence_path(
            bundle_dir, "step2.ci.json"
        )
        inventory_for_step4 = load_inventory_evidence(
            _evidence_file_path(bundle_dir, "step2.inventory.json")
        )
        inventory_for_step4["evidence_path"] = _relative_evidence_path(
            bundle_dir, "step2.inventory.json"
        )
        step4 = map_test_coverage(
            step3,
            contracts=contracts,
            ci_evidence=[ci_for_step4],
            inventory_evidence=[inventory_for_step4],
            semantic_indexes=[semantic_index] if semantic_index else [],
        )
        step5 = recommend_actions(step3, step4)

        errors: list[str] = []
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
                "categories": validation_summary(
                    artifact_integrity="passed" if not errors else "failed",
                    provenance_revision_consistency="passed"
                    if not errors
                    else "failed",
                    step3=step3,
                    step4=step4,
                    runtime_status="unavailable",
                    runtime_reason="step7_inputs_unavailable",
                ),
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
