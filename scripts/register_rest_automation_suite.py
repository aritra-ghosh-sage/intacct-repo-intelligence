#!/usr/bin/env python3
"""Validate REST automation evidence paths owned by the workspace manifest.

Suite registration used to write a second repository registry directly into
the active catalog.  Candidate refreshes now use ``repos`` as the sole
identity source, so this command is intentionally validation-only.
"""

from __future__ import annotations

from pathlib import Path

import click


def validate_suite(suite_root: Path, object_mapping: Path) -> str:
    """Return the mapping path relative to its suite root or raise ValueError."""
    root = suite_root.resolve()
    mapping = object_mapping.resolve()
    if not mapping.is_relative_to(root):
        raise ValueError("object_mapping must be located inside suite_root")
    if not mapping.is_file():
        raise ValueError(f"object_mapping file does not exist: {mapping}")
    return mapping.relative_to(root).as_posix()


@click.command()
@click.option("--suite-root", type=click.Path(path_type=Path, exists=True, file_okay=False), required=True)
@click.option("--object-mapping", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
def main(suite_root: Path, object_mapping: Path) -> None:
    """Validate paths before adding them to workspace_repos.yaml."""
    try:
        relative = validate_suite(suite_root, object_mapping)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Validated manifest-owned object mapping: {relative}")


if __name__ == "__main__":
    main()
