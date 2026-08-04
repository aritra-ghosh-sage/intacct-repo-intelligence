from __future__ import annotations

import ast
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from catalog.repositories import (
    RepositoryError,
    load_workspace_manifest,
    register_manifest,
)
from scripts import refresh_gateway_sidecar
from scripts.query_gateway_sidecar import query
from scripts.refresh_gateway_sidecar import build

ROOT = Path(__file__).resolve().parents[1]


class GatewaySidecarTests(unittest.TestCase):
    def _git(self, root: Path, *args: str) -> None:
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)

    def _commit(self, root: Path, message: str) -> str:
        self._git(root, "add", ".")
        self._git(
            root,
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            message,
        )
        return subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
        ).strip()

    def _mapping(self, root: Path) -> Path:
        mapping = root / "mappings.yaml"
        mapping.write_text(
            "version: 1\nmappings:\n"
            "  - operation: create\n    object: GLBATCH\n    entity: GLBatch\n"
            "  - operation: update\n    object: GLBATCH\n    entity: GLBatch\n",
            encoding="utf-8",
        )
        return mapping

    def _empty_mapping(self, root: Path) -> Path:
        mapping = root / "empty-mappings.yaml"
        mapping.write_text("version: 1\nmappings: []\n", encoding="utf-8")
        return mapping

    def _definition_row(self, request: str, response: str = "") -> str:
        return f"{request},{response},description,pass,std_company,FALSE\n"

    def _request_xml(
        self,
        operation: str = "create",
        object_name: str = "GLBATCH",
        *,
        declaration: bool = False,
    ) -> str:
        prefix = '<?xml version="1.0"?>' if declaration else ""
        return (
            f"{prefix}<request><operation><content><function>"
            f"<{operation}><{object_name}/></{operation}>"
            "</function></content></operation></request>"
        )

    def _normalized_facts(self, db: Path) -> dict[str, list[tuple]]:
        connection = sqlite3.connect(db)
        self.addCleanup(connection.close)
        build_id = int(
            connection.execute(
                "SELECT id FROM gateway_sidecar_builds ORDER BY id DESC LIMIT 1"
            ).fetchone()[0]
        )
        return {
            "definitions": connection.execute(
                "SELECT source_path,source_blob_sha,row_number,gateway_operation,gateway_object,xml_reference,reference_state,resolved_xml_path FROM gateway_definitions WHERE build_id=? ORDER BY source_path,row_number",
                (build_id,),
            ).fetchall(),
            "xml": connection.execute(
                "SELECT source_path,source_blob_sha,parse_status,diagnostic_code FROM gateway_xml_artifacts WHERE build_id=? ORDER BY source_path",
                (build_id,),
            ).fetchall(),
            "links": connection.execute(
                "SELECT gd.source_path,gd.row_number,gel.entity_name,gel.mapping_key FROM gateway_entity_links gel JOIN gateway_definitions gd ON gd.id=gel.definition_id WHERE gd.build_id=? ORDER BY gd.source_path,gd.row_number,gel.entity_name,gel.mapping_key",
                (build_id,),
            ).fetchall(),
            "diagnostics": connection.execute(
                "SELECT source_path,row_number,code FROM gateway_diagnostics WHERE build_id=? ORDER BY source_path,row_number,code",
                (build_id,),
            ).fetchall(),
        }

    def test_raw_blob_build_is_isolated_and_rejects_unsafe_xml(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name) / "gateway"
        root.mkdir()
        self._git(root, "init", "-b", "main")
        (root / "testdefinitions").mkdir()
        (root / "testdefinitions" / "definitions.csv").write_text(
            "; functional_team=General Ledger\n"
            + self._definition_row("unsafe/request.xml", "unsafe/response.xml"),
            encoding="utf-8",
        )
        (root / "testscripts" / "unsafe").mkdir(parents=True)
        (root / "testscripts" / "unsafe" / "request.xml").write_text(
            "<!DOCTYPE x><request/>", encoding="utf-8"
        )
        (root / "testscripts" / "unsafe" / "response.xml").write_text(
            "<response/>", encoding="utf-8"
        )
        self._git(root, "add", ".")
        self._git(root, "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixture")
        mapping = Path(directory.name) / "mappings.yaml"
        mapping.write_text("version: 1\nmappings: []\n", encoding="utf-8")
        sidecar = Path(directory.name) / "sidecar.db"
        sha = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
        build(
            repo_root=root,
            target_sha=sha,
            sidecar_db=sidecar,
            mapping_file=mapping,
            ia_main_sha=sha,
        )
        status = query(sidecar)
        self.assertEqual("ok", status["status"])
        self.assertEqual(1, status["definitions"])
        self.assertEqual(0, status["approved_mappings"])
        self.assertEqual(1, status["xml"]["rejected"])
        self.assertEqual(1, status["diagnostics"]["unsafe_xml_declaration"])
        # The central schema tables are absent by construction.
        conn = sqlite3.connect(sidecar)
        self.addCleanup(conn.close)
        self.assertIsNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name='files'").fetchone())
        stored_paths = {
            row[0]
            for row in conn.execute(
                "SELECT source_path FROM gateway_xml_artifacts"
            )
        }
        self.assertEqual({"testscripts/unsafe/request.xml"}, stored_paths)

    def test_positional_contract_ignores_response_and_extracts_direct_pair(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        base = Path(directory.name)
        root = base / "gateway"
        root.mkdir()
        self._git(root, "init", "-b", "main")
        (root / "testdefinitions").mkdir()
        (root / "testscripts" / "case").mkdir(parents=True)
        definition = root / "testdefinitions" / "definition.csv"
        definition.write_text(
            "; functional_team=General Ledger\n"
            '"; contact_email=PROHIBITED_METADATA_TOKEN",,,,,\n'
            + self._definition_row("case/request.xml", "case/response.xml")
            + "; publish_dashboard=1\n",
            encoding="utf-8",
        )
        request = root / "testscripts" / "case" / "request.xml"
        response = root / "testscripts" / "case" / "response.xml"
        request.write_text(
            self._request_xml(declaration=True), encoding="utf-8"
        )
        response.write_text(
            "<response><PROHIBITED_RESPONSE_TOKEN/></response>", encoding="utf-8"
        )
        target_sha = self._commit(root, "fixture")
        sidecar = base / "sidecar.db"
        build(
            repo_root=root,
            target_sha=target_sha,
            sidecar_db=sidecar,
            mapping_file=self._empty_mapping(base),
            ia_main_sha=target_sha,
        )
        connection = sqlite3.connect(sidecar)
        self.addCleanup(connection.close)
        row = connection.execute(
            "SELECT source_path,source_blob_sha,gateway_operation,gateway_object,xml_reference,reference_state,resolved_xml_path FROM gateway_definitions"
        ).fetchone()
        self.assertEqual(
            (
                "testdefinitions/definition.csv",
                subprocess.check_output(
                    ["git", "-C", str(root), "rev-parse", "HEAD:testdefinitions/definition.csv"],
                    text=True,
                ).strip(),
                "create",
                "GLBATCH",
                "case/request.xml",
                "resolved",
                "testscripts/case/request.xml",
            ),
            row,
        )
        artifacts = connection.execute(
            "SELECT source_path,source_blob_sha,parse_status FROM gateway_xml_artifacts"
        ).fetchall()
        self.assertEqual(
            [
                (
                    "testscripts/case/request.xml",
                    subprocess.check_output(
                        ["git", "-C", str(root), "rev-parse", "HEAD:testscripts/case/request.xml"],
                        text=True,
                    ).strip(),
                    "parsed",
                )
            ],
            artifacts,
        )
        self.assertEqual(0, query(sidecar)["approved_mappings"])
        self.assertEqual(
            1, query(sidecar)["diagnostics"]["unmapped_gateway_operation"]
        )
        stored_text = "\n".join(
            str(value)
            for table, columns in (
                ("gateway_definitions", "source_path,xml_reference,resolved_xml_path"),
                ("gateway_xml_artifacts", "source_path,diagnostic_code"),
                ("gateway_diagnostics", "source_path,code"),
            )
            for row_values in connection.execute(f"SELECT {columns} FROM {table}")
            for value in row_values
            if value is not None
        )
        self.assertNotIn("response.xml", stored_text)
        self.assertNotIn("PROHIBITED_RESPONSE_TOKEN", stored_text)
        self.assertNotIn("PROHIBITED_METADATA_TOKEN", stored_text)

    def test_invalid_csv_shapes_and_unsafe_references_fail_closed(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        base = Path(directory.name)
        root = base / "gateway"
        root.mkdir()
        self._git(root, "init", "-b", "main")
        definitions = root / "testdefinitions"
        definitions.mkdir()
        scripts = root / "testscripts"
        scripts.mkdir()
        scripts.joinpath("delay_2").write_text("2", encoding="utf-8")
        (definitions / "invalid-utf8.csv").write_bytes(
            b"request.xml,,description,pass,std_company,FALSE\xff\n"
        )
        (definitions / "invalid-csv.csv").write_text(
            '"request.xml,,description,pass,std_company,FALSE\n', encoding="utf-8"
        )
        (definitions / "unsupported-width.csv").write_text(
            "request.xml,response.xml\n", encoding="utf-8"
        )
        (definitions / "unsafe.csv").write_text(
            self._definition_row(
                "<request><password>PROHIBITED_INLINE_TOKEN</password></request>"
            )
            + self._definition_row("https://example.invalid/request.xml")
            + self._definition_row("../escape.xml")
            + self._definition_row("Delay_120")
            + self._definition_row("delay_2")
            + self._definition_row("missing/request.xml"),
            encoding="utf-8",
        )
        target_sha = self._commit(root, "fixture")
        sidecar = base / "sidecar.db"
        build(
            repo_root=root,
            target_sha=target_sha,
            sidecar_db=sidecar,
            mapping_file=self._empty_mapping(base),
            ia_main_sha=target_sha,
        )
        status = query(sidecar)
        self.assertEqual(2, status["diagnostics"]["invalid_csv"])
        self.assertEqual(1, status["diagnostics"]["unsupported_csv_shape"])
        self.assertEqual(
            5, status["diagnostics"]["unsupported_request_reference"]
        )
        self.assertEqual(1, status["diagnostics"]["xml_reference_missing"])
        connection = sqlite3.connect(sidecar)
        self.addCleanup(connection.close)
        rows = connection.execute(
            "SELECT xml_reference,resolved_xml_path,reference_state FROM gateway_definitions ORDER BY id"
        ).fetchall()
        self.assertEqual(
            [(None, None, "literal")] * 5
            + [("missing/request.xml", None, "missing")],
            rows,
        )
        stored_values = [
            str(value)
            for table, columns in (
                ("gateway_definitions", "source_path,xml_reference,resolved_xml_path"),
                ("gateway_diagnostics", "source_path,code"),
            )
            for row_values in connection.execute(f"SELECT {columns} FROM {table}")
            for value in row_values
            if value is not None
        ]
        self.assertFalse(
            any("PROHIBITED_INLINE_TOKEN" in value for value in stored_values)
        )

    def test_declared_documents_fragments_and_ambiguous_requests(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        base = Path(directory.name)
        root = base / "gateway"
        root.mkdir()
        self._git(root, "init", "-b", "main")
        definitions = root / "testdefinitions"
        scripts = root / "testscripts"
        definitions.mkdir()
        scripts.mkdir()
        references = (
            "declared.xml",
            "root-function.xml",
            "fragment.xml",
            "ambiguous.xml",
            "declared-fragment.xml",
            "unsafe.xml",
            "utf16-unsafe.xml",
        )
        definitions.joinpath("definitions.csv").write_text(
            "".join(self._definition_row(reference) for reference in references),
            encoding="utf-8",
        )
        scripts.joinpath("declared.xml").write_text(
            self._request_xml(declaration=True), encoding="utf-8"
        )
        repeated = "<function><create><GLBATCH/></create></function>"
        scripts.joinpath("root-function.xml").write_text(
            repeated, encoding="utf-8"
        )
        scripts.joinpath("fragment.xml").write_text(repeated * 2, encoding="utf-8")
        scripts.joinpath("ambiguous.xml").write_text(
            repeated + "<function><update><GLBATCH/></update></function>",
            encoding="utf-8",
        )
        scripts.joinpath("declared-fragment.xml").write_text(
            '<?xml version="1.0"?>' + repeated * 2, encoding="utf-8"
        )
        scripts.joinpath("unsafe.xml").write_text(
            "<!DOCTYPE x><request/>", encoding="utf-8"
        )
        scripts.joinpath("utf16-unsafe.xml").write_bytes(
            (
                '<?xml version="1.0" encoding="UTF-16"?>'
                '<!DOCTYPE request [<!ENTITY x "GLBATCH">]>'
                "<request><operation><content><function><read>"
                "<object>&x;</object></read></function></content></operation></request>"
            ).encode("utf-16")
        )
        target_sha = self._commit(root, "fixture")
        sidecar = base / "sidecar.db"
        build(
            repo_root=root,
            target_sha=target_sha,
            sidecar_db=sidecar,
            mapping_file=self._mapping(base),
            ia_main_sha=target_sha,
        )
        status = query(sidecar)
        self.assertEqual({"parsed": 4, "rejected": 3}, status["xml"])
        self.assertEqual(1, status["diagnostics"]["ambiguous_gateway_request"])
        self.assertEqual(2, status["diagnostics"]["invalid_xml"])
        self.assertEqual(1, status["diagnostics"]["unsafe_xml_declaration"])
        self.assertEqual(3, status["approved_mappings"])

    def test_uniform_multi_payload_is_grounded_but_mixed_payload_is_not(self) -> None:
        uniform = refresh_gateway_sidecar._parse_xml(
            b"<function><create><GLBATCH/><GLBATCH/></create></function>"
        )
        self.assertEqual(("create", "GLBATCH"), (uniform.operation, uniform.object_name))
        mixed = refresh_gateway_sidecar._parse_xml(
            b"<function><create><GLBATCH/><APBILL/></create></function>"
        )
        self.assertEqual("unsupported_gateway_object", mixed.diagnostic_code)
        self.assertEqual(("create", None), (mixed.operation, mixed.object_name))

    def test_manifest_marks_gateway_as_sidecar_and_central_registration_rejects_it(self) -> None:
        manifest = load_workspace_manifest(ROOT / "config/workspace_repos.yaml")
        sidecar = next(item for item in manifest["repositories"] if item["repo_key"] == "ia-gwdata-gl")
        self.assertEqual("xml_gateway_automation", sidecar["profile"])
        self.assertEqual("sidecar", sidecar["storage"])
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.executescript((ROOT / "catalog/schema.sql").read_text())
        with self.assertRaisesRegex(RepositoryError, "sidecar storage"):
            register_manifest(conn, {"version": 1, "repositories": [sidecar]})

    def test_delta_reuses_unchanged_blobs_and_matches_full_build(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name) / "gateway"
        root.mkdir()
        self._git(root, "init", "-b", "main")
        (root / "testdefinitions").mkdir()
        (root / "testscripts" / "xml").mkdir(parents=True)
        (root / "testdefinitions" / "a.csv").write_text(
            self._definition_row("xml/a.xml", "xml/res_a.xml"),
            encoding="utf-8",
        )
        (root / "testdefinitions" / "b.csv").write_text(
            self._definition_row("xml/b.xml", "xml/res_b.xml"),
            encoding="utf-8",
        )
        (root / "testscripts" / "xml" / "a.xml").write_text(
            self._request_xml(), encoding="utf-8"
        )
        (root / "testscripts" / "xml" / "b.xml").write_text(
            self._request_xml(), encoding="utf-8"
        )
        for name in ("res_a.xml", "res_b.xml"):
            (root / "testscripts" / "xml" / name).write_text(
                "<response/>", encoding="utf-8"
            )
        first_sha = self._commit(root, "first")
        mapping = self._mapping(Path(directory.name))
        delta_db = Path(directory.name) / "delta.db"
        build(
            repo_root=root,
            target_sha=first_sha,
            sidecar_db=delta_db,
            mapping_file=mapping,
            ia_main_sha=first_sha,
        )

        unchanged_csv_blob = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD:testdefinitions/b.csv"], text=True
        ).strip()
        unchanged_xml_blob = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD:testscripts/xml/b.xml"], text=True
        ).strip()
        (root / "testdefinitions" / "a.csv").write_text(
            self._definition_row("xml/update.xml", "xml/res_a.xml"),
            encoding="utf-8",
        )
        (root / "testscripts" / "xml" / "update.xml").write_text(
            self._request_xml("update"), encoding="utf-8"
        )
        second_sha = self._commit(root, "second")
        original_blob = refresh_gateway_sidecar._blob
        read_blobs: list[str] = []

        def recording_blob(repo_root: Path, sha: str) -> bytes:
            read_blobs.append(sha)
            return original_blob(repo_root, sha)

        with mock.patch.object(
            refresh_gateway_sidecar, "_blob", side_effect=recording_blob
        ):
            result = build(
                repo_root=root,
                target_sha=second_sha,
                sidecar_db=delta_db,
                mapping_file=mapping,
                ia_main_sha=first_sha,
                requested_mode="delta",
            )
        self.assertEqual("delta", result["effective_mode"])
        self.assertNotIn(unchanged_csv_blob, read_blobs)
        self.assertNotIn(unchanged_xml_blob, read_blobs)

        full_db = Path(directory.name) / "full.db"
        build(
            repo_root=root,
            target_sha=second_sha,
            sidecar_db=full_db,
            mapping_file=mapping,
            ia_main_sha=first_sha,
        )
        self.assertEqual(
            self._normalized_facts(full_db), self._normalized_facts(delta_db)
        )

    def test_delta_ignores_csv_outside_definition_surface(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        base = Path(directory.name)
        root = base / "gateway"
        root.mkdir()
        self._git(root, "init", "-b", "main")
        (root / "testdefinitions").mkdir()
        (root / "testscripts").mkdir()
        (root / "testdefinitions" / "definition.csv").write_text(
            self._definition_row("request.xml"), encoding="utf-8"
        )
        (root / "testscripts" / "request.xml").write_text(
            self._request_xml(), encoding="utf-8"
        )
        first_sha = self._commit(root, "first")
        mapping = self._mapping(base)
        delta_db = base / "delta.db"
        build(
            repo_root=root,
            target_sha=first_sha,
            sidecar_db=delta_db,
            mapping_file=mapping,
            ia_main_sha=first_sha,
        )
        (root / "testscripts" / "leak.csv").write_text(
            self._definition_row("request.xml"), encoding="utf-8"
        )
        second_sha = self._commit(root, "second")
        result = build(
            repo_root=root,
            target_sha=second_sha,
            sidecar_db=delta_db,
            mapping_file=mapping,
            ia_main_sha=first_sha,
            requested_mode="delta",
        )
        self.assertEqual("delta", result["effective_mode"])
        self.assertEqual(1, result["definitions"])
        full_db = base / "full.db"
        build(
            repo_root=root,
            target_sha=second_sha,
            sidecar_db=full_db,
            mapping_file=mapping,
            ia_main_sha=first_sha,
        )
        self.assertEqual(
            self._normalized_facts(full_db), self._normalized_facts(delta_db)
        )

    def test_dependency_change_relinks_without_reparsing_and_noop_is_stable(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name) / "gateway"
        root.mkdir()
        self._git(root, "init", "-b", "main")
        (root / "testdefinitions").mkdir()
        (root / "testscripts").mkdir()
        (root / "testdefinitions" / "definition.csv").write_text(
            self._definition_row("request.xml"), encoding="utf-8"
        )
        (root / "testscripts" / "request.xml").write_text(
            self._request_xml(), encoding="utf-8"
        )
        target_sha = self._commit(root, "fixture")
        mapping = self._mapping(Path(directory.name))
        sidecar = Path(directory.name) / "sidecar.db"
        first = build(
            repo_root=root,
            target_sha="HEAD",
            sidecar_db=sidecar,
            mapping_file=mapping,
            ia_main_sha="a" * 40,
        )
        with mock.patch.object(
            refresh_gateway_sidecar,
            "_blob",
            side_effect=AssertionError("unchanged blobs must not be reparsed"),
        ):
            second = build(
                repo_root=root,
                target_sha="HEAD",
                sidecar_db=sidecar,
                mapping_file=mapping,
                ia_main_sha="b" * 40,
                requested_mode="delta",
            )
        self.assertEqual("delta", second["effective_mode"])
        self.assertNotEqual(first["build_id"], second["build_id"])
        provenance = query(sidecar)["provenance"]
        self.assertEqual(target_sha, provenance["target_sha"])
        self.assertEqual(
            {"ia-gwdata-gl": target_sha, "ia-main": "b" * 40},
            json.loads(provenance["dependency_revisions_json"]),
        )
        noop = build(
            repo_root=root,
            target_sha="HEAD",
            sidecar_db=sidecar,
            mapping_file=mapping,
            ia_main_sha="b" * 40,
            requested_mode="delta",
        )
        self.assertEqual("noop", noop["effective_mode"])
        self.assertEqual(second["build_id"], noop["build_id"])

    def test_changed_request_xml_clears_stale_semantics_and_matches_full(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        base = Path(directory.name)
        root = base / "gateway"
        root.mkdir()
        self._git(root, "init", "-b", "main")
        (root / "testdefinitions").mkdir()
        (root / "testscripts").mkdir()
        (root / "testdefinitions" / "definition.csv").write_text(
            self._definition_row("request.xml"), encoding="utf-8"
        )
        request = root / "testscripts" / "request.xml"
        request.write_text(self._request_xml(), encoding="utf-8")
        first_sha = self._commit(root, "first")
        mapping = self._mapping(base)
        delta_db = base / "delta.db"
        first = build(
            repo_root=root,
            target_sha=first_sha,
            sidecar_db=delta_db,
            mapping_file=mapping,
            ia_main_sha=first_sha,
        )
        self.assertEqual(1, query(delta_db)["approved_mappings"])
        request.write_text(
            "<function><create><GLBATCH/></create></function>"
            "<function><update><GLBATCH/></update></function>",
            encoding="utf-8",
        )
        second_sha = self._commit(root, "second")
        second = build(
            repo_root=root,
            target_sha=second_sha,
            sidecar_db=delta_db,
            mapping_file=mapping,
            ia_main_sha=first_sha,
            requested_mode="delta",
        )
        self.assertEqual("delta", second["effective_mode"])
        self.assertNotEqual(first["build_id"], second["build_id"])
        self.assertEqual(0, query(delta_db)["approved_mappings"])
        connection = sqlite3.connect(delta_db)
        self.addCleanup(connection.close)
        self.assertEqual(
            (None, None),
            connection.execute(
                "SELECT gateway_operation,gateway_object FROM gateway_definitions WHERE build_id=?",
                (second["build_id"],),
            ).fetchone(),
        )
        full_db = base / "full.db"
        build(
            repo_root=root,
            target_sha=second_sha,
            sidecar_db=full_db,
            mapping_file=mapping,
            ia_main_sha=first_sha,
        )
        self.assertEqual(
            self._normalized_facts(full_db), self._normalized_facts(delta_db)
        )

    def test_legacy_delta_base_falls_back_explicitly(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name) / "gateway"
        root.mkdir()
        self._git(root, "init", "-b", "main")
        (root / "testdefinitions").mkdir()
        definition = root / "testdefinitions" / "definition.csv"
        definition.write_text(
            self._definition_row("missing/request.xml"), encoding="utf-8"
        )
        first_sha = self._commit(root, "first")
        mapping = self._mapping(Path(directory.name))
        sidecar = Path(directory.name) / "sidecar.db"
        build(
            repo_root=root,
            target_sha=first_sha,
            sidecar_db=sidecar,
            mapping_file=mapping,
            ia_main_sha=first_sha,
        )
        connection = sqlite3.connect(sidecar)
        connection.execute(
            "UPDATE gateway_sidecar_builds SET inclusion_policy_version='gateway-sidecar-v1'"
        )
        connection.commit()
        connection.close()
        definition.write_text(
            self._definition_row("missing/updated.xml"), encoding="utf-8"
        )
        second_sha = self._commit(root, "second")
        result = build(
            repo_root=root,
            target_sha=second_sha,
            sidecar_db=sidecar,
            mapping_file=mapping,
            ia_main_sha=first_sha,
            requested_mode="delta",
        )
        self.assertEqual("full", result["effective_mode"])
        self.assertEqual(1, query(sidecar)["diagnostics"]["delta_full_fallback"])

    def test_failed_delta_keeps_previous_build_query_visible(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name) / "gateway"
        root.mkdir()
        self._git(root, "init", "-b", "main")
        (root / "testdefinitions").mkdir()
        definition = root / "testdefinitions" / "definition.csv"
        definition.write_text(
            self._definition_row("missing/request.xml"), encoding="utf-8"
        )
        first_sha = self._commit(root, "first")
        mapping = self._mapping(Path(directory.name))
        sidecar = Path(directory.name) / "sidecar.db"
        first = build(
            repo_root=root,
            target_sha=first_sha,
            sidecar_db=sidecar,
            mapping_file=mapping,
            ia_main_sha=first_sha,
        )
        definition.write_text(
            self._definition_row("missing/updated.xml"), encoding="utf-8"
        )
        second_sha = self._commit(root, "second")
        with mock.patch.object(
            refresh_gateway_sidecar,
            "_parse_definition_csv",
            side_effect=RuntimeError("injected failure"),
        ), self.assertRaisesRegex(RuntimeError, "injected failure"):
            build(
                repo_root=root,
                target_sha=second_sha,
                sidecar_db=sidecar,
                mapping_file=mapping,
                ia_main_sha=first_sha,
                requested_mode="delta",
            )
        self.assertEqual(first["build_id"], query(sidecar)["provenance"]["id"])

    def test_cli_reads_verified_ia_main_revision_from_manifest(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        base = Path(directory.name)
        gateway = base / "gateway"
        main = base / "main"
        for root in (gateway, main):
            root.mkdir()
            self._git(root, "init", "-b", "main")
            (root / "tracked.txt").write_text("fixture\n", encoding="utf-8")
        (gateway / "testdefinitions").mkdir()
        (gateway / "testdefinitions" / "definition.csv").write_text(
            self._definition_row("missing/request.xml"), encoding="utf-8"
        )
        gateway_sha = self._commit(gateway, "gateway")
        main_sha = self._commit(main, "main")
        mapping = self._mapping(base)
        manifest = base / "workspace.yaml"
        manifest.write_text(
            yaml.safe_dump(
                {
                    "version": 1,
                    "repositories": [
                        {
                            "repo_key": "ia-main",
                            "local_root": str(main),
                            "tracked_branch": "main",
                            "enabled": True,
                            "builders": [],
                        },
                        {
                            "repo_key": "ia-gwdata-gl",
                            "local_root": str(gateway),
                            "tracked_branch": "main",
                            "enabled": False,
                            "profile": "xml_gateway_automation",
                            "storage": "sidecar",
                            "depends_on": ["ia-main"],
                            "builders": [],
                        },
                    ],
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        sidecar = base / "sidecar.db"
        completed = subprocess.run(
            [
                sys.executable,
                "scripts/refresh_gateway_sidecar.py",
                "--repo-root",
                str(gateway),
                "--target-sha",
                "HEAD",
                "--db",
                str(sidecar),
                "--mapping-file",
                str(mapping),
                "--manifest",
                str(manifest),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        result = ast.literal_eval(completed.stdout.strip())
        self.assertEqual(gateway_sha, result["target_sha"])
        revisions = json.loads(query(sidecar)["provenance"]["dependency_revisions_json"])
        self.assertEqual(
            {"ia-gwdata-gl": gateway_sha, "ia-main": main_sha}, revisions
        )


if __name__ == "__main__":
    unittest.main()
