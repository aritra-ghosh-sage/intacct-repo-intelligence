"""Read-only Greenfield Step 7 patch validation in an isolated checkout."""

from __future__ import annotations

import os
import selectors
import subprocess
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from greenfield.step6_contract import validate_step6_report
from greenfield.step7_contract import (
    CHECK_CATEGORIES,
    REPORT_ANALYSIS_KIND,
    RULE_SET_VERSION,
    Step7Error,
    artifact_sha256,
    sha256_bytes,
    validate_step7_request,
)


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


class _OutputLimitExceeded(RuntimeError):
    def __init__(self, stdout: bytes, stderr: bytes) -> None:
        super().__init__("validation command output exceeded the configured limit")
        self.stdout = stdout
        self.stderr = stderr


def _terminate(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.kill()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            stream.close()


def _run_bounded(
    argv: list[str],
    *,
    cwd: Path,
    timeout: int,
    output_limit: int,
) -> subprocess.CompletedProcess[bytes]:
    process = subprocess.Popen(
        argv,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )
    selector = selectors.DefaultSelector()
    assert process.stdout is not None
    assert process.stderr is not None
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    output = {"stdout": bytearray(), "stderr": bytearray()}
    deadline = time.monotonic() + timeout
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                stdout = bytes(output["stdout"])
                stderr = bytes(output["stderr"])
                _terminate(process)
                raise subprocess.TimeoutExpired(
                    argv, timeout, output=stdout, stderr=stderr
                )
            events = selector.select(remaining)
            if not events:
                stdout = bytes(output["stdout"])
                stderr = bytes(output["stderr"])
                _terminate(process)
                raise subprocess.TimeoutExpired(
                    argv, timeout, output=stdout, stderr=stderr
                )
            for key, _ in events:
                chunk = os.read(key.fileobj.fileno(), 64 * 1024)
                if not chunk:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                stream = output[key.data]
                if len(stream) + len(chunk) > output_limit:
                    stdout = bytes(output["stdout"])
                    stderr = bytes(output["stderr"])
                    _terminate(process)
                    raise _OutputLimitExceeded(stdout, stderr)
                stream.extend(chunk)
        return subprocess.CompletedProcess(
            argv,
            process.wait(),
            stdout=bytes(output["stdout"]),
            stderr=bytes(output["stderr"]),
        )
    finally:
        selector.close()


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
) -> dict[str, Any]:
    target = step6.get("target", {})
    patch = step6.get("patch", {})
    source = step6.get("source", {})
    report: dict[str, Any] = {
        "schema_version": "0.1",
        "analysis_kind": REPORT_ANALYSIS_KIND,
        "status": status,
        "pr_eligible": status == "validated",
        "step6_report_sha256": artifact_sha256(step6),
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
    step6: Mapping[str, Any], request: Mapping[str, Any]
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


def _repository_identity_matches(checkout: Path, repository: str) -> bool:
    remote = _git(checkout, ["remote", "get-url", "origin"])
    if remote.returncode:
        return True
    value = remote.stdout.decode("utf-8", errors="replace").strip()
    normalized = value.removesuffix(".git")
    if normalized.startswith("git@github.com:"):
        normalized = normalized.removeprefix("git@github.com:")
    elif "github.com/" in normalized:
        normalized = normalized.split("github.com/", 1)[1]
    return normalized == repository


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
    try:
        result = _run_bounded(
            argv,
            cwd=cwd,
            timeout=int(command["timeout_seconds"]),
            output_limit=output_limit,
        )
        stdout_sha256 = sha256_bytes(result.stdout)
        stderr_sha256 = sha256_bytes(result.stderr)
        row = {
            "id": command["id"],
            "argv": argv,
            "cwd": str(cwd.relative_to(checkout_root)),
            "status": "passed" if result.returncode == 0 else "failed",
            "exit_code": result.returncode,
            "stdout_sha256": stdout_sha256,
            "stderr_sha256": stderr_sha256,
            "stdout": _text_output(result.stdout, output_limit),
            "stderr": _text_output(result.stderr, output_limit),
        }
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
    except _OutputLimitExceeded as exc:
        return (
            {
                "id": command["id"],
                "argv": argv,
                "cwd": str(cwd.relative_to(checkout_root)),
                "status": "failed",
                "result": "output_limit",
                "stdout_sha256": sha256_bytes(exc.stdout),
                "stderr_sha256": sha256_bytes(exc.stderr),
                "stdout": _text_output(exc.stdout, output_limit),
                "stderr": _text_output(exc.stderr, output_limit),
            },
            _failure(
                "validation_output_limit_exceeded",
                phase="commands",
                command=argv,
                remediation="Reduce validation command output or increase the reviewed output limit.",
            ),
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, bytes) else b""
        stderr = exc.stderr if isinstance(exc.stderr, bytes) else b""
        return (
            {
                "id": command["id"],
                "argv": argv,
                "cwd": str(cwd.relative_to(checkout_root)),
                "status": "failed",
                "result": "timeout",
                "stdout_sha256": sha256_bytes(stdout),
                "stderr_sha256": sha256_bytes(stderr),
                "stdout": _text_output(stdout, output_limit),
                "stderr": _text_output(stderr, output_limit),
            },
            _failure(
                "validation_command_timeout",
                phase="commands",
                command=argv,
                remediation="Reduce command scope or increase its declared timeout after review.",
            ),
        )
    except OSError as exc:
        return (
            {"id": command["id"], "argv": argv, "status": "failed"},
            _failure(
                "validation_command_unavailable",
                phase="commands",
                command=argv,
                remediation="Install or expose the declared validation tool in the runner environment.",
                observed=str(exc),
            ),
        )


def validate_step7(
    step6_report: Mapping[str, Any],
    request: Mapping[str, Any],
    target_checkout: str | Path,
) -> dict[str, Any]:
    request_errors = validate_step7_request(request)
    if request_errors:
        raise Step7Error("invalid Step 7 request: " + "; ".join(request_errors))
    step6_errors = validate_step6_report(
        step6_report,
        strict_target_evidence=True,
        require_approvals=True,
        require_step7_eligibility=True,
    )
    if step6_errors:
        generation, validation = _fingerprints(step6_report, request)
        return _report(
            step6_report,
            request,
            status="blocked",
            checks=_empty_checks(),
            failures=[
                _failure(
                    "step6_report_invalid",
                    phase="preflight",
                    remediation="Regenerate a strict Step 6 report with exact target evidence and both approvals.",
                    observed=step6_errors,
                )
            ],
            generation_fingerprint=generation,
            validation_fingerprint=validation,
        )
    expected_hash = request["step6_report_sha256"]
    actual_hash = artifact_sha256(step6_report)
    if expected_hash != actual_hash:
        generation, validation = _fingerprints(step6_report, request)
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
        )
    target = step6_report["target"]
    request_target = request["target"]
    if target.get("repository") != request_target.get("repository") or target.get(
        "base_revision"
    ) != request_target.get("base_revision"):
        generation, validation = _fingerprints(step6_report, request)
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
        )

    generation, validation = _fingerprints(step6_report, request)
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
            if not _repository_identity_matches(checkout, target["repository"]):
                failures.append(
                    _failure(
                        "target_repository_mismatch",
                        phase="preflight",
                        remediation="Use a checkout whose origin matches target.repository.",
                        expected=target["repository"],
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
        )

    patch = step6_report["patch"]
    patch_files = {
        str(row["path"]): row for row in patch["files"] if isinstance(row, Mapping)
    }
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
    )


__all__ = ["validate_step7"]
