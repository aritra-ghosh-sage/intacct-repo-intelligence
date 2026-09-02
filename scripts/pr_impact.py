"""Small, read-only PR-impact experiment for the Greenfield harness."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

import yaml

from .artifacts import file_sha256, sha256, write_json, write_text

STAGES = (
    "capture",
    "extract",
    "ai_summary",
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


class PrImpactError(ValueError):
    pass


class ImpactProvider(Protocol):
    def summarize(self, case_summary: Mapping[str, Any]) -> Mapping[str, Any]: ...


def _git(root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(root), *args], check=False, capture_output=True, timeout=30
    )
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


def _validate_summary(value: Mapping[str, Any], extraction: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {str(row["id"]) for row in extraction["items"]}
    behaviors = value.get("behaviors")
    if not isinstance(behaviors, list):
        raise PrImpactError("AI summary must contain behaviors")
    normalized = []
    for index, row in enumerate(behaviors):
        if not isinstance(row, Mapping) or not isinstance(row.get("summary"), str) or not row["summary"].strip():
            raise PrImpactError("AI behavior summary is invalid")
        evidence = row.get("evidence_ids")
        if not isinstance(evidence, list) or not evidence or any(not isinstance(item, str) or item not in allowed for item in evidence):
            raise PrImpactError("AI behavior must cite extracted evidence IDs")
        normalized.append({"id": str(row.get("id") or f"behavior:{index + 1}"), "summary": row["summary"].strip(), "evidence_ids": sorted(set(evidence)), "status": "candidate"})
    return {"schema_version": "0.1", "artifact_kind": "greenfield_harness_ai_behavior_summary", "extraction_sha256": extraction["extraction_sha256"], "behaviors": sorted(normalized, key=lambda row: row["id"])}


def _files_at(root: Path, revision: str, roots: Sequence[str]) -> list[str]:
    paths = set()
    for prefix in roots:
        output = _git(root, "ls-tree", "-r", "--name-only", revision, "--", prefix).decode().splitlines()
        paths.update(output)
    return sorted(paths)


def _matching_lines(text: str, terms: Sequence[str]) -> list[tuple[str, int, str]]:
    result = []
    for number, line in enumerate(text.splitlines(), 1):
        for term in terms:
            if term and term in line:
                result.append((term, number, line))
    return result


def inspect_candidates(context: Mapping[str, Any], extraction: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    terms = sorted({str(row["value"]) for row in extraction["items"]})
    discovery, evidence, gaps = [], [], []
    for candidate in context["candidate_repositories"]:
        root, revision = Path(candidate["local_root"]), candidate["revision"]
        matched = []
        try:
            for path in _files_at(root, revision, [*candidate["test_roots"], ".github/workflows"]):
                raw = _git(root, "show", f"{revision}:{path}")
                text = raw.decode("utf-8", errors="replace")
                blob = hashlib.sha256(raw).hexdigest()
                for term, line, excerpt in _matching_lines(text, terms):
                    row = {"repository": candidate["repository"], "revision": revision, "path": path, "line": line, "excerpt": excerpt, "source_blob_sha256": blob, "matched_value": term, "kind": "workflow" if path.startswith(".github/workflows/") else "test", "status": "available"}
                    evidence.append(row)
                    matched.append(row)
        except PrImpactError as exc:
            gaps.append({"repository": candidate["repository"], "status": "unavailable", "reason": str(exc)})
        discovery.append({"repository": candidate["repository"], "revision": revision, "status": "candidate" if matched else "no_evidence", "match_count": len(matched)})
    discovery_value = {"schema_version": "0.1", "artifact_kind": "greenfield_harness_candidate_discovery", "extraction_sha256": extraction["extraction_sha256"], "candidates": discovery, "gaps": gaps}
    discovery_value["discovery_sha256"] = sha256(discovery_value)
    evidence_value = {"schema_version": "0.1", "artifact_kind": "greenfield_harness_test_evidence", "discovery_sha256": discovery_value["discovery_sha256"], "evidence": evidence, "ci_execution": {"status": "unavailable", "reason": "live_ci_evidence_out_of_scope"}, "gaps": gaps}
    evidence_value["test_evidence_sha256"] = sha256(evidence_value)
    return discovery_value, evidence_value


def assess_and_recommend(summary: Mapping[str, Any], extraction: Mapping[str, Any], evidence: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    coverage, recommendations = [], []
    for behavior in summary["behaviors"]:
        source_ids = set(behavior["evidence_ids"])
        source_values = {str(row["value"]) for row in extraction["items"] if row["id"] in source_ids}
        matches = [row for row in evidence["evidence"] if row["matched_value"] in source_values and row["kind"] == "test"]
        status = "strong_candidate" if matches else "no_evidence"
        row = {"behavior_id": behavior["id"], "status": status, "source_evidence_ids": sorted(source_ids), "test_evidence": [{"repository": item["repository"], "revision": item["revision"], "path": item["path"], "line": item["line"], "source_blob_sha256": item["source_blob_sha256"]} for item in matches], "ci_execution": evidence["ci_execution"]}
        coverage.append(row)
        if not matches:
            recommendations.append({"id": f"recommendation:{behavior['id']}", "status": "candidate", "behavior_id": behavior["id"], "recommendation": f"Add a revision-pinned test that exercises: {behavior['summary']}", "source_evidence_ids": sorted(source_ids), "reason": "no_matching_pinned_test_evidence"})
    assessment = {"schema_version": "0.1", "artifact_kind": "greenfield_harness_coverage_assessment", "ai_summary_sha256": summary["ai_summary_sha256"], "test_evidence_sha256": evidence["test_evidence_sha256"], "coverage": coverage}
    assessment["assessment_sha256"] = sha256(assessment)
    proposal = {"schema_version": "0.1", "artifact_kind": "greenfield_harness_test_recommendations", "assessment_sha256": assessment["assessment_sha256"], "recommendations": recommendations}
    proposal["recommendations_sha256"] = sha256(proposal)
    return assessment, proposal


def run_pr_impact(*, source_root: Path, output_dir: Path, pr: int, base_revision: str, target_revision: str, candidates: Sequence[Mapping[str, Any]], provider: ImpactProvider, input_paths: Sequence[Path] = ()) -> dict[str, Path]:
    root = output_dir.resolve()
    parent = (Path.cwd() / "artifacts" / "greenfield-harness").resolve()
    if root.parent != parent or root.exists():
        raise PrImpactError("output-dir must be a new direct child of artifacts/greenfield-harness")
    root.mkdir(parents=True)
    context = _capture(source_root, pr, base_revision, target_revision, candidates, input_paths)
    handoff, paths = ImpactHandoff(root, context["source"]), {}
    paths["context"] = write_json(root / "pr-impact-run-context.json", context)
    handoff.complete("capture", inputs={}, outputs={"context": paths["context"]})
    extraction = deterministic_extraction(context)
    paths["extraction"] = write_json(root / "deterministic-extraction.json", extraction)
    handoff.complete("extract", inputs={"context": paths["context"]}, outputs={"extraction": paths["extraction"]})
    try:
        raw_summary = provider.summarize({"source": context["source"], "extraction": extraction["items"]})
        summary = _validate_summary(raw_summary, extraction)
    except Exception as exc:  # noqa: BLE001 - provider failures must become retained evidence
        failure = {"schema_version": "0.1", "artifact_kind": "greenfield_harness_pr_impact_failure", "stage": "ai_summary", "reason": str(exc)[:500], "extraction_sha256": extraction["extraction_sha256"]}
        paths["failure"] = write_json(root / "failure.json", failure)
        handoff.complete("ai_summary", inputs={"extraction": paths["extraction"]}, outputs={"failure": paths["failure"]}, status="blocked")
        paths["handoff"] = handoff.finish(status="blocked")
        raise PrImpactError("Strands/AWS impact summary failed; retained blocked bundle")
    summary["ai_summary_sha256"] = sha256(summary)
    paths["summary"] = write_json(root / "ai-behavior-summary.json", summary)
    handoff.complete("ai_summary", inputs={"extraction": paths["extraction"]}, outputs={"summary": paths["summary"]})
    discovery, test_evidence = inspect_candidates(context, extraction)
    paths["discovery"] = write_json(root / "candidate-discovery.json", discovery)
    handoff.complete("candidate_discovery", inputs={"summary": paths["summary"]}, outputs={"discovery": paths["discovery"]})
    paths["test_evidence"] = write_json(root / "test-evidence.json", test_evidence)
    handoff.complete("test_inspection", inputs={"discovery": paths["discovery"]}, outputs={"test_evidence": paths["test_evidence"]})
    assessment, recommendations = assess_and_recommend(summary, extraction, test_evidence)
    paths["assessment"] = write_json(root / "coverage-assessment.json", assessment)
    handoff.complete("coverage_assessment", inputs={"test_evidence": paths["test_evidence"]}, outputs={"assessment": paths["assessment"]})
    paths["recommendations"] = write_json(root / "test-recommendations.json", recommendations)
    handoff.complete("test_recommendations", inputs={"assessment": paths["assessment"]}, outputs={"recommendations": paths["recommendations"]})
    analysis = {"schema_version": "0.1", "artifact_kind": "greenfield_harness_pr_impact_analysis", "context_sha256": context["context_sha256"], "ai_summary_sha256": summary["ai_summary_sha256"], "coverage_assessment_sha256": assessment["assessment_sha256"], "recommendations_sha256": recommendations["recommendations_sha256"], "coverage": assessment["coverage"], "recommendations": recommendations["recommendations"], "provenance": context["provenance"]}
    analysis["analysis_sha256"] = sha256(analysis)
    paths["analysis"] = write_json(root / "pr-impact-analysis.json", analysis)
    handoff.complete("analyze", inputs={"assessment": paths["assessment"], "recommendations": paths["recommendations"]}, outputs={"analysis": paths["analysis"]})
    markdown = "# Harness PR-impact report\n\nCanonical analysis: `pr-impact-analysis.json`\n\n```json\n" + json.dumps({"coverage": analysis["coverage"], "recommendations": analysis["recommendations"]}, indent=2) + "\n```\n"
    paths["markdown"] = write_text(root / "pr-impact-report.md", markdown)
    handoff.complete("project", inputs={"analysis": paths["analysis"]}, outputs={"markdown": paths["markdown"]})
    paths["handoff"] = handoff.finish(status="complete")
    return paths
