"""Scope validation and Intacct file discovery."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from .config import PHP_FAMILY_EXTENSIONS


def is_intacct_source_path(root: Path, path: Path) -> bool:
    """Return whether a resolved path stays in the repository and is eligible."""

    try:
        relative = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return False
    if path.suffix.lower() != ".map":
        return True
    return relative == "app/source" or relative.startswith("app/source/")


def resolve_scopes(root: Path, scopes: tuple[str, ...]) -> list[Path]:
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"repository root is not a directory: {root}")
    resolved: list[Path] = []
    for raw_scope in scopes:
        candidate = (root / raw_scope).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"scope escapes repository root: {raw_scope}") from exc
        if not candidate.exists():
            raise ValueError(f"scope does not exist: {raw_scope}")
        if (
            not candidate.is_dir()
            and candidate.suffix.lower() not in PHP_FAMILY_EXTENSIONS
        ):
            raise ValueError(f"scope is not a supported Intacct source file: {raw_scope}")
        if not candidate.is_dir() and not is_intacct_source_path(root, candidate):
            raise ValueError(f".map scope is restricted to app/source: {raw_scope}")
        resolved.append(candidate)
    return resolved


def iter_files(root: Path, scopes: tuple[str, ...]) -> Iterator[Path]:
    """Yield regular files deterministically, omitting common build trees."""

    excluded = {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "vendor",
        "build",
        "dist",
        "out",
        "target",
        "__pycache__",
    }
    for scope in resolve_scopes(root, scopes):
        if scope.is_file():
            if (
                scope.suffix.lower() in PHP_FAMILY_EXTENSIONS
                and is_intacct_source_path(root, scope)
            ):
                yield scope
            continue
        for directory, dirnames, filenames in os.walk(scope):
            dirnames[:] = sorted(name for name in dirnames if name not in excluded)
            for filename in sorted(filenames):
                path = Path(directory) / filename
                if (
                    path.is_file()
                    and path.suffix.lower() in PHP_FAMILY_EXTENSIONS
                    and is_intacct_source_path(root, path)
                ):
                    yield path


def relative_path(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()
