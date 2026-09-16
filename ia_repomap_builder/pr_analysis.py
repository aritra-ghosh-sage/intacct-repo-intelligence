"""Validated v1 PR-analysis report contract and local coordinator."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .config import PrContextRequest, PrContextResult, PrImpactRequest
from .impact import build_symbol_impact
from .pr_context import build_pr_context
from .identity import git_revision, is_dirty

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


def _unique_evidence_ids(value: list[str]) -> list[str]:
    if len(value) != len(set(value)):
        raise ValueError("evidence_ids must be unique")
    return value


class Request(StrictModel):
    repository: str | None = Field(..., min_length=1)
    base: str | None = Field(..., min_length=1)
    analysis_schema: Literal["ia-repomap.pr-analysis/v1"]


class Identity(StrictModel):
    repository: str | None = Field(..., min_length=1)
    head: str | None = Field(..., pattern=r"^[0-9a-f]{40}$")
    base: str | None = Field(..., pattern=r"^[0-9a-f]{40}$")
    merge_base: str | None = Field(..., pattern=r"^[0-9a-f]{40}$")
    configuration_digest: str | None = Field(..., pattern=r"^[0-9a-f]{64}$")
    engine_identity: str | None = Field(..., min_length=1)


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

    @field_validator("evidence_ids")
    @classmethod
    def unique_evidence_ids(cls, value: list[str]) -> list[str]:
        return _unique_evidence_ids(value)


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

    @field_validator("evidence_ids")
    @classmethod
    def unique_evidence_ids(cls, value: list[str]) -> list[str]:
        return _unique_evidence_ids(value)

    @model_validator(mode="after")
    def validate_graph_distance(self) -> "BlastRadiusRow":
        if self.relationship == "direct_caller" and self.graph_distance != 1:
            raise ValueError("direct_caller rows require graph_distance=1")
        if self.relationship != "direct_caller" and self.graph_distance is not None:
            raise ValueError("non-direct relationships require graph_distance=null")
        return self


class TestArea(StrictModel):
    area: str = Field(min_length=1)
    paths: list[str] = Field(max_length=100)
    reason: str = Field(min_length=1)
    confidence: Confidence
    evidence_ids: list[str] = Field(min_length=1, max_length=20)
    execution_status: Literal["not_run"]

    @field_validator("evidence_ids")
    @classmethod
    def unique_evidence_ids(cls, value: list[str]) -> list[str]:
        return _unique_evidence_ids(value)


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
    invoked: bool
    model_id: str | None = Field(..., min_length=1)
    region: str | None = Field(..., min_length=1)
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

    @model_validator(mode="after")
    def validate_outcome_provenance(self) -> "PRAnalysisReportV1":
        if self.status == "ok":
            request_complete = self.request.repository is not None and self.request.base is not None
            identity_complete = all(
                value is not None
                for value in (
                    self.identity.repository,
                    self.identity.head,
                    self.identity.base,
                    self.identity.merge_base,
                    self.identity.configuration_digest,
                    self.identity.engine_identity,
                )
            )
            if not request_complete or not identity_complete:
                raise ValueError("ok reports require complete request and identity provenance")
        if self.agent.invoked and (self.agent.model_id is None or self.agent.region is None):
            raise ValueError("invoked agents require model_id and region provenance")
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence identifiers must be unique")
        registered = set(evidence_ids)
        references = [
            reference
            for changed in self.changed_files
            for reference in changed.evidence_ids
        ] + [
            reference
            for row in self.blast_radius
            for reference in row.evidence_ids
        ] + [
            reference
            for area in self.test_areas
            for reference in area.evidence_ids
        ]
        missing = sorted(set(references) - registered)
        if missing:
            raise ValueError(f"report references unregistered evidence: {', '.join(missing)}")
        return self


class PRAnalysisRequestV1(StrictModel):
    """Versioned, host-supplied input for one local coordinator run."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_: Literal["ia-repomap.pr-analysis-request/v1"] = Field(alias="schema")
    repo_root: Path
    base_ref: str = Field(min_length=1)
    artifact_root: Path
    output_dir: Path

    @field_validator("repo_root", "artifact_root", "output_dir")
    @classmethod
    def absolute_path(cls, value: Path) -> Path:
        path = Path(value)
        if not path.is_absolute():
            raise ValueError("coordinator paths must be absolute")
        return path

    @field_validator("base_ref")
    @classmethod
    def non_blank_base_ref(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("base_ref must not be blank")
        return value

    @model_validator(mode="after")
    def validate_paths(self) -> "PRAnalysisRequestV1":
        repo = self.repo_root.resolve()
        artifact = self.artifact_root.resolve()
        output = self.output_dir.resolve()
        if artifact == repo or repo in artifact.parents:
            raise ValueError("artifact_root must be outside repo_root")
        if output == repo or repo in output.parents:
            raise ValueError("output_dir must be outside repo_root")
        if self.output_dir.is_symlink():
            raise ValueError("output_dir must not be a symlink")
        if output.exists() and not output.is_dir():
            raise ValueError("output_dir must be a directory")
        if output.exists() and output.is_dir() and any(output.iterdir()):
            raise ValueError("output_dir must be new or empty")
        return self


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
    content: bytes | None = None


class EvidenceSession:
    """Invocation-local evidence registry and candidate-target guard."""

    def __init__(self, seed: PreAgenticSeed) -> None:
        self._allowed_symbols = set(seed.allowed_symbols)
        self._allowed_paths = {path for path, _ in seed.allowed_symbols}
        for changed in seed.context.changed_files:
            self._allowed_paths.add(changed.path)
            if changed.old_path:
                self._allowed_paths.add(changed.old_path)
            for impacted in changed.impact_files:
                self._allowed_paths.add(impacted.path)
            for affected_test in changed.affected_tests:
                self._allowed_paths.add(affected_test.path)
            for symbol in changed.symbols:
                self._allowed_symbols.add((symbol.path, symbol.name))
                self._allowed_paths.add(symbol.path)
                for caller in symbol.callers:
                    self._allowed_symbols.add((caller.path, caller.name))
                    self._allowed_paths.add(caller.path)
        self._records: dict[str, EvidenceRecord] = {}
        self._impact_calls = 0
        self._inspection_calls = 0
        self._tool_gaps: list[dict[str, Any]] = []
        self._tool_diagnostics: list[str] = []
        self._inspection_paths = frozenset(path for path, _ in seed.allowed_symbols)
        self._inspection_terms = frozenset(
            term for path, name in seed.allowed_symbols for term in (path, name)
        )

    def register(self, record: EvidenceRecord) -> None:
        if not record.evidence_id or record.evidence_id in self._records:
            raise ValueError("evidence_id must be non-empty and unique within the session")
        self._records[record.evidence_id] = record

    @property
    def tool_gaps(self) -> tuple[dict[str, Any], ...]:
        return tuple(dict(gap) for gap in self._tool_gaps)

    @property
    def tool_diagnostics(self) -> tuple[str, ...]:
        return tuple(self._tool_diagnostics)

    def _record_gap(self, value: Any) -> None:
        if isinstance(value, Mapping):
            kind = value.get("kind")
            detail = value.get("detail")
            path = value.get("path")
            count = value.get("count")
        else:
            kind = getattr(value, "kind", None)
            detail = getattr(value, "detail", None)
            path = getattr(value, "path", None)
            count = getattr(value, "count", None)
        if not isinstance(kind, str) or not kind or not isinstance(detail, str) or not detail:
            return
        if isinstance(path, str) and path:
            detail = f"{detail} (path: {path})"
        gap: dict[str, Any] = {"kind": kind, "detail": detail}
        if isinstance(count, int) and count >= 0:
            gap["count"] = count
        self._tool_gaps.append(gap)

    def record_tool_result(self, result: Any) -> None:
        """Retain host-owned gaps and diagnostics returned by a bounded tool."""

        if isinstance(result, Mapping):
            gaps = result.get("gaps", ())
            diagnostics = result.get("diagnostics", ())
        else:
            gaps = getattr(result, "gaps", ())
            diagnostics = getattr(result, "diagnostics", ())
        for gap in gaps or ():
            self._record_gap(gap)
        for diagnostic in diagnostics or ():
            if isinstance(diagnostic, str) and diagnostic:
                self._tool_diagnostics.append(diagnostic)

    def record_tool_failure(self, kind: str, diagnostic: str) -> None:
        self._record_gap({"kind": kind, "detail": diagnostic})
        if diagnostic:
            self._tool_diagnostics.append(diagnostic)

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
        if self._impact_calls >= 5:
            raise ValueError("analysis permits at most five symbol-impact calls")
        self._impact_calls += 1

    @property
    def inspection_calls(self) -> int:
        return self._inspection_calls

    def consume_inspection_call(self) -> None:
        if self._inspection_calls >= 2:
            raise ValueError("analysis permits at most two inspection calls")
        self._inspection_calls += 1

    @property
    def authorized_inspection_paths(self) -> tuple[str, ...]:
        return tuple(sorted(self._inspection_paths))

    @property
    def allowed_paths(self) -> tuple[str, ...]:
        return tuple(sorted(self._allowed_paths))

    @property
    def authorized_inspection_terms(self) -> tuple[str, ...]:
        return tuple(sorted(self._inspection_terms))

    def register_inspection_evidence(self, evidence_id: str, content: bytes) -> None:
        self.register(EvidenceRecord(
            evidence_id=evidence_id,
            kind="inspection",
            status="ok",
            sha256=sha256(content).hexdigest(),
            content=content,
        ))

    def authorize_inspection_keys(self, paths: Any) -> None:
        """Extend inspection discovery to paths and names returned by impact."""
        for path, name in paths:
            self._allowed_symbols.add((path, name))
            self._allowed_paths.add(path)
            self._inspection_paths = self._inspection_paths | {path}
            self._inspection_terms = self._inspection_terms | {path, name}

    def authorize_inspection_terms(self, terms: Any) -> None:
        """Allow literals surfaced by a prior bounded inspection result."""
        self._inspection_terms = self._inspection_terms | {
            term for term in terms if isinstance(term, str) and term
        }

    def authorize_inspection_matches(self, matches: Any) -> None:
        """Allow paths returned by a successful bounded inspection."""
        for match in matches:
            path = match.get("path") if isinstance(match, dict) else None
            if isinstance(path, str) and path:
                self._allowed_paths.add(path)
                self._inspection_paths = self._inspection_paths | {path}

    def validate_model_paths(self, draft: AgentAnalysisDraftV1) -> None:
        """Reject structured rows that name paths or symbols absent from evidence."""
        unknown: set[str] = set()
        unknown_symbols: set[tuple[str, str]] = set()
        for row in draft.blast_radius:
            for path in (row.source_path, row.target_path):
                if path not in self._allowed_paths:
                    unknown.add(path)
            for symbol in ((row.source_path, row.source_symbol), (row.target_path, row.target_symbol)):
                if symbol not in self._allowed_symbols:
                    unknown_symbols.add(symbol)
        for area in draft.test_areas:
            unknown.update(path for path in area.paths if path not in self._allowed_paths)
        if unknown:
            raise ValueError(
                "model referenced paths outside host evidence: "
                + ", ".join(sorted(unknown))
            )
        if unknown_symbols:
            raise ValueError(
                "model referenced symbols outside host evidence: "
                + ", ".join(f"{path}:{name}" for path, name in sorted(unknown_symbols))
            )


def _require_complete_context_identity(identity: Mapping[str, Any]) -> None:
    required = (
        "repository_id",
        "head",
        "base_revision",
        "merge_base",
        "configuration_digest",
    )
    missing = [
        name for name in required
        if not isinstance(identity.get(name), str) or not identity[name]
    ]
    engine = identity.get("engine")
    if not isinstance(engine, Mapping) or not isinstance(engine.get("id"), str) or not engine["id"]:
        missing.append("engine.id")
    if identity.get("dirty") is not False:
        missing.append("dirty=false")
    if missing:
        raise ValueError(
            "successful PR context has incomplete identity provenance: "
            + ", ".join(missing)
        )


def _with_tool_observations(
    report: PRAnalysisReportV1,
    session: EvidenceSession,
) -> PRAnalysisReportV1:
    """Add bounded-tool gaps and diagnostics to a host-owned report."""

    payload = report.model_dump(mode="python", by_alias=True)
    payload["gaps"] = [*payload["gaps"], *session.tool_gaps]
    payload["diagnostics"] = [*payload["diagnostics"], *session.tool_diagnostics]
    return PRAnalysisReportV1.model_validate(payload)


def _payload_kind_and_path(evidence_id: str) -> tuple[str, str]:
    if evidence_id == "pr-context-001":
        return "pr_context_xml", "evidence/pr-context.xml"
    if evidence_id.startswith("inspection-"):
        return "inspection", f"evidence/{evidence_id}.json"
    return "symbol_impact_xml", f"evidence/{evidence_id}.xml"


def _with_payload_evidence(
    report: PRAnalysisReportV1,
    payloads: list[tuple[str, bytes]],
) -> PRAnalysisReportV1:
    """Attach any evidence collected before an agent failure to the report."""

    payload = report.model_dump(mode="python", by_alias=True)
    known = {item["evidence_id"] for item in payload["evidence"]}
    for evidence_id, content in payloads:
        if evidence_id in known:
            continue
        kind, relative_path = _payload_kind_and_path(evidence_id)
        payload["evidence"].append({
            "evidence_id": evidence_id,
            "kind": kind,
            "relative_path": relative_path,
            "sha256": sha256(content).hexdigest(),
        })
        known.add(evidence_id)
    return PRAnalysisReportV1.model_validate(payload)


def _merge_payloads(
    first: list[tuple[str, bytes]],
    second: list[tuple[str, bytes]],
) -> list[tuple[str, bytes]]:
    merged: list[tuple[str, bytes]] = []
    seen: set[str] = set()
    for evidence_id, content in [*first, *second]:
        if evidence_id in seen:
            continue
        merged.append((evidence_id, content))
        seen.add(evidence_id)
    return merged


def _affected_test_areas(context: PrContextResult) -> list[TestArea]:
    """Convert host-normalized Ripwire test evidence into report rows."""

    areas: list[TestArea] = []
    for changed in sorted(context.changed_files, key=lambda item: item.path):
        if not changed.affected_tests:
            continue
        tests = sorted(changed.affected_tests, key=lambda item: item.path)
        runners = sorted({item.runner for item in tests if item.runner})
        reason = f"Ripwire identified these tests in the lower-bound impact of {changed.path}."
        if runners:
            reason += " Disclosed runners: " + "; ".join(runners)
        areas.append(TestArea(
            area=f"Affected tests for {changed.path}",
            paths=[item.path for item in tests],
            reason=reason,
            confidence="candidate",
            evidence_ids=["pr-context-001"],
            execution_status="not_run",
        ))
    return areas


def _merge_test_areas(*collections: list[TestArea]) -> list[TestArea]:
    unique: dict[tuple[Any, ...], TestArea] = {}
    for area in (item for collection in collections for item in collection):
        key = (
            area.area,
            tuple(area.paths),
            area.reason,
            area.confidence,
            tuple(area.evidence_ids),
            area.execution_status,
        )
        unique[key] = area
    return sorted(
        unique.values(),
        key=lambda item: (item.area, tuple(item.paths), item.reason),
    )


def run_symbol_impact_once(
    session: EvidenceSession,
    request: PrImpactRequest,
    *,
    impact_builder: Callable[[PrImpactRequest], Any],
    evidence_payloads: list[tuple[str, bytes]] | None = None,
) -> Any:
    """Run the single MVP impact expansion and register its evidence."""

    session.consume_impact_call()
    # Rejected attempts consume a slot too; otherwise a model could retry
    # unauthorized targets indefinitely without reaching the host-enforced
    # call ceiling.
    session.authorize_impact(request.symbol_path, request.symbol_name)
    try:
        result = impact_builder(request)
    except Exception as exc:
        session.record_tool_failure("symbol_impact_unavailable", str(exc))
        raise
    session.record_tool_result(result)
    if hasattr(result, "candidates"):
        session.authorize_inspection_keys(
            (candidate.path, candidate.name) for candidate in result.candidates
        )
    if result.raw_xml:
        evidence_id = f"symbol-impact-{session.impact_calls:03d}"
        content = result.raw_xml.encode("utf-8")
        session.register(EvidenceRecord(
            evidence_id=evidence_id,
            kind="symbol_impact_xml",
            status=result.status,
            sha256=sha256(content).hexdigest(),
            content=content,
        ))
        if evidence_payloads is not None:
            evidence_payloads.append((evidence_id, content))
    return result


def make_symbol_impact_tool(
    session: EvidenceSession,
    request: PrImpactRequest,
    *,
    impact_builder: Callable[[PrImpactRequest], Any] = build_symbol_impact,
    evidence_payloads: list[tuple[str, bytes]] | None = None,
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
        result = run_symbol_impact_once(
            session, selected, impact_builder=impact_builder,
            evidence_payloads=evidence_payloads,
        )
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
    impact_request: PrImpactRequest | None,
    settings: BedrockSettings,
    *,
    agent_factory: Callable[..., Any] | None = None,
    impact_builder: Callable[[PrImpactRequest], Any] = build_symbol_impact,
    allow_source_inspection: bool = False,
    evidence_payloads: list[tuple[str, bytes]] | None = None,
    session_sink: list[EvidenceSession] | None = None,
) -> PRAnalysisReportV1:
    """Run one bounded coordinator invocation and validate its report."""

    if seed.status != "ok":
        raise ValueError("coordinator requires an ok seed")
    degraded = not seed.allowed_symbols
    if degraded and not seed.context.changed_files:
        raise ValueError("degraded coordinator requires changed files")
    if not isinstance(seed.context.identity, Mapping):
        raise ValueError("successful PR context identity must be a mapping")
    _require_complete_context_identity(seed.context.identity)
    session = EvidenceSession(seed)
    if session_sink is not None:
        session_sink.append(session)
    if seed.context.raw_xml or seed.context.changed_files:
        context_bytes = seed.context.raw_xml.encode("utf-8")
        session.register(EvidenceRecord(
            evidence_id="pr-context-001",
            kind="pr_context_xml",
            status=seed.context.status,
            sha256=sha256(context_bytes).hexdigest(),
            content=context_bytes,
        ))
        if evidence_payloads is not None:
            evidence_payloads.append(("pr-context-001", context_bytes))
    tools = []
    if impact_request is not None:
        tools.append(make_symbol_impact_tool(
            session,
            impact_request,
            impact_builder=impact_builder,
            evidence_payloads=evidence_payloads,
        ))
    if allow_source_inspection and impact_request is not None:
        from .pr_analysis_inspection import make_repository_inspection_tool

        tools.append(make_repository_inspection_tool(
            session,
            repo_root=impact_request.repo_root,
            evidence_payloads=evidence_payloads,
        ))
    factory = agent_factory or build_bedrock_agent
    agent = factory(settings, tools=tools)
    normalized_context = seed.context.as_dict()
    normalized_context.pop("raw_xml", None)
    identity = normalized_context.get("identity")
    if isinstance(identity, dict):
        normalized_context["identity"] = {
            key: identity[key]
            for key in (
                "repository_id",
                "head",
                "dirty",
                "scope",
                "configuration_digest",
                "base_ref",
                "base_revision",
                "merge_base",
                "engine",
            )
            if key in identity
        }
    prompt_context = {
        **normalized_context,
        "evidence": [
            {
                "evidence_id": "pr-context-001",
                "sha256": sha256(seed.context.raw_xml.encode("utf-8")).hexdigest(),
            }
        ],
        "allowed_symbols": list(seed.allowed_symbols),
        "analysis_mode": "degraded_file_diff" if degraded else "symbol_seeded",
        "limits": {"impact_calls": 0 if degraded else 5, "inspection_calls": 2, "impact_rows": 20},
    }
    prompt = (
        "Analyze this PR context as a lower-bound, evidence-bound report. "
        "Treat Git changed files and exact revision identity as confirmed; "
        "treat Ripwire symbols and relationships as candidate evidence. "
        "Use only supplied candidate path/name pairs for impact expansion, "
        "cite registered evidence IDs, and report ambiguity, truncation, "
        "unresolved, out-of-scope, and unavailable gaps explicitly. "
        "Use at most five impact calls and two inspection calls. "
        "Inspect source before consequential claims, distinguish candidate "
        "test areas from executed coverage, keep test execution_status as "
        "not_run, and do not claim exhaustive impact. "
        + (
            "This is degraded file/diff analysis: do not produce symbol-level "
            "blast-radius rows; provide at least one evidence-backed test or "
            "review action tied to a changed file. "
            if degraded
            else ""
        )
        + "Return only the requested structured report; do not include hidden "
        "reasoning.\n"
        + json.dumps(prompt_context, default=str, sort_keys=True)
    )
    result = agent(prompt)
    structured = getattr(result, "structured_output", result)
    draft = structured if isinstance(structured, AgentAnalysisDraftV1) else AgentAnalysisDraftV1.model_validate(structured)
    if degraded and draft.blast_radius:
        raise ValueError("degraded analysis cannot produce symbol-level blast-radius rows")
    if degraded and not draft.test_areas:
        draft = AgentAnalysisDraftV1(
            summary=draft.summary,
            blast_radius=[],
            test_areas=[TestArea(
                area="Review changed file behavior",
                paths=[changed.path for changed in seed.context.changed_files],
                reason="Review the changed hunks and validate the affected behavior; symbol-level impact remains unresolved.",
                confidence="unresolved",
                evidence_ids=["pr-context-001"],
                execution_status="not_run",
            )],
        )
    test_areas = _merge_test_areas(_affected_test_areas(seed.context), draft.test_areas)
    session.validate_model_paths(draft)
    for row in draft.blast_radius:
        session.require_evidence(row.evidence_ids)
    for area in draft.test_areas:
        session.require_evidence(area.evidence_ids)
    records = [
        Evidence(
            evidence_id=item.evidence_id,
            kind=item.kind,  # type: ignore[arg-type]
            relative_path=(
                "evidence/pr-context.xml"
                if item.evidence_id == "pr-context-001"
                else (f"evidence/{item.evidence_id}.json" if item.kind == "inspection" else f"evidence/{item.evidence_id}.xml")
            ),
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
        request={"repository": identity["repository_id"], "base": identity["base_revision"], "analysis_schema": "ia-repomap.pr-analysis/v1"},
        identity={"repository": identity["repository_id"], "head": identity["head"], "base": identity["base_revision"], "merge_base": identity["merge_base"], "configuration_digest": identity["configuration_digest"], "engine_identity": identity["engine"]["id"]},
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
        test_areas=test_areas,
        gaps=[
            *(
                {
                    "kind": gap.kind,
                    "detail": gap.detail,
                    **({"count": gap.count} if gap.count is not None else {}),
                }
                for gap in seed.context.gaps
            ),
            *session.tool_gaps,
        ],
        evidence=records,
        diagnostics=[*seed.context.diagnostics, *session.tool_diagnostics],
        remediation=[],
        metrics={"impact_calls": session.impact_calls, "inspection_calls": session.inspection_calls},
        agent={"invoked": True, "model_id": settings.model_id, "region": settings.region, "prompt_version": "pr-analysis-prompt-v1", "tool_contract_version": "ia-repomap.agent-tools/v1", "coordinator_version": "pr-analysis-implementation-v1"},
    )
    return report


def _report_from_context(
    request: PRAnalysisRequestV1 | None,
    context: PrContextResult,
    *,
    status: Status,
    phase: Phase,
    diagnostic: str | None = None,
) -> PRAnalysisReportV1:
    """Build the schema-valid host-owned report used before model execution."""

    identity_data = context.identity
    identity = {
        "repository": identity_data.get("repository_id"),
        "head": identity_data.get("head"),
        "base": identity_data.get("base_revision"),
        "merge_base": identity_data.get("merge_base"),
        "configuration_digest": identity_data.get("configuration_digest"),
        "engine_identity": (identity_data.get("engine") or {}).get("id")
        if isinstance(identity_data.get("engine"), dict)
        else identity_data.get("engine_identity"),
    }
    request_data = {
        "repository": identity["repository"] or (str(request.repo_root) if request else None),
        "base": identity["base"] or (request.base_ref if request else None),
        "analysis_schema": "ia-repomap.pr-analysis/v1",
    }
    complete_identity = all(value is not None for value in identity.values())
    effective_status = status
    diagnostics = list(context.diagnostics)
    if diagnostic:
        diagnostics.append(diagnostic)
    if status == "ok" and not complete_identity:
        effective_status = "error"
        phase = "pr_context"
        diagnostics.append("successful PR context did not include complete identity provenance")

    evidence: list[Evidence] = []
    if context.raw_xml or context.changed_files:
        context_bytes = context.raw_xml.encode("utf-8")
        evidence.append(Evidence(
            evidence_id="pr-context-001",
            kind="pr_context_xml",
            relative_path="evidence/pr-context.xml",
            sha256=sha256(context_bytes).hexdigest(),
        ))
    changed_files = [
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
        for changed in context.changed_files
    ]
    gaps = [gap.__dict__ for gap in context.gaps]
    if effective_status == "ok" and not any(file["symbols"] for file in changed_files):
        gaps.append({"kind": "no_candidate_symbols", "detail": "No candidate symbols were returned by PR context"})
    if effective_status == "unavailable":
        remediation = [
            "Verify the exact clean checkout and prepared external index before retrying"
        ]
    elif phase == "request_validation":
        remediation = ["Correct the versioned request schema and external paths before retrying"]
    elif effective_status == "error":
        remediation = ["Review the diagnostics and retry only after the reported failure is resolved"]
    else:
        remediation = []
    test_areas = _affected_test_areas(context) if effective_status == "ok" else []
    return PRAnalysisReportV1(
        schema="ia-repomap.pr-analysis/v1",
        status=effective_status,
        phase=phase,
        request=request_data,
        identity=identity,
        summary={
            "purpose": "Evidence-bound PR context analysis",
            "behavioral_change": "No model analysis was invoked",
            "confidence": "unavailable" if effective_status != "ok" else "candidate",
        },
        changed_files=changed_files,
        blast_radius=[],
        test_areas=test_areas,
        gaps=gaps,
        evidence=evidence,
        diagnostics=diagnostics,
        remediation=remediation,
        metrics={**context.metrics, "agent_invoked": False},
        agent={
            "invoked": False,
            "model_id": None,
            "region": None,
            "prompt_version": "pr-analysis-prompt-v1",
            "tool_contract_version": "ia-repomap.agent-tools/v1",
            "coordinator_version": "pr-analysis-implementation-v1",
        },
    )


def run_pr_analysis(
    request: PRAnalysisRequestV1 | Mapping[str, Any],
    settings: BedrockSettings | None = None,
    *,
    allow_source_inspection: bool = False,
    context_builder: Callable[[PrContextRequest], PrContextResult] = build_pr_context,
    agent_factory: Callable[..., Any] | None = None,
    impact_builder: Callable[[PrImpactRequest], Any] = build_symbol_impact,
    revision_checker: Callable[[Path], str | None] = git_revision,
    dirty_checker: Callable[[Path], bool | None] = is_dirty,
) -> PRAnalysisReportV1:
    """Run the host-owned PR gate and, when seeded, one coordinator agent."""

    try:
        parsed = request if isinstance(request, PRAnalysisRequestV1) else PRAnalysisRequestV1.model_validate(request)
    except (ValidationError, TypeError, ValueError) as exc:
        context = PrContextResult("error", diagnostics=[f"invalid coordinator request: {exc}"])
        return _report_from_context(None, context, status="error", phase="request_validation")

    context_request = PrContextRequest(
        repo_root=parsed.repo_root,
        artifact_root=parsed.artifact_root,
        base_ref=parsed.base_ref,
        limit=20,
        offset=0,
        history_commits=500,
    )
    try:
        context = context_builder(context_request)
        if not isinstance(context, PrContextResult):
            raise TypeError("PR context builder returned an invalid result")
    except Exception as exc:  # adapter failures are an explicit report outcome
        context = PrContextResult("error", diagnostics=[f"PR context failed: {exc}"])

    def context_payloads(value: PrContextResult) -> list[tuple[str, bytes]]:
        if not (value.raw_xml or value.changed_files):
            return []
        return [("pr-context-001", value.raw_xml.encode("utf-8"))]

    if context.status == "unavailable":
        report = _report_from_context(parsed, context, status="unavailable", phase="readiness")
        return _persist_report(parsed, report, context_payloads(context))
    if context.status != "ok":
        report = _report_from_context(parsed, context, status="error", phase="pr_context")
        return _persist_report(parsed, report, context_payloads(context))

    allowed = tuple(
        (symbol.path, symbol.name)
        for changed in context.changed_files
        for symbol in changed.symbols
    )
    if not allowed:
        if context.changed_files and settings is not None:
            seed = PreAgenticSeed("ok", "analysis", context, ())
            payloads: list[tuple[str, bytes]] = []
            sessions: list[EvidenceSession] = []
            agent_created = False

            def tracked_agent_factory(*args: Any, **kwargs: Any) -> Any:
                nonlocal agent_created
                factory = agent_factory or build_bedrock_agent
                agent = factory(*args, **kwargs)
                agent_created = True
                return agent

            try:
                report = run_coordinator(
                    seed,
                    None,
                    settings,
                    agent_factory=tracked_agent_factory,
                    allow_source_inspection=False,
                    evidence_payloads=payloads,
                    session_sink=sessions,
                )
            except Exception as exc:
                report = _report_from_context(
                    parsed,
                    context,
                    status="error",
                    phase="analysis",
                    diagnostic=f"degraded analysis failed: {exc}",
                )
                if agent_created:
                    report = _with_agent_provenance(report, settings)
                payloads = _merge_payloads(context_payloads(context), payloads)
                if sessions:
                    report = _with_tool_observations(report, sessions[0])
                report = _with_payload_evidence(report, payloads)
            try:
                final_head = revision_checker(parsed.repo_root)
                final_dirty = dirty_checker(parsed.repo_root)
            except Exception as exc:
                report = _analysis_error_report(
                    report,
                    f"repository state check failed: {exc}",
                    gap_kind="repository_state_check_failed",
                )
            else:
                if final_head != context.identity.get("head") or final_dirty is not False:
                    report = _analysis_error_report(report, "repository changed during analysis")
            return _persist_report(parsed, report, payloads)
        report = _report_from_context(parsed, context, status="ok", phase="pr_context")
        return _persist_report(parsed, report, context_payloads(context))
    if settings is None:
        report = _report_from_context(
            parsed,
            context,
            status="error",
            phase="analysis",
            diagnostic="Bedrock settings are required when candidate symbols are present",
        )
        return _persist_report(parsed, report, context_payloads(context))
    seed = PreAgenticSeed("ok", "analysis", context, tuple(dict.fromkeys(allowed)))
    first_path, first_name = seed.allowed_symbols[0]
    impact_request = PrImpactRequest(parsed.repo_root, parsed.artifact_root, first_path, first_name)
    payloads: list[tuple[str, bytes]] = []
    sessions: list[EvidenceSession] = []
    agent_created = False

    def tracked_agent_factory(*args: Any, **kwargs: Any) -> Any:
        nonlocal agent_created
        factory = agent_factory or build_bedrock_agent
        agent = factory(*args, **kwargs)
        agent_created = True
        return agent

    try:
        report = run_coordinator(
            seed,
            impact_request,
            settings,
            agent_factory=tracked_agent_factory,
            impact_builder=impact_builder,
            allow_source_inspection=allow_source_inspection,
            evidence_payloads=payloads,
            session_sink=sessions,
        )
    except Exception as exc:
        report = _report_from_context(
            parsed,
            context,
            status="error",
            phase="analysis",
            diagnostic=f"analysis failed: {exc}",
        )
        if agent_created:
            report = _with_agent_provenance(report, settings)
        payloads = _merge_payloads(context_payloads(context), payloads)
        if sessions:
            report = _with_tool_observations(report, sessions[0])
        report = _with_payload_evidence(report, payloads)

    # The repository guard applies after every attempted agent run, including
    # model, structured-output, and tool failures.  A failed identity check is
    # itself an analysis error and must never publish model conclusions.
    try:
        final_head = revision_checker(parsed.repo_root)
        final_dirty = dirty_checker(parsed.repo_root)
    except Exception as exc:
        report = _analysis_error_report(
            report,
            f"repository state check failed: {exc}",
            gap_kind="repository_state_check_failed",
        )
    else:
        if final_head != context.identity.get("head") or final_dirty is not False:
            report = _analysis_error_report(report, "repository changed during analysis")
    return _persist_report(parsed, report, payloads)


def _analysis_error_report(
    report: PRAnalysisReportV1,
    diagnostic: str,
    *,
    gap_kind: str = "repository_changed_during_analysis",
) -> PRAnalysisReportV1:
    payload = report.model_dump(mode="python", by_alias=True)
    payload.update(
        {
            "status": "error",
            "phase": "analysis",
            "summary": {
                "purpose": "Evidence-bound PR context analysis",
                "behavioral_change": "Model conclusions were discarded after repository validation failed",
                "confidence": "unavailable",
            },
            "blast_radius": [],
            "test_areas": [],
        }
    )
    payload["diagnostics"] = [*report.diagnostics, diagnostic]
    if not any(gap.get("kind") == gap_kind for gap in payload["gaps"]):
        payload["gaps"] = [
            *payload["gaps"],
            {"kind": gap_kind, "detail": diagnostic},
        ]
    return PRAnalysisReportV1.model_validate(payload)


def _with_agent_provenance(
    report: PRAnalysisReportV1,
    settings: BedrockSettings,
) -> PRAnalysisReportV1:
    """Mark a report after the host constructed the configured agent."""

    payload = report.model_dump(mode="python", by_alias=True)
    payload["agent"] = {
        **payload["agent"],
        "invoked": True,
        "model_id": settings.model_id,
        "region": settings.region,
    }
    return PRAnalysisReportV1.model_validate(payload)


def _persist_report(
    request: PRAnalysisRequestV1,
    report: PRAnalysisReportV1,
    payloads: list[tuple[str, bytes]],
) -> PRAnalysisReportV1:
    try:
        from .pr_analysis_output import EvidencePayload, write_pr_analysis_bundle

        write_pr_analysis_bundle(
            request.output_dir,
            report,
            [EvidencePayload(evidence_id, content) for evidence_id, content in payloads],
            repo_root=request.repo_root,
            artifact_root=request.artifact_root,
        )
        return report
    except Exception as exc:
        payload = report.model_dump(mode="python", by_alias=True)
        payload.update({"status": "error", "phase": "persistence"})
        payload["diagnostics"] = [*report.diagnostics, f"report persistence failed: {exc}"]
        return PRAnalysisReportV1.model_validate(payload)


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
    boto_session = None
    if settings.profile:
        import boto3

        boto_session = boto3.Session(profile_name=settings.profile, region_name=settings.region)
    model = BedrockModel(
        boto_session=boto_session,
        model_id=settings.model_id,
        region_name=settings.region,
        temperature=0,
        max_tokens=4096,
    )
    from strands.tools.executors import SequentialToolExecutor

    return Agent(
        model=model,
        tools=tools or [],
        structured_output_model=AgentAnalysisDraftV1,
        system_prompt="Produce only an evidence-bound lower-bound PR analysis report.",
        tool_executor=SequentialToolExecutor(),
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
    "PRAnalysisRequestV1",
    "PreAgenticSeed",
    "build_bedrock_agent",
    "load_bedrock_settings",
    "make_symbol_impact_tool",
    "prepare_pre_agentic_seed",
    "run_coordinator",
    "run_pr_analysis",
    "run_symbol_impact_once",
]
