"""Read-only downstream REST test coverage lookup for PR impact."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from catalog.repositories import RepositoryError, load_workspace_manifest
from catalog.rest_coverage import coverage_rows, coverage_summary

COVERAGE_PAGE_SIZE = 500


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def _gap(
    code: str,
    subject: str,
    status: str,
    consequence: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    result = {
        "gap_code": code,
        "stage": "test_coverage",
        "surface": "tests",
        "subject": subject,
        "status": status,
        "consequence": consequence or "",
    }
    result.update(extra)
    return result


def _manifest_entry(manifest_path: str | Path, repo_key: str) -> dict[str, Any] | None:
    try:
        manifest = load_workspace_manifest(manifest_path)
    except RepositoryError:
        return None
    rows = [
        entry for entry in manifest["repositories"] if entry.get("repo_key") == repo_key
    ]
    return rows[0] if len(rows) == 1 else None


def _repository_key_for_identity(
    manifest_path: str | Path, identity: str
) -> str | None:
    """Resolve a configured repository identity without using checkout names."""
    try:
        manifest = load_workspace_manifest(manifest_path)
    except RepositoryError:
        return None

    def normalize_remote(value: Any) -> str:
        text = str(value or "").strip()
        if text.startswith("git@github.com:"):
            text = text.removeprefix("git@github.com:")
        elif text.startswith("https://github.com/"):
            text = text.removeprefix("https://github.com/")
        elif text.startswith("http://github.com/"):
            text = text.removeprefix("http://github.com/")
        return text.removesuffix(".git").rstrip("/")

    wanted = normalize_remote(identity)
    matches = [
        entry
        for entry in manifest["repositories"]
        if entry.get("repo_key") == identity
        or normalize_remote(entry.get("remote_url")) == wanted
    ]
    return str(matches[0]["repo_key"]) if len(matches) == 1 else None


def analyze_test_coverage(
    manifest_path: str | Path,
    *,
    main_target_revision: str,
    entity_names: list[str],
    catalog_path: str | Path | None = None,
    repo_key: str = "ia-restapi-automation-tests",
) -> dict[str, Any]:
    """Look up exact REST coverage when a legacy coverage catalog is supplied."""
    entry = _manifest_entry(manifest_path, repo_key)
    if entry is None:
        return {
            "status": "unavailable",
            "repository": repo_key,
            "gaps": [
                _gap(
                    "test_repo_contract_missing",
                    repo_key,
                    "missing",
                    "the test repository is not configured",
                )
            ],
        }
    contracts = entry.get("pr_impact_contracts")
    contract = next(
        (
            item
            for item in contracts
            if isinstance(item, Mapping)
            and item.get("type") == "tests_rest_of"
            and isinstance(item.get("target_repository"), str)
        ),
        None,
    ) if isinstance(contracts, list) else None
    if contract is None:
        return {
            "status": "unavailable",
            "repository": repo_key,
            "gaps": [
                _gap(
                    "test_repo_contract_missing",
                    repo_key,
                    "missing",
                    "no tests_rest_of contract targets a configured production repository",
                )
            ],
        }
    target_identity = str(contract["target_repository"])
    target_repo_key = _repository_key_for_identity(manifest_path, target_identity)
    if target_repo_key is None:
        return {
            "status": "unavailable",
            "repository": repo_key,
            "gaps": [
                _gap(
                    "test_target_repo_contract_missing",
                    target_identity,
                    "missing",
                    "the tests_rest_of target cannot be resolved to exactly one configured repository",
                )
            ],
        }
    result: dict[str, Any] = {
        "status": "deferred",
        "repository": repo_key,
        "contract": {"type": "tests_rest_of", "target_repository": target_identity},
        "scope": {
            "endpoint_repository": target_repo_key,
            "test_repository": repo_key,
            "requested_entity_count": len(set(entity_names)),
            "evaluated_entity_count": 0,
        },
        "main_target_revision": main_target_revision,
        "entities": [],
        "gaps": [],
    }
    requested_entities = sorted(set(entity_names))
    if not requested_entities:
        result["gaps"].append(
            _gap(
                "test_coverage_unscoped",
                "entities",
                "deferred",
                consequence="no exact entities were supplied, so zero test surfaces were evaluated",
                remediation="provide reviewed entity mappings from Step 3 before evaluating downstream coverage",
            )
        )
        return result
    if catalog_path is None:
        result["gaps"].append(
            _gap(
                "test_catalog_unavailable",
                repo_key,
                "unavailable",
                "no downstream coverage catalog was supplied",
            )
        )
        return result
    path = Path(catalog_path).expanduser().resolve()
    if not path.is_file():
        result["gaps"].append(
            _gap(
                "test_catalog_unavailable",
                str(path),
                "unavailable",
                "downstream coverage catalog does not exist",
            )
        )
        return result
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        result["gaps"].append(
            _gap("test_catalog_unavailable", str(path), "unavailable", str(exc))
        )
        return result
    try:
        required = {
            "repos",
            "entity_nodes",
            "rest_endpoints",
            "test_cases",
            "test_requests",
            "test_endpoint_links",
            "test_entity_links",
            "test_diagnostics",
            "api_version_compatibility",
            "test_coverage_build_state",
        }
        missing = sorted(required - _tables(conn))
        if missing:
            result["gaps"].append(
                _gap(
                    "test_catalog_schema_missing",
                    ",".join(missing),
                    "unavailable",
                    "coverage schema is incomplete",
                )
            )
            return result
        state = conn.execute(
            """SELECT r.repo_key,s.indexed_suite_target_sha,s.dependency_revisions_json,
                      s.coverage_contract_version
                 FROM test_coverage_build_state s
                 JOIN repos r ON r.id=s.repo_id
                WHERE r.repo_key=?""",
            (repo_key,),
        ).fetchone()
        if state is None:
            result["gaps"].append(
                _gap(
                    "test_coverage_build_missing",
                    repo_key,
                    "unavailable",
                    "no coverage build state exists",
                )
            )
            return result
        repository_rows = conn.execute(
            "SELECT id,repo_key FROM repos WHERE repo_key IN (?,?) ORDER BY repo_key",
            (repo_key, target_repo_key),
        ).fetchall()
        repository_ids = {str(row["repo_key"]): int(row["id"]) for row in repository_rows}
        if set(repository_ids) != {repo_key, target_repo_key}:
            result["gaps"].append(
                _gap(
                    "test_coverage_repository_missing",
                    ",".join(sorted({repo_key, target_repo_key} - set(repository_ids))),
                    "unavailable",
                    consequence="coverage cannot be attributed without both the production endpoint repository and test suite repository",
                    remediation="build both repository identities into the same read-only catalog",
                )
            )
            return result
        try:
            dependencies = json.loads(str(state["dependency_revisions_json"]))
        except json.JSONDecodeError:
            dependencies = {}
        result["build_state"] = {
            "suite_target_revision": state["indexed_suite_target_sha"],
            "dependency_revisions": dependencies,
            "coverage_contract_version": state["coverage_contract_version"],
        }
        if (
            not isinstance(dependencies, Mapping)
            or dependencies.get(target_repo_key) != main_target_revision
        ):
            result["gaps"].append(
                _gap(
                    "test_coverage_stale",
                    repo_key,
                    "stale",
                    "coverage dependency revision does not equal the PR target",
                )
            )
            return result
        for name in requested_entities:
            entity = conn.execute(
                "SELECT id FROM entity_nodes WHERE name=?", (name,)
            ).fetchone()
            if entity is None:
                result["entities"].append(
                    {"entity_name": name, "status": "unresolved", "coverage": []}
                )
                result["gaps"].append(
                    _gap(
                        "test_entity_missing",
                        name,
                        "missing",
                        "test catalog has no exact entity identity",
                    )
                )
                continue
            all_endpoints: list[dict[str, Any]] = []
            diagnostics: list[dict[str, Any]] = []
            offset = 0
            while True:
                page, page_diagnostics = coverage_rows(
                    conn,
                    int(entity["id"]),
                    None,
                    COVERAGE_PAGE_SIZE,
                    endpoint_repo_id=repository_ids[target_repo_key],
                    suite_repo_id=repository_ids[repo_key],
                    offset=offset,
                )
                if offset == 0:
                    diagnostics = page_diagnostics
                all_endpoints.extend(page)
                if len(page) < COVERAGE_PAGE_SIZE:
                    break
                offset += COVERAGE_PAGE_SIZE
            endpoints = all_endpoints
            result["scope"]["evaluated_entity_count"] += 1
            result["entities"].append(
                {
                    "entity_name": name,
                    "status": "confirmed",
                    "coverage": endpoints,
                    "diagnostics": diagnostics,
                    "summary": coverage_summary(endpoints, diagnostics),
                }
            )
            for endpoint in endpoints:
                if endpoint["coverage"] == "uncovered":
                    result["gaps"].append(
                        _gap(
                            "test_endpoint_uncovered",
                            f"{name}:{endpoint['path']}",
                            "missing",
                            "add or update a REST scenario",
                        )
                    )
                elif endpoint["coverage"] in {
                    "known_issue_only",
                    "ci_conditional",
                    "conditional",
                }:
                    result["gaps"].append(
                        _gap(
                            "test_endpoint_weak_coverage",
                            f"{name}:{endpoint['path']}",
                            "unresolved",
                            "review whether active regression coverage is required",
                        )
                    )
        result["status"] = "ready" if not result["gaps"] else "partial"
        return result
    finally:
        conn.close()
