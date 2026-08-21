from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

from greenfield.step6_contract import artifact_sha256 as step6_artifact_sha256
from greenfield.step7_contract import (
    artifact_sha256,
    validate_step7_report,
    validate_step7_request,
)
from greenfield.step7_validate import validate_step7
from scripts import validate_greenfield_step7

ROOT = Path(__file__).resolve().parents[1]
STEP6_GOLDEN = ROOT / "examples/greenfield/ia-app-pr-49156/replay/step6.report.json"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, check=True, text=True
    )
    return result.stdout.strip()


def _target_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "target"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    _git(repo, "config", "user.email", "step7@example.invalid")
    _git(repo, "config", "user.name", "Step 7")
    (repo / "features/gl/v1-beta2/input").mkdir(parents=True)
    (repo / "features/gl/v1-beta2/example.feature").write_text(
        "Feature: Example\n\n  Scenario: Existing case\n"
        "    Given request fixture request.json\n    Then old\n",
        encoding="utf-8",
    )
    (repo / "features/gl/v1-beta2/input/request.json").write_text(
        '{"field": "old"}\n', encoding="utf-8"
    )
    (repo / ".gitignore").write_text(".step7-build/\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "target base")
    return repo, _git(repo, "rev-parse", "HEAD")


def _step6_report(repo: Path, revision: str) -> dict:
    report = json.loads(STEP6_GOLDEN.read_text(encoding="utf-8"))
    report["eligibility_profile"] = "step7"
    report["target"]["base_revision"] = revision
    report["target"]["files"] = [
        {
            "path": row["path"],
            "content": row["before"],
            "sha256": row["before_sha256"],
        }
        for row in report["patch"]["files"]
    ]
    evidence = {
        "provider": "github_git_api",
        "repository": report["target"]["repository"],
        "revision": revision,
        "files": [
            {
                "path": row["path"],
                "content_sha256": row["before_sha256"],
                "blob_or_response_id": f"blob:{index}",
            }
            for index, row in enumerate(report["patch"]["files"])
        ],
    }
    evidence["evidence_sha256"] = step6_artifact_sha256(evidence)
    report["target_evidence"] = evidence
    report["approvals"] = [
        {
            "role": "source_interface_owner",
            "status": "approved",
            "approver": "source-owner",
            "approval_evidence": {
                "provider": "test-approval",
                "record_id": "source-1",
                "sha256": "1" * 64,
            },
        },
        {
            "role": "consumer_test_owner",
            "status": "approved",
            "approver": "test-owner",
            "approval_evidence": {
                "provider": "test-approval",
                "record_id": "consumer-1",
                "sha256": "2" * 64,
            },
        },
    ]
    for approval in report["approvals"]:
        approval["approval_sha256"] = step6_artifact_sha256(
            {
                "role": approval["role"],
                "status": approval["status"],
                "approver": approval["approver"],
                "approval_evidence": approval["approval_evidence"],
            }
        )
    report["idempotency_key"] = step6_artifact_sha256(
        {
            "source_repository": report["source"]["repository"],
            "pr_number": report["source"]["pr_number"],
            "source_revision": report["source"]["head_revision"],
            "target_repository": report["target"]["repository"],
            "target_revision": revision,
            "template": report["patch"]["generator"],
            "patch_sha256": report["patch"]["patch_sha256"],
        }
    )
    unsigned = dict(report)
    unsigned.pop("proposal_id")
    report["proposal_id"] = step6_artifact_sha256(unsigned)
    return report


def _request(step6: dict, revision: str) -> dict:
    commands = {
        category: [
            {
                "id": f"{category}-check",
                "argv": [sys.executable, "-c", "assert True"],
                "cwd": ".",
                "timeout_seconds": 30,
                "shell": False,
            }
        ]
        for category in (
            "format",
            "lint",
            "compile_or_type",
            "targeted",
            "integration",
            "regression",
        )
    }
    return {
        "schema_version": "0.1",
        "analysis_kind": "greenfield_pr_impact_step_7_request",
        "step6_report_sha256": artifact_sha256(step6),
        "target": {
            "repository": step6["target"]["repository"],
            "base_revision": revision,
        },
        "commands": commands,
        "policy": {
            "diff_limits": {
                "max_files": 5,
                "max_added_lines": 20,
                "max_deleted_lines": 20,
                "max_bytes": 10000,
            },
            "max_output_bytes": 4000,
            "generated_file_policy": {
                "mode": "reject",
                "generated_paths": [],
                "source_paths": sorted(row["path"] for row in step6["patch"]["files"]),
                "allowed_generated_paths": [],
                "unknown_status": "fail",
            },
        },
    }


def test_successful_validation_is_strict_and_reproducible(tmp_path: Path) -> None:
    repo, revision = _target_repo(tmp_path)
    step6 = _step6_report(repo, revision)
    request = _request(step6, revision)

    assert validate_step7_request(request) == []
    first = validate_step7(step6, request, repo)
    second = validate_step7(step6, request, repo)

    assert first["status"] == "validated"
    assert first["pr_eligible"] is True
    assert first["generation_fingerprint"] == second["generation_fingerprint"]
    assert first["validation_fingerprint"] == second["validation_fingerprint"]
    assert validate_step7_report(first) == []
    assert _git(repo, "status", "--porcelain") == ""


def test_dirty_or_wrong_base_checkout_is_blocked(tmp_path: Path) -> None:
    repo, revision = _target_repo(tmp_path)
    step6 = _step6_report(repo, revision)
    request = _request(step6, revision)
    (repo / "unrelated.txt").write_text("dirty\n", encoding="utf-8")

    report = validate_step7(step6, request, repo)

    assert report["status"] == "blocked"
    assert report["pr_eligible"] is False
    assert report["failures"][0]["code"] == "target_checkout_dirty"


def test_failed_command_returns_actionable_report(tmp_path: Path) -> None:
    repo, revision = _target_repo(tmp_path)
    step6 = _step6_report(repo, revision)
    request = _request(step6, revision)
    request["commands"]["targeted"][0]["argv"] = [
        sys.executable,
        "-c",
        "raise SystemExit(3)",
    ]

    report = validate_step7(step6, request, repo)

    assert report["status"] == "failed"
    assert report["pr_eligible"] is False
    assert any(
        failure["code"] == "validation_command_failed" for failure in report["failures"]
    )
    assert report["checks"][3]["status"] == "failed"


def test_command_mutation_is_rejected(tmp_path: Path) -> None:
    repo, revision = _target_repo(tmp_path)
    step6 = _step6_report(repo, revision)
    request = _request(step6, revision)
    request["commands"]["targeted"][0]["argv"] = [
        sys.executable,
        "-c",
        "from pathlib import Path; Path('unrelated.txt').write_text('bad')",
    ]

    report = validate_step7(step6, request, repo)

    assert report["status"] == "failed"
    assert any(
        failure["code"] == "unexpected_worktree_changes"
        for failure in report["failures"]
    )


def test_ignored_build_output_does_not_fail_validation(tmp_path: Path) -> None:
    repo, revision = _target_repo(tmp_path)
    step6 = _step6_report(repo, revision)
    request = _request(step6, revision)
    request["commands"]["targeted"][0]["argv"] = [
        sys.executable,
        "-c",
        "from pathlib import Path; Path('.step7-build').mkdir(); Path('.step7-build/result').write_text('ok')",
    ]

    report = validate_step7(step6, request, repo)

    assert report["status"] == "validated"


def test_command_output_limit_is_enforced(tmp_path: Path) -> None:
    repo, revision = _target_repo(tmp_path)
    step6 = _step6_report(repo, revision)
    request = _request(step6, revision)
    request["policy"]["max_output_bytes"] = 16
    request["commands"]["targeted"][0]["argv"] = [
        sys.executable,
        "-c",
        "print('x' * 1000)",
    ]

    report = validate_step7(step6, request, repo)

    assert report["status"] == "failed"
    assert any(
        failure["code"] == "validation_output_limit_exceeded"
        for failure in report["failures"]
    )


def test_validated_report_requires_passed_commands(tmp_path: Path) -> None:
    repo, revision = _target_repo(tmp_path)
    step6 = _step6_report(repo, revision)
    request = _request(step6, revision)
    report = validate_step7(step6, request, repo)
    tampered = deepcopy(report)
    tampered["checks"] = [
        {**row, "status": "not_run", "commands": []} for row in tampered["checks"]
    ]
    tampered.pop("report_sha256")
    tampered["report_sha256"] = artifact_sha256(tampered)

    errors = validate_step7_report(tampered)

    assert any("every check category to pass" in error for error in errors)


def test_generated_file_policy_blocks_declared_generated_path(tmp_path: Path) -> None:
    repo, revision = _target_repo(tmp_path)
    step6 = _step6_report(repo, revision)
    request = _request(step6, revision)
    request["policy"]["generated_file_policy"]["generated_paths"] = [
        step6["patch"]["files"][0]["path"]
    ]

    report = validate_step7(step6, request, repo)

    assert report["status"] == "blocked"
    assert report["failures"][0]["code"] == "generated_file_change_rejected"


def test_unclassified_changed_path_is_blocked(tmp_path: Path) -> None:
    repo, revision = _target_repo(tmp_path)
    step6 = _step6_report(repo, revision)
    request = _request(step6, revision)
    request["policy"]["generated_file_policy"]["source_paths"] = []

    report = validate_step7(step6, request, repo)

    assert report["status"] == "blocked"
    assert report["failures"][0]["code"] == "generated_file_status_unknown"


def test_diff_size_policy_blocks_patch(tmp_path: Path) -> None:
    repo, revision = _target_repo(tmp_path)
    step6 = _step6_report(repo, revision)
    request = _request(step6, revision)
    request["policy"]["diff_limits"]["max_files"] = 1

    report = validate_step7(step6, request, repo)

    assert report["status"] == "failed"
    assert any(
        failure["code"] == "diff_file_limit_exceeded" for failure in report["failures"]
    )


def test_cli_writes_report_and_returns_success(tmp_path: Path) -> None:
    repo, revision = _target_repo(tmp_path)
    step6 = _step6_report(repo, revision)
    request = _request(step6, revision)
    step6_path = tmp_path / "step6.json"
    request_path = tmp_path / "step7.request.json"
    output_path = tmp_path / "step7.report.json"
    step6_path.write_text(json.dumps(step6), encoding="utf-8")
    request_path.write_text(json.dumps(request), encoding="utf-8")

    assert (
        validate_greenfield_step7.main(
            [
                "--step6-report",
                str(step6_path),
                "--request",
                str(request_path),
                "--target-checkout",
                str(repo),
                "--output",
                str(output_path),
            ]
        )
        == 0
    )
    assert validate_step7_report(json.loads(output_path.read_text())) == []
