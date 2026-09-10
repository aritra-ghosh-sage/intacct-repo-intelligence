from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ia_repomap_builder import (
    BuildResult,
    PrImpactRequest,
    PrepareRepoMapRequest,
    build_symbol_impact,
    check_prepared_repomap_readiness,
)
from ia_repomap_builder.impact import _parse_impact_xml
from ia_repomap_builder.readiness import ArtifactLocations


IMPACT_XML = """\
<impact schema="ripwire.impact/v1" of="gl/Changed.cls:changed"
 defs="1" reaches="3" shown="2" total="3" capped="1" has_more="1"
 next_offset="2" offset="0" limit="2" radius_tested="0" radius_untested="3"
 importers="1" shown_importers="1" graph_ambiguous="2" graph_unresolved="1">
  <s t="method" n="zCaller" p="gl/Z.cls:30"/>
  <s t="method" n="aCaller" p="gl/A.cls:10"/>
</impact>
"""


class ImpactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name) / "repo"
        (self.root / "app/source/gl").mkdir(parents=True)
        (self.root / "app/source/gl/Changed.cls").write_text("<?php class Changed {}\n", encoding="utf-8")
        (self.root / ".ia-repomap.toml").write_text(
            """schema_version = 1
engine = \"ripwire\"
scope = [\"app/source\"]
token_budget = 4000
php_family_extensions = [\".cls\"]
map_php_scope = [\"app/source\"]
""",
            encoding="utf-8",
        )
        self.artifacts = Path(self.tempdir.name) / "artifacts"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_parser_orders_deduplicates_and_discloses_gaps(self) -> None:
        xml = IMPACT_XML.replace(
            '<s t="method" n="aCaller" p="gl/A.cls:10"/>',
            '<s t="method" n="aCaller" p="gl/A.cls:10"/>\n'
            '  <s t="method" n="aCaller" p="gl/A.cls:10"/>\n'
            '  <s t="method" n="bad" p="/outside/Bad.cls:0"/>\n'
            '  <s t="method" n="missing" p="gl/Missing.cls"/>',
        )
        candidates, gaps, metrics = _parse_impact_xml(self.root, "app/source", xml)
        self.assertEqual([(item.name, item.line) for item in candidates], [("aCaller", 10), ("zCaller", 30)])
        self.assertEqual(metrics["reaches"], 3)
        self.assertEqual(metrics["next_offset"], 2)
        self.assertEqual(
            {gap.kind for gap in gaps},
            {
                "impact_lower_bound",
                "impact_truncated",
                "impact_importers_not_normalized",
                "impact_location_unavailable",
                "graph_ambiguous",
                "graph_unresolved",
            },
        )

    def test_parser_rejects_wrong_schema_and_out_of_scope_rows(self) -> None:
        with self.assertRaises(ValueError):
            _parse_impact_xml(
                self.root,
                "app/source",
                '<impact schema="ripwire.pr-context/v1"/>',
            )
        candidates, gaps, _ = _parse_impact_xml(
            self.root,
            "app/source",
            '<impact schema="ripwire.impact/v1"><s n="outside" p="../outside.cls:2"/></impact>',
        )
        self.assertEqual(candidates, [])
        self.assertEqual({gap.kind for gap in gaps}, {"impact_lower_bound", "impact_out_of_scope"})

    def test_request_validation_rejects_unsafe_inputs(self) -> None:
        for request, message in (
            (PrImpactRequest(self.root, self.artifacts, "../outside.cls", "Changed"), "traversal"),
            (PrImpactRequest(self.root, self.artifacts, "/tmp/Changed.cls", "Changed"), "relative"),
            (PrImpactRequest(self.root, self.artifacts, "app/source/gl/Changed.cls", " "), "empty"),
            (PrImpactRequest(self.root, self.artifacts, "app/source/gl/Changed.cls", "Changed", 0), "positive"),
            (PrImpactRequest(self.root, self.artifacts, "app/source/gl/Changed.cls", "Changed", 20, -1), "negative"),
        ):
            result = build_symbol_impact(request)
            self.assertEqual(result.status, "error")
            self.assertIn(message, result.diagnostics[0])

    def test_build_uses_named_cache_and_preserves_raw_xml(self) -> None:
        raw = '<impact schema="ripwire.impact/v1" defs="1" reaches="1" shown="1" total="1"><s t="method" n="caller" p="gl/Caller.cls:20"/></impact>'
        readiness = BuildResult(
            engine="ripwire",
            status="ok",
            diagnostics=["cache validated"],
            identity={"head": "head", "lean_cache": "/artifacts/index.lean.ripwirecache"},
        )
        request = PrImpactRequest(self.root, self.artifacts, "app/source/gl/Changed.cls", "changed")
        completed = SimpleNamespace(returncode=0, stdout=raw, stderr="")
        with (
            patch("ia_repomap_builder.impact.check_prepared_repomap_readiness", return_value=readiness),
            patch("ia_repomap_builder.impact.load_repomap_config", return_value=SimpleNamespace(scope=("app/source",))),
            patch("ia_repomap_builder.impact._ripwire_binary", return_value="/bin/ripwire"),
            patch("ia_repomap_builder.impact.subprocess.run", return_value=completed) as run,
            patch("ia_repomap_builder.impact.git_revision", return_value="head"),
            patch("ia_repomap_builder.impact.is_dirty", return_value=False),
        ):
            result = build_symbol_impact(request)
        command = run.call_args.args[0]
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.raw_xml, raw)
        self.assertEqual([(item.name, item.line) for item in result.candidates], [("caller", 20)])
        self.assertIn("--cache=/artifacts/index.lean.ripwirecache", command)
        self.assertIn("--impact=gl/Changed.cls:changed", command)
        self.assertIn("--limit=20", command)
        self.assertIn("--offset=0", command)
        self.assertEqual(hashlib.sha256(result.raw_xml.encode()).hexdigest(), hashlib.sha256(raw.encode()).hexdigest())
        self.assertEqual(json.loads(json.dumps(result.as_dict()))["candidates"][0]["line"], 20)

    def test_not_found_is_unavailable_with_explicit_gap(self) -> None:
        readiness = BuildResult(engine="ripwire", status="ok", identity={"head": "head", "lean_cache": "/cache"})
        request = PrImpactRequest(self.root, self.artifacts, "app/source/gl/Changed.cls", "missing")
        for message in (
            "ripwire: --impact symbol not found: missing",
            "ripwire: symbol is ambiguous: changed",
        ):
            completed = SimpleNamespace(returncode=1, stdout="", stderr=message)
            with (
                patch("ia_repomap_builder.impact.check_prepared_repomap_readiness", return_value=readiness),
                patch("ia_repomap_builder.impact.load_repomap_config", return_value=SimpleNamespace(scope=("app/source",))),
                patch("ia_repomap_builder.impact._ripwire_binary", return_value="/bin/ripwire"),
                patch("ia_repomap_builder.impact.subprocess.run", return_value=completed),
            ):
                result = build_symbol_impact(request)
            self.assertEqual(result.status, "unavailable")
            self.assertEqual(result.gaps[0].kind, "impact_symbol_unresolved")

    def test_prepared_readiness_does_not_require_a_base_reference(self) -> None:
        config = SimpleNamespace(scope=("app/source",))
        locations = ArtifactLocations(
            directory=self.artifacts,
            manifest=self.artifacts / "manifest.json",
            lean_cache=self.artifacts / "index.lean.ripwirecache",
            rich_cache=self.artifacts / "index.rich.ripwirecache",
        )
        self.artifacts.mkdir()
        locations.manifest.write_text("{}", encoding="utf-8")
        locations.lean_cache.write_text("lean", encoding="utf-8")
        locations.rich_cache.write_text("rich", encoding="utf-8")
        with (
            patch(
                "ia_repomap_builder.readiness._prepare_inputs",
                return_value=(
                    self.root,
                    self.artifacts,
                    config,
                    "head",
                    {"name": "ripwire", "id": "engine", "binary": "/bin/ripwire"},
                ),
            ),
            patch("ia_repomap_builder.readiness.artifact_locations", return_value=locations),
            patch("ia_repomap_builder.readiness._identity", return_value={"head": "head"}),
            patch("ia_repomap_builder.readiness._load_manifest", return_value={}),
            patch("ia_repomap_builder.readiness._manifest_matches", return_value=True),
            patch("ia_repomap_builder.readiness._verify_ripwire_pr_history", return_value=None),
            patch(
                "ia_repomap_builder.readiness._validate_ripwire_lean_cache",
                return_value=SimpleNamespace(status="ok", diagnostic=None),
            ),
        ):
            result = check_prepared_repomap_readiness(PrepareRepoMapRequest(self.root, self.artifacts))
        self.assertEqual(result.status, "ok")

    def test_malformed_xml_and_timeout_are_errors(self) -> None:
        readiness = BuildResult(engine="ripwire", status="ok", identity={"head": "head", "lean_cache": "/cache"})
        request = PrImpactRequest(self.root, self.artifacts, "app/source/gl/Changed.cls", "changed")
        for completed in (
            SimpleNamespace(returncode=0, stdout="not xml", stderr=""),
            subprocess.TimeoutExpired(["ripwire"], 300),
        ):
            with (
                patch("ia_repomap_builder.impact.check_prepared_repomap_readiness", return_value=readiness),
                patch("ia_repomap_builder.impact.load_repomap_config", return_value=SimpleNamespace(scope=("app/source",))),
                patch("ia_repomap_builder.impact._ripwire_binary", return_value="/bin/ripwire"),
                patch("ia_repomap_builder.impact.subprocess.run", side_effect=completed if isinstance(completed, Exception) else None, return_value=completed if not isinstance(completed, Exception) else None),
            ):
                result = build_symbol_impact(request)
            self.assertEqual(result.status, "error")

    def test_post_run_revision_change_discards_candidates(self) -> None:
        raw = '<impact schema="ripwire.impact/v1" defs="1" reaches="1"><s n="caller" p="gl/Caller.cls:20"/></impact>'
        readiness = BuildResult(engine="ripwire", status="ok", identity={"head": "head", "lean_cache": "/cache"})
        request = PrImpactRequest(self.root, self.artifacts, "app/source/gl/Changed.cls", "changed")
        completed = SimpleNamespace(returncode=0, stdout=raw, stderr="")
        with (
            patch("ia_repomap_builder.impact.check_prepared_repomap_readiness", return_value=readiness),
            patch("ia_repomap_builder.impact.load_repomap_config", return_value=SimpleNamespace(scope=("app/source",))),
            patch("ia_repomap_builder.impact._ripwire_binary", return_value="/bin/ripwire"),
            patch("ia_repomap_builder.impact.subprocess.run", return_value=completed),
            patch("ia_repomap_builder.impact.git_revision", return_value="changed"),
            patch("ia_repomap_builder.impact.is_dirty", return_value=False),
        ):
            result = build_symbol_impact(request)
        self.assertEqual(result.status, "error")
        self.assertEqual(result.candidates, [])
        self.assertIn("revision", result.diagnostics[0])


if __name__ == "__main__":
    unittest.main()
