"""Shared repo-local `.env` handling for Greenfield AI/LLM runtimes."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Sequence

DEFAULT_GREENFIELD_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
DEFAULT_GREENFIELD_ENV_EXAMPLE_PATH = (
    Path(__file__).resolve().parents[1] / "config" / "greenfield_llm.example.env"
)
_ENV_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_LOADED_GREENFIELD_ENV_VALUES: dict[str, str] = {}


class GreenfieldEnvError(ValueError):
    """Raised when Greenfield env loading or validation fails."""


def default_greenfield_env_path() -> Path:
    """Return the repo-local `.env` path used by Greenfield runners."""

    return DEFAULT_GREENFIELD_ENV_PATH


def default_greenfield_env_example_path() -> Path:
    """Return the checked-in example env file path."""

    return DEFAULT_GREENFIELD_ENV_EXAMPLE_PATH


def _parse_line(line: str, *, path: Path, line_number: int) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("export "):
        stripped = stripped[len("export ") :].lstrip()
    if "=" not in stripped:
        raise GreenfieldEnvError(
            f"invalid .env line {line_number} in {path}: expected KEY=VALUE syntax"
        )
    key, raw_value = stripped.split("=", 1)
    key = key.strip()
    if not _ENV_KEY_PATTERN.fullmatch(key):
        raise GreenfieldEnvError(
            f"invalid .env key {key!r} on line {line_number} in {path}"
        )
    value = raw_value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    elif " #" in value:
        value = value.split(" #", 1)[0].rstrip()
    return key, value


def load_greenfield_env(path: str | Path | None = None) -> Path:
    """Load repo-local `.env` values into the current process environment.

    Existing shell-provided values win; repo-local values only fill gaps.
    """

    env_path = Path(path) if path is not None else default_greenfield_env_path()
    env_path = env_path.expanduser().resolve()
    if not env_path.is_file():
        return env_path
    parsed_values: dict[str, str] = {}
    for line_number, line in enumerate(
        env_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        parsed = _parse_line(line, path=env_path, line_number=line_number)
        if parsed is None:
            continue
        key, value = parsed
        parsed_values[key] = value
    for key, value in parsed_values.items():
        previous = _LOADED_GREENFIELD_ENV_VALUES.get(key)
        current = os.environ.get(key)
        if current is None or current == previous:
            os.environ[key] = value
            _LOADED_GREENFIELD_ENV_VALUES[key] = value
    for key, previous in list(_LOADED_GREENFIELD_ENV_VALUES.items()):
        if key not in parsed_values and os.environ.get(key) == previous:
            os.environ.pop(key, None)
            del _LOADED_GREENFIELD_ENV_VALUES[key]
    return env_path


def validate_greenfield_llm_env(
    *,
    model: str | None = None,
    base_url: str | None = None,
    env_path: str | Path | None = None,
) -> None:
    """Fail fast when the NexAU/LLM runtime cannot be configured."""

    missing: list[str] = []
    if not os.environ.get("LLM_API_KEY"):
        missing.append("LLM_API_KEY")
    if not (model or os.environ.get("LLM_MODEL")):
        missing.append("LLM_MODEL")
    if not (base_url or os.environ.get("LLM_BASE_URL")):
        missing.append("LLM_BASE_URL")
    if not missing:
        return
    checked = (
        Path(env_path).expanduser().resolve()
        if env_path is not None
        else default_greenfield_env_path()
    )
    example = default_greenfield_env_example_path()
    expected = "\n".join(
        [
            "LLM_API_KEY=...",
            "LLM_MODEL=...",
            "LLM_BASE_URL=https://your-llm-endpoint.example/v1",
        ]
    )
    raise GreenfieldEnvError(
        "Greenfield NexAU/LLM configuration is missing required values: "
        + ", ".join(missing)
        + f".\nChecked .env: {checked}\n"
        f"Copy {example} to {checked} and fill in the missing values, or export them in your shell.\n"
        "Minimum example:\n"
        f"{expected}"
    )


__all__ = [
    "DEFAULT_GREENFIELD_ENV_PATH",
    "DEFAULT_GREENFIELD_ENV_EXAMPLE_PATH",
    "GreenfieldEnvError",
    "default_greenfield_env_example_path",
    "default_greenfield_env_path",
    "load_greenfield_env",
    "validate_greenfield_llm_env",
]
