"""Small, read-only PR-impact experiment for the Greenfield harness."""

from __future__ import annotations

import hashlib
import json
import re
import select
import subprocess
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from .artifacts import file_sha256, sha256, write_json, write_text
from .pr_impact_planner import (
    MAX_SOURCE_READS,
    PlannerProvider,
    initial_plan,
    test_plan,
)
from .pr_impact_planner import (
    report as planning_report,
)

STAGES = (
    "capture",
    "extract",
    "planner_initial",
    "source_investigation",
    "planner_replan",
    "candidate_discovery",
    "test_inspection",
    "coverage_assessment",
    "test_recommendations",
    "analyze",
    "project",
)
_SHA = re.compile(r"^[0-9a-f]{40}$")
_SAFE_PATH = re.compile(r"^[^/][^\\]*")
_DECLARATION = re.compile(
    r"^\s*(?:abstract\s+|final\s+)?(?:class|interface|trait|function)\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.MULTILINE,
)
_PYTHON_DECLARATION = re.compile(r"^\s*(?:class|def)\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)
_CONFIG_VALUE = re.compile(r"['\"]([A-Za-z][A-Za-z0-9_.:-]{2,})['\"]")
MAX_CANDIDATE_MATCHES_PER_REPOSITORY = 20
_ELIGIBILITY_STATUSES = {"eligible", "excluded_archived", "unavailable", "invalid"}


class PrImpactError(ValueError):
    pass


def _git(root: Path, *args: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args], check=False, capture_output=True, timeout=30
        )
    except subprocess.TimeoutExpired as exc:
        raise PrImpactError("git evidence read timed out") from exc
    if result.returncode:
        raise PrImpactError(result.stderr.decode("utf-8", errors="replace").strip() or "git failed")
    return result.stdout


def _safe_path(path: str) -> str:
    value = Path(path)
    if not path or value.is_absolute() or ".." in value.parts or not _SAFE_PATH.match(path):
        raise PrImpactError("path must be a safe repository-relative path")
    return path


def _line(value: str, index: int) -> int:
    return value.count("\n", 0, index) + 1


def _reference(root: Path, path: Path) -> dict[str, str]:
    return {"path": str(path.resolve().relative_to(root.resolve())), "sha256": file_sha256(path)}


class ImpactHandoff:
    """Fixed-order, hash-bound handoff isolated from the legacy harness flow."""

    def __init__(self, root: Path, identity: Mapping[str, Any]) -> None:
        self.root, self.path = root, root / "pr-impact-flow-handoff.json"
        self.body: dict[str, Any] = {
            "schema_version": "0.1",
            "artifact_kind": "greenfield_harness_pr_impact_handoff",
            "identity": dict(identity),
            "stages": [],
            "provenance": {"read_only": True, "github_writes": "none", "catalog_mutation": "none"},
        }

    def complete(self, name: str, *, inputs: Mapping[str, Path], outputs: Mapping[str, Path], status: str = "succeeded") -> None:
        if name not in STAGES or len(self.body["stages"]) != STAGES.index(name):
            raise PrImpactError("PR-impact stages are out of order")
        refs = {
            direction: {key: _reference(self.root, value) for key, value in sorted(rows.items())}
            for direction, rows in (("inputs", inputs), ("outputs", outputs))
        }
        self.body["stages"].append({"name": name, "status": status, **refs})

    def finish(self, *, status: str) -> Path:
        self.body["status"] = status
        return write_json(self.path, self.body)

    @classmethod
    def validate(cls, root: Path) -> dict[str, Any]:
        body = json.loads((root / "pr-impact-flow-handoff.json").read_text(encoding="utf-8"))
        if not isinstance(body, Mapping) or body.get("artifact_kind") != "greenfield_harness_pr_impact_handoff":
            raise PrImpactError("invalid PR-impact handoff")
        names = tuple(row.get("name") for row in body.get("stages", []))
        expected = STAGES if body.get("status") == "complete" else STAGES[: len(names)]
        if names != expected:
            raise PrImpactError("invalid PR-impact handoff stage order")
        for stage in body["stages"]:
            for direction in ("inputs", "outputs"):
                for row in stage[direction].values():
                    path = root / str(row.get("path", ""))
                    if not path.is_file() or file_sha256(path) != row.get("sha256"):
                        raise PrImpactError("PR-impact handoff SHA mismatch")
        return dict(body)


