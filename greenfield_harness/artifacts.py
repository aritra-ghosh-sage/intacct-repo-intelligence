"""Small, self-contained immutable artifact helpers for the harness."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: Mapping[str, Any] | list[Any]) -> Path:
    """Atomically create a harness artifact, never replacing an existing one."""
    if path.exists():
        raise ValueError(f"immutable harness artifact already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".harness-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical_bytes(value))
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return path


def write_text(path: Path, value: str) -> Path:
    if path.exists():
        raise ValueError(f"immutable harness artifact already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    return path


def reference(root: Path, path: Path) -> dict[str, str]:
    return {
        "path": str(path.resolve().relative_to(root.resolve())),
        "sha256": file_sha256(path),
    }
