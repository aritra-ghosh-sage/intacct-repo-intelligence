"""Run the Codex-backed Greenfield Step 1.5 through deterministic Step 5."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from greenfield.artifact_io import read_json_object, write_json_atomic
from greenfield.codex_agent import (
    generate_contract,
    run_codex_test_proposal,
    run_codex_trace,
)
from greenfield.github_repository_evidence import (
    RepositoryEvidenceError,
    collect_repository_evidence,
)
from greenfield.replay_validation import validation_summary
from greenfield.step2_contract import normalize_repository_inventory
from scripts import (
    trace_greenfield_step1,
    trace_greenfield_step2,
    trace_greenfield_step3,
    trace_greenfield_step4,
    trace_greenfield_step5,
)
from scripts.validate_greenfield_test_proposal import validate as validate_test_proposal


def _manifest_candidates(
    path: Path, source_repository: str, source_repo_key: str
) -> list[str]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    rows = data.get("repositories", []) if isinstance(data, dict) else []
    result: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        for relation in row.get("pr_impact_contracts", []):
            if not isinstance(relation, dict) or relation.get(
                "target_repository"
            ) not in {source_repository, source_repo_key}:
                continue
            remote = str(row.get("remote_url", ""))
            if "github.com:" in remote:
                remote = remote.split("github.com:", 1)[1]
            elif "github.com/" in remote:
                remote = remote.split("github.com/", 1)[1]
            if remote:
                result.add(remote.removesuffix(".git"))
    return sorted(result)


def _collect_inventories(
    step1: dict[str, object],
    manifest: Path,
    output_dir: Path,
    repositories: list[str],
    supplied: list[str],
) -> list[str]:
    if supplied:
        return supplied
    source = step1["input"]
    source_repository = str(
        source.get("repository") or source.get("canonical_repository")
    )
    source_repo_key = str(source.get("repo_key") or source.get("source_repo_key"))
    targets = repositories or _manifest_candidates(
        manifest, source_repository, source_repo_key
    )
    paths: list[str] = []
    for index, repository in enumerate(targets):
        try:
            evidence = normalize_repository_inventory(
                collect_repository_evidence(
                    repository,
                    source_repository=source_repo_key,
                    source_revision=str(
                        source.get("target_revision") or source.get("head_sha")
                    ),
                )
            )
        except RepositoryEvidenceError as exc:
            evidence = trace_greenfield_step2._unavailable_inventory(
                repository,
                source_repo_key,
                str(source.get("target_revision") or source.get("head_sha")),
                str(exc),
            )
        path = output_dir / f"inventory-{index:02d}.json"
        write_json_atomic(path, evidence)
        paths.append(str(path))
    return paths


def _run(main, argv: list[str]) -> None:
    result = main(argv)
    if result != 0:
        raise RuntimeError(f"stage failed: {main.__module__}")


def _step6_handoff() -> dict[str, object]:
    return {
        "schema_version": "0.1",
        "analysis_kind": "greenfield_pr_impact_step_6_handoff",
        "status": "unavailable",
        "reason": "step6_request_and_target_evidence_not_supplied",
        "required_inputs": [
            "explicit Step 6 request",
            "exact target repository and base revision",
            "target-evidence package bound to that revision",
            "required owner and approval evidence",
        ],
        "inference_policy": "no_repository_revision_owner_approval_or_edit_inference",
        "invoked": False,
        "pr_eligible": False,
        "provenance": {
            "read_only": True,
            "github_writes": "none",
            "catalog_mutation": "none",
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--step1-report", type=Path)
    source.add_argument("--pr", type=int)
    parser.add_argument("--repo-key", default="ia-main")
    parser.add_argument(
        "--manifest", type=Path, default=Path("config/workspace_repos.yaml")
    )
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--codex-binary", default="codex")
    parser.add_argument("--model")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--max-file-bytes", type=int, default=0)
    parser.add_argument("--ci-evidence", action="append", default=[])
    parser.add_argument("--inventory-evidence", action="append", default=[])
    parser.add_argument("--semantic-index", action="append", default=[])
    parser.add_argument("--related-pr-evidence")
    parser.add_argument("--repository", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        step1_path = args.step1_report or args.output_dir / "step1.json"
        if (
            args.pr is not None
            and trace_greenfield_step1.main(
                [
                    "--manifest",
                    str(args.manifest),
                    "--repo-key",
                    args.repo_key,
                    "--pr",
                    str(args.pr),
                    "--output",
                    str(step1_path),
                ]
            )
            != 0
        ):
            return 2
        step1 = read_json_object(step1_path)
        trace, context = run_codex_trace(
            step1,
            args.source_root,
            codex_binary=args.codex_binary,
            model=args.model,
            timeout=args.timeout,
            max_file_bytes=args.max_file_bytes,
        )
        trace_path = args.output_dir / "step1.5.trace.json"
        contract_path = args.output_dir / "step1.5.contract.json"
        write_json_atomic(trace_path, trace)
        write_json_atomic(
            contract_path, generate_contract(step1, trace, str(trace_path))
        )
        inventories = _collect_inventories(
            step1,
            args.manifest,
            args.output_dir,
            args.repository,
            args.inventory_evidence,
        )
        step2_path = args.output_dir / "step2.json"
        step2_args = [
            "--step1-report",
            str(step1_path),
            "--generated-contract",
            str(contract_path),
            "--output",
            str(step2_path),
            "--manifest",
            str(args.manifest),
            "--evidence-score",
        ]
        for value in args.repository:
            step2_args.extend(["--repository", value])
        for value in args.ci_evidence:
            step2_args.extend(["--ci-evidence", value])
        for value in inventories:
            step2_args.extend(["--inventory-evidence", value])
        for value in args.semantic_index:
            step2_args.extend(["--semantic-index", value])
        _run(trace_greenfield_step2.main, step2_args)
        step3_path = args.output_dir / "step3.json"
        step3_args = ["--step2-report", str(step2_path), "--output", str(step3_path)]
        if args.related_pr_evidence:
            step3_args.extend(["--related-pr-evidence", args.related_pr_evidence])
        _run(trace_greenfield_step3.main, step3_args)
        step4_path = args.output_dir / "step4.json"
        step4_args = [
            "--step3-report",
            str(step3_path),
            "--contract",
            str(contract_path),
            "--output",
            str(step4_path),
        ]
        for value in args.ci_evidence:
            step4_args.extend(["--ci-evidence", value])
        for value in inventories:
            step4_args.extend(["--inventory", value])
        for value in args.semantic_index:
            step4_args.extend(["--semantic-index", value])
        _run(trace_greenfield_step4.main, step4_args)
        step5_path = args.output_dir / "step5.json"
        _run(
            trace_greenfield_step5.main,
            [
                "--step3-report",
                str(step3_path),
                "--step4-report",
                str(step4_path),
                "--output",
                str(step5_path),
            ],
        )
        proposal = run_codex_test_proposal(
            step1,
            {
                "step2": read_json_object(step2_path),
                "step3": read_json_object(step3_path),
                "step4": read_json_object(step4_path),
                "step5": read_json_object(step5_path),
            },
            args.source_root,
            codex_binary=args.codex_binary,
            model=args.model,
            timeout=args.timeout,
        )
        proposal_errors = validate_test_proposal(proposal)
        if proposal_errors:
            raise ValueError(
                "invalid Codex test proposal: " + "; ".join(proposal_errors)
            )
        proposal_path = args.output_dir / "test-proposal.json"
        write_json_atomic(proposal_path, proposal)
        step3_report = read_json_object(step3_path)
        step4_report = read_json_object(step4_path)
        step6_handoff = _step6_handoff()
        step6_handoff_path = args.output_dir / "step6.handoff.json"
        write_json_atomic(step6_handoff_path, step6_handoff)
        categories = validation_summary(
            artifact_integrity="passed",
            provenance_revision_consistency="passed",
            step3=step3_report,
            step4=step4_report,
            runtime_status="unavailable",
            runtime_reason="step7_inputs_unavailable",
        )
        print(
            json.dumps(
                {
                    "status": "complete",
                    "context_sha256": context["context_sha256"],
                    "step6_handoff": step6_handoff,
                    "validation": categories,
                    "artifacts": {
                        name: str(path)
                        for name, path in {
                            "step1": step1_path,
                            "trace": trace_path,
                            "contract": contract_path,
                            "step2": step2_path,
                            "step3": step3_path,
                            "step4": step4_path,
                            "step5": step5_path,
                            "test_proposal": proposal_path,
                            "step6_handoff": step6_handoff_path,
                        }.items()
                    },
                },
                sort_keys=True,
            )
        )
        return 0
    except (OSError, ValueError, TypeError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"greenfield Codex pipeline failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