def _capture(source_root: Path, pr: int, base: str, target: str, candidates: Sequence[Mapping[str, Any]], input_paths: Sequence[Path]) -> dict[str, Any]:
    root = source_root.resolve()
    if not root.is_dir() or pr < 1 or not _SHA.fullmatch(base) or not _SHA.fullmatch(target):
        raise PrImpactError("source root, PR number, and lowercase base/target SHAs are required")
    _git(root, "cat-file", "-e", f"{base}^{{commit}}")
    _git(root, "cat-file", "-e", f"{target}^{{commit}}")
    remote = _git(root, "config", "--get", "remote.origin.url").decode().strip()
    repository = remote.split("github.com:", 1)[-1].split("github.com/", 1)[-1].removesuffix(".git")
    normalized_candidates = []
    for raw in candidates:
        if not isinstance(raw, Mapping):
            raise PrImpactError("candidate must be an object")
        status = raw.get("eligibility_status")
        if status not in _ELIGIBILITY_STATUSES:
            raise PrImpactError("candidate eligibility_status must be explicit and valid")
        if status != "eligible":
            continue
        candidate_root = Path(str(raw.get("local_root", ""))).resolve()
        revision = str(raw.get("revision", ""))
        roots = raw.get("test_roots", [])
        if not candidate_root.is_dir() or not _SHA.fullmatch(revision) or not isinstance(roots, list):
            raise PrImpactError("candidate requires local_root, revision, and test_roots")
        _git(candidate_root, "cat-file", "-e", f"{revision}^{{commit}}")
        normalized_candidates.append({
            "repository": str(raw.get("repository") or ""), "repo_key": str(raw.get("repo_key") or raw.get("repository") or ""),
            "local_root": str(candidate_root), "revision": revision,
            "test_roots": sorted({_safe_path(str(item)) for item in roots}),
        })
    if any(not row["repository"] for row in normalized_candidates):
        raise PrImpactError("candidate repository is required")
    source = {
        "repository": repository, "pr_number": pr, "base_revision": base, "target_revision": target,
        "base_tree_sha256": _git(root, "rev-parse", f"{base}^{{tree}}").decode().strip(),
        "target_tree_sha256": _git(root, "rev-parse", f"{target}^{{tree}}").decode().strip(),
        "changed_paths": sorted(_git(root, "diff", "--name-only", base, target).decode().splitlines()),
        "local_root": str(root),
    }
    value: dict[str, Any] = {
        "schema_version": "0.1", "artifact_kind": "greenfield_harness_pr_impact_context", "source": source,
        "candidate_repositories": sorted(normalized_candidates, key=lambda row: row["repository"]),
        "input_provenance": sorted([{"path": str(path.resolve()), "sha256": file_sha256(path)} for path in input_paths], key=lambda row: row["path"]),
        "provenance": {"read_only": True, "github_writes": "none", "catalog_mutation": "none", "model_calls": "strands_aws"},
    }
    value["context_sha256"] = sha256(value)
    return value


def _add(rows: list[dict[str, Any]], path: str, kind: str, value: str, line: int, blob: str) -> None:
    rows.append({"id": f"extract:{path}:{kind}:{value}:{line}", "path": path, "kind": kind, "value": value, "line": line, "source_blob_sha256": blob, "status": "available"})


