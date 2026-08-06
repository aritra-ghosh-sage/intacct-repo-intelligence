"""Focused regression tests for repository registry and 019 migration."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from catalog.delta import DELTA_CONTRACT_VERSION
from catalog.migrations import apply_multi_repo_migration
from catalog.repositories import (
    RepositoryError,
    get_repository,
    load_workspace_manifest,
    register_manifest,
    resolve_repository_root,
)


class MultiRepoMigrationTests(unittest.TestCase):
    def _assert_manifest_error(self, text: str, pattern: str) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "repos.yaml"
            manifest.write_text(text, encoding="utf-8")
            with self.assertRaisesRegex(RepositoryError, pattern):
                load_workspace_manifest(manifest)

    def test_manifest_rejects_unknown_fields(self) -> None:
        cases = (
            (
                "version: 1\nrepository: []\nrepositories: []\n",
                "unknown field: repository",
            ),
            (
                "version: 1\nrepositories:\n"
                "  - repo_key: service\n"
                "    local_root: /tmp/service\n"
                "    tracked_branch: main\n"
                "    depend_on: null\n",
                "unknown field: depend_on",
            ),
            (
                "version: 1\nrepositories:\n"
                "  - repo_key: suite\n"
                "    local_root: /tmp/suite\n"
                "    tracked_branch: main\n"
                "    profile: rest_automation\n"
                "    rest_automation:\n"
                "      features_root: features\n"
                "      object_mapping: mapping.json\n"
                "      feature_root: typo\n",
                "unknown field: feature_root",
            ),
        )
        for manifest, pattern in cases:
            with self.subTest(pattern=pattern):
                self._assert_manifest_error(manifest, pattern)

    def test_manifest_rejects_ineffective_ignore_path_syntax(self) -> None:
        cases = (
            (".", "safe relative paths|directory below the repository root"),
            ("./", "safe relative paths|directory below the repository root"),
            (r"app\resources", "Git POSIX separators"),
        )
        for ignore_path, pattern in cases:
            with self.subTest(ignore_path=ignore_path):
                self._assert_manifest_error(
                    "version: 1\nrepositories:\n"
                    "  - repo_key: ia-main\n"
                    "    local_root: /tmp/ia-main\n"
                    "    tracked_branch: main\n"
                    "    ignore_paths:\n"
                    f"      - {ignore_path}\n",
                    pattern,
                )

    def test_manifest_requires_exact_version_and_non_empty_repositories(self) -> None:
        cases = (
            ("repositories: []\n", "version must be the integer 1"),
            ("version: 1.0\nrepositories: []\n", "version must be the integer 1"),
            ("version: true\nrepositories: []\n", "version must be the integer 1"),
            ("version: 1\nrepositories: []\n", "non-empty repositories list"),
        )
        for manifest, pattern in cases:
            with self.subTest(manifest=manifest):
                self._assert_manifest_error(manifest, pattern)

    def test_manifest_rejects_missing_or_invalid_required_fields(self) -> None:
        entries = (
            (
                "    local_root: /tmp/service\n    tracked_branch: main\n",
                "missing required field: repo_key",
            ),
            (
                "    repo_key: service\n    tracked_branch: main\n",
                "missing required field: local_root",
            ),
            (
                "    repo_key: service\n    local_root: /tmp/service\n",
                "missing required field: tracked_branch",
            ),
            (
                "    repo_key: 7\n"
                "    local_root: /tmp/service\n"
                "    tracked_branch: main\n",
                "repo_key must be a non-empty string",
            ),
            (
                "    repo_key: service\n"
                "    local_root: '   '\n"
                "    tracked_branch: main\n",
                "local_root must be a non-empty string",
            ),
        )
        for entry, pattern in entries:
            with self.subTest(pattern=pattern):
                self._assert_manifest_error(
                    f"version: 1\nrepositories:\n  -\n{entry}",
                    pattern,
                )

    def test_manifest_rejects_invalid_optional_field_types(self) -> None:
        cases = (
            ("    enabled: 'false'\n", "enabled must be a boolean"),
            ("    name: 42\n", "name must be null or a non-empty string"),
            ("    profile: 42\n", "profile must be null or a non-empty string"),
            ("    builders: scan\n", "builders must be a list"),
            (
                "    builders: [scan, scan]\n",
                "builders contains duplicate builder: scan",
            ),
        )
        base = (
            "version: 1\nrepositories:\n"
            "  - repo_key: service\n"
            "    local_root: /tmp/service\n"
            "    tracked_branch: main\n"
        )
        for field, pattern in cases:
            with self.subTest(pattern=pattern):
                self._assert_manifest_error(base + field, pattern)

    def test_manifest_rejects_invalid_profile_builder_selections(self) -> None:
        cases = (
            ("made_up", "    builders: []\n", "unknown repository profile"),
            ("generic", "    builders: [security]\n", "not supported by profile"),
            ("generic", "    builders: [made_up]\n", "unknown builder"),
        )
        for profile, builders, pattern in cases:
            with self.subTest(profile=profile, pattern=pattern):
                self._assert_manifest_error(
                    "version: 1\nrepositories:\n"
                    "  - repo_key: service\n"
                    "    local_root: /tmp/service\n"
                    "    tracked_branch: main\n"
                    f"    profile: {profile}\n"
                    f"{builders}",
                    pattern,
                )

    def test_manifest_rejects_invalid_rest_automation_contract(self) -> None:
        cases = (
            (
                "    profile: rest_automation\n",
                "requires a rest_automation mapping",
            ),
            (
                "    profile: rest_automation\n"
                "    rest_automation:\n"
                "      features_root: features\n",
                "requires rest_automation.object_mapping",
            ),
            (
                "    profile: rest_automation\n"
                "    rest_automation:\n"
                "      features_root: ../features\n"
                "      object_mapping: mapping.json\n",
                "must stay inside local_root",
            ),
            (
                "    profile: generic\n"
                "    rest_automation:\n"
                "      features_root: features\n"
                "      object_mapping: mapping.json\n",
                "only valid for profile rest_automation",
            ),
        )
        base = (
            "version: 1\nrepositories:\n"
            "  - repo_key: suite\n"
            "    local_root: /tmp/suite\n"
            "    tracked_branch: main\n"
        )
        for fields, pattern in cases:
            with self.subTest(pattern=pattern):
                self._assert_manifest_error(base + fields, pattern)

    def test_manifest_accepts_null_dependencies_and_valid_chains(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "repos.yaml"
            manifest.write_text(
                "version: 1\nrepositories:\n"
                "  - repo_key: base\n"
                "    local_root: /tmp/base\n"
                "    tracked_branch: main\n"
                "    depends_on: null\n"
                "  - repo_key: dependent\n"
                "    local_root: /tmp/dependent\n"
                "    tracked_branch: main\n"
                "    depends_on:\n"
                "      - base\n",
                encoding="utf-8",
            )
            document = load_workspace_manifest(manifest)
            self.assertIsNone(document["repositories"][0]["depends_on"])
            self.assertEqual(document["repositories"][1]["depends_on"], ["base"])

    def test_manifest_rejects_missing_dependency_reference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "repos.yaml"
            manifest.write_text(
                "version: 1\nrepositories:\n"
                "  - repo_key: dependent\n"
                "    local_root: /tmp/dependent\n"
                "    tracked_branch: main\n"
                "    depends_on:\n"
                "      - missing\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RepositoryError, "unknown repository"):
                load_workspace_manifest(manifest)

    def test_manifest_rejects_self_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "repos.yaml"
            manifest.write_text(
                "version: 1\nrepositories:\n"
                "  - repo_key: dependent\n"
                "    local_root: /tmp/dependent\n"
                "    tracked_branch: main\n"
                "    depends_on:\n"
                "      - dependent\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RepositoryError, "cannot depend on itself"):
                load_workspace_manifest(manifest)

    def test_manifest_rejects_duplicate_dependencies(self) -> None:
        self._assert_manifest_error(
            "version: 1\nrepositories:\n"
            "  - repo_key: base\n"
            "    local_root: /tmp/base\n"
            "    tracked_branch: main\n"
            "  - repo_key: dependent\n"
            "    local_root: /tmp/dependent\n"
            "    tracked_branch: main\n"
            "    depends_on: [base, base]\n",
            "duplicate repository key: base",
        )

    def test_manifest_rejects_dependency_cycles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "repos.yaml"
            manifest.write_text(
                "version: 1\nrepositories:\n"
                "  - repo_key: a\n"
                "    local_root: /tmp/a\n"
                "    tracked_branch: main\n"
                "    depends_on:\n"
                "      - b\n"
                "  - repo_key: b\n"
                "    local_root: /tmp/b\n"
                "    tracked_branch: main\n"
                "    depends_on:\n"
                "      - a\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RepositoryError, "cyclic dependency"):
                load_workspace_manifest(manifest)

    def legacy_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(
            """
            CREATE TABLE files (id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT UNIQUE NOT NULL, language TEXT);
            CREATE TABLE symbols (id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL,
                FOREIGN KEY(file_id) REFERENCES files(id));
            CREATE TABLE relationships (id INTEGER PRIMARY KEY, file_id INTEGER);
            CREATE TABLE repos (id INTEGER PRIMARY KEY, name TEXT, kind TEXT, language TEXT);
            INSERT INTO files(id, path, language) VALUES (7, 'app/source/Foo.cls', 'php');
            INSERT INTO symbols(id, file_id) VALUES (11, 7);
            """
        )
        conn.commit()
        return conn

    def test_migration_preserves_file_ids_and_allows_colliding_paths(self) -> None:
        conn = self.legacy_connection()
        apply_multi_repo_migration(conn, local_root="/tmp/main")
        main = get_repository(conn, "ia-main")
        self.assertEqual(
            tuple(conn.execute("SELECT id, repo_id FROM files").fetchone()),
            (7, main["id"]),
        )
        second_id = conn.execute(
            "INSERT INTO repos(repo_key, local_root, tracked_branch) VALUES ('service', '/tmp/service', 'main')"
        ).lastrowid
        conn.execute(
            "INSERT INTO files(repo_id, path) VALUES (?, ?)",
            (second_id, "app/source/Foo.cls"),
        )
        self.assertEqual(
            conn.execute("SELECT file_id FROM symbols WHERE id = 11").fetchone()[0], 7
        )
        self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_migration_is_idempotent(self) -> None:
        conn = self.legacy_connection()
        apply_multi_repo_migration(conn, local_root="/tmp/main")
        apply_multi_repo_migration(conn, local_root="/tmp/main")
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE name = '019_multi_repo'"
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE name = '020_rest_automation_coverage'"
            ).fetchone()[0],
            1,
        )
        self.assertIsNotNone(
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='test_cases'"
            ).fetchone()
        )
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE name = '021_entity_semantics'"
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM schema_migrations "
                "WHERE name = '022_entity_semantics_repo_scope'"
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE name='023_delta_refresh'"
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE name='024_refresh_contracts'"
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM schema_migrations "
                "WHERE name='031_rest_automation_contract'"
            ).fetchone()[0],
            1,
        )
        request_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(test_requests)")
        }
        state_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(test_coverage_build_state)")
        }
        self.assertTrue(
            {"coverage_scope", "mapping_provenance_json"}.issubset(request_columns)
        )
        self.assertTrue(
            {"coverage_contract_version", "contract_input_hashes_json"}.issubset(
                state_columns
            )
        )
        conn.execute(
            """INSERT INTO catalog_builds(
                   build_token,catalog_path,requested_mode,effective_mode,status,
                   source_revisions_json,delta_contract_version,diagnostic_error
               ) VALUES ('failed-plan',':memory:','delta','not_started','failed','{}',1,'preflight')"""
        )
        self.assertEqual(
            conn.execute(
                "SELECT effective_mode FROM catalog_builds WHERE build_token='failed-plan'"
            ).fetchone()[0],
            "not_started",
        )
        baseline = conn.execute(
            "SELECT status,length(content_fingerprint),delta_contract_version,"
            "manifest_hash,builder_plan_hash FROM catalog_builds WHERE status='active'"
        ).fetchone()
        self.assertEqual(
            tuple(baseline),
            ("active", 64, DELTA_CONTRACT_VERSION, None, None),
        )
        stable = conn.execute("SELECT stable_key FROM symbols WHERE id=11").fetchone()[
            0
        ]
        self.assertEqual(len(stable), 64)
        self.assertIsNotNone(
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='entity_relationship_facts'"
            ).fetchone()
        )
        relationship_fks = conn.execute(
            "PRAGMA foreign_key_list(entity_relationship_facts)"
        ).fetchall()
        composite_groups: dict[int, set[str]] = {}
        for row in relationship_fks:
            composite_groups.setdefault(int(row[0]), set()).add(str(row[3]))
        self.assertIn({"source_occurrence_id", "repo_id"}, composite_groups.values())
        self.assertIn({"target_occurrence_id", "repo_id"}, composite_groups.values())

    def test_delta_migration_preserves_graph_build_ids_and_installs_constraints(
        self,
    ) -> None:
        conn = self.legacy_connection()
        conn.executescript(
            """CREATE TABLE graph_builds(
                   id INTEGER PRIMARY KEY AUTOINCREMENT,graph_path TEXT NOT NULL,
                   source_db TEXT NOT NULL,status TEXT NOT NULL,
                   source_fingerprint TEXT NOT NULL,
                   started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                   completed_at TEXT,validation_summary TEXT,error TEXT);
               INSERT INTO graph_builds(id,graph_path,source_db,status,source_fingerprint)
               VALUES (17,'graph.lbug','catalog.db','active','legacy');"""
        )
        conn.commit()
        apply_multi_repo_migration(conn, local_root="/tmp/main")
        row = conn.execute(
            "SELECT id,catalog_build_id,projection_version FROM graph_builds"
        ).fetchone()
        self.assertEqual(tuple(row), (17, None, None))
        stage_columns = {
            item[1] for item in conn.execute("PRAGMA table_info(repo_index_stages)")
        }
        self.assertTrue(
            {"execution_mode", "invalidation_reason", "affected_record_count"}.issubset(
                stage_columns
            )
        )
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                """INSERT INTO repo_changed_paths(change_set_id,change_type,old_path,new_path)
                   VALUES (999,'added','old.py','new.py')"""
            )
        conn.rollback()
        self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_refresh_contract_migration_preserves_generation_links(self) -> None:
        conn = self.legacy_connection()
        apply_multi_repo_migration(conn, local_root="/tmp/main")
        repo_id = conn.execute(
            "SELECT id FROM repos WHERE repo_key='ia-main'"
        ).fetchone()[0]
        build_id = conn.execute(
            "SELECT id FROM catalog_builds WHERE status='active'"
        ).fetchone()[0]
        run_id = conn.execute(
            """INSERT INTO repo_index_runs(
                   repo_id,tracked_branch,commit_sha,status
               ) VALUES (?,'main','base','active')""",
            (repo_id,),
        ).lastrowid
        change_id = conn.execute(
            """INSERT INTO repo_change_sets(
                   catalog_build_id,repo_index_run_id,repo_id,base_commit_sha,
                   target_commit_sha,requested_mode,effective_mode,status
               ) VALUES (?,?,?,'base','base','auto','noop','succeeded')""",
            (build_id, run_id, repo_id),
        ).lastrowid
        graph_id = conn.execute(
            """INSERT INTO graph_builds(
                   graph_path,source_db,status,source_fingerprint,catalog_build_id,
                   build_mode
               ) VALUES ('graph.lbug','catalog.db','active','fingerprint',?,'full')""",
            (build_id,),
        ).lastrowid
        conn.execute("DELETE FROM schema_migrations WHERE name='024_refresh_contracts'")
        conn.commit()

        apply_multi_repo_migration(conn, local_root="/tmp/main")

        self.assertEqual(
            conn.execute(
                "SELECT catalog_build_id FROM repo_change_sets WHERE id=?", (change_id,)
            ).fetchone()[0],
            build_id,
        )
        self.assertEqual(
            conn.execute(
                "SELECT catalog_build_id FROM graph_builds WHERE id=?", (graph_id,)
            ).fetchone()[0],
            build_id,
        )
        self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_repo_scope_upgrade_preserves_semantic_record_ids(self) -> None:
        conn = self.legacy_connection()
        apply_multi_repo_migration(conn, local_root="/tmp/main")
        repo_id = conn.execute(
            "SELECT id FROM repos WHERE repo_key='ia-main'"
        ).fetchone()[0]
        conn.execute(
            "CREATE TABLE IF NOT EXISTS entity_nodes("
            "id INTEGER PRIMARY KEY,name TEXT NOT NULL UNIQUE)"
        )
        conn.execute("INSERT INTO entity_nodes(id,name) VALUES (1,'Customer')")
        occurrence_id = conn.execute(
            "INSERT INTO entity_occurrences(repo_id,entity_id,ent_file) VALUES (?,?,?)",
            (repo_id, 1, "customer.ent"),
        ).lastrowid
        conn.execute(
            """INSERT INTO entity_schema_components(
                   id,repo_id,occurrence_id,component_kind,component_path,
                   source_path,evidence_text,evidence_hash,extractor,
                   extractor_version,confidence
               ) VALUES (41,?,?,?,?,?,?,?,?,?,?)""",
            (
                repo_id,
                occurrence_id,
                "field",
                "PARENT",
                "customer.ent",
                "PARENT",
                "component-hash",
                "fixture",
                "1",
                1.0,
            ),
        )
        conn.execute(
            """INSERT INTO entity_relationship_facts(
                   id,repo_id,source_occurrence_id,source_component_id,axis,
                   relation_kind,fact_key,target_occurrence_id,assertion_status,
                   source_path,evidence_text,evidence_hash,extractor,
                   extractor_version,confidence
               ) VALUES (42,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                repo_id,
                occurrence_id,
                41,
                "B",
                "business_parent_reference",
                "customer.parent",
                occurrence_id,
                "VERIFIED",
                "customer.ent",
                "PARENT",
                "fact-hash",
                "fixture",
                "1",
                1.0,
            ),
        )
        conn.execute(
            "DELETE FROM schema_migrations WHERE name='022_entity_semantics_repo_scope'"
        )
        conn.execute("DROP INDEX uq_entity_schema_components_id_repo")
        conn.execute("DROP INDEX uq_entity_relationship_facts_id_repo")
        conn.execute("DROP INDEX uq_entity_occurrences_id_repo")
        conn.commit()

        apply_multi_repo_migration(conn, local_root="/tmp/main")
        self.assertEqual(
            conn.execute("SELECT id FROM entity_schema_components").fetchone()[0],
            41,
        )
        self.assertEqual(
            tuple(
                conn.execute(
                    "SELECT id,source_component_id FROM entity_relationship_facts"
                ).fetchone()
            ),
            (42, 41),
        )
        self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_migration_makes_workflow_identity_repo_qualified(self) -> None:
        conn = self.legacy_connection()
        conn.executescript(
            """
            CREATE TABLE entity_nodes (id INTEGER PRIMARY KEY, name TEXT);
            INSERT INTO entity_nodes(id, name) VALUES (1, 'Invoice');
            CREATE TABLE workflows (
                id INTEGER PRIMARY KEY, entity_id INTEGER NOT NULL, name TEXT NOT NULL,
                workflow_type TEXT NOT NULL, source_kind TEXT NOT NULL, source_file TEXT,
                file_id INTEGER, source_symbol_id INTEGER, confidence REAL, reason TEXT,
                created_at TEXT,
                UNIQUE(entity_id, name, workflow_type, source_file)
            );
            INSERT INTO workflows(id, entity_id, name, workflow_type, source_kind, source_file)
                VALUES (31, 1, 'post', 'posting', 'yaml', 'app/source/workflows.yml');
            """
        )
        conn.commit()
        apply_multi_repo_migration(conn, local_root="/tmp/main")
        second_id = conn.execute(
            "INSERT INTO repos(repo_key, local_root, tracked_branch) VALUES ('service', '/tmp/service', 'main')"
        ).lastrowid
        conn.execute(
            """INSERT INTO workflows(repo_id, entity_id, name, workflow_type, source_kind, source_file)
               VALUES (?, 1, 'post', 'posting', 'yaml', 'app/source/workflows.yml')""",
            (second_id,),
        )
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM workflows").fetchone()[0], 2
        )

    def test_migration_rejects_populated_unsafe_legacy_unique_table(self) -> None:
        conn = self.legacy_connection()
        conn.execute("INSERT INTO relationships(id, file_id) VALUES (1, 7)")
        conn.commit()
        with self.assertRaisesRegex(RuntimeError, "relationships"):
            apply_multi_repo_migration(conn, local_root="/tmp/main")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM files").fetchone()[0], 1)

    def test_empty_legacy_families_receive_repo_qualified_constraints(self) -> None:
        conn = self.legacy_connection()
        conn.executescript(
            """
            CREATE TABLE security_operations (
                id INTEGER PRIMARY KEY, op_key TEXT NOT NULL UNIQUE,
                source_file TEXT NOT NULL, source_kind TEXT NOT NULL
            );
            CREATE TABLE openapispec_index (
                id INTEGER PRIMARY KEY, file_path TEXT NOT NULL UNIQUE
            );
            CREATE TABLE entity_nodes (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL);
            CREATE TABLE entity_access_links (
                id INTEGER PRIMARY KEY, entity_id INTEGER NOT NULL, surface TEXT NOT NULL,
                record_id INTEGER NOT NULL, link_type TEXT NOT NULL,
                UNIQUE(entity_id, surface, record_id, link_type)
            );
            """
        )
        conn.commit()
        apply_multi_repo_migration(conn, local_root="/tmp/main")
        second_id = conn.execute(
            "INSERT INTO repos(repo_key, local_root, tracked_branch) VALUES ('service', '/tmp/service', 'main')"
        ).lastrowid
        main_id = get_repository(conn, "ia-main")["id"]
        for repo_id in (main_id, second_id):
            conn.execute(
                """INSERT INTO security_operations(repo_id, op_key, op_numeric_id, source_file, source_kind)
                   VALUES (?, 'same.op', 1, 'app/security.xml', 'xml')""",
                (repo_id,),
            )
            conn.execute(
                """INSERT INTO openapispec_index(repo_id, file_path)
                   VALUES (?, 'openapi/shared.yaml')""",
                (repo_id,),
            )
        entity_id = conn.execute(
            "INSERT INTO entity_nodes(name) VALUES ('Invoice')"
        ).lastrowid
        for repo_id in (main_id, second_id):
            conn.execute(
                """INSERT INTO entity_access_links(repo_id, entity_id, surface, record_id, link_type)
                   VALUES (?, ?, 'security_operation', 1, 'exact')""",
                (repo_id, entity_id),
            )
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM security_operations").fetchone()[0], 2
        )
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM openapispec_index").fetchone()[0], 2
        )
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM entity_access_links").fetchone()[0], 2
        )

    def test_entity_access_links_are_rebuilt_with_repo_scope(self) -> None:
        conn = self.legacy_connection()
        conn.executescript(
            """
            CREATE TABLE entity_nodes (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE entity_access_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id INTEGER NOT NULL,
                surface TEXT NOT NULL,
                record_id INTEGER NOT NULL,
                link_type TEXT NOT NULL,
                evidence_file_id INTEGER,
                evidence_symbol_id INTEGER,
                confidence_mode TEXT NOT NULL DEFAULT 'deterministic_exact',
                notes TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(entity_id, surface, record_id, link_type, evidence_file_id, evidence_symbol_id)
            );
            INSERT INTO entity_nodes(id, name) VALUES (1, 'Invoice');
            INSERT INTO entity_access_links(entity_id, surface, record_id, link_type, evidence_file_id)
                VALUES (1, 'rest_endpoint', 22, 'file_id_overlap', 7);
            """
        )
        conn.commit()
        apply_multi_repo_migration(conn, local_root="/tmp/main")
        main_id = get_repository(conn, "ia-main")["id"]
        second_id = conn.execute(
            "INSERT INTO repos(repo_key, local_root, tracked_branch) VALUES ('service', '/tmp/service', 'main')"
        ).lastrowid
        conn.execute(
            """INSERT INTO entity_access_links(repo_id, entity_id, surface, record_id, link_type, evidence_file_id)
               VALUES (?, 1, 'rest_endpoint', 22, 'file_id_overlap', 7)""",
            (second_id,),
        )
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM entity_access_links WHERE entity_id = 1 AND record_id = 22"
            ).fetchone()[0],
            2,
        )
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM entity_access_links WHERE repo_id = ?",
                (main_id,),
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM entity_access_links WHERE repo_id = ?",
                (second_id,),
            ).fetchone()[0],
            1,
        )

    def test_schema_contract_moves_repo_local_entity_metadata_to_occurrences(
        self,
    ) -> None:
        conn = self.legacy_connection()
        conn.executescript(
            """
            CREATE TABLE entity_nodes (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE entity_access_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id INTEGER NOT NULL,
                surface TEXT NOT NULL,
                record_id INTEGER NOT NULL,
                link_type TEXT NOT NULL,
                evidence_file_id INTEGER,
                evidence_symbol_id INTEGER,
                confidence_mode TEXT NOT NULL DEFAULT 'deterministic_exact',
                notes TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(entity_id, surface, record_id, link_type, evidence_file_id, evidence_symbol_id)
            );
            INSERT INTO entity_nodes(id, name) VALUES (1, 'Invoice');
            INSERT INTO entity_access_links(entity_id, surface, record_id, link_type, evidence_file_id)
                VALUES (1, 'rest_endpoint', 22, 'file_id_overlap', 7);
            """
        )
        apply_multi_repo_migration(conn, local_root="/tmp/main")

        entity_node_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(entity_nodes)").fetchall()
        }
        self.assertTrue(
            {"ent_file", "module", "table_name", "view_name", "dummy"}.isdisjoint(
                entity_node_columns
            )
        )

        occurrence_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(entity_occurrences)").fetchall()
        }
        self.assertTrue(
            {
                "repo_id",
                "entity_id",
                "ent_file",
                "module",
                "table_name",
                "view_name",
                "dummy",
                "source_file_id",
                "extractor",
            }.issubset(occurrence_columns)
        )

        index_rows = conn.execute("PRAGMA index_list(entity_access_links)").fetchall()
        unique_index_names = [row["name"] for row in index_rows if row["unique"]]
        unique_column_sets = [
            [
                info["name"]
                for info in conn.execute(
                    f"PRAGMA index_info('{index_name}')"
                ).fetchall()
            ]
            for index_name in unique_index_names
        ]
        self.assertIn(
            [
                "repo_id",
                "entity_id",
                "surface",
                "record_id",
                "link_type",
                "evidence_file_id",
                "evidence_symbol_id",
            ],
            unique_column_sets,
        )

    def test_manifest_registration_and_root_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "checkout"
            root.mkdir()
            manifest_path = Path(directory) / "repos.yaml"
            manifest_path.write_text(
                "version: 1\nrepositories:\n  - repo_key: service\n    local_root: "
                f"{root}\n    tracked_branch: main\n    builders: [scan, symbols]\n"
            )
            manifest = load_workspace_manifest(manifest_path)
            conn = sqlite3.connect(":memory:")
            conn.row_factory = sqlite3.Row
            conn.executescript(
                """CREATE TABLE repos (id INTEGER PRIMARY KEY, repo_key TEXT UNIQUE,
                name TEXT, kind TEXT, language TEXT, remote_url TEXT, local_root TEXT,
                tracked_branch TEXT, enabled INTEGER, profile TEXT, effective_builders_json TEXT)"""
            )
            register_manifest(conn, manifest)
            self.assertEqual(resolve_repository_root(conn, "service"), root.resolve())
            with self.assertRaises(RepositoryError):
                get_repository(conn, "missing")

    def test_rest_automation_manifest_requires_relative_evidence_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = Path(directory) / "repos.yaml"
            manifest_path.write_text(
                "version: 1\nrepositories:\n  - repo_key: suite\n"
                "    local_root: /tmp/suite\n    tracked_branch: main\n"
                "    profile: rest_automation\n"
                "    rest_automation:\n      features_root: /outside\n"
                "      object_mapping: object-mapping.json\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RepositoryError, "relative path"):
                load_workspace_manifest(manifest_path)


if __name__ == "__main__":
    unittest.main()
