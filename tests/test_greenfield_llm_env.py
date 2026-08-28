from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

import greenfield.llm_env as llm_env
from greenfield.llm_env import (
    GreenfieldEnvError,
    load_greenfield_env,
    validate_greenfield_llm_env,
)
from greenfield.nexau_planner import run_nexau_planner
from greenfield.strands_config import credential_status
from greenfield.strands_tools import GreenfieldToolbox
from scripts import trace_greenfield_step1_5
from tests.test_greenfield_step1_5 import _trace as build_trace
from tests.test_greenfield_simplified_flow import _context


@pytest.fixture(autouse=True)
def _restore_greenfield_env() -> None:
    keys = (
        "AWS_PROFILE",
        "AWS_REGION",
        "AWS_SESSION_TOKEN",
        "LLM_API_KEY",
        "LLM_MODEL",
        "LLM_BASE_URL",
    )
    snapshot = {key: os.environ.get(key) for key in keys}
    loaded_snapshot = dict(llm_env._LOADED_GREENFIELD_ENV_VALUES)
    yield
    for key, value in snapshot.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    llm_env._LOADED_GREENFIELD_ENV_VALUES.clear()
    llm_env._LOADED_GREENFIELD_ENV_VALUES.update(loaded_snapshot)


def test_load_greenfield_env_ignores_comments_and_preserves_shell_values(
    tmp_path: Path, monkeypatch
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        """
# comment line
export LLM_API_KEY=file-key
LLM_MODEL="file-model"
LLM_BASE_URL=https://file.example/v1
AWS_PROFILE=greenfield
AWS_REGION=us-west-2 # trailing comment
""".strip()
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LLM_API_KEY", "shell-key")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("AWS_REGION", raising=False)

    loaded = load_greenfield_env(env_path)

    assert loaded == env_path.resolve()
    assert load_greenfield_env(env_path) == env_path.resolve()
    assert os.environ["LLM_API_KEY"] == "shell-key"
    assert os.environ["LLM_MODEL"] == "file-model"
    assert os.environ["LLM_BASE_URL"] == "https://file.example/v1"
    assert os.environ["AWS_PROFILE"] == "greenfield"
    assert os.environ["AWS_REGION"] == "us-west-2"


def test_load_greenfield_env_refreshes_file_backed_values(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "LLM_MODEL=first\nLLM_BASE_URL=https://first.example/v1\n",
        encoding="utf-8",
    )

    load_greenfield_env(env_path)
    assert os.environ["LLM_MODEL"] == "first"
    assert os.environ["LLM_BASE_URL"] == "https://first.example/v1"

    env_path.write_text("LLM_MODEL=second\n", encoding="utf-8")
    load_greenfield_env(env_path)
    assert os.environ["LLM_MODEL"] == "second"
    assert "LLM_BASE_URL" not in os.environ


def test_validate_greenfield_llm_env_reports_clear_instructions(
    tmp_path: Path, monkeypatch
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("", encoding="utf-8")
    for key in ("LLM_API_KEY", "LLM_MODEL", "LLM_BASE_URL"):
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(GreenfieldEnvError) as excinfo:
        validate_greenfield_llm_env(env_path=env_path)

    message = str(excinfo.value)
    assert "Greenfield NexAU/LLM configuration is missing required values" in message
    assert "LLM_API_KEY" in message
    assert "LLM_MODEL" in message
    assert "LLM_BASE_URL" in message
    assert str(env_path.resolve()) in message
    assert "config/greenfield_llm.example.env" in message
    assert "Minimum example" in message


def test_shared_env_values_are_available_to_strands_and_nexau(
    tmp_path: Path, monkeypatch
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "AWS_PROFILE=greenfield",
                "AWS_REGION=us-west-2",
                "LLM_API_KEY=shared-key",
                "LLM_MODEL=shared-model",
                "LLM_BASE_URL=https://shared.example/v1",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    for key in (
        "AWS_PROFILE",
        "AWS_REGION",
        "LLM_API_KEY",
        "LLM_MODEL",
        "LLM_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)

    load_greenfield_env(env_path)
    status = credential_status()
    assert status["aws_profile"] == "greenfield"
    assert status["aws_region"] == "us-west-2"

    context, _ = _context(tmp_path)

    def planner_factory(_config: dict[str, object]):
        def runner(_prompt: str) -> str:
            assert os.environ["LLM_API_KEY"] == "shared-key"
            assert os.environ["LLM_MODEL"] == "shared-model"
            assert os.environ["LLM_BASE_URL"] == "https://shared.example/v1"
            return json.dumps(
                {
                    "tasks": [
                        {
                            "task_id": "synth",
                            "task_type": "synthesize_review",
                            "question": "Summarize the evidence.",
                        }
                    ]
                }
            )

        return runner

    report = run_nexau_planner(
        context,
        {"gaps": []},
        GreenfieldToolbox(context),
        mode="shadow",
        planner_factory=planner_factory,
    )

    assert report["analysis"]["agent"]["name"] == "nexau"
    assert report["status"] == "complete"


def test_missing_env_file_does_not_block_step1_5_runner(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    missing_env = tmp_path / ".env"
    monkeypatch.setattr(
        "greenfield.llm_env.DEFAULT_GREENFIELD_ENV_PATH", missing_env, raising=False
    )
    trace_path = tmp_path / "trace.json"
    contract_path = tmp_path / "contract.json"
    config_path = tmp_path / "strands.yaml"
    config_path.write_text(
        "region: us-east-1\nmodel: test-model\ntimeout_seconds: 12\n",
        encoding="utf-8",
    )
    step1_path = (
        Path(__file__).resolve().parents[1]
        / "examples/greenfield/ia-app-pr-49137/replay/step1.json"
    )

    with patch(
        "scripts.trace_greenfield_step1_5.run_strands_trace",
        return_value=(build_trace(), {"context_sha256": "a" * 64}),
    ):
        result = trace_greenfield_step1_5.main(
            [
                "--step1-report",
                str(step1_path),
                "--source-root",
                str(tmp_path),
                "--strands-config",
                str(config_path),
                "--trace-output",
                str(trace_path),
                "--contract-output",
                str(contract_path),
            ]
        )

    assert result == 0
    assert trace_path.exists()
    assert contract_path.exists()
    assert "greenfield Step 1.5 failed" not in capsys.readouterr().err
