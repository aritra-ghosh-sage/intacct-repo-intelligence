"""Run the supported four-phase Greenfield Strands flow."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import shutil
import sys
from collections.abc import Mapping
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from greenfield.analysis_report import AnalysisReportError, build_analysis_report
from greenfield.artifact_io import read_json_object, write_json_atomic
from greenfield.behavior_impact_report import (
    BehaviorImpactReportError,
    build_behavior_impact_report,
    render_behavior_impact_report_markdown,
    validate_behavior_impact_report,
)
from greenfield.flow_handoff import GreenfieldFlowHandoff
from greenfield.github_repository_evidence import (
    RepositoryEvidenceError,
    collect_repository_evidence,
)
from greenfield.impact_discovery import discover_from_trace, validate_discovery
from greenfield.llm_env import (
    GreenfieldEnvError,
    load_greenfield_env,
    validate_greenfield_llm_env,
)
from greenfield.nexau_planner import NexAUPlannerError, run_nexau_planner
from greenfield.planning_contract import build_planning_report
from greenfield.pr_analysis_contract import make_request
from greenfield.pr_review import render_review, validate_review
from greenfield.publish import build_publication, publish_github
from greenfield.remediation import build_automatic_step6_request
from greenfield.replay_validation import validation_summary
from greenfield.repository_context import collect_repository_context
from greenfield.repository_handbook import resynchronize_repository_handbook_at_revision
from greenfield.run_context import build_run_context
from greenfield.step2_contract import normalize_repository_inventory
from greenfield.step6_contract import Step6Error, load_json, validate_step6_report
from greenfield.step6_patch import generate_step6
from greenfield.step7_contract import Step7Error, validate_step7_report
from greenfield.step7_prepare import prepare_step7
from greenfield.step7_profiles import load_profile_registry
from greenfield.step7_runner import LocalSubprocessRunner
from greenfield.step7_validate import create_ephemeral_revision, validate_step7
from greenfield.step8_contract import Step8Error, prepare_step8_request
from greenfield.step8_create import (
    GhApiWriter,
    NoWriteGitHubWriter,
    RejectingStep8Authorizer,
    ValidatedDraftAuthorizer,
    create_step8,
)
from greenfield.strands_agent import (
    Step1TraceFailure,
    generate_contract,
    run_strands_trace,
)
from greenfield.strands_config import apply_strands_environment, load_strands_config
from greenfield.strands_tools import GreenfieldToolbox
from greenfield.telemetry import GreenfieldTelemetry, redact
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


def _repository_handbooks(values: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        repository, separator, path = value.partition("=")
        if not separator or not repository.strip() or not path.strip():
            raise ValueError("--repository-handbook must use repository=/path syntax")
        if repository in result:
            raise ValueError(f"duplicate repository handbook: {repository}")
        result[repository] = Path(path)
    return result


def _manifest_handbook_values(path: Path) -> list[str]:
    """Read optional revision-bound handbook paths from central repository metadata."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    values: list[str] = []
    for row in data.get("repositories", []) if isinstance(data, dict) else []:
        if not isinstance(row, Mapping):
            continue
        analysis = row.get("greenfield_analysis")
        handbook = (
            analysis.get("repository_handbook")
            if isinstance(analysis, Mapping)
            else None
        )
        if isinstance(handbook, str) and handbook.strip():
            remote = str(row.get("remote_url") or "")
            repository = remote.split("github.com:", 1)[-1].split("github.com/", 1)[-1]
            values.append(f"{repository.removesuffix('.git')}={handbook}")
    return values


def _captured_candidate(
    run_context: dict[str, Any], repository: str
) -> dict[str, Any] | None:
    return next(
        (
            dict(row)
            for row in run_context.get("candidate_repositories", [])
            if isinstance(row, dict) and row.get("repository") == repository
        ),
        None,
    )


def _resolve_llm_runtime(
    *,
    cli_model: str | None,
    strands_config: Any,
    planner_config: dict[str, Any],
) -> tuple[str, str | None]:
    model = (
        cli_model or strands_config.model or os.environ.get("LLM_MODEL") or ""
    ).strip()
    if not model:
        raise ValueError(
            "Greenfield LLM model is not configured; supply --model, set model in the Greenfield config, or export LLM_MODEL"
        )
    base_url = (
        str(
            planner_config.get("base_url")
            or strands_config.base_url
            or os.environ.get("LLM_BASE_URL")
            or ""
        ).strip()
        or None
    )
    if not base_url:
        raise ValueError(
            "NexAU is enabled but no base URL is configured; set base_url in the planner config or export LLM_BASE_URL"
        )
    return model, base_url


