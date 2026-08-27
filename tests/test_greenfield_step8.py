from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from greenfield.artifact_io import artifact_sha256
from greenfield.step8_contract import (
    Step8Error,
    _code_span,
    prepare_step8_request,
    validate_step8_report,
    validate_step8_request,
)
from greenfield.step8_create import (
    GitHubApiError,
    RejectingStep8Authorizer,
    create_step8,
)
from scripts import prepare_greenfield_step8
from tests.test_greenfield_step7 import (
    AttestedSandboxRunner,
    _registry,
    _step6_report,
    _target_repo,
    _validate,
)

ROOT = Path(__file__).resolve().parents[1]
STEP3 = ROOT / "examples/greenfield/ia-app-pr-49156/replay/step3.report.json"
STEP4 = ROOT / "examples/greenfield/ia-app-pr-49156/replay/step4.report.json"


def _git_blob_sha(content: str) -> str:
    payload = content.encode("utf-8")
    header = f"blob {len(payload)}\0".encode("ascii")
    return hashlib.sha1(header + payload, usedforsecurity=False).hexdigest()


def _artifacts(tmp_path: Path) -> tuple[dict, dict, dict, dict]:
    checkout, revision = _target_repo(tmp_path)
    step3 = json.loads(STEP3.read_text(encoding="utf-8"))
    step4 = json.loads(STEP4.read_text(encoding="utf-8"))
    step6 = _step6_report(revision)
    for evidence, patch in zip(
        step6["target_evidence"]["files"], step6["patch"]["files"], strict=True
    ):
        evidence["blob_or_response_id"] = _git_blob_sha(patch["before"])
    unsigned_evidence = dict(step6["target_evidence"])
    unsigned_evidence.pop("evidence_sha256")
    step6["target_evidence"]["evidence_sha256"] = artifact_sha256(unsigned_evidence)
    unsigned = dict(step6)
    unsigned.pop("proposal_id")
    step6["proposal_id"] = artifact_sha256(unsigned)
    registry = _registry()
    step7 = _validate(step6, registry, checkout, runner=AttestedSandboxRunner())
    assert step7["status"] == "validated"
    return step3, step4, step6, step7


def test_draft_request_does_not_require_owner_approvals(tmp_path: Path) -> None:
    checkout, revision = _target_repo(tmp_path)
    step3 = json.loads(STEP3.read_text(encoding="utf-8"))
    step4 = json.loads(STEP4.read_text(encoding="utf-8"))
    step6 = _step6_report(revision)
    step6.pop("approvals")
    unsigned = dict(step6)
    unsigned.pop("proposal_id")
    step6["proposal_id"] = artifact_sha256(unsigned)
    step7 = _validate(step6, _registry(), checkout, runner=AttestedSandboxRunner())
    assert step7["status"] == "validated"
    request = prepare_step8_request(step3, step4, step6, step7, base_branch="main")
    assert request["pr"]["draft"] is True
    assert request["human_owner_gate"]["status"] == "pending"


class TrustedAuthorizer:
    def authorize(self, request: dict, step6: dict, step7: dict) -> dict[str, Any]:
        del step6
        return {
            "authorized": True,
            "verifier": {"id": "production-orchestrator", "version": "1"},
            "step7_report_sha256": request["artifacts"]["step7_report_sha256"],
            "validation_fingerprint": step7["validation_fingerprint"],
            "evidence": {
                "kind": "trusted_orchestrator_decision",
                "id": "authorization-1",
                "sha256": "a" * 64,
            },
        }


