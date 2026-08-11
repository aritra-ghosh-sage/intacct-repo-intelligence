from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from catalog.repo_v1 import build_ia_main
from catalog.repo_v1 import RepoV1Error
from tests.test_repo_v1_entities import _fixture


def _database_fixture(tmp_path: Path) -> tuple[Path, Path, str]:
    return _fixture(
        tmp_path,
        {
            "app/source/common/dbschema.inc": """<?php
$kTables = array('apbill' => array('db_fieldinfo' => array('record#' => array('type' => 'integer'))));
""",
            "app/source/apar/apbill.ent": """<?php
$kSchemas['apbill'] = array('table' => 'apbill', 'schema' => array('RECORDNO' => 'record#'));
""",
        },
    )


def _preserved_database_bytes(active: Path) -> dict[Path, bytes]:
    previous = active.with_name(f"{active.name}.previous")
    return {
        path: path.read_bytes()
        for path in (
            active,
            active.with_name(f"{active.name}-wal"),
            active.with_name(f"{active.name}-shm"),
            previous,
            previous.with_name(f"{previous.name}-wal"),
            previous.with_name(f"{previous.name}-shm"),
        )
        if path.exists()
    }


def test_p0_static_database_and_schema_mapping(tmp_path: Path) -> None:
    _root, manifest, target = _fixture(
        tmp_path,
        {
            "app/source/common/dbschema.inc": """<?php
$kTables = array(
  'apbill' => array('primarykey' => array('record#'), 'db_fieldinfo' => array('record#' => array('type' => 'integer'))),
);
""",
            "app/source/apar/apbill.ent": """<?php
$kSchemas['apbill'] = array('table' => 'apbill', 'schema' => array('RECORDNO' => 'record#'), 'fieldinfo' => array(array('fullname' => 'IA.RECORD_NUMBER')));
""",
        },
    )
    db = tmp_path / "catalog.db"
    build_ia_main(manifest_path=manifest, active_db=db, target_sha=target)
    conn = sqlite3.connect(db)
    try:
        assert (
            conn.execute("select table_name from dbschema_tables").fetchone()[0]
            == "apbill"
        )
        assert conn.execute(
            "select target_field,resolution_status from entity_db_field_links"
        ).fetchone() == ("record#", "resolved")
    finally:
        conn.close()


def test_p0_nested_entity_assignments_create_database_links(tmp_path: Path) -> None:
    _root, manifest, target = _fixture(
        tmp_path,
        {
            "app/source/common/dbschema.inc": """<?php
$kTables = array('apbill' => array('db_fieldinfo' => array('record#' => array('type' => 'integer'))));
""",
            "app/source/apar/apbill.ent": """<?php
$kSchemas['apbill'] = array();
$kSchemas['apbill']['table'] = 'apbill';
$kSchemas['apbill']['schema'] = array();
$kSchemas['apbill']['schema']['RECORDNO'] = 'record#';
""",
        },
    )
    db = tmp_path / "catalog.db"
    build_ia_main(manifest_path=manifest, active_db=db, target_sha=target)
    conn = sqlite3.connect(db)
    try:
        assert conn.execute(
            "select entity_table,resolution_status from entity_db_table_links"
        ).fetchone() == ("apbill", "resolved")
        assert conn.execute(
            "select entity_field,target_field,resolution_status from entity_db_field_links"
        ).fetchone() == ("RECORDNO", "record#", "resolved")
    finally:
        conn.close()


def test_database_candidate_validation_failure_preserves_active_and_previous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _root, manifest, target = _database_fixture(tmp_path)
    active = tmp_path / "catalog.db"
    build_ia_main(manifest_path=manifest, active_db=active, target_sha=target)
    build_ia_main(manifest_path=manifest, active_db=active, target_sha=target)
    preserved = _preserved_database_bytes(active)

    def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("injected database validation failure")

    monkeypatch.setattr("catalog.repo_v1.validate_database_candidate", fail)
    with pytest.raises(RepoV1Error, match="injected database validation failure"):
        build_ia_main(manifest_path=manifest, active_db=active, target_sha=target)

    assert _preserved_database_bytes(active) == preserved
    assert not list(active.parent.glob(f".{active.name}.candidate.*"))


def test_database_snapshot_failure_preserves_active_and_previous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _root, manifest, target = _database_fixture(tmp_path)
    active = tmp_path / "catalog.db"
    build_ia_main(manifest_path=manifest, active_db=active, target_sha=target)
    build_ia_main(manifest_path=manifest, active_db=active, target_sha=target)
    preserved = _preserved_database_bytes(active)

    def fail(*_args: object, **_kwargs: object) -> None:
        raise OSError("injected database snapshot failure")

    monkeypatch.setattr("catalog.repo_v1.extract_snapshot_database_facts", fail)
    with pytest.raises(RepoV1Error, match="injected database snapshot failure"):
        build_ia_main(manifest_path=manifest, active_db=active, target_sha=target)

    assert _preserved_database_bytes(active) == preserved
    assert not list(active.parent.glob(f".{active.name}.candidate.*"))
