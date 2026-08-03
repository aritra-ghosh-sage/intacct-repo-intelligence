"""Deterministic fingerprint of evidence-affecting refresh runtime inputs."""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from pathlib import Path

_EXACT_INPUTS = (
    "config.py",
    "pyproject.toml",
    "uv.lock",
    "catalog/schema.sql",
    "scripts/builder_registry.py",
    "scripts/builder_outcome.py",
    "scripts/refresh_workspace.py",
    "validation/validate_catalog_integrity.py",
)
_GLOB_INPUTS = (
    "catalog/**/*.py",
    "parser/**/*.py",
    "migrations/*.sql",
    "scripts/build_*.py",
    "scripts/scan_*.py",
    "scripts/link_*.py",
)


def runtime_input_paths(root: Path | None = None) -> tuple[Path, ...]:
    project_root = (root or Path(__file__).resolve().parents[1]).resolve()
    relative_paths: set[Path] = {Path(value) for value in _EXACT_INPUTS}
    for pattern in _GLOB_INPUTS:
        relative_paths.update(
            path.relative_to(project_root)
            for path in project_root.glob(pattern)
            if path.is_file()
        )
    return tuple(
        project_root / path
        for path in sorted(relative_paths, key=lambda p: p.as_posix())
    )


def runtime_fingerprint(root: Path | None = None) -> str:
    project_root = (root or Path(__file__).resolve().parents[1]).resolve()
    digest = hashlib.sha256()
    runtime = {
        "implementation": sys.implementation.name,
        "implementation_version": platform.python_version(),
        "version": sys.version,
    }
    digest.update(
        json.dumps(
            runtime, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
    )
    digest.update(b"\0")
    for path in runtime_input_paths(project_root):
        relative = path.relative_to(project_root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        if path.is_file():
            data = path.read_bytes()
            digest.update(str(len(data)).encode("ascii"))
            digest.update(b"\0")
            digest.update(data)
        else:
            digest.update(b"missing")
        digest.update(b"\0")
    return digest.hexdigest()
