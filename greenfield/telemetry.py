"""Redacted, bundle-local telemetry for Greenfield lifecycle diagnostics."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_SECRET = re.compile(r"(?i)(api[_-]?key|authorization|password|secret|token)")


def redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): "[redacted]" if _SECRET.search(str(key)) else redact(child)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [redact(child) for child in value]
    if isinstance(value, str):
        value = re.sub(
            r"(?i)(api[_-]?key|authorization|password|secret|token)(\s*[=:]\s*)\S+",
            r"\1\2[redacted]",
            value,
        )
        for name in ("LLM_API_KEY", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
            secret = os.environ.get(name)
            if secret:
                value = value.replace(secret, "[redacted]")
        return re.sub(r"Bearer\s+\S+", "Bearer [redacted]", value)
    return value


class GreenfieldTelemetry:
    """Append structured run events without copying credentials into artifacts."""

    def __init__(self, output_dir: str | Path) -> None:
        self.path = Path(output_dir) / "telemetry.jsonl"

    def emit(self, event: str, **fields: Any) -> None:
        row = {"event": event, **redact(fields)}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


__all__ = ["GreenfieldTelemetry", "redact"]