def deterministic_extraction(context: Mapping[str, Any]) -> dict[str, Any]:
    source, root = context["source"], Path(str(context["source"]["local_root"]))
    rows, gaps = [], []
    for path in source["changed_paths"]:
        try:
            raw = _git(root, "show", f"{source['target_revision']}:{path}")
        except PrImpactError:
            gaps.append({"path": path, "status": "unavailable", "reason": "target_path_absent_at_revision"})
            continue
        text, blob = raw.decode("utf-8", errors="replace"), hashlib.sha256(raw).hexdigest()
        suffix = Path(path).suffix.lower()
        if suffix in {".cls", ".inc", ".php", ".phtml", ".py"}:
            pattern = _PYTHON_DECLARATION if suffix == ".py" else _DECLARATION
            for match in pattern.finditer(text):
                _add(rows, path, "symbol", match.group(1), _line(text, match.start(1)), blob)
        elif suffix in {".yaml", ".yml", ".json"}:
            try:
                data = json.loads(text) if suffix == ".json" else yaml.safe_load(text)
                if isinstance(data, Mapping):
                    for key, value in data.items():
                        if isinstance(key, str) and key.startswith("/") and isinstance(value, Mapping):
                            for method in sorted(str(name).upper() for name in value if str(name).lower() in {"get", "post", "put", "patch", "delete"}):
                                _add(rows, path, "api", f"{method} {key}", 1, blob)
            except (json.JSONDecodeError, yaml.YAMLError):
                gaps.append({"path": path, "status": "unavailable", "reason": "structured_parse_failed"})
        elif "config" in path.lower() or "backend" in path.lower():
            for match in _CONFIG_VALUE.finditer(text):
                _add(rows, path, "config", match.group(1), _line(text, match.start(1)), blob)
        else:
            gaps.append({"path": path, "status": "unavailable", "reason": "unsupported_extraction_format"})
    value = {"schema_version": "0.1", "artifact_kind": "greenfield_harness_deterministic_extraction", "context_sha256": context["context_sha256"], "items": sorted(rows, key=lambda row: row["id"]), "gaps": gaps}
    value["extraction_sha256"] = sha256(value)
    return value


def _grep_at(
    root: Path, revision: str, terms: Sequence[str], roots: Sequence[str], *, limit: int
) -> tuple[list[tuple[str, int, str]], bool]:
    if not terms:
        return [], False
    command = ["git", "-C", str(root), "grep", "-n", "-F"]
    for term in terms:
        command.extend(("-e", term))
    command.extend((revision, "--", *roots))
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert process.stdout is not None
    rows: list[tuple[str, int, str]] = []
    truncated = False
    deadline = time.monotonic() + 30
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, 30)
            ready, _write, _error = select.select([process.stdout], [], [], remaining)
            if not ready:
                raise subprocess.TimeoutExpired(command, 30)
            raw = process.stdout.readline()
            if not raw:
                break
            try:
                path, line, excerpt = raw.decode("utf-8", errors="replace").rstrip("\n").removeprefix(f"{revision}:").split(":", 2)
                rows.append((path, int(line), excerpt))
            except ValueError:
                raise PrImpactError("git candidate search returned an invalid match") from None
            if len(rows) >= limit:
                truncated = True
                process.kill()
                break
        _stdout, stderr = process.communicate(timeout=30)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.communicate()
        raise PrImpactError("git candidate search timed out") from exc
    if not truncated and process.returncode not in {0, 1}:
        raise PrImpactError(stderr.decode("utf-8", errors="replace").strip() or "git candidate search failed")
    return rows, truncated


