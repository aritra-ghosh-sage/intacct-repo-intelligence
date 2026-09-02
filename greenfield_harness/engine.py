"""Deterministic L1/L2/L3 progressive evidence flow, isolated from Greenfield."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .artifacts import file_sha256, sha256, write_json, write_text
from .handoff import HarnessHandoff

SHA = re.compile(r"^[0-9a-f]{40}$")
DEFAULT_BUDGETS = {
    "max_evidence_reads": 8,
    "max_files": 8,
    "max_bytes": 120_000,
    "max_paths": 16,
    "max_results": 20,
}


class HarnessError(ValueError):
    pass


def _git(root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(root), *args], check=False, capture_output=True, timeout=30
    )
    if result.returncode:
        raise HarnessError(
            result.stderr.decode("utf-8", errors="replace").strip()
            or "git evidence read failed"
        )
    return result.stdout


def _safe_path(path: str) -> str:
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts or not path.strip():
        raise HarnessError("path must be a safe repository-relative path")
    return path


def _blob(root: Path, revision: str, path: str) -> bytes:
    return _git(root, "show", f"{revision}:{_safe_path(path)}")


def _blob_or_none(root: Path, revision: str, path: str) -> bytes | None:
    """Return no blob for a path absent at the captured revision."""
    result = subprocess.run(
        ["git", "-C", str(root), "cat-file", "-e", f"{revision}:{_safe_path(path)}"],
        check=False,
        capture_output=True,
        timeout=30,
    )
    return _blob(root, revision, path) if result.returncode == 0 else None


def _excerpt(
    root: Path, revision: str, path: str, start_line: int, end_line: int
) -> bytes:
    """Read one exact target-revision line range without charging the whole blob."""
    lines = _blob(root, revision, path).splitlines(keepends=True)
    if start_line < 1 or end_line < start_line or end_line > len(lines):
        raise HarnessError("excerpt range is outside the target blob")
    return b"".join(lines[start_line - 1 : end_line])


def _identity(root: Path) -> tuple[str, str]:
    remote = _git(root, "config", "--get", "remote.origin.url").decode().strip()
    repository = (
        remote.split("github.com:", 1)[-1]
        .split("github.com/", 1)[-1]
        .removesuffix(".git")
    )
    if not repository or repository == remote:
        raise HarnessError("source remote must identify a repository")
    return repository, repository.rsplit("/", 1)[-1]


def _input_record(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": file_sha256(path)}


def capture_context(
    *,
    source_root: Path,
    pr: int,
    base_revision: str,
    target_revision: str,
    candidates: Sequence[Mapping[str, Any]] = (),
    budgets: Mapping[str, int] | None = None,
    input_records: Sequence[Path] = (),
) -> dict[str, Any]:
    root = source_root.resolve()
    if (
        not root.is_dir()
        or not SHA.fullmatch(base_revision)
        or not SHA.fullmatch(target_revision)
        or pr < 1
    ):
        raise HarnessError(
            "source root, PR number, and lowercase base/target revisions are required"
        )
    _git(root, "cat-file", "-e", f"{base_revision}^{{commit}}")
    _git(root, "cat-file", "-e", f"{target_revision}^{{commit}}")
    repository, repo_key = _identity(root)
    changed_paths = sorted(
        {
            line.decode("utf-8", errors="replace").strip()
            for line in _git(
                root, "diff", "--name-only", base_revision, target_revision
            ).splitlines()
            if line.strip()
        }
    )
    resolved_budgets = {
        **DEFAULT_BUDGETS,
        **{key: int(value) for key, value in (budgets or {}).items()},
    }
    if any(value < 1 for value in resolved_budgets.values()):
        raise HarnessError("evidence budgets must be positive")
    captured_candidates = []
    for row in candidates:
        candidate = dict(row)
        candidate_root = Path(str(candidate.get("local_root", ""))).resolve()
        revision = str(candidate.get("revision", ""))
        if not candidate_root.is_dir() or not SHA.fullmatch(revision):
            raise HarnessError(
                "candidate repository requires local_root and captured revision"
            )
        _git(candidate_root, "cat-file", "-e", f"{revision}^{{commit}}")
        captured_candidates.append(
            {
                "repository": str(candidate["repository"]),
                "repo_key": str(candidate.get("repo_key") or candidate["repository"]),
                "local_root": str(candidate_root),
                "revision": revision,
            }
        )
    context: dict[str, Any] = {
        "schema_version": "0.1",
        "artifact_kind": "greenfield_harness_run_context",
        "source": {
            "repository": repository,
            "repo_key": repo_key,
            "pr_number": pr,
            "base_revision": base_revision,
            "target_revision": target_revision,
            "changed_paths": changed_paths,
            "local_root": str(root),
            "source_root_identity": {
                "git_dir": _git(root, "rev-parse", "--git-dir").decode().strip(),
                "remote": _git(root, "config", "--get", "remote.origin.url")
                .decode()
                .strip(),
            },
        },
        "candidate_repositories": sorted(
            captured_candidates, key=lambda row: row["repository"]
        ),
        "evidence_budgets": resolved_budgets,
        "input_provenance": sorted(
            [_input_record(path) for path in input_records], key=lambda row: row["path"]
        ),
        "provenance": {
            "read_only": True,
            "github_writes": "none",
            "catalog_mutation": "none",
            "model_calls": "none",
        },
    }
    context["context_sha256"] = sha256(context)
    return context


def _hunks(root: Path, base: str, target: str, path: str) -> list[dict[str, int]]:
    output = _git(root, "diff", "--unified=0", base, target, "--", path).decode(
        "utf-8", errors="replace"
    )
    rows = []
    for old_start, old_len, new_start, new_len in re.findall(
        r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", output
    ):
        rows.append(
            {
                "base_start": int(old_start),
                "base_count": int(old_len or 1),
                "target_start": int(new_start),
                "target_count": int(new_len or 1),
            }
        )
    return rows


def behavior_packet(
    context: Mapping[str, Any],
    handbook: Mapping[str, Any] | None = None,
    contracts: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    source = context["source"]
    root = Path(str(source["local_root"]))
    target = str(source["target_revision"])
    paths = []
    for path in source["changed_paths"]:
        content = _blob_or_none(root, target, path)
        hunks = _hunks(root, str(source["base_revision"]), target, path)
        if content is None:
            base_content = _blob_or_none(root, str(source["base_revision"]), path)
            paths.append(
                {
                    "path": path,
                    "target_blob_sha256": None,
                    "line_count": None,
                    "changed_hunks": hunks,
                    "excerpt_ranges": [],
                    "omitted_ranges": [],
                    "static_facts": [
                        {
                            "kind": "target_blob",
                            "path": path,
                            "status": "unavailable",
                            "reason": "path_absent_at_target_revision",
                        },
                        *(
                            [
                                {
                                    "kind": "base_blob",
                                    "path": path,
                                    "sha256": hashlib.sha256(base_content).hexdigest(),
                                }
                            ]
                            if base_content is not None
                            else []
                        ),
                    ],
                    "l1_locators": [],
                    "context_gaps": [
                        {
                            "status": "unavailable",
                            "reason": "target_path_absent_at_revision",
                        }
                    ],
                }
            )
            continue
        lines = content.decode("utf-8", errors="replace").splitlines()
        excerpts = []
        context_gaps = []
        if not lines:
            context_gaps.append(
                {"status": "unavailable", "reason": "target_path_empty_at_revision"}
            )
        else:
            for hunk in hunks or [
                {"target_start": 1, "target_count": min(20, len(lines))}
            ]:
                start = max(1, hunk["target_start"] - 3)
                end = min(
                    len(lines), hunk["target_start"] + max(1, hunk["target_count"]) + 2
                )
                if start <= end:
                    excerpts.append({"start_line": start, "end_line": end})
        paths.append(
            {
                "path": path,
                "target_blob_sha256": hashlib.sha256(content).hexdigest(),
                "line_count": len(lines),
                "changed_hunks": hunks,
                "excerpt_ranges": excerpts,
                "omitted_ranges": _omitted(len(lines), excerpts),
                "static_facts": [
                    {
                        "kind": "target_blob",
                        "path": path,
                        "sha256": hashlib.sha256(content).hexdigest(),
                    },
                    {"kind": "changed_hunk_count", "value": len(hunks)},
                ],
                "l1_locators": [],
                "context_gaps": context_gaps,
            }
        )
    unassigned = []
    for locator in _locators(handbook, contracts):
        matched = False
        for row in paths:
            if locator.get("path") == row["path"]:
                row["l1_locators"].append(locator)
                matched = True
        if not matched:
            unassigned.append(
                {"evidence": locator, "reason": "no_exact_changed_path_join"}
            )
    for row in paths:
        if not row["l1_locators"]:
            row["context_gaps"].append(
                {"status": "unavailable", "reason": "no_exact_l1_locator"}
            )
    packet = {
        "schema_version": "0.1",
        "artifact_kind": "greenfield_harness_behavior_packet",
        "context_sha256": context["context_sha256"],
        "paths": paths,
        "unassigned_evidence": unassigned,
        "provenance": {"deterministic": True, "model_calls": "none"},
    }
    packet["packet_sha256"] = sha256(packet)
    return packet


def _omitted(
    line_count: int, excerpts: Sequence[Mapping[str, int]]
) -> list[dict[str, int]]:
    omitted, cursor = [], 1
    for excerpt in sorted(excerpts, key=lambda row: row["start_line"]):
        if cursor < excerpt["start_line"]:
            omitted.append(
                {"start_line": cursor, "end_line": excerpt["start_line"] - 1}
            )
        cursor = max(cursor, excerpt["end_line"] + 1)
    if cursor <= line_count:
        omitted.append({"start_line": cursor, "end_line": line_count})
    return omitted


def _locators(
    handbook: Mapping[str, Any] | None, contracts: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            if isinstance(value.get("path"), str):
                rows.append(
                    {
                        key: value[key]
                        for key in (
                            "path",
                            "line",
                            "source_revision",
                            "source_sha256",
                            "material",
                        )
                        if key in value
                    }
                )
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(handbook or {})
    visit(list(contracts))
    return rows


def _stage(
    name: str, body: Mapping[str, Any], *, gaps: Sequence[Mapping[str, Any]] = ()
) -> dict[str, Any]:
    result = {
        "schema_version": "0.1",
        "artifact_kind": f"greenfield_harness_{name}",
        "status": "succeeded" if not gaps else "degraded",
        **body,
        "gaps": list(gaps),
    }
    result["result_sha256"] = sha256(result)
    return result


def _l2_path_priority(item: Mapping[str, Any]) -> tuple[int, str]:
    """Rank retained L2 reads: source/tests, other paths, then metadata/docs."""
    path = str(item["path"])
    lowered = path.lower()
    parts = lowered.split("/")
    is_metadata_or_docs = lowered.startswith((".github/", "docs/")) or lowered.endswith(
        (".md", ".rst", ".adoc")
    )
    is_test = any(part in {"test", "tests", "spec", "specs"} for part in parts) or (
        Path(lowered).stem.startswith("test_")
        or Path(lowered).stem.endswith(("_test", "_spec"))
    )
    is_application_source = lowered.startswith(
        ("app/", "src/", "lib/")
    ) or lowered.endswith(
        (".py", ".php", ".cls", ".js", ".ts", ".java", ".go", ".rb", ".cs")
    )
    # Stable lexical tie-breaking makes retained evidence independently replayable.
    return (
        2 if is_metadata_or_docs else 0 if is_test or is_application_source else 1,
        path,
    )


def l1_locate(context: Mapping[str, Any], packet: Mapping[str, Any]) -> dict[str, Any]:
    target = context["source"]["target_revision"]
    rows, gaps = [], []
    for item in packet["paths"]:
        locators = []
        if item["target_blob_sha256"] is None:
            gaps.append(
                {
                    "status": "unavailable",
                    "path": item["path"],
                    "reason": "target_path_absent_at_revision",
                }
            )
            rows.append({"path": item["path"], "status": "unavailable", "locators": []})
            continue
        for locator in item["l1_locators"]:
            if locator.get("source_revision") not in (None, target):
                gaps.append(
                    {
                        "status": "unresolved",
                        "path": item["path"],
                        "reason": "l1_locator_revision_mismatch",
                    }
                )
            else:
                locators.append({**locator, "status": "available"})
        status = "available" if locators else "unavailable"
        if not locators:
            gaps.append(
                {
                    "status": "unavailable",
                    "path": item["path"],
                    "reason": "no_exact_l1_locator",
                }
            )
        rows.append({"path": item["path"], "status": status, "locators": locators})
    return _stage(
        "l1_locate",
        {"context_sha256": context["context_sha256"], "locators": rows},
        gaps=gaps,
    )


def l2_inspect(context: Mapping[str, Any], packet: Mapping[str, Any]) -> dict[str, Any]:
    source = context["source"]
    root = Path(str(source["local_root"]))
    budget = context["evidence_budgets"]
    ledger, gaps, used_bytes, accessed_paths = [], [], 0, set()
    # L2 reads excerpt-bearing paths in deterministic value order: application/test,
    # then other changed paths, then metadata/documentation (including .github/**).
    # Paths in a tier use a lexical tie-breaker; a large low-value blob cannot starve
    # a smaller, higher-value exact target-revision excerpt.
    items = sorted(packet["paths"], key=_l2_path_priority)
    for item in items:
        if item["target_blob_sha256"] is None:
            gaps.append(
                {
                    "status": "unavailable",
                    "path": item["path"],
                    "reason": "target_path_absent_at_revision",
                }
            )
            continue
        if not item["excerpt_ranges"]:
            gaps.append(
                {
                    "status": "unavailable",
                    "path": item["path"],
                    "reason": (
                        "target_path_empty_at_revision"
                        if item.get("line_count") == 0
                        else "no_exact_excerpt_range"
                    ),
                }
            )
            continue
        if (
            len(ledger) >= budget["max_evidence_reads"]
            or len(accessed_paths) >= budget["max_files"]
            or len(accessed_paths) >= budget["max_paths"]
        ):
            gaps.append(
                {
                    "status": "unavailable",
                    "path": item["path"],
                    "reason": "context_budget_exhausted",
                    "budget": "max_evidence_reads_or_files_or_paths",
                }
            )
            break
        for excerpt in item["excerpt_ranges"]:
            if len(ledger) >= budget["max_evidence_reads"]:
                gaps.append(
                    {
                        "status": "unavailable",
                        "path": item["path"],
                        "reason": "context_budget_exhausted",
                        "budget": "max_evidence_reads",
                    }
                )
                break
            start, end = excerpt["start_line"], excerpt["end_line"]
            retained_bytes = _excerpt(
                root, str(source["target_revision"]), item["path"], start, end
            )
            try:
                retained_excerpt = retained_bytes.decode("utf-8")
            except UnicodeDecodeError:
                gaps.append(
                    {
                        "status": "unavailable",
                        "path": item["path"],
                        "start_line": start,
                        "end_line": end,
                        "reason": "target_excerpt_not_utf8",
                    }
                )
                continue
            if used_bytes + len(retained_bytes) > budget["max_bytes"]:
                gaps.append(
                    {
                        "status": "unavailable",
                        "path": item["path"],
                        "start_line": start,
                        "end_line": end,
                        "reason": "context_budget_exhausted",
                        "budget": "max_bytes",
                    }
                )
                # An oversized local excerpt does not consume budget or prevent
                # later priority-ranked excerpts from being retained.
                continue
            used_bytes += len(retained_bytes)
            accessed_paths.add(item["path"])
            result = {
                "status": "available",
                "repository": source["repository"],
                "source_revision": source["target_revision"],
                "path": item["path"],
                "start_line": start,
                "end_line": end,
                "source_blob_sha256": item["target_blob_sha256"],
                "excerpt_sha256": hashlib.sha256(retained_bytes).hexdigest(),
                "excerpt": retained_excerpt,
            }
            ledger.append(
                {
                    "tool": "read_source",
                    "tool_call_id": sha256(
                        {
                            "sequence": len(ledger) + 1,
                            "path": item["path"],
                            "start_line": start,
                            "end_line": end,
                        }
                    ),
                    "result_sha256": sha256(result),
                    "result": result,
                    "status": "available",
                }
            )
    return _stage(
        "l2_inspect",
        {
            "context_sha256": context["context_sha256"],
            "ledger": ledger,
            "used_bytes": used_bytes,
        },
        gaps=gaps,
    )


def l3_resolve(
    context: Mapping[str, Any],
    l1: Mapping[str, Any],
    l2: Mapping[str, Any],
    gap_requests: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Literal source search only for caller-supplied, material, explicit gaps."""
    budget = context["evidence_budgets"]
    source = context["source"]
    root = Path(str(source["local_root"]))
    ledger, gaps, used = [], [], 0
    searched_paths: set[str] = set()
    matched_files: set[str] = set()
    used_results = 0
    material = [dict(row) for row in gap_requests if row.get("material") is True]
    for request in material:
        if request.get("repository", source["repository"]) != source["repository"]:
            gaps.append(
                {"status": "unavailable", "reason": "repository_outside_captured_scope"}
            )
            continue
        query = str(request.get("literal", ""))
        prefix = str(request.get("path_prefix", ""))
        search_path = prefix or "."
        if (
            not query
            or len(ledger) >= budget["max_evidence_reads"]
            or (
                search_path not in searched_paths
                and len(searched_paths) >= budget["max_paths"]
            )
        ):
            gaps.append(
                {
                    "status": "unavailable",
                    "reason": "context_budget_exhausted"
                    if query
                    else "explicit_gap_request_invalid",
                }
            )
            continue
        searched_paths.add(search_path)
        args = ["grep", "-n", "-F", query, str(source["target_revision"])] + (
            ["--", _safe_path(prefix)] if prefix else []
        )
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            check=False,
            capture_output=True,
            timeout=30,
        )
        remaining_results = budget["max_results"] - used_results
        if remaining_results < 1:
            gaps.append(
                {
                    "status": "unavailable",
                    "reason": "context_budget_exhausted",
                    "budget": "max_results",
                }
            )
            break
        matches = []
        for match in result.stdout.decode("utf-8", errors="replace").splitlines():
            path_match = re.match(r"^(.*?):\d+:", match)
            match_path = path_match.group(1) if path_match else None
            if (
                match_path
                and match_path not in matched_files
                and len(matched_files) >= budget["max_files"]
            ):
                gaps.append(
                    {
                        "status": "unavailable",
                        "reason": "context_budget_exhausted",
                        "budget": "max_files",
                    }
                )
                break
            if match_path:
                matched_files.add(match_path)
            matches.append(match)
            if len(matches) >= remaining_results:
                break
        used_results += len(matches)
        payload = {
            "status": "available" if result.returncode in (0, 1) else "unavailable",
            "repository": source["repository"],
            "source_revision": source["target_revision"],
            "query": query,
            "path_prefix": prefix,
            "matches": matches,
            "truncated": len(matches) == remaining_results,
        }
        payload_bytes = len(json.dumps(payload).encode())
        if used + payload_bytes > budget["max_bytes"]:
            gaps.append(
                {
                    "status": "unavailable",
                    "reason": "context_budget_exhausted",
                    "budget": "max_bytes",
                }
            )
            continue
        used += payload_bytes
        ledger.append(
            {
                "tool": "search_source",
                "tool_call_id": sha256(
                    {"sequence": len(ledger) + 1, "query": query, "prefix": prefix}
                ),
                "result_sha256": sha256(payload),
                "result": payload,
                "status": payload["status"],
            }
        )
    if not material:
        gaps.append({"status": "no_evidence", "reason": "no_material_explicit_l3_gap"})
    return _stage(
        "l3_resolve",
        {
            "context_sha256": context["context_sha256"],
            "ledger": ledger,
            "used_bytes": used,
            "used_results": used_results,
            "matched_files": sorted(matched_files),
        },
        gaps=gaps,
    )


