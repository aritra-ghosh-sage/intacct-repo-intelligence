"""Validated v1 PR-analysis report contract.

This module contains only the report models. Orchestration and persistence are
deliberately left for the coordinator implementation slice.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Literal

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import PrContextRequest, PrContextResult, PrImpactRequest
from .impact import build_symbol_impact
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


class AgentAnalysisDraftV1(StrictModel):
    """Model-owned analysis only; provenance and metrics stay host-owned."""

    summary: Summary
    blast_radius: list[BlastRadiusRow] = Field(max_length=1000)
    test_areas: list[TestArea] = Field(max_length=500)


@dataclass(frozen=True)
class PreAgenticSeed:
    """Minimal host-owned result before any model is constructed."""

    status: str
    phase: str
    context: PrContextResult
    allowed_symbols: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    kind: str
    status: str
    sha256: str | None = None


class EvidenceSession:
    """Invocation-local evidence registry and candidate-target guard."""

    def __init__(self, seed: PreAgenticSeed) -> None:
        self._allowed_symbols = frozenset(seed.allowed_symbols)
        self._records: dict[str, EvidenceRecord] = {}
        self._impact_calls = 0

    def register(self, record: EvidenceRecord) -> None:
        if not record.evidence_id or record.evidence_id in self._records:
            raise ValueError("evidence_id must be non-empty and unique within the session")
        self._records[record.evidence_id] = record

    def require_evidence(self, evidence_ids: tuple[str, ...] | list[str]) -> None:
        missing = [item for item in evidence_ids if item not in self._records]
        if missing:
            raise ValueError(f"evidence IDs are not registered in this session: {', '.join(missing)}")

    def authorize_impact(self, symbol_path: str, symbol_name: str) -> None:
        if (symbol_path, symbol_name) not in self._allowed_symbols:
            raise ValueError("impact target is not a PR-context candidate symbol")

    @property
    def impact_calls(self) -> int:
        return self._impact_calls

    def consume_impact_call(self) -> None:
        if self._impact_calls >= 1:
            raise ValueError("MVP permits one symbol-impact call per analysis")
        self._impact_calls += 1


def run_symbol_impact_once(
    session: EvidenceSession,
    request: PrImpactRequest,
    *,
    impact_builder: Callable[[PrImpactRequest], Any],
) -> Any:
    """Run the single MVP impact expansion and register its evidence."""

    session.authorize_impact(request.symbol_path, request.symbol_name)
    session.consume_impact_call()
    result = impact_builder(request)
    if result.raw_xml:
        session.register(EvidenceRecord(
            evidence_id=f"symbol-impact-{session.impact_calls:03d}",
            kind="symbol_impact_xml",
            status=result.status,
            sha256=sha256(result.raw_xml.encode("utf-8")).hexdigest(),
        ))
    return result


def make_symbol_impact_tool(
    session: EvidenceSession,
    request: PrImpactRequest,
    *,
    impact_builder: Callable[[PrImpactRequest], Any] = build_symbol_impact,
) -> Any:
    """Create the one Strands tool bound to this analysis invocation."""

    try:
        from strands import tool
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError("strands-agents is required for the impact tool") from exc

    @tool
    def symbol_impact(symbol_path: str, symbol_name: str) -> dict[str, Any]:
        """Expand one PR-context candidate symbol through Ripwire."""

        selected = PrImpactRequest(
            request.repo_root,
            request.artifact_root,
            symbol_path,
            symbol_name,
            limit=request.limit,
            offset=request.offset,
        )
        result = run_symbol_impact_once(session, selected, impact_builder=impact_builder)
        return {
            "status": result.status,
            "candidates": [candidate.__dict__ for candidate in result.candidates],
            "gaps": [gap.__dict__ for gap in result.gaps],
            "diagnostics": list(result.diagnostics),
            "metrics": dict(result.metrics),
            "evidence_id": f"symbol-impact-{session.impact_calls:03d}" if result.raw_xml else None,
        }

    return symbol_impact


def run_coordinator(
    seed: PreAgenticSeed,
    impact_request: PrImpactRequest,
    settings: BedrockSettings,
    *,
    agent_factory: Callable[..., Any] | None = None,
    impact_builder: Callable[[PrImpactRequest], Any] = build_symbol_impact,
) -> PRAnalysisReportV1:
    """Run one bounded coordinator invocation and validate its report."""

    if seed.status != "ok" or not seed.allowed_symbols:
        raise ValueError("coordinator requires an ok seed with candidate symbols")
    session = EvidenceSession(seed)
    if seed.context.raw_xml:
        session.register(EvidenceRecord(
            evidence_id="pr-context-001",
            kind="pr_context_xml",
            status=seed.context.status,
            sha256=sha256(seed.context.raw_xml.encode("utf-8")).hexdigest(),
        ))
    tool = make_symbol_impact_tool(session, impact_request, impact_builder=impact_builder)
    factory = agent_factory or build_bedrock_agent
    agent = factory(settings, tools=[tool])
    prompt = "Analyze this PR context and use symbol_impact at most once. Return only the requested structured report.\n" + json.dumps(seed.context.as_dict(), default=str)
    result = agent(prompt)
    structured = getattr(result, "structured_output", result)
    draft = structured if isinstance(structured, AgentAnalysisDraftV1) else AgentAnalysisDraftV1.model_validate(structured)
    for row in draft.blast_radius:
        session.require_evidence(row.evidence_ids)
    for area in draft.test_areas:
        session.require_evidence(area.evidence_ids)
    records = [
        Evidence(
            evidence_id=item.evidence_id,
            kind=item.kind,  # type: ignore[arg-type]
            relative_path=f"evidence/{item.evidence_id}.xml",
            sha256=item.sha256 or "0" * 64,
        )
        for item in session._records.values()
        if item.sha256
    ]
    identity = seed.context.identity
    report = PRAnalysisReportV1(
        schema="ia-repomap.pr-analysis/v1",
        status="ok",
        phase="analysis",
        request={"repository": identity.get("repository_id", str(impact_request.repo_root)), "base": identity.get("base_revision", ""), "analysis_schema": "ia-repomap.pr-analysis/v1"},
        identity={"repository": identity.get("repository_id", str(impact_request.repo_root)), "head": identity["head"], "base": identity.get("base_revision", identity["head"]), "merge_base": identity.get("merge_base", identity["head"]), "configuration_digest": identity.get("configuration_digest", "0" * 64), "engine_identity": identity.get("engine", {}).get("id", "unknown")},
        summary=draft.summary,
        changed_files=[
            {
                "path": changed.path,
                "change": changed.change,
                "symbols": [
                    {
                        "path": symbol.path,
                        "name": symbol.name,
                        "line": symbol.line,
                        "kind": symbol.kind,
                        "confidence": symbol.confidence,
                    }
                    for symbol in changed.symbols
                ],
                "evidence_ids": ["pr-context-001"],
            }
            for changed in seed.context.changed_files
        ],
        blast_radius=draft.blast_radius,
        test_areas=draft.test_areas,
        gaps=[gap.__dict__ for gap in seed.context.gaps],
        evidence=records,
        diagnostics=list(seed.context.diagnostics),
        remediation=[],
        metrics={"impact_calls": session.impact_calls},
        agent={"model_id": settings.model_id, "region": settings.region, "prompt_version": "pr-analysis-prompt-v1", "tool_contract_version": "ia-repomap.agent-tools/v1", "coordinator_version": "pr-analysis-implementation-v1"},
    )
    return report


@dataclass(frozen=True)
class BedrockSettings:
    region: str
    model_id: str
    profile: str | None = None


def load_bedrock_settings(env_file: str = ".env.local") -> BedrockSettings:
    """Load non-secret Bedrock settings; credentials stay with boto3."""

    values = dotenv_values(env_file)
    region = values.get("AWS_REGION") or values.get("AWS_DEFAULT_REGION")
    model_id = values.get("BEDROCK_MODEL_ID")
    if not region or not model_id:
        raise ValueError(".env.local must define AWS_REGION and BEDROCK_MODEL_ID")
    profile = values.get("AWS_PROFILE") or None
    return BedrockSettings(region=region, model_id=model_id, profile=profile)


def build_bedrock_agent(settings: BedrockSettings, *, tools: list[Any] | None = None) -> Any:
    """Construct the single MVP Strands agent; this does not invoke Bedrock."""

    try:
        from strands import Agent
        from strands.models import BedrockModel
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError("strands-agents is required for the Bedrock coordinator") from exc
    model = BedrockModel(model_id=settings.model_id, region_name=settings.region)
    return Agent(
        model=model,
        tools=tools or [],
        structured_output_model=AgentAnalysisDraftV1,
        system_prompt="Produce only an evidence-bound lower-bound PR analysis report.",
    )


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


__all__ = [
    "AgentAnalysisDraftV1",
    "BedrockSettings",
    "EvidenceRecord",
    "EvidenceSession",
    "PRAnalysisReportV1",
    "PreAgenticSeed",
    "build_bedrock_agent",
    "load_bedrock_settings",
    "make_symbol_impact_tool",
    "prepare_pre_agentic_seed",
    "run_coordinator",
    "run_symbol_impact_once",
]