def inspect_source_questions(
    context: Mapping[str, Any], extraction: Mapping[str, Any], plan: Mapping[str, Any]
) -> dict[str, Any]:
    source = context["source"]
    root, revision = Path(str(source["local_root"])), str(source["target_revision"])
    rows: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    for question in plan["questions"]:
        if len(rows) >= MAX_SOURCE_READS:
            gaps.append({"question_id": question["id"], "status": "unavailable", "reason": "source_read_budget_exhausted"})
            continue
        try:
            matches, truncated = _grep_at(root, revision, question["source_terms"], [], limit=MAX_SOURCE_READS - len(rows))
            if truncated:
                gaps.append({"question_id": question["id"], "status": "unavailable", "reason": "source_read_budget_exhausted"})
            for path, line, excerpt in matches:
                raw = _git(root, "show", f"{revision}:{path}")
                for term in question["source_terms"]:
                    if term in excerpt:
                        rows.append({"question_id": question["id"], "evidence_ids": question["evidence_ids"], "path": path, "line": line, "excerpt": excerpt, "matched_term": term, "source_blob_sha256": hashlib.sha256(raw).hexdigest(), "status": "available"})
        except PrImpactError as exc:
            gaps.append({"question_id": question["id"], "status": "unavailable", "reason": str(exc)})
    value: dict[str, Any] = {"schema_version": "0.1", "artifact_kind": "greenfield_harness_source_tool_ledger", "extraction_sha256": extraction["extraction_sha256"], "evidence": rows[:MAX_SOURCE_READS], "gaps": gaps}
    value["tool_ledger_sha256"] = sha256(value)
    return value


