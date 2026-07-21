from __future__ import annotations

import sqlite3
import subprocess
import tempfile
import unittest
import os
from pathlib import Path

from scripts.refresh_workspace import RefreshError, refresh_repository


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
        self._git(checkout, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "initial")
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
                subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip(),
            )
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM files").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT status FROM repo_index_runs").fetchone()[0], "active")
        finally:
            conn.close()
        self.assertTrue(database.with_name("catalog.db.previous").is_file())

    def test_dirty_checkout_does_not_promote_candidate(self) -> None:
        directory, checkout, database, manifest = self._fixture()
        self.addCleanup(directory.cleanup)
        # Establish an active revision first.  A later failed attempt must not
        # make the working catalog look failed or discard its indexed SHA.
        refresh_repository(database, manifest, "service")
        before = sqlite3.connect(database).execute(
            "SELECT indexed_commit_sha FROM repos WHERE repo_key='service'"
        ).fetchone()[0]
        (checkout / "source.py").write_text("changed\n", encoding="utf-8")
        with self.assertRaises(RefreshError):
            refresh_repository(database, manifest, "service")
        conn = sqlite3.connect(database)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM files").fetchone()[0], 1)
            repo = conn.execute(
                """SELECT indexed_commit_sha,index_status,last_attempt_status,last_attempt_error
                   FROM repos WHERE repo_key='service'"""
            ).fetchone()
            self.assertEqual(repo[0], before)
            self.assertEqual(repo[1], "active")
            self.assertEqual(repo[2], "failed")
            self.assertIn("dirty", repo[3])
            self.assertEqual(
                conn.execute("SELECT status FROM repo_index_runs ORDER BY id DESC").fetchone()[0],
                "failed",
            )
        finally:
            conn.close()

    def test_compatibility_refresh_script_uses_workspace_runner(self) -> None:
        directory, _checkout, database, manifest = self._fixture()
        self.addCleanup(directory.cleanup)
        database.unlink()
        result = subprocess.run(
            [
                "bash", str(ROOT / "scripts" / "refresh.sh"),
                "--db", str(database), "--manifest", str(manifest), "--repo", "service",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            env={**os.environ, "PYTHON_BIN": "/Users/aritra.ghosh/projects/intacct-repo-intelligence/.venv/bin/python"},
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        conn = sqlite3.connect(database)
        try:
            self.assertEqual(
                conn.execute("SELECT index_status FROM repos WHERE repo_key='service'").fetchone()[0],
                "active",
            )
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