def _canonical(
    context: Mapping[str, Any],
    packet: Mapping[str, Any],
    l1: Mapping[str, Any],
    l2: Mapping[str, Any],
    l3: Mapping[str, Any],
) -> dict[str, Any]:
    gaps = [*l1["gaps"], *l2["gaps"], *l3["gaps"]]
    findings = [
        {
            "path": row["path"],
            "status": "confirmed",
            "evidence": [
                entry["tool_call_id"]
                for entry in l2["ledger"]
                if entry["result"]["path"] == row["path"]
            ],
            "claim": "revision-bound changed source excerpt retained",
        }
        for row in packet["paths"]
        if any(entry["result"]["path"] == row["path"] for entry in l2["ledger"])
    ]
    report = {
        "schema_version": "0.1",
        "artifact_kind": "greenfield_harness_analysis",
        "context_sha256": context["context_sha256"],
        "behavior_packet_sha256": packet["packet_sha256"],
        "behavior_findings": findings,
        "repository_impacts": [],
        "coverage_obligations": [],
        "recommended_actions": [],
        "gaps": gaps,
        "investigation_ledger": {"l1": l1, "l2": l2["ledger"], "l3": l3["ledger"]},
        "unassigned_evidence": packet["unassigned_evidence"],
        "provenance": {
            "read_only": True,
            "github_writes": "none",
            "catalog_mutation": "none",
            "model_calls": "none",
        },
    }
    report["analysis_sha256"] = sha256(report)
    return report


