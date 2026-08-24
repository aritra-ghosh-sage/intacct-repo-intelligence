"""Run Codex read-only against an exact source revision for Step 1.5."""

from __future__ import annotations

import json
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from greenfield.artifact_io import artifact_sha256, read_json_object
from greenfield.behavior_contract import generate_behavior_contract
from greenfield.step1_5_trace import TraceError, normalize_trace, validate_trace
from greenfield.step1_capture import evidence_fingerprint
from scripts.validate_greenfield_step1 import validate as validate_step1


class CodexAgentError(ValueError):
    """Raised when Codex cannot produce a valid Step 1.5 trace."""


def _git(source_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(source_root), *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode:
        raise CodexAgentError(f"git evidence command failed: {result.stderr.strip()}")
    return result.stdout


def build_context(step1: Mapping[str, Any], source_root: str | Path, *, max_file_bytes: int = 120_000) -> dict[str, Any]:
    """Materialize only target-revision blobs needed by the agent."""

    root = Path(source_root).resolve()
    errors = validate_step1(step1)
    if errors:
        raise CodexAgentError("invalid Step 1 report: " + "; ".join(errors))
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
                raise CodexAgentError(
                    f"source blob exceeds max_file_bytes for {path}; "
                    "raise --max-file-bytes to preserve exact evidence"
                )
        files.append({"path": path, "status": status, "content": content, "truncated": truncated})
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


def _prompt(context_path: Path, source_root: Path) -> str:
    return f"""You are the Codex Step 1.5 source-impact analyst.

Read the JSON context at {context_path}. The source repository is {source_root}.
The context contains the exact target-revision file bytes; use those bytes as
the primary source evidence. You may use read-only git commands at the exact
target_revision to inspect bounded surrounding source. Do not modify files,
checkout another revision, call GitHub, or invent cross-repository facts.

Return only JSON matching the supplied output schema. Produce a trace with:
- affected_symbols: exact symbols in changed paths with source lines;
- calls: exact CALLS or STATIC_CALLS relationships with source/target paths and lines;
- behaviors: non-empty behavior groups compatible with the existing Greenfield
  behavior contract, including entry_symbols, source_paths, symbol_paths,
  exact edges, and a concise description;
- surfaces: statuses available, empty, unavailable, not_run, unresolved,
  ambiguous, or dynamic, with no claim that an unexamined surface is unaffected;
- findings: explicit unresolved or unavailable evidence and next checks.

Every asserted edge must have exact source evidence and resolution='exact'.
Use the target revision from the context and preserve all changed paths.
"""


def _run_codex_json(
    source_root: Path,
    schema: Path,
    context: Mapping[str, Any],
    prompt_factory,
    *,
    codex_binary: str,
    model: str | None,
    timeout: int,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="greenfield-codex-") as temporary:
        directory = Path(temporary)
        context_path = directory / "context.json"
        output_path = directory / "output.json"
        context_path.write_text(
            json.dumps(context, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        command = [
            codex_binary,
            "exec",
            "--ephemeral",
            "--sandbox",
            "read-only",
            "--ask-for-approval",
            "never",
            "--ignore-user-config",
            "--output-schema",
            str(schema),
            "--output-last-message",
            str(output_path),
            "-C",
            str(source_root),
            "--add-dir",
            str(Path(__file__).resolve().parents[1]),
            "--add-dir",
            str(directory),
            "-",
        ]
        if model:
            command[2:2] = ["--model", model]
        try:
            result = subprocess.run(
                command,
                input=prompt_factory(context_path),
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CodexAgentError(f"Codex execution failed: {exc}") from exc
        if result.returncode:
            detail = result.stderr.strip() or result.stdout.strip()
            raise CodexAgentError(f"Codex returned {result.returncode}: {detail[-2000:]}")
        try:
            return read_json_object(output_path)
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            raise CodexAgentError(f"Codex did not produce JSON output: {exc}") from exc


def run_codex_trace(
    step1: Mapping[str, Any],
    source_root: str | Path,
    *,
    codex_binary: str = "codex",
    model: str | None = None,
    timeout: int = 300,
    max_file_bytes: int = 120_000,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run Codex and return (validated_trace, exact context)."""

    root = Path(source_root).resolve()
    context = build_context(step1, root, max_file_bytes=max_file_bytes)
    schema = Path(__file__).resolve().parents[1] / "schemas" / "greenfield_step1_5_trace.schema.json"
    raw = _run_codex_json(
        root,
        schema,
        context,
        lambda path: _prompt(path, root),
        codex_binary=codex_binary,
        model=model,
        timeout=timeout,
    )
    metadata = {"name": "codex", "model": model or "configured", "timeout_seconds": timeout}
    trace = normalize_trace(step1, raw, agent_metadata=metadata, context_sha256=str(context["context_sha256"]))
    errors = validate_trace(step1, trace)
    if errors:
        raise TraceError("invalid Codex Step 1.5 trace: " + "; ".join(errors))
    return trace, context


def generate_contract(step1: Mapping[str, Any], trace: Mapping[str, Any], trace_path: str) -> dict[str, Any]:
    return generate_behavior_contract(step1, trace, source_trace_path=trace_path)


def _proposal_prompt(context_path: Path) -> str:
    return f"""You are the Codex Greenfield test-impact analyst.

Read the deterministic Step 1 through Step 5 reports in {context_path}.
Produce only JSON matching the supplied test-proposal schema. Propose only
test updates or additions supported by the reports' exact repository, base
revision, paths, test IDs, obligations, and evidence. Do not invent owners,
repositories, files, commands, or executed coverage. If evidence is missing,
return an empty proposal list and record the blocker in findings.
"""


def run_codex_test_proposal(
    step1: Mapping[str, Any],
    reports: Mapping[str, Mapping[str, Any]],
    source_root: str | Path,
    *,
    codex_binary: str = "codex",
    model: str | None = None,
    timeout: int = 300,
) -> dict[str, Any]:
    """Ask Codex for bounded test updates grounded in validated reports."""

    source = step1["input"]
    context = {
        "schema_version": "0.1",
        "step1": dict(step1),
        "reports": {name: dict(value) for name, value in sorted(reports.items())},
        "report_sha256": {
            name: artifact_sha256(value) for name, value in sorted(reports.items())
        },
        "source_repository": source.get("repository") or source.get("repo_key"),
        "source_revision": source.get("target_revision") or source.get("head_sha"),
    }
    context["context_sha256"] = artifact_sha256(context)
    schema = Path(__file__).resolve().parents[1] / "schemas" / "greenfield_test_proposal.schema.json"
    raw = _run_codex_json(
        Path(source_root).resolve(),
        schema,
        context,
        _proposal_prompt,
        codex_binary=codex_binary,
        model=model,
        timeout=timeout,
    )
    raw["schema_version"] = "0.1"
    raw["analysis_kind"] = "greenfield_pr_test_proposal"
    raw["input"] = {
        "source_repository": context["source_repository"],
        "source_revision": context["source_revision"],
        "changed_paths": step1["input"].get("changed_paths") or [
            row.get("path") or row.get("filename") for row in step1["changed_files"]
        ],
        "report_sha256": context["report_sha256"],
    }
    raw["provenance"] = {
        "read_only": True,
        "catalog_mutation": "none",
        "github_writes": "none",
        "context_sha256": context["context_sha256"],
        "agent": {"name": "codex", "model": model or "configured", "timeout_seconds": timeout},
    }
    return raw
