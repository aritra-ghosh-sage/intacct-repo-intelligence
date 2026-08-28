"""Contracts and deterministic preparation for Greenfield PR-impact Step 8."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote, urlparse

from greenfield.artifact_io import artifact_sha256
from greenfield.step4_contract import validate_step4_report
from greenfield.step6_contract import validate_step6_report
from greenfield.step7_contract import validate_step7_report
from scripts.validate_greenfield_step3 import validate as validate_step3_report

SCHEMA_VERSION = "0.1"
REQUEST_ANALYSIS_KIND = "greenfield_pr_impact_step_8_request"
REPORT_ANALYSIS_KIND = "greenfield_pr_impact_step_8"
RULE_SET_VERSION = "0.1"
REPORT_STATUSES = {"created", "reused", "blocked", "failed"}
MUTATION_STAGES = {"none", "blobs", "tree", "commit", "ref", "pull_request"}
SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
GITHUB_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
UNSUPPORTED_COVERAGE = {"candidate", "stale", "missing", "unavailable", "unknown"}


class Step8Error(ValueError):
    """Raised when a Step 8 artifact or operation is unsafe or malformed."""


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Step8Error(f"{label} must be a non-empty string")
    return value.strip()


def _sha(value: Any, label: str) -> str:
    result = _text(value, label).lower()
    if not SHA.fullmatch(result):
        raise Step8Error(f"{label} must be a lowercase 40-character SHA")
    return result


def _sha256(value: Any, label: str) -> str:
    result = _text(value, label).lower()
    if not SHA256.fullmatch(result):
        raise Step8Error(f"{label} must be a lowercase SHA-256")
    return result


def _branch(value: Any, label: str) -> str:
    result = _text(value, label)
    invalid = (
        not BRANCH.fullmatch(result)
        or ".." in result
        or "@{" in result
        or result.endswith(("/", "."))
        or "//" in result
        or any(
            part.startswith(".") or part.endswith(".lock") for part in result.split("/")
        )
    )
    if invalid:
        raise Step8Error(f"{label} must be a safe Git branch name")
    return result


def _github_repository(value: Any, label: str) -> str:
    result = _text(value, label)
    if not GITHUB_REPOSITORY.fullmatch(result):
        raise Step8Error(f"{label} must be a GitHub owner/repository identity")
    return result


def _source_pr_identity(source: Mapping[str, Any]) -> tuple[str, str]:
    url = _text(source.get("pr_url"), "source.pr_url")
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    if (
        parsed.scheme != "https"
        or parsed.netloc.lower() != "github.com"
        or parsed.query
        or parsed.fragment
        or len(parts) != 4
        or parts[2] != "pull"
        or not parts[3].isdigit()
        or int(parts[3]) != source.get("pr_number")
    ):
        raise Step8Error("source.pr_url must canonically match source.pr_number")
    repository = f"{parts[0]}/{parts[1]}"
    _github_repository(repository, "source PR repository")
    if url != f"https://github.com/{repository}/pull/{source['pr_number']}":
        raise Step8Error("source.pr_url must be canonical")
    return repository, url


def _markdown(value: Any) -> str:
    text = " ".join(str(value).split())
    for character in "\\`*_[]<>#":
        text = text.replace(character, f"\\{character}")
    return text


def _code_span(value: Any) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ")
    longest_run = max((len(run) for run in re.findall(r"`+", text)), default=0)
    delimiter = "`" * (longest_run + 1)
    return f"{delimiter} {text} {delimiter}"


def _github_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlparse(value.strip())
    if (
        parsed.scheme != "https"
        or parsed.netloc.lower() != "github.com"
        or not parsed.path.strip("/")
        or any(character in value for character in "()<> \t\r\n")
    ):
        return None
    return parsed.geturl()


def _walk_evidence(value: Any) -> tuple[list[str], list[str]]:
    links: set[str] = set()
    references: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, Mapping):
            for key in sorted(item):
                cell = item[key]
                if key in {"url", "html_url", "pr_url"}:
                    link = _github_url(cell)
                    if link is not None:
                        links.add(link)
                if key in {
                    "path",
                    "id",
                    "evidence_id",
                    "record_id",
                    "sha256",
                    "response_sha256",
                } and isinstance(cell, (str, int)):
                    references.add(f"{key}={cell}")
                visit(cell)
        elif isinstance(item, list):
            for cell in item:
                visit(cell)

    visit(value)
    return sorted(links), sorted(references)[:100]


def _uncertainties(step3: Mapping[str, Any], step4: Mapping[str, Any]) -> list[str]:
    values = {
        str(item)
        for report in (step3, step4)
        for key in ("gaps", "warnings")
        for item in report.get(key, [])
        if isinstance(item, str) and item.strip()
    }
    coverage = step4.get("coverage", {})
    rows = coverage.get("items", []) if isinstance(coverage, Mapping) else []
    for row in rows:
        if (
            not isinstance(row, Mapping)
            or row.get("classification") not in UNSUPPORTED_COVERAGE
        ):
            continue
        values.add(
            "coverage:"
            + ":".join(
                str(row.get(field) or "unknown")
                for field in (
                    "classification",
                    "target_repository",
                    "interface_id",
                    "reason",
                )
            )
        )
    values.add(
        "Step 7 validates the declared patch and checks; it does not prove complete impact, "
        "complete test discovery, runtime business coverage, or merge readiness."
    )
    return sorted(values)


def _render_pr_body(
    step3: Mapping[str, Any],
    step4: Mapping[str, Any],
    step6: Mapping[str, Any],
    step7: Mapping[str, Any],
    *,
    base_branch: str,
    operation_id: str,
) -> str:
    source = step6["source"]
    target = step6["target"]
    justification = step6["justification"]
    source_repository, source_pr_url = _source_pr_identity(source)
    target_repository = _github_repository(target["repository"], "target.repository")
    source_root = f"https://github.com/{source_repository}"
    target_root = f"https://github.com/{target_repository}"
    patch_rows = step6["patch"]["files"]
    evidence_links, evidence_references = _walk_evidence(
        [justification.get("evidence", []), step3, step4]
    )
    evidence_links = sorted(
        {
            source_pr_url,
            f"{source_root}/commit/{source['base_revision']}",
            f"{source_root}/commit/{source['head_revision']}",
            f"{target_root}/commit/{target['base_revision']}",
            *(
                f"{target_root}/blob/{target['base_revision']}/{quote(str(row['path']), safe='/')}"
                for row in patch_rows
            ),
            *evidence_links,
        }
    )
    patch_origin = (
        "strands-tool-guided"
        if step6.get("reason") == "strands_tool_guided_patch"
        else "template-generated"
    )
    lines = [
        "## Source change",
        "",
        f"- Source PR: [{source_repository}#{source['pr_number']}]({source_pr_url})",
        f"- Source base commit: [`{source['base_revision']}`]({source_root}/commit/{source['base_revision']})",
        f"- Source head commit: [`{source['head_revision']}`]({source_root}/commit/{source['head_revision']})",
        f"- Target base: {_code_span(f'{target_repository}:{base_branch}@{target["base_revision"]}')}",
        "",
        "## Impact and justification",
        "",
        f"- Impacted contract or interface: {_code_span(justification['interface_id'])}",
        f"- Required because: {_code_span(justification['trigger'])}",
        f"- Step 5 action: {_code_span(justification['step5_action_id'])}",
        f"- Test obligation: {_code_span(justification['test_id'])} at {_code_span(justification['test_path'])}",
        f"- Patch origin: `{patch_origin}`",
        f"- Generator: {_code_span(f'{step6["patch"]["generator"]["id"]}@{step6["patch"]["generator"]["version"]}')}",
        "",
        "## Tests added or changed",
        "",
    ]
    lines.extend(f"- Changed: {_code_span(row['path'])}" for row in patch_rows)
    lines.extend(["", "## Validation commands and results", ""])
    for check in step7["checks"]:
        lines.append(
            f"### {_markdown(check['category'])}: {_markdown(check['status'])}"
        )
        lines.append("")
        for command in check["commands"]:
            argv = " ".join(str(item) for item in command["argv"])
            exit_code = command.get("exit_code", "not-recorded")
            lines.append(
                f"- {_code_span(argv)} — **{_markdown(command['status'])}**, "
                f"exit {_code_span(exit_code)}, "
                f"stdout {_code_span(command.get('stdout_sha256', 'not-recorded'))}, "
                f"stderr {_code_span(command.get('stderr_sha256', 'not-recorded'))}"
            )
        lines.append("")
    lines.extend(["## Remaining uncertainty", ""])
    lines.extend(f"- {_markdown(value)}" for value in _uncertainties(step3, step4))
    lines.extend(["", "## Evidence links", ""])
    lines.extend(f"- [{_markdown(value)}]({value})" for value in evidence_links)
    lines.extend(["", "## Evidence references", ""])
    if evidence_references:
        lines.extend(f"- {_code_span(value)}" for value in evidence_references)
    else:
        lines.append("- No additional non-link evidence references were recorded.")
    lines.extend(
        [
            "",
            "## Human owner gate",
            "",
            (
                "This PR is intentionally a draft. Earlier source-interface and consumer-test-owner "
                "approvals authorized generation and validation only. A human owner of this test "
                "repository must approve the transition before it becomes ready for review or merge."
            ),
            "",
            f"<!-- greenfield-step8:{operation_id} -->",
        ]
    )
    body = "\n".join(lines) + "\n"
    if len(body.encode("utf-8")) > 60_000:
        raise Step8Error("rendered PR body exceeds the 60000-byte Step 8 limit")
    return body


def prepare_step8_request(
    step3: Mapping[str, Any],
    step4: Mapping[str, Any],
    step6: Mapping[str, Any],
    step7: Mapping[str, Any],
    *,
    base_branch: str,
) -> dict[str, Any]:
    """Validate and bind the exact artifacts needed for a future Step 8 write."""

    validations = (
        ("Step 3", validate_step3_report(step3)),
        ("Step 4", validate_step4_report(step4)),
        (
            "Step 6",
            validate_step6_report(
                step6,
                strict_target_evidence=True,
                require_step7_eligibility=True,
            ),
        ),
        ("Step 7", validate_step7_report(step7)),
    )
    for label, errors in validations:
        if errors:
            raise Step8Error(f"invalid {label} report: " + "; ".join(errors))
    if step6.get("status") != "ready_for_ai_pr":
        raise Step8Error("Step 6 status must be ready_for_ai_pr")
    if step7.get("status") != "validated":
        raise Step8Error("Step 7 status must be validated")
    runner = step7.get("runner", {})
    if (
        runner.get("isolation") != "sandbox"
        or runner.get("production_eligible") is not True
    ):
        raise Step8Error("Step 7 requires a production-eligible sandbox runner")
    if step7.get("pr_eligible") is not False:
        raise Step8Error("Step 7 must preserve its non-PR-eligible contract")

    step3_hash = artifact_sha256(step3)
    step4_hash = artifact_sha256(step4)
    step6_hash = artifact_sha256(step6)
    step7_hash = artifact_sha256(step7)
    if step6["provenance"].get("step3_report_sha256") != step3_hash:
        raise Step8Error("Step 3 report does not match Step 6 provenance")
    if step6["provenance"].get("step4_report_sha256") != step4_hash:
        raise Step8Error("Step 4 report does not match Step 6 provenance")
    if step4["provenance"].get("step3_report_sha256") != step3_hash:
        raise Step8Error("Step 3 report does not match Step 4 provenance")
    if step7.get("step6_report_sha256") != step6_hash:
        raise Step8Error("Step 6 report does not match Step 7 provenance")
    if step7.get("target") != {
        "repository": step6["target"]["repository"],
        "base_revision": step6["target"]["base_revision"],
    }:
        raise Step8Error("Step 7 target does not match Step 6 target")
    if (
        step7["patch"].get("patch_sha256") != step6["patch"].get("patch_sha256")
        or step7["patch"].get("generator_id") != step6["patch"]["generator"].get("id")
        or step7["patch"].get("generator_version")
        != step6["patch"]["generator"].get("version")
        or step7["patch"].get("paths")
        != sorted(row["path"] for row in step6["patch"]["files"])
    ):
        raise Step8Error("Step 7 patch identity does not match Step 6")
    if any(
        str(row["path"]).startswith(".github/workflows/")
        for row in step6["patch"]["files"]
    ):
        raise Step8Error("Step 8 v1 does not modify GitHub workflow files")
    patch_origins = {
        "deterministic_template_generated_patch": "template_generated",
        "strands_tool_guided_patch": "strands_tool_guided",
    }
    patch_origin = patch_origins.get(str(step6.get("reason")))
    if patch_origin is None:
        raise Step8Error("Step 8 accepts only validated bounded patches")

    target_repository = _github_repository(
        step6["target"]["repository"], "target.repository"
    )
    normalized_base_branch = _branch(base_branch, "base_branch")
    _source_pr_identity(step6["source"])
    artifacts = {
        "step3_report_sha256": step3_hash,
        "step4_report_sha256": step4_hash,
        "step6_report_sha256": step6_hash,
        "step7_report_sha256": step7_hash,
    }
    operation_id = artifact_sha256(
        {
            "artifacts": artifacts,
            "base_branch": normalized_base_branch,
            "idempotency_key": step6["idempotency_key"],
            "target_repository": target_repository.lower(),
            "target_revision": step6["target"]["base_revision"],
        }
    )
    branch = f"strands/greenfield-{operation_id[:16]}"
    title = " ".join(_text(step6["pr_request"]["title"], "pr_request.title").split())
    if len(title) > 256:
        raise Step8Error("pr_request.title exceeds the 256-character Step 8 limit")
    body = _render_pr_body(
        step3,
        step4,
        step6,
        step7,
        base_branch=normalized_base_branch,
        operation_id=operation_id,
    )
    request: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "analysis_kind": REQUEST_ANALYSIS_KIND,
        "operation_id": operation_id,
        "idempotency_key": step6["idempotency_key"],
        "artifacts": artifacts,
        "target": {
            "repository": target_repository,
            "base_branch": normalized_base_branch,
            "base_revision": _sha(
                step6["target"]["base_revision"], "target.base_revision"
            ),
            "branch": branch,
        },
        "patch_origin": patch_origin,
        "pr": {
            "draft": True,
            "title": title,
            "body": body,
            "body_sha256": artifact_sha256(body),
        },
        "human_owner_gate": {
            "status": "pending",
            "required_role": "consumer_test_owner",
        },
        "provenance": {
            "rule_set_version": RULE_SET_VERSION,
            "github_writes": "not_performed",
            "catalog_mutation": "none",
        },
    }
    request["request_sha256"] = artifact_sha256(request)
    return request


def validate_step8_request(request: Any) -> list[str]:
    try:
        if not isinstance(request, Mapping):
            raise Step8Error("request must be an object")
        if request.get("schema_version") != SCHEMA_VERSION:
            raise Step8Error(f"schema_version must be {SCHEMA_VERSION}")
        if request.get("analysis_kind") != REQUEST_ANALYSIS_KIND:
            raise Step8Error("analysis_kind is invalid")
        _sha256(request.get("operation_id"), "operation_id")
        _sha256(request.get("idempotency_key"), "idempotency_key")
        _sha256(request.get("request_sha256"), "request_sha256")
        unsigned = dict(request)
        unsigned.pop("request_sha256", None)
        if artifact_sha256(unsigned) != request["request_sha256"]:
            raise Step8Error("request_sha256 does not match request contents")
        artifacts = request.get("artifacts")
        if not isinstance(artifacts, Mapping):
            raise Step8Error("artifacts must be an object")
        for field in (
            "step3_report_sha256",
            "step4_report_sha256",
            "step6_report_sha256",
            "step7_report_sha256",
        ):
            _sha256(artifacts.get(field), f"artifacts.{field}")
        target = request.get("target")
        if not isinstance(target, Mapping):
            raise Step8Error("target must be an object")
        _github_repository(target.get("repository"), "target.repository")
        _branch(target.get("base_branch"), "target.base_branch")
        _branch(target.get("branch"), "target.branch")
        _sha(target.get("base_revision"), "target.base_revision")
        expected_operation_id = artifact_sha256(
            {
                "artifacts": dict(artifacts),
                "base_branch": target["base_branch"],
                "idempotency_key": request["idempotency_key"],
                "target_repository": str(target["repository"]).lower(),
                "target_revision": target["base_revision"],
            }
        )
        if request["operation_id"] != expected_operation_id:
            raise Step8Error("operation_id does not match request inputs")
        if target["branch"] != f"strands/greenfield-{request['operation_id'][:16]}":
            raise Step8Error("target.branch does not match operation_id")
        if request.get("patch_origin") not in {
            "template_generated",
            "strands_tool_guided",
        }:
            raise Step8Error("patch_origin is invalid")
        pr = request.get("pr")
        if not isinstance(pr, Mapping) or pr.get("draft") is not True:
            raise Step8Error("pr.draft must be true")
        title = _text(pr.get("title"), "pr.title")
        if title != " ".join(title.split()) or len(title) > 256:
            raise Step8Error("pr.title must be one line of at most 256 characters")
        body = pr.get("body")
        if not isinstance(body, str) or not body.strip():
            raise Step8Error("pr.body must be a non-empty string")
        if artifact_sha256(body) != pr.get("body_sha256"):
            raise Step8Error("pr.body_sha256 does not match pr.body")
        if len(body.encode("utf-8")) > 60_000:
            raise Step8Error("pr.body exceeds the 60000-byte Step 8 limit")
        required_sections = (
            "## Source change",
            "## Impact and justification",
            "## Tests added or changed",
            "## Validation commands and results",
            "## Remaining uncertainty",
            "## Evidence links",
            "## Human owner gate",
        )
        if any(section not in body for section in required_sections):
            raise Step8Error("pr.body is missing a required Step 8 section")
        expected_origin = str(request["patch_origin"]).replace("_", "-")
        if f"Patch origin: `{expected_origin}`" not in body:
            raise Step8Error("pr.body patch origin does not match request")
        marker = f"<!-- greenfield-step8:{request['operation_id']} -->"
        if marker not in body:
            raise Step8Error("pr.body is missing the operation marker")
        gate = request.get("human_owner_gate")
        if (
            not isinstance(gate, Mapping)
            or gate.get("status") != "pending"
            or gate.get("required_role") != "consumer_test_owner"
        ):
            raise Step8Error("human_owner_gate must remain pending")
        provenance = request.get("provenance")
        if (
            not isinstance(provenance, Mapping)
            or provenance.get("github_writes") != "not_performed"
            or provenance.get("catalog_mutation") != "none"
            or provenance.get("rule_set_version") != RULE_SET_VERSION
        ):
            raise Step8Error("request provenance is invalid")
    except Step8Error as exc:
        return [str(exc)]
    return []


def validate_step8_report(report: Any) -> list[str]:
    try:
        if not isinstance(report, Mapping):
            raise Step8Error("report must be an object")
        if report.get("schema_version") != SCHEMA_VERSION:
            raise Step8Error(f"schema_version must be {SCHEMA_VERSION}")
        if report.get("analysis_kind") != REPORT_ANALYSIS_KIND:
            raise Step8Error("analysis_kind is invalid")
        if report.get("status") not in REPORT_STATUSES:
            raise Step8Error("status is invalid")
        if report.get("mutation_stage") not in MUTATION_STAGES:
            raise Step8Error("mutation_stage is invalid")
        _sha256(report.get("operation_id"), "operation_id")
        _sha256(report.get("request_sha256"), "request_sha256")
        _sha256(report.get("report_sha256"), "report_sha256")
        unsigned = dict(report)
        unsigned.pop("report_sha256", None)
        if artifact_sha256(unsigned) != report["report_sha256"]:
            raise Step8Error("report_sha256 does not match report contents")
        artifacts = report.get("artifacts")
        if not isinstance(artifacts, Mapping):
            raise Step8Error("artifacts must be an object")
        for field in (
            "step3_report_sha256",
            "step4_report_sha256",
            "step6_report_sha256",
            "step7_report_sha256",
        ):
            _sha256(artifacts.get(field), f"artifacts.{field}")
        if report.get("patch_origin") not in {
            "template_generated",
            "strands_tool_guided",
        }:
            raise Step8Error("patch_origin is invalid")
        target = report.get("target")
        if not isinstance(target, Mapping):
            raise Step8Error("target must be an object")
        _github_repository(target.get("repository"), "target.repository")
        _branch(target.get("base_branch"), "target.base_branch")
        _branch(target.get("branch"), "target.branch")
        _sha(target.get("base_revision"), "target.base_revision")
        if target["branch"] != f"strands/greenfield-{report['operation_id'][:16]}":
            raise Step8Error("target.branch does not match operation_id")
        if target.get("patch_commit_sha") is not None:
            _sha(target["patch_commit_sha"], "target.patch_commit_sha")
        authorization = report.get("authorization")
        if not isinstance(authorization, Mapping) or not isinstance(
            authorization.get("authorized"), bool
        ):
            raise Step8Error("authorization must contain authorized")
        verifier = authorization.get("verifier")
        if not isinstance(verifier, Mapping):
            raise Step8Error("authorization.verifier must be an object")
        for field in ("id", "version"):
            _text(verifier.get(field), f"authorization.verifier.{field}")
        if authorization.get("step7_report_sha256") != artifacts["step7_report_sha256"]:
            raise Step8Error("authorization is not bound to the Step 7 report")
        _sha256(
            authorization.get("validation_fingerprint"),
            "authorization.validation_fingerprint",
        )
        if authorization["authorized"] is True:
            evidence = authorization.get("evidence")
            if not isinstance(evidence, Mapping):
                raise Step8Error("authorized decisions require evidence")
            for field in ("kind", "id"):
                _text(evidence.get(field), f"authorization.evidence.{field}")
            _sha256(evidence.get("sha256"), "authorization.evidence.sha256")
        else:
            _text(authorization.get("reason"), "authorization.reason")
        failures = report.get("failures")
        if not isinstance(failures, list) or any(
            not isinstance(row, Mapping) for row in failures
        ):
            raise Step8Error("failures must be a list of objects")
        pull = report.get("pull_request")
        if pull is not None and not isinstance(pull, Mapping):
            raise Step8Error("pull_request must be an object or null")
        if report["status"] in {"created", "reused"}:
            if failures or authorization.get("authorized") is not True:
                raise Step8Error(
                    "successful reports require authorization and no failures"
                )
            if not isinstance(pull, Mapping) or pull.get("draft") is not True:
                raise Step8Error("successful reports require a draft pull request")
            if pull.get("state") != "open":
                raise Step8Error("successful reports require an open pull request")
            number = pull.get("number")
            if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
                raise Step8Error("successful reports require a positive PR number")
            if pull.get("url") != (
                f"https://github.com/{target['repository']}/pull/{number}"
            ):
                raise Step8Error("successful reports require a canonical PR URL")
            if (
                pull.get("head_branch") != target["branch"]
                or pull.get("head_sha") != target.get("patch_commit_sha")
                or pull.get("base_branch") != target["base_branch"]
            ):
                raise Step8Error("successful report PR identity does not match target")
            if target.get("patch_commit_sha") is None:
                raise Step8Error("successful reports require a patch commit SHA")
            if (
                report["status"] == "created"
                and report["mutation_stage"] != "pull_request"
            ):
                raise Step8Error("created reports require pull_request mutation stage")
            if report["status"] == "reused" and report["mutation_stage"] != "none":
                raise Step8Error("reused reports must not claim GitHub mutations")
        elif not failures:
            raise Step8Error("blocked and failed reports require a failure")
        gate = report.get("human_owner_gate")
        if (
            not isinstance(gate, Mapping)
            or gate.get("status") != "pending"
            or gate.get("required_role") != "consumer_test_owner"
        ):
            raise Step8Error("human_owner_gate must remain pending")
        provenance = report.get("provenance")
        if not isinstance(provenance, Mapping):
            raise Step8Error("provenance must be an object")
        expected_writes = report["mutation_stage"] != "none"
        if provenance.get("github_writes") is not expected_writes:
            raise Step8Error("provenance.github_writes must match mutation_stage")
        if provenance.get("rule_set_version") != RULE_SET_VERSION:
            raise Step8Error("provenance.rule_set_version is invalid")
        if (
            provenance.get("catalog_mutation") != "none"
            or provenance.get("approval") != "none"
            or provenance.get("merge") != "none"
            or provenance.get("ready_for_review") != "none"
        ):
            raise Step8Error(
                "provenance must prohibit catalog mutation, approval, merge, and ready-for-review"
            )
        if authorization["authorized"] is False and (
            report["status"] != "blocked" or report["mutation_stage"] != "none"
        ):
            raise Step8Error(
                "rejected authorization requires a pre-write blocked report"
            )
    except Step8Error as exc:
        return [str(exc)]
    return []


__all__ = [
    "MUTATION_STAGES",
    "REPORT_ANALYSIS_KIND",
    "REPORT_STATUSES",
    "REQUEST_ANALYSIS_KIND",
    "RULE_SET_VERSION",
    "SCHEMA_VERSION",
    "Step8Error",
    "prepare_step8_request",
    "validate_step8_report",
    "validate_step8_request",
]
