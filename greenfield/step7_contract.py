"""Contracts and hashing helpers for Greenfield PR-impact Step 7."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "0.2"
REQUEST_ANALYSIS_KIND = "greenfield_pr_impact_step_7_request"
REPORT_ANALYSIS_KIND = "greenfield_pr_impact_step_7"
RULE_SET_VERSION = "0.1"
SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
CHECK_CATEGORIES = (
    "format",
    "lint",
    "compile_or_type",
    "targeted",
    "integration",
    "regression",
)
REPORT_STATUSES = {"validated", "failed", "blocked"}


class Step7Error(ValueError):
    """Raised when Step 7 input or output is unsafe or malformed."""


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def artifact_sha256(value: object) -> str:
    return sha256_bytes(canonical_json(value).encode("utf-8"))


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Step7Error(f"{label} must be a non-empty string")
    return value.strip()


def _sha(value: Any, label: str) -> str:
    result = _text(value, label).lower()
    if not SHA.fullmatch(result):
        raise Step7Error(f"{label} must be a lowercase 40-character SHA")
    return result


def _sha256(value: Any, label: str) -> str:
    result = _text(value, label).lower()
    if not SHA256.fullmatch(result):
        raise Step7Error(f"{label} must be a lowercase SHA-256")
    return result


def _path(value: Any, label: str) -> str:
    result = _text(value, label)
    candidate = Path(result)
    if candidate.is_absolute() or ".." in candidate.parts or "\\" in result:
        raise Step7Error(f"{label} must be an exact safe relative path")
    return result


def _sorted_paths(value: Any, label: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        required = "" if allow_empty else "non-empty "
        raise Step7Error(f"{label} must be a {required}list")
    result = [_path(item, f"{label}[{index}]") for index, item in enumerate(value)]
    if result != sorted(set(result)):
        raise Step7Error(f"{label} must be sorted and unique")
    return result


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise Step7Error(f"{label} must be a positive integer")
    return value


def _nonnegative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise Step7Error(f"{label} must be a non-negative integer")
    return value


def _validate_command(value: Any, label: str) -> None:
    if not isinstance(value, Mapping):
        raise Step7Error(f"{label} must be an object")
    _text(value.get("id"), f"{label}.id")
    argv = value.get("argv")
    if (
        not isinstance(argv, list)
        or not argv
        or any(not isinstance(item, str) or not item.strip() for item in argv)
    ):
        raise Step7Error(f"{label}.argv must be a non-empty list of strings")
    if value.get("shell", False) is not False:
        raise Step7Error(f"{label}.shell must be false")
    cwd = value.get("cwd", ".")
    if not isinstance(cwd, str) or not cwd.strip():
        raise Step7Error(f"{label}.cwd must be a non-empty string")
    cwd_path = Path(cwd)
    if cwd_path.is_absolute() or ".." in cwd_path.parts:
        raise Step7Error(f"{label}.cwd must be relative and stay within the checkout")
    _positive_int(value.get("timeout_seconds"), f"{label}.timeout_seconds")


def _validate_commands(value: Any) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(value, Mapping):
        raise Step7Error("commands must be an object")
    result: dict[str, list[dict[str, Any]]] = {}
    for category in CHECK_CATEGORIES:
        rows = value.get(category)
        if not isinstance(rows, list) or not rows:
            raise Step7Error(f"commands.{category} must be a non-empty list")
        normalized: list[dict[str, Any]] = []
        ids: list[str] = []
        for index, command in enumerate(rows):
            _validate_command(command, f"commands.{category}[{index}]")
            normalized.append(dict(command))
            ids.append(str(command["id"]))
        if ids != sorted(set(ids)):
            raise Step7Error(f"commands.{category} must be sorted and unique by id")
        result[category] = normalized
    return result


def _validate_policy(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise Step7Error("policy must be an object")
    limits = value.get("diff_limits")
    if not isinstance(limits, Mapping):
        raise Step7Error("policy.diff_limits must be an object")
    for field in ("max_files", "max_added_lines", "max_deleted_lines", "max_bytes"):
        _nonnegative_int(limits.get(field), f"policy.diff_limits.{field}")
    output_limit = _positive_int(
        value.get("max_output_bytes"), "policy.max_output_bytes"
    )
    if output_limit > 10_000_000:
        raise Step7Error("policy.max_output_bytes must not exceed 10000000")
    generated = value.get("generated_file_policy")
    if not isinstance(generated, Mapping):
        raise Step7Error("policy.generated_file_policy must be an object")
    if generated.get("mode") not in {"reject", "allowlist"}:
        raise Step7Error(
            "policy.generated_file_policy.mode must be reject or allowlist"
        )
    _sorted_paths(
        generated.get("generated_paths"),
        "policy.generated_file_policy.generated_paths",
        allow_empty=True,
    )
    _sorted_paths(
        generated.get("source_paths"),
        "policy.generated_file_policy.source_paths",
        allow_empty=True,
    )
    _sorted_paths(
        generated.get("allowed_generated_paths"),
        "policy.generated_file_policy.allowed_generated_paths",
        allow_empty=True,
    )
    if generated.get("unknown_status") != "fail":
        raise Step7Error("policy.generated_file_policy.unknown_status must be fail")
    return {
        "diff_limits": dict(limits),
        "max_output_bytes": output_limit,
        "generated_file_policy": dict(generated),
    }


def validate_step7_request(request: Any) -> list[str]:
    try:
        _validate_request(request)
    except Step7Error as exc:
        return [str(exc)]
    return []


def _validate_request(request: Any) -> None:
    if not isinstance(request, Mapping):
        raise Step7Error("request must be an object")
    if request.get("schema_version") != SCHEMA_VERSION:
        raise Step7Error(f"schema_version must be {SCHEMA_VERSION}")
    if request.get("analysis_kind") != REQUEST_ANALYSIS_KIND:
        raise Step7Error(f"analysis_kind must be {REQUEST_ANALYSIS_KIND}")
    _sha256(request.get("step6_report_sha256"), "step6_report_sha256")
    profile = request.get("profile")
    if not isinstance(profile, Mapping):
        raise Step7Error("profile must be an object")
    _text(profile.get("id"), "profile.id")
    _text(profile.get("version"), "profile.version")
    _sha256(profile.get("sha256"), "profile.sha256")
    runner = request.get("runner")
    if not isinstance(runner, Mapping) or runner.get("required_class") != "sandbox":
        raise Step7Error("runner.required_class must be sandbox")
    target = request.get("target")
    if not isinstance(target, Mapping):
        raise Step7Error("target must be an object")
    _text(target.get("repository"), "target.repository")
    _sha(target.get("base_revision"), "target.base_revision")
    _validate_commands(request.get("commands"))
    _validate_policy(request.get("policy"))


def validate_step7_report(report: Any) -> list[str]:
    try:
        _validate_report(report)
    except Step7Error as exc:
        return [str(exc)]
    return []


def _validate_report(report: Any) -> None:
    if not isinstance(report, Mapping):
        raise Step7Error("report must be an object")
    if report.get("schema_version") != SCHEMA_VERSION:
        raise Step7Error(f"schema_version must be {SCHEMA_VERSION}")
    if report.get("analysis_kind") != REPORT_ANALYSIS_KIND:
        raise Step7Error(f"analysis_kind must be {REPORT_ANALYSIS_KIND}")
    status = report.get("status")
    if status not in REPORT_STATUSES:
        raise Step7Error("status is invalid")
    if not isinstance(report.get("pr_eligible"), bool):
        raise Step7Error("pr_eligible must be a boolean")
    _sha256(report.get("report_sha256"), "report_sha256")
    unsigned = dict(report)
    unsigned.pop("report_sha256", None)
    if artifact_sha256(unsigned) != report["report_sha256"]:
        raise Step7Error("report_sha256 does not match report contents")
    _sha256(report.get("step6_report_sha256"), "step6_report_sha256")
    _sha256(report.get("generation_fingerprint"), "generation_fingerprint")
    _sha256(report.get("validation_fingerprint"), "validation_fingerprint")
    profile = report.get("profile")
    if not isinstance(profile, Mapping):
        raise Step7Error("profile must be an object")
    _text(profile.get("id"), "profile.id")
    _text(profile.get("version"), "profile.version")
    _sha256(profile.get("sha256"), "profile.sha256")
    runner = report.get("runner")
    if not isinstance(runner, Mapping):
        raise Step7Error("runner must be an object")
    _text(runner.get("id"), "runner.id")
    _text(runner.get("version"), "runner.version")
    if runner.get("isolation") not in {"local", "sandbox"}:
        raise Step7Error("runner.isolation is invalid")
    if not isinstance(runner.get("production_eligible"), bool):
        raise Step7Error("runner.production_eligible must be a boolean")
    _sha256(runner.get("attestation_sha256"), "runner.attestation_sha256")
    unsigned_runner = dict(runner)
    unsigned_runner.pop("attestation_sha256", None)
    if artifact_sha256(unsigned_runner) != runner["attestation_sha256"]:
        raise Step7Error("runner.attestation_sha256 does not match runner")
    if report.get("pr_eligible") is not False:
        raise Step7Error("Step 7 reports must remain non-PR-eligible")
    target = report.get("target")
    if not isinstance(target, Mapping):
        raise Step7Error("target must be an object")
    _text(target.get("repository"), "target.repository")
    _sha(target.get("base_revision"), "target.base_revision")
    patch = report.get("patch")
    if not isinstance(patch, Mapping):
        raise Step7Error("patch must be an object")
    _sha256(patch.get("patch_sha256"), "patch.patch_sha256")
    _text(patch.get("generator_id"), "patch.generator_id")
    _text(patch.get("generator_version"), "patch.generator_version")
    checks = report.get("checks")
    if not isinstance(checks, list):
        raise Step7Error("checks must be a list")
    categories = [
        str(row.get("category")) for row in checks if isinstance(row, Mapping)
    ]
    if categories != list(CHECK_CATEGORIES):
        raise Step7Error("checks must contain each category in deterministic order")
    if any(
        not isinstance(row, Mapping)
        or row.get("status") not in {"passed", "failed", "not_run"}
        or not isinstance(row.get("commands"), list)
        for row in checks
    ):
        raise Step7Error("checks contain an invalid row")
    for row in checks:
        for command in row["commands"]:
            if not isinstance(command, Mapping):
                raise Step7Error("check command must be an object")
            _text(command.get("id"), "check command id")
            if command.get("status") not in {"passed", "failed"}:
                raise Step7Error("check command status is invalid")
            argv = command.get("argv")
            if (
                not isinstance(argv, list)
                or not argv
                or any(not isinstance(item, str) or not item for item in argv)
            ):
                raise Step7Error("check command argv is invalid")
            for field in ("stdout_sha256", "stderr_sha256"):
                if field in command:
                    _sha256(command[field], f"check command {field}")
    failures = report.get("failures")
    if not isinstance(failures, list) or any(
        not isinstance(item, Mapping) for item in failures
    ):
        raise Step7Error("failures must be a list of objects")
    if status == "validated":
        if failures:
            raise Step7Error("validated reports must not contain failures")
        for row in checks:
            if row["status"] != "passed" or not row["commands"]:
                raise Step7Error(
                    "validated reports require every check category to pass"
                )
            if any(
                not isinstance(command, Mapping) or command.get("status") != "passed"
                for command in row["commands"]
            ):
                raise Step7Error(
                    "validated reports require every validation command to pass"
                )
    provenance = report.get("provenance")
    if not isinstance(provenance, Mapping):
        raise Step7Error("provenance must be an object")
    if (
        provenance.get("read_only") is not True
        or provenance.get("github_writes") != "none"
        or provenance.get("catalog_mutation") != "none"
        or provenance.get("pr_creation") != "none"
    ):
        raise Step7Error("provenance must declare read-only and no writes")


def load_json(path: str | Path, label: str) -> dict[str, Any]:
    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Step7Error(f"{label}_read_failed: {source}: {exc}") from exc
    if not isinstance(value, dict):
        raise Step7Error(f"{label} must be an object")
    return value


__all__ = [
    "CHECK_CATEGORIES",
    "REPORT_ANALYSIS_KIND",
    "REQUEST_ANALYSIS_KIND",
    "RULE_SET_VERSION",
    "SCHEMA_VERSION",
    "Step7Error",
    "artifact_sha256",
    "canonical_json",
    "load_json",
    "sha256_bytes",
    "validate_step7_report",
    "validate_step7_request",
]
