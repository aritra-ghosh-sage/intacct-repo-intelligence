from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ia_repomap_builder import (
    PHP_FAMILY_EXTENSIONS,
    BuildRequest,
    BuildResult,
    EvaluationTask,
    build,
    evaluate,
    load_tasks,
    score_task,
)
from ia_repomap_builder.engines import _parse_ripwire_xml, build_ripwire
from ia_repomap_builder.files import resolve_scopes


class RepoMapBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        (self.root / "app/source/gl").mkdir(parents=True)
        (self.root / "app/source/gl/GLSetupManager.cls").write_text(
            "<?php class GLSetupManager { function loadPreferences() {} }\n",
            encoding="utf-8",
        )
        (self.root / "app/source/gl/GLSetupEditor.ent").write_text(
            "<?php interface GLSetupEditor { function savePreferences(); }\n",
            encoding="utf-8",
        )
        (self.root / "app/source/gl/ignored.map").write_text(
            "<?php class ShouldNotBeRouted {}\n", encoding="utf-8"
        )
        (self.root / "app/resources/thirdparty/foo.js.map").parent.mkdir(parents=True)
        (self.root / "app/resources/thirdparty/foo.js.map").write_text(
            '{"version":3,"sources":["foo.js"]}', encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_intacct_extensions_route_source_map_and_exclude_resources(self) -> None:
        result = build(
            BuildRequest(self.root, scope=("app/source",), query="GLSetupManager", engine="lexical")
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual({item.path for item in result.items}, {"app/source/gl/GLSetupManager.cls"})
        self.assertIn(".map", PHP_FAMILY_EXTENSIONS)
        self.assertNotIn("app/resources", {item.path for item in result.items})
        self.assertIn(".cls", PHP_FAMILY_EXTENSIONS)
        source_map = build(
            BuildRequest(self.root, scope=("app/source",), query="ShouldNotBeRouted", engine="lexical")
        )
        self.assertEqual([item.path for item in source_map.items], ["app/source/gl/ignored.map"])
        direct_map = build(BuildRequest(self.root, scope=("app/resources/thirdparty/foo.js.map",), engine="lexical"))
        self.assertEqual(direct_map.status, "error")

    def test_deterministic_output_and_identity(self) -> None:
        request = BuildRequest(self.root, query="preferences", engine="lexical", token_budget=4000)
        first = build(request).as_dict()
        second = build(request).as_dict()
        first["metrics"].pop("elapsed_ms", None)
        second["metrics"].pop("elapsed_ms", None)
        self.assertEqual(first, second)
        self.assertEqual(first["identity"]["scope"], ["app/source"])
        self.assertTrue(first["identity"]["cache_key"])

    def test_invalid_scope_and_budget_are_reported(self) -> None:
        with self.assertRaises(ValueError):
            resolve_scopes(self.root, ("../outside",))
        result = build(BuildRequest(self.root, token_budget=0))
        self.assertEqual(result.status, "error")
        self.assertIn("token_budget", result.diagnostics[0])

    def test_optional_engines_do_not_fabricate_results(self) -> None:
        for engine in ("aider", "ripwire"):
            result = build(BuildRequest(self.root, engine=engine))
            self.assertIn(result.status, {"ok", "unavailable", "error"})
            if result.status != "ok":
                self.assertTrue(result.diagnostics)

    def test_ripwire_rejects_broad_scopes_for_map_safety(self) -> None:
        result = build(BuildRequest(self.root, scope=(".",), engine="ripwire"))
        self.assertEqual(result.status, "error")
        self.assertIn("within app/source", result.diagnostics[0])

    def test_unknown_engine_is_rejected(self) -> None:
        result = build(BuildRequest(self.root, engine="unknown"))
        self.assertEqual(result.status, "error")
        self.assertIn("unknown engine", result.diagnostics[0])

    def test_lexical_context_respects_budget(self) -> None:
        result = build(BuildRequest(self.root, query="preferences", engine="lexical", token_budget=3))
        self.assertLessEqual(result.metrics["estimated_tokens"], 3)
        normal = build(BuildRequest(self.root, query="preferences", engine="lexical", token_budget=40))
        self.assertLessEqual(len(normal.context) // 4, 40)

    def test_ripwire_xml_rows_are_structured(self) -> None:
        rows = _parse_ripwire_xml(
            self.root,
            '<ctx><sigs><d l="12" n="GLSetupManager" p="gl/GLSetupManager.cls" r="1"/></sigs></ctx>',
            "app/source",
        )
        self.assertEqual(rows[0].path, "app/source/gl/GLSetupManager.cls")
        self.assertEqual(rows[0].symbol, "GLSetupManager")

    def test_ripwire_default_map_rows_are_structured(self) -> None:
        rows = _parse_ripwire_xml(
            self.root,
            '<r><f p="gl/GLSetupManager.cls"><s t="fn" n="GLSetupManager" k="0.13"/></f></r>',
            "app/source",
        )
        self.assertEqual(rows[0].path, "app/source/gl/GLSetupManager.cls")
        self.assertEqual(rows[0].symbol, "GLSetupManager")
        self.assertAlmostEqual(rows[0].score or 0, 0.13)

    def test_ripwire_malformed_xml_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _parse_ripwire_xml(self.root, "not xml", "app/source")

    def test_ripwire_malformed_xml_returns_error_result(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout="not xml", stderr="")
        request = BuildRequest(self.root, engine="ripwire")
        with (
            patch("ia_repomap_builder.engines._ripwire_binary", return_value="/bin/ripwire"),
            patch("ia_repomap_builder.engines.subprocess.run", return_value=completed),
        ):
            result = build_ripwire(request)
        self.assertEqual(result.status, "error")
        self.assertIn("XML parse failed", result.diagnostics[0])

    def test_discovery_rejects_symlink_outside_repository(self) -> None:
        with tempfile.TemporaryDirectory() as outside_dir:
            outside = Path(outside_dir) / "outside.cls"
            outside.write_text("<?php class Outside {}\n", encoding="utf-8")
            link = self.root / "app/source/outside.cls"
            try:
                link.symlink_to(outside)
            except OSError:
                self.skipTest("symlink creation is unavailable")
            result = build(BuildRequest(self.root, scope=("app/source",), engine="lexical"))
            self.assertEqual(result.status, "ok")
            self.assertNotIn("app/source/outside.cls", {item.path for item in result.items})

    def test_aider_text_scoring_uses_all_rendered_paths(self) -> None:
        task = EvaluationTask("text", "manager", frozenset({"app/source/gl/GLSetupManager.cls"}))
        result = BuildResult(
            engine="aider",
            status="ok",
            context="app/source/gl/Other.cls:\napp/source/gl/GLSetupManager.cls:",
            metrics={
                "files": [
                    "app/source/gl/Other.cls",
                    "app/source/gl/GLSetupManager.cls",
                ]
            },
        )
        row = score_task(result, task)
        self.assertEqual(row["mrr"], 0.5)

    @unittest.skipUnless(os.environ.get("IA_APP_REPO"), "set IA_APP_REPO to run the read-only ia-app smoke test")
    def test_live_ia_app_scope_smoke(self) -> None:
        repo = Path(os.environ["IA_APP_REPO"])
        result = build(
            BuildRequest(repo, scope=("app/source",), query="GLSetupManager", engine="lexical")
        )
        self.assertIn(result.status, {"ok", "unavailable", "error"})
        self.assertNotEqual(result.identity.get("scope"), ["."])

    def test_known_answer_scoring_uses_strict_file_recall_and_mrr(self) -> None:
        task = EvaluationTask(
            "manager",
            "GLSetupManager",
            frozenset({"app/source/gl/GLSetupManager.cls"}),
            frozenset({"GLSetupManager"}),
        )
        result = build(BuildRequest(self.root, query=task.query, engine="lexical"))
        row = score_task(result, task)
        self.assertEqual(row["strict_file_recall_at_5"], 1)
        self.assertEqual(row["mrr"], 1.0)
        report = evaluate(self.root, [task], "lexical")
        self.assertEqual(report["strict_file_recall_at_10"], 1.0)

    def test_task_loader_rejects_missing_gold_files(self) -> None:
        path = self.root / "tasks.json"
        path.write_text('[{"id":"bad","query":"x","gold_files":[]}]', encoding="utf-8")
        with self.assertRaises(ValueError):
            load_tasks(path)


if __name__ == "__main__":
    unittest.main()
