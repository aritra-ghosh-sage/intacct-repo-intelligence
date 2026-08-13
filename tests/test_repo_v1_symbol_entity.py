from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest
import yaml

from catalog.repo_v1 import RepoV1Error, build_ia_main


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _fixture(tmp_path: Path) -> tuple[Path, Path, str]:
    root = tmp_path / "source"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.test")
    _git(root, "config", "user.name", "Test")
    (root / "app.php").write_text(
        "<?php function changed_symbol() {}\n", encoding="utf-8"
    )
    (root / "thing.ent").write_text(
        "<?php $kSchemas['thing'] = ['module'=>'test','table'=>'thing'];\n",
        encoding="utf-8",
    )
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "fixture")
    target = _git(root, "rev-parse", "HEAD")
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        f"""version: 1
repositories:
  - repo_key: ia-main
    name: Fixture
    kind: monorepo
    language: php
    local_root: {root}
    tracked_branch: main
    builders: []
""",
        encoding="utf-8",
    )
    return root, manifest, target


def test_reviewed_mapping_resolves_by_exact_persisted_identity(tmp_path: Path) -> None:
    _root, manifest, target = _fixture(tmp_path)
    initial = tmp_path / "initial.db"
    build_ia_main(manifest_path=manifest, active_db=initial, target_sha=target)
    conn = sqlite3.connect(initial)
    try:
        stable_key = conn.execute(
            "SELECT stable_key FROM symbols WHERE name='changed_symbol'"
        ).fetchone()[0]
        assert (
            conn.execute("SELECT COUNT(*) FROM symbol_entity_links").fetchone()[0] == 0
        )
    finally:
        conn.close()
    contract = tmp_path / "symbol-entity.yaml"
    contract.write_text(
        yaml.safe_dump(
            {
                "schema_version": "1",
                "repository": "ia-main",
                "target_revision": target,
                "mappings": [
                    {
                        "symbol": {"file_path": "app.php", "stable_key": stable_key},
                        "entity": {"source_path": "thing.ent", "source_key": "thing"},
                        "mapping_type": "reviewed_explicit",
                        "evidence": "review:test-review",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    db = tmp_path / "mapped.db"
    build_ia_main(
        manifest_path=manifest,
        active_db=db,
        target_sha=target,
        mapping_contract=contract,
    )
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            """SELECT symbol_id,entity_occurrence_id,mapping_type,resolution_status,
                      resolution_reason,target_revision,extractor
                 FROM symbol_entity_links"""
        ).fetchone()
        assert row[0] > 0 and row[1] > 0
        assert row[2:] == (
            "reviewed_explicit",
            "resolved",
            "exact_contract_identity",
            target,
            "repo_v1_symbol_entity_v1",
        )
    finally:
        conn.close()


def test_mapping_contract_revision_mismatch_fails_closed(tmp_path: Path) -> None:
    _root, manifest, target = _fixture(tmp_path)
    contract = tmp_path / "stale.yaml"
    contract.write_text(
        yaml.safe_dump(
            {
                "schema_version": "1",
                "repository": "ia-main",
                "target_revision": "0" * 40,
                "mappings": [],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RepoV1Error, match="target_revision"):
        build_ia_main(
            manifest_path=manifest,
            active_db=tmp_path / "stale.db",
            target_sha=target,
            mapping_contract=contract,
        )
