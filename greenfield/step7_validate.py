"""Read-only Greenfield Step 7 patch validation in an isolated checkout."""

from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from greenfield.step6_contract import validate_step6_report
from greenfield.step7_contract import (
    CHECK_CATEGORIES,
    REPORT_ANALYSIS_KIND,
    RULE_SET_VERSION,
    SCHEMA_VERSION,
    Step7Error,
    artifact_sha256,
    sha256_bytes,
    validate_step7_request,
)
from greenfield.step7_prepare import build_step7_request
from greenfield.step7_profiles import Step7ProfileError
from greenfield.step7_runner import Step7Runner


def _run(
    argv: list[str],
    *,
    cwd: Path,
    timeout: int,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        argv,
        cwd=cwd,
        capture_output=True,
        check=False,
        timeout=timeout,
        shell=False,
    )


def _git(
    checkout: Path, args: list[str], *, timeout: int = 120
) -> subprocess.CompletedProcess[bytes]:
    return _run(["git", *args], cwd=checkout, timeout=timeout)


def _text_output(value: bytes, limit: int) -> str:
    return value[:limit].decode("utf-8", errors="replace")


def _failure(
    code: str,
    *,
    phase: str,
    remediation: str,
    path: str | None = None,
    command: list[str] | None = None,
    expected: Any = None,
    observed: Any = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "code": code,
        "phase": phase,
        "remediation": remediation,
    }
    if path is not None:
        result["path"] = path
    if command is not None:
        result["command"] = command
    if expected is not None:
        result["expected"] = expected
    if observed is not None:
        result["observed"] = observed
    return result


def _empty_checks() -> list[dict[str, Any]]:
    return [
        {"category": category, "status": "not_run", "commands": []}
        for category in CHECK_CATEGORIES
    ]


def _report(
    step6: Mapping[str, Any],
    request: Mapping[str, Any],
    *,
    status: str,
    checks: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    generation_fingerprint: str | None = None,
    validation_fingerprint: str | None = None,
    runner_attestation: Mapping[str, Any],
) -> dict[str, Any]:
    target = step6.get("target", {})
    patch = step6.get("patch", {})
    source = step6.get("source", {})
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "analysis_kind": REPORT_ANALYSIS_KIND,
        "status": status,
        # A checksum only proves this report's bytes are internally consistent;
        # it cannot authenticate an external sandbox. Step 8 must independently
        # verify a trusted runner attestation before it can create a PR.
        "pr_eligible": False,
        "step6_report_sha256": artifact_sha256(step6),
        "profile": dict(request.get("profile", {})),
        "runner": dict(runner_attestation),
        "source": {
            "repository": source.get("repository"),
            "pr_number": source.get("pr_number"),
            "head_revision": source.get("head_revision"),
        },
        "target": {
            "repository": target.get("repository"),
            "base_revision": target.get("base_revision"),
        },
        "patch": {
            "patch_sha256": patch.get("patch_sha256"),
            "generator_id": patch.get("generator", {}).get("id"),
            "generator_version": patch.get("generator", {}).get("version"),
            "paths": sorted(
                row.get("path")
                for row in patch.get("files", [])
                if isinstance(row, Mapping)
            ),
        },
        "generation_fingerprint": generation_fingerprint or "0" * 64,
        "validation_fingerprint": validation_fingerprint or "0" * 64,
        "checks": checks,
        "failures": failures,
        "provenance": {
            "rule_set_version": RULE_SET_VERSION,
            "read_only": True,
            "catalog_mutation": "none",
            "github_writes": "none",
            "pr_creation": "none",
        },
    }
    report["report_sha256"] = artifact_sha256(report)
    return report


def _fingerprints(
    step6: Mapping[str, Any],
    request: Mapping[str, Any],
    runner_attestation: Mapping[str, Any],
) -> tuple[str, str]:
    patch = step6["patch"]
    generation = artifact_sha256(
        {
            "step6_report_sha256": artifact_sha256(step6),
            "proposal_id": step6.get("proposal_id"),
            "patch_sha256": patch.get("patch_sha256"),
            "generator": patch.get("generator"),
            "files": [
                {
                    "path": row.get("path"),
                    "before_sha256": row.get("before_sha256"),
                    "after_sha256": row.get("after_sha256"),
                }
                for row in patch.get("files", [])
                if isinstance(row, Mapping)
            ],
        }
    )
    validation = artifact_sha256(
        {
            "generation_fingerprint": generation,
            "target": request["target"],
            "commands": request["commands"],
            "policy": request["policy"],
            "profile": request["profile"],
            "runner": dict(runner_attestation),
        }
    )
    return generation, validation


