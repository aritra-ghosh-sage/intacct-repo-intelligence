"""Validated v1 PR-analysis report contract.

This module contains only the report models. Orchestration and persistence are
deliberately left for the coordinator implementation slice.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import PrContextRequest, PrContextResult
from .pr_context import build_pr_context

Confidence = Literal["candidate", "unresolved", "unavailable"]
Status = Literal["ok", "unavailable", "error"]
Phase = Literal["request_validation", "readiness", "pr_context", "analysis", "persistence"]
Relationship = Literal["direct_caller", "transitive_reacher", "source_reference"]
Change = Literal["A", "M", "D", "R", "C"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("path", "source_path", "target_path", mode="before", check_fields=False)
    @classmethod
    def repository_path(cls, value: str) -> str:
        if not isinstance(value, str) or not value or value.startswith("/"):
            raise ValueError("path must be a non-empty repository-relative path")
        if ".." in value.split("/"):
            raise ValueError("path must not contain traversal")
        return value


class Request(StrictModel):
    repository: str = Field(min_length=1)
    base: str = Field(min_length=1)
    analysis_schema: Literal["ia-repomap.pr-analysis/v1"]


class Identity(StrictModel):
    repository: str = Field(min_length=1)
    head: str = Field(pattern=r"^[0-9a-f]{40}$")
    base: str = Field(pattern=r"^[0-9a-f]{40}$")
    merge_base: str = Field(pattern=r"^[0-9a-f]{40}$")
    configuration_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    engine_identity: str = Field(min_length=1)


class Summary(StrictModel):
    purpose: str
    behavioral_change: str
    confidence: Confidence


class Symbol(StrictModel):
    path: str
    name: str = Field(min_length=1)
    line: int | None = Field(default=None, ge=1)
    kind: str | None = None
    confidence: Confidence


class ChangedFile(StrictModel):
    path: str
    change: Change
    symbols: list[Symbol] = Field(max_length=100)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)


class BlastRadiusRow(StrictModel):
    source_path: str
    source_symbol: str = Field(min_length=1)
    target_path: str
    target_symbol: str = Field(min_length=1)
    relationship: Relationship
    graph_distance: int | None = Field(default=None, ge=1)
    confidence: Confidence
    evidence_ids: list[str] = Field(min_length=1, max_length=20)
    reason: str = Field(min_length=1)


class TestArea(StrictModel):
    area: str = Field(min_length=1)
    paths: list[str] = Field(max_length=100)
    reason: str = Field(min_length=1)
    confidence: Confidence
    evidence_ids: list[str] = Field(min_length=1, max_length=20)
    execution_status: Literal["not_run"]


class Gap(StrictModel):
    kind: str = Field(min_length=1)
    detail: str = Field(min_length=1)
    count: int | None = Field(default=None, ge=0)


class Evidence(StrictModel):
    evidence_id: str = Field(min_length=1)
    kind: Literal["pr_context_xml", "symbol_impact_xml", "inspection"]
    relative_path: str = Field(pattern=r"^[^/].*")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class Agent(StrictModel):
    model_id: str = Field(min_length=1)
    region: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    tool_contract_version: str = Field(min_length=1)
    coordinator_version: str = Field(min_length=1)


class PRAnalysisReportV1(StrictModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_: Literal["ia-repomap.pr-analysis/v1"] = Field(alias="schema")
    status: Status
    phase: Phase
    request: Request
    identity: Identity
    summary: Summary
    changed_files: list[ChangedFile] = Field(max_length=1000)
    blast_radius: list[BlastRadiusRow] = Field(max_length=1000)
    test_areas: list[TestArea] = Field(max_length=500)
    gaps: list[Gap] = Field(max_length=1000)
    evidence: list[Evidence] = Field(max_length=1000)
    diagnostics: list[str] = Field(max_length=100)
    remediation: list[str] = Field(max_length=100)
    metrics: dict[str, Any]
    agent: Agent


@dataclass(frozen=True)
class PreAgenticSeed:
    """Minimal host-owned result before any model is constructed."""

    status: str
    phase: str
    context: PrContextResult
    allowed_symbols: tuple[tuple[str, str], ...] = ()


def prepare_pre_agentic_seed(
    request: PrContextRequest,
    *,
    context_builder: Callable[[PrContextRequest], PrContextResult] = build_pr_context,
) -> PreAgenticSeed:
    """Run the deterministic gate; never prepare an index or call a model."""

    if not request.repo_root.is_absolute() or not request.artifact_root.is_absolute():
        return PreAgenticSeed("error", "request_validation", PrContextResult("error", diagnostics=["repository and artifact paths must be absolute"]))
    if request.base_ref.strip() == "":
        return PreAgenticSeed("error", "request_validation", PrContextResult("error", diagnostics=["base_ref must not be empty"]))

    context = context_builder(request)
    if context.status != "ok":
        return PreAgenticSeed(context.status, "pr_context", context)

    allowed = tuple(
        (symbol.path, symbol.name)
        for changed in context.changed_files
        for symbol in changed.symbols
    )
    if not allowed:
        return PreAgenticSeed("ok", "pr_context", context)
    return PreAgenticSeed("ok", "analysis", context, tuple(dict.fromkeys(allowed)))


__all__ = ["PRAnalysisReportV1", "PreAgenticSeed", "prepare_pre_agentic_seed"]
