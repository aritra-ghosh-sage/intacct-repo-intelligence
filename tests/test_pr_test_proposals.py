from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import jsonschema

from ia_repomap_builder import pr_test_proposals as proposals


FIELD = "dimensionGroups.propertyLeaseGroup.key"
SCHEMA_PATH = "app/source/openapispec/gl/models/objects.general-ledger.statistical-posting-preference.s1.schema.yaml"
API_OPERATION = "GET /objects/general-ledger/statistical-posting-preference/{key}"
API_COLLECTION_OPERATION = "GET /objects/general-ledger/statistical-posting-preference"
OBJECT_ALIAS = "gl-statistical-posting-preference"
FEATURE_PATH = "features/gl/v1-beta2/gl-statistical-posting-preference/gl-statistical-posting-preference.feature"
INPUT_PATH = "features/gl/input/gl-statistical-posting-preference/query-gl-statistical-posting-preference.json"
OUTPUT_PATH = "features/gl/output/gl-statistical-posting-preference/res-query-gl-statistical-posting-preference.json"
EXAMPLE_VALUE = "PROPERTYLEASEGROUPKEY"


def _draft(**overrides: object) -> proposals.ProposalDraft:
    payload: dict[str, object] = {
        "title": "Read the property lease group key",
        "method": "GET",
        "route": API_COLLECTION_OPERATION.removeprefix("GET "),
        "changed_field": FIELD,
        "expected_behavior": "The response includes the documented property lease group key.",
        "prerequisites": [],
        "gaps": [],
        "evidence_paths": [SCHEMA_PATH, FEATURE_PATH],
        "feature_path": FEATURE_PATH,
        "scenario_name": "read property lease group key",
        "tag": "regression",
        "test_repo_object_alias": OBJECT_ALIAS,
        "test_repo_uri": None,
        "response_status": 200,
        "request_case": "get_preferences",
        "expected_case": "property_lease_key",
        "request_fixture": {},
        "expected_fixture": {
            "dimensionGroups": {"propertyLeaseGroup": {"key": EXAMPLE_VALUE}},
        },
    }
    payload.update(overrides)
    return proposals.ProposalDraft.model_validate(payload)


class _FakeAgent:
    def __init__(self, result: proposals._ProposalDrafts) -> None:
        self.result = result
        self.prompt = ""

    def __call__(self, prompt: str) -> proposals._ProposalDrafts:
        self.prompt = prompt
        return self.result