def _status_lines(checkout: Path) -> list[str]:
    result = _git(
        checkout,
        [
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ],
    )
    if result.returncode:
        raise Step7Error(
            "git status failed: " + _text_output(result.stderr, 4000).strip()
        )
    return result.stdout.decode("utf-8", errors="replace").splitlines()


def _expected_patch_status(checkout: Path, paths: set[str]) -> list[dict[str, Any]]:
    lines = _status_lines(checkout)
    unexpected = [line for line in lines if line[:2] != " M" or len(line) < 4]
    observed_paths = {line[3:] for line in lines if line[:2] == " M" and len(line) >= 4}
    if unexpected or observed_paths != paths:
        return [
            _failure(
                "unexpected_worktree_changes",
                phase="post_apply",
                remediation="Ensure validation commands are check-only and touch no files outside the patch.",
                expected=sorted(paths),
                observed=lines,
            )
        ]
    return []


def _github_identity(remote_url: str) -> str | None:
    value = remote_url.strip()
    if value.startswith("git@github.com:"):
        path = value.removeprefix("git@github.com:")
    else:
        parsed = urlparse(value)
        if parsed.hostname is None or parsed.hostname.lower() != "github.com":
            return None
        path = parsed.path.lstrip("/")
    normalized = path.removesuffix(".git").strip("/").lower()
    parts = normalized.split("/")
    return normalized if len(parts) == 2 and all(parts) else None


def _repository_identity(checkout: Path) -> str | None:
    remote = _git(checkout, ["remote", "get-url", "origin"])
    if remote.returncode:
        return None
    return _github_identity(remote.stdout.decode("utf-8", errors="replace"))


def _tracked_blob_mode(checkout: Path, revision: str, path: str) -> str | None:
    result = _git(checkout, ["ls-tree", revision, "--", path])
    if result.returncode:
        raise Step7Error(f"git ls-tree failed for {path}")
    lines = result.stdout.decode("utf-8", errors="replace").splitlines()
    if len(lines) != 1 or "\t" not in lines[0]:
        return None
    metadata, observed_path = lines[0].split("\t", 1)
    parts = metadata.split()
    if len(parts) != 3 or observed_path != path or parts[1] != "blob":
        return None
    return parts[0]


def _diff_stats(checkout: Path) -> tuple[int, int, int]:
    result = _git(checkout, ["diff", "--numstat", "--no-renames"])
    if result.returncode:
        raise Step7Error("git diff --numstat failed")
    files = added = deleted = 0
    for line in result.stdout.decode("utf-8", errors="replace").splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            raise Step7Error("binary or malformed diff is not supported by Step 7")
        files += 1
        added += int(parts[0])
        deleted += int(parts[1])
    return files, added, deleted


