"""Resolve greenfield Step 2 impact candidates from standalone evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from greenfield.github_repository_evidence import (
    RepositoryEvidenceError,
    collect_repository_evidence,
)
from greenfield.step2_candidates import CandidateError, resolve_candidates
from greenfield.step2_contract import (
    EvidenceError,
    load_ci_evidence,
    load_contract,
    load_repository_inventory,
    load_semantic_index,
    normalize_repository_inventory,
)


def _unavailable_inventory(
    repository: str, source_repository: str, source_revision: str, reason: str
) -> dict[str, object]:
    return normalize_repository_inventory(
        {
            "schema_version": "0.1",
            "evidence_type": "repository_inventory",
            "repository": repository,
            "source_repository": source_repository,
            "source_revision": source_revision,
            "inspected_revision": "0" * 40,
            "workflow_paths": [],
            "inventory_paths": [],
            "workflows": [],
            "workflow_runs": [],
            "check_runs": [],
            "artifacts": [],
            "artifact_status": "empty",
            "status": "unavailable",
            "gaps": [reason],
            "provenance": {
                "endpoints": [],
                "provider": "github_api",
                "response_sha256": "0" * 64,
                "read_only": True,
            },
        },
        path=f"<unavailable:{repository}>",
    )


def _manifest_candidates(path: str | Path, source_repository: str) -> list[str]:
    """Return only explicit pr_impact_contracts consumers from the local manifest."""

    data: Any = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    repositories = data.get("repositories") if isinstance(data, dict) else None
    if not isinstance(repositories, list):
        return []
    source_short = source_repository.rsplit("/", 1)[-1]
    source_short = {"ia-main": "ia-app"}.get(source_short, source_short)
    result: set[str] = set()
    for entry in repositories:
        if not isinstance(entry, dict):
            continue
        for contract in entry.get("pr_impact_contracts", []):
            if not isinstance(contract, dict):
                continue
            target = str(contract.get("target_repository", ""))
            if target.rsplit("/", 1)[-1] != source_short:
                continue
            remote = str(entry.get("remote_url", ""))
            if "github.com:" in remote:
                remote = remote.split("github.com:", 1)[1]
            elif "github.com/" in remote:
                remote = remote.split("github.com/", 1)[1]
            result.add(remote.removesuffix(".git"))
    return sorted(result)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step1-report", required=True)
    parser.add_argument("--contract", action="append", default=[])
    parser.add_argument("--ci-evidence", action="append", default=[])
    parser.add_argument("--inventory-evidence", action="append", default=[])
    parser.add_argument("--semantic-index", action="append", default=[])
    parser.add_argument("--repository", action="append", default=[])
    parser.add_argument("--manifest", default="config/workspace_repos.yaml")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    try:
        step1 = json.loads(Path(args.step1_report).read_text(encoding="utf-8"))
        contracts = [load_contract(path) for path in args.contract]
        ci_evidence = [load_ci_evidence(path) for path in args.ci_evidence]
        inventory = [
            load_repository_inventory(path) for path in args.inventory_evidence
        ]
        semantic_indexes = [load_semantic_index(path) for path in args.semantic_index]
        input_data = step1.get("input", {})
        source_repository = input_data.get("repo_key") or input_data.get("repository")
        source_revision = input_data.get("target_revision") or input_data.get(
            "head_sha"
        )
        if not isinstance(source_repository, str) or not isinstance(
            source_revision, str
        ):
            raise CandidateError(
                "Step 1 source repository and target revision are required"
            )
        requested_repositories = list(args.repository)
        if not requested_repositories:
            requested_repositories = _manifest_candidates(
                args.manifest, source_repository
            )
        for repository in sorted(set(requested_repositories)):
            try:
                inventory.append(
                    normalize_repository_inventory(
                        collect_repository_evidence(
                            repository,
                            source_repository=source_repository,
                            source_revision=source_revision,
                        )
                    )
                )
            except RepositoryEvidenceError as exc:
                inventory.append(
                    _unavailable_inventory(
                        repository, source_repository, source_revision, str(exc)
                    )
                )
        report = resolve_candidates(
            step1, contracts, ci_evidence, inventory, semantic_indexes
        )
    except (OSError, json.JSONDecodeError, CandidateError, EvidenceError) as exc:
        print(f"greenfield Step 2 failed: {exc}", file=sys.stderr)
        return 2
    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
