"""Run the Strands-backed Greenfield flow through gated Step 8 handoff."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from greenfield.artifact_io import read_json_object, write_json_atomic
from greenfield.behavior_handbook import (
    BehaviorHandbookError,
    build_behavior_handbook,
    render_behavior_handbook_markdown,
    validate_behavior_handbook,
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
from greenfield.step6_contract import Step6Error, load_json, validate_step6_report
from greenfield.step6_patch import generate_step6
from greenfield.step7_contract import Step7Error, validate_step7_report
from greenfield.step7_prepare import prepare_step7
from greenfield.step7_profiles import Step7ProfileError, load_profile_registry
from greenfield.step7_runner import LocalSubprocessRunner
from greenfield.step7_validate import validate_step7
from greenfield.step8_contract import Step8Error, prepare_step8_request
from greenfield.step8_create import (
    NoWriteGitHubWriter,
    RejectingStep8Authorizer,
    create_step8,
)
from greenfield.strands_agent import generate_contract, run_strands_trace
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
        if row.get("enabled") is False:
            continue
        analysis = row.get("greenfield_analysis")
        # A declared test role only nominates a repository.  It is not evidence
        # of impact and is never auto-collected as coverage inventory.
        if (
            not isinstance(analysis, dict)
            or analysis.get("role") != "test"
            or not analysis.get("discovery_eligible")
        ):
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


def _blocked_handoff(
    *,
    analysis_kind: str,
    status: str,
    reason: str,
    required_inputs: list[str],
    details: list[str] | None = None,
) -> dict[str, object]:
    artifact: dict[str, object] = {
        "schema_version": "0.1",
        "analysis_kind": analysis_kind,
        "status": status,
        "reason": reason,
        "required_inputs": required_inputs,
        "pr_eligible": False,
        "provenance": {
            "read_only": True,
            "github_writes": "none",
            "catalog_mutation": "none",
        },
    }
    if details:
        artifact["details"] = details
    return artifact


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
    parser.add_argument("--model")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--max-file-bytes", type=int, default=120_000)
    parser.add_argument("--ci-evidence", action="append", default=[])
    parser.add_argument(
        "--contract",
        action="append",
        default=[],
        help="Additional Step 2/4 contract artifacts to evaluate alongside generated Step 1.5 contract evidence.",
    )
    parser.add_argument("--inventory-evidence", action="append", default=[])
    parser.add_argument("--semantic-index", action="append", default=[])
    parser.add_argument("--related-pr-evidence")
    parser.add_argument("--repository", action="append", default=[])
    parser.add_argument("--step6-request", type=Path)
    parser.add_argument("--strict-target-evidence", action="store_true")
    parser.add_argument("--require-owner-approvals", action="store_true")
    parser.add_argument("--step7-eligible", action="store_true")
    parser.add_argument("--step7-profiles", type=Path)
    parser.add_argument("--step7-runner", choices=("local",), default="local")
    parser.add_argument("--target-checkout", type=Path)
    parser.add_argument("--step8-base-branch")
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
            "request",
            inputs={"step1": step1_path},
            outputs={"request": request_path, "identity": identity_path},
        )
        current_stage = "step1_5"
        trace, context = run_strands_trace(
            step1,
            args.source_root,
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
            "repository_context",
            inputs={"request": request_path},
            outputs={"context": context_path},
        )
        current_stage = "impact_discovery"
        discovery_path = args.output_dir / "impact-discovery.json"
        discovery = discover_from_trace(
            request=request, trace=trace, contract=read_json_object(contract_path)
        )
        discovery_errors = validate_discovery(discovery)
        if discovery_errors:
            raise ValueError("invalid impact discovery: " + "; ".join(discovery_errors))
        write_json_atomic(discovery_path, discovery)
        handoff.complete_stage(
            "impact_discovery",
            inputs={
                "request": request_path,
                "trace": trace_path,
                "contract": contract_path,
                "context": context_path,
            },
            outputs={"discovery": discovery_path},
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
                f"inventory_{index:02d}": path for index, path in enumerate(inventories)
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
        for value in args.contract:
            step2_args.extend(["--contract", value])
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
                    f"contract_{index:02d}": path
                    for index, path in enumerate(args.contract)
                },
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
        for value in args.contract:
            step4_args.extend(["--contract", value])
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
                    f"contract_{index:02d}": path
                    for index, path in enumerate(args.contract)
                },
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
        current_stage = "behavior_handbook"
        handbook = build_behavior_handbook(
            read_json_object(contract_path),
            read_json_object(step2_path),
            read_json_object(step3_path),
            read_json_object(step4_path),
            read_json_object(step5_path),
        )
        handbook_errors = validate_behavior_handbook(handbook)
        if handbook_errors:
            raise BehaviorHandbookError(
                "generated invalid behavior handbook: " + "; ".join(handbook_errors)
            )
        handbook_path = args.output_dir / "behavior-handbook.json"
        handbook_markdown_path = args.output_dir / "behavior-handbook.md"
        write_json_atomic(handbook_path, handbook)
        handbook_markdown_path.write_text(
            render_behavior_handbook_markdown(handbook), encoding="utf-8"
        )
        handoff.complete_stage(
            "behavior_handbook",
            inputs={
                "contract": contract_path,
                "step2": step2_path,
                "step3": step3_path,
                "step4": step4_path,
                "step5": step5_path,
            },
            outputs={
                "handbook": handbook_path,
                "markdown": handbook_markdown_path,
            },
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
            "test_assessment",
            inputs={"discovery": discovery_path, "step4": step4_path},
            outputs={"assessment": assessment_path},
        )
        current_stage = "test_proposal"
        proposal = {
            "schema_version": "0.1",
            "analysis_kind": "greenfield_pr_test_proposal",
            "status": "partial",
            "input": {
                "source_repository": request["source_repository"],
                "source_revision": request["head_revision"],
                "changed_paths": request["changed_paths"],
            },
            "proposals": [],
            "findings": [
                "no qualified test repository and revision-bound test evidence"
            ],
            "provenance": {
                "read_only": True,
                "catalog_mutation": "none",
                "github_writes": "none",
            },
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
        review = render_review(
            request=request,
            discovery=discovery,
            assessment=assessment,
            ci_evidence=[read_json_object(path) for path in args.ci_evidence],
            contexts=[repository_context],
        )
        review_errors = validate_review(review)
        if review_errors:
            raise ValueError("invalid PR review: " + "; ".join(review_errors))
        write_json_atomic(review_path, review)
        review_markdown_path = args.output_dir / "pr-review.md"
        review_markdown_path.write_text(str(review["markdown"]), encoding="utf-8")
        handoff.complete_stage(
            "pr_review",
            inputs={
                "request": request_path,
                "discovery": discovery_path,
                "assessment": assessment_path,
                "context": context_path,
            },
            outputs={"review": review_path, "markdown": review_markdown_path},
        )
        step3_report = read_json_object(step3_path)
        step4_report = read_json_object(step4_path)
        step5_report = read_json_object(step5_path)
        if args.step7_eligible and not (
            args.strict_target_evidence and args.require_owner_approvals
        ):
            raise ValueError(
                "--step7-eligible requires --strict-target-evidence and "
                "--require-owner-approvals"
            )

        step6_report: dict[str, Any] | None = None
        step6_artifact_path: Path
        step6_artifact_name: str
        if args.step6_request is None:
            current_stage = "step6_handoff"
            step6_artifact = _step6_handoff()
            step6_artifact_path = args.output_dir / "step6.handoff.json"
            step6_artifact_name = "step6_handoff"
            write_json_atomic(step6_artifact_path, step6_artifact)
            handoff.complete_stage(
                "step6_handoff",
                inputs={
                    "step3": step3_path,
                    "step4": step4_path,
                    "step5": step5_path,
                    "test_proposal": proposal_path,
                    "pr_review": review_path,
                },
                outputs={step6_artifact_name: step6_artifact_path},
            )
        else:
            current_stage = "step6"
            step6_request = load_json(args.step6_request, "Step 6 request")
            if args.step7_eligible:
                step6_request["_step7_eligibility"] = True
            step6_report = generate_step6(
                step6_request,
                step1,
                step3_report,
                step4_report,
                step5_report,
                strict_target_evidence=args.strict_target_evidence,
                require_approvals=args.require_owner_approvals,
            )
            step6_errors = validate_step6_report(
                step6_report,
                strict_target_evidence=args.strict_target_evidence,
                require_approvals=args.require_owner_approvals,
                require_step7_eligibility=args.step7_eligible,
            )
            if step6_errors:
                raise Step6Error(
                    "generated invalid Step 6 report: " + "; ".join(step6_errors)
                )
            step6_artifact_path = args.output_dir / "step6.report.json"
            step6_artifact_name = "step6"
            write_json_atomic(step6_artifact_path, step6_report)
            handoff.complete_stage(
                "step6",
                inputs={
                    "request": args.step6_request,
                    "step1": step1_path,
                    "step3": step3_path,
                    "step4": step4_path,
                    "step5": step5_path,
                },
                outputs={step6_artifact_name: step6_artifact_path},
            )

        step7_report: dict[str, Any] | None = None
        step7_handoff: dict[str, object] | None = None
        step7_handoff_path: Path | None = None
        step7_request_path: Path | None = None
        step7_report_path: Path | None = None
        current_stage = "step7_handoff"
        if step6_report is None:
            step7_handoff = _blocked_handoff(
                analysis_kind="greenfield_pr_impact_step_7_handoff",
                status="blocked",
                reason="step6_report_not_supplied",
                required_inputs=["valid strict Step 6 report"],
            )
        else:
            strict_step6_errors = validate_step6_report(
                step6_report,
                strict_target_evidence=True,
                require_approvals=True,
                require_step7_eligibility=True,
            )
            if strict_step6_errors:
                step7_handoff = _blocked_handoff(
                    analysis_kind="greenfield_pr_impact_step_7_handoff",
                    status="blocked",
                    reason="step6_strict_evidence_unavailable",
                    required_inputs=[
                        "exact target evidence",
                        "source interface owner approval",
                        "consumer test owner approval",
                        "Step 6 eligibility_profile=step7",
                    ],
                    details=strict_step6_errors,
                )
            elif args.step7_profiles is None:
                step7_handoff = _blocked_handoff(
                    analysis_kind="greenfield_pr_impact_step_7_handoff",
                    status="unavailable",
                    reason="step7_profiles_not_supplied",
                    required_inputs=["central Step 7 validation profile registry"],
                )
            else:
                registry = load_profile_registry(args.step7_profiles)
                prepared = prepare_step7(step6_report, registry)
                if prepared.get("analysis_kind") != "greenfield_pr_impact_step_7_request":
                    step7_handoff = _blocked_handoff(
                        analysis_kind="greenfield_pr_impact_step_7_handoff",
                        status="blocked",
                        reason="step7_profile_unavailable",
                        required_inputs=["enabled Step 7 validation profile"],
                        details=[str(row) for row in prepared.get("failures", [])],
                    )
                    step7_request_path = args.output_dir / "step7.blocked.json"
                    write_json_atomic(step7_request_path, prepared)
                elif args.target_checkout is None:
                    step7_handoff = _blocked_handoff(
                        analysis_kind="greenfield_pr_impact_step_7_handoff",
                        status="unavailable",
                        reason="target_checkout_not_supplied",
                        required_inputs=[
                            "clean target checkout at Step 6 target base revision"
                        ],
                    )
                    step7_request_path = args.output_dir / "step7.request.json"
                    write_json_atomic(step7_request_path, prepared)
                else:
                    current_stage = "step7"
                    step7_request_path = args.output_dir / "step7.request.json"
                    write_json_atomic(step7_request_path, prepared)
                    step7_report = validate_step7(
                        step6_report,
                        prepared,
                        args.target_checkout,
                        profile_registry=registry,
                        runner=LocalSubprocessRunner(),
                    )
                    step7_errors = validate_step7_report(step7_report)
                    if step7_errors:
                        raise Step7Error(
                            "generated invalid Step 7 report: "
                            + "; ".join(step7_errors)
                        )
                    step7_report_path = args.output_dir / "step7.report.json"
                    write_json_atomic(step7_report_path, step7_report)
                    handoff.complete_stage(
                        "step7",
                        inputs={
                            "step6": step6_artifact_path,
                            "profiles": args.step7_profiles,
                            "target_checkout": args.target_checkout,
                        },
                        outputs={
                            "request": step7_request_path,
                            "report": step7_report_path,
                        },
                    )
        if step7_handoff is not None:
            step7_handoff_path = args.output_dir / "step7.handoff.json"
            write_json_atomic(step7_handoff_path, step7_handoff)
            outputs: dict[str, Path] = {"handoff": step7_handoff_path}
            if step7_request_path is not None:
                outputs["request_or_blocked"] = step7_request_path
            handoff.complete_stage(
                "step7_handoff",
                inputs={"step6": step6_artifact_path},
                outputs=outputs,
            )

        step8_result: dict[str, Any] | None = None
        step8_handoff: dict[str, object] | None = None
        step8_request_path: Path | None = None
        step8_result_path: Path | None = None
        current_stage = "step8_handoff"
        if step7_report is None:
            step8_handoff = _blocked_handoff(
                analysis_kind="greenfield_pr_impact_step_8_handoff",
                status="blocked",
                reason="step7_report_not_supplied",
                required_inputs=["validated Step 7 report from an approved runner"],
                details=[str(step7_handoff.get("reason"))]
                if step7_handoff is not None
                else None,
            )
        elif args.step8_base_branch is None:
            step8_handoff = _blocked_handoff(
                analysis_kind="greenfield_pr_impact_step_8_handoff",
                status="unavailable",
                reason="step8_base_branch_not_supplied",
                required_inputs=["target repository base branch name"],
            )
        else:
            current_stage = "step8_preparation"
            step8_request = prepare_step8_request(
                step3_report,
                step4_report,
                step6_report,
                step7_report,
                base_branch=args.step8_base_branch,
            )
            step8_result = create_step8(
                step3_report,
                step4_report,
                step6_report,
                step7_report,
                base_branch=args.step8_base_branch,
                authorizer=RejectingStep8Authorizer(),
                github=NoWriteGitHubWriter(),
            )
            step8_request_path = args.output_dir / "step8.request.json"
            step8_result_path = args.output_dir / "step8.blocked.json"
            write_json_atomic(step8_request_path, step8_request)
            write_json_atomic(step8_result_path, step8_result)
            handoff.complete_stage(
                "step8_preparation",
                inputs={
                    "step3": step3_path,
                    "step4": step4_path,
                    "step6": step6_artifact_path,
                    "step7": step7_report_path,
                },
                outputs={"request": step8_request_path, "result": step8_result_path},
            )
        if step8_handoff is not None:
            step8_handoff_path = args.output_dir / "step8.handoff.json"
            write_json_atomic(step8_handoff_path, step8_handoff)
            handoff.complete_stage(
                "step8_handoff",
                inputs={
                    "step6": step6_artifact_path,
                    **({"step7": step7_report_path} if step7_report_path else {}),
                },
                outputs={"handoff": step8_handoff_path},
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
            step7=step7_report,
            runtime_status=(
                str(step8_result.get("status"))
                if step8_result is not None
                else str(step8_handoff.get("status"))
                if step8_handoff is not None
                else None
            ),
            runtime_reason=(
                str(step8_result.get("authorization", {}).get("reason"))
                if step8_result is not None
                else str(step8_handoff.get("reason"))
                if step8_handoff is not None
                else None
            ),
        )
        artifact_paths = {
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
            "behavior_handbook": handbook_path,
            "behavior_handbook_markdown": handbook_markdown_path,
            "test_proposal": proposal_path,
            "test_assessment": assessment_path,
            "pr_review": review_path,
            "pr_review_markdown": review_markdown_path,
            step6_artifact_name: step6_artifact_path,
            "flow_handoff": handoff.path,
        }
        if step7_handoff_path is not None:
            artifact_paths["step7_handoff"] = step7_handoff_path
        if step7_request_path is not None:
            artifact_paths["step7_request"] = step7_request_path
        if step7_report_path is not None:
            artifact_paths["step7_report"] = step7_report_path
        if step8_request_path is not None:
            artifact_paths["step8_request"] = step8_request_path
        if step8_result_path is not None:
            artifact_paths["step8_result"] = step8_result_path
        if step8_handoff is not None:
            artifact_paths["step8_handoff"] = step8_handoff_path
        print(
            json.dumps(
                {
                    "status": "complete",
                    "context_sha256": context["context_sha256"],
                    "step6": {
                        "status": step6_report.get("status")
                        if step6_report is not None
                        else "unavailable",
                        "artifact": str(step6_artifact_path),
                    },
                    "step7": step7_report
                    if step7_report is not None
                    else step7_handoff,
                    "step8": step8_result
                    if step8_result is not None
                    else step8_handoff,
                    "validation": categories,
                    "artifacts": {
                        name: str(path) for name, path in artifact_paths.items()
                    },
                },
                sort_keys=True,
            )
        )
        return 0
    except (OSError, ValueError, TypeError, json.JSONDecodeError, RuntimeError) as exc:
        if handoff is not None:
            handoff.fail(current_stage, exc)
        print(f"greenfield Strands pipeline failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