class FakeGitHub:
    def __init__(
        self,
        request: dict,
        step6: dict,
        *,
        base_revision: str | None = None,
        existing_commit: str | None = None,
        pulls: list[dict] | None = None,
        fail_method_endpoint: tuple[str, str] | None = None,
    ) -> None:
        self.request_artifact = request
        self.step6 = step6
        self.repository = request["target"]["repository"]
        self.base_revision = base_revision or request["target"]["base_revision"]
        self.base_tree_sha = "b" * 40
        self.created_tree_sha = "c" * 40
        self.created_commit_sha = "d" * 40
        self.existing_commit = existing_commit
        self.pulls = pulls or []
        self.fail_method_endpoint = fail_method_endpoint
        self.calls: list[tuple[str, str, dict | None]] = []
        self.base_entries = [
            {
                "path": row["path"],
                "mode": "100755" if index == 0 else "100644",
                "type": "blob",
                "sha": _git_blob_sha(row["before"]),
            }
            for index, row in enumerate(step6["patch"]["files"])
        ]
        self.base_entries.append(
            {
                "path": "features",
                "mode": "040000",
                "type": "tree",
                "sha": "1" * 40,
            }
        )

    def _pull(self, commit_sha: str) -> dict:
        return {
            "number": 42,
            "html_url": f"https://github.com/{self.repository}/pull/42",
            "draft": True,
            "state": "open",
            "title": self.request_artifact["pr"]["title"],
            "body": self.request_artifact["pr"]["body"],
            "head": {
                "ref": self.request_artifact["target"]["branch"],
                "sha": commit_sha,
            },
            "base": {"ref": self.request_artifact["target"]["base_branch"]},
        }

    def request(self, method: str, endpoint: str, body: dict | None = None) -> Any:
        self.calls.append((method, endpoint, deepcopy(body)))
        if self.fail_method_endpoint == (method, endpoint):
            raise GitHubApiError("injected GitHub failure")
        if method == "GET" and "/git/ref/heads/main" in endpoint:
            return {"object": {"sha": self.base_revision}}
        if method == "GET" and endpoint.endswith(
            f"/git/commits/{self.request_artifact['target']['base_revision']}"
        ):
            return {
                "sha": self.request_artifact["target"]["base_revision"],
                "tree": {"sha": self.base_tree_sha},
                "committer": {"date": "2026-08-20T12:34:56Z"},
            }
        if method == "GET" and endpoint.endswith(
            f"/git/trees/{self.base_tree_sha}?recursive=1"
        ):
            return {"truncated": False, "tree": self.base_entries}
        if method == "GET" and "/pulls?state=all" in endpoint:
            return self.pulls
        if method == "GET" and "/git/ref/heads/strands/" in endpoint:
            if self.existing_commit is None:
                raise GitHubApiError("not found", status=404)
            return {"object": {"sha": self.existing_commit}}
        if (
            method == "GET"
            and self.existing_commit is not None
            and endpoint.endswith(f"/git/commits/{self.existing_commit}")
        ):
            return {
                "sha": self.existing_commit,
                "message": (
                    "test commit\n\n"
                    f"Greenfield-Step8: {self.request_artifact['operation_id']}"
                ),
                "parents": [{"sha": self.request_artifact["target"]["base_revision"]}],
                "tree": {"sha": self.created_tree_sha},
            }
        if method == "GET" and endpoint.endswith(
            f"/git/trees/{self.created_tree_sha}?recursive=1"
        ):
            changed = []
            after_by_path = {
                row["path"]: row["after"] for row in self.step6["patch"]["files"]
            }
            for row in self.base_entries:
                updated = dict(row)
                updated["sha"] = (
                    "2" * 40
                    if row["type"] == "tree"
                    else _git_blob_sha(after_by_path[row["path"]])
                )
                changed.append(updated)
            return {"truncated": False, "tree": changed}
        if method == "POST" and endpoint.endswith("/git/blobs"):
            assert body is not None
            content = base64_decode(body["content"])
            return {"sha": _git_blob_sha(content.decode("utf-8"))}
        if method == "POST" and endpoint.endswith("/git/trees"):
            return {"sha": self.created_tree_sha}
        if method == "POST" and endpoint.endswith("/git/commits"):
            assert body is not None
            return {
                "sha": self.created_commit_sha,
                "message": body["message"],
                "tree": {"sha": body["tree"]},
                "parents": [{"sha": body["parents"][0]}],
                "author": body["author"],
                "committer": body["committer"],
            }
        if method == "POST" and endpoint.endswith("/git/refs"):
            return {"object": {"sha": self.created_commit_sha}}
        if method == "POST" and endpoint.endswith("/pulls"):
            commit = self.existing_commit or self.created_commit_sha
            return self._pull(commit)
        raise AssertionError(f"unexpected GitHub call: {method} {endpoint}")


def base64_decode(value: str) -> bytes:
    import base64

    return base64.b64decode(value, validate=True)