def _run_command(
    checkout: Path,
    command: Mapping[str, Any],
    *,
    output_limit: int,
    runner: Step7Runner,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    argv = [str(item) for item in command["argv"]]
    checkout_root = checkout.resolve()
    cwd = (checkout_root / str(command.get("cwd", "."))).resolve()
    if not cwd.is_relative_to(checkout_root) or not cwd.is_dir():
        return (
            {"id": command["id"], "status": "failed", "argv": argv},
            _failure(
                "command_working_directory_missing",
                phase="commands",
                command=argv,
                remediation="Declare a working directory that exists in the target base checkout.",
                observed=str(cwd.relative_to(checkout_root)),
            ),
        )
    result = runner.run(
        argv,
        cwd=cwd,
        timeout=int(command["timeout_seconds"]),
        output_limit=output_limit,
    )
    row = {
        "id": command["id"],
        "argv": argv,
        "cwd": str(cwd.relative_to(checkout_root)),
        "status": "passed"
        if result.outcome == "completed" and result.returncode == 0
        else "failed",
        "stdout_sha256": sha256_bytes(result.stdout),
        "stderr_sha256": sha256_bytes(result.stderr),
        "stdout": _text_output(result.stdout, output_limit),
        "stderr": _text_output(result.stderr, output_limit),
    }
    if result.outcome == "completed":
        row["exit_code"] = result.returncode
        if result.returncode == 0:
            return row, None
        return row, _failure(
            "validation_command_failed",
            phase="commands",
            command=argv,
            remediation="Fix the failing validation command and rerun Step 7.",
            expected=0,
            observed=result.returncode,
        )
    row["result"] = result.outcome
    if result.outcome == "output_limit":
        return (
            row,
            _failure(
                "validation_output_limit_exceeded",
                phase="commands",
                command=argv,
                remediation="Reduce validation command output or increase the reviewed output limit.",
            ),
        )
    if result.outcome == "timeout":
        return (
            row,
            _failure(
                "validation_command_timeout",
                phase="commands",
                command=argv,
                remediation="Reduce command scope or increase its declared timeout after review.",
            ),
        )
    return (
        row,
        _failure(
            "validation_command_unavailable",
            phase="commands",
            command=argv,
            remediation="Install or expose the declared validation tool in the runner environment.",
            observed=result.error or result.outcome,
        ),
    )


def validate_step7(
    step6_report: Mapping[str, Any],
    request: Mapping[str, Any],
    target_checkout: str | Path,
    *,
    profile_registry: Mapping[str, Any],
    runner: Step7Runner,
) -> dict[str, Any]:
    runner_attestation = runner.attestation()
    if not isinstance(runner_attestation, Mapping):
        raise Step7Error("runner attestation must be an object")
    unsigned_attestation = dict(runner_attestation)
    attestation_digest = unsigned_attestation.pop("attestation_sha256", None)
    if (
        not isinstance(attestation_digest, str)
        or artifact_sha256(unsigned_attestation) != attestation_digest
    ):
        raise Step7Error("runner attestation fingerprint is invalid")
    request_errors = validate_step7_request(request)
    if request_errors:
        raise Step7Error("invalid Step 7 request: " + "; ".join(request_errors))
    step6_errors = validate_step6_report(
        step6_report,
        strict_target_evidence=True,
        require_approvals=False,
        require_step7_eligibility=True,
    )
    if step6_errors:
        generation, validation = _fingerprints(
            step6_report, request, runner_attestation
        )
        return _report(
            step6_report,
            request,
            status="blocked",
            checks=_empty_checks(),
            failures=[
                _failure(
                    "step6_report_invalid",
                    phase="preflight",
                    remediation="Regenerate a strict Step 6 report with exact target evidence.",
                    observed=step6_errors,
                )
            ],
            generation_fingerprint=generation,
            validation_fingerprint=validation,
            runner_attestation=runner_attestation,
        )
    try:
        expected_request = build_step7_request(step6_report, profile_registry)
    except Step7ProfileError as exc:
        generation, validation = _fingerprints(
            step6_report, request, runner_attestation
        )
        return _report(
            step6_report,
            request,
            status="blocked",
            checks=_empty_checks(),
            failures=[
                _failure(
                    "step7_profile_unavailable",
                    phase="preflight",
                    remediation="Enable an owner-approved central validation profile.",
                    observed=str(exc),
                )
            ],
            generation_fingerprint=generation,
            validation_fingerprint=validation,
            runner_attestation=runner_attestation,
        )
    if artifact_sha256(request) != artifact_sha256(expected_request):
        generation, validation = _fingerprints(
            step6_report, request, runner_attestation
        )
        return _report(
            step6_report,
            request,
            status="blocked",
            checks=_empty_checks(),
            failures=[
                _failure(
                    "step7_request_profile_mismatch",
                    phase="preflight",
                    remediation="Regenerate the request from the exact central profile and Step 6 report.",
                    expected=artifact_sha256(expected_request),
                    observed=artifact_sha256(request),
                )
            ],
            generation_fingerprint=generation,
            validation_fingerprint=validation,
            runner_attestation=runner_attestation,
        )
    expected_hash = request["step6_report_sha256"]
    actual_hash = artifact_sha256(step6_report)
    if expected_hash != actual_hash:
        generation, validation = _fingerprints(
            step6_report, request, runner_attestation
        )
        return _report(
            step6_report,
            request,
            status="blocked",
            checks=_empty_checks(),
            failures=[
                _failure(
                    "step6_report_fingerprint_mismatch",
                    phase="preflight",
                    remediation="Use the exact Step 6 report identified by the request.",
                    expected=expected_hash,
                    observed=actual_hash,
                )
            ],
            generation_fingerprint=generation,
            validation_fingerprint=validation,
            runner_attestation=runner_attestation,
        )
    target = step6_report["target"]
    request_target = request["target"]
    if target.get("repository") != request_target.get("repository") or target.get(
        "base_revision"
    ) != request_target.get("base_revision"):
        generation, validation = _fingerprints(
            step6_report, request, runner_attestation
        )
        return _report(
            step6_report,
            request,
            status="blocked",
            checks=_empty_checks(),
            failures=[
                _failure(
                    "target_identity_mismatch",
                    phase="preflight",
                    remediation="Regenerate the Step 7 request from the same Step 6 target.",
                    expected=dict(target),
                    observed=dict(request_target),
                )
            ],
            generation_fingerprint=generation,
            validation_fingerprint=validation,
            runner_attestation=runner_attestation,
        )

    generation, validation = _fingerprints(step6_report, request, runner_attestation)
    checkout = Path(target_checkout).resolve()
    failures: list[dict[str, Any]] = []
    checks = _empty_checks()
    if not checkout.is_dir():
        failures.append(
            _failure(
                "target_checkout_missing",
                phase="preflight",
                remediation="Provide a local target repository checkout at the exact base revision.",
                observed=str(checkout),
            )
        )
    else:
        try:
            status_lines = _status_lines(checkout)
            if status_lines:
                failures.append(
                    _failure(
                        "target_checkout_dirty",
                        phase="preflight",
                        remediation="Use a clean target checkout; Step 7 never overwrites local changes.",
                        observed=status_lines,
                    )
                )
            head = _git(checkout, ["rev-parse", "HEAD"])
            actual_head = head.stdout.decode("utf-8", errors="replace").strip()
            if head.returncode or actual_head != target["base_revision"]:
                failures.append(
                    _failure(
                        "target_base_revision_mismatch",
                        phase="preflight",
                        remediation="Check out the exact target.base_revision before rerunning Step 7.",
                        expected=target["base_revision"],
                        observed=actual_head or _text_output(head.stderr, 4000).strip(),
                    )
                )
            observed_repository = _repository_identity(checkout)
            if observed_repository is None:
                failures.append(
                    _failure(
                        "target_repository_unverified",
                        phase="preflight",
                        remediation="Configure a canonical GitHub origin matching target.repository.",
                        expected=str(target["repository"]).lower(),
                    )
                )
            elif observed_repository != str(target["repository"]).lower():
                failures.append(
                    _failure(
                        "target_repository_mismatch",
                        phase="preflight",
                        remediation="Use a checkout whose origin matches target.repository.",
                        expected=str(target["repository"]).lower(),
                        observed=observed_repository,
                    )
                )
        except (OSError, Step7Error) as exc:
            failures.append(
                _failure(
                    "target_checkout_unreadable",
                    phase="preflight",
                    remediation="Provide a readable Git checkout with the target commit available.",
                    observed=str(exc),
                )
            )
    if failures:
        return _report(
            step6_report,
            request,
            status="blocked",
            checks=checks,
            failures=failures,
            generation_fingerprint=generation,
            validation_fingerprint=validation,
            runner_attestation=runner_attestation,
        )

    patch = step6_report["patch"]
    patch_files = {
        str(row["path"]): row for row in patch["files"] if isinstance(row, Mapping)
    }
    for path in sorted(patch_files):
        try:
            mode = _tracked_blob_mode(checkout, target["base_revision"], path)
        except Step7Error as exc:
            failures.append(
                _failure(
                    "patch_path_mode_unavailable",
                    phase="preflight",
                    path=path,
                    remediation="Provide an exact checkout where the patch path can be inspected.",
                    observed=str(exc),
                )
            )
            continue
        if mode not in {"100644", "100755"}:
            failures.append(
                _failure(
                    "patch_path_mode_unsupported",
                    phase="preflight",
                    path=path,
                    remediation="Use update-only patches against ordinary tracked files.",
                    expected=["100644", "100755"],
                    observed=mode,
                )
            )
    allowed = set(target["allowed_paths"])
    if set(patch_files) - allowed:
        failures.append(
            _failure(
                "patch_path_outside_approved_scope",
                phase="preflight",
                remediation="Regenerate the patch using only Step 6 target.allowed_paths.",
                expected=sorted(allowed),
                observed=sorted(set(patch_files) - allowed),
            )
        )
    policy = request["policy"]
    generated_policy = policy["generated_file_policy"]
    generated_paths = set(generated_policy["generated_paths"])
    source_paths = set(generated_policy["source_paths"])
    unclassified_paths = set(patch_files) - generated_paths - source_paths
    if unclassified_paths:
        failures.append(
            _failure(
                "generated_file_status_unknown",
                phase="preflight",
                remediation="Classify every changed path as source or generated in the Step 7 policy.",
                observed=sorted(unclassified_paths),
            )
        )
    changed_generated = set(patch_files) & generated_paths
    if generated_policy["mode"] == "reject" and changed_generated:
        failures.append(
            _failure(
                "generated_file_change_rejected",
                phase="preflight",
                remediation="Remove generated files from the patch or explicitly allow them in policy.",
                observed=sorted(changed_generated),
            )
        )
    if generated_policy["mode"] == "allowlist" and not changed_generated <= set(
        generated_policy["allowed_generated_paths"]
    ):
        failures.append(
            _failure(
                "generated_file_not_allowlisted",
                phase="preflight",
                remediation="Add every changed generated path to allowed_generated_paths after review.",
                observed=sorted(
                    changed_generated - set(generated_policy["allowed_generated_paths"])
                ),
            )
        )
    if failures:
        return _report(
            step6_report,
            request,
            status="blocked",
            checks=checks,
            failures=failures,
            generation_fingerprint=generation,
            validation_fingerprint=validation,
            runner_attestation=runner_attestation,
        )

    try:
        with tempfile.TemporaryDirectory(prefix="greenfield-step7-") as temporary:
            isolated = Path(temporary) / "target"
            clone = _run(
                [
                    "git",
                    "clone",
                    "--local",
                    "--no-hardlinks",
                    str(checkout),
                    str(isolated),
                ],
                cwd=checkout,
                timeout=120,
            )
            if clone.returncode:
                failures.append(
                    _failure(
                        "isolated_checkout_creation_failed",
                        phase="preflight",
                        remediation="Make the local target checkout cloneable and retry Step 7.",
                        observed=_text_output(clone.stderr, 4000).strip(),
                    )
                )
            else:
                detached = _git(
                    isolated, ["checkout", "--detach", target["base_revision"]]
                )
                if detached.returncode:
                    failures.append(
                        _failure(
                            "target_base_revision_unavailable",
                            phase="preflight",
                            remediation="Fetch or materialize the exact target base commit in the local mirror.",
                            observed=_text_output(detached.stderr, 4000).strip(),
                        )
                    )
                else:
                    for path, row in sorted(patch_files.items()):
                        actual = (
                            (isolated / path).read_bytes()
                            if (isolated / path).is_file()
                            else None
                        )
                        actual_hash = (
                            sha256_bytes(actual) if actual is not None else None
                        )
                        if actual_hash != row["before_sha256"]:
                            failures.append(
                                _failure(
                                    "patch_base_blob_mismatch",
                                    phase="preflight",
                                    path=path,
                                    remediation="Regenerate the patch against the exact target base file contents.",
                                    expected=row["before_sha256"],
                                    observed=actual_hash,
                                )
                            )
                    if not failures:
                        patch_path = Path(temporary) / "patch.diff"
                        patch_path.write_text(patch["unified_diff"], encoding="utf-8")
                        apply_check = _git(
                            isolated,
                            [
                                "apply",
                                "--check",
                                "--whitespace=error-all",
                                str(patch_path),
                            ],
                        )
                        if apply_check.returncode:
                            failures.append(
                                _failure(
                                    "patch_application_check_failed",
                                    phase="apply",
                                    remediation="Regenerate the patch with context matching the exact target base.",
                                    observed=_text_output(
                                        apply_check.stderr, 4000
                                    ).strip(),
                                )
                            )
                        else:
                            applied = _git(
                                isolated,
                                ["apply", "--whitespace=error-all", str(patch_path)],
                            )
                            if applied.returncode:
                                failures.append(
                                    _failure(
                                        "patch_application_failed",
                                        phase="apply",
                                        remediation="Fix the patch application failure before creating a PR.",
                                        observed=_text_output(
                                            applied.stderr, 4000
                                        ).strip(),
                                    )
                                )
                    if not failures:
                        failures.extend(
                            _expected_patch_status(isolated, set(patch_files))
                        )
                        for path, row in sorted(patch_files.items()):
                            actual = (isolated / path).read_bytes()
                            if sha256_bytes(actual) != row["after_sha256"]:
                                failures.append(
                                    _failure(
                                        "patched_file_hash_mismatch",
                                        phase="post_apply",
                                        path=path,
                                        remediation="Ensure the unified diff and after_sha256 describe the same content.",
                                        expected=row["after_sha256"],
                                        observed=sha256_bytes(actual),
                                    )
                                )
                    if not failures:
                        files, added, deleted = _diff_stats(isolated)
                        limits = policy["diff_limits"]
                        if files > limits["max_files"]:
                            failures.append(
                                _failure(
                                    "diff_file_limit_exceeded",
                                    phase="policy",
                                    remediation="Reduce the patch file count.",
                                    expected=limits["max_files"],
                                    observed=files,
                                )
                            )
                        if added > limits["max_added_lines"]:
                            failures.append(
                                _failure(
                                    "diff_added_line_limit_exceeded",
                                    phase="policy",
                                    remediation="Reduce added lines or obtain an explicit policy change.",
                                    expected=limits["max_added_lines"],
                                    observed=added,
                                )
                            )
                        if deleted > limits["max_deleted_lines"]:
                            failures.append(
                                _failure(
                                    "diff_deleted_line_limit_exceeded",
                                    phase="policy",
                                    remediation="Reduce deleted lines or obtain an explicit policy change.",
                                    expected=limits["max_deleted_lines"],
                                    observed=deleted,
                                )
                            )
                        diff_bytes = len(patch["unified_diff"].encode("utf-8"))
                        if diff_bytes > limits["max_bytes"]:
                            failures.append(
                                _failure(
                                    "diff_byte_limit_exceeded",
                                    phase="policy",
                                    remediation="Reduce the unified diff size.",
                                    expected=limits["max_bytes"],
                                    observed=diff_bytes,
                                )
                            )
                    if not failures:
                        for check in checks:
                            category = check["category"]
                            category_failed = False
                            for command in request["commands"][category]:
                                row, command_failure = _run_command(
                                    isolated,
                                    command,
                                    output_limit=policy["max_output_bytes"],
                                    runner=runner,
                                )
                                check["commands"].append(row)
                                if command_failure is not None:
                                    failures.append(command_failure)
                                    category_failed = True
                                if not category_failed:
                                    state_failures = _expected_patch_status(
                                        isolated, set(patch_files)
                                    )
                                    if state_failures:
                                        failures.extend(state_failures)
                                        category_failed = True
                                if category_failed:
                                    check["status"] = "failed"
                                    break
                            else:
                                check["status"] = (
                                    "failed" if category_failed else "passed"
                                )
                            if category_failed:
                                for remaining in checks[checks.index(check) + 1 :]:
                                    remaining["status"] = "not_run"
                                break
                        if not failures:
                            failures.extend(
                                _expected_patch_status(isolated, set(patch_files))
                            )
    except (OSError, subprocess.TimeoutExpired, Step7Error) as exc:
        failures.append(
            _failure(
                "step7_execution_failed",
                phase="execution",
                remediation="Inspect the validator error and rerun against a valid target checkout.",
                observed=str(exc),
            )
        )

    status = "validated" if not failures else "failed"
    return _report(
        step6_report,
        request,
        status=status,
        checks=checks,
        failures=failures,
        generation_fingerprint=generation,
        validation_fingerprint=validation,
        runner_attestation=runner_attestation,
    )


__all__ = ["validate_step7"]
