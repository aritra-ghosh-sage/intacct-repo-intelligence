"""Deterministic cross-reference of PR-analysis evidence against a persisted
``TestInventory`` artifact.

This module is intentionally agent-free: it only reads an already-persisted
``inventory.json`` (produced by ``test_inventory.persist_test_inventory``) and
classifies each changed PR path as ``covered`` (a matching executable suite),
``partial`` (only a heuristic module/API-object match, or a fixture-only
suite), or ``gap`` (no match at all). Gaps are conservative by construction:
an ambiguous or unresolved match is a ``gap``, never a false ``covered``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from . import test_inventory as ti

MAX_MATCHED_SUITE_IDS = 50
MAX_SUGGESTED_STUBS = 20

CoverageStatus = Literal["covered", "partial", "gap"]
MatchBasis = Literal["path", "module_api_object", "none"]


class _CoverageStrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CoverageFinding(_CoverageStrictModel):
    changed_path: str = Field(min_length=1)
    status: CoverageStatus
    matched_suite_ids: list[str] = Field(default_factory=list, max_length=MAX_MATCHED_SUITE_IDS)
    matched_suite_count: int = Field(ge=0)
    match_basis: MatchBasis
    reason: str = Field(min_length=1, max_length=200)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)


class CoverageGap(_CoverageStrictModel):
    changed_path: str = Field(min_length=1)
    suggested_area: str = Field(min_length=1, max_length=120)
    suggested_tags: list[str] = Field(default_factory=list, max_length=10)
    reason: str = Field(min_length=1, max_length=200)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)


class SuggestedTestArtifact(_CoverageStrictModel):
    evidence_id: str = Field(min_length=1)
    relative_path: str = Field(pattern=r"^evidence/[^/].*")
    description: str = Field(min_length=1, max_length=200)


class InventoryIdentity(_CoverageStrictModel):
    repository_id: str
    head: str | None = None
    inventory_digest: str


class TestInventoryCoverage(_CoverageStrictModel):
    status: Literal["ok", "unavailable"]
    inventory_identity: InventoryIdentity | None = None
    findings: list[CoverageFinding] = Field(default_factory=list, max_length=1000)
    gaps: list[CoverageGap] = Field(default_factory=list, max_length=1000)
    suggested_artifacts: list[SuggestedTestArtifact] = Field(default_factory=list, max_length=MAX_SUGGESTED_STUBS)
    metrics: dict[str, int] = Field(default_factory=dict)
    diagnostics: list[str] = Field(default_factory=list, max_length=20)


def load_persisted_test_inventory(inventory_path: Path) -> ti.TestInventory:
    """Reconstruct a ``TestInventory`` from a persisted ``inventory.json``."""

    path = Path(inventory_path)
    if not path.is_absolute():
        raise ValueError("test inventory path must be absolute")
    if not path.is_file():
        raise FileNotFoundError(f"test inventory artifact not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"test inventory artifact is not readable JSON: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != ti.SCHEMA:
        raise ValueError(f"unsupported test inventory schema: {payload.get('schema') if isinstance(payload, dict) else None!r}")
    try:
        repository = ti.InventoryRepository(**payload["repository"])
        suites = tuple(
            ti.InventorySuite(
                suite_id=suite["suite_id"],
                module=suite["module"],
                category=suite["category"],
                feature_files=tuple(suite["feature_files"]),
                input_files=tuple(suite["input_files"]),
                output_files=tuple(suite["output_files"]),
                scenarios=tuple(
                    ti.InventoryScenario(
                        name=scenario["name"],
                        tags=tuple(scenario["tags"]),
                        fixtures=tuple(scenario["fixtures"]),
                        methods=tuple(scenario["methods"]),
                    )
                    for scenario in suite["scenarios"]
                ),
                tags=tuple(suite["tags"]),
                api_objects=tuple(suite["api_objects"]),
                methods=tuple(suite["methods"]),
            )
            for suite in payload["suites"]
        )
        gaps = tuple(ti.InventoryGap(**gap) for gap in payload["gaps"])
    except (KeyError, TypeError) as exc:
        raise ValueError(f"test inventory artifact is malformed: {exc}") from exc
    return ti.TestInventory(
        schema=payload["schema"],
        status=payload["status"],
        repository=repository,
        suites=suites,
        gaps=gaps,
        metrics=dict(payload.get("metrics", {})),
    )


def _normalize_path(value: str) -> str:
    return value.strip().strip("/").lower()


def _build_file_index(inventory: ti.TestInventory) -> dict[str, str]:
    index: dict[str, str] = {}
    for suite in inventory.suites:
        for file_path in (*suite.feature_files, *suite.input_files, *suite.output_files):
            index[_normalize_path(file_path)] = suite.suite_id
    return index


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(value: str) -> set[str]:
    return set(_TOKEN_RE.findall(value.lower()))


def _module_token(changed_path: str) -> str | None:
    parts = [part for part in Path(changed_path).parts if part not in ("app", "source")]
    return parts[0] if parts else None


def _build_module_index(inventory: ti.TestInventory) -> dict[str, set[str]]:
    index: dict[str, set[str]] = {}
    for suite in inventory.suites:
        if suite.module:
            index.setdefault(suite.module.lower(), set()).add(suite.suite_id)
    return index


def _build_object_token_index(inventory: ti.TestInventory) -> dict[str, set[str]]:
    index: dict[str, set[str]] = {}
    for suite in inventory.suites:
        for api_object in suite.api_objects:
            for token in _tokenize(api_object):
                index.setdefault(token, set()).add(suite.suite_id)
    return index


def _known_test_paths_for(test_areas: list, changed_path: str) -> list[str]:
    """Best-effort recovery of the test paths a host-generated area attaches
    to one changed file, using the deterministic area-naming convention in
    ``pr_analysis._affected_test_areas``. This is a heuristic, not an exact
    join: model-authored areas are matched the same way and may be missed.
    """

    return [
        path
        for area in test_areas
        if changed_path in area.area
        for path in area.paths
    ]


def _suite_is_executable(inventory: ti.TestInventory, suite_id: str) -> bool:
    for suite in inventory.suites:
        if suite.suite_id == suite_id:
            return suite.category in ("executable", "executable_and_fixture")
    return False


def render_suggested_stub(gap: CoverageGap) -> str:
    """Render a deterministic Gherkin scaffold stub for one coverage gap."""

    tags = " ".join(sorted({"@needs-review", *gap.suggested_tags}))
    lines = [
        tags,
        f"Feature: {gap.suggested_area}",
        "",
        f"  # Suggested by ia_repomap test-inventory coverage cross-reference: {gap.reason}",
        f"  Scenario: Cover changes in {gap.changed_path}",
        "    Given a request exercising the changed behavior",
        "    When the request is submitted",
        "    Then the response confirms the expected behavior",
        "",
    ]
    return "\n".join(lines)


def _slug(value: str) -> str:
    token = _TOKEN_RE.sub("-", value.lower()).strip("-")
    return re.sub(r"-{2,}", "-", token) or "gap"


def evaluate_test_coverage(
    changed_paths: list[str],
    test_areas: list,
    inventory: ti.TestInventory,
    *,
    inventory_evidence_id: str,
) -> TestInventoryCoverage:
    """Classify each changed path as covered, partial, or a gap.

    ``test_areas`` are the report's existing ``TestArea`` rows (host- and
    model-authored); they supply the already-known candidate test paths used
    for the path-based match. ``inventory_evidence_id`` is the evidence id the
    caller registered for the loaded inventory file; every finding cites it.
    """

    file_index = _build_file_index(inventory)
    module_index = _build_module_index(inventory)
    object_index = _build_object_token_index(inventory)

    findings: list[CoverageFinding] = []
    gaps: list[CoverageGap] = []
    suggested: list[SuggestedTestArtifact] = []
    truncated_stubs = 0
    truncated_match_findings = 0
    omitted_match_ids = 0

    for changed_path in sorted(dict.fromkeys(changed_paths)):
        known_paths = _known_test_paths_for(test_areas, changed_path)
        matched: set[str] = set()
        module_only_match = False
        basis: MatchBasis = "none"
        for known_path in known_paths:
            suite_id = file_index.get(_normalize_path(known_path))
            if suite_id:
                matched.add(suite_id)
        if matched:
            basis = "path"
        else:
            module = _module_token(changed_path)
            module_candidates: set[str] = set()
            object_candidates: set[str] = set()
            if module:
                module_candidates |= module_index.get(module.lower(), set())
                object_candidates |= object_index.get(module.lower(), set())
            for token in _tokenize(Path(changed_path).stem):
                object_candidates |= object_index.get(token, set())
            if object_candidates:
                matched = object_candidates
                basis = "module_api_object"
            elif module_candidates:
                matched = module_candidates
                module_only_match = True
                basis = "module_api_object"

        if matched and basis == "path":
            executable = any(_suite_is_executable(inventory, suite_id) for suite_id in matched)
            status: CoverageStatus = "covered" if executable else "partial"
            reason = (
                f"Ripwire-identified test path(s) for {changed_path} matched inventory "
                f"suite(s): {', '.join(sorted(matched))}."
            )
        elif module_only_match and len(matched) > MAX_MATCHED_SUITE_IDS:
            status = "gap"
            reason = (
                f"Module heuristic for {changed_path} matched {len(matched)} inventory suites; "
                "the result is too broad to establish candidate coverage."
            )
        elif matched:
            status = "partial"
            reason = (
                f"No direct test-path match for {changed_path}; matched inventory "
                f"suite(s) by module/API-object heuristic: {', '.join(sorted(matched))}."
            )
        else:
            status = "gap"
            reason = f"No inventory suite matched {changed_path} by path or module/API-object heuristic."

        matched_suite_ids = sorted(matched)[:MAX_MATCHED_SUITE_IDS]
        omitted = len(matched) - len(matched_suite_ids)
        if omitted:
            truncated_match_findings += 1
            omitted_match_ids += omitted
        findings.append(CoverageFinding(
            changed_path=changed_path,
            status=status,
            matched_suite_ids=matched_suite_ids,
            matched_suite_count=len(matched),
            match_basis=basis,
            reason=reason[:200],
            evidence_ids=[inventory_evidence_id],
        ))

        if status == "gap":
            module = _module_token(changed_path) or "general"
            gap_index = len(gaps) + 1
            stub_available = len(suggested) < MAX_SUGGESTED_STUBS
            gap_evidence_id = f"test-coverage-gap-{gap_index:03d}" if stub_available else inventory_evidence_id
            gap = CoverageGap(
                changed_path=changed_path,
                suggested_area=f"Add test coverage for {changed_path}"[:120],
                suggested_tags=[f"@{module}"],
                reason=reason[:200],
                evidence_ids=[gap_evidence_id],
            )
            gaps.append(gap)
            if stub_available:
                stub_slug = _slug(changed_path)
                suggested.append(SuggestedTestArtifact(
                    evidence_id=gap_evidence_id,
                    relative_path=f"evidence/coverage/suggested/{stub_slug}.feature.suggested",
                    description=f"Suggested scaffold scenario for {changed_path}",
                ))
            else:
                truncated_stubs += 1

    metrics = {
        "changed_paths": len(changed_paths),
        "covered": sum(1 for f in findings if f.status == "covered"),
        "partial": sum(1 for f in findings if f.status == "partial"),
        "gap": sum(1 for f in findings if f.status == "gap"),
        "truncated_match_findings": truncated_match_findings,
        "omitted_match_ids": omitted_match_ids,
    }
    diagnostics = []
    if truncated_match_findings:
        diagnostics.append(
            f"{omitted_match_ids} matched suite id(s) omitted across "
            f"{truncated_match_findings} finding(s) by the {MAX_MATCHED_SUITE_IDS}-id cap"
        )
    if truncated_stubs:
        diagnostics.append(
            f"{truncated_stubs} coverage gap(s) exceeded the {MAX_SUGGESTED_STUBS} suggested-stub cap"
        )

    return TestInventoryCoverage(
        status="ok",
        inventory_identity=InventoryIdentity(
            repository_id=inventory.repository.repository_id,
            head=inventory.repository.head,
            inventory_digest=inventory.repository.inventory_digest,
        ),
        findings=findings,
        gaps=gaps,
        suggested_artifacts=suggested,
        metrics=metrics,
        diagnostics=diagnostics,
    )