def _project(analysis: Mapping[str, Any]) -> tuple[dict[str, Any], str, str]:
    decisions = {
        key: analysis[key]
        for key in (
            "behavior_findings",
            "repository_impacts",
            "coverage_obligations",
            "recommended_actions",
            "gaps",
            "unassigned_evidence",
        )
    }
    report = {
        "schema_version": "0.1",
        "artifact_kind": "greenfield_harness_behavior_impact_report",
        "analysis_sha256": analysis["analysis_sha256"],
        "decisions": decisions,
    }
    markdown = (
        "# Harness behavior impact report\n\nCanonical analysis: `harness-analysis.json`\n\n```json\n"
        + json.dumps(decisions, indent=2, sort_keys=True)
        + "\n```\n"
    )
    review = (
        "# Harness review\n\nThis is a read-only experimental projection. Decisions are reproduced verbatim from `harness-analysis.json`.\n\n"
        + markdown.split("\n\n", 1)[1]
    )
    return report, markdown, review


def run_harness(
    *,
    source_root: Path,
    output_dir: Path,
    pr: int,
    base_revision: str,
    target_revision: str,
    candidates: Sequence[Mapping[str, Any]] = (),
    handbook: Mapping[str, Any] | None = None,
    contracts: Sequence[Mapping[str, Any]] = (),
    gap_requests: Sequence[Mapping[str, Any]] = (),
    budgets: Mapping[str, int] | None = None,
    input_records: Sequence[Path] = (),
) -> dict[str, Path]:
    """Run the fixed experiment into one new immutable harness bundle."""
    root = output_dir.resolve()
    required_parent = (Path.cwd() / "artifacts" / "greenfield-harness").resolve()
    if root.parent != required_parent:
        raise HarnessError(
            "output-dir must be a direct child of artifacts/greenfield-harness"
        )
    if root.exists():
        raise HarnessError("harness output bundle must not already exist")
    root.mkdir(parents=True)
    context = capture_context(
        source_root=source_root,
        pr=pr,
        base_revision=base_revision,
        target_revision=target_revision,
        candidates=candidates,
        budgets=budgets,
        input_records=input_records,
    )
    handoff = HarnessHandoff(root, context["source"])
    paths: dict[str, Path] = {}
    paths["context"] = write_json(root / "harness-run-context.json", context)
    handoff.complete("capture", inputs={}, outputs={"context": paths["context"]})
    packet = behavior_packet(context, handbook, contracts)
    paths["packet"] = write_json(root / "behavior-packet.json", packet)
    handoff.complete(
        "behavior_packet",
        inputs={"context": paths["context"]},
        outputs={"packet": paths["packet"]},
    )
    l1 = l1_locate(context, packet)
    paths["l1"] = write_json(root / "l1-locate.json", l1)
    handoff.complete(
        "l1_locate",
        inputs={"packet": paths["packet"]},
        outputs={"l1": paths["l1"]},
        status=l1["status"],
    )
    l2 = l2_inspect(context, packet)
    paths["l2"] = write_json(root / "l2-inspect.json", l2)
    handoff.complete(
        "l2_inspect",
        inputs={"packet": paths["packet"], "l1": paths["l1"]},
        outputs={"l2": paths["l2"]},
        status=l2["status"],
    )
    l3 = l3_resolve(context, l1, l2, gap_requests)
    paths["l3"] = write_json(root / "l3-resolve.json", l3)
    handoff.complete(
        "l3_resolve",
        inputs={"l1": paths["l1"], "l2": paths["l2"]},
        outputs={"l3": paths["l3"]},
        status=l3["status"],
    )
    analysis = _canonical(context, packet, l1, l2, l3)
    paths["analysis"] = write_json(root / "harness-analysis.json", analysis)
    handoff.complete(
        "analyze",
        inputs={
            "context": paths["context"],
            "packet": paths["packet"],
            "l1": paths["l1"],
            "l2": paths["l2"],
            "l3": paths["l3"],
        },
        outputs={"analysis": paths["analysis"]},
    )
    report, markdown, review = _project(analysis)
    paths["report"] = write_json(root / "behavior-impact-report.json", report)
    paths["markdown"] = write_text(root / "behavior-impact-report.md", markdown)
    paths["review"] = write_text(root / "review.md", review)
    handoff.complete(
        "project",
        inputs={"analysis": paths["analysis"]},
        outputs={
            "report": paths["report"],
            "markdown": paths["markdown"],
            "review": paths["review"],
        },
    )
    paths["handoff"] = handoff.finish()
    return paths
