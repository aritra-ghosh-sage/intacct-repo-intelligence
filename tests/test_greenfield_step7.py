from __future__ import annotations

import json
import subprocess
import sys
import time
from copy import deepcopy
from pathlib import Path

import yaml

from greenfield.step6_contract import artifact_sha256 as step6_artifact_sha256
from greenfield.step7_contract import artifact_sha256, validate_step7_report
from greenfield.step7_prepare import build_step7_request
from greenfield.step7_profiles import normalize_profile_registry
from greenfield.step7_runner import LocalSubprocessRunner, runner_attestation
from greenfield.step7_validate import validate_step7
from scripts import validate_greenfield_step7

ROOT = Path(__file__).resolve().parents[1]
STEP6_GOLDEN = ROOT / "examples/greenfield/ia-app-pr-49156/replay/step6.report.json"
TARGET_REPOSITORY = "intacct/ia-restapi-automation-tests"


class AttestedSandboxRunner(LocalSubprocessRunner):
    def attestation(self) -> dict[str, object]:
        return runner_attestation(
            runner_id="test-sandbox",
            version="0.1",
            isolation="sandbox",
            production_eligible=True,
        )


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
    _git(repo, "remote", "add", "origin", f"git@github.com:{TARGET_REPOSITORY}.git")
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


def _step6_report(revision: str) -> dict:
    report = json.loads(STEP6_GOLDEN.read_text(encoding="utf-8"))
    report["eligibility_profile"] = "step7"
    report["target"]["repository"] = TARGET_REPOSITORY
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
        "repository": TARGET_REPOSITORY,
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
            "target_repository": TARGET_REPOSITORY,
            "target_revision": revision,
            "template": report["patch"]["generator"],
            "patch_sha256": report["patch"]["patch_sha256"],
        }
    )
    unsigned = dict(report)
    unsigned.pop("proposal_id")
    report["proposal_id"] = step6_artifact_sha256(unsigned)
    return report


