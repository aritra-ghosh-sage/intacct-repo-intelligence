"""Deterministic, atomic persistence for PR-analysis reports.

This module deliberately knows nothing about the coordinator.  It accepts a
validated report and the bytes registered as evidence, then publishes one
complete external bundle or no bundle at all.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .pr_analysis import PRAnalysisReportV1, assessment_gap_kinds


@dataclass(frozen=True)
class EvidencePayload:
    """Bytes to retain for one report evidence record."""

    evidence_id: str
    content: bytes


def _safe_relative_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or not value or ".." in path.parts:
        raise ValueError("evidence paths must be relative and cannot traverse")
    if path.parts[:1] != ("evidence",) or len(path.parts) == 1:
        raise ValueError("evidence must be stored beneath evidence/")
    return path


def _inside_immutable_cache(path: Path, artifact_root: Path) -> bool:
    """Return whether *path* is beneath a prepared Ripwire identity dir.

    Reports may live under ``artifact_root/reports``.  A prepared cache
    identity is distinguished by its manifest and both cache files, so this
    check protects the immutable index without reserving the whole artifact
    root for caches only.
    """

    root = artifact_root.resolve(strict=False)
    resolved = path.resolve(strict=False)
    if resolved != root and root not in resolved.parents:
        return False
    current = resolved
    while True:
        if all(
            (current / name).is_file()
            for name in (
                "manifest.json",
                "index.lean.ripwirecache",
                "index.rich.ripwirecache",
            )
        ):
            return True
        if current == root:
            return False
        if current.parent == current:
            return False
        current = current.parent


def _validate_destination(
    output_dir: Path,
    repo_root: Path | None,
    artifact_root: Path | None = None,
) -> tuple[Path, bool]:
    if not output_dir.is_absolute():
        raise ValueError("output_dir must be absolute")
    resolved = output_dir.resolve(strict=False)
    if repo_root is not None:
        if not repo_root.is_absolute():
            raise ValueError("repo_root must be absolute")
        repo = repo_root.resolve(strict=False)
        if resolved == repo or repo in resolved.parents:
            raise ValueError("output_dir must be outside the repository")
    if artifact_root is not None:
        if not artifact_root.is_absolute():
            raise ValueError("artifact_root must be absolute")
        if _inside_immutable_cache(resolved, artifact_root):
            raise ValueError("output_dir must not be beneath an immutable Ripwire cache")
    if output_dir.is_symlink():
        raise ValueError("output_dir must not be a symlink")
    if output_dir.exists() and not output_dir.is_dir():
        raise ValueError("output_dir must be a directory")
    existed = output_dir.exists()
    if existed and any(output_dir.iterdir()):
        raise FileExistsError("refusing to overwrite non-empty output_dir")
    return resolved, existed


def _markdown(report: PRAnalysisReportV1) -> str:
    data = report.model_dump(mode="json", by_alias=True)
    lines = [
        "# PR analysis",
        "",
        f"- status: `{data['status']}`",
        f"- assessment: `{data['assessment']}`",
        f"- assessment gaps: {', '.join(f'`{kind}`' for kind in assessment_gap_kinds(data['gaps'])) or 'None'}",
        f"- phase: `{data['phase']}`",
        f"- repository: `{data['identity']['repository']}`",
        f"- head: `{data['identity']['head']}`",
        f"- base: `{data['identity']['base']}`",
        f"- merge base: `{data['identity']['merge_base']}`",
        "",
        "## Summary",
        "",
        f"Purpose: {data['summary']['purpose']}",
        f"Behavioral change: {data['summary']['behavioral_change']}",
        f"Confidence: `{data['summary']['confidence']}`",
        "",
        "## Changed files and symbols",
        "",
    ]
    for changed in data["changed_files"]:
        lines.append(f"- `{changed['change']}` `{changed['path']}`")
        for symbol in changed["symbols"]:
            line = f"  - `{symbol['name']}`"
            if symbol.get("line") is not None:
                line += f" (line {symbol['line']})"
            lines.append(line)
    if not data["changed_files"]:
        lines.append("- None")
    lines.extend(["", "## Candidate impacted files", ""])
    for impacted in data["impacted_files"]:
        lines.append(
            f"- `{impacted['path']}` (`{impacted['confidence']}`, "
            f"dependent symbols: {impacted['dependent_symbols']}; "
            f"changed path: `{impacted['changed_path']}`)"
        )
    if not data["impacted_files"]:
        lines.append("- None")
    impact_truncation_gaps = [
        gap for gap in data["gaps"] if gap["kind"] == "impact_truncated"
    ]
    if impact_truncation_gaps:
        lines.extend(["", "Impact truncation", ""])
        for gap in impact_truncation_gaps:
            suffix = f" (count: {gap['count']})" if gap.get("count") is not None else ""
            lines.append(f"- `{gap['kind']}`: {gap['detail']}{suffix}")
    lines.extend(["", "## Lower-bound blast radius", ""])
    for row in data["blast_radius"]:
        lines.append(
            f"- `{row['relationship']}` `{row['source_path']}:{row['source_symbol']}` "
            f"→ `{row['target_path']}:{row['target_symbol']}` "
            f"(`{row['confidence']}`): {row['reason']}"
        )
    if not data["blast_radius"]:
        lines.append("- None")
    lines.extend(["", "## Candidate test areas", ""])
    for area in data["test_areas"]:
        paths = ", ".join(f"`{path}`" for path in area["paths"]) or "(no paths)"
        lines.append(f"- `{area['area']}`: {paths} — {area['reason']} (`{area['execution_status']}`)")
    if not data["test_areas"]:
        lines.append("- None")
    coverage = data.get("test_inventory_coverage")
    if coverage is not None:
        lines.extend(["", "## Test inventory coverage", ""])
        lines.append(f"- status: `{coverage['status']}`")
        for finding in coverage["findings"]:
            suites = ", ".join(f"`{item}`" for item in finding["matched_suite_ids"]) or "(no suites)"
            lines.append(
                f"- `{finding['status']}` `{finding['changed_path']}` "
                f"(`{finding['match_basis']}`): {suites} — {finding['reason']}"
            )
        if not coverage["findings"]:
            lines.append("- None")
        if coverage["gaps"]:
            lines.extend(["", "Suggested corrective tests", ""])
            for gap in coverage["gaps"]:
                tags = ", ".join(gap["suggested_tags"]) or "(no tags)"
                lines.append(f"- `{gap['changed_path']}`: {gap['suggested_area']} ({tags}) — {gap['reason']}")
        if coverage["suggested_artifacts"]:
            lines.extend(["", "Suggested test scaffolds", ""])
            for artifact in coverage["suggested_artifacts"]:
                lines.append(f"- `{artifact['relative_path']}`: {artifact['description']}")
        for diagnostic in coverage["diagnostics"]:
            lines.append(f"- diagnostic: {diagnostic}")
    lines.extend(["", "## Gaps and diagnostics", ""])
    for gap in data["gaps"]:
        suffix = f" (count: {gap['count']})" if gap.get("count") is not None else ""
        lines.append(f"- `{gap['kind']}`: {gap['detail']}{suffix}")
    for diagnostic in data["diagnostics"]:
        lines.append(f"- diagnostic: {diagnostic}")
    if not data["gaps"] and not data["diagnostics"]:
        lines.append("- None")
    lines.extend(["", "## Evidence", ""])
    for evidence in data["evidence"]:
        lines.append(f"- `{evidence['evidence_id']}`: `{evidence['relative_path']}` ({evidence['sha256']})")
    if not data["evidence"]:
        lines.append("- None")
    lines.extend(["", "## Remediation", ""])
    lines.extend(f"- {item}" for item in data["remediation"] or ["None"])
    lines.extend(["", "## Agent provenance", ""])
    agent = data["agent"]
    lines.extend([
        f"- invoked: `{agent['invoked']}`",
        f"- model: `{agent['model_id']}`",
        f"- region: `{agent['region']}`",
        f"- prompt: `{agent['prompt_version']}`",
        f"- tools: `{agent['tool_contract_version']}`",
        f"- coordinator: `{agent['coordinator_version']}`",
    ])
    return "\n".join(lines) + "\n"


def _write_file(path: Path, content: bytes) -> None:
    with path.open("wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def write_pr_analysis_bundle(
    output_dir: Path,
    report: PRAnalysisReportV1,
    evidence: Sequence[EvidencePayload],
    *,
    repo_root: Path | None = None,
    artifact_root: Path | None = None,
) -> None:
    """Atomically write a report and its evidence without overwriting output."""

    destination, existed = _validate_destination(output_dir, repo_root, artifact_root)
    records = {item.evidence_id: item for item in report.evidence}
    payloads = {item.evidence_id: item for item in evidence}
    if len(records) != len(report.evidence) or len(payloads) != len(evidence):
        raise ValueError("evidence identifiers must be unique")
    if set(records) != set(payloads):
        raise ValueError("evidence payloads must exactly match report evidence")
    targets: dict[str, Path] = {}
    for record in report.evidence:
        relative = _safe_relative_path(record.relative_path)
        if str(relative) in targets:
            raise ValueError("evidence paths must be unique")
        content = payloads[record.evidence_id].content
        if hashlib.sha256(content).hexdigest() != record.sha256:
            raise ValueError(f"evidence digest mismatch: {record.evidence_id}")
        targets[str(relative)] = relative

    parent = destination.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging: Path | None = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.tmp-", dir=parent)
    )
    backup: Path | None = None
    published = False
    try:
        report_json = json.dumps(
            report.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8") + b"\n"
        _write_file(staging / "pr-analysis.json", report_json)
        _write_file(staging / "pr-analysis.md", _markdown(report).encode("utf-8"))
        evidence_root = staging / "evidence"
        evidence_root.mkdir()
        for record in report.evidence:
            relative = _safe_relative_path(record.relative_path)
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            _write_file(target, payloads[record.evidence_id].content)
        fd = os.open(staging, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        if existed:
            backup = parent / f".{destination.name}.old-{uuid.uuid4().hex}"
            os.replace(output_dir, backup)
        os.replace(staging, destination)
        published = True
        staging = None
        fd = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        if backup is not None and backup.exists():
            backup.rmdir()
            backup = None
    except Exception:
        if staging is not None and staging.exists():
            shutil.rmtree(staging)
        if published and destination.exists() and not destination.is_symlink():
            shutil.rmtree(destination)
        if backup is not None and backup.exists() and not destination.exists():
            os.replace(backup, destination)
        raise


__all__ = ["EvidencePayload", "write_pr_analysis_bundle"]
