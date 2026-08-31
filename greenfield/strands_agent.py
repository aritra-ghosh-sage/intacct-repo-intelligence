"""Run AWS Strands against exact source evidence for Greenfield Step 1.5."""

from __future__ import annotations

import concurrent.futures
import inspect
import json
import re
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from greenfield.artifact_io import artifact_sha256, write_json_atomic
from greenfield.behavior_contract import generate_behavior_contract
from greenfield.step1_5_trace import (
    TraceError,
    build_trace_rejection_diagnostic,
    normalize_trace,
    validate_trace,
)
from greenfield.step1_capture import evidence_fingerprint
from greenfield.strands_config import credential_status
from scripts.validate_greenfield_step1 import validate as validate_step1


class StrandsAgentError(ValueError):
    """Raised when Strands cannot produce a valid Step 1.5 trace."""


class ProviderExecutionError(StrandsAgentError):
    """Raised when the Strands provider fails before returning parseable output."""

    def __init__(
        self,
        message: str,
        *,
        provider_error: dict[str, Any],
        aws_credential_status: dict[str, object],
    ) -> None:
        super().__init__(message)
        self.provider_error = provider_error
        self.aws_credential_status = aws_credential_status


class Step1TraceFailure(StrandsAgentError):
    """Raised when a Step 1.5 provider response is rejected at the boundary."""

    def __init__(
        self,
        message: str,
        *,
        stage: str,
        contract_path: str | None,
        diagnostic_path: Path | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.contract_path = contract_path
        self.diagnostic_path = diagnostic_path


def _git(source_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(source_root), *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode:
        raise StrandsAgentError(f"git evidence command failed: {result.stderr.strip()}")
    return result.stdout


def build_context(
    step1: Mapping[str, Any], source_root: str | Path, *, max_file_bytes: int = 500_000
) -> dict[str, Any]:
    """Materialize only target-revision blobs needed by the agent."""

    root = Path(source_root).resolve()
    errors = validate_step1(step1)
    if errors:
        raise StrandsAgentError("invalid Step 1 report: " + "; ".join(errors))
    source = step1["input"]
    revision = str(source.get("target_revision") or source.get("head_sha")).lower()
    _git(root, "cat-file", "-e", f"{revision}^{{commit}}")
    files: list[dict[str, Any]] = []
    for row in step1["changed_files"]:
        path = str(row.get("path") or row.get("filename"))
        status = str(row.get("status", "modified"))
        content = None
        truncated = False
        if status != "deleted":
            content = _git(root, "show", f"{revision}:{path}")
            if max_file_bytes > 0 and len(content.encode("utf-8")) > max_file_bytes:
                raise StrandsAgentError(
                    f"source blob exceeds max_file_bytes for {path}; "
                    "raise --max-file-bytes to preserve exact evidence"
                )
        files.append(
            {"path": path, "status": status, "content": content, "truncated": truncated}
        )
    context = {
        "schema_version": "0.1",
        "source_repository": source.get("repository") or source.get("repo_key"),
        "source_repo_key": source.get("repo_key") or source.get("source_repo_key"),
        "pr_number": source.get("pr_number"),
        "base_revision": source.get("base_sha") or source.get("base_revision"),
        "target_revision": revision,
        "step1_evidence_sha256": evidence_fingerprint(step1),
        "changed_files": files,
    }
    context["context_sha256"] = artifact_sha256(context)
    return context


def _prompt(context: Mapping[str, Any], source_root: Path) -> str:
    context_json = json.dumps(context, sort_keys=True, indent=2)
    return f"""You are the AWS Strands Step 1.5 source-impact analyst.

The source repository is {source_root}. The following JSON contains exact
target-revision file bytes; use these bytes as the primary source evidence.
Do not modify files, checkout another revision, call GitHub, or invent
cross-repository facts.

Return only JSON matching the supplied Greenfield Step 1.5 trace schema.

Canonical output examples:
- surfaces must be an object/map, not a list:
  {{"surfaces": {{"http_qrequest": "available", "rest_api": "not_run"}}}}
  Allowed statuses are available, empty, unavailable, not_run, unresolved,
  ambiguous, and dynamic. Provider metadata such as path, lines, or notes is
  not trusted evidence and must not change the persisted status map.
- calls must use relationship_type, not kind:
  {{"source_symbol": "A", "target_symbol": "B", "relationship_type": "CALLS",
  "source_path": "app/source/a.cls", "source_line": 10,
  "source_revision": "<target revision>", "target_path": "app/source/b.cls",
  "target_line": 42, "target_revision": "<target revision>",
  "resolution": "exact"}}
  Supported relationship_type values are CALLS and STATIC_CALLS. Do not guess
  line numbers or revisions. If line evidence is missing or ambiguous, mark it
  unavailable rather than inventing values.
- behaviors must keep symbol_paths as an object/map, not a list:
  {{"symbol_paths": {{"Example.method": {{"path": "app/source/example.cls",
  "line": 42, "revision": "<target revision>"}}}}}}
  The boundary persists only the existing canonical symbol-to-path mapping;
  provider-only path/line/revision metadata must not be treated as trusted
  evidence.
- findings should explain explicit unresolved or unavailable evidence and the
  next checks.

Every asserted edge must preserve exact source evidence, use resolution="exact",
and keep provider-only metadata out of the persisted contract.
Use the target revision from the context and preserve all changed paths.

Context JSON:
```json
{context_json}
```
"""


def _default_agent_factory(
    model: str | None, *, tools: list[Any] | None = None
) -> Callable[[str], Any]:
    try:
        from strands import Agent
    except ImportError as exc:
        raise StrandsAgentError(
            "strands-agents is not installed; run `uv sync` after updating dependencies"
        ) from exc
    try:
        options: dict[str, Any] = {}
        if model:
            options["model"] = model
        if tools is not None:
            options["tools"] = tools
        agent = Agent(**options)
    except Exception as exc:  # pragma: no cover - provider-specific failure shape
        raise StrandsAgentError(f"Strands agent initialization failed: {exc}") from exc
    return agent


def _extract_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    for attribute in ("message", "content", "text"):
        child = getattr(value, attribute, None)
        if isinstance(child, str):
            return child
    if isinstance(value, Mapping):
        for key in ("message", "content", "text", "output"):
            child = value.get(key)
            if isinstance(child, str):
                return child
    return str(value)


def _parse_json_response(value: Any) -> dict[str, Any]:
    text = _extract_text(value).strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise StrandsAgentError(f"Strands did not produce JSON output: {exc}") from exc
    if not isinstance(parsed, dict):
        raise StrandsAgentError("Strands JSON output must be an object")
    return parsed


_AWS_ACCESS_KEY_PATTERN = re.compile(r"(?:AKIA|ASIA)[0-9A-Z]{16}")
_SECRET_VALUE_PATTERN = re.compile(
    r"(?i)(authorization|x-amz-security-token|aws_secret_access_key|aws_session_token|secretaccesskey|sessiontoken|api[-_]?key|token|secret)(\s*[=:]\s*)[^,;\n\r]+"
)


def _redact_provider_message(value: object, *, limit: int = 2_000) -> str:
    text = str(value)
    text = _AWS_ACCESS_KEY_PATTERN.sub("<redacted-aws-access-key>", text)
    text = _SECRET_VALUE_PATTERN.sub(r"\1\2<redacted>", text)
    if len(text) > limit:
        return text[:limit] + "...<truncated>"
    return text


def _safe_exception_summary(exc: BaseException, *, max_depth: int = 5) -> dict[str, Any]:
    provider_error: dict[str, Any] = {
        "type": type(exc).__name__,
        "message": _redact_provider_message(exc),
        "cause_chain": [],
    }
    causes: list[dict[str, str]] = []
    current = exc.__cause__ or exc.__context__
    seen = {id(exc)}
    while current is not None and id(current) not in seen and len(causes) < max_depth:
        seen.add(id(current))
        causes.append(
            {
                "type": type(current).__name__,
                "message": _redact_provider_message(current),
            }
        )
        current = current.__cause__ or current.__context__
    provider_error["cause_chain"] = causes
    return provider_error


def _run_strands_json(
    prompt: str,
    *,
    model: str | None,
    timeout: float,
    agent_factory: Callable[[str | None], Callable[[str], Any]] | None = None,
    tools: list[Any] | None = None,
) -> dict[str, Any]:
    factory = agent_factory or _default_agent_factory
    if factory is _default_agent_factory:
        agent = _default_agent_factory(model, tools=tools)
    else:
        parameters = inspect.signature(factory).parameters
        agent = factory(model, tools=tools) if "tools" in parameters else factory(model)
    executor: concurrent.futures.ThreadPoolExecutor | None = (
        concurrent.futures.ThreadPoolExecutor(max_workers=1)
    )
    try:
        future = executor.submit(agent, prompt)
        return _parse_json_response(future.result(timeout=timeout))
    except concurrent.futures.TimeoutError as exc:
        future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
        executor = None
        raise StrandsAgentError(
            f"Strands execution timed out after {timeout}s"
        ) from exc
    except StrandsAgentError:
        raise
    except Exception as exc:  # pragma: no cover - provider-specific failure shape
        status = credential_status()
        provider_error = _safe_exception_summary(exc)
        raise ProviderExecutionError(
            "Strands execution failed: "
            f"{provider_error['type']}: {provider_error['message']}; "
            "AWS credential status is " + json.dumps(status, sort_keys=True),
            provider_error=provider_error,
            aws_credential_status=status,
        ) from exc
    finally:
        if executor is not None:
            executor.shutdown(wait=True)


def run_strands_trace(
    step1: Mapping[str, Any],
    source_root: str | Path,
    *,
    model: str | None = None,
    timeout: int = 300,
    max_file_bytes: int = 120_000,
    contract_path: str | Path | None = None,
    diagnostic_output: str | Path | None = None,
    agent_factory: Callable[[str | None], Callable[[str], Any]] | None = None,
    tools: list[Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run Strands and return (validated_trace, exact context)."""

    root = Path(source_root).resolve()
    context = build_context(step1, root, max_file_bytes=max_file_bytes)
    diagnostic_path = Path(diagnostic_output) if diagnostic_output is not None else None
    try:
        raw = _run_strands_json(
            _prompt(context, root),
            model=model,
            timeout=timeout,
            agent_factory=agent_factory,
            tools=tools,
        )
    except StrandsAgentError as exc:
        provider_error = getattr(exc, "provider_error", None)
        aws_credential_status = getattr(exc, "aws_credential_status", None)
        if diagnostic_path is not None:
            write_json_atomic(
                diagnostic_path,
                build_trace_rejection_diagnostic(
                    step1,
                    stage="provider_call",
                    reason=str(exc),
                    contract_path=contract_path,
                    provider_name="strands",
                    provider_model=model or "configured",
                    context_sha256=str(context["context_sha256"]),
                    provider_error=provider_error,
                    aws_credential_status=aws_credential_status,
                ),
            )
        raise Step1TraceFailure(
            str(exc),
            stage="provider_call",
            contract_path=str(contract_path) if contract_path is not None else None,
            diagnostic_path=diagnostic_path,
        ) from exc
    metadata = {
        "name": "strands",
        "model": model or "configured",
        "timeout_seconds": timeout,
    }
    try:
        trace = normalize_trace(
            step1,
            raw,
            agent_metadata=metadata,
            context_sha256=str(context["context_sha256"]),
        )
    except TraceError as exc:
        if diagnostic_path is not None:
            write_json_atomic(
                diagnostic_path,
                build_trace_rejection_diagnostic(
                    step1,
                    stage="normalize_trace",
                    reason=str(exc),
                    contract_path=contract_path,
                    provider_name="strands",
                    provider_model=model or "configured",
                    raw_provider_response=raw,
                    context_sha256=str(context["context_sha256"]),
                ),
            )
        raise Step1TraceFailure(
            str(exc),
            stage="normalize_trace",
            contract_path=str(contract_path) if contract_path is not None else None,
            diagnostic_path=diagnostic_path,
        ) from exc
    errors = validate_trace(step1, trace)
    if errors:
        reason = "invalid Strands Step 1.5 trace: " + "; ".join(errors)
        if diagnostic_path is not None:
            write_json_atomic(
                diagnostic_path,
                build_trace_rejection_diagnostic(
                    step1,
                    stage="validate_trace",
                    reason=reason,
                    contract_path=contract_path,
                    provider_name="strands",
                    provider_model=model or "configured",
                    raw_provider_response=raw,
                    normalized_trace=trace,
                    context_sha256=str(context["context_sha256"]),
                ),
            )
        raise Step1TraceFailure(
            reason,
            stage="validate_trace",
            contract_path=str(contract_path) if contract_path is not None else None,
            diagnostic_path=diagnostic_path,
        )
    return trace, context


def _analysis_prompt(
    run_context: Mapping[str, Any], compatibility_summary: Mapping[str, Any]
) -> str:
    return f"""You are the Strands analyst for the Greenfield Analyze phase.

Use the available read-only tools to investigate repository behavior, impacted
test repositories, existing coverage, and missing coverage. Follow this order:
1. inspect explicit-contract candidates;
2. screen every discovery_eligible repository;
3. deep-inspect a screened repository only after finding supporting evidence;
4. route through a repository handbook when one is available, then verify the
   current source at the captured revision.

Return only a JSON object with repository_impacts, actions, coverage,
recommendation, gaps, and agent. Allowed evidence states are confirmed,
strong_candidate, candidate, unresolved, unavailable, and no_evidence.
Confirmed and strong_candidate rows must cite exact tool results using their
tool_call_id. Naming similarity and repository eligibility are never proof.

Actions use only run_test_suite, update_existing_test, add_missing_test,
request_owner_review, or block_automation. Each action must contain action_id,
action_type, target_repository, target_revision, evidence_state, scope,
evidence, rationale, completion_condition, and draft_eligible. Set
draft_eligible=true only for confirmed or strong_candidate update_existing_test
or add_missing_test actions with exact target revision and bounded file scope.
For a draft-eligible remediation, scope must include sorted allowed_paths,
edit_operations with path, old_text, new_text, and expected_occurrences=1, plus
the central validation_plan. New tests may be added to an existing captured test
file; creating a new repository file is not yet permitted by the mutation gate.
Owner uncertainty does not block a draft; unknown target revision, ambiguous
path scope, or unavailable validation does.

Run context:
```json
{json.dumps(run_context, sort_keys=True, indent=2)}
```

Compatibility analysis summary:
```json
{json.dumps(compatibility_summary, sort_keys=True, indent=2)}
```
"""


def run_strands_analysis(
    run_context: Mapping[str, Any],
    compatibility_summary: Mapping[str, Any],
    toolbox: Any,
    *,
    model: str | None = None,
    timeout: int = 300,
    agent_factory: Callable[[str | None], Callable[[str], Any]] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run tool-guided semantic analysis and return its output plus tool ledger."""

    try:
        from strands import tool
    except ImportError as exc:
        if agent_factory is None:
            raise StrandsAgentError(
                "strands-agents is not installed; run `uv sync` after updating dependencies"
            ) from exc

        def tool(value: Callable[..., Any]) -> Callable[..., Any]:
            return value

    tools = toolbox.as_strands_tools(tool)
    raw = _run_strands_json(
        _analysis_prompt(run_context, compatibility_summary),
        model=model,
        timeout=timeout,
        agent_factory=agent_factory,
        tools=tools,
    )
    return raw, toolbox.ledger()


def generate_contract(
    step1: Mapping[str, Any], trace: Mapping[str, Any], trace_path: str
) -> dict[str, Any]:
    return generate_behavior_contract(step1, trace, source_trace_path=trace_path)


__all__ = [
    "ProviderExecutionError",
    "Step1TraceFailure",
    "StrandsAgentError",
    "build_context",
    "generate_contract",
    "run_strands_analysis",
    "run_strands_trace",
]
