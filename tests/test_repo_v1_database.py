from __future__ import annotations

import sqlite3
from pathlib import Path

from catalog.repo_v1 import build_ia_main
from tests.test_repo_v1_entities import _fixture


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
