from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ia_repomap_builder import BuildResult
from ia_repomap_builder.review import (
    PRMetadata,
    REQUIRED_RIPWIRE_SKILLS,
    ReviewSetupError,
    ReviewRequest,
    _fetch_and_verify,
    acquire_review_checkout,
    marker_env,
    resolve_pr,
    run_review,
    setup_review,
    workspace_root,
)


class ReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.source = self.root / "source"
        self.remote = self.root / "remote.git"
        self.workspace = self.root / "workspace"
        self.source.mkdir(parents=True)
        self._git(self.source, "init", "-q")
        self._git(self.source, "config", "user.email", "review@example.invalid")
        self._git(self.source, "config", "user.name", "review")
        (self.source / "README.md").write_text("base\n", encoding="utf-8")
        self._git(self.source, "add", "README.md")
        self._git(self.source, "commit", "-qm", "base")
        self.base = self._git(self.source, "rev-parse", "HEAD", output=True)
        (self.source / "README.md").write_text("head\n", encoding="utf-8")
        self._git(self.source, "commit", "-qam", "head")
        self.head = self._git(self.source, "rev-parse", "HEAD", output=True)
        self._git(self.root, "clone", "--bare", str(self.source), str(self.remote))
        self._git(self.remote, "update-ref", "refs/pull/7/head", self.head)
        self._git(self.source, "remote", "add", "origin", str(self.remote))
        self.metadata = PRMetadata(
            url="https://github.com/acme/demo/pull/7", number=7, base_sha=self.base,
            head_sha=self.head, repository="acme/demo", clone_url=str(self.remote),
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _git(self, cwd: Path, *args: str, output: bool = False) -> str:
        completed = subprocess.run(
            ["git", *args], cwd=cwd if cwd.exists() else None, check=True,
            capture_output=True, text=True,
        )
        return completed.stdout.strip() if output else ""

    def _runner(self, arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if arguments[:3] == ["gh", "pr", "view"]:
            payload = {
                "number": 7, "baseRefOid": self.base, "headRefOid": self.head,
            }
            return subprocess.CompletedProcess(arguments, 0, json.dumps(payload), "")
        return subprocess.run(arguments, **kwargs)

    def test_resolve_pr_metadata_uses_mocked_gh_and_validates_identity(self) -> None:
        calls: list[list[str]] = []

        def runner(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(arguments)
            return self._runner(arguments, **kwargs)

        metadata = resolve_pr("https://github.com/acme/demo/pull/7", runner=runner)
        self.assertEqual((metadata.number, metadata.base_sha, metadata.head_sha), (7, self.base, self.head))
        self.assertEqual(metadata.head_ref, "refs/ia-repomap/pr/7/head")
        self.assertEqual(metadata.repository, "acme/demo")
        self.assertEqual(metadata.clone_url, "https://github.com/acme/demo.git")
        self.assertEqual(
            calls,
            [[
                "gh", "pr", "view", "https://github.com/acme/demo/pull/7",
                "--json", "number,baseRefOid,headRefOid",
            ]],
        )

    def test_checkout_fetches_exact_refs_without_changing_caller_checkout(self) -> None:
        before = (
            self._git(self.source, "rev-parse", "HEAD", output=True),
            self._git(self.source, "status", "--porcelain", output=True),
            (self.source / "README.md").read_text(encoding="utf-8"),
        )
        checkout = acquire_review_checkout(
            self.metadata, repo=self.source, workspace=self.workspace, runner=self._runner
        )
        self.assertFalse(checkout.reused)
        self.assertEqual(
            checkout.worktree,
            (self.workspace / "checkouts" / "acme-demo" / self.head / self.base).resolve(),
        )
        self.assertEqual(self._git(checkout.worktree, "rev-parse", "HEAD", output=True), self.head)
        self.assertEqual(checkout.merge_base, self.base)
        self.assertEqual(
            before,
            (
                self._git(self.source, "rev-parse", "HEAD", output=True),
                self._git(self.source, "status", "--porcelain", output=True),
                (self.source / "README.md").read_text(encoding="utf-8"),
            ),
        )
        self.assertEqual(self._git(self.source, "rev-parse", self.metadata.head_ref, output=True), self.head)

    def test_checkout_reuses_clean_registered_exact_worktree(self) -> None:
        first = acquire_review_checkout(self.metadata, repo=self.source, workspace=self.workspace, runner=self._runner)
        second = acquire_review_checkout(self.metadata, repo=self.source, workspace=self.workspace, runner=self._runner)
        self.assertFalse(first.reused)
        self.assertTrue(second.reused)
        self.assertEqual(first.worktree, second.worktree)

    def test_repo_is_required(self) -> None:
        with self.assertRaises(TypeError):
            acquire_review_checkout(self.metadata, workspace=self.workspace, runner=self._runner)  # type: ignore[call-arg]

    def test_supplied_repo_with_wrong_remote_is_rejected(self) -> None:
        wrong = self.root / "wrong"
        wrong.mkdir()
        self._git(wrong, "init", "-q")
        self._git(wrong, "remote", "add", "origin", "https://github.com/acme/other.git")
        with self.assertRaisesRegex(ReviewSetupError, "does not match"):
            acquire_review_checkout(self.metadata, repo=wrong, workspace=self.workspace, runner=self._runner)

    def test_ssh_origin_normalization_is_accepted(self) -> None:
        def runner(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            if arguments == ["git", "remote", "get-url", "origin"]:
                return subprocess.CompletedProcess(
                    arguments, 0, "ssh://git@github.com/acme/demo.git\n", ""
                )
            return self._runner(arguments, **kwargs)

        checkout = acquire_review_checkout(
            self.metadata, repo=self.source, workspace=self.workspace, runner=runner
        )
        self.assertEqual(checkout.worktree.name, self.base)

    def test_head_mismatch_is_rejected_after_fetch(self) -> None:
        with self.assertRaisesRegex(ReviewSetupError, "resolved PR head differs"):
            acquire_review_checkout(
                replace(self.metadata, head_sha=self.base), repo=self.source,
                workspace=self.workspace, runner=self._runner,
            )

    def test_unresolvable_base_is_rejected_during_fetch(self) -> None:
        with self.assertRaisesRegex(ReviewSetupError, "git fetch"):
            acquire_review_checkout(
                replace(self.metadata, base_sha="0" * 40), repo=self.source,
                workspace=self.workspace, runner=self._runner,
            )

    def test_base_revision_and_merge_base_are_kept_separate(self) -> None:
        other_merge_base = "1" * 40
        calls: list[list[str]] = []

        def runner(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(arguments)
            if arguments[:2] == ["git", "fetch"]:
                return subprocess.CompletedProcess(arguments, 0, "", "")
            if arguments[:4] == ["git", "rev-parse", "--verify", f"{self.metadata.base_sha}^{{commit}}"]:
                return subprocess.CompletedProcess(arguments, 0, self.metadata.base_sha + "\n", "")
            if arguments[:4] == ["git", "rev-parse", "--verify", f"{self.metadata.head_ref}^{{commit}}"]:
                return subprocess.CompletedProcess(arguments, 0, self.metadata.head_sha + "\n", "")
            if arguments[:2] == ["git", "merge-base"]:
                return subprocess.CompletedProcess(arguments, 0, other_merge_base + "\n", "")
            raise AssertionError(f"unexpected command: {arguments}")

        self.assertEqual(_fetch_and_verify(self.metadata, self.workspace, runner), other_merge_base)
        self.assertFalse(any("--is-ancestor" in call for call in calls))

    def test_dirty_retained_worktree_is_rejected(self) -> None:
        checkout = acquire_review_checkout(self.metadata, repo=self.source, workspace=self.workspace, runner=self._runner)
        (checkout.worktree / "untracked.txt").write_text("dirty\n", encoding="utf-8")
        with self.assertRaisesRegex(ReviewSetupError, "not clean"):
            acquire_review_checkout(self.metadata, repo=self.source, workspace=self.workspace, runner=self._runner)

    def test_stale_worktree_path_is_rejected(self) -> None:
        checkout = acquire_review_checkout(self.metadata, repo=self.source, workspace=self.workspace, runner=self._runner)
        self._git(self.source, "worktree", "remove", "--force", str(checkout.worktree))
        checkout.worktree.mkdir(parents=True)
        with self.assertRaisesRegex(ReviewSetupError, "not registered"):
            acquire_review_checkout(self.metadata, repo=self.source, workspace=self.workspace, runner=self._runner)

    def test_worktree_add_failure_retains_bounded_partial_path(self) -> None:
        expected = self.workspace / "checkouts" / "acme-demo" / self.head / self.base

        def runner(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            if arguments[:4] == ["git", "worktree", "add", "--detach"]:
                expected.mkdir(parents=True)
                (expected / "partial.txt").write_text("partial\n", encoding="utf-8")
                return subprocess.CompletedProcess(arguments, 1, "", "worktree add failed")
            return self._runner(arguments, **kwargs)

        with self.assertRaisesRegex(ReviewSetupError, "state was not deleted"):
            acquire_review_checkout(self.metadata, repo=self.source, workspace=self.workspace, runner=runner)
        self.assertTrue((expected / "partial.txt").is_file())

    def test_workspace_inside_source_is_rejected(self) -> None:
        with self.assertRaisesRegex(ReviewSetupError, "outside"):
            acquire_review_checkout(
                self.metadata, repo=self.source, workspace=self.source / ".review-state", runner=self._runner
            )

    def test_workspace_inside_registered_worktree_is_rejected(self) -> None:
        other = self.root / "other-worktree"
        self._git(self.source, "worktree", "add", "--detach", str(other), self.head)
        with self.assertRaisesRegex(ReviewSetupError, "registered worktrees"):
            acquire_review_checkout(
                self.metadata, repo=self.source, workspace=other / "state", runner=self._runner
            )

    def test_symlinked_retained_worktree_path_is_rejected(self) -> None:
        target = self.root / "outside"
        target.mkdir()
        nested = self.workspace / "checkouts" / "acme-demo" / self.head
        nested.mkdir(parents=True)
        os.symlink(target, nested / self.base)
        with self.assertRaisesRegex(ReviewSetupError, "symlink|escapes"):
            acquire_review_checkout(
                self.metadata, repo=self.source, workspace=self.workspace, runner=self._runner
            )

    def test_workspace_mkdir_failure_has_bounded_remediation(self) -> None:
        with patch.object(Path, "mkdir", side_effect=OSError("read only")):
            with self.assertRaisesRegex(ReviewSetupError, "writable external --workspace"):
                acquire_review_checkout(
                    self.metadata, repo=self.source, workspace=self.workspace, runner=self._runner
                )

    def test_gh_failure_is_reported(self) -> None:
        def runner(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(arguments, 1, "", "gh authentication failed")

        with self.assertRaisesRegex(ReviewSetupError, "gh pr view.*authentication failed"):
            resolve_pr("https://github.com/acme/demo/pull/7", runner=runner)

    def test_invalid_pr_url_is_rejected_before_gh(self) -> None:
        with self.assertRaisesRegex(ReviewSetupError, "canonical GitHub URL"):
            resolve_pr("https://example.com/acme/demo/pull/7", runner=lambda *_args, **_kwargs: None)  # type: ignore[arg-type]

    def test_workspace_precedence(self) -> None:
        with patch.dict(os.environ, {"IA_REPOMAP_HOME": str(self.root / "env")}, clear=False):
            self.assertEqual(workspace_root(), (self.root / "env").resolve())
            self.assertEqual(workspace_root(self.root / "explicit"), (self.root / "explicit").resolve())

    def test_marker_context_restores_environment(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with marker_env() as marker:
                self.assertTrue(marker.is_file())
                self.assertEqual(Path(os.environ["IA_REPOMAP_CONFIG"]), marker)
            self.assertNotIn("IA_REPOMAP_CONFIG", os.environ)

    def test_setup_review_propagates_exact_identity_and_loads_fixed_skills(self) -> None:
        checkout = acquire_review_checkout(
            self.metadata, repo=self.source, workspace=self.workspace, runner=self._runner
        )
        observed: dict[str, object] = {}

        @contextmanager
        def marker() -> object:
            observed["marker_before_prepare"] = os.environ.get("IA_REPOMAP_CONFIG")
            os.environ["IA_REPOMAP_CONFIG"] = "fixture-marker"
            try:
                yield Path("fixture-marker")
            finally:
                observed["marker_after_prepare"] = os.environ.get("IA_REPOMAP_CONFIG")
                os.environ.pop("IA_REPOMAP_CONFIG", None)

        def prepare(request: object) -> BuildResult:
            observed["prepare_request"] = request
            observed["marker_during_prepare"] = os.environ.get("IA_REPOMAP_CONFIG")
            return BuildResult(engine="ripwire", status="ok", identity={"head": self.head})

        with (
            patch("ia_repomap_builder.review.resolve_pr", return_value=self.metadata),
            patch("ia_repomap_builder.review.acquire_review_checkout", return_value=checkout),
            patch.dict(os.environ, {"RIPWIRE_SKILLS_DIR": str(self.root)}, clear=False),
            patch("ia_repomap_builder.review.load_selected_ripwire_skills") as loader,
            patch("ia_repomap_builder.review.marker_env", side_effect=marker),
            patch("ia_repomap_builder.review.prepare_repomap", side_effect=prepare),
            patch("ia_repomap_builder.review.uuid.uuid4", return_value=SimpleNamespace(hex="run")),
        ):
            result = setup_review(ReviewRequest(self.metadata.url, self.source, self.workspace))

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.base_sha, self.base)
        self.assertEqual(result.head_sha, self.head)
        self.assertEqual(result.identity["merge_base"], checkout.merge_base)
        self.assertEqual(result.report_directory, (self.workspace / "reports" / "acme-demo" / self.head / self.base / "run").resolve())
        self.assertEqual(result.artifact_root, (self.workspace / "artifacts").resolve())
        self.assertEqual(loader.call_args.args[1], REQUIRED_RIPWIRE_SKILLS)
        self.assertEqual(observed["marker_during_prepare"], "fixture-marker")
        self.assertEqual(observed["marker_after_prepare"], "fixture-marker")

    def test_setup_review_returns_unavailable_skill_prerequisite_without_exposing_path(self) -> None:
        with (
            patch("ia_repomap_builder.review.resolve_pr", return_value=self.metadata),
            patch("ia_repomap_builder.review.acquire_review_checkout") as acquire,
            patch.dict(os.environ, {}, clear=True),
        ):
            result = setup_review(ReviewRequest(self.metadata.url, self.source, self.workspace))
        self.assertEqual(result.status, "unavailable")
        self.assertTrue(result.remediation)
        self.assertNotIn(str(self.root), " ".join(result.remediation))
        acquire.assert_called_once()

    def test_run_review_reuses_setup_and_does_not_analyse_failed_setup(self) -> None:
        setup = SimpleNamespace(
            status="unavailable", base_sha=self.base, head_sha=self.head,
            report_directory=self.workspace / "report", remediation=("missing",),
            metadata=None, checkout=None,
        )
        with patch("ia_repomap_builder.review.setup_review", return_value=setup), patch(
            "ia_repomap_builder.review.run_pr_analysis"
        ) as analysis:
            result = run_review(ReviewRequest(self.metadata.url, self.source, self.workspace))
        self.assertEqual(result.status, "unavailable")
        analysis.assert_not_called()

    def test_run_review_passes_exact_shas_and_returns_partial_report_success(self) -> None:
        checkout = acquire_review_checkout(
            self.metadata, repo=self.source, workspace=self.workspace, runner=self._runner
        )
        report_directory = self.workspace / "reports" / "run"
        setup = SimpleNamespace(
            status="ok", metadata=self.metadata, checkout=checkout,
            artifact_root=self.workspace / "artifacts", report_directory=report_directory,
            skill_policy=SimpleNamespace(name="loaded-policy"),
            base_sha=self.base, head_sha=self.head, remediation=(),
        )
        observed: dict[str, object] = {}

        def analysis(request: object, **kwargs: object) -> object:
            observed["request"] = request
            observed["policy"] = kwargs["ripwire_skill_policy"]
            report_directory.mkdir(parents=True)
            (report_directory / "report.json").write_text("{}\n", encoding="utf-8")
            return SimpleNamespace(status="ok", assessment="partial", remediation=())

        with (
            patch("ia_repomap_builder.review.setup_review", return_value=setup),
            patch("ia_repomap_builder.review.load_bedrock_settings", return_value=object()),
            patch("ia_repomap_builder.review.marker_env", return_value=__import__("contextlib").nullcontext()),
        ):
            result = run_review(
                ReviewRequest(self.metadata.url, self.source),
                analysis_runner=analysis,
            )

        request = observed["request"]
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.assessment, "partial")
        self.assertEqual(result.base_sha, self.base)
        self.assertEqual(result.head_sha, self.head)
        self.assertEqual(result.report_files, ("report.json",))
        self.assertEqual(request.base_ref, self.base)
        self.assertEqual(observed["policy"].name, "loaded-policy")


if __name__ == "__main__":
    unittest.main()