def inspect_candidates(context: Mapping[str, Any], replan: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    term_questions: dict[str, list[dict[str, Any]]] = {}
    for question in replan["questions"]:
        for term in question["source_terms"]:
            term_questions.setdefault(term, []).append({"question": question, "origin": "exact_source"})
        for term in question["ai_terms"]:
            term_questions.setdefault(term, []).append({"question": question, "origin": "ai_expanded"})
    terms = sorted(term_questions)
    discovery, evidence, gaps = [], [], []
    for candidate in context["candidate_repositories"]:
        root, revision = Path(candidate["local_root"]), candidate["revision"]
        matched = []
        try:
            file_blobs: dict[str, str] = {}
            matches, truncated = _grep_at(root, revision, terms, [*candidate["test_roots"], ".github/workflows"], limit=MAX_CANDIDATE_MATCHES_PER_REPOSITORY)
            if truncated:
                gaps.append({"repository": candidate["repository"], "status": "unavailable", "reason": "candidate_match_budget_exhausted"})
            for path, line, excerpt in matches:
                blob = file_blobs.get(path)
                if blob is None:
                    raw = _git(root, "show", f"{revision}:{path}")
                    blob = hashlib.sha256(raw).hexdigest()
                    file_blobs[path] = blob
                for term in terms:
                    if term in excerpt:
                        for provenance in term_questions[term]:
                            question = provenance["question"]
                            row = {"repository": candidate["repository"], "revision": revision, "path": path, "line": line, "excerpt": excerpt, "source_blob_sha256": blob, "matched_value": term, "term_origin": provenance["origin"], "question_id": question["id"], "source_evidence_ids": question["evidence_ids"], "kind": "workflow" if path.startswith(".github/workflows/") else "test", "status": "available"}
                            evidence.append(row)
                            matched.append(row)
        except PrImpactError as exc:
            gaps.append({"repository": candidate["repository"], "status": "unavailable", "reason": str(exc)})
        discovery.append({"repository": candidate["repository"], "revision": revision, "status": "candidate" if matched else "no_evidence", "match_count": len(matched)})
    discovery_value = {"schema_version": "0.1", "artifact_kind": "greenfield_harness_candidate_discovery", "planning_replan_sha256": replan["replan_sha256"], "candidates": discovery, "gaps": gaps}
    discovery_value["discovery_sha256"] = sha256(discovery_value)
    evidence_value = {"schema_version": "0.1", "artifact_kind": "greenfield_harness_test_evidence", "discovery_sha256": discovery_value["discovery_sha256"], "evidence": evidence, "ci_execution": {"status": "unavailable", "reason": "live_ci_evidence_out_of_scope"}, "gaps": gaps}
    evidence_value["test_evidence_sha256"] = sha256(evidence_value)
    return discovery_value, evidence_value


def assess_and_recommend(initial: Mapping[str, Any], evidence: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    coverage, recommendations = [], []
    for behavior in initial["behaviors"]:
        source_ids = set(behavior["evidence_ids"])
        matches = [row for row in evidence["evidence"] if source_ids & set(row["source_evidence_ids"]) and row["kind"] == "test"]
        exact = [row for row in matches if row["term_origin"] == "exact_source"]
        status = "strong_candidate" if exact else ("candidate" if matches else "no_evidence")
        row = {"behavior_id": behavior["id"], "status": status, "source_evidence_ids": sorted(source_ids), "test_evidence": [{"repository": item["repository"], "revision": item["revision"], "path": item["path"], "line": item["line"], "source_blob_sha256": item["source_blob_sha256"], "term_origin": item["term_origin"], "question_id": item["question_id"]} for item in matches], "ci_execution": evidence["ci_execution"]}
        coverage.append(row)
        if not matches:
            recommendations.append({"id": f"recommendation:{behavior['id']}", "status": "candidate", "behavior_id": behavior["id"], "recommendation": f"Add a revision-pinned test that exercises: {behavior['summary']}", "source_evidence_ids": sorted(source_ids), "reason": "no_matching_pinned_test_evidence"})
    assessment = {"schema_version": "0.1", "artifact_kind": "greenfield_harness_coverage_assessment", "planning_initial_sha256": initial["initial_sha256"], "test_evidence_sha256": evidence["test_evidence_sha256"], "coverage": coverage}
    assessment["assessment_sha256"] = sha256(assessment)
    proposal = {"schema_version": "0.1", "artifact_kind": "greenfield_harness_test_recommendations", "assessment_sha256": assessment["assessment_sha256"], "recommendations": recommendations}
    proposal["recommendations_sha256"] = sha256(proposal)
    return assessment, proposal


def run_pr_impact(*, source_root: Path, output_dir: Path, pr: int, base_revision: str, target_revision: str, candidates: Sequence[Mapping[str, Any]], provider: PlannerProvider, input_paths: Sequence[Path] = ()) -> dict[str, Path]:
    root = output_dir.resolve()
    parent = (Path.cwd() / "artifacts" / "greenfield-harness").resolve()
    if root.parent != parent or root.exists():
        raise PrImpactError("output-dir must be a new direct child of artifacts/greenfield-harness")
    context = _capture(source_root, pr, base_revision, target_revision, candidates, input_paths)
    root.mkdir(parents=True)
    handoff, paths = ImpactHandoff(root, context["source"]), {}
    paths["context"] = write_json(root / "pr-impact-run-context.json", context)
    handoff.complete("capture", inputs={}, outputs={"context": paths["context"]})
    extraction = deterministic_extraction(context)
    paths["extraction"] = write_json(root / "deterministic-extraction.json", extraction)
    handoff.complete("extract", inputs={"context": paths["context"]}, outputs={"extraction": paths["extraction"]})
    try:
        initial = initial_plan(provider.initial_plan({"source": context["source"], "extraction": extraction["items"]}), extraction)
    except Exception as exc:  # noqa: BLE001 - provider failures must become retained evidence
        failure = {"schema_version": "0.1", "artifact_kind": "greenfield_harness_planner_failure", "stage": "planner_initial", "reason": str(exc)[:500], "extraction_sha256": extraction["extraction_sha256"]}
        paths["failure"] = write_json(root / "planner-failure.json", failure)
        handoff.complete("planner_initial", inputs={"extraction": paths["extraction"]}, outputs={"failure": paths["failure"]}, status="blocked")
        paths["handoff"] = handoff.finish(status="blocked")
        raise PrImpactError("planner initial turn failed; retained blocked bundle")
    initial["initial_sha256"] = sha256(initial)
    paths["initial"] = write_json(root / "planner-initial.json", initial)
    handoff.complete("planner_initial", inputs={"extraction": paths["extraction"]}, outputs={"initial": paths["initial"]})
    ledger = inspect_source_questions(context, extraction, initial)
    paths["ledger"] = write_json(root / "tool-ledger.json", ledger)
    handoff.complete("source_investigation", inputs={"initial": paths["initial"]}, outputs={"tool_ledger": paths["ledger"]}, status="degraded" if ledger["gaps"] else "succeeded")
    try:
        replan = test_plan(provider.replan({"source": context["source"], "extraction": extraction["items"], "initial": initial, "source_ledger": ledger}), extraction, behaviors=initial["behaviors"])
    except Exception as exc:  # noqa: BLE001 - provider failures must become retained evidence
        failure = {"schema_version": "0.1", "artifact_kind": "greenfield_harness_planner_failure", "stage": "planner_replan", "reason": str(exc)[:500], "tool_ledger_sha256": ledger["tool_ledger_sha256"]}
        paths["failure"] = write_json(root / "planner-failure.json", failure)
        handoff.complete("planner_replan", inputs={"tool_ledger": paths["ledger"]}, outputs={"failure": paths["failure"]}, status="blocked")
        paths["handoff"] = handoff.finish(status="blocked")
        raise PrImpactError("planner replan turn failed; retained blocked bundle")
    replan["replan_sha256"] = sha256(replan)
    paths["replan"] = write_json(root / "planner-replan.json", replan)
    handoff.complete("planner_replan", inputs={"tool_ledger": paths["ledger"]}, outputs={"replan": paths["replan"]})
    planning = planning_report(extraction=extraction, initial=initial, source_ledger=ledger, replan=replan)
    paths["planning"] = write_json(root / "planning-report.json", planning)
    discovery, test_evidence = inspect_candidates(context, replan)
    paths["discovery"] = write_json(root / "candidate-discovery.json", discovery)
    handoff.complete("candidate_discovery", inputs={"planning": paths["planning"]}, outputs={"discovery": paths["discovery"]})
    paths["test_evidence"] = write_json(root / "test-evidence.json", test_evidence)
    handoff.complete("test_inspection", inputs={"discovery": paths["discovery"]}, outputs={"test_evidence": paths["test_evidence"]})
    assessment, recommendations = assess_and_recommend(initial, test_evidence)
    paths["assessment"] = write_json(root / "coverage-assessment.json", assessment)
    handoff.complete("coverage_assessment", inputs={"test_evidence": paths["test_evidence"]}, outputs={"assessment": paths["assessment"]})
    paths["recommendations"] = write_json(root / "test-recommendations.json", recommendations)
    handoff.complete("test_recommendations", inputs={"assessment": paths["assessment"]}, outputs={"recommendations": paths["recommendations"]})
    analysis = {"schema_version": "0.1", "artifact_kind": "greenfield_harness_pr_impact_analysis", "context_sha256": context["context_sha256"], "planning_sha256": planning["planning_sha256"], "coverage_assessment_sha256": assessment["assessment_sha256"], "recommendations_sha256": recommendations["recommendations_sha256"], "behaviors": initial["behaviors"], "coverage": assessment["coverage"], "recommendations": recommendations["recommendations"], "provenance": context["provenance"]}
    analysis["analysis_sha256"] = sha256(analysis)
    paths["analysis"] = write_json(root / "pr-impact-analysis.json", analysis)
    handoff.complete("analyze", inputs={"assessment": paths["assessment"], "recommendations": paths["recommendations"]}, outputs={"analysis": paths["analysis"]})
    markdown = "# Harness PR-impact report\n\nCanonical analysis: `pr-impact-analysis.json`\n\n```json\n" + json.dumps({"coverage": analysis["coverage"], "recommendations": analysis["recommendations"]}, indent=2) + "\n```\n"
    paths["markdown"] = write_text(root / "pr-impact-report.md", markdown)
    handoff.complete("project", inputs={"analysis": paths["analysis"]}, outputs={"markdown": paths["markdown"]})
    paths["handoff"] = handoff.finish(status="complete")
    return paths
