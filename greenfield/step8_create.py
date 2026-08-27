"""Guarded GitHub branch, commit, and draft-PR creation for Greenfield Step 8."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import subprocess
from collections.abc import Mapping
from typing import Any, Protocol
from urllib.parse import quote

from greenfield.artifact_io import artifact_sha256
from greenfield.step8_contract import (
    REPORT_ANALYSIS_KIND,
    RULE_SET_VERSION,
    SCHEMA_VERSION,
    SHA,
    SHA256,
    Step8Error,
    prepare_step8_request,
    validate_step8_report,
    validate_step8_request,
)


class GitHubApiError(RuntimeError):
    """A GitHub REST request failed without being normalized as evidence."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


COMMIT_IDENTITY = {
    "name": "Greenfield Step 8",
    "email": "greenfield-step8@users.noreply.github.com",
}
GITHUB_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class Step8Authorizer(Protocol):
    """Trusted production boundary that authenticates the exact Step 8 inputs."""

    def authorize(
        self,
        request: Mapping[str, Any],
        step6_report: Mapping[str, Any],
        step7_report: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


class GitHubWriter(Protocol):
    """Minimal injectable GitHub REST surface used by Step 8."""

    def request(
        self,
        method: str,
        endpoint: str,
        body: Mapping[str, Any] | None = None,
    ) -> Any: ...


class RejectingStep8Authorizer:
    """Local/default authorizer: it can never authorize GitHub writes."""

    def authorize(
        self,
        request: Mapping[str, Any],
        step6_report: Mapping[str, Any],
        step7_report: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        del step6_report
        return {
            "authorized": False,
            "reason": "local_authorizer_refuses_github_writes",
            "verifier": {"id": "local-rejecting-authorizer", "version": "0.1"},
            "step7_report_sha256": request["artifacts"]["step7_report_sha256"],
            "validation_fingerprint": step7_report["validation_fingerprint"],
        }


class ValidatedDraftAuthorizer:
    """Authorize draft creation from exact successful Step 7 evidence, not owner approval."""

    def authorize(
        self,
        request: Mapping[str, Any],
        step6_report: Mapping[str, Any],
        step7_report: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        del step6_report
        validated = step7_report.get("status") == "validated"
        evidence_sha = artifact_sha256(step7_report)
        return {
            "authorized": validated,
            **({"reason": "step7_validation_not_passed"} if not validated else {}),
            "verifier": {"id": "greenfield-step7-draft-policy", "version": "0.1"},
            "step7_report_sha256": request["artifacts"]["step7_report_sha256"],
            "validation_fingerprint": step7_report["validation_fingerprint"],
            **(
                {
                    "evidence": {
                        "kind": "validated_step7_report",
                        "id": step7_report["validation_fingerprint"],
                        "sha256": evidence_sha,
                    }
                }
                if validated
                else {}
            ),
        }


class NoWriteGitHubWriter:
    """Sentinel used by local preparation to prove authorization precedes I/O."""

    def request(
        self,
        method: str,
        endpoint: str,
        body: Mapping[str, Any] | None = None,
    ) -> Any:
        del method, endpoint, body
        raise AssertionError("local Step 8 preparation must not call GitHub")


class GhApiWriter:
    """GitHub REST adapter using the authenticated ``gh api`` transport."""

    def request(
        self,
        method: str,
        endpoint: str,
        body: Mapping[str, Any] | None = None,
    ) -> Any:
        command = [
            "gh",
            "api",
            "--hostname",
            "github.com",
            "--method",
            method.upper(),
            endpoint,
        ]
        payload = None
        if body is not None:
            command.extend(["--input", "-"])
            payload = json.dumps(
                body, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
        try:
            result = subprocess.run(
                command,
                input=payload,
                capture_output=True,
                check=False,
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GitHubApiError(f"github_provider_unavailable: {exc}") from exc
        if result.returncode:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            status = 404 if "HTTP 404" in detail else None
            raise GitHubApiError(
                f"github_api_failed: {detail or endpoint}", status=status
            )
        if not result.stdout.strip():
            return {}
        try:
            return json.loads(result.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GitHubApiError(f"github_response_invalid: {endpoint}") from exc


def _failure(code: str, *, remediation: str, observed: Any = None) -> dict[str, Any]:
    row: dict[str, Any] = {"code": code, "remediation": remediation}
    if observed is not None:
        row["observed"] = observed
    return row


def _report(
    request: Mapping[str, Any],
    *,
    status: str,
    mutation_stage: str,
    authorization: Mapping[str, Any],
    failures: list[dict[str, Any]],
    patch_commit_sha: str | None = None,
    pull_request: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    target = request["target"]
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "analysis_kind": REPORT_ANALYSIS_KIND,
        "status": status,
        "mutation_stage": mutation_stage,
        "request_sha256": request["request_sha256"],
        "operation_id": request["operation_id"],
        "artifacts": dict(request["artifacts"]),
        "patch_origin": request["patch_origin"],
        "target": {
            "repository": target["repository"],
            "base_branch": target["base_branch"],
            "base_revision": target["base_revision"],
            "branch": target["branch"],
            "patch_commit_sha": patch_commit_sha,
        },
        "authorization": dict(authorization),
        "pull_request": dict(pull_request) if pull_request is not None else None,
        "human_owner_gate": dict(request["human_owner_gate"]),
        "failures": failures,
        "provenance": {
            "rule_set_version": RULE_SET_VERSION,
            "github_writes": mutation_stage != "none",
            "catalog_mutation": "none",
            "approval": "none",
            "merge": "none",
            "ready_for_review": "none",
        },
    }
    report["report_sha256"] = artifact_sha256(report)
    errors = validate_step8_report(report)
    if errors:
        raise Step8Error("generated invalid Step 8 report: " + "; ".join(errors))
    return report


def _authorization(
    authorizer: Step8Authorizer,
    request: Mapping[str, Any],
    step6: Mapping[str, Any],
    step7: Mapping[str, Any],
) -> dict[str, Any]:
    raw = authorizer.authorize(request, step6, step7)
    if not isinstance(raw, Mapping):
        raise Step8Error("Step 8 authorizer must return an object")
    authorization = dict(raw)
    verifier = authorization.get("verifier")
    if not isinstance(verifier, Mapping):
        raise Step8Error("authorization.verifier must be an object")
    for field in ("id", "version"):
        if not isinstance(verifier.get(field), str) or not verifier[field].strip():
            raise Step8Error(f"authorization.verifier.{field} is required")
    if (
        authorization.get("step7_report_sha256")
        != request["artifacts"]["step7_report_sha256"]
    ):
        raise Step8Error("authorization is not bound to the Step 7 report")
    if authorization.get("validation_fingerprint") != step7["validation_fingerprint"]:
        raise Step8Error("authorization is not bound to the validation fingerprint")
    if authorization.get("authorized") is True:
        evidence = authorization.get("evidence")
        if not isinstance(evidence, Mapping):
            raise Step8Error("authorized decisions require evidence")
        for field in ("kind", "id", "sha256"):
            value = evidence.get(field)
            if not isinstance(value, str) or not value.strip():
                raise Step8Error(f"authorization.evidence.{field} is required")
        if not SHA256.fullmatch(str(evidence["sha256"])):
            raise Step8Error("authorization.evidence.sha256 must be lowercase SHA-256")
    elif authorization.get("authorized") is not False:
        raise Step8Error("authorization.authorized must be a boolean")
    return authorization


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise GitHubApiError(f"{label} must be an object")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise GitHubApiError(f"{label} must be a list")
    return value


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise GitHubApiError(f"{label} must be a lowercase 40-character SHA")
    return value


def _git_blob_sha(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode("ascii")
    return hashlib.sha1(header + content, usedforsecurity=False).hexdigest()


def _tree_entries(value: Any, label: str) -> dict[str, dict[str, Any]]:
    tree = _object(value, label)
    if tree.get("truncated") is True:
        raise GitHubApiError(f"{label} is truncated")
    rows = _list(tree.get("tree"), f"{label}.tree")
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            raise GitHubApiError(f"{label} contains a malformed entry")
        result[row["path"]] = row
    return result


def _optional_ref(github: GitHubWriter, endpoint: str) -> dict[str, Any] | None:
    try:
        return _object(github.request("GET", endpoint), endpoint)
    except GitHubApiError as exc:
        if exc.status == 404:
            return None
        raise


def _ref_sha(value: Mapping[str, Any], label: str) -> str:
    target = value.get("object")
    if not isinstance(target, Mapping):
        raise GitHubApiError(f"{label}.object must be an object")
    return _sha(target.get("sha"), f"{label}.object.sha")


def _verify_base_snapshot(
    github: GitHubWriter,
    repository: str,
    base_revision: str,
    patch_files: list[Mapping[str, Any]],
    target_evidence: Mapping[str, Any],
) -> tuple[str, dict[str, dict[str, Any]], dict[str, str]]:
    commit_endpoint = f"repos/{repository}/git/commits/{base_revision}"
    commit = _object(github.request("GET", commit_endpoint), commit_endpoint)
    if _sha(commit.get("sha"), f"{commit_endpoint}.sha") != base_revision:
        raise GitHubApiError(
            "target commit response does not match target base revision"
        )
    tree = commit.get("tree")
    if not isinstance(tree, Mapping):
        raise GitHubApiError("target commit tree is missing")
    tree_sha = _sha(tree.get("sha"), "target commit tree.sha")
    committer = commit.get("committer")
    if not isinstance(committer, Mapping) or not isinstance(committer.get("date"), str):
        raise GitHubApiError("target commit committer date is missing")
    commit_date = committer["date"]
    if not GITHUB_DATE.fullmatch(commit_date):
        raise GitHubApiError("target commit committer date is not canonical UTC")
    commit_identity = {**COMMIT_IDENTITY, "date": commit_date}
    entries = _tree_entries(
        github.request("GET", f"repos/{repository}/git/trees/{tree_sha}?recursive=1"),
        "target base tree",
    )
    evidence_rows = target_evidence.get("files")
    if not isinstance(evidence_rows, list):
        raise Step8Error("Step 6 target evidence files are missing")
    evidence_by_path = {
        row.get("path"): row for row in evidence_rows if isinstance(row, Mapping)
    }
    for patch in patch_files:
        path = str(patch["path"])
        entry = entries.get(path)
        if (
            not isinstance(entry, Mapping)
            or entry.get("type") != "blob"
            or entry.get("mode") not in {"100644", "100755"}
        ):
            raise Step8Error(
                f"target patch path is not a supported tracked blob: {path}"
            )
        evidence = evidence_by_path.get(path)
        if not isinstance(evidence, Mapping):
            raise Step8Error(f"target evidence is missing for patch path: {path}")
        evidence_blob = evidence.get("blob_or_response_id")
        if not isinstance(evidence_blob, str) or not SHA.fullmatch(evidence_blob):
            raise Step8Error(f"target evidence blob identity is not exact for: {path}")
        if entry.get("sha") != evidence_blob:
            raise Step8Error(f"target evidence blob identity changed for: {path}")
    return tree_sha, entries, commit_identity


def _verify_created_commit(
    commit: Mapping[str, Any],
    *,
    message: str,
    tree_sha: str,
    parent_sha: str,
    identity: Mapping[str, str],
) -> str:
    commit_sha = _sha(commit.get("sha"), "created commit.sha")
    tree = commit.get("tree")
    parents = commit.get("parents")
    if not isinstance(tree, Mapping) or tree.get("sha") != tree_sha:
        raise GitHubApiError("created commit tree does not match the request")
    if (
        not isinstance(parents, list)
        or len(parents) != 1
        or not isinstance(parents[0], Mapping)
        or parents[0].get("sha") != parent_sha
    ):
        raise GitHubApiError("created commit parent does not match the request")
    if commit.get("message") != message:
        raise GitHubApiError("created commit message does not match the request")
    for role in ("author", "committer"):
        value = commit.get(role)
        if not isinstance(value, Mapping) or any(
            value.get(field) != identity[field] for field in ("name", "email", "date")
        ):
            raise GitHubApiError(
                f"created commit {role} identity does not match the request"
            )
    return commit_sha


def _expected_tree(
    base_entries: Mapping[str, Mapping[str, Any]],
    patch_files: list[Mapping[str, Any]],
) -> dict[str, tuple[Any, Any, Any]]:
    result = {
        path: (row.get("mode"), row.get("type"), row.get("sha"))
        for path, row in base_entries.items()
        if row.get("type") != "tree"
    }
    for patch in patch_files:
        path = str(patch["path"])
        mode = base_entries[path]["mode"]
        result[path] = (
            mode,
            "blob",
            _git_blob_sha(str(patch["after"]).encode("utf-8")),
        )
    return result


def _verify_existing_branch(
    github: GitHubWriter,
    repository: str,
    commit_sha: str,
    *,
    base_revision: str,
    operation_id: str,
    expected_tree: Mapping[str, tuple[Any, Any, Any]],
) -> None:
    endpoint = f"repos/{repository}/git/commits/{commit_sha}"
    commit = _object(github.request("GET", endpoint), endpoint)
    parents = commit.get("parents")
    if (
        not isinstance(parents, list)
        or len(parents) != 1
        or not isinstance(parents[0], Mapping)
        or parents[0].get("sha") != base_revision
    ):
        raise Step8Error(
            "existing Step 8 branch does not have the exact target base parent"
        )
    if f"Greenfield-Step8: {operation_id}" not in str(commit.get("message", "")):
        raise Step8Error("existing Step 8 branch commit lacks the operation marker")
    tree = commit.get("tree")
    if not isinstance(tree, Mapping):
        raise GitHubApiError("existing branch commit tree is missing")
    tree_sha = _sha(tree.get("sha"), "existing branch commit tree.sha")
    actual_entries = _tree_entries(
        github.request("GET", f"repos/{repository}/git/trees/{tree_sha}?recursive=1"),
        "existing branch tree",
    )
    actual_tree = {
        path: (row.get("mode"), row.get("type"), row.get("sha"))
        for path, row in actual_entries.items()
        if row.get("type") != "tree"
    }
    if actual_tree != dict(expected_tree):
        raise Step8Error(
            "existing Step 8 branch tree does not match the validated patch"
        )


def _pull_result(
    pull: Mapping[str, Any], *, expected_commit: str, repository: str
) -> dict[str, Any]:
    head = pull.get("head")
    base = pull.get("base")
    if not isinstance(head, Mapping) or not isinstance(base, Mapping):
        raise GitHubApiError("pull request response is missing head/base identity")
    if head.get("sha") != expected_commit:
        raise Step8Error("pull request head does not match the Step 8 commit")
    number = pull.get("number")
    url = pull.get("html_url")
    if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
        raise GitHubApiError("pull request response has no positive number")
    if url != f"https://github.com/{repository}/pull/{number}":
        raise GitHubApiError("pull request response has no canonical GitHub URL")
    if pull.get("draft") is not True:
        raise Step8Error("GitHub did not create or retain the pull request as a draft")
    if pull.get("state") != "open":
        raise Step8Error("the matching Step 8 pull request is not open")
    return {
        "number": number,
        "url": url,
        "draft": True,
        "state": pull.get("state"),
        "head_branch": head.get("ref"),
        "head_sha": head.get("sha"),
        "base_branch": base.get("ref"),
    }


def create_step8(
    step3_report: Mapping[str, Any],
    step4_report: Mapping[str, Any],
    step6_report: Mapping[str, Any],
    step7_report: Mapping[str, Any],
    *,
    base_branch: str,
    authorizer: Step8Authorizer,
    github: GitHubWriter,
) -> dict[str, Any]:
    """Create or recover one idempotent draft test PR after all trust gates pass."""

    request = prepare_step8_request(
        step3_report,
        step4_report,
        step6_report,
        step7_report,
        base_branch=base_branch,
    )
    request_errors = validate_step8_request(request)
    if request_errors:
        raise Step8Error(
            "generated invalid Step 8 request: " + "; ".join(request_errors)
        )
    try:
        authorization = _authorization(authorizer, request, step6_report, step7_report)
    except Step8Error as exc:
        authorization = {
            "authorized": False,
            "reason": "invalid_authorizer_response",
            "verifier": {"id": "invalid-authorizer-response", "version": "0.1"},
            "step7_report_sha256": request["artifacts"]["step7_report_sha256"],
            "validation_fingerprint": step7_report["validation_fingerprint"],
        }
        return _report(
            request,
            status="blocked",
            mutation_stage="none",
            authorization=authorization,
            failures=[
                _failure(
                    "step8_authorization_invalid",
                    remediation="Repair the trusted authorizer and retry with the same artifacts.",
                    observed=str(exc),
                )
            ],
        )
    if authorization["authorized"] is not True:
        return _report(
            request,
            status="blocked",
            mutation_stage="none",
            authorization=authorization,
            failures=[
                _failure(
                    "step8_authorization_rejected",
                    remediation="Run Step 8 through an approved production authorizer.",
                    observed=authorization.get("reason", "authorization rejected"),
                )
            ],
        )

    target = request["target"]
    repository = target["repository"]
    base_revision = target["base_revision"]
    branch = target["branch"]
    patch_files = list(step6_report["patch"]["files"])
    mutation_stage = "none"
    patch_commit_sha: str | None = None
    try:
        base_ref_endpoint = (
            f"repos/{repository}/git/ref/heads/{quote(target['base_branch'], safe='/')}"
        )
        base_ref = _object(github.request("GET", base_ref_endpoint), base_ref_endpoint)
        if _ref_sha(base_ref, "target base ref") != base_revision:
            raise Step8Error(
                "target base branch moved from the validated base revision"
            )
        base_tree_sha, base_entries, commit_identity = _verify_base_snapshot(
            github,
            repository,
            base_revision,
            patch_files,
            step6_report["target_evidence"],
        )
        expected_tree = _expected_tree(base_entries, patch_files)

        pulls_endpoint = (
            f"repos/{repository}/pulls?state=all&head="
            f"{quote(repository.split('/', 1)[0] + ':' + branch, safe='')}&base="
            f"{quote(target['base_branch'], safe='')}&per_page=100"
        )
        pulls = _list(github.request("GET", pulls_endpoint), pulls_endpoint)
        branch_endpoint = f"repos/{repository}/git/ref/heads/{quote(branch, safe='/')}"
        branch_ref = _optional_ref(github, branch_endpoint)
        if len(pulls) > 1:
            raise Step8Error(
                "multiple pull requests match the deterministic Step 8 branch"
            )
        if pulls and branch_ref is None:
            raise Step8Error("an existing Step 8 pull request has no matching branch")
        if branch_ref is not None:
            patch_commit_sha = _ref_sha(branch_ref, "Step 8 branch ref")
            _verify_existing_branch(
                github,
                repository,
                patch_commit_sha,
                base_revision=base_revision,
                operation_id=request["operation_id"],
                expected_tree=expected_tree,
            )
        if pulls:
            pull = _object(pulls[0], "existing pull request")
            if f"<!-- greenfield-step8:{request['operation_id']} -->" not in str(
                pull.get("body", "")
            ):
                raise Step8Error(
                    "existing pull request lacks the Step 8 operation marker"
                )
            if (
                pull.get("title") != request["pr"]["title"]
                or pull.get("body") != request["pr"]["body"]
            ):
                raise Step8Error(
                    "existing pull request content differs from the prepared request"
                )
            result = _pull_result(
                pull, expected_commit=patch_commit_sha, repository=repository
            )
            if (
                result["head_branch"] != branch
                or result["base_branch"] != target["base_branch"]
            ):
                raise Step8Error(
                    "existing pull request branch identity does not match Step 8"
                )
            return _report(
                request,
                status="reused",
                mutation_stage="none",
                authorization=authorization,
                failures=[],
                patch_commit_sha=patch_commit_sha,
                pull_request=result,
            )

        if branch_ref is None:
            tree_rows: list[dict[str, Any]] = []
            for patch in patch_files:
                path = str(patch["path"])
                content = str(patch["after"]).encode("utf-8")
                blob_endpoint = f"repos/{repository}/git/blobs"
                mutation_stage = "blobs"
                blob = _object(
                    github.request(
                        "POST",
                        blob_endpoint,
                        {
                            "content": base64.b64encode(content).decode("ascii"),
                            "encoding": "base64",
                        },
                    ),
                    blob_endpoint,
                )
                blob_sha = _sha(blob.get("sha"), f"blob {path}.sha")
                if blob_sha != _git_blob_sha(content):
                    raise Step8Error(
                        f"GitHub blob SHA does not match prepared content: {path}"
                    )
                tree_rows.append(
                    {
                        "path": path,
                        "mode": base_entries[path]["mode"],
                        "type": "blob",
                        "sha": blob_sha,
                    }
                )
            tree_endpoint = f"repos/{repository}/git/trees"
            mutation_stage = "tree"
            tree = _object(
                github.request(
                    "POST",
                    tree_endpoint,
                    {"base_tree": base_tree_sha, "tree": tree_rows},
                ),
                tree_endpoint,
            )
            tree_sha = _sha(tree.get("sha"), "created tree.sha")
            commit_endpoint = f"repos/{repository}/git/commits"
            commit_message = (
                f"{request['pr']['title']}\n\n"
                f"Greenfield-Step8: {request['operation_id']}"
            )
            mutation_stage = "commit"
            commit = _object(
                github.request(
                    "POST",
                    commit_endpoint,
                    {
                        "message": commit_message,
                        "tree": tree_sha,
                        "parents": [base_revision],
                        "author": commit_identity,
                        "committer": commit_identity,
                    },
                ),
                commit_endpoint,
            )
            patch_commit_sha = _verify_created_commit(
                commit,
                message=commit_message,
                tree_sha=tree_sha,
                parent_sha=base_revision,
                identity=commit_identity,
            )
            ref_endpoint = f"repos/{repository}/git/refs"
            mutation_stage = "ref"
            created_ref = _object(
                github.request(
                    "POST",
                    ref_endpoint,
                    {"ref": f"refs/heads/{branch}", "sha": patch_commit_sha},
                ),
                ref_endpoint,
            )
            if _ref_sha(created_ref, "created Step 8 ref") != patch_commit_sha:
                raise Step8Error(
                    "created Step 8 ref does not point to the patch commit"
                )

        assert patch_commit_sha is not None
        final_base_ref = _object(
            github.request("GET", base_ref_endpoint), base_ref_endpoint
        )
        if _ref_sha(final_base_ref, "final target base ref") != base_revision:
            raise Step8Error(
                "target base branch moved after patch preparation and before PR creation"
            )
        pull_endpoint = f"repos/{repository}/pulls"
        mutation_stage = "pull_request"
        pull = _object(
            github.request(
                "POST",
                pull_endpoint,
                {
                    "title": request["pr"]["title"],
                    "head": branch,
                    "base": target["base_branch"],
                    "body": request["pr"]["body"],
                    "draft": True,
                    "maintainer_can_modify": True,
                },
            ),
            pull_endpoint,
        )
        if (
            pull.get("title") != request["pr"]["title"]
            or pull.get("body") != request["pr"]["body"]
        ):
            raise Step8Error(
                "created pull request content differs from the prepared request"
            )
        result = _pull_result(
            pull, expected_commit=patch_commit_sha, repository=repository
        )
        if (
            result["head_branch"] != branch
            or result["base_branch"] != target["base_branch"]
        ):
            raise Step8Error(
                "created pull request branch identity does not match Step 8"
            )
        return _report(
            request,
            status="created",
            mutation_stage=mutation_stage,
            authorization=authorization,
            failures=[],
            patch_commit_sha=patch_commit_sha,
            pull_request=result,
        )
    except (GitHubApiError, Step8Error) as exc:
        return _report(
            request,
            status="failed" if isinstance(exc, GitHubApiError) else "blocked",
            mutation_stage=mutation_stage,
            authorization=authorization,
            failures=[
                _failure(
                    "github_operation_failed"
                    if isinstance(exc, GitHubApiError)
                    else "step8_safety_check_failed",
                    remediation=(
                        "Inspect the recorded mutation stage and retry with the same artifacts; "
                        "Step 8 never deletes or force-updates refs."
                    ),
                    observed=str(exc),
                )
            ],
            patch_commit_sha=patch_commit_sha,
        )


__all__ = [
    "GhApiWriter",
    "GitHubApiError",
    "GitHubWriter",
    "NoWriteGitHubWriter",
    "RejectingStep8Authorizer",
    "Step8Authorizer",
    "ValidatedDraftAuthorizer",
    "create_step8",
]