def _execution_context(
    *, dry_run: bool, planner_mode: str, model: str, base_url: str | None
) -> dict[str, Any]:
    context: dict[str, Any] = {
        "dry_run": dry_run,
        "planner_mode": planner_mode,
        "model": model,
    }
    if base_url is not None:
        context["base_url"] = base_url
    return context


def _capability_preflight(
    *,
    model: str,
    base_url: str | None,
    env_path: Path,
    planner_config: Mapping[str, Any],
) -> dict[str, Any]:
    diagnostics: list[dict[str, str]] = []
    optional_diagnostics: list[dict[str, str]] = []
    try:
        validate_greenfield_llm_env(
            model=model or None, base_url=base_url, env_path=env_path
        )
    except GreenfieldEnvError as exc:
        diagnostics.append(
            {
                "component": "llm",
                "code": "configuration_unavailable",
                "message": redact(str(exc)),
            }
        )
    if not isinstance(planner_config, Mapping):
        diagnostics.append(
            {
                "component": "planner_config",
                "code": "invalid",
                "message": "planner config must be an object",
            }
        )
    try:
        import nexau  # noqa: F401
    except ImportError:
        diagnostics.append(
            {
                "component": "nexau",
                "code": "dependency_unavailable",
                "message": (
                    "pinned NexAU dependency is unavailable; run Greenfield with "
                    "./.venv-greenfield/bin/python after installing the "
                    "nexau-planner extra"
                ),
            }
        )
    try:
        import strands  # noqa: F401
    except ImportError:
        diagnostics.append(
            {
                "component": "strands",
                "code": "dependency_unavailable",
                "message": "Strands dependency is unavailable",
            }
        )
    try:
        e2b_available = importlib.util.find_spec("e2b") is not None
    except (ImportError, ValueError):
        e2b_available = False
    if not e2b_available:
        optional_diagnostics.append(
            {
                "component": "e2b",
                "code": "optional_dependency_unavailable",
                "message": "E2B is unavailable; it is required only by sandbox-backed validation profiles",
            }
        )
    packages: dict[str, str | None] = {}
    for package in ("strands-agents", "nexau"):
        try:
            packages[package] = version(package)
        except PackageNotFoundError:
            packages[package] = None
    return {
        "status": "ready" if not diagnostics else "unavailable",
        "nexau": "ready"
        if not any(row["component"] == "nexau" for row in diagnostics)
        else "unavailable",
        "strands": "ready"
        if not any(row["component"] == "strands" for row in diagnostics)
        else "unavailable",
        "optional_capabilities": {"e2b": "ready" if e2b_available else "unavailable"},
        "runtime": {
            "python_executable": sys.executable,
            "python_prefix": sys.prefix,
            "python_version": platform.python_version(),
            "packages": packages,
        },
        "diagnostics": diagnostics,
        "optional_diagnostics": optional_diagnostics,
    }


def _downgrade_incomplete_analysis(
    value: Mapping[str, Any], *, reason: str
) -> dict[str, Any]:
    """Prevent partial planner/Strands lifecycles from retaining draft claims."""
    result = json.loads(json.dumps(value))
    for row in result.get("repository_impacts", []):
        if isinstance(row, dict) and row.get("evidence_state") in {
            "confirmed",
            "strong_candidate",
        }:
            row["evidence_state"] = "candidate"
            row.pop("challenge_task_id", None)
    for row in result.get("actions", []):
        if isinstance(row, dict):
            row["draft_eligible"] = False
            if row.get("evidence_state") in {"confirmed", "strong_candidate"}:
                row["evidence_state"] = "candidate"
            row.pop("challenge_task_id", None)
    gaps = result.setdefault("gaps", [])
    if reason not in gaps:
        gaps.append(reason)
    result["agent"] = {**result.get("agent", {}), "status": "partial", "reason": reason}
    return result


