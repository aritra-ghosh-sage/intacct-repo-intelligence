from __future__ import annotations

import json

import pytest

from catalog.github_pr_metadata import GitHubPrMetadataError
from catalog.pr_review_catalog import PrReviewCatalogError
from catalog.pr_review_prompt import PromptBuildError
from scripts import generate_pr_review_prompt as cli


def _envelope() -> dict:
    return {"prompt_text": "review prompt", "status": "ready"}


@pytest.mark.parametrize(
    "value",
    ["0", "not-a-pr", "https://github.com/example/other/pull/1"],
)
def test_pr_number_rejects_invalid_input(value: str) -> None:
    with pytest.raises(SystemExit) as caught:
        cli.main(["--pr", value, "--request", "Review"])

    assert caught.value.code == 2


def test_cli_rejects_removed_active_db_option() -> None:
    with pytest.raises(SystemExit) as caught:
        cli.main(
            [
                "--pr",
                "49156",
                "--request",
                "Review",
                "--active-db",
                "catalog.db",
            ]
        )

    assert caught.value.code == 2


def test_cli_prints_json_and_returns_zero(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "generate_prompt", lambda **_kwargs: _envelope())

    assert cli.main(["--pr", "49156", "--request", "Review"]) == 0

    assert '"prompt_text": "review prompt"' in capsys.readouterr().out


def test_cli_prompt_only_prints_only_prompt(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "generate_prompt", lambda **_kwargs: _envelope())

    assert cli.main(["--pr", "49156", "--request", "Review", "--prompt-only"]) == 0

    captured = capsys.readouterr()
    assert captured.out == "review prompt\n"
    assert captured.err == ""


def test_cli_compact_json_omits_prompt_text(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        cli,
        "generate_prompt",
        lambda **_kwargs: {
            "schema_version": "0.1",
            "analysis_kind": "pr_review_prompt",
            "status": "partial",
            "input": {"target_revision": "b" * 40},
            "step0": {"changed_files": []},
            "step0_validation": {"status": "pass", "errors": []},
            "task_plan": [],
            "reports": {"step1": {"status": "partial"}},
            "provenance": {"catalog_revision": "b" * 40},
            "prompt_text": "rendered prompt",
        },
    )

    assert cli.main(["--pr", "49156", "--request", "Review", "--compact-json"]) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["analysis_kind"] == "pr_review_result"
    assert output["reports"] == {"step1": {"status": "partial"}}
    assert "prompt_text" not in output


def test_cli_rejects_compact_json_with_prompt_only() -> None:
    with pytest.raises(SystemExit) as caught:
        cli.main(
            [
                "--pr",
                "49156",
                "--request",
                "Review",
                "--prompt-only",
                "--compact-json",
            ]
        )

    assert caught.value.code == 2


def test_cli_progress_is_forwarded_without_changing_stdout(monkeypatch, capsys) -> None:
    captured_kwargs: dict[str, object] = {}

    def generate(**kwargs):
        captured_kwargs.update(kwargs)
        return _envelope()

    monkeypatch.setattr(cli, "generate_prompt", generate)

    assert cli.main(["--pr", "49156", "--request", "Review", "--progress"]) == 0

    assert captured_kwargs["show_progress"] is True
    assert json.loads(capsys.readouterr().out) == _envelope()


def test_cli_reports_github_error_with_remediation(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        cli,
        "generate_prompt",
        lambda **_kwargs: (_ for _ in ()).throw(GitHubPrMetadataError("denied")),
    )

    assert cli.main(["--pr", "49156", "--request", "Review"]) == 1

    error = capsys.readouterr().err
    assert "github_metadata_unavailable" in error
    assert "verify gh authentication" in error


def test_cli_reports_catalog_error_with_remediation(monkeypatch, capsys) -> None:
    error = PrReviewCatalogError(
        "catalog_build_failed", "build failed", "free disk and retry"
    )
    monkeypatch.setattr(
        cli, "generate_prompt", lambda **_kwargs: (_ for _ in ()).throw(error)
    )

    assert cli.main(["--pr", "49156", "--request", "Review"]) == 1

    output = capsys.readouterr().err
    assert "catalog_build_failed" in output
    assert "free disk and retry" in output


def test_cli_reports_prompt_error_with_remediation(monkeypatch, capsys) -> None:
    error = PromptBuildError(
        "blank request", code="request_missing", fix="provide --request"
    )
    monkeypatch.setattr(
        cli, "generate_prompt", lambda **_kwargs: (_ for _ in ()).throw(error)
    )

    assert cli.main(["--pr", "49156", "--request", "Review"]) == 1

    output = capsys.readouterr().err
    assert "request_missing" in output
    assert "provide --request" in output


def test_cli_reports_local_file_error_with_remediation(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        cli,
        "generate_prompt",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("template is missing")),
    )

    assert cli.main(["--pr", "49156", "--request", "Review"]) == 1

    output = capsys.readouterr().err
    assert "local_file_unavailable" in output
    assert "permissions" in output