def test_prepare_request_renders_required_draft_pr_evidence(tmp_path: Path) -> None:
    step3, step4, step6, step7 = _artifacts(tmp_path)
    request = prepare_step8_request(step3, step4, step6, step7, base_branch="main")

    assert request["target"]["branch"].startswith("strands/greenfield-")
    assert request["pr"]["draft"] is True
    assert request["patch_origin"] == "template_generated"
    assert request["human_owner_gate"]["status"] == "pending"
    assert validate_step8_request(request) == []
    body = request["pr"]["body"]
    for text in (
        "Source PR",
        "Source base commit",
        "Source head commit",
        "Impacted contract or interface",
        "Tests added or changed",
        "Validation commands and results",
        "Remaining uncertainty",
        "Evidence links",
        "Patch origin: `template-generated`",
        "Human owner gate",
    ):
        assert text in body
    assert step6["justification"]["interface_id"] in body
    assert all(row["path"] in body for row in step6["patch"]["files"])


def test_code_spans_cannot_be_terminated_by_evidence_backticks() -> None:
    assert _code_span("path` **not emphasis**") == "`` path` **not emphasis** ``"
    assert _code_span("two``ticks") == "``` two``ticks ```"


def test_local_authorizer_blocks_before_any_github_access(tmp_path: Path) -> None:
    step3, step4, step6, step7 = _artifacts(tmp_path)

    class NoCalls:
        def request(self, method: str, endpoint: str, body: dict | None = None) -> Any:
            raise AssertionError((method, endpoint, body))

    report = create_step8(
        step3,
        step4,
        step6,
        step7,
        base_branch="main",
        authorizer=RejectingStep8Authorizer(),
        github=NoCalls(),
    )
    assert report["status"] == "blocked"
    assert report["mutation_stage"] == "none"
    assert report["authorization"]["authorized"] is False
    assert validate_step8_report(report) == []


def test_forged_authorization_is_blocked_before_github_access(tmp_path: Path) -> None:
    step3, step4, step6, step7 = _artifacts(tmp_path)

    class ForgedAuthorizer(TrustedAuthorizer):
        def authorize(self, request: dict, step6: dict, step7: dict) -> dict[str, Any]:
            value = super().authorize(request, step6, step7)
            value["validation_fingerprint"] = "f" * 64
            return value

    class NoCalls:
        def request(self, method: str, endpoint: str, body: dict | None = None) -> Any:
            raise AssertionError((method, endpoint, body))

    report = create_step8(
        step3,
        step4,
        step6,
        step7,
        base_branch="main",
        authorizer=ForgedAuthorizer(),
        github=NoCalls(),
    )
    assert report["status"] == "blocked"
    assert report["failures"][0]["code"] == "step8_authorization_invalid"


def test_creates_exact_commit_branch_and_draft_pr(tmp_path: Path) -> None:
    step3, step4, step6, step7 = _artifacts(tmp_path)
    request = prepare_step8_request(step3, step4, step6, step7, base_branch="main")
    github = FakeGitHub(request, step6)

    report = create_step8(
        step3,
        step4,
        step6,
        step7,
        base_branch="main",
        authorizer=TrustedAuthorizer(),
        github=github,
    )

    assert report["status"] == "created"
    assert report["pull_request"]["draft"] is True
    assert report["human_owner_gate"]["status"] == "pending"
    assert validate_step8_report(report) == []
    methods_and_endpoints = [(method, endpoint) for method, endpoint, _ in github.calls]
    assert not any(
        token in endpoint
        for _, endpoint in methods_and_endpoints
        for token in ("merge", "reviews", "ready_for_review")
    )
    tree_call = next(
        body
        for method, endpoint, body in github.calls
        if method == "POST" and endpoint.endswith("/git/trees")
    )
    assert tree_call["base_tree"] == github.base_tree_sha
    assert [row["mode"] for row in tree_call["tree"]] == ["100755", "100644"]
    commit_call = next(
        body
        for method, endpoint, body in github.calls
        if method == "POST" and endpoint.endswith("/git/commits")
    )
    assert commit_call["parents"] == [step6["target"]["base_revision"]]
    expected_identity = {
        "name": "Greenfield Step 8",
        "email": "greenfield-step8@users.noreply.github.com",
        "date": "2026-08-20T12:34:56Z",
    }
    assert commit_call["author"] == expected_identity
    assert commit_call["committer"] == expected_identity
    pull_call = next(
        body
        for method, endpoint, body in github.calls
        if method == "POST" and endpoint.endswith("/pulls")
    )
    assert pull_call["draft"] is True


