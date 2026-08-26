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
    run_codex_trace,
)
from greenfield.flow_handoff import GreenfieldFlowHandoff
from greenfield.github_repository_evidence import (
    RepositoryEvidenceError,
    collect_repository_evidence,
)
from greenfield.impact_discovery import discover_from_trace, validate_discovery
from greenfield.pr_analysis_contract import make_request
from greenfield.pr_review import render_review, validate_review
from greenfield.replay_validation import validation_summary
from greenfield.repository_context import collect_repository_context
from greenfield.step2_contract import normalize_repository_inventory
from greenfield.test_assessment import build_assessment, validate_assessment
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
        analysis = row.get("greenfield_analysis")
        # A declared test role only nominates a repository.  It is not evidence
        # of impact and is never auto-collected as coverage inventory.
        if not isinstance(analysis, dict) or analysis.get("role") != "test" or not analysis.get("discovery_eligible"):
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
    discovery: dict[str, object] | None = None,
) -> list[str]:
    if supplied:
        return supplied
    source = step1["input"]
    source_repo_key = str(source.get("repo_key") or source.get("source_repo_key"))
    # Generic manifest relationships are capability metadata only.  Read a
    # candidate inventory solely when an operator or confirmed discovery names it.
    discovered = []
    if isinstance(discovery, dict):
        discovered = [
            str(claim["repository"])
            for claim in discovery.get("claims", [])
            if isinstance(claim, dict)
            and claim.get("evidence_status") == "confirmed"
            and isinstance(claim.get("repository"), str)
            and claim.get("repository") != source.get("repository")
        ]
    targets = sorted(set(repositories) | set(discovered))
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
    parser.add_argument("--max-file-bytes", type=int, default=120_000)
    parser.add_argument("--ci-evidence", action="append", default=[])
    parser.add_argument("--inventory-evidence", action="append", default=[])
    parser.add_argument("--semantic-index", action="append", default=[])
    parser.add_argument("--related-pr-evidence")
    parser.add_argument("--repository", action="append", default=[])
    args = parser.parse_args(argv)
    handoff: GreenfieldFlowHandoff | None = None
    current_stage = "initialization"
    try:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        step1_path = args.step1_report or args.output_dir / "step1.json"
        current_stage = "step1"
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
        handoff = GreenfieldFlowHandoff(args.output_dir, source=step1["input"])
        handoff.complete_stage(
            "step1",
            inputs=(
                {"manifest": args.manifest}
                if args.pr is not None
                else {"step1_report": step1_path}
            ),
            outputs={"step1": step1_path},
        )
        current_stage = "request"
        request_path = args.output_dir / "pr-analysis.request.json"
        request = make_request(step1)
        write_json_atomic(request_path, request)
        identity_path = args.output_dir / "bundle.identity.json"
        write_json_atomic(
            identity_path,
            {
                "schema_version": "0.1",
                "source_repository": request["source_repository"],
                "pr_number": request.get("pr_number"),
                "base_revision": request["base_revision"],
                "head_revision": request["head_revision"],
                "bundle_key": f"{request['source_repository'].replace('/', '-')}-pr-{request.get('pr_number')}-{request['head_revision']}",
            },
        )
        handoff.complete_stage(
            "request", inputs={"step1": step1_path}, outputs={"request": request_path, "identity": identity_path}
        )
        current_stage = "step1_5"
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
        handoff.complete_stage(
            "step1_5",
            inputs={"step1": step1_path},
            outputs={"trace": trace_path, "contract": contract_path},
        )
        current_stage = "repository_context"
        context_path = args.output_dir / "repository-context.json"
        repository_context = collect_repository_context(
            repository=str(request["source_repository"]),
            revision=str(request["head_revision"]),
            changed_paths=list(request["changed_paths"]),
            local_root=args.source_root,
        )
        write_json_atomic(context_path, repository_context)
        handoff.complete_stage(
            "repository_context", inputs={"request": request_path}, outputs={"context": context_path}
        )
        current_stage = "impact_discovery"
        discovery_path = args.output_dir / "impact-discovery.json"
        discovery = discover_from_trace(request=request, trace=trace, contract=read_json_object(contract_path))
        discovery_errors = validate_discovery(discovery)
        if discovery_errors:
            raise ValueError("invalid impact discovery: " + "; ".join(discovery_errors))
        write_json_atomic(discovery_path, discovery)
        handoff.complete_stage(
            "impact_discovery", inputs={"request": request_path, "trace": trace_path, "contract": contract_path, "context": context_path}, outputs={"discovery": discovery_path}
        )
        current_stage = "inventory"
        inventories = _collect_inventories(
            step1,
            args.manifest,
            args.output_dir,
            args.repository,
            args.inventory_evidence,
            discovery,
        )
        handoff.complete_stage(
            "inventory",
            inputs={"step1": step1_path, "manifest": args.manifest},
            outputs={
                f"inventory_{index:02d}": path
                for index, path in enumerate(inventories)
            },
        )
        current_stage = "step2"
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
        handoff.complete_stage(
            "step2",
            inputs={
                "step1": step1_path,
                "contract": contract_path,
                "manifest": args.manifest,
                **{
                    f"inventory_{index:02d}": path
                    for index, path in enumerate(inventories)
                },
                **{
                    f"ci_{index:02d}": path
                    for index, path in enumerate(args.ci_evidence)
                },
                **{
                    f"semantic_index_{index:02d}": path
                    for index, path in enumerate(args.semantic_index)
                },
            },
            outputs={"step2": step2_path},
        )
        current_stage = "step3"
        step3_path = args.output_dir / "step3.json"
        step3_args = ["--step2-report", str(step2_path), "--output", str(step3_path)]
        if args.related_pr_evidence:
            step3_args.extend(["--related-pr-evidence", args.related_pr_evidence])
        _run(trace_greenfield_step3.main, step3_args)
        handoff.complete_stage(
            "step3",
            inputs={
                "step2": step2_path,
                **(
                    {"related_pr_evidence": args.related_pr_evidence}
                    if args.related_pr_evidence
                    else {}
                ),
            },
            outputs={"step3": step3_path},
        )
        current_stage = "step4"
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
        handoff.complete_stage(
            "step4",
            inputs={
                "step3": step3_path,
                "contract": contract_path,
                **{
                    f"inventory_{index:02d}": path
                    for index, path in enumerate(inventories)
                },
                **{
                    f"ci_{index:02d}": path
                    for index, path in enumerate(args.ci_evidence)
                },
                **{
                    f"semantic_index_{index:02d}": path
                    for index, path in enumerate(args.semantic_index)
                },
            },
            outputs={"step4": step4_path},
        )
        current_stage = "step5"
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
        handoff.complete_stage(
            "step5",
            inputs={"step3": step3_path, "step4": step4_path},
            outputs={"step5": step5_path},
        )
        current_stage = "test_assessment"
        assessment_path = args.output_dir / "test-assessment.json"
        # A source-only discovery has no qualified downstream test repository.
        assessment = build_assessment(
            repository=str(request["source_repository"]),
            revision=str(request["head_revision"]),
            candidates=[],
            evidence=[{"artifact": "impact-discovery.json"}],
            assessed=False,
        )
        assessment_errors = validate_assessment(assessment)
        if assessment_errors:
            raise ValueError("invalid test assessment: " + "; ".join(assessment_errors))
        write_json_atomic(assessment_path, assessment)
        handoff.complete_stage(
            "test_assessment", inputs={"discovery": discovery_path, "step4": step4_path}, outputs={"assessment": assessment_path}
        )
        current_stage = "test_proposal"
        proposal = {
            "schema_version": "0.1", "analysis_kind": "greenfield_pr_test_proposal", "status": "partial",
            "input": {"source_repository": request["source_repository"], "source_revision": request["head_revision"], "changed_paths": request["changed_paths"]},
            "proposals": [], "findings": ["no qualified test repository and revision-bound test evidence"],
            "provenance": {"read_only": True, "catalog_mutation": "none", "github_writes": "none"},
        }
        proposal_errors = validate_test_proposal(proposal)
        if proposal_errors:
            raise ValueError(
                "invalid Codex test proposal: " + "; ".join(proposal_errors)
            )
        proposal_path = args.output_dir / "test-proposal.json"
        write_json_atomic(proposal_path, proposal)
        handoff.complete_stage(
            "test_proposal",
            inputs={
                "step1": step1_path,
                "step2": step2_path,
                "step3": step3_path,
                "step4": step4_path,
                "step5": step5_path,
            },
            outputs={"test_proposal": proposal_path},
        )
        current_stage = "pr_review"
        review_path = args.output_dir / "pr-review.json"
        review = render_review(request=request, discovery=discovery, assessment=assessment, ci_evidence=[read_json_object(path) for path in args.ci_evidence], contexts=[repository_context])
        review_errors = validate_review(review)
        if review_errors:
            raise ValueError("invalid PR review: " + "; ".join(review_errors))
        write_json_atomic(review_path, review)
        review_markdown_path = args.output_dir / "pr-review.md"
        review_markdown_path.write_text(str(review["markdown"]), encoding="utf-8")
        handoff.complete_stage("pr_review", inputs={"request": request_path, "discovery": discovery_path, "assessment": assessment_path, "context": context_path}, outputs={"review": review_path, "markdown": review_markdown_path})
        current_stage = "step6_handoff"
        step3_report = read_json_object(step3_path)
        step4_report = read_json_object(step4_path)
        step6_handoff = _step6_handoff()
        step6_handoff_path = args.output_dir / "step6.handoff.json"
        write_json_atomic(step6_handoff_path, step6_handoff)
        handoff.complete_stage(
            "step6_handoff",
            inputs={
                "step3": step3_path,
                "step4": step4_path,
                "step5": step5_path,
                "test_proposal": proposal_path,
                "pr_review": review_path,
            },
            outputs={"step6_handoff": step6_handoff_path},
        )
        handoff.finish()
        # This small pointer is the sole mutable convenience artifact; all
        # evidence remains in the immutable, identity-bound bundle directory.
        write_json_atomic(
            args.output_dir.parent / "latest.json",
            {
                "bundle": str(args.output_dir.name),
                "bundle_identity": str(identity_path.name),
                "head_revision": request["head_revision"],
                "flow_handoff_sha256": handoff._reference(handoff.path)["sha256"],
            },
        )
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
                            "request": request_path,
                            "trace": trace_path,
                            "contract": contract_path,
                            "repository_context": context_path,
                            "impact_discovery": discovery_path,
                            "step2": step2_path,
                            "step3": step3_path,
                            "step4": step4_path,
                            "step5": step5_path,
                            "test_proposal": proposal_path,
                            "test_assessment": assessment_path,
                            "pr_review": review_path,
                            "pr_review_markdown": review_markdown_path,
                            "step6_handoff": step6_handoff_path,
                            "flow_handoff": handoff.path,
                        }.items()
                    },
                },
                sort_keys=True,
            )
        )
        return 0
    except (OSError, ValueError, TypeError, json.JSONDecodeError, RuntimeError) as exc:
        if handoff is not None:
            handoff.fail(current_stage, exc)
        print(f"greenfield Codex pipeline failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
