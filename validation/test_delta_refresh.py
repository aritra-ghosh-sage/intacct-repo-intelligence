from __future__ import annotations

import json
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from catalog import delta as delta_module
from catalog.content_fingerprint import logical_content_fingerprint
from catalog.delta import (
    DELTA_CONTRACT_VERSION,
    ChangedPath,
    ChangeType,
    DeltaUnavailable,
    RepositoryChangeSet,
    collect_changed_paths,
    collect_repository_change_set,
)
from catalog.repositories import load_workspace_manifest
from scripts.builder_registry import build_plan, stage_execution_modes
from scripts.refresh_workspace import (
    RefreshError,
    _changed_input_paths,
    _plan_repository_changes,
    _refresh_repository_closure,
    _repository_manifest_hash,
    _repository_plan_hash,
    refresh_repository,
)
from scripts.scan_ent_files import ENTITY_INPUT_SUFFIXES
from validation.validate_catalog_integrity import CatalogIntegrityError

ROOT = Path(__file__).resolve().parents[1]


class DeltaRefreshTests(unittest.TestCase):
    def _git(self, root: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            text=True,
            capture_output=True,
            check=True,
        )
        return result.stdout.strip()

    def _commit(self, root: Path, message: str) -> str:
        self._git(root, "add", "-A")
        self._git(
            root,
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            message,
        )
        return self._git(root, "rev-parse", "HEAD")

    def _fixture(self):
        directory = tempfile.TemporaryDirectory()
        root = Path(directory.name)
        checkout = root / "checkout"
        checkout.mkdir()
        self._git(checkout, "init", "-b", "main")
        (checkout / "source.php").write_text("<?php\nclass Source {}\n")
        initial = self._commit(checkout, "initial")
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
            "    builders: []\n"
        )
        return directory, checkout, database, manifest, initial

    def _two_repo_fixture(self):
        directory = tempfile.TemporaryDirectory()
        root = Path(directory.name)
        repositories: dict[str, Path] = {}
        for repo_key in ("a", "b"):
            checkout = root / repo_key
            checkout.mkdir()
            self._git(checkout, "init", "-b", "main")
            (checkout / "source.php").write_text(
                f"<?php\nclass {repo_key.upper()}Source {{}}\n"
            )
            self._commit(checkout, f"initial {repo_key}")
            repositories[repo_key] = checkout
        database = root / "catalog.db"
        conn = sqlite3.connect(database)
        conn.executescript((ROOT / "catalog/schema.sql").read_text())
        conn.close()
        manifest = root / "repos.yaml"
        manifest.write_text(
            "version: 1\nrepositories:\n"
            + "".join(
                f"  - repo_key: {repo_key}\n"
                f"    local_root: {checkout}\n"
                "    tracked_branch: main\n"
                "    profile: generic\n"
                "    builders: []\n"
                for repo_key, checkout in repositories.items()
            )
        )
        return directory, repositories, database, manifest

    def test_collects_add_modify_delete_and_boundary_rename(self) -> None:
        directory, checkout, _database, _manifest, base = self._fixture()
        self.addCleanup(directory.cleanup)
        (checkout / "source.php").write_text(
            "<?php\nclass Source { public $value = 1; }\n"
        )
        (checkout / "added.py").write_text("VALUE = 1\n")
        target = self._commit(checkout, "modify and add")
        changes = collect_changed_paths(checkout, base, target)
        self.assertEqual(
            {(change.change_type, change.path) for change in changes},
            {(ChangeType.MODIFIED, "source.php"), (ChangeType.ADDED, "added.py")},
        )

        base = target
        self._git(checkout, "mv", "added.py", "ignored.txt")
        (checkout / "source.php").unlink()
        target = self._commit(checkout, "delete and boundary rename")
        changes = collect_changed_paths(checkout, base, target)
        self.assertEqual(
            {(change.change_type, change.path) for change in changes},
            {(ChangeType.DELETED, "added.py"), (ChangeType.DELETED, "source.php")},
        )

    def test_rename_invalidation_uses_old_and_new_paths(self) -> None:
        change = RepositoryChangeSet(
            repo_key="service",
            base_commit_sha="base",
            target_commit_sha="target",
            requested_mode="auto",
            effective_mode="delta",
            changed_paths=(
                ChangedPath(
                    ChangeType.RENAMED,
                    "app/source/openapispec/ap/object.yaml",
                    "app/source/misc/object.yaml",
                ),
            ),
        )
        paths = _changed_input_paths(change)
        self.assertEqual(
            paths,
            (
                "app/source/misc/object.yaml",
                "app/source/openapispec/ap/object.yaml",
            ),
        )
        modes = stage_execution_modes(
            build_plan("intacct_app", []),
            repository_mode="delta",
            changed_paths=paths,
        )
        self.assertEqual(modes["openapi_scan"][0], "full")
        self.assertEqual(modes["openapi_link"][0], "full")

    def test_refresh_planning_passes_both_rename_paths_to_invalidation(self) -> None:
        change = RepositoryChangeSet(
            repo_key="service",
            base_commit_sha="base",
            target_commit_sha="target",
            requested_mode="auto",
            effective_mode="delta",
            changed_paths=(
                ChangedPath(
                    ChangeType.RENAMED,
                    "app/source/openapispec/ap/object.yaml",
                    "app/source/misc/object.yaml",
                ),
            ),
        )
        manifest = {
            "repositories": [
                {
                    "repo_key": "service",
                    "local_root": "/unused",
                    "tracked_branch": "main",
                    "profile": "intacct_app",
                    "builders": [],
                }
            ]
        }
        with (
            mock.patch(
                "scripts.refresh_workspace._plan_repository_changes",
                return_value=[change],
            ),
            mock.patch(
                "scripts.refresh_workspace.stage_execution_modes",
                return_value={},
            ) as mocked_modes,
            mock.patch(
                "scripts.refresh_workspace._backup_database",
                side_effect=RuntimeError("stop after invalidation planning"),
            ),
            mock.patch("scripts.refresh_workspace._record_failed_refresh"),
            self.assertRaisesRegex(RuntimeError, "stop after invalidation planning"),
        ):
            _refresh_repository_closure(
                Path("/unused/catalog.db"),
                manifest,
                ["service"],
                "auto",
                start_revisions={"service": "target"},
            )

        self.assertEqual(mocked_modes.call_count, 1)
        self.assertEqual(
            mocked_modes.call_args.kwargs["changed_paths"],
            (
                "app/source/misc/object.yaml",
                "app/source/openapispec/ap/object.yaml",
            ),
        )

    def test_noop_and_line_only_delta_preserve_symbol_id(self) -> None:
        directory, checkout, database, manifest, _initial = self._fixture()
        self.addCleanup(directory.cleanup)
        refresh_repository(database, manifest, "service", mode="full")
        conn = sqlite3.connect(database)
        symbol_id = conn.execute(
            "SELECT id FROM symbols WHERE name='Source'"
        ).fetchone()[0]
        build_count = conn.execute("SELECT COUNT(*) FROM catalog_builds").fetchone()[0]
        conn.close()

        refresh_repository(database, manifest, "service", mode="auto")
        conn = sqlite3.connect(database)
        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM catalog_builds").fetchone()[0],
            build_count,
        )
        self.assertEqual(
            conn.execute(
                "SELECT effective_mode FROM repo_change_sets ORDER BY id DESC LIMIT 1"
            ).fetchone()[0],
            "noop",
        )
        conn.close()

        (checkout / "source.php").write_text("\n<?php\nclass Source {}\n")
        self._commit(checkout, "move symbol")
        refresh_repository(database, manifest, "service", mode="auto")
        conn = sqlite3.connect(database)
        try:
            self.assertEqual(
                conn.execute("SELECT id FROM symbols WHERE name='Source'").fetchone()[
                    0
                ],
                symbol_id,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT effective_mode FROM repo_change_sets ORDER BY id DESC LIMIT 1"
                ).fetchone()[0],
                "delta",
            )
            self.assertEqual(
                conn.execute(
                    "SELECT execution_mode FROM repo_index_stages "
                    "WHERE builder_name='symbols' ORDER BY id DESC LIMIT 1"
                ).fetchone()[0],
                "delta",
            )
        finally:
            conn.close()

    def test_advanced_revision_with_no_scoped_paths_creates_metadata_delta(
        self,
    ) -> None:
        directory, checkout, database, manifest, initial = self._fixture()
        self.addCleanup(directory.cleanup)
        refresh_repository(database, manifest, "service", mode="full")

        conn = sqlite3.connect(database)
        active_build = conn.execute(
            "SELECT id,content_fingerprint,source_revisions_json "
            "FROM catalog_builds WHERE status='active'"
        ).fetchone()
        build_count = conn.execute("SELECT COUNT(*) FROM catalog_builds").fetchone()[0]
        conn.execute(
            """INSERT INTO graph_builds(
                   graph_path,source_db,status,source_fingerprint,catalog_build_id,
                   build_mode,projection_version,source_revisions_json
               ) VALUES ('graph.lbug','catalog.db','active',?,?,'full',2,?)""",
            (active_build[1], active_build[0], active_build[2]),
        )
        conn.commit()
        conn.close()

        (checkout / "README.md").write_text("documentation only\n")
        target = self._commit(checkout, "docs only")
        change = collect_repository_change_set(
            repo_key="service",
            root=checkout,
            tracked_branch="main",
            base_commit_sha=initial,
            requested_mode="auto",
            target_commit_sha=target,
        )
        self.assertEqual(change.effective_mode, "delta")
        self.assertEqual(change.changed_paths, ())

        refresh_repository(database, manifest, "service", mode="auto")

        conn = sqlite3.connect(database)
        conn.row_factory = sqlite3.Row
        try:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM catalog_builds").fetchone()[0],
                build_count + 1,
            )
            repo = conn.execute(
                "SELECT indexed_commit_sha FROM repos WHERE repo_key='service'"
            ).fetchone()
            self.assertEqual(repo["indexed_commit_sha"], target)
            build = conn.execute(
                "SELECT * FROM catalog_builds WHERE status='active'"
            ).fetchone()
            self.assertEqual(
                json.loads(build["source_revisions_json"])["service"], target
            )
            self.assertEqual(
                build["content_fingerprint"], logical_content_fingerprint(conn)
            )
            change_row = conn.execute(
                "SELECT effective_mode,added_count,modified_count,deleted_count,renamed_count "
                "FROM repo_change_sets ORDER BY id DESC LIMIT 1"
            ).fetchone()
            self.assertEqual(
                tuple(change_row),
                ("delta", 0, 0, 0, 0),
            )
            stage_modes = {
                row[0]
                for row in conn.execute(
                    "SELECT DISTINCT execution_mode FROM repo_index_stages "
                    "WHERE run_id=(SELECT MAX(id) FROM repo_index_runs)"
                )
            }
            self.assertEqual(stage_modes, {"skipped"})
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM graph_builds WHERE status='active'"
                ).fetchone()[0],
                0,
            )
        finally:
            conn.close()

        previous = database.with_name(database.name + ".previous")
        self.assertTrue(previous.is_file())
        previous_conn = sqlite3.connect(previous)
        try:
            self.assertEqual(
                previous_conn.execute(
                    "SELECT indexed_commit_sha FROM repos WHERE repo_key='service'"
                ).fetchone()[0],
                initial,
            )
        finally:
            previous_conn.close()

    def test_true_noop_history_failure_rolls_back_all_in_progress_rows(self) -> None:
        directory, _checkout, database, manifest, _initial = self._fixture()
        self.addCleanup(directory.cleanup)
        refresh_repository(database, manifest, "service", mode="full")

        from scripts import refresh_workspace as refresh_module

        real_stage = refresh_module._stage
        for fail_at in (1, 2):
            calls = 0

            def fail_during_history(*args, fail_at=fail_at, **kwargs):
                nonlocal calls
                calls += 1
                if calls == fail_at:
                    raise RuntimeError("injected no-op history failure")
                return real_stage(*args, **kwargs)

            with (
                mock.patch(
                    "scripts.refresh_workspace._stage",
                    side_effect=fail_during_history,
                ),
                self.assertRaisesRegex(RuntimeError, "injected no-op"),
            ):
                refresh_repository(database, manifest, "service", mode="auto")

            conn = sqlite3.connect(database)
            try:
                self.assertEqual(
                    conn.execute(
                        "SELECT COUNT(*) FROM repo_index_runs WHERE status='building'"
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    conn.execute(
                        "SELECT COUNT(*) FROM repo_index_stages WHERE status='pending'"
                    ).fetchone()[0],
                    0,
                )
                self.assertEqual(
                    conn.execute(
                        "SELECT COUNT(*) FROM repo_change_sets WHERE status='planned'"
                    ).fetchone()[0],
                    0,
                )
            finally:
                conn.close()

    def test_forced_delta_fails_without_generation_base(self) -> None:
        directory, _checkout, database, manifest, _initial = self._fixture()
        self.addCleanup(directory.cleanup)
        with self.assertRaises(DeltaUnavailable):
            refresh_repository(database, manifest, "service", mode="delta")

    def test_planner_modes_and_compatibility_metadata_failures(self) -> None:
        directory, _checkout, database, manifest_path, revision = self._fixture()
        self.addCleanup(directory.cleanup)
        refresh_repository(database, manifest_path, "service", mode="full")
        manifest = load_workspace_manifest(manifest_path)
        plans = {"service": build_plan("generic", [])}
        revisions = {"service": revision}

        expected = {"full": "full", "auto": "noop", "delta": "noop"}
        for mode, effective in expected.items():
            with self.subTest(mode=mode):
                change = _plan_repository_changes(
                    database, manifest, ["service"], mode, revisions, plans
                )[0]
                self.assertEqual(change.requested_mode, mode)
                self.assertEqual(change.effective_mode, effective)

        conn = sqlite3.connect(database)
        conn.execute(
            "UPDATE repo_index_runs SET manifest_hash=NULL "
            "WHERE id=(SELECT MAX(id) FROM repo_index_runs)"
        )
        conn.commit()
        conn.close()
        auto = _plan_repository_changes(
            database, manifest, ["service"], "auto", revisions, plans
        )[0]
        self.assertEqual(auto.effective_mode, "full")
        self.assertEqual(auto.fallback_reason, "compatibility metadata unavailable")
        with self.assertRaisesRegex(DeltaUnavailable, "metadata unavailable"):
            _plan_repository_changes(
                database, manifest, ["service"], "delta", revisions, plans
            )

        conn = sqlite3.connect(database)
        conn.execute(
            "UPDATE repo_index_runs SET manifest_hash=?,builder_plan_hash=? "
            "WHERE id=(SELECT MAX(id) FROM repo_index_runs)",
            (
                _repository_manifest_hash(manifest["repositories"][0]),
                _repository_plan_hash(plans["service"]),
            ),
        )
        conn.execute(
            "UPDATE catalog_builds SET source_revisions_json='{}' WHERE status='active'"
        )
        conn.commit()
        conn.close()
        auto = _plan_repository_changes(
            database, manifest, ["service"], "auto", revisions, plans
        )[0]
        self.assertEqual(auto.effective_mode, "full")
        self.assertEqual(
            auto.fallback_reason,
            "active generation revision metadata is inconsistent",
        )

        conn = sqlite3.connect(database)
        conn.execute(
            "UPDATE catalog_builds SET source_revisions_json=?,delta_contract_version=999 "
            "WHERE status='active'",
            (json.dumps(revisions, sort_keys=True, separators=(",", ":")),),
        )
        conn.commit()
        conn.close()
        auto = _plan_repository_changes(
            database, manifest, ["service"], "auto", revisions, plans
        )[0]
        self.assertEqual(auto.fallback_reason, "delta-contract version mismatch")

        conn = sqlite3.connect(database)
        conn.execute(
            "UPDATE catalog_builds SET delta_contract_version=? WHERE status='active'",
            (DELTA_CONTRACT_VERSION,),
        )
        conn.execute(
            "UPDATE repo_index_runs SET builder_plan_hash='incompatible' "
            "WHERE id=(SELECT MAX(id) FROM repo_index_runs)"
        )
        conn.commit()
        conn.close()
        auto = _plan_repository_changes(
            database, manifest, ["service"], "auto", revisions, plans
        )[0]
        self.assertEqual(
            auto.fallback_reason, "repository builder-plan incompatibility"
        )
        with self.assertRaisesRegex(DeltaUnavailable, "builder-plan"):
            _plan_repository_changes(
                database, manifest, ["service"], "delta", revisions, plans
            )

    def test_auto_falls_back_when_change_collection_fails(self) -> None:
        directory, checkout, database, manifest, _initial = self._fixture()
        self.addCleanup(directory.cleanup)
        refresh_repository(database, manifest, "service", mode="full")
        (checkout / "source.php").write_text("<?php\nclass Changed {}\n")
        self._commit(checkout, "change")

        with mock.patch(
            "scripts.refresh_workspace.collect_repository_change_set",
            side_effect=DeltaUnavailable("injected git diff failure"),
        ):
            refresh_repository(database, manifest, "service", mode="auto")

        conn = sqlite3.connect(database)
        try:
            change = conn.execute(
                "SELECT requested_mode,effective_mode,fallback_reason,status "
                "FROM repo_change_sets ORDER BY id DESC LIMIT 1"
            ).fetchone()
            self.assertEqual(
                tuple(change),
                ("auto", "full", "injected git diff failure", "succeeded"),
            )
            build = conn.execute(
                "SELECT requested_mode,effective_mode,status "
                "FROM catalog_builds ORDER BY id DESC LIMIT 1"
            ).fetchone()
            self.assertEqual(tuple(build), ("auto", "full", "active"))
        finally:
            conn.close()

    def test_forced_delta_collection_failure_records_not_started(self) -> None:
        directory, checkout, database, manifest, _initial = self._fixture()
        self.addCleanup(directory.cleanup)
        refresh_repository(database, manifest, "service", mode="full")
        (checkout / "source.php").write_text("<?php\nclass Changed {}\n")
        self._commit(checkout, "change")

        with (
            mock.patch(
                "scripts.refresh_workspace.collect_repository_change_set",
                side_effect=DeltaUnavailable("injected git diff failure"),
            ),
            self.assertRaisesRegex(DeltaUnavailable, "injected git diff"),
        ):
            refresh_repository(database, manifest, "service", mode="delta")

        conn = sqlite3.connect(database)
        try:
            build = conn.execute(
                "SELECT requested_mode,effective_mode,status,diagnostic_error "
                "FROM catalog_builds ORDER BY id DESC LIMIT 1"
            ).fetchone()
            self.assertEqual(tuple(build[:3]), ("delta", "not_started", "failed"))
            self.assertIn("delta_preflight", build[3])
        finally:
            conn.close()

    def test_malformed_and_fatal_git_results_fail_closed(self) -> None:
        malformed = subprocess.CompletedProcess(
            ["git"], 0, stdout=b"Z\0source.php\0", stderr=b""
        )
        with (
            mock.patch("catalog.delta.subprocess.run", return_value=malformed),
            self.assertRaisesRegex(DeltaUnavailable, "unsupported status"),
        ):
            collect_changed_paths(Path("/tmp/fake"), "base", "target")

        success = subprocess.CompletedProcess(["git"], 0, stdout="", stderr="")
        fatal = subprocess.CompletedProcess(
            ["git"], 128, stdout="", stderr="fatal: injected merge failure"
        )
        with (
            mock.patch("catalog.delta._git", side_effect=(success, fatal)),
            self.assertRaisesRegex(DeltaUnavailable, "injected merge failure"),
        ):
            collect_repository_change_set(
                repo_key="service",
                root=Path("/tmp/fake"),
                tracked_branch="main",
                base_commit_sha="base",
                target_commit_sha="target",
                requested_mode="auto",
            )

    def test_blob_lookup_failure_falls_back_in_auto_and_fails_forced_delta(
        self,
    ) -> None:
        real_git = delta_module._git

        def fail_blob_lookup(root, *args, **kwargs):
            if args and args[0] == "rev-parse" and ":" in args[-1]:
                return subprocess.CompletedProcess(
                    ["git"], 128, stdout="", stderr="fatal: injected blob failure"
                )
            return real_git(root, *args, **kwargs)

        for mode in ("auto", "delta"):
            with self.subTest(mode=mode):
                directory, checkout, database, manifest, _initial = self._fixture()
                self.addCleanup(directory.cleanup)
                refresh_repository(database, manifest, "service", mode="full")
                (checkout / "source.php").write_text("<?php\nclass Changed {}\n")
                self._commit(checkout, "change")

                with mock.patch("catalog.delta._git", side_effect=fail_blob_lookup):
                    if mode == "delta":
                        with self.assertRaisesRegex(
                            DeltaUnavailable, "injected blob failure"
                        ):
                            refresh_repository(database, manifest, "service", mode=mode)
                    else:
                        refresh_repository(database, manifest, "service", mode=mode)

                conn = sqlite3.connect(database)
                try:
                    if mode == "auto":
                        row = conn.execute(
                            "SELECT requested_mode,effective_mode,fallback_reason,status "
                            "FROM repo_change_sets ORDER BY id DESC LIMIT 1"
                        ).fetchone()
                        self.assertEqual(row[0:2], ("auto", "full"))
                        self.assertIn("injected blob failure", row[2])
                        self.assertEqual(row[3], "succeeded")
                    else:
                        row = conn.execute(
                            "SELECT requested_mode,effective_mode,status,diagnostic_error "
                            "FROM catalog_builds ORDER BY id DESC LIMIT 1"
                        ).fetchone()
                        self.assertEqual(row[0:3], ("delta", "not_started", "failed"))
                        self.assertIn("injected blob failure", row[3])
                finally:
                    conn.close()

    def test_promotion_does_not_activate_preexisting_validated_run(self) -> None:
        directory, _checkout, database, manifest, _initial = self._fixture()
        self.addCleanup(directory.cleanup)
        refresh_repository(database, manifest, "service", mode="full")
        conn = sqlite3.connect(database)
        repo_id, tracked_branch = conn.execute(
            "SELECT id,tracked_branch FROM repos WHERE repo_key='service'"
        ).fetchone()
        stale_run_id = conn.execute(
            """INSERT INTO repo_index_runs(
                   repo_id,tracked_branch,commit_sha,status
               ) VALUES (?,?,?,'validated')""",
            (repo_id, tracked_branch, "stale-validated-sha"),
        ).lastrowid
        active_build_id = conn.execute(
            "SELECT id FROM catalog_builds WHERE status='active'"
        ).fetchone()[0]
        conn.commit()
        conn.close()

        with self.assertRaisesRegex(CatalogIntegrityError, "in_progress_state"):
            refresh_repository(database, manifest, "service", mode="full")

        conn = sqlite3.connect(database)
        try:
            self.assertEqual(
                conn.execute(
                    "SELECT status FROM repo_index_runs WHERE id=?", (stale_run_id,)
                ).fetchone()[0],
                "validated",
            )
            self.assertEqual(
                conn.execute(
                    "SELECT id FROM catalog_builds WHERE status='active'"
                ).fetchone()[0],
                active_build_id,
            )
        finally:
            conn.close()

    def test_independent_closure_switching_preserves_repository_compatibility(
        self,
    ) -> None:
        directory, _repositories, database, manifest = self._two_repo_fixture()
        self.addCleanup(directory.cleanup)
        refresh_repository(database, manifest, "a", mode="full")
        refresh_repository(database, manifest, "b", mode="full")
        conn = sqlite3.connect(database)
        build_count = conn.execute("SELECT COUNT(*) FROM catalog_builds").fetchone()[0]
        conn.close()

        refresh_repository(database, manifest, "a", mode="auto")
        conn = sqlite3.connect(database)
        try:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM catalog_builds").fetchone()[0],
                build_count,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT effective_mode,fallback_reason FROM repo_change_sets "
                    "ORDER BY id DESC LIMIT 1"
                ).fetchone(),
                ("noop", None),
            )
        finally:
            conn.close()

    def test_dependency_closure_records_mixed_delta_and_noop_atomically(self) -> None:
        directory, repositories, database, manifest = self._two_repo_fixture()
        self.addCleanup(directory.cleanup)
        manifest.write_text(
            manifest.read_text().replace(
                "  - repo_key: b\n",
                "  - repo_key: b\n    depends_on: [a]\n",
            )
        )
        refresh_repository(database, manifest, "b", mode="full")
        (repositories["a"] / "source.php").write_text("<?php\nclass AChanged {}\n")
        target = self._commit(repositories["a"], "change dependency")

        refresh_repository(database, manifest, "b", mode="auto")

        conn = sqlite3.connect(database)
        try:
            build_id = conn.execute(
                "SELECT id FROM catalog_builds WHERE status='active'"
            ).fetchone()[0]
            modes = dict(
                conn.execute(
                    """SELECT r.repo_key,rcs.effective_mode
                       FROM repo_change_sets rcs
                       JOIN repos r ON r.id=rcs.repo_id
                       WHERE rcs.catalog_build_id=?""",
                    (build_id,),
                )
            )
            self.assertEqual(modes, {"a": "delta", "b": "noop"})
            self.assertEqual(
                conn.execute(
                    "SELECT indexed_commit_sha FROM repos WHERE repo_key='a'"
                ).fetchone()[0],
                target,
            )
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            conn.close()

        text = manifest.read_text().replace(
            f"local_root: {repositories['b']}", "local_root: /unused/operator/path"
        )
        manifest.write_text(text)
        refresh_repository(database, manifest, "a", mode="auto")
        conn = sqlite3.connect(database)
        try:
            self.assertEqual(
                conn.execute(
                    "SELECT effective_mode FROM repo_change_sets ORDER BY id DESC LIMIT 1"
                ).fetchone()[0],
                "noop",
            )
            self.assertEqual(
                conn.execute(
                    "SELECT local_root FROM repos WHERE repo_key='b'"
                ).fetchone()[0],
                str(repositories["b"]),
            )
        finally:
            conn.close()

    def test_active_fingerprint_mismatch_cannot_record_noop(self) -> None:
        directory, _checkout, database, manifest, _initial = self._fixture()
        self.addCleanup(directory.cleanup)
        refresh_repository(database, manifest, "service", mode="full")
        conn = sqlite3.connect(database)
        before_changes = conn.execute(
            "SELECT COUNT(*) FROM repo_change_sets"
        ).fetchone()[0]
        conn.execute(
            "UPDATE repos SET indexed_commit_sha='tampered' WHERE repo_key='service'"
        )
        conn.commit()
        conn.close()

        with self.assertRaisesRegex(RefreshError, "logical fingerprint"):
            refresh_repository(database, manifest, "service", mode="auto")

        conn = sqlite3.connect(database)
        try:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM repo_change_sets").fetchone()[0],
                before_changes,
            )
            failed = conn.execute(
                "SELECT requested_mode,effective_mode,status FROM catalog_builds "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
            self.assertEqual(failed, ("auto", "not_started", "failed"))
        finally:
            conn.close()

    def test_builder_invalidation_closure_and_cross_repo_coverage(self) -> None:
        app_plan = build_plan("intacct_app", [])
        modes = stage_execution_modes(
            app_plan,
            repository_mode="delta",
            changed_paths=["app/source/openapispec/ap/object.yaml"],
        )
        for builder in (
            "entities",
            "openapi_scan",
            "openapi_link",
            "workflows",
            "rest_endpoints",
            "entity_semantics",
            "entity_access_links",
        ):
            self.assertNotEqual(modes[builder][0], "skipped", builder)

        automation_plan = build_plan("rest_automation", [])
        coverage_modes = stage_execution_modes(
            automation_plan,
            repository_mode="delta",
            forced=("gherkin_coverage",),
        )
        self.assertEqual(coverage_modes["gherkin_coverage"][0], "full")
        self.assertIn("cross-repository", coverage_modes["gherkin_coverage"][1])

    def test_all_entity_scanner_inputs_invalidate_entity_mappings(self) -> None:
        plan = build_plan("intacct_app", [])
        for suffix in sorted(ENTITY_INPUT_SUFFIXES):
            modes = stage_execution_modes(
                plan,
                repository_mode="delta",
                changed_paths=[f"app/source/ap/APBillManager{suffix}"],
            )
            self.assertEqual(modes["entities"][0], "full", suffix)
            self.assertNotEqual(modes["entity_roots"][0], "skipped", suffix)

        openapi_modes = stage_execution_modes(
            plan,
            repository_mode="delta",
            changed_paths=["app/source/openapispec/ap/workflows.bill.s1.schema.yaml"],
        )
        self.assertEqual(openapi_modes["entities"][0], "full")

        unrelated_modes = stage_execution_modes(
            plan,
            repository_mode="delta",
            changed_paths=["app/source/ap/Unrelated.java"],
        )
        self.assertEqual(
            unrelated_modes["entities"], ("skipped", "source inputs unchanged")
        )

    def test_dbschema_include_invalidates_security_and_downstream_builders(
        self,
    ) -> None:
        modes = stage_execution_modes(
            build_plan("intacct_app", []),
            repository_mode="delta",
            changed_paths=["app/source/common/dbschema.inc"],
        )

        self.assertEqual(
            modes["security"],
            ("full", "source change: app/source/common/dbschema.inc"),
        )
        self.assertEqual(modes["entity_semantics"][0], "full")
        self.assertTrue(modes["entity_semantics"][1].startswith("invalidated by "))
        self.assertEqual(modes["entity_access_links"][0], "full")
        self.assertIn("app/source/common/dbschema.inc", modes["entity_access_links"][1])
        self.assertEqual(modes["entities"], ("skipped", "source inputs unchanged"))
        self.assertEqual(modes["openapi_scan"], ("skipped", "source inputs unchanged"))
        self.assertEqual(
            modes["rest_endpoints"], ("skipped", "source inputs unchanged")
        )


if __name__ == "__main__":
    unittest.main()
