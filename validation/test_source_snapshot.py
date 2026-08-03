from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from catalog.source_snapshot import SourceSnapshotError, materialize_source_snapshot


class SourceSnapshotTests(unittest.TestCase):
    def _git(self, root: Path, *args: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(root), *args], text=True
        ).strip()

    def _commit(self, root: Path, message: str) -> str:
        self._git(root, "add", "-A")
        subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.invalid",
                "commit",
                "-m",
                message,
            ],
            check=True,
            capture_output=True,
        )
        return self._git(root, "rev-parse", "HEAD")

    def _fixture(self) -> tuple[tempfile.TemporaryDirectory[str], Path, str]:
        directory = tempfile.TemporaryDirectory()
        root = Path(directory.name) / "repo"
        root.mkdir()
        self._git(root, "init", "-b", "main")
        (root / "nested").mkdir()
        (root / "nested" / "source.txt").write_bytes(b"committed\r\nbytes\n")
        executable = root / "run.sh"
        executable.write_bytes(b"#!/bin/sh\nexit 0\n")
        executable.chmod(0o755)
        sha = self._commit(root, "initial")
        return directory, root, sha

    def test_materializes_raw_commit_bytes_and_mode_then_cleans_up(self) -> None:
        directory, root, sha = self._fixture()
        self.addCleanup(directory.cleanup)
        (root / "nested" / "source.txt").write_bytes(b"dirty working tree\n")
        (root / "ignored.txt").write_text("untracked\n")
        snapshot_path: Path | None = None
        with materialize_source_snapshot("repo", root, sha) as snapshot:
            snapshot_path = snapshot.snapshot_root
            self.assertEqual(
                (snapshot.snapshot_root / "nested/source.txt").read_bytes(),
                b"committed\r\nbytes\n",
            )
            self.assertFalse((snapshot.snapshot_root / "ignored.txt").exists())
            self.assertEqual(
                os.stat(snapshot.snapshot_root / "run.sh").st_mode & 0o777, 0o755
            )
            self.assertEqual(snapshot.target_sha, sha)
            self.assertEqual(snapshot.tracked_file_count, 2)
        assert snapshot_path is not None
        self.assertFalse(snapshot_path.exists())

    def test_rejects_symlink_before_yield(self) -> None:
        directory, root, _sha = self._fixture()
        self.addCleanup(directory.cleanup)
        (root / "link").symlink_to("run.sh")
        sha = self._commit(root, "symlink")
        with (
            self.assertRaisesRegex(SourceSnapshotError, "120000"),
            materialize_source_snapshot("repo", root, sha),
        ):
            self.fail("unsupported tree yielded a snapshot")

    def test_missing_or_wrong_type_target_is_rejected(self) -> None:
        directory, root, _sha = self._fixture()
        self.addCleanup(directory.cleanup)
        with (
            self.assertRaises(SourceSnapshotError),
            materialize_source_snapshot("repo", root, "f" * 40),
        ):
            self.fail("missing commit yielded a snapshot")
        blob = self._git(root, "hash-object", "run.sh")
        with (
            self.assertRaises(SourceSnapshotError),
            materialize_source_snapshot("repo", root, blob),
        ):
            self.fail("blob target yielded a snapshot")

    def test_rejects_gitlink_before_materialization(self) -> None:
        directory, root, _sha = self._fixture()
        self.addCleanup(directory.cleanup)
        nested = root / "nested-repository"
        nested.mkdir()
        self._git(nested, "init", "-b", "main")
        (nested / "tracked.txt").write_text("nested\n", encoding="utf-8")
        self._commit(nested, "nested")
        sha = self._commit(root, "gitlink")
        with (
            self.assertRaisesRegex(SourceSnapshotError, "160000"),
            materialize_source_snapshot("repo", root, sha),
        ):
            self.fail("gitlink tree yielded a snapshot")

    def test_large_batch_streams_requests_and_responses_without_deadlock(self) -> None:
        directory, root, _sha = self._fixture()
        self.addCleanup(directory.cleanup)
        bulk = root / "bulk"
        bulk.mkdir()
        for index in range(2000):
            (bulk / f"file-{index:04d}.txt").write_text(
                f"value {index}\n", encoding="utf-8"
            )
        sha = self._commit(root, "large tree")
        with materialize_source_snapshot("repo", root, sha) as snapshot:
            self.assertEqual(snapshot.tracked_file_count, 2002)
            self.assertEqual(
                (snapshot.snapshot_root / "bulk/file-1999.txt").read_text(),
                "value 1999\n",
            )


if __name__ == "__main__":
    unittest.main()
