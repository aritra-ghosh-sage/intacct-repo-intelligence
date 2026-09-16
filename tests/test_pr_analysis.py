"""Focused tests for the frozen PR-analysis report contract."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

import jsonschema
from pydantic import ValidationError

from ia_repomap_builder.config import (
    PrAffectedTestCandidate,
    PrChangedFile,
    PrContextGap,
    PrContextRequest,
    PrContextResult,
    PrImpactRequest,
    PrImpactResult,
    PrImpactedFileCandidate,
    PrSymbolCandidate,
)
from ia_repomap_builder.pr_analysis import (
    BedrockSettings,
    EvidenceRecord,
    EvidenceSession,
    PRAnalysisReportV1,
    PRAnalysisRequestV1,
    build_bedrock_agent,
    load_bedrock_settings,
    prepare_pre_agentic_seed,
    run_coordinator,
    run_pr_analysis,
    run_symbol_impact_once,
)
from ia_repomap_builder.identity import git_revision, is_dirty


_LIVE_COORDINATOR_ENV = (
    "IA_REPOMAP_RUN_LIVE_BEDROCK",
    "IA_APP_REPO",
    "IA_REPOMAP_ARTIFACT_ROOT",
    "IA_APP_BASE_REF",
    "IA_REPOMAP_EXPECTED_HEAD",
    "IA_REPOMAP_LIVE_OUTPUT_DIR",
    "RIPWIRE_BIN",
)


def _live_coordinator_enabled() -> bool:
    return os.environ.get("IA_REPOMAP_RUN_LIVE_BEDROCK") == "1" and all(
        os.environ.get(name) for name in _LIVE_COORDINATOR_ENV[1:]
    )


class PRAnalysisReportTests(unittest.TestCase):
    def coordinator_request(self, directory: str) -> dict[str, object]:
        root = Path(directory)
        return {
            "schema": "ia-repomap.pr-analysis-request/v1",
            "repo_root": str(root / "repo"),
            "base_ref": "a" * 40,
            "artifact_root": str(root / "artifacts"),
            "output_dir": str(root / "reports"),
        }

    def assert_checked_in_schema(self, report: PRAnalysisReportV1) -> None:
        schema_path = Path(__file__).parents[1] / "schemas" / "ia-repomap.pr-analysis-v1.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        jsonschema.validate(report.model_dump(mode="json", by_alias=True), schema)

    def context_identity(self) -> dict[str, object]:
        return {
            "repository_id": "intacct/ia-app",
            "head": "b" * 40,
            "base_revision": "a" * 40,
            "merge_base": "a" * 40,
            "configuration_digest": "c" * 64,
            "engine": {"id": "ripwire-test"},
            "dirty": False,
        }

    def test_coordinator_request_is_versioned_and_rejects_unknown_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            request = PRAnalysisRequestV1.model_validate(self.coordinator_request(directory))
            self.assertEqual(request.base_ref, "a" * 40)
            self.assertTrue(request.repo_root.is_absolute())
            invalid = self.coordinator_request(directory)
            invalid["unexpected"] = True
            with self.assertRaises(ValidationError):
                PRAnalysisRequestV1.model_validate(invalid)

    def test_coordinator_request_rejects_internal_or_nonempty_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            invalid = self.coordinator_request(directory)
            invalid["output_dir"] = str(root / "repo" / "reports")
            with self.assertRaises(ValidationError):
                PRAnalysisRequestV1.model_validate(invalid)
            output = root / "reports"
            output.mkdir()
            (output / "existing.json").write_text("x", encoding="utf-8")
            invalid = self.coordinator_request(directory)
            with self.assertRaises(ValidationError):
                PRAnalysisRequestV1.model_validate(invalid)
            (output / "existing.json").unlink()
            output.rmdir()
            output.write_text("not a directory", encoding="utf-8")
            invalid = self.coordinator_request(directory)
            with self.assertRaises(ValidationError):
                PRAnalysisRequestV1.model_validate(invalid)

    def test_run_pr_analysis_invalid_request_is_schema_valid_without_context(self) -> None:
        calls = 0
        def builder(_: PrContextRequest) -> PrContextResult:
            nonlocal calls
            calls += 1
            return PrContextResult("ok")
        report = run_pr_analysis({"schema": "wrong"}, context_builder=builder)
        self.assertEqual((report.status, report.phase, report.agent.invoked), ("error", "request_validation", False))
        self.assertEqual(calls, 0)
        self.assertTrue(report.remediation)
        PRAnalysisReportV1.model_validate(report.model_dump(by_alias=True))
        self.assert_checked_in_schema(report)

    def test_run_pr_analysis_unavailable_and_error_do_not_construct_agent(self) -> None:
        for status, expected_phase in (("unavailable", "readiness"), ("error", "pr_context")):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                context = PrContextResult(status=status, identity=self.context_identity(), diagnostics=["fixture"])
                agent_calls = 0
                def factory(*_: object, **__: object) -> object:
                    nonlocal agent_calls
                    agent_calls += 1
                    raise AssertionError("agent must not be constructed")
                report = run_pr_analysis(
                    self.coordinator_request(directory),
                    settings=BedrockSettings("test", "model"),
                    context_builder=lambda _: context,
                    agent_factory=factory,
                )
                self.assertEqual((report.status, report.phase), (status, expected_phase))
                self.assertFalse(report.agent.invoked)
                self.assertEqual(agent_calls, 0)
                PRAnalysisReportV1.model_validate(report.model_dump(by_alias=True))
                self.assert_checked_in_schema(report)

    def test_run_pr_analysis_invalid_context_result_is_schema_valid_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = run_pr_analysis(
                self.coordinator_request(directory),
                context_builder=lambda _: None,  # type: ignore[return-value]
            )
            self.assertEqual((report.status, report.phase), ("error", "pr_context"))
            self.assertIn("invalid result", " ".join(report.diagnostics))
            PRAnalysisReportV1.model_validate(report.model_dump(by_alias=True))
            self.assert_checked_in_schema(report)

    def test_run_pr_analysis_no_seed_is_schema_valid_and_does_not_construct_agent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = PrContextResult(status="ok", identity=self.context_identity())
            requests: list[PrContextRequest] = []
            report = run_pr_analysis(
                self.coordinator_request(directory),
                context_builder=lambda request: (requests.append(request), context)[1],
                agent_factory=lambda *_args, **_kwargs: self.fail("agent must not be constructed"),
            )
            self.assertEqual((report.status, report.phase), ("ok", "pr_context"))
            self.assertFalse(report.agent.invoked)
            self.assertIn("no_candidate_symbols", {gap.kind for gap in report.gaps})
            self.assertEqual((requests[0].limit, requests[0].offset, requests[0].history_commits), (20, 0, 500))
            PRAnalysisReportV1.model_validate(report.model_dump(by_alias=True))
            self.assert_checked_in_schema(report)

    def test_run_pr_analysis_degraded_context_produces_file_action(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = PrContextResult(
                status="ok",
                raw_xml="<pr-context truncated=\"trim\"/>",
                identity=self.context_identity(),
                changed_files=[PrChangedFile(path="app/source/example/Example.cls", change="M")],
                gaps=[PrContextGap("truncated", "Ripwire output was structurally truncated")],
            )

            def factory(_settings: object, tools: list[object]) -> object:
                self.assertEqual(tools, [])
                return lambda _prompt: {
                    "summary": {
                        "purpose": "changed example behavior",
                        "behavioral_change": "unresolved",
                        "confidence": "unresolved",
                    },
                    "blast_radius": [],
                    "test_areas": [],
                }

            report = run_pr_analysis(
                self.coordinator_request(directory),
                settings=BedrockSettings("test", "model"),
                context_builder=lambda _: context,
                agent_factory=factory,
                revision_checker=lambda _: context.identity["head"],
                dirty_checker=lambda _: False,
            )
            self.assertEqual((report.status, report.phase), ("ok", "analysis"))
            self.assertTrue(report.agent.invoked)
            self.assertEqual(report.blast_radius, [])
            self.assertEqual(len(report.test_areas), 1)
            self.assertEqual(report.test_areas[0].paths, ["app/source/example/Example.cls"])
            self.assertEqual(report.metrics["impact_calls"], 0)
            self.assertIn("truncated", {gap.kind for gap in report.gaps})
            self.assert_checked_in_schema(report)

    def test_run_pr_analysis_degraded_rejects_repository_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = PrContextResult(
                status="ok",
                raw_xml="<pr-context/>",
                identity=self.context_identity(),
                changed_files=[PrChangedFile(path="app/source/example/Example.cls", change="M")],
            )
            report = run_pr_analysis(
                self.coordinator_request(directory),
                settings=BedrockSettings("test", "model"),
                context_builder=lambda _: context,
                agent_factory=lambda _settings, _tools: lambda _prompt: {
                    "summary": {
                        "purpose": "changed behavior",
                        "behavioral_change": "unresolved",
                        "confidence": "unresolved",
                    },
                    "blast_radius": [],
                    "test_areas": [],
                },
                revision_checker=lambda _: "d" * 40,
                dirty_checker=lambda _: False,
            )
            self.assertEqual((report.status, report.phase), ("error", "analysis"))
            self.assertEqual(report.blast_radius, [])
            self.assertEqual(report.test_areas, [])
            self.assertIn("repository_changed_during_analysis", {gap.kind for gap in report.gaps})

    def test_run_pr_analysis_sanitizes_prompt_and_persists_after_exact_head_guard(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            request = self.coordinator_request(directory)
            raw_xml = "<pr-context><secret>source</secret></pr-context>"
            context = PrContextResult(
                status="ok",
                raw_xml=raw_xml,
                identity={
                    **self.context_identity(),
                    "artifact_dir": "/artifacts/repo/head",
                    "manifest": "/artifacts/repo/head/manifest.json",
                    "lean_cache": "/artifacts/repo/head/index.lean.ripwirecache",
                    "rich_cache": "/artifacts/repo/head/index.rich.ripwirecache",
                },
                changed_files=[PrChangedFile(
                    path="app/source/example/Example.cls",
                    change="M",
                    symbols=(PrSymbolCandidate(
                        path="app/source/example/Example.cls",
                        name="changed",
                        line=1,
                    ),),
                )],
            )
            prompts: list[str] = []

            class FakeAgent:
                def __call__(self, prompt: str) -> dict[str, object]:
                    prompts.append(prompt)
                    return {
                        "summary": {
                            "purpose": "test",
                            "behavioral_change": "candidate",
                            "confidence": "candidate",
                        },
                        "blast_radius": [],
                        "test_areas": [],
                    }

            report = run_pr_analysis(
                request,
                settings=BedrockSettings("test", "model"),
                context_builder=lambda _: context,
                agent_factory=lambda *_args, **_kwargs: FakeAgent(),
                revision_checker=lambda _: "b" * 40,
                dirty_checker=lambda _: False,
            )
            self.assertEqual(report.status, "ok")
            self.assertTrue(report.agent.invoked)
            self.assertEqual(len(prompts), 1)
            self.assertNotIn(raw_xml, prompts[0])
            self.assertIn("pr-context-001", prompts[0])
            self.assertNotIn("/artifacts", prompts[0])
            self.assertNotIn("lean_cache", prompts[0])
            output = Path(directory) / "reports"
            self.assertEqual((output / "evidence/pr-context.xml").read_text(), raw_xml)

    def test_run_pr_analysis_rejects_post_run_head_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = PrContextResult(status="ok", identity=self.context_identity(), raw_xml="<pr-context/>", changed_files=[PrChangedFile(
                path="app/source/example/Example.cls", change="M",
                symbols=(PrSymbolCandidate("app/source/example/Example.cls", "changed", 1),),
            )])
            agent_calls = 0

            def factory(*_args: object, **_kwargs: object) -> object:
                nonlocal agent_calls
                agent_calls += 1
                return lambda _prompt: {"summary": {"purpose": "x", "behavioral_change": "x", "confidence": "candidate"}, "blast_radius": [], "test_areas": []}

            report = run_pr_analysis(
                self.coordinator_request(directory),
                settings=BedrockSettings("test", "model"),
                context_builder=lambda _: context,
                agent_factory=factory,
                revision_checker=lambda _: "c" * 40,
                dirty_checker=lambda _: False,
            )
            self.assertEqual((report.status, report.phase), ("error", "analysis"))
            self.assertIn("repository changed during analysis", " ".join(report.diagnostics))
            self.assertEqual(agent_calls, 1)
            self.assertEqual(report.summary.confidence, "unavailable")

    def test_run_pr_analysis_checks_repository_after_agent_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = PrContextResult(
                status="ok",
                identity=self.context_identity(),
                raw_xml="<pr-context/>",
                changed_files=[PrChangedFile(
                    path="app/source/example/Example.cls",
                    change="M",
                    symbols=(PrSymbolCandidate("app/source/example/Example.cls", "changed", 1),),
                )],
            )

            def factory(*_args: object, **_kwargs: object) -> object:
                return lambda _prompt: (_ for _ in ()).throw(RuntimeError("model failed"))

            report = run_pr_analysis(
                self.coordinator_request(directory),
                settings=BedrockSettings("test", "model"),
                context_builder=lambda _: context,
                agent_factory=factory,
                revision_checker=lambda _: "c" * 40,
                dirty_checker=lambda _: False,
            )
            self.assertEqual((report.status, report.phase), ("error", "analysis"))
            self.assertTrue(report.agent.invoked)
            self.assertEqual(report.agent.model_id, "model")
            self.assertIn("repository changed during analysis", " ".join(report.diagnostics))
            self.assertIn("repository_changed_during_analysis", {gap.kind for gap in report.gaps})
            self.assertEqual(report.summary.confidence, "unavailable")
            PRAnalysisReportV1.model_validate(report.model_dump(by_alias=True))

    def test_run_pr_analysis_retains_tool_evidence_after_agent_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = PrContextResult(
                status="ok",
                raw_xml="<pr-context/>",
                identity=self.context_identity(),
                changed_files=[PrChangedFile(
                    path="app/source/example/Example.cls",
                    change="M",
                    symbols=(PrSymbolCandidate(
                        "app/source/example/Example.cls", "changed", 1
                    ),),
                )],
            )
            impact = PrImpactResult(status="ok", raw_xml="<impact/>\n")

            class FailingAgent:
                def __init__(self, tool: object) -> None:
                    self.tool = tool

                def __call__(self, _prompt: str) -> object:
                    self.tool(  # type: ignore[operator]
                        symbol_path="app/source/example/Example.cls",
                        symbol_name="changed",
                    )
                    raise RuntimeError("structured output failed")

            report = run_pr_analysis(
                self.coordinator_request(directory),
                settings=BedrockSettings("test", "model"),
                context_builder=lambda _: context,
                impact_builder=lambda _: impact,
                agent_factory=lambda _settings, tools: FailingAgent(tools[0]),
                revision_checker=lambda _: "b" * 40,
                dirty_checker=lambda _: False,
            )
            self.assertEqual(report.status, "error")
            self.assertEqual(
                [item.evidence_id for item in report.evidence],
                ["pr-context-001", "symbol-impact-001"],
            )
            output = Path(directory) / "reports"
            self.assertEqual(
                (output / "evidence/symbol-impact-001.xml").read_text(),
                "<impact/>\n",
            )
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
                "invoked": False,
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

    def test_checked_in_schema_enforces_success_and_agent_provenance(self) -> None:
        path = Path(__file__).parents[1] / "schemas" / "ia-repomap.pr-analysis-v1.schema.json"
        schema = json.loads(path.read_text(encoding="utf-8"))
        jsonschema.validate(self.valid_payload(), schema)
        invalid_identity = self.valid_payload()
        invalid_identity["identity"]["head"] = None  # type: ignore[index]
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(invalid_identity, schema)
        invalid_agent = self.valid_payload()
        invalid_agent["agent"] = {  # type: ignore[assignment]
            **invalid_agent["agent"],  # type: ignore[index]
            "invoked": True,
            "model_id": None,
            "region": None,
        }
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(invalid_agent, schema)

    def test_report_rejects_duplicate_evidence_ids_on_analysis_rows(self) -> None:
        for field, row in (
            (
                "blast_radius",
                {
                    "source_path": "app/source/example.cls",
                    "source_symbol": "source",
                    "target_path": "app/source/example.cls",
                    "target_symbol": "target",
                    "relationship": "direct_caller",
                    "graph_distance": 1,
                    "confidence": "candidate",
                    "evidence_ids": ["e1", "e1"],
                    "reason": "candidate",
                },
            ),
            (
                "test_areas",
                {
                    "area": "example",
                    "paths": ["app/source/example.cls"],
                    "reason": "candidate",
                    "confidence": "candidate",
                    "evidence_ids": ["e1", "e1"],
                    "execution_status": "not_run",
                },
            ),
        ):
            with self.subTest(field=field):
                payload = self.valid_payload()
                payload["evidence"] = [{
                    "evidence_id": "e1",
                    "kind": "pr_context_xml",
                    "relative_path": "evidence/pr.xml",
                    "sha256": "d" * 64,
                }]
                payload[field] = [row]
                with self.assertRaises(ValidationError):
                    PRAnalysisReportV1.model_validate(payload)

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

    def test_bedrock_agent_uses_deterministic_generation_limits(self) -> None:
        agent = build_bedrock_agent(BedrockSettings(region="us-east-1", model_id="test-model"))
        self.assertEqual(agent.model.config["temperature"], 0)
        self.assertEqual(agent.model.config["max_tokens"], 4096)
        self.assertEqual(type(agent.tool_executor).__name__, "SequentialToolExecutor")

    @unittest.skipUnless(
        _live_coordinator_enabled(),
        "set IA_REPOMAP_RUN_LIVE_BEDROCK=1 and all coordinator smoke inputs",
    )
    def test_live_ia_app_coordinator_smoke(self) -> None:
        repo = Path(os.environ["IA_APP_REPO"]).resolve()
        artifact_root = Path(os.environ["IA_REPOMAP_ARTIFACT_ROOT"]).resolve()
        output_dir = Path(os.environ["IA_REPOMAP_LIVE_OUTPUT_DIR"]).resolve()
        expected_head = os.environ["IA_REPOMAP_EXPECTED_HEAD"]
        self.assertEqual(git_revision(repo), expected_head)
        self.assertFalse(is_dirty(repo))

        report = run_pr_analysis(
            PRAnalysisRequestV1.model_validate({
                "schema": "ia-repomap.pr-analysis-request/v1",
                "repo_root": str(repo),
                "base_ref": os.environ["IA_APP_BASE_REF"],
                "artifact_root": str(artifact_root),
                "output_dir": str(output_dir),
            }),
            settings=load_bedrock_settings(),
            allow_source_inspection=False,
        )
        self.assertEqual((report.status, report.phase), ("ok", "analysis"))
        self.assertTrue(report.agent.invoked)
        self.assertEqual(report.identity.head, expected_head)
        self.assertFalse(is_dirty(repo))
        self.assertEqual(git_revision(repo), expected_head)
        self.assertLessEqual(report.metrics.get("impact_calls", 0), 5)
        self.assertEqual(report.metrics.get("inspection_calls", 0), 0)

        report_json = output_dir / "pr-analysis.json"
        report_markdown = output_dir / "pr-analysis.md"
        raw_xml = output_dir / "evidence" / "pr-context.xml"
        self.assertTrue(report_json.is_file())
        self.assertTrue(report_markdown.is_file())
        self.assertTrue(raw_xml.is_file())
        payload = json.loads(report_json.read_text(encoding="utf-8"))
        evidence = next(item for item in payload["evidence"] if item["evidence_id"] == "pr-context-001")
        self.assertEqual(hashlib.sha256(raw_xml.read_bytes()).hexdigest(), evidence["sha256"])

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
        for _ in range(4):
            run_symbol_impact_once(session, request, impact_builder=lambda _: result)
        with self.assertRaises(ValueError):
            run_symbol_impact_once(session, request, impact_builder=lambda _: result)

    def test_symbol_impact_adapter_rejects_unauthorized_symbol(self) -> None:
        seed = prepare_pre_agentic_seed(self.context_request(), context_builder=lambda _: PrContextResult("ok"))
        session = EvidenceSession(seed)
        request = PrImpactRequest(Path("/repo"), Path("/artifacts"), "app/source/example/Example.cls", "unknown")
        with self.assertRaises(ValueError):
            run_symbol_impact_once(session, request, impact_builder=lambda _: PrContextResult("ok"))
        self.assertEqual(session.impact_calls, 1)

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
                "dirty": False,
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
        self_payload["agent"] = {"invoked": True, "model_id": "fake", "region": "test", "prompt_version": "v1", "tool_contract_version": "v1", "coordinator_version": "v1"}
        report = run_coordinator(seed, request, BedrockSettings(region="test", model_id="fake"), agent_factory=lambda _, tools: FakeAgent(tools[0]), impact_builder=lambda _: impact)
        self.assertEqual(report.schema_, "ia-repomap.pr-analysis/v1")
        self.assertEqual(report.metrics["impact_calls"], 1)

    def test_degraded_coordinator_returns_file_action_without_symbol_impact_tool(self) -> None:
        context = PrContextResult(
            status="ok",
            raw_xml="<pr-context truncated=\"trim\"/>",
            identity={
                "repository_id": "test-repository",
                "head": "b" * 40,
                "base_revision": "a" * 40,
                "merge_base": "a" * 40,
                "configuration_digest": "c" * 64,
                "engine": {"id": "test-engine"},
                "dirty": False,
            },
            changed_files=[PrChangedFile(path="app/source/example/Example.cls", change="M")],
            gaps=[PrContextGap("truncated", "Ripwire output was structurally truncated")],
        )
        seed = prepare_pre_agentic_seed(self.context_request(), context_builder=lambda _: context)
        prompts: list[str] = []

        def factory(_settings: object, tools: list[object]) -> object:
            self.assertEqual(tools, [])

            def agent(prompt: str) -> dict[str, object]:
                prompts.append(prompt)
                return {
                    "summary": {
                        "purpose": "changed example behavior",
                        "behavioral_change": "unresolved",
                        "confidence": "unresolved",
                    },
                    "blast_radius": [],
                    "test_areas": [],
                }

            return agent

        report = run_coordinator(
            seed,
            None,
            BedrockSettings(region="test", model_id="fake"),
            agent_factory=factory,
        )
        self.assertEqual(report.status, "ok")
        self.assertEqual(report.blast_radius, [])
        self.assertEqual(len(report.test_areas), 1)
        self.assertEqual(report.test_areas[0].paths, ["app/source/example/Example.cls"])
        self.assertEqual(report.test_areas[0].execution_status, "not_run")
        self.assertIn("degraded_file_diff", prompts[0])
        self.assertIn("truncated", {gap.kind for gap in report.gaps})

    def test_coordinator_preserves_tool_gaps_and_diagnostics(self) -> None:
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
                "dirty": False,
            },
            changed_files=[PrChangedFile(
                path="app/source/example/Example.cls", change="M",
                symbols=(PrSymbolCandidate(
                    path="app/source/example/Example.cls", name="changed", line=1
                ),),
            )],
        )
        seed = prepare_pre_agentic_seed(
            self.context_request(), context_builder=lambda _: context
        )
        impact = PrImpactResult(
            status="ok",
            raw_xml="<impact/>",
            gaps=[PrContextGap("impact_ambiguous", "resolution is ambiguous", 2)],
            diagnostics=["impact warning"],
        )
        request = PrImpactRequest(
            Path("/repo"), Path("/artifacts"),
            "app/source/example/Example.cls", "changed",
        )

        class FakeAgent:
            def __init__(self, tool: object) -> None:
                self.tool = tool

            def __call__(self, _prompt: str) -> dict[str, object]:
                self.tool(  # type: ignore[operator]
                    symbol_path=request.symbol_path,
                    symbol_name=request.symbol_name,
                )
                return {
                    "summary": {
                        "purpose": "test",
                        "behavioral_change": "candidate",
                        "confidence": "candidate",
                    },
                    "blast_radius": [],
                    "test_areas": [],
                }

        report = run_coordinator(
            seed,
            request,
            BedrockSettings(region="test", model_id="fake"),
            agent_factory=lambda _settings, tools: FakeAgent(tools[0]),
            impact_builder=lambda _: impact,
        )
        self.assertIn("impact_ambiguous", {gap.kind for gap in report.gaps})
        self.assertIn("impact warning", report.diagnostics)

    def test_coordinator_adds_host_affected_tests_without_authorizing_symbols(self) -> None:
        changed_path = "app/source/example/Example.cls"
        impacted_path = "app/source/service/Caller.cls"
        test_path = "app/source/tests/ExampleTest.cls"
        context = PrContextResult(
            status="ok",
            raw_xml="<pr-context/>",
            identity=self.context_identity(),
            changed_files=[PrChangedFile(
                path=changed_path,
                change="M",
                symbols=(PrSymbolCandidate(changed_path, "changed", 1),),
                impact_files=(PrImpactedFileCandidate(impacted_path, 2),),
                affected_tests=(PrAffectedTestCandidate(test_path, "run ExampleTest"),),
            )],
        )
        seed = prepare_pre_agentic_seed(
            self.context_request(), context_builder=lambda _: context
        )
        session = EvidenceSession(seed)
        self.assertIn(impacted_path, session.allowed_paths)
        self.assertIn(test_path, session.allowed_paths)

        impact_request = PrImpactRequest(
            Path("/repo"), Path("/artifacts"), changed_path, "changed"
        )
        report = run_coordinator(
            seed,
            impact_request,
            BedrockSettings(region="test", model_id="fake"),
            agent_factory=lambda _settings, tools: lambda _prompt: {
                "summary": {
                    "purpose": "test",
                    "behavioral_change": "candidate",
                    "confidence": "candidate",
                },
                "blast_radius": [],
                "test_areas": [],
            },
        )
        self.assertEqual(len(report.test_areas), 1)
        self.assertEqual(report.test_areas[0].paths, [test_path])
        self.assertEqual(report.test_areas[0].confidence, "candidate")
        self.assertEqual(report.test_areas[0].execution_status, "not_run")
        self.assertIn("run ExampleTest", report.test_areas[0].reason)

        invented_symbol_agent = lambda _settings, tools: lambda _prompt: {
            "summary": {
                "purpose": "test",
                "behavioral_change": "candidate",
                "confidence": "candidate",
            },
            "blast_radius": [{
                "source_path": changed_path,
                "source_symbol": "changed",
                "target_path": impacted_path,
                "target_symbol": "invented",
                "relationship": "transitive_reacher",
                "graph_distance": None,
                "confidence": "candidate",
                "evidence_ids": ["pr-context-001"],
                "reason": "file-only evidence has no target symbol",
            }],
            "test_areas": [],
        }
        with self.assertRaisesRegex(ValueError, "symbols outside host evidence"):
            run_coordinator(
                seed,
                impact_request,
                BedrockSettings(region="test", model_id="fake"),
                agent_factory=invented_symbol_agent,
            )

    def test_no_seed_report_retains_affected_tests_and_cap_gaps(self) -> None:
        changed_path = "app/source/example/Example.cls"
        test_path = "app/source/tests/ExampleTest.cls"
        context = PrContextResult(
            status="ok",
            raw_xml="<pr-context/>",
            identity=self.context_identity(),
            changed_files=[PrChangedFile(
                path=changed_path,
                change="M",
                affected_tests=(PrAffectedTestCandidate(test_path),),
            )],
            gaps=[
                PrContextGap("impact_truncated", "impact files capped", 2),
                PrContextGap("affected_tests_truncated", "tests capped", 1),
            ],
        )
        with tempfile.TemporaryDirectory() as directory:
            request = PRAnalysisRequestV1.model_validate(
                self.coordinator_request(directory)
            )
            report = run_pr_analysis(request, context_builder=lambda _: context)
        self.assertEqual((report.status, report.phase), ("ok", "pr_context"))
        self.assertFalse(report.agent.invoked)
        self.assertEqual(report.test_areas[0].paths, [test_path])
        self.assertEqual(report.test_areas[0].execution_status, "not_run")
        self.assertEqual(
            {gap.kind for gap in report.gaps},
            {"impact_truncated", "affected_tests_truncated", "no_candidate_symbols"},
        )
        self.assert_checked_in_schema(report)

    def test_coordinator_rejects_incomplete_identity_before_agent(self) -> None:
        context = PrContextResult(
            status="ok",
            raw_xml="<pr-context/>",
            identity={
                "repository_id": "test-repository",
                "head": "b" * 40,
                "base_revision": "a" * 40,
                "configuration_digest": "c" * 64,
                "engine": {"id": "test-engine"},
                "dirty": False,
            },
            changed_files=[PrChangedFile(
                path="app/source/example/Example.cls", change="M",
                symbols=(PrSymbolCandidate(
                    path="app/source/example/Example.cls", name="changed", line=1
                ),),
            )],
        )
        seed = prepare_pre_agentic_seed(
            self.context_request(), context_builder=lambda _: context
        )
        request = PrImpactRequest(
            Path("/repo"), Path("/artifacts"),
            "app/source/example/Example.cls", "changed",
        )
        with self.assertRaisesRegex(ValueError, "incomplete identity"):
            run_coordinator(
                seed,
                request,
                BedrockSettings(region="test", model_id="fake"),
                agent_factory=lambda *_args, **_kwargs: self.fail("agent must not be constructed"),
            )

    def test_coordinator_rejects_model_paths_not_seen_in_host_evidence(self) -> None:
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
                "dirty": False,
            },
            changed_files=[PrChangedFile(
                path="app/source/example/Example.cls",
                change="M",
                symbols=(PrSymbolCandidate(
                    "app/source/example/Example.cls", "changed", 1
                ),),
            )],
        )
        seed = prepare_pre_agentic_seed(
            self.context_request(), context_builder=lambda _: context
        )
        impact_request = PrImpactRequest(
            Path("/repo"), Path("/artifacts"),
            "app/source/example/Example.cls", "changed"
        )

        def fake_agent(*_args: object, **_kwargs: object) -> object:
            return lambda _prompt: {
                "summary": {
                    "purpose": "test",
                    "behavioral_change": "candidate",
                    "confidence": "candidate",
                },
                "blast_radius": [{
                    "source_path": "app/source/unknown/Unknown.cls",
                    "source_symbol": "unknown",
                    "target_path": "app/source/unknown/Unknown.cls",
                    "target_symbol": "unknown",
                    "relationship": "direct_caller",
                    "graph_distance": 1,
                    "confidence": "candidate",
                    "evidence_ids": ["pr-context-001"],
                    "reason": "untrusted",
                }],
                "test_areas": [],
            }

        with self.assertRaises(ValueError):
            run_coordinator(
                seed,
                impact_request,
                BedrockSettings(region="test", model_id="fake"),
                agent_factory=fake_agent,
            )


if __name__ == "__main__":
    unittest.main()