def test_base_branch_drift_blocks_without_writes(tmp_path: Path) -> None:
    step3, step4, step6, step7 = _artifacts(tmp_path)
    request = prepare_step8_request(step3, step4, step6, step7, base_branch="main")
    github = FakeGitHub(request, step6, base_revision="f" * 40)
    report = create_step8(
        step3,
        step4,
        step6,
        step7,
        base_branch="main",
        authorizer=TrustedAuthorizer(),
        github=github,
    )
    assert report["status"] == "blocked"
    assert report["mutation_stage"] == "none"
    assert all(method == "GET" for method, _, _ in github.calls)


def test_existing_exact_draft_is_reused_without_writes(tmp_path: Path) -> None:
    step3, step4, step6, step7 = _artifacts(tmp_path)
    request = prepare_step8_request(step3, step4, step6, step7, base_branch="main")
    commit = "e" * 40
    github = FakeGitHub(request, step6, existing_commit=commit)
    github.pulls = [github._pull(commit)]
    report = create_step8(
        step3,
        step4,
        step6,
        step7,
        base_branch="main",
        authorizer=TrustedAuthorizer(),
        github=github,
    )
    assert report["status"] == "reused"
    assert report["mutation_stage"] == "none"
    assert all(method == "GET" for method, _, _ in github.calls)


def test_branch_only_retry_creates_only_the_draft_pr(tmp_path: Path) -> None:
    step3, step4, step6, step7 = _artifacts(tmp_path)
    request = prepare_step8_request(step3, step4, step6, step7, base_branch="main")
    github = FakeGitHub(request, step6, existing_commit="e" * 40)
    report = create_step8(
        step3,
        step4,
        step6,
        step7,
        base_branch="main",
        authorizer=TrustedAuthorizer(),
        github=github,
    )
    posts = [
        (endpoint, body) for method, endpoint, body in github.calls if method == "POST"
    ]
    assert report["status"] == "created"
    assert len(posts) == 1 and posts[0][0].endswith("/pulls")


def test_conflicting_existing_pr_is_blocked(tmp_path: Path) -> None:
    step3, step4, step6, step7 = _artifacts(tmp_path)
    request = prepare_step8_request(step3, step4, step6, step7, base_branch="main")
    commit = "e" * 40
    github = FakeGitHub(request, step6, existing_commit=commit)
    conflict = github._pull(commit)
    conflict["body"] = "unrelated"
    github.pulls = [conflict]
    report = create_step8(
        step3,
        step4,
        step6,
        step7,
        base_branch="main",
        authorizer=TrustedAuthorizer(),
        github=github,
    )
    assert report["status"] == "blocked"
    assert report["mutation_stage"] == "none"


def test_closed_existing_draft_is_blocked(tmp_path: Path) -> None:
    step3, step4, step6, step7 = _artifacts(tmp_path)
    request = prepare_step8_request(step3, step4, step6, step7, base_branch="main")
    commit = "e" * 40
    github = FakeGitHub(request, step6, existing_commit=commit)
    closed = github._pull(commit)
    closed["state"] = "closed"
    github.pulls = [closed]
    report = create_step8(
        step3,
        step4,
        step6,
        step7,
        base_branch="main",
        authorizer=TrustedAuthorizer(),
        github=github,
    )
    assert report["status"] == "blocked"
    assert report["mutation_stage"] == "none"


def test_pr_failure_records_pull_request_attempt_without_cleanup(
    tmp_path: Path,
) -> None:
    step3, step4, step6, step7 = _artifacts(tmp_path)
    request = prepare_step8_request(step3, step4, step6, step7, base_branch="main")
    github = FakeGitHub(
        request,
        step6,
        fail_method_endpoint=("POST", f"repos/{request['target']['repository']}/pulls"),
    )
    report = create_step8(
        step3,
        step4,
        step6,
        step7,
        base_branch="main",
        authorizer=TrustedAuthorizer(),
        github=github,
    )
    assert report["status"] == "failed"
    assert report["mutation_stage"] == "pull_request"
    assert report["provenance"]["github_writes"] is True
    assert report["target"]["patch_commit_sha"] == github.created_commit_sha
    assert not any(
        method in {"DELETE", "PATCH", "PUT"} for method, _, _ in github.calls
    )


