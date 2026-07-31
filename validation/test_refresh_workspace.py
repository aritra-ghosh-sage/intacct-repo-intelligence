from __future__ import annotations

import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from catalog.repositories import RepositoryError
from scripts.refresh_workspace import RefreshError, refresh_repository
from validation.validate_catalog_integrity import CatalogIntegrityError

ROOT = Path(__file__).resolve().parents[1]


class WorkspaceRefreshTests(unittest.TestCase):
    def _git(self, root: Path, *args: str) -> None:
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)

    def _fixture(self) -> tuple[tempfile.TemporaryDirectory[str], Path, Path, Path]:
        directory = tempfile.TemporaryDirectory()
        root = Path(directory.name)
        checkout = root / "checkout"
        checkout.mkdir()
        self._git(checkout, "init", "-b", "main")
        (checkout / "source.py").write_text("class Source: pass\n", encoding="utf-8")
        self._git(checkout, "add", "source.py")
        self._git(
            checkout,
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "initial",
        )
        database = root / "catalog.db"
        conn = sqlite3.connect(database)
        conn.executescript((ROOT / "catalog/schema.sql").read_text())
        conn.close()
        manifest = root / "repos.yaml"
        manifest.write_text(
            "version: 1\nrepositories:\n"
            "  - repo_key: service\n"
            f"    local_root: {checkout}\n"
            "    tracked_branch: main\n"
            "    profile: generic\n"
            "    builders: []\n",
            encoding="utf-8",
        )
        return directory, checkout, database, manifest

    def test_generic_refresh_promotes_candidate_and_records_sha(self) -> None:
        directory, checkout, database, manifest = self._fixture()
        self.addCleanup(directory.cleanup)
        refresh_repository(database, manifest, "service")
        conn = sqlite3.connect(database)
        try:
            repo = conn.execute(
                "SELECT indexed_commit_sha,index_status FROM repos WHERE repo_key='service'"
            ).fetchone()
            self.assertEqual(repo[1], "active")
            self.assertEqual(
                repo[0],
                subprocess.check_output(
                    ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True
                ).strip(),
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM files").fetchone()[0], 1
            )
            self.assertEqual(
                conn.execute("SELECT status FROM repo_index_runs").fetchone()[0],
                "active",
            )
        finally:
            conn.close()
        self.assertTrue(database.with_name("catalog.db.previous").is_file())

    def test_dirty_checkout_does_not_promote_candidate(self) -> None:
        directory, checkout, database, manifest = self._fixture()
        self.addCleanup(directory.cleanup)
        # Establish an active revision first.  A later failed attempt must not
        # make the working catalog look failed or discard its indexed SHA.
        refresh_repository(database, manifest, "service")
        before = (
            sqlite3.connect(database)
            .execute("SELECT indexed_commit_sha FROM repos WHERE repo_key='service'")
            .fetchone()[0]
        )
        (checkout / "source.py").write_text("changed\n", encoding="utf-8")
        with self.assertRaises(RefreshError):
            refresh_repository(database, manifest, "service", mode="delta")
        conn = sqlite3.connect(database)
        try:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM files").fetchone()[0], 1
            )
            repo = conn.execute(
                """SELECT indexed_commit_sha,index_status,last_attempt_status,last_attempt_error
                   FROM repos WHERE repo_key='service'"""
            ).fetchone()
            self.assertEqual(repo[0], before)
            self.assertEqual(repo[1], "active")
            self.assertEqual(repo[2], "failed")
            self.assertIn("dirty", repo[3])
            self.assertEqual(
                conn.execute(
                    "SELECT status FROM repo_index_runs ORDER BY id DESC"
                ).fetchone()[0],
                "failed",
            )
        finally:
            conn.close()

    def test_catalog_integrity_failure_preserves_active_generation(self) -> None:
        directory, checkout, database, manifest = self._fixture()
        self.addCleanup(directory.cleanup)
        refresh_repository(database, manifest, "service", mode="full")
        previous_path = database.with_name("catalog.db.previous")
        previous_bytes = previous_path.read_bytes()
        conn = sqlite3.connect(database)
        before_build = conn.execute(
            "SELECT id,content_fingerprint FROM catalog_builds WHERE status='active'"
        ).fetchone()
        before_files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        conn.close()

        (checkout / "source.py").write_text(
            "class Source:\n    changed = True\n", encoding="utf-8"
        )
        self._git(checkout, "add", "source.py")
        self._git(
            checkout,
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "candidate change",
        )

        with (
            mock.patch(
                "scripts.refresh_workspace.validate_catalog_connection",
                side_effect=CatalogIntegrityError("logical_orphans"),
            ),
            self.assertRaisesRegex(CatalogIntegrityError, "logical_orphans"),
        ):
            refresh_repository(database, manifest, "service", mode="auto")

        conn = sqlite3.connect(database)
        try:
            after_build = conn.execute(
                "SELECT id,content_fingerprint FROM catalog_builds WHERE status='active'"
            ).fetchone()
            self.assertEqual(after_build, before_build)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM files").fetchone()[0], before_files
            )
        finally:
            conn.close()
        self.assertEqual(previous_path.read_bytes(), previous_bytes)

    def test_semantic_builder_dispatch_uses_real_runner(self) -> None:
        directory, checkout, database, manifest = self._fixture()
        self.addCleanup(directory.cleanup)
        conn = sqlite3.connect(database)
        conn.execute("CREATE TABLE active_sentinel(value TEXT NOT NULL)")
        conn.execute("INSERT INTO active_sentinel(value) VALUES ('preserved')")
        conn.commit()
        conn.close()

        with (
            mock.patch(
                "scripts.refresh_workspace.build_plan",
                return_value=["entity_semantics"],
            ),
            mock.patch(
                "scripts.build_entity_semantics.build",
                return_value={"occurrences": 0},
            ) as semantic_build,
        ):
            refresh_repository(database, manifest, "service")

        semantic_build.assert_called_once()
        self.assertTrue(
            Path(semantic_build.call_args.args[0]).name.startswith(
                "catalog.db.candidate."
            )
        )
        self.assertEqual(
            Path(semantic_build.call_args.args[1]).resolve(), checkout.resolve()
        )
        self.assertEqual(semantic_build.call_args.args[2], "service")
        self.assertTrue(semantic_build.call_args.kwargs["reset"])

        conn = sqlite3.connect(database)
        try:
            self.assertEqual(
                conn.execute("SELECT value FROM active_sentinel").fetchone()[0],
                "preserved",
            )
            stage = conn.execute(
                "SELECT builder_name,status,diagnostic_error "
                "FROM repo_index_stages ORDER BY id DESC LIMIT 1"
            ).fetchone()
            self.assertEqual(stage[0], "entity_semantics")
            self.assertEqual(stage[1], "succeeded")
            self.assertIsNone(stage[2])
        finally:
            conn.close()

    def test_refresh_honors_manifest_dependency_order(self) -> None:
        directory, _checkout, database, manifest = self._fixture()
        self.addCleanup(directory.cleanup)
        manifest.write_text(
            "version: 1\nrepositories:\n"
            "  - repo_key: base\n"
            "    local_root: /tmp/base\n"
            "    tracked_branch: main\n"
            "    profile: generic\n"
            "    depends_on: null\n"
            "    builders: []\n"
            "  - repo_key: shared\n"
            "    local_root: /tmp/shared\n"
            "    tracked_branch: main\n"
            "    profile: generic\n"
            "    depends_on:\n"
            "      - base\n"
            "    builders: []\n"
            "  - repo_key: sibling\n"
            "    local_root: /tmp/sibling\n"
            "    tracked_branch: main\n"
            "    profile: generic\n"
            "    depends_on:\n"
            "      - base\n"
            "    builders: []\n"
            "  - repo_key: service\n"
            "    local_root: /tmp/service\n"
            "    tracked_branch: main\n"
            "    profile: generic\n"
            "    depends_on:\n"
            "      - shared\n"
            "      - sibling\n"
            "    builders: []\n",
            encoding="utf-8",
        )

        refresh_calls: list[str] = []

        def record_refresh(
            active: Path, manifest_document: dict, repo_key: str
        ) -> None:
            refresh_calls.append(repo_key)

        with (
            mock.patch(
                "scripts.refresh_workspace._validate_refresh_preconditions"
            ) as validate_preconditions,
            mock.patch(
                "scripts.refresh_workspace._refresh_repository_once",
                side_effect=record_refresh,
            ),
        ):
            refresh_repository(database, manifest, "service")

        validate_preconditions.assert_called_once()
        self.assertEqual(
            validate_preconditions.call_args.args[1],
            ["base", "shared", "sibling", "service"],
        )
        self.assertEqual(refresh_calls, ["base", "shared", "sibling", "service"])

    def test_refresh_rejects_disabled_dependencies_before_refreshing(self) -> None:
        directory, _checkout, database, manifest = self._fixture()
        self.addCleanup(directory.cleanup)
        manifest.write_text(
            "version: 1\nrepositories:\n"
            "  - repo_key: base\n"
            "    local_root: /tmp/base\n"
            "    tracked_branch: main\n"
            "    enabled: false\n"
            "    profile: generic\n"
            "    depends_on: null\n"
            "    builders: []\n"
            "  - repo_key: service\n"
            "    local_root: /tmp/service\n"
            "    tracked_branch: main\n"
            "    profile: generic\n"
            "    depends_on:\n"
            "      - base\n"
            "    builders: []\n",
            encoding="utf-8",
        )

        with mock.patch(
            "scripts.refresh_workspace._refresh_repository_once"
        ) as refresh_once:
            with self.assertRaisesRegex(RefreshError, "disabled repository"):
                refresh_repository(database, manifest, "service")

        refresh_once.assert_not_called()

    def test_refresh_rejects_later_disabled_dependency_before_refreshing(self) -> None:
        directory, _checkout, database, manifest = self._fixture()
        self.addCleanup(directory.cleanup)
        manifest.write_text(
            "version: 1\nrepositories:\n"
            "  - repo_key: base\n"
            "    local_root: /tmp/base\n"
            "    tracked_branch: main\n"
            "  - repo_key: blocked\n"
            "    local_root: /tmp/blocked\n"
            "    tracked_branch: main\n"
            "    enabled: false\n"
            "  - repo_key: middle\n"
            "    local_root: /tmp/middle\n"
            "    tracked_branch: main\n"
            "    depends_on: [blocked]\n"
            "  - repo_key: service\n"
            "    local_root: /tmp/service\n"
            "    tracked_branch: main\n"
            "    depends_on: [base, middle]\n",
            encoding="utf-8",
        )

        with mock.patch(
            "scripts.refresh_workspace._refresh_repository_once"
        ) as refresh_once:
            with self.assertRaisesRegex(RefreshError, "disabled repository: blocked"):
                refresh_repository(database, manifest, "service")

        refresh_once.assert_not_called()

    def test_refresh_rejects_disabled_target_and_records_preflight(self) -> None:
        directory, _checkout, database, manifest = self._fixture()
        self.addCleanup(directory.cleanup)
        manifest.write_text(
            "version: 1\nrepositories:\n"
            "  - repo_key: base\n"
            "    local_root: /tmp/base\n"
            "    tracked_branch: main\n"
            "  - repo_key: service\n"
            "    local_root: /tmp/service\n"
            "    tracked_branch: main\n"
            "    enabled: false\n"
            "    depends_on: [base]\n",
            encoding="utf-8",
        )

        with mock.patch(
            "scripts.refresh_workspace._refresh_repository_once"
        ) as refresh_once:
            with self.assertRaisesRegex(
                RefreshError, "repository is disabled: service"
            ):
                refresh_repository(database, manifest, "service")

        refresh_once.assert_not_called()
        conn = sqlite3.connect(database)
        try:
            runs = conn.execute(
                "SELECT status FROM repo_index_runs ORDER BY id"
            ).fetchall()
            self.assertEqual(runs, [("failed",)])
            stage = conn.execute(
                "SELECT builder_name,status FROM repo_index_stages"
            ).fetchone()
            self.assertEqual(stage, ("dependency_preflight", "failed"))
        finally:
            conn.close()

    def test_invalid_manifest_records_load_failure_without_refreshing(self) -> None:
        directory, checkout, database, manifest = self._fixture()
        self.addCleanup(directory.cleanup)
        conn = sqlite3.connect(database)
        try:
            conn.execute(
                """INSERT INTO repos(repo_key,local_root,tracked_branch)
                   VALUES ('service', ?, 'main')""",
                (str(checkout),),
            )
            conn.commit()
        finally:
            conn.close()
        manifest.write_text(
            "version: 1\nrepositories:\n"
            "  - repo_key: service\n"
            f"    local_root: {checkout}\n"
            "    tracked_branch: main\n"
            "    depends_on: [missing]\n",
            encoding="utf-8",
        )

        with mock.patch(
            "scripts.refresh_workspace._refresh_repository_once"
        ) as refresh_once:
            with self.assertRaisesRegex(RepositoryError, "unknown repository"):
                refresh_repository(database, manifest, "service")

        refresh_once.assert_not_called()
        conn = sqlite3.connect(database)
        try:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM repo_index_runs").fetchone()[0],
                1,
            )
            stage = conn.execute(
                "SELECT builder_name,status FROM repo_index_stages"
            ).fetchone()
            self.assertEqual(stage, ("load_workspace_manifest", "failed"))
        finally:
            conn.close()

    def test_missing_later_checkout_fails_before_any_refresh(self) -> None:
        directory, checkout, database, manifest = self._fixture()
        self.addCleanup(directory.cleanup)
        missing = Path(directory.name) / "missing"
        manifest.write_text(
            "version: 1\nrepositories:\n"
            "  - repo_key: base\n"
            f"    local_root: {checkout}\n"
            "    tracked_branch: main\n"
            "  - repo_key: service\n"
            f"    local_root: {missing}\n"
            "    tracked_branch: main\n"
            "    depends_on: [base]\n",
            encoding="utf-8",
        )

        with mock.patch(
            "scripts.refresh_workspace._refresh_repository_once"
        ) as refresh_once:
            with self.assertRaisesRegex(RefreshError, "checkout root does not exist"):
                refresh_repository(database, manifest, "service")

        refresh_once.assert_not_called()

    def test_invalid_later_branch_fails_before_any_refresh(self) -> None:
        directory, checkout, database, manifest = self._fixture()
        self.addCleanup(directory.cleanup)
        manifest.write_text(
            "version: 1\nrepositories:\n"
            "  - repo_key: base\n"
            f"    local_root: {checkout}\n"
            "    tracked_branch: main\n"
            "  - repo_key: service\n"
            f"    local_root: {checkout}\n"
            "    tracked_branch: missing-branch\n"
            "    depends_on: [base]\n",
            encoding="utf-8",
        )

        with mock.patch(
            "scripts.refresh_workspace._refresh_repository_once"
        ) as refresh_once:
            with self.assertRaisesRegex(RefreshError, "missing-branch"):
                refresh_repository(database, manifest, "service")

        refresh_once.assert_not_called()

    def test_missing_rest_evidence_fails_before_any_refresh(self) -> None:
        directory, checkout, database, manifest = self._fixture()
        self.addCleanup(directory.cleanup)
        manifest.write_text(
            "version: 1\nrepositories:\n"
            "  - repo_key: service\n"
            f"    local_root: {checkout}\n"
            "    tracked_branch: main\n"
            "    profile: rest_automation\n"
            "    rest_automation:\n"
            "      features_root: missing-features\n"
            "      object_mapping: missing-mapping.json\n",
            encoding="utf-8",
        )

        with mock.patch(
            "scripts.refresh_workspace._refresh_repository_once"
        ) as refresh_once:
            with self.assertRaisesRegex(RepositoryError, "does not exist"):
                refresh_repository(database, manifest, "service")

        refresh_once.assert_not_called()

    def test_dirty_checkout_records_dependency_preflight_failure_stage(self) -> None:
        directory, checkout, database, manifest = self._fixture()
        self.addCleanup(directory.cleanup)
        refresh_repository(database, manifest, "service")
        (checkout / "source.py").write_text("changed\n", encoding="utf-8")

        with self.assertRaises(RefreshError):
            refresh_repository(database, manifest, "service", mode="delta")

        conn = sqlite3.connect(database)
        try:
            stage = conn.execute(
                "SELECT builder_name,status,diagnostic_error "
                "FROM repo_index_stages ORDER BY id DESC LIMIT 1"
            ).fetchone()
            self.assertEqual(stage[0], "dependency_preflight")
            self.assertEqual(stage[1], "failed")
            self.assertIn("dirty", stage[2])
            build = conn.execute(
                "SELECT requested_mode,effective_mode,status FROM catalog_builds "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
            self.assertEqual(build, ("delta", "not_started", "failed"))
        finally:
            conn.close()

    def test_rest_automation_refresh_builds_candidate_coverage(self) -> None:
        directory, checkout, database, manifest = self._fixture()
        self.addCleanup(directory.cleanup)
        features = checkout / "features"
        features.mkdir()
        (features / "account.feature").write_text(
            """@version:v1
Feature: Account
  Scenario: Create account
    When "POST" to "account" with key "" and file ""
""",
            encoding="utf-8",
        )
        (checkout / "object-mapping.json").write_text(
            '{"accounts": {"account": "accounts-payable/account"}}',
            encoding="utf-8",
        )
        self._git(checkout, "add", "features/account.feature", "object-mapping.json")
        self._git(
            checkout,
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "add coverage",
        )
        manifest.write_text(
            "version: 1\nrepositories:\n"
            "  - repo_key: service\n"
            f"    local_root: {checkout}\n"
            "    tracked_branch: main\n"
            "    profile: rest_automation\n"
            "    rest_automation:\n"
            "      features_root: features\n"
            "      object_mapping: object-mapping.json\n",
            encoding="utf-8",
        )
        conn = sqlite3.connect(database)
        try:
            production_repo_id = conn.execute(
                "INSERT INTO repos(repo_key,local_root,tracked_branch) VALUES ('ia-main','/tmp/main','main')"
            ).lastrowid
            endpoint_file_id = conn.execute(
                "INSERT INTO files(repo_id,path,language) VALUES (?, 'openapi/account.yaml', 'yaml')",
                (production_repo_id,),
            ).lastrowid
            entity_id = conn.execute(
                "INSERT INTO entity_nodes(name) VALUES ('Account')"
            ).lastrowid
            conn.execute(
                """INSERT INTO rest_endpoints(repo_id,method,path,source_version,entity_id,file_id)
                   VALUES (?, 'POST', '/objects/accounts-payable/account', 'v1', ?, ?)""",
                (production_repo_id, entity_id, endpoint_file_id),
            )
            conn.commit()
        finally:
            conn.close()

        refresh_repository(database, manifest, "service")
        conn = sqlite3.connect(database)
        try:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM test_cases").fetchone()[0], 1
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM test_endpoint_links").fetchone()[0],
                1,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM test_entity_links").fetchone()[0], 1
            )
            self.assertEqual(
                conn.execute(
                    "SELECT status FROM repo_index_stages WHERE builder_name='gherkin_coverage'"
                ).fetchone()[0],
                "succeeded",
            )
        finally:
            conn.close()

    def test_compatibility_refresh_script_uses_workspace_runner(self) -> None:
        directory, _checkout, database, manifest = self._fixture()
        self.addCleanup(directory.cleanup)
        database.unlink()
        result = subprocess.run(
            [
                "bash",
                str(ROOT / "scripts" / "refresh.sh"),
                "--db",
                str(database),
                "--manifest",
                str(manifest),
                "--repo",
                "service",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            env={
                **os.environ,
                "PYTHON_BIN": "/Users/aritra.ghosh/projects/intacct-repo-intelligence/.venv/bin/python",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        conn = sqlite3.connect(database)
        try:
            self.assertEqual(
                conn.execute(
                    "SELECT index_status FROM repos WHERE repo_key='service'"
                ).fetchone()[0],
                "active",
            )
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