class OpenApiEvidenceTests(unittest.TestCase):
    def test_changed_openapi_evidence_limits_operations_and_captures_field_example(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            schema_path = root / SCHEMA_PATH
            api_path = root / "app/source/openapispec/gl/paths/objects.general-ledger.statistical-posting-preference.s1.api.yaml"
            schema_path.parent.mkdir(parents=True)
            api_path.parent.mkdir(parents=True)
            schema_path.write_text(
                """properties:\n  dimensionGroups:\n    properties:\n      propertyLeaseGroup:\n        properties:\n          key:\n            type: string\n            example: PROPERTYLEASEGROUPKEY\n""",
                encoding="utf-8",
            )
            api_path.write_text(
                """paths:\n  /objects/general-ledger/statistical-posting-preference:\n    get:\n      responses:\n        '200': {description: OK}\n  /objects/general-ledger/statistical-posting-preference/{key}:\n    get:\n      responses:\n        '200': {description: OK}\n    patch:\n      responses:\n        '200': {description: OK}\n""",
                encoding="utf-8",
            )
            report = {
                "changed_files": [{
                    "path": SCHEMA_PATH,
                    "scope": "in_scope",
                    "changed_elements": [{
                        "name": "objects.general-ledger.statistical-posting-preference.s1.schema.dimensionGroups.propertyLeaseGroup.key",
                    }],
                }],
            }

            context, operations, fields, statuses, examples = proposals._changed_openapi_paths(report, root)

        self.assertEqual(operations, {
            "GET /objects/general-ledger/statistical-posting-preference",
            API_OPERATION,
            "PATCH /objects/general-ledger/statistical-posting-preference/{key}",
        })
        self.assertNotIn("POST /objects/general-ledger/statistical-posting-preference", operations)
        self.assertIn(FIELD, fields)
        self.assertEqual(examples[FIELD], EXAMPLE_VALUE)
        self.assertTrue(any("OPENAPI_SCHEMA" in item for item in context))
        self.assertEqual(statuses[API_OPERATION], [200])

    def test_changed_path_document_is_used_without_a_changed_schema(self) -> None:
        relative = "app/source/openapispec/gl/paths/objects.statistical-preference.s1.api.yaml"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api_path = root / relative
            api_path.parent.mkdir(parents=True)
            api_path.write_text(
                """paths:\n  /objects/general-ledger/statistical-posting-preference:\n    get:\n      responses:\n        '200': {description: OK}\n""",
                encoding="utf-8",
            )
            report = {"changed_files": [{"path": relative, "scope": "in_scope", "changed_elements": []}]}

            context, operations, fields, statuses, examples = proposals._changed_openapi_paths(report, root)

        operation = "GET /objects/general-ledger/statistical-posting-preference"
        self.assertEqual(operations, {operation})
        self.assertEqual(statuses[operation], [200])
        self.assertEqual(fields, set())
        self.assertEqual(examples, {})
        self.assertTrue(any(operation in item for item in context))


class TargetRepositoryConventionTests(unittest.TestCase):
    def test_object_and_uri_columns_are_read_from_target_feature_examples(self) -> None:
        relative = "features/gl/v1-beta2/gl-statistical-journal/gl_statistical-journal.feature"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            feature = root / relative
            feature.parent.mkdir(parents=True)
            feature.write_text(
                """Examples:\n  | operation | object | uri |\n  | GET | gl-statistical-journal | /objects/general-ledger/statistical-journal/{key} |\n""",
                encoding="utf-8",
            )
            inventory = SimpleNamespace(suites=(SimpleNamespace(module="gl", feature_files=(relative,)),))

            aliases = proposals._known_object_routes(root, inventory)

        self.assertIn(("GET", "gl-statistical-journal", "/objects/general-ledger/statistical-journal/{key}"), aliases)

class ScaffoldValidationTests(unittest.TestCase):
    def test_scaffold_uses_target_module_tags_and_fixture_case_references(self) -> None:
        known = {("GET", OBJECT_ALIAS, None)}
        rendered = proposals._render_scaffold(_draft(), "IA-12345", "gl", known)

        self.assertIsNotNone(rendered)
        feature_path, feature, input_path, input_bytes, output_path, output_bytes = rendered
        self.assertEqual(feature_path, FEATURE_PATH)
        self.assertEqual(input_path, INPUT_PATH)
        self.assertEqual(output_path, OUTPUT_PATH)
        feature_text = feature.decode()
        self.assertIn("@gl @IA-12345 @regression", feature_text)
        self.assertIn("Given user copies company", feature_text)
        self.assertIn("Given user generates token", feature_text)
        self.assertLess(feature_text.index("Given user copies company"), feature_text.index("Given user generates token"))
        self.assertIn('"query-gl-statistical-posting-preference.json:get_preferences"', feature_text)
        self.assertIn('Then response code is "200"', feature_text)
        self.assertIn('And response matches "res-query-gl-statistical-posting-preference.json:property_lease_key"', feature_text)
        self.assertNotIn("I am logged in as default user", feature_text)
        self.assertNotIn("response status should be", feature_text)
        self.assertNotIn("response should match", feature_text)
        self.assertIn('"res-query-gl-statistical-posting-preference.json:property_lease_key"', feature_text)
        self.assertEqual(json.loads(input_bytes), {"get_preferences": {}})
        self.assertEqual(json.loads(output_bytes)["property_lease_key"]["dimensionGroups"]["propertyLeaseGroup"]["key"], EXAMPLE_VALUE)

    def test_scaffold_is_withheld_without_explicit_prerequisites_or_known_url_alias(self) -> None:
        self.assertIsNone(proposals._render_scaffold(
            _draft(prerequisites=["Create a preference record with a known key"]),
            "IA-12345", "gl", {("GET", OBJECT_ALIAS, None)},
        ))
        self.assertIsNone(proposals._render_scaffold(
            _draft(), "IA-12345", "gl", set(),
        ))
        self.assertIsNone(proposals._render_scaffold(
            _draft(route=API_OPERATION.removeprefix("GET ")), "IA-12345", "gl", {("GET", OBJECT_ALIAS, None)},
        ))

    def test_keyed_route_never_renders_without_setup_inside_scenario(self) -> None:
        keyed = _draft(method="PATCH", route=API_OPERATION.removeprefix("GET "))
        self.assertIsNone(proposals._render_scaffold(
            keyed, "IA-12345", "gl", {("PATCH", OBJECT_ALIAS, None)},
        ))

    def test_model_text_cannot_inject_gherkin_lines(self) -> None:
        known = {("GET", OBJECT_ALIAS, None)}
        self.assertIsNone(proposals._render_scaffold(
            _draft(title="Read preference\n  Scenario: injected"), "IA-12345", "gl", known,
        ))
        self.assertIsNone(proposals._render_scaffold(
            _draft(scenario_name="read preference\n    When injected"), "IA-12345", "gl", known,
        ))

    def test_target_destination_collision_and_symlink_withhold_scaffold(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            existing = root / OUTPUT_PATH
            existing.parent.mkdir(parents=True)
            existing.write_text("existing\n", encoding="utf-8")
            self.assertFalse(proposals._target_artifacts_available(root, [OUTPUT_PATH]))

            linked_root = root / "linked-root"
            linked_root.mkdir()
            target = root / "target"
            target.mkdir()
            (linked_root / "features").symlink_to(target, target_is_directory=True)
            self.assertFalse(proposals._target_artifacts_available(linked_root, [FEATURE_PATH]))


class PersistedInventoryValidationTests(unittest.TestCase):
    def _validate(self, status: str, dirty: bool | None, gaps: tuple[object, ...] = ()) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_dir = root / "report"
            app_repo = root / "app"
            test_repo = root / "tests"
            evidence_dir = report_dir / "evidence"
            evidence_dir.mkdir(parents=True)
            app_repo.mkdir()
            test_repo.mkdir()
            raw_inventory = b"{}\n"
            (report_dir / "pr-analysis.json").write_text("{}\n", encoding="utf-8")
            (evidence_dir / "test-inventory.json").write_bytes(raw_inventory)
            inventory_digest = "c" * 64
            report = {
                "status": "ok",
                "identity": {"head": "a" * 40, "repository": "app-repository"},
                "evidence": [{
                    "kind": "test_inventory",
                    "relative_path": "evidence/test-inventory.json",
                    "sha256": hashlib.sha256(raw_inventory).hexdigest(),
                }],
                "test_inventory_coverage": {"inventory_identity": {
                    "head": "b" * 40,
                    "repository_id": "test-repository",
                    "inventory_digest": inventory_digest,
                }},
            }
            inventory = SimpleNamespace(
                status=status,
                repository=SimpleNamespace(
                    dirty=dirty,
                    head="b" * 40,
                    repository_id="test-repository",
                    inventory_digest=inventory_digest,
                    root=str(test_repo),
                ),
                suites=(),
                gaps=gaps,
            )
            import ia_repomap_builder.identity as identity
            import ia_repomap_builder.test_inventory as test_inventory

            with (
                patch.object(proposals, "_json", return_value=(report, b"report bytes")),
                patch.object(proposals.PRAnalysisReportV1, "model_validate", return_value=SimpleNamespace(status="ok")),
                patch.object(proposals, "_inventory_from_payload", return_value=inventory),
                patch.object(proposals, "_canonical_inventory_digest", return_value=inventory_digest),
                patch.object(proposals, "git_revision", side_effect=lambda path: "a" * 40 if path == app_repo.resolve() else "b" * 40),
                patch.object(proposals, "is_dirty", return_value=False),
                patch.object(identity, "repository_id", return_value="app-repository"),
                patch.object(test_inventory, "_repository_id", return_value="test-repository"),
            ):
                proposals._validate_bundle(report_dir, app_repo, test_repo)

    def test_rejects_non_ok_persisted_inventory_status(self) -> None:
        with self.assertRaisesRegex(ValueError, "successful persisted test inventory"):
            self._validate("unavailable", False)

    def test_rejects_persisted_dirty_or_unknown_state(self) -> None:
        for dirty in (True, None):
            with self.subTest(dirty=dirty), self.assertRaisesRegex(ValueError, "record a clean repository"):
                self._validate("ok", dirty)

    def test_ok_inventory_with_discovery_gaps_remains_eligible(self) -> None:
        self._validate("ok", False, gaps=(object(),))


class ProposalOutputTests(unittest.TestCase):
    def _run_with_draft(
        self,
        draft: proposals.ProposalDraft,
        jira_key: str | None = "IA-12345",
        target_collision_path: str | None = None,
    ) -> tuple[Path, dict[str, object]]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        report_dir = root / "report"
        app_repo = root / "app"
        test_repo = root / "tests"
        output_dir = root / "output"
        for path in (report_dir, app_repo, test_repo):
            path.mkdir()
        report = {
            "identity": {"head": "a" * 40},
            "changed_files": [{"path": SCHEMA_PATH, "scope": "in_scope", "changed_elements": []}],
            "evidence": [],
            "summary": {"purpose": "Add a property lease group field"},
        }
        inventory = SimpleNamespace(repository=SimpleNamespace(
            head="b" * 40,
            repository_id="test-repository",
            inventory_digest="c" * 64,
        ), suites=(), gaps=())
        if target_collision_path:
            collision = test_repo / target_collision_path
            collision.parent.mkdir(parents=True, exist_ok=True)
            collision.write_text("existing target artifact\n", encoding="utf-8")
        fake_agent = _FakeAgent(proposals._ProposalDrafts(proposals=[draft]))
        operation = f"{draft.method.upper()} {draft.route}"
        allowed_operations = {API_OPERATION} if draft.route == "/objects/not-in-openapi" else {operation}
        with (
            patch.object(proposals, "_validate_bundle", return_value=(report, inventory, "d" * 64, "e" * 64)),
            patch.object(proposals, "_changed_openapi_paths", return_value=(
                ["OpenAPI evidence"], allowed_operations, {FIELD}, {operation: [200]}, {FIELD: EXAMPLE_VALUE},
            )),
            patch.object(proposals, "_target_examples", return_value=[{
                "path": FEATURE_PATH, "kind": "feature", "evidence_role": "style_only", "content": "Feature: example",
            }]),
            patch.object(proposals, "_known_object_routes", return_value={(draft.method.upper(), OBJECT_ALIAS, None)}),
            patch.object(proposals, "_build_agent", return_value=fake_agent),
            patch("ia_repomap_builder.pr_analysis.load_bedrock_settings", return_value=object()),
        ):
            result = proposals.run_test_proposals(report_dir, app_repo, test_repo, output_dir, jira_key)
        self.assertIn("allowed_operations", fake_agent.prompt)
        return output_dir, result

    def test_complete_evidence_emits_hashed_draft_artifacts_with_not_run_status(self) -> None:
        output_dir, result = self._run_with_draft(_draft())

        proposal = result["proposals"][0]
        self.assertEqual(proposal["scaffold_status"], "ready")
        self.assertEqual(proposal["execution_status"], "not_run")
        self.assertEqual(len(proposal["artifacts"]), 3)
        self.assertEqual(set(proposal["artifact_sha256"]), set(proposal["artifacts"]))
        for relative in proposal["artifacts"]:
            self.assertTrue((output_dir / relative).is_file())
        persisted = json.loads((output_dir / "test-proposals.json").read_text(encoding="utf-8"))
        self.assertEqual(persisted["execution_status"], "not_run")
        self.assertEqual(persisted["identity"]["app_head"], "a" * 40)
        schema_path = Path(__file__).parents[1] / "schemas" / "ia-repomap.pr-test-proposal-v1.schema.json"
        jsonschema.validate(persisted, json.loads(schema_path.read_text(encoding="utf-8")))

    def test_unsupported_operation_or_missing_jira_yields_proposal_with_gaps_no_scaffold(self) -> None:
        draft = _draft(method="POST", route="/objects/not-in-openapi")
        output_dir, result = self._run_with_draft(draft, jira_key=None)

        proposal = result["proposals"][0]
        self.assertEqual(proposal["scaffold_status"], "proposal_with_gaps")
        self.assertTrue(any("not present in the changed OpenAPI" in gap for gap in proposal["proposal"]["gaps"]))
        self.assertTrue(any("supply a Jira key" in gap for gap in proposal["proposal"]["gaps"]))
        self.assertEqual(proposal["artifacts"], [])
        self.assertFalse((output_dir / "features").exists())

    def test_keyed_patch_without_executable_setup_remains_a_proposal(self) -> None:
        draft = _draft(method="PATCH", route=API_OPERATION.removeprefix("GET "))
        output_dir, result = self._run_with_draft(draft)

        proposal = result["proposals"][0]
        self.assertEqual(proposal["scaffold_status"], "proposal_with_gaps")
        self.assertTrue(any("lacks executable setup in the generated scenario" in gap for gap in proposal["proposal"]["gaps"]))
        self.assertEqual(proposal["artifacts"], [])
        self.assertFalse((output_dir / "features").exists())

    def test_existing_target_fixture_withholds_scaffold(self) -> None:
        output_dir, result = self._run_with_draft(_draft(), target_collision_path=OUTPUT_PATH)

        proposal = result["proposals"][0]
        self.assertEqual(proposal["scaffold_status"], "proposal_with_gaps")
        self.assertTrue(any("target test repository already contains" in gap for gap in proposal["proposal"]["gaps"]))
        self.assertEqual(proposal["artifacts"], [])
        self.assertFalse((output_dir / "features").exists())

    def test_output_directory_cannot_overlap_an_input_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / "report"
            app_repo = root / "app"
            test_repo = root / "tests"
            for path in (report, app_repo, test_repo):
                path.mkdir()
            with self.assertRaisesRegex(ValueError, "outside"):
                proposals.run_test_proposals(report, app_repo, test_repo, app_repo / "proposals")

    def test_failed_scaffold_write_rolls_back_files_already_written(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        report_dir, app_repo, test_repo = root / "report", root / "app", root / "tests"
        for path in (report_dir, app_repo, test_repo):
            path.mkdir()
        output_dir = root / "output"
        report = {
            "identity": {"head": "a" * 40},
            "changed_files": [{"path": SCHEMA_PATH, "scope": "in_scope", "changed_elements": []}],
            "evidence": [],
            "summary": {"purpose": "Add a property lease group field"},
        }
        inventory = SimpleNamespace(repository=SimpleNamespace(
            head="b" * 40, repository_id="test-repository", inventory_digest="c" * 64,
        ), suites=(SimpleNamespace(module="gl", feature_files=(FEATURE_PATH,)),), gaps=())
        agent = _FakeAgent(proposals._ProposalDrafts(proposals=[_draft()]))
        write_original = proposals._write_new_file
        calls = 0

        def fail_second_write(root_path: Path, relative: str, content: bytes) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated fixture write failure")
            write_original(root_path, relative, content)

        with (
            patch.object(proposals, "_validate_bundle", return_value=(report, inventory, "d" * 64, "e" * 64)),
            patch.object(proposals, "_changed_openapi_paths", return_value=(
                ["GET evidence"], {API_OPERATION}, {FIELD}, {API_OPERATION: [200]}, {FIELD: EXAMPLE_VALUE},
            )),
            patch.object(proposals, "_target_examples", return_value=[{
                "path": FEATURE_PATH, "kind": "feature", "evidence_role": "style_only", "content": "Feature: example",
            }]),
            patch.object(proposals, "_known_object_routes", return_value={("GET", OBJECT_ALIAS, None)}),
            patch.object(proposals, "_build_agent", return_value=agent),
            patch("ia_repomap_builder.pr_analysis.load_bedrock_settings", return_value=object()),
            patch.object(proposals, "_write_new_file", side_effect=fail_second_write),
        ):
            with self.assertRaisesRegex(OSError, "simulated fixture write failure"):
                proposals.run_test_proposals(report_dir, app_repo, test_repo, output_dir, "IA-12345")

        self.assertFalse((output_dir / FEATURE_PATH).exists())
        self.assertFalse((output_dir / INPUT_PATH).exists())
        self.assertFalse((output_dir / OUTPUT_PATH).exists())


if __name__ == "__main__":
    unittest.main()
