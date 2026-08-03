from __future__ import annotations

import json
import sqlite3
import unittest

from validation.validate_ui_catalog import (
    UiCatalogValidationError,
    duplicate_natural_key_groups,
    validate_ui_catalog_connection,
)


class UiCatalogValidatorTests(unittest.TestCase):
    def _catalog(self) -> tuple[sqlite3.Connection, dict[str, int]]:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        with open("catalog/schema.sql", encoding="utf-8") as schema:
            conn.executescript(schema.read())
        conn.execute(
            "INSERT INTO repos(id,repo_key,local_root,tracked_branch) "
            "VALUES (1,'main','/tmp/main','main'),(2,'other','/tmp/other','main')"
        )
        file_ids: dict[str, int] = {}
        for name, repo_id, path, language in (
            ("form", 1, "app/source/gl/glbatch_form.xml", "xml"),
            ("script", 1, "app/resources/js/glbatch.js", "javascript"),
            (
                "nextgen",
                1,
                "app/source/openapispec/gl/objects.general-ledger.journal-entry.s1.uimeta.yaml",
                "yaml",
            ),
            ("other_form", 2, "app/source/gl/other_form.xml", "xml"),
        ):
            file_ids[name] = int(
                conn.execute(
                    "INSERT INTO files(repo_id,path,language) VALUES (?,?,?)",
                    (repo_id, path, language),
                ).lastrowid
            )

        surface_id = int(
            conn.execute(
                """INSERT INTO ui_surfaces(
                       repo_id,surface_key,surface_kind,source_file_id,source_path,
                       extractor,extractor_version
                   ) VALUES (1,'actionui:app/source/gl/glbatch_form.xml',
                             'actionui_form',?,'app/source/gl/glbatch_form.xml','test','1')""",
                (file_ids["form"],),
            ).lastrowid
        )
        artifact_id = int(
            conn.execute(
                """INSERT INTO ui_artifacts(
                       repo_id,surface_id,artifact_key,artifact_kind,file_id,source_path
                   ) VALUES (1,?,'form','actionui_form',?,'app/source/gl/glbatch_form.xml')""",
                (surface_id, file_ids["form"]),
            ).lastrowid
        )
        nextgen_surface_id = int(
            conn.execute(
                """INSERT INTO ui_surfaces(
                       repo_id,surface_key,surface_kind,source_file_id,source_path,
                       extractor,extractor_version
                   ) VALUES (1,'nextgen:general-ledger/journal-entry','nextgen',?,
                             'app/source/openapispec/gl/objects.general-ledger.journal-entry.s1.uimeta.yaml',
                             'test','1')""",
                (file_ids["nextgen"],),
            ).lastrowid
        )
        conn.execute(
            """INSERT INTO ui_artifacts(
                   repo_id,surface_id,artifact_key,artifact_kind,file_id,source_path
               ) VALUES (1,?,'uimeta','uimeta',?,
                         'app/source/openapispec/gl/objects.general-ledger.journal-entry.s1.uimeta.yaml')""",
            (nextgen_surface_id, file_ids["nextgen"]),
        )
        entity_id = int(
            conn.execute("INSERT INTO entity_nodes(name) VALUES ('GLBatch')").lastrowid
        )
        occurrence_id = int(
            conn.execute(
                "INSERT INTO entity_occurrences(repo_id,entity_id) VALUES (1,?)",
                (entity_id,),
            ).lastrowid
        )
        conn.execute(
            """INSERT INTO ui_entity_references(
                   repo_id,surface_id,entity_id,entity_occurrence_id,evidence_artifact_id,
                   reference_kind,confidence,evidence_text
               ) VALUES (1,?,?,?,?, 'direct',1.0,'fixture')""",
            (surface_id, entity_id, occurrence_id, artifact_id),
        )
        event_id = int(
            conn.execute(
                """INSERT INTO ui_events(
                       repo_id,artifact_id,event_key,event_type,handler_name,evidence_text
                   ) VALUES (1,?,'event:load','load','onLoadFunctionCalls','fixture')""",
                (artifact_id,),
            ).lastrowid
        )
        dependency_id = int(
            conn.execute(
                """INSERT INTO ui_script_dependencies(
                       repo_id,surface_id,source_artifact_id,dependency_key,script_path,
                       target_file_id,load_scope,resolution_status,evidence_text
                   ) VALUES (1,? ,?,'glbatch.js','app/resources/js/glbatch.js',?,
                             'active','resolved','fixture')""",
                (surface_id, artifact_id, file_ids["script"]),
            ).lastrowid
        )
        symbol_id = int(
            conn.execute(
                """INSERT INTO symbols(file_id,name,kind,stable_key)
                   VALUES (?,'onLoadFunctionCalls','function','function:onLoadFunctionCalls')""",
                (file_ids["script"],),
            ).lastrowid
        )
        conn.execute(
            """INSERT INTO ui_event_calls(
                   repo_id,event_id,dependency_id,call_key,handler_name,handler_symbol_id,
                   resolution_status,resolution_reason,evidence_text
               ) VALUES (1,?,?, 'call:onLoadFunctionCalls','onLoadFunctionCalls',?,
                         'resolved','unique_active_exact_callable','fixture')""",
            (event_id, dependency_id, symbol_id),
        )
        conn.commit()
        return conn, {
            "surface": surface_id,
            "nextgen_surface": nextgen_surface_id,
            "artifact": artifact_id,
            "event": event_id,
            "dependency": dependency_id,
            **file_ids,
        }

    def test_valid_fixture_passes_in_normal_and_strict_modes(self) -> None:
        conn, _ = self._catalog()
        self.addCleanup(conn.close)

        self.assertTrue(validate_ui_catalog_connection(conn)["ok"])
        self.assertTrue(
            validate_ui_catalog_connection(conn, strict_resolution=True)["ok"]
        )

    def test_invalid_surface_key_and_provenance_fail(self) -> None:
        conn, ids = self._catalog()
        self.addCleanup(conn.close)
        conn.execute(
            "UPDATE ui_surfaces SET surface_key='nextgen:glbatch', source_path='wrong.xml' "
            "WHERE id=?",
            (ids["surface"],),
        )
        conn.commit()

        with self.assertRaises(UiCatalogValidationError) as raised:
            validate_ui_catalog_connection(conn)
        summary = json.loads(str(raised.exception))
        self.assertIn("surface_keys", summary["failures"])

    def test_foreign_key_and_cross_surface_dependency_fail(self) -> None:
        conn, ids = self._catalog()
        self.addCleanup(conn.close)
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("DROP TRIGGER trg_ui_event_calls_surface_match_update")
        conn.execute("DROP TRIGGER trg_ui_event_calls_surface_match_insert")
        second_surface_id = int(
            conn.execute(
                """INSERT INTO ui_surfaces(
                       repo_id,surface_key,surface_kind,source_file_id,source_path,
                       extractor,extractor_version
                   ) VALUES (1,'actionui:app/source/gl/second_form.xml','actionui_form',
                             ?,'app/source/gl/second_form.xml','test','1')""",
                (ids["form"],),
            ).lastrowid
        )
        second_artifact_id = int(
            conn.execute(
                """INSERT INTO ui_artifacts(
                       repo_id,surface_id,artifact_key,artifact_kind,file_id,source_path
                   ) VALUES (1,?,'second','actionui_form',?,'app/source/gl/glbatch_form.xml')""",
                (second_surface_id, ids["form"]),
            ).lastrowid
        )
        conn.execute(
            "UPDATE ui_script_dependencies SET source_artifact_id=? WHERE id=?",
            (second_artifact_id, ids["dependency"]),
        )
        conn.execute("UPDATE ui_surfaces SET source_file_id=999 WHERE id=?", (ids["surface"],))
        conn.commit()

        with self.assertRaises(UiCatalogValidationError) as raised:
            validate_ui_catalog_connection(conn)
        summary = json.loads(str(raised.exception))
        self.assertIn("foreign_key_check", summary["failures"])
        self.assertIn("repo_ownership_or_provenance", summary["failures"])

    def test_error_issues_are_always_blocking_and_unresolved_is_strict_only(self) -> None:
        conn, ids = self._catalog()
        self.addCleanup(conn.close)
        conn.execute(
            """INSERT INTO ui_resolution_issues(
                   repo_id,surface_id,issue_key,severity,issue_code,message
               ) VALUES (1,?,'missing-parent','warning',
                         'actionui.loader.inheritance_unresolved','fixture')""",
            (ids["surface"],),
        )
        conn.commit()
        self.assertTrue(validate_ui_catalog_connection(conn)["ok"])
        with self.assertRaisesRegex(UiCatalogValidationError, "strict_resolution_issues"):
            validate_ui_catalog_connection(conn, strict_resolution=True)

        conn.execute(
            """INSERT INTO ui_resolution_issues(
                   repo_id,surface_id,issue_key,severity,issue_code,message
               ) VALUES (1,?,'invalid-yaml','error','nextgen.yaml.invalid','fixture')""",
            (ids["surface"],),
        )
        conn.commit()
        with self.assertRaisesRegex(UiCatalogValidationError, "blocking_resolution_issues"):
            validate_ui_catalog_connection(conn)

    def test_unattached_source_diagnostic_is_strict_only(self) -> None:
        conn, _ = self._catalog()
        self.addCleanup(conn.close)
        source_path = "app/source/openapispec/inv/uimeta/objects.aisle.s1.uimeta.yaml"
        source_file_id = int(
            conn.execute(
                "INSERT INTO files(repo_id,path,language) VALUES (1,?, 'yaml')",
                (source_path,),
            ).lastrowid
        )
        conn.execute(
            """INSERT INTO ui_source_diagnostics(
                   repo_id,source_file_id,source_path,source_kind,source_pointer,
                   diagnostic_key,severity,diagnostic_code,message,evidence_text
               ) VALUES (1,?,?, 'uimeta','lines:1-1','aisle-bare-uimeta','warning',
                         'nextgen.family.unresolved','fixture','objects.aisle.s1.uimeta.yaml')""",
            (source_file_id, source_path),
        )
        conn.commit()

        self.assertTrue(validate_ui_catalog_connection(conn)["ok"])
        with self.assertRaisesRegex(UiCatalogValidationError, "strict_source_diagnostics"):
            validate_ui_catalog_connection(conn, strict_resolution=True)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM ui_surfaces").fetchone()[0],
            2,
        )
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM ui_artifacts").fetchone()[0],
            2,
        )
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM ui_entity_references").fetchone()[0],
            1,
        )

    def test_duplicate_key_helper_detects_duplicate_groups(self) -> None:
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.execute("CREATE TABLE fixture_keys (repo_id INTEGER, natural_key TEXT)")
        conn.executemany(
            "INSERT INTO fixture_keys(repo_id,natural_key) VALUES (?,?)",
            ((1, "first"), (1, "first"), (1, "second")),
        )

        self.assertEqual(
            duplicate_natural_key_groups(conn, "fixture_keys", ("repo_id", "natural_key")),
            1,
        )


if __name__ == "__main__":
    unittest.main()
