"""Non-secret runtime configuration for Greenfield Strands execution."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class StrandsConfigError(ValueError):
    """Raised when Strands runtime configuration is unavailable or unsafe."""


@dataclass(frozen=True)
class StrandsRuntimeConfig:
    """Resolved non-secret settings for the Strands agent runtime."""

    region: str | None = None
    profile: str | None = None
    model: str | None = None
    base_url: str | None = None
    timeout_seconds: int = 300
    max_tokens: int = 32000
    max_continuations: int = 2

    def environment(self) -> dict[str, str]:
        values: dict[str, str] = {}
        if self.region:
            values["AWS_REGION"] = self.region
        if self.profile:
            values["AWS_PROFILE"] = self.profile
        return values


FORBIDDEN_SECRET_KEYS = {
    "aws_access_key_id",
    "aws_secret_access_key",
    "aws_session_token",
    "access_key",
    "secret_key",
    "secret",
    "token",
}


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise StrandsConfigError(f"{label} must be a positive integer")
    return value


def _secret_key_paths(value: Any, path: str = "<root>") -> list[str]:
    if isinstance(value, dict):
        paths: list[str] = []
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if isinstance(key, str) and key.lower() in FORBIDDEN_SECRET_KEYS:
                paths.append(child_path)
            paths.extend(_secret_key_paths(child, child_path))
        return paths
    if isinstance(value, list):
        paths = []
        for index, child in enumerate(value):
            paths.extend(_secret_key_paths(child, f"{path}[{index}]"))
        return paths
    return []


def load_strands_config(path: str | Path | None = None) -> StrandsRuntimeConfig:
    """Load optional non-secret Strands defaults from YAML.

    AWS access keys and secret keys must be supplied through the standard AWS
    credential provider chain, not this repository configuration file.
    """

    data: dict[str, Any] = {}
    if path is not None:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise StrandsConfigError("Strands config must be an object")
        unsafe = sorted(_secret_key_paths(raw))
        if unsafe:
            raise StrandsConfigError(
                "Strands config must not contain secret fields: "
                + ", ".join(unsafe)
            )
        data = raw
    timeout = data.get("timeout_seconds", 300)
    max_tokens = data.get("max_tokens", 32000)
    max_continuations = data.get("max_continuations", 2)
    return StrandsRuntimeConfig(
        region=str(data["region"]).strip() if data.get("region") else None,
        profile=str(data["profile"]).strip() if data.get("profile") else None,
        model=str(data["model"]).strip() if data.get("model") else None,
        base_url=str(data["base_url"]).strip() if data.get("base_url") else None,
        timeout_seconds=_positive_int(timeout, "timeout_seconds"),
        max_tokens=_positive_int(max_tokens, "max_tokens"),
        max_continuations=_positive_int(max_continuations, "max_continuations"),
    )


def credential_status() -> dict[str, object]:
    """Return redacted AWS credential availability for diagnostics."""

    has_env_key = bool(os.environ.get("AWS_ACCESS_KEY_ID"))
    has_env_secret = bool(os.environ.get("AWS_SECRET_ACCESS_KEY"))
    return {
        "aws_profile": os.environ.get("AWS_PROFILE") or None,
        "aws_region": os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION"),
        "env_access_key_present": has_env_key,
        "env_secret_key_present": has_env_secret,
        "env_session_token_present": bool(os.environ.get("AWS_SESSION_TOKEN")),
        "secrets_redacted": True,
    }


def apply_strands_environment(config: StrandsRuntimeConfig) -> None:
    """Apply non-secret AWS environment defaults without overriding callers."""

    for key, value in config.environment().items():
        os.environ.setdefault(key, value)


__all__ = [
    "StrandsConfigError",
    "StrandsRuntimeConfig",
    "apply_strands_environment",
    "credential_status",
    "load_strands_config",
]