@pytest.mark.parametrize(
    ("endpoint_suffix", "expected_stage"),
    [
        ("/git/blobs", "blobs"),
        ("/git/trees", "tree"),
        ("/git/commits", "commit"),
        ("/git/refs", "ref"),
        ("/pulls", "pull_request"),
    ],
)
def test_each_mutation_failure_records_attempted_stage(
    tmp_path: Path, endpoint_suffix: str, expected_stage: str
) -> None:
    step3, step4, step6, step7 = _artifacts(tmp_path)
    request = prepare_step8_request(step3, step4, step6, step7, base_branch="main")
    endpoint = f"repos/{request['target']['repository']}{endpoint_suffix}"
    github = FakeGitHub(
        request,
        step6,
        fail_method_endpoint=("POST", endpoint),
    )
    report = create_step8(
        step3,
        step4,
        step6,
        step7,
        base_branch="main",
        authorizer=TrustedAuthorizer(),
        github=github,
    )
    assert report["status"] == "failed"
    assert report["mutation_stage"] == expected_stage
    assert report["provenance"]["github_writes"] is True
    assert not any(
        method in {"DELETE", "PATCH", "PUT"} for method, _, _ in github.calls
    )


def test_report_validator_rejects_forged_authorization_and_provenance(
    tmp_path: Path,
) -> None:
    step3, step4, step6, step7 = _artifacts(tmp_path)
    request = prepare_step8_request(step3, step4, step6, step7, base_branch="main")
    report = create_step8(
        step3,
        step4,
        step6,
        step7,
        base_branch="main",
        authorizer=TrustedAuthorizer(),
        github=FakeGitHub(request, step6),
    )

    def resigned(**changes: Any) -> dict[str, Any]:
        candidate = deepcopy(report)
        for section, values in changes.items():
            candidate[section].update(values)
        unsigned = dict(candidate)
        unsigned.pop("report_sha256")
        candidate["report_sha256"] = artifact_sha256(unsigned)
        return candidate

    missing_evidence = deepcopy(report)
    missing_evidence["authorization"].pop("evidence")
    unsigned = dict(missing_evidence)
    unsigned.pop("report_sha256")
    missing_evidence["report_sha256"] = artifact_sha256(unsigned)
    assert validate_step8_report(missing_evidence)
    assert validate_step8_report(
        resigned(authorization={"step7_report_sha256": "0" * 64})
    )
    assert validate_step8_report(resigned(provenance={"github_writes": False}))
    assert validate_step8_report(resigned(provenance={"ready_for_review": "performed"}))
    assert validate_step8_report(resigned(provenance={"rule_set_version": "forged"}))


def test_tampered_step7_and_ai_origin_fail_closed(tmp_path: Path) -> None:
    step3, step4, step6, step7 = _artifacts(tmp_path)
    tampered = deepcopy(step7)
    tampered["checks"][0]["status"] = "failed"
    with pytest.raises(Step8Error, match="invalid Step 7 report"):
        prepare_step8_request(step3, step4, step6, tampered, base_branch="main")

    ai = deepcopy(step6)
    ai["reason"] = "ai_proposed_patch"
    unsigned = dict(ai)
    unsigned.pop("proposal_id")
    ai["proposal_id"] = artifact_sha256(unsigned)
    with pytest.raises(Step8Error):
        prepare_step8_request(step3, step4, ai, step7, base_branch="main")


def test_local_cli_writes_request_and_blocked_result(tmp_path: Path) -> None:
    step3, step4, step6, step7 = _artifacts(tmp_path)
    inputs = {}
    for name, value in (
        ("step3", step3),
        ("step4", step4),
        ("step6", step6),
        ("step7", step7),
    ):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        inputs[name] = path
    request_output = tmp_path / "step8.request.json"
    result_output = tmp_path / "step8.result.json"

    result = prepare_greenfield_step8.main(
        [
            "--step3-report",
            str(inputs["step3"]),
            "--step4-report",
            str(inputs["step4"]),
            "--step6-report",
            str(inputs["step6"]),
            "--step7-report",
            str(inputs["step7"]),
            "--base-branch",
            "main",
            "--request-output",
            str(request_output),
            "--result-output",
            str(result_output),
        ]
    )
    assert result == 1
    assert validate_step8_request(json.loads(request_output.read_text())) == []
    blocked = json.loads(result_output.read_text())
    assert blocked["status"] == "blocked"
    assert blocked["provenance"]["github_writes"] is False
