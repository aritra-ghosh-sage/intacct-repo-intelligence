#!/usr/bin/env python3
"""Validate Gateway extraction against the pinned committed corpus."""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sqlite3
import sys
import tempfile
from pathlib import Path, PurePosixPath

try:
    from catalog.delta import verify_clean_committed_checkout
    from scripts.query_gateway_sidecar import query
    from scripts.refresh_gateway_sidecar import (
        _blob,
        _tree,
        build,
    )
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from catalog.delta import verify_clean_committed_checkout
    from scripts.query_gateway_sidecar import query
    from scripts.refresh_gateway_sidecar import (
        _blob,
        _tree,
        build,
    )

PINNED_TARGET = "b159255de66a41e368fd67263f72cbc46761a537"
_QUOTED_METADATA = re.compile(
    r'^"; (?:functional_team|publish_dashboard|contact_email|removal_list|description|baseline_type)\s*='
)


def _oracle_definition_csv_paths(tree: dict[str, str]) -> list[str]:
    """Apply the independently evidenced pinned-corpus inclusion rule."""
    return sorted(
        path
        for path in tree
        if PurePosixPath(path).parent == PurePosixPath("testdefinitions")
        and PurePosixPath(path).suffix.casefold() == ".csv"
        and PurePosixPath(path).name.casefold() != "baselines.csv"
    )


def _resolved_reference_sets(
    repo_root: Path, tree: dict[str, str]
) -> tuple[set[str], set[str]]:
    requests: set[str] = set()
    responses: set[str] = set()
    for source_path in _oracle_definition_csv_paths(tree):
        try:
            text = _blob(repo_root, tree[source_path]).decode(
                "utf-8-sig", errors="strict"
            )
            lines = [
                line
                for line in text.splitlines(keepends=True)
                if not line.startswith(";")
                and _QUOTED_METADATA.match(line) is None
                and line.strip()
            ]
            rows = list(csv.reader(io.StringIO("".join(lines)), strict=True))
        except (csv.Error, UnicodeError):
            continue
        for row in rows:
            if len(row) != 6:
                continue
            for index, destination in ((0, requests), (1, responses)):
                raw = row[index].strip().replace("\\", "/")
                candidate = PurePosixPath(raw)
                if (
                    not raw
                    or candidate.is_absolute()
                    or ".." in candidate.parts
                    or candidate.suffix.casefold() != ".xml"
                ):
                    continue
                resolved = (PurePosixPath("testscripts") / candidate).as_posix()
                if resolved in tree:
                    destination.add(resolved)
    return requests, responses


def probe(
    *,
    repo_root: Path,
    main_root: Path,
    mapping_file: Path,
    target_sha: str = PINNED_TARGET,
) -> dict[str, object]:
    tree = _tree(repo_root, target_sha)
    main_sha = verify_clean_committed_checkout(main_root, "main")
    with tempfile.TemporaryDirectory(prefix="gateway-real-target-") as directory:
        sidecar = Path(directory) / "sidecar.db"
        result = build(
            repo_root=repo_root,
            target_sha=target_sha,
            sidecar_db=sidecar,
            mapping_file=mapping_file,
            ia_main_sha=main_sha,
        )
        status = query(sidecar)
        if result["target_sha"] != PINNED_TARGET:
            raise AssertionError(f"unexpected target SHA: {result['target_sha']}")
        if status["definitions"] != 12945:
            raise AssertionError(
                f"unexpected supported definition count: {status['definitions']}"
            )
        xml_total = sum(int(value) for value in status["xml"].values())
        if xml_total != 11955 or xml_total <= 0:
            raise AssertionError(f"unexpected request artifact count: {xml_total}")
        if status["approved_mappings"] != 0:
            raise AssertionError("empty approved mapping file produced entity links")

        requests, responses = _resolved_reference_sets(repo_root, tree)
        response_only = responses - requests
        connection = sqlite3.connect(sidecar)
        try:
            definition_sources = connection.execute(
                "SELECT DISTINCT source_path,source_blob_sha FROM gateway_definitions"
            ).fetchall()
            artifacts = connection.execute(
                "SELECT source_path,source_blob_sha FROM gateway_xml_artifacts"
            ).fetchall()
            stored_artifact_paths = {str(row[0]) for row in artifacts}
            if stored_artifact_paths != requests:
                raise AssertionError("stored artifacts differ from resolved request set")
            leaked_responses = stored_artifact_paths.intersection(response_only)
            if leaked_responses:
                raise AssertionError(
                    f"response-only artifacts were stored: {len(leaked_responses)}"
                )
            if any(tree.get(str(path)) != str(sha) for path, sha in definition_sources):
                raise AssertionError("definition source SHA does not match target tree")
            if any(tree.get(str(path)) != str(sha) for path, sha in artifacts):
                raise AssertionError("request artifact SHA does not match target tree")
            operation_rows = int(
                connection.execute(
                    "SELECT COUNT(*) FROM gateway_definitions WHERE gateway_operation IS NOT NULL"
                ).fetchone()[0]
            )
            pair_rows = int(
                connection.execute(
                    "SELECT COUNT(*) FROM gateway_definitions WHERE gateway_operation IS NOT NULL AND gateway_object IS NOT NULL"
                ).fetchone()[0]
            )
            if operation_rows <= 0 or pair_rows <= 0:
                raise AssertionError("real target produced no direct Gateway semantics")
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
            if foreign_keys:
                raise AssertionError(f"foreign-key failures: {len(foreign_keys)}")
        finally:
            connection.close()
        return {
            "target_sha": result["target_sha"],
            "definitions": status["definitions"],
            "request_artifacts": xml_total,
            "operation_rows": operation_rows,
            "operation_object_rows": pair_rows,
            "approved_mappings": status["approved_mappings"],
            "diagnostics": status["diagnostics"],
            "response_only_artifacts_stored": 0,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--main-root",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--mapping-file",
        type=Path,
        default=Path("config/gateway_entity_mappings.yaml"),
    )
    parser.add_argument("--target-sha", default=PINNED_TARGET)
    args = parser.parse_args()
    result = probe(
        repo_root=args.repo_root.expanduser().resolve(),
        main_root=args.main_root.expanduser().resolve(),
        mapping_file=args.mapping_file.resolve(),
        target_sha=args.target_sha,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