def _require_draft_step8_success(step8_result: Mapping[str, Any] | None) -> None:
    """Fail closed unless Step 8 created or recovered the draft PR."""
    if not isinstance(step8_result, Mapping) or step8_result.get("status") not in {
        "created",
        "reused",
    }:
        raise RuntimeError("draft blocked: Step 8 did not create or reuse the draft PR")


def _step6_handoff(
    *,
    reason: str = "step6_request_and_target_evidence_not_supplied",
    details: list[str] | None = None,
) -> dict[str, object]:
    artifact: dict[str, object] = {
        "schema_version": "0.1",
        "analysis_kind": "greenfield_pr_impact_step_6_handoff",
        "status": "unavailable",
        "reason": reason,
        "required_inputs": [
            "evidence-backed remediation action",
            "exact target repository and base revision",
            "target-evidence package bound to that revision",
            "bounded edit operations and validation profile",
        ],
        "inference_policy": "no_repository_revision_or_edit_inference",
        "invoked": False,
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
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--mode", choices=("analyze", "publish", "draft"), default="analyze"
    )
    args = parser.parse_args(argv)
    # Operator flags intentionally stop at the four public flow controls.  The
    # remaining values are centrally owned defaults used by internal stages.
    args.step1_report = None
    args.repo_key = "ia-main"
    args.manifest = (
        Path(__file__).resolve().parents[1] / "config" / "workspace_repos.yaml"
    )
    args.strands_config = None
    args.planner_config = None
    args.model = None
    args.dry_run = args.mode == "analyze"
    args.timeout = None
    args.max_file_bytes = 500_000
    args.ci_evidence = []
    args.contract = []
    args.inventory_evidence = []
    args.semantic_index = []
    args.related_pr_evidence = None
    args.repository = []
    args.repository_handbook = []
    args.step6_request = None
    args.strict_target_evidence = args.mode == "draft"
    args.step7_eligible = args.mode == "draft"
    args.step7_profiles = (
        Path(__file__).resolve().parents[1]
        / "config"
        / "greenfield_step7_profiles.yaml"
    )
    args.step7_runner = "local"
    args.target_checkout = None
    args.step8_base_branch = None
    args.create_draft_pr = args.mode == "draft"
    args.publish_github = args.mode in {"publish", "draft"}
    args.repository_handbook = _manifest_handbook_values(args.manifest)
    handoff: GreenfieldFlowHandoff | None = None
    current_stage = "initialization"
    try:
        env_path = load_greenfield_env()
        strands_config = load_strands_config(args.strands_config)
        apply_strands_environment(strands_config)
        planner_config = {}
        model = (strands_config.model or os.environ.get("LLM_MODEL") or "").strip()
        base_url = (
            strands_config.base_url or os.environ.get("LLM_BASE_URL") or ""
        ).strip() or None
        dry_run = args.dry_run or not (args.publish_github or args.create_draft_pr)
        timeout = args.timeout or strands_config.timeout_seconds
        args.output_dir.mkdir(parents=True, exist_ok=True)
        telemetry = GreenfieldTelemetry(args.output_dir)
        preflight = _capability_preflight(
            model=model,
            base_url=base_url,
            env_path=env_path,
            planner_config=planner_config,
        )
        telemetry.emit("capability_preflight", mode=args.mode, **preflight)
        if args.mode == "draft" and preflight["status"] != "ready":
            print(
                json.dumps(
                    {
                        "status": "blocked",
                        "reason": "capability_preflight_unavailable",
                        "diagnostics": preflight["diagnostics"],
                    },
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
            return 2
        args.planner_mode = "default"
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
        current_stage = "capture"
        run_context = build_run_context(
            step1,
            args.manifest,
            source_root=args.source_root,
            evidence_artifacts=[
                *args.contract,
                *args.ci_evidence,
                *args.inventory_evidence,
                *args.semantic_index,
                *([args.related_pr_evidence] if args.related_pr_evidence else []),
            ],
            contract_artifacts=args.contract,
            repository_handbooks=_repository_handbooks(args.repository_handbook),
            tool_limits={"max_file_bytes": args.max_file_bytes},
            execution=_execution_context(
                dry_run=dry_run,
                planner_mode=args.planner_mode,
                model=model,
                base_url=base_url,
            ),
        )
        run_context_path = args.output_dir / "run-context.json"
        write_json_atomic(run_context_path, run_context)
        handoff.complete_stage(
            "capture",
            inputs={"step1": step1_path, "manifest": args.manifest},
            outputs={"run_context": run_context_path},
        )
        current_stage = "step1_5"
        trace_path = args.output_dir / "step1.5.trace.json"
        contract_path = args.output_dir / "step1.5.contract.json"
        diagnostic_path = args.output_dir / "step1.5.diagnostic.json"
        try:
            trace, context = run_strands_trace(
                step1,
                args.source_root,
                model=model,
                timeout=timeout,
                max_file_bytes=args.max_file_bytes,
                max_tokens=strands_config.max_tokens,
                max_continuations=strands_config.max_continuations,
                contract_path=contract_path,
                diagnostic_output=diagnostic_path,
            )
            write_json_atomic(trace_path, trace)
            write_json_atomic(
                contract_path, generate_contract(step1, trace, str(trace_path))
            )
        except Step1TraceFailure as exc:
            if handoff is not None:
                diagnostics = (
                    {"step1_5_diagnostic": diagnostic_path}
                    if diagnostic_path.exists()
                    else None
                )
                handoff.fail(
                    current_stage,
                    exc,
                    contract_path=contract_path,
                    diagnostics=diagnostics,
                )
            print(f"greenfield Strands pipeline failed: {exc}", file=sys.stderr)
            return 2
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
        step2_report = read_json_object(step2_path)
        step3_report = read_json_object(step3_path)
        step4_report = read_json_object(step4_path)
        step5_report = read_json_object(step5_path)
        current_stage = "analyze"
        compatibility_summary = {
            "step2_candidates": step2_report.get("candidates", []),
            "step3_repositories": step3_report.get(
                "potentially_affected_repositories", {}
            ),
            "step4_coverage": step4_report.get("coverage", {}),
            "step4_obligations": step4_report.get("obligations", {}),
            "step5_actions": step5_report.get("actions", []),
            "gaps": sorted(
                {
                    str(value)
                    for report in (
                        step2_report,
                        step3_report,
                        step4_report,
                        step5_report,
                    )
                    for value in report.get("gaps", [])
                }
            ),
        }
        toolbox = GreenfieldToolbox(run_context)
        compatibility_summary["candidate_screen"] = {
            "index": toolbox.list_candidate_repositories(),
            "repositories": [
                toolbox.repository_metadata(str(row["repository"]))
                for row in run_context["candidate_repositories"]
            ],
        }
        planning_report: dict[str, Any] | None = None
        planning_path: Path | None = None
        current_stage = "nexau_planning"
        telemetry.emit(
            "analysis_inputs",
            context_sha256=run_context["context_sha256"],
            summary=compatibility_summary,
        )
        try:
            planning_report = run_nexau_planner(
                run_context,
                compatibility_summary,
                toolbox,
                mode=args.planner_mode,
                config={
                    **planner_config,
                    "model": model,
                    **({"base_url": base_url} if base_url else {}),
                },
                model=model,
                timeout=timeout,
            )
            telemetry.emit(
                "nexau_plan_generated",
                status=planning_report.get("status"),
                planning_sha256=planning_report.get("planning_sha256"),
                tasks=[
                    cycle.get("task") for cycle in planning_report.get("cycles", [])
                ],
            )
        except NexAUPlannerError as exc:
            nexau_reason = redact(str(exc))
            planning_report = build_planning_report(
                run_context,
                mode="default",
                planner={
                    "name": "nexau",
                    "status": "unavailable",
                    "reason": nexau_reason,
                },
                cycles=[],
                status="unavailable",
                stop_reason="planner_runtime_unavailable",
                gaps=["nexau_planner_unavailable"],
                analysis={
                    "repository_impacts": [],
                    "actions": [],
                    "coverage": {},
                    "recommendation": "Review deterministic evidence; NexAU investigation is unavailable.",
                    "gaps": ["nexau_planner_unavailable"],
                    "agent": {
                        "name": "nexau",
                        "status": "unavailable",
                        "reason": nexau_reason,
                    },
                },
            )
            telemetry.emit("nexau_planner_unavailable", reason=nexau_reason)
        planning_path = args.output_dir / "planning-report.json"
        write_json_atomic(planning_path, planning_report)
        handoff.complete_stage(
            "nexau_planning",
            inputs={
                "run_context": run_context_path,
                "step2": step2_path,
                "step3": step3_path,
                "step4": step4_path,
                "step5": step5_path,
            },
            outputs={"planning_report": planning_path},
        )
        try:
            planned_analysis = (
                planning_report.get("analysis")
                if isinstance(planning_report, dict)
                and planning_report.get("status")
                in {"complete", "partial", "blocked", "unavailable"}
                else None
            )
            if (
                isinstance(planned_analysis, dict)
                and planning_report.get("status") in {"partial", "blocked"}
            ):
                planned_analysis = _downgrade_incomplete_analysis(
                    planned_analysis, reason="nexau_planner_incomplete"
                )
            if isinstance(planned_analysis, dict):
                analysis = build_analysis_report(
                    run_context,
                    step2=step2_report,
                    step3=step3_report,
                    step4=step4_report,
                    step5=step5_report,
                    agent_analysis=planned_analysis,
                    tool_calls=toolbox.ledger(),
                    planning=planning_report,
                    lifecycle_complete=planning_report.get("status") == "complete",
                    source_trace=trace,
                )
            else:
                raise AnalysisReportError(
                    "NexAU planning report did not contain analysis for status "
                    f"{planning_report.get('status')!r}"
                )
        except (AnalysisReportError, ValueError) as exc:
            analysis = build_analysis_report(
                run_context,
                step2=step2_report,
                step3=step3_report,
                step4=step4_report,
                step5=step5_report,
                agent_analysis={
                    "agent": {
                        "name": "nexau",
                        "status": "unavailable",
                        "reason": redact(str(exc)),
                    },
                    "gaps": ["nexau_planner_unavailable"],
                },
                tool_calls=toolbox.ledger(),
                planning=planning_report,
                lifecycle_complete=False,
                source_trace=trace,
            )
        analysis_path = args.output_dir / "analysis-report.json"
        write_json_atomic(analysis_path, analysis)
        telemetry.emit(
            "analysis_report_written",
            status=analysis.get("status"),
            report_sha256=analysis.get("report_sha256"),
            tool_call_count=len(analysis.get("tool_calls", [])),
        )
        handoff.complete_stage(
            "analyze",
            inputs={
                "run_context": run_context_path,
                "step2": step2_path,
                "step3": step3_path,
                "step4": step4_path,
                "step5": step5_path,
            },
            outputs={
                "analysis_report": analysis_path,
                "planning_report": planning_path,
            },
        )
        current_stage = "behavior_impact_report"
        handbook = build_behavior_impact_report(
            read_json_object(contract_path),
            read_json_object(step2_path),
            read_json_object(step3_path),
            read_json_object(step4_path),
            read_json_object(step5_path),
        )
        handbook_errors = validate_behavior_impact_report(handbook)
        if handbook_errors:
            raise BehaviorImpactReportError(
                "generated invalid behavior handbook: " + "; ".join(handbook_errors)
            )
        handbook_path = args.output_dir / "behavior-impact-report.json"
        handbook_markdown_path = args.output_dir / "behavior-impact-report.md"
        write_json_atomic(handbook_path, handbook)
        handbook_markdown_path.write_text(
            render_behavior_impact_report_markdown(handbook), encoding="utf-8"
        )
        handoff.complete_stage(
            "behavior_impact_report",
            inputs={
                "contract": contract_path,
                "step2": step2_path,
                "step3": step3_path,
                "step4": step4_path,
                "step5": step5_path,
            },
            outputs={
                "behavior_impact_report": handbook_path,
                "markdown": handbook_markdown_path,
            },
        )
        current_stage = "test_assessment"
        assessment_path = args.output_dir / "test-assessment.json"
        candidate_revisions = {
            str(row["repository"]): row.get("inspected_revision")
            for row in run_context["candidate_repositories"]
            if isinstance(row, dict)
        }
        assessment_candidates = [
            {
                "action_type": row.get("action_type"),
                "target_repository": row.get("target_repository"),
                "target_revision": row.get("target_revision")
                or candidate_revisions.get(str(row.get("target_repository"))),
                "scope": row.get("scope", {}),
                "evidence_state": row.get("evidence_state"),
            }
            for row in analysis.get("actions", [])
            if isinstance(row, dict)
            and row.get("action_type")
            in {"run_test_suite", "update_existing_test", "add_missing_test"}
            and (
                row.get("target_revision")
                or candidate_revisions.get(str(row.get("target_repository")))
            )
        ]
        assessment = build_assessment(
            repository=str(request["source_repository"]),
            revision=str(request["head_revision"]),
            candidates=assessment_candidates,
            evidence=[
                {
                    "kind": "artifact",
                    "artifact": "analysis-report.json",
                    "sha256": analysis["report_sha256"],
                }
            ],
            assessed=bool(assessment_candidates),
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
        proposal_rows = []
        for row in analysis.get("actions", []):
            if not isinstance(row, dict) or row.get("draft_eligible") is not True:
                continue
            scope = row.get("scope", {})
            paths = scope.get("allowed_paths", []) if isinstance(scope, dict) else []
            validation = (
                scope.get("validation_plan", []) if isinstance(scope, dict) else []
            )
            if not row.get("target_revision") or not paths or not validation:
                continue
            proposal_rows.append(
                {
                    "target_repository": row["target_repository"],
                    "target_base_revision": row["target_revision"],
                    "paths": paths,
                    "operation": "add"
                    if row["action_type"] == "add_missing_test"
                    else "update",
                    "test_area": scope.get("interface_id", "unknown"),
                    "rationale": row["rationale"],
                    "evidence": row["evidence"],
                    "validation_commands": validation,
                }
            )
        proposal = {
            "schema_version": "0.1",
            "analysis_kind": "greenfield_pr_test_proposal",
            "status": "complete" if proposal_rows else "partial",
            "input": {
                "source_repository": request["source_repository"],
                "source_revision": request["head_revision"],
                "changed_paths": request["changed_paths"],
            },
            "proposals": proposal_rows,
            "findings": []
            if proposal_rows
            else ["no remediation met the strong-candidate draft gate"],
            "provenance": {
                "read_only": True,
                "catalog_mutation": "none",
                "github_writes": "none",
            },
        }
        proposal_errors = validate_test_proposal(proposal)
        if proposal_errors:
            raise ValueError(
                "invalid Strands test proposal: " + "; ".join(proposal_errors)
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
            analysis=analysis,
            behavior_impact=handbook,
            step2=step2_report,
            step3=step3_report,
            step4=step4_report,
            step5=step5_report,
            planning=planning_report,
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
                "analysis_report": analysis_path,
                "behavior_impact_report": handbook_path,
                **({"planning_report": planning_path} if planning_path else {}),
            },
            outputs={"review": review_path, "markdown": review_markdown_path},
        )
        if args.step7_eligible and not args.strict_target_evidence:
            raise ValueError("--step7-eligible requires --strict-target-evidence")

        step6_report: dict[str, Any] | None = None
        step6_artifact_path: Path
        step6_artifact_name: str
        automatic_step6_request = None
        automatic_step6_error: str | None = None
        if args.step6_request is None:
            try:
                automatic_step6_request = build_automatic_step6_request(
                    analysis,
                    run_context,
                    step1,
                    step3_report,
                    step4_report,
                    step5_report,
                )
            except Step6Error as exc:
                automatic_step6_error = str(exc)
        if args.step6_request is None and automatic_step6_request is None:
            current_stage = "step6_handoff"
            step6_artifact = _step6_handoff(
                reason=(
                    "automatic_remediation_not_safe"
                    if automatic_step6_error
                    else "no_analysis_action_met_draft_gate"
                ),
                details=[automatic_step6_error] if automatic_step6_error else None,
            )
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
            step6_request = (
                load_json(args.step6_request, "Step 6 request")
                if args.step6_request is not None
                else automatic_step6_request
            )
            assert step6_request is not None
            if args.step7_eligible:
                step6_request["_step7_eligibility"] = True
            step6_report = generate_step6(
                step6_request,
                step1,
                step3_report,
                step4_report,
                step5_report,
                strict_target_evidence=args.strict_target_evidence,
            )
            step6_errors = validate_step6_report(
                step6_report,
                strict_target_evidence=args.strict_target_evidence,
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
                    "request": args.step6_request or analysis_path,
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
        handbook_resynchronization: dict[str, Any] = {
            "status": "not_applicable",
            "reason": "no_validated_remediation_diff",
        }
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
                require_step7_eligibility=True,
            )
            if strict_step6_errors:
                step7_handoff = _blocked_handoff(
                    analysis_kind="greenfield_pr_impact_step_7_handoff",
                    status="blocked",
                    reason="step6_strict_evidence_unavailable",
                    required_inputs=[
                        "exact target evidence",
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
                candidate = _captured_candidate(
                    run_context, str(step6_report["target"]["repository"])
                )
                target_checkout = args.target_checkout or (
                    Path(str(candidate["local_root"]))
                    if candidate
                    and candidate.get("inspected_revision")
                    == step6_report["target"]["base_revision"]
                    else None
                )
                if (
                    prepared.get("analysis_kind")
                    != "greenfield_pr_impact_step_7_request"
                ):
                    step7_handoff = _blocked_handoff(
                        analysis_kind="greenfield_pr_impact_step_7_handoff",
                        status="blocked",
                        reason="step7_profile_unavailable",
                        required_inputs=["enabled Step 7 validation profile"],
                        details=[str(row) for row in prepared.get("failures", [])],
                    )
                    step7_request_path = args.output_dir / "step7.blocked.json"
                    write_json_atomic(step7_request_path, prepared)
                elif target_checkout is None:
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
                        target_checkout,
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
                            "target_checkout": target_checkout,
                        },
                        outputs={
                            "request": step7_request_path,
                            "report": step7_report_path,
                        },
                    )
                    if (
                        args.mode != "analyze"
                        and step7_report.get("status") == "validated"
                    ):
                        patch_paths = [
                            str(row.get("path"))
                            for row in step6_report.get("patch", {}).get("files", [])
                            if isinstance(row, Mapping)
                        ]
                        if patch_paths:
                            target_repository = str(
                                step6_report["target"]["repository"]
                            )
                            captured_handbook = next(
                                (
                                    row
                                    for row in run_context.get(
                                        "repository_handbooks", []
                                    )
                                    if isinstance(row, Mapping)
                                    and row.get("repository") == target_repository
                                ),
                                None,
                            )
                            if not captured_handbook:
                                handbook_resynchronization = {
                                    "status": "unavailable",
                                    "reason": "handbook_resynchronization_unavailable",
                                    "repository": target_repository,
                                }
                            else:
                                ephemeral_revision, isolated_checkout = (
                                    create_ephemeral_revision(
                                        step6_report, target_checkout
                                    )
                                )
                                try:
                                    captured = read_json_object(
                                        Path(str(captured_handbook["path"]))
                                    )
                                    refreshed = (
                                        resynchronize_repository_handbook_at_revision(
                                            captured,
                                            isolated_checkout,
                                            revision=ephemeral_revision,
                                            changed_paths=patch_paths,
                                        )
                                    )
                                finally:
                                    shutil.rmtree(
                                        isolated_checkout.parent, ignore_errors=True
                                    )
                                resync_path = (
                                    args.output_dir
                                    / "repository-handbook-resynchronization.json"
                                )
                                write_json_atomic(resync_path, refreshed)
                                handbook_resynchronization = {
                                    "status": "complete",
                                    "repository": target_repository,
                                    "revision": ephemeral_revision,
                                    "artifact": str(resync_path),
                                    "handbook_sha256": refreshed["handbook_sha256"],
                                }
                                telemetry.emit(
                                    "handbook_resynchronized",
                                    **handbook_resynchronization,
                                )
                        else:
                            handbook_resynchronization = {
                                "status": "reused",
                                "reason": "empty_diff",
                            }
                    elif args.mode != "analyze" and step6_report is not None:
                        handbook_resynchronization = {
                            "status": "unavailable",
                            "reason": "handbook_resynchronization_unavailable",
                        }
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
                required_inputs=["validated Step 7 report from an attested runner"],
                details=[str(step7_handoff.get("reason"))]
                if step7_handoff is not None
                else None,
            )
        else:
            candidate = _captured_candidate(
                run_context, str(step6_report["target"]["repository"])
            )
            step8_base_branch = args.step8_base_branch or (
                str(candidate.get("tracked_branch"))
                if candidate and candidate.get("tracked_branch")
                else None
            )
        if step7_report is not None and step8_base_branch is None:
            step8_handoff = _blocked_handoff(
                analysis_kind="greenfield_pr_impact_step_8_handoff",
                status="unavailable",
                reason="step8_base_branch_not_supplied",
                required_inputs=["target repository base branch name"],
            )
        elif step7_report is not None:
            current_stage = "step8_preparation"
            try:
                step8_request = prepare_step8_request(
                    step3_report,
                    step4_report,
                    step6_report,
                    step7_report,
                    base_branch=step8_base_branch,
                )
                step8_result = create_step8(
                    step3_report,
                    step4_report,
                    step6_report,
                    step7_report,
                    base_branch=step8_base_branch,
                    authorizer=(
                        ValidatedDraftAuthorizer()
                        if args.create_draft_pr
                        else RejectingStep8Authorizer()
                    ),
                    github=(
                        GhApiWriter() if args.create_draft_pr else NoWriteGitHubWriter()
                    ),
                )
                step8_request_path = args.output_dir / "step8.request.json"
                step8_result_path = args.output_dir / (
                    "step8.result.json"
                    if args.create_draft_pr
                    else "step8.blocked.json"
                )
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
                    outputs={
                        "request": step8_request_path,
                        "result": step8_result_path,
                    },
                )
            except Step8Error as exc:
                step8_handoff = _blocked_handoff(
                    analysis_kind="greenfield_pr_impact_step_8_handoff",
                    status="blocked",
                    reason="step8_trust_boundary_unavailable",
                    required_inputs=["production-eligible Step 7 runner attestation"],
                    details=[str(exc)],
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
        if args.mode == "draft":
            if planning_report.get("status") != "complete":
                raise RuntimeError("draft blocked: NexAU planning is not complete")
            if step7_report is None or step7_report.get("status") != "validated":
                raise RuntimeError("draft blocked: Step 7 validation did not pass")
            _require_draft_step8_success(step8_result)
            if handbook_resynchronization.get("status") == "unavailable" and any(
                isinstance(row, Mapping)
                and row.get("repository") == str(step6_report["target"]["repository"])
                for row in run_context.get("repository_handbooks", [])
            ):
                raise RuntimeError(
                    "draft blocked: handbook resynchronization unavailable"
                )
        current_stage = "publish"
        publication = build_publication(
            analysis,
            artifact_bundle=str(args.output_dir),
            draft_pr=step8_result,
            validation=step7_report,
            review=review,
            planning=planning_report,
            handbook_resynchronization=handbook_resynchronization,
        )
        publication_path = args.output_dir / "publication.json"
        write_json_atomic(publication_path, publication)
        publication_result_path: Path | None = None
        if args.publish_github or args.create_draft_pr:
            publication_result = publish_github(publication, GhApiWriter())
            publication_result_path = args.output_dir / "publication.result.json"
            write_json_atomic(publication_result_path, publication_result)
        handoff.complete_stage(
            "publish",
            inputs={
                "analysis_report": analysis_path,
                "pr_review": review_path,
                **({"step7": step7_report_path} if step7_report_path else {}),
                **({"step8": step8_result_path} if step8_result_path else {}),
            },
            outputs={
                "publication": publication_path,
                **(
                    {"publication_result": publication_result_path}
                    if publication_result_path
                    else {}
                ),
            },
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
            "behavior_impact_report": handbook_path,
            "behavior_impact_report_markdown": handbook_markdown_path,
            "run_context": run_context_path,
            "analysis_report": analysis_path,
            "publication": publication_path,
            "test_proposal": proposal_path,
            "test_assessment": assessment_path,
            "pr_review": review_path,
            "pr_review_markdown": review_markdown_path,
            step6_artifact_name: step6_artifact_path,
            "flow_handoff": handoff.path,
            "telemetry": telemetry.path,
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
        if publication_result_path is not None:
            artifact_paths["publication_result"] = publication_result_path
        telemetry.emit(
            "run_complete",
            mode=args.mode,
            analysis_status=analysis.get("status"),
            planning_status=planning_report.get("status")
            if planning_report
            else "not_run",
            step7_status=step7_report.get("status") if step7_report else "not_run",
            handbook_resynchronization=handbook_resynchronization,
            artifacts={name: str(path) for name, path in artifact_paths.items()},
        )
        print(
            json.dumps(
                {
                    "status": analysis["status"],
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
