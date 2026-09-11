"""Focused tests for the frozen PR-analysis report contract."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from ia_repomap_builder.config import (
    PrChangedFile,
    PrContextRequest,
    PrContextResult,
    PrImpactRequest,
    PrImpactResult,
    PrSymbolCandidate,
)
from ia_repomap_builder.pr_analysis import (
    BedrockSettings,
    EvidenceRecord,
    EvidenceSession,
    PRAnalysisReportV1,
    build_bedrock_agent,
    load_bedrock_settings,
    prepare_pre_agentic_seed,
    run_coordinator,
    run_symbol_impact_once,
)


class PRAnalysisReportTests(unittest.TestCase):
    def valid_payload(self) -> dict[str, object]:
        return {
            "schema": "ia-repomap.pr-analysis/v1",
            "status": "ok",
            "phase": "analysis",
            "request": {
                "repository": "intacct/ia-app",
                "base": "a" * 40,
                "analysis_schema": "ia-repomap.pr-analysis/v1",
            },
            "identity": {
                "repository": "intacct/ia-app",
                "head": "b" * 40,
                "base": "a" * 40,
                "merge_base": "c" * 40,
                "configuration_digest": "d" * 64,
                "engine_identity": "ripwire-test",
            },
            "summary": {
                "purpose": "Change validation behavior",
                "behavioral_change": "A bounded change was detected",
                "confidence": "candidate",
            },
            "changed_files": [],
            "blast_radius": [],
            "test_areas": [],
            "gaps": [{"kind": "impact_lower_bound", "detail": "Not exhaustive"}],
            "evidence": [],
            "diagnostics": [],
            "remediation": [],
            "metrics": {"impact_calls": 0, "truncated": False},
            "agent": {
                "model_id": "test-model",
                "region": "test-region",
                "prompt_version": "prompt-v1",
                "tool_contract_version": "tools-v1",
                "coordinator_version": "coordinator-v1",
            },
        }

    def test_valid_report_parses_and_serializes_external_schema_name(self) -> None:
        report = PRAnalysisReportV1.model_validate(self.valid_payload())
        self.assertEqual(report.schema_, "ia-repomap.pr-analysis/v1")
        self.assertIn('"schema":"ia-repomap.pr-analysis/v1"', report.model_dump_json(by_alias=True))

    def test_unknown_fields_are_rejected(self) -> None:
        payload = self.valid_payload()
        payload["unexpected"] = True
        with self.assertRaises(ValidationError):
            PRAnalysisReportV1.model_validate(payload)

    def test_invalid_enums_and_identity_are_rejected(self) -> None:
        payload = self.valid_payload()
        payload["status"] = "complete"
        with self.assertRaises(ValidationError):
            PRAnalysisReportV1.model_validate(payload)
        payload = self.valid_payload()
        payload["identity"]["head"] = "not-a-sha"  # type: ignore[index]
        with self.assertRaises(ValidationError):
            PRAnalysisReportV1.model_validate(payload)

    def test_test_area_must_be_not_run(self) -> None:
        payload = self.valid_payload()
        payload["test_areas"] = [{
            "area": "validation",
            "paths": ["app/tests/example.cls"],
            "reason": "related reference",
            "confidence": "candidate",
            "evidence_ids": ["inspect-001"],
            "execution_status": "passed",
        }]
        with self.assertRaises(ValidationError):
            PRAnalysisReportV1.model_validate(payload)

    def test_checked_in_schema_has_required_contract_surface(self) -> None:
        path = Path(__file__).parents[1] / "schemas" / "ia-repomap.pr-analysis-v1.schema.json"
        schema = json.loads(path.read_text(encoding="utf-8"))
        generated = PRAnalysisReportV1.model_json_schema()
        self.assertEqual(schema["$id"], "ia-repomap.pr-analysis/v1")
        self.assertEqual(set(schema["required"]), set(generated["required"]))
        self.assertEqual(set(schema["properties"]), set(generated["properties"]))
        self.assertEqual(schema["properties"]["status"]["enum"], ["ok", "unavailable", "error"])
        self.assertEqual(schema["properties"]["schema"]["const"], "ia-repomap.pr-analysis/v1")

    def context_request(self) -> PrContextRequest:
        return PrContextRequest(Path("/repo"), Path("/artifacts"), "origin/main")

    def test_pre_agentic_seed_calls_context_once_and_allows_only_candidates(self) -> None:
        calls: list[PrContextRequest] = []
        result = PrContextResult(status="ok", changed_files=[PrChangedFile(
            path="app/source/example/Example.cls",
            change="M",
            symbols=(PrSymbolCandidate(path="app/source/example/Example.cls", name="changed", line=1),),
        )])
        seed = prepare_pre_agentic_seed(self.context_request(), context_builder=lambda request: (calls.append(request), result)[1])
        self.assertEqual(seed.phase, "analysis")
        self.assertEqual(seed.allowed_symbols, (("app/source/example/Example.cls", "changed"),))
        self.assertEqual(len(calls), 1)

    def test_pre_agentic_seed_skips_agent_for_empty_symbols_or_failed_context(self) -> None:
        empty = prepare_pre_agentic_seed(self.context_request(), context_builder=lambda _: PrContextResult("ok"))
        self.assertEqual((empty.status, empty.phase, empty.allowed_symbols), ("ok", "pr_context", ()))
        unavailable = prepare_pre_agentic_seed(self.context_request(), context_builder=lambda _: PrContextResult("unavailable"))
        self.assertEqual((unavailable.status, unavailable.phase), ("unavailable", "pr_context"))

    def test_pre_agentic_seed_rejects_invalid_request_without_context_call(self) -> None:
        calls = 0
        def builder(_: PrContextRequest) -> PrContextResult:
            nonlocal calls
            calls += 1
            return PrContextResult("ok")
        request = PrContextRequest(Path("relative"), Path("/artifacts"), "origin/main")
        seed = prepare_pre_agentic_seed(request, context_builder=builder)
        self.assertEqual((seed.status, seed.phase), ("error", "request_validation"))
        self.assertEqual(calls, 0)

    def test_evidence_session_requires_current_unique_ids(self) -> None:
        seed = prepare_pre_agentic_seed(self.context_request(), context_builder=lambda _: PrContextResult("ok"))
        session = EvidenceSession(seed)
        session.register(EvidenceRecord("pr-context-001", "pr_context_xml", "ok"))
        session.require_evidence(["pr-context-001"])
        with self.assertRaises(ValueError):
            session.require_evidence(["impact-001"])
        with self.assertRaises(ValueError):
            session.register(EvidenceRecord("pr-context-001", "pr_context_xml", "ok"))

    def test_evidence_session_authorizes_only_seed_symbols(self) -> None:
        result = PrContextResult(status="ok", changed_files=[PrChangedFile(
            path="app/source/example/Example.cls", change="M",
            symbols=(PrSymbolCandidate(path="app/source/example/Example.cls", name="changed", line=1),),
        )])
        seed = prepare_pre_agentic_seed(self.context_request(), context_builder=lambda _: result)
        session = EvidenceSession(seed)
        session.authorize_impact("app/source/example/Example.cls", "changed")
        with self.assertRaises(ValueError):
            session.authorize_impact("app/source/example/Example.cls", "not_changed")

    def test_bedrock_settings_load_non_secret_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env.local"
            path.write_text("AWS_REGION=us-east-1\nAWS_PROFILE=dev\nBEDROCK_MODEL_ID=test-model\n", encoding="utf-8")
            settings = load_bedrock_settings(str(path))
        self.assertEqual(settings.region, "us-east-1")
        self.assertEqual(settings.model_id, "test-model")
        self.assertEqual(settings.profile, "dev")

    def test_bedrock_settings_require_region_and_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env.local"
            path.write_text("AWS_REGION=us-east-1\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_bedrock_settings(str(path))

    def test_bedrock_agent_construction_does_not_call_aws(self) -> None:
        agent = build_bedrock_agent(BedrockSettings(region="us-east-1", model_id="test-model"))
        self.assertIsNotNone(agent)

    def test_symbol_impact_adapter_authorizes_once_and_registers_xml(self) -> None:
        context = PrContextResult(status="ok", changed_files=[PrChangedFile(
            path="app/source/example/Example.cls", change="M",
            symbols=(PrSymbolCandidate(path="app/source/example/Example.cls", name="changed", line=1),),
        )])
        seed = prepare_pre_agentic_seed(self.context_request(), context_builder=lambda _: context)
        session = EvidenceSession(seed)
        request = PrImpactRequest(Path("/repo"), Path("/artifacts"), "app/source/example/Example.cls", "changed")
        result = run_symbol_impact_once(session, request, impact_builder=lambda _: PrContextResult("ok", raw_xml="<impact/>") )
        self.assertEqual(result.status, "ok")
        session.require_evidence(["symbol-impact-001"])
        with self.assertRaises(ValueError):
            run_symbol_impact_once(session, request, impact_builder=lambda _: result)

    def test_symbol_impact_adapter_rejects_unauthorized_symbol(self) -> None:
        seed = prepare_pre_agentic_seed(self.context_request(), context_builder=lambda _: PrContextResult("ok"))
        session = EvidenceSession(seed)
        request = PrImpactRequest(Path("/repo"), Path("/artifacts"), "app/source/example/Example.cls", "unknown")
        with self.assertRaises(ValueError):
            run_symbol_impact_once(session, request, impact_builder=lambda _: PrContextResult("ok"))

    def test_coordinator_fake_agent_runs_tool_and_validates_report(self) -> None:
        context = PrContextResult(
            status="ok",
            raw_xml="<pr-context/>",
            identity={
                "repository_id": "test-repository",
                "head": "b" * 40,
                "base_revision": "a" * 40,
                "merge_base": "a" * 40,
                "configuration_digest": "c" * 64,
                "engine": {"id": "test-engine"},
            },
            changed_files=[PrChangedFile(
                path="app/source/example/Example.cls", change="M",
                symbols=(PrSymbolCandidate(path="app/source/example/Example.cls", name="changed", line=1),),
            )],
        )
        seed = prepare_pre_agentic_seed(self.context_request(), context_builder=lambda _: context)
        impact = PrImpactResult(status="ok", raw_xml="<impact/>")
        request = PrImpactRequest(Path("/repo"), Path("/artifacts"), "app/source/example/Example.cls", "changed")

        class FakeAgent:
            def __init__(self, tool: object) -> None:
                self.tool = tool

            def __call__(self, _: str) -> dict[str, object]:
                self.tool(symbol_path=request.symbol_path, symbol_name=request.symbol_name)  # type: ignore[operator]
                return {"summary": self_payload["summary"], "blast_radius": [], "test_areas": []}

        self_payload = self.valid_payload()
        self_payload["changed_files"] = [{"path": request.symbol_path, "change": "M", "symbols": [{"path": request.symbol_path, "name": "changed", "line": 1, "confidence": "candidate"}], "evidence_ids": ["pr-context-001"]}]
        self_payload["blast_radius"] = []
        self_payload["agent"] = {"model_id": "fake", "region": "test", "prompt_version": "v1", "tool_contract_version": "v1", "coordinator_version": "v1"}
        report = run_coordinator(seed, request, BedrockSettings(region="test", model_id="fake"), agent_factory=lambda _, tools: FakeAgent(tools[0]), impact_builder=lambda _: impact)
        self.assertEqual(report.schema_, "ia-repomap.pr-analysis/v1")
        self.assertEqual(report.metrics["impact_calls"], 1)


if __name__ == "__main__":
    unittest.main()