def _registry(*, command_code: str = "assert True", timeout: int = 30) -> dict:
    commands = {
        category: [
            {
                "id": f"{category}-check",
                "argv": [sys.executable, "-c", command_code],
                "cwd": ".",
                "timeout_seconds": timeout,
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
    return normalize_profile_registry(
        {
            "version": 1,
            "profiles": [
                {
                    "profile_id": "rest-step7",
                    "profile_version": "0.1",
                    "repository": TARGET_REPOSITORY,
                    "enabled": True,
                    "required_runner": "sandbox",
                    "commands": commands,
                    "policy": {
                        "diff_limits": {
                            "max_files": 5,
                            "max_added_lines": 20,
                            "max_deleted_lines": 20,
                            "max_bytes": 10000,
                        },
                        "max_output_bytes": 4000,
                        "path_classification": {
                            "source_prefixes": ["features"],
                            "generated_prefixes": [],
                            "allowed_generated_prefixes": [],
                        },
                    },
                }
            ],
        }
    )


def _validate(
    step6: dict,
    registry: dict,
    repo: Path,
    *,
    runner: LocalSubprocessRunner | None = None,
) -> dict:
    request = build_step7_request(step6, registry)
    return validate_step7(
        step6,
        request,
        repo,
        profile_registry=registry,
        runner=runner or LocalSubprocessRunner(),
    )


def test_claimed_sandbox_validation_is_strict_reproducible_but_not_pr_eligible(
    tmp_path: Path,
) -> None:
    repo, revision = _target_repo(tmp_path)
    step6 = _step6_report(revision)
    registry = _registry()
    first = _validate(step6, registry, repo, runner=AttestedSandboxRunner())
    second = _validate(step6, registry, repo, runner=AttestedSandboxRunner())
    assert first["status"] == "validated"
    assert first["pr_eligible"] is False
    assert first["generation_fingerprint"] == second["generation_fingerprint"]
    assert first["validation_fingerprint"] == second["validation_fingerprint"]
    assert validate_step7_report(first) == []
    assert _git(repo, "status", "--porcelain") == ""


def test_local_success_is_validated_but_not_pr_eligible(tmp_path: Path) -> None:
    repo, revision = _target_repo(tmp_path)
    report = _validate(_step6_report(revision), _registry(), repo)
    assert report["status"] == "validated"
    assert report["pr_eligible"] is False
    assert report["runner"]["isolation"] == "local"
    assert validate_step7_report(report) == []


def test_missing_or_wrong_origin_is_blocked(tmp_path: Path) -> None:
    repo, revision = _target_repo(tmp_path)
    step6 = _step6_report(revision)
    registry = _registry()
    _git(repo, "remote", "remove", "origin")
    missing = _validate(step6, registry, repo)
    assert missing["failures"][0]["code"] == "target_repository_unverified"
    _git(repo, "remote", "add", "origin", "https://github.com/intacct/wrong.git")
    wrong = _validate(step6, registry, repo)
    assert wrong["failures"][0]["code"] == "target_repository_mismatch"


def test_dirty_checkout_is_blocked(tmp_path: Path) -> None:
    repo, revision = _target_repo(tmp_path)
    (repo / "unrelated.txt").write_text("dirty\n", encoding="utf-8")
    report = _validate(_step6_report(revision), _registry(), repo)
    assert report["status"] == "blocked"
    assert report["failures"][0]["code"] == "target_checkout_dirty"


def test_failed_command_returns_actionable_report(tmp_path: Path) -> None:
    repo, revision = _target_repo(tmp_path)
    report = _validate(
        _step6_report(revision), _registry(command_code="raise SystemExit(3)"), repo
    )
    assert report["status"] == "failed"
    assert any(row["code"] == "validation_command_failed" for row in report["failures"])


def test_command_mutation_is_rejected(tmp_path: Path) -> None:
    repo, revision = _target_repo(tmp_path)
    code = "from pathlib import Path; Path('unrelated.txt').write_text('bad')"
    report = _validate(_step6_report(revision), _registry(command_code=code), repo)
    assert any(
        row["code"] == "unexpected_worktree_changes" for row in report["failures"]
    )


def test_ignored_build_output_does_not_fail_validation(tmp_path: Path) -> None:
    repo, revision = _target_repo(tmp_path)
    code = (
        "from pathlib import Path; Path('.step7-build').mkdir(exist_ok=True); "
        "Path('.step7-build/result').write_text('ok')"
    )
    report = _validate(_step6_report(revision), _registry(command_code=code), repo)
    assert report["status"] == "validated"


def test_output_limit_is_enforced(tmp_path: Path) -> None:
    repo, revision = _target_repo(tmp_path)
    registry = _registry(command_code="print('x' * 10000)")
    profile = registry["profiles"][0]
    profile["policy"]["max_output_bytes"] = 16
    profile["profile_sha256"] = artifact_sha256(
        {key: value for key, value in profile.items() if key != "profile_sha256"}
    )
    report = _validate(_step6_report(revision), registry, repo)
    assert any(
        row["code"] == "validation_output_limit_exceeded" for row in report["failures"]
    )


def test_profile_request_tampering_is_blocked(tmp_path: Path) -> None:
    repo, revision = _target_repo(tmp_path)
    step6 = _step6_report(revision)
    registry = _registry()
    request = build_step7_request(step6, registry)
    request["commands"]["targeted"][0]["argv"] = [sys.executable, "-c", "assert False"]
    report = validate_step7(
        step6,
        request,
        repo,
        profile_registry=registry,
        runner=LocalSubprocessRunner(),
    )
    assert report["status"] == "blocked"
    assert report["failures"][0]["code"] == "step7_request_profile_mismatch"


def test_symlink_patch_target_is_blocked(tmp_path: Path) -> None:
    repo, _ = _target_repo(tmp_path)
    path = repo / "features/gl/v1-beta2/example.feature"
    content = path.read_text(encoding="utf-8")
    backing = repo / "backing.feature"
    backing.write_text(content, encoding="utf-8")
    path.unlink()
    path.symlink_to("../../../backing.feature")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "symlink target")
    revision = _git(repo, "rev-parse", "HEAD")
    report = _validate(_step6_report(revision), _registry(), repo)
    assert any(
        row["code"] == "patch_path_mode_unsupported" for row in report["failures"]
    )


def test_tampered_runner_attestation_is_rejected(tmp_path: Path) -> None:
    class BadRunner(LocalSubprocessRunner):
        def attestation(self) -> dict[str, object]:
            value = super().attestation()
            value["production_eligible"] = True
            return value

    repo, revision = _target_repo(tmp_path)
    try:
        _validate(_step6_report(revision), _registry(), repo, runner=BadRunner())
    except ValueError as exc:
        assert "attestation fingerprint" in str(exc)
    else:
        raise AssertionError("tampered runner attestation was accepted")


def test_report_tampering_is_rejected(tmp_path: Path) -> None:
    repo, revision = _target_repo(tmp_path)
    report = _validate(_step6_report(revision), _registry(), repo)
    tampered = deepcopy(report)
    tampered["checks"][0]["commands"][0]["status"] = "failed"
    assert validate_step7_report(tampered)


def test_report_cannot_claim_pr_eligibility_with_a_recomputed_hash(
    tmp_path: Path,
) -> None:
    repo, revision = _target_repo(tmp_path)
    report = _validate(
        _step6_report(revision), _registry(), repo, runner=AttestedSandboxRunner()
    )
    tampered = deepcopy(report)
    tampered["pr_eligible"] = True
    unsigned = dict(tampered)
    unsigned.pop("report_sha256")
    tampered["report_sha256"] = artifact_sha256(unsigned)
    assert any("non-PR-eligible" in error for error in validate_step7_report(tampered))


def test_local_runner_terminates_descendants_on_timeout(tmp_path: Path) -> None:
    marker = tmp_path / "late-marker"
    child = (
        "import time; from pathlib import Path; time.sleep(1); "
        f"Path({str(marker)!r}).write_text('bad')"
    )
    parent = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(30)"
    )
    result = LocalSubprocessRunner().run(
        [sys.executable, "-c", parent], cwd=tmp_path, timeout=1, output_limit=1000
    )
    assert result.outcome == "timeout"
    time.sleep(1.2)
    assert not marker.exists()


def test_cli_writes_local_noneligible_report_and_returns_one(tmp_path: Path) -> None:
    repo, revision = _target_repo(tmp_path)
    step6 = _step6_report(revision)
    registry = _registry()
    request = build_step7_request(step6, registry)
    step6_path = tmp_path / "step6.json"
    request_path = tmp_path / "step7.request.json"
    profiles_path = tmp_path / "profiles.yaml"
    output_path = tmp_path / "step7.report.json"
    step6_path.write_text(json.dumps(step6), encoding="utf-8")
    request_path.write_text(json.dumps(request), encoding="utf-8")
    raw_profile = deepcopy(registry)
    raw_profile.pop("registry_sha256")
    for profile in raw_profile["profiles"]:
        profile.pop("profile_sha256")
    profiles_path.write_text(
        yaml.safe_dump(raw_profile, sort_keys=False), encoding="utf-8"
    )
    result = validate_greenfield_step7.main(
        [
            "--step6-report",
            str(step6_path),
            "--request",
            str(request_path),
            "--profiles",
            str(profiles_path),
            "--runner",
            "local",
            "--target-checkout",
            str(repo),
            "--output",
            str(output_path),
        ]
    )
    assert result == 1
    output = json.loads(output_path.read_text(encoding="utf-8"))
    assert output["status"] == "validated"
    assert output["pr_eligible"] is False
