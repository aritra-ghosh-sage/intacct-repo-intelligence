from __future__ import annotations

import hashlib
import sqlite3
import subprocess
from pathlib import Path

import pytest

from catalog.repo_v1 import build_ia_main
from catalog.repo_v1 import RepoV1Error
from catalog.repo_v1_database import _key, validate_database_candidate
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


def test_database_evidence_is_exact_bounded_and_source_provenanced(
    tmp_path: Path,
) -> None:
    root, manifest, target = _fixture(
        tmp_path,
        {
            "app/source/common/dbschema.inc": """<?php
// UTF-8 prefix: café
$kTables = array(
  'alpha' => array(
    'db_fieldinfo' => array(
      'record#' => array('type' => 'integer'),
      'amount' => array('type' => 'decimal'),
    ),
  ),
  'beta' => array(
    'db_fieldinfo' => array(
      'status' => array('type' => 'string'),
    ),
  ),
);
""",
            "app/source/apar/alpha.ent": "<?php\n$kSchemas['alpha'] = ['table' => 'alpha'];\n",
            "app/source/apar/beta.ent": "<?php\n$kSchemas['beta'] = ['table' => 'beta'];\n",
        },
    )
    db = tmp_path / "catalog.db"
    build_ia_main(manifest_path=manifest, active_db=db, target_sha=target)

    raw = subprocess.check_output(
        ["git", "-C", str(root), "show", f"{target}:app/source/common/dbschema.inc"]
    )
    text = raw.decode("utf-8")
    lines = text.splitlines()
    full_hash = hashlib.sha256(raw).hexdigest()
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        tables = conn.execute(
            "SELECT * FROM dbschema_tables ORDER BY table_name"
        ).fetchall()
        fields = conn.execute(
            "SELECT * FROM dbschema_fields ORDER BY table_name,field_name"
        ).fetchall()
        assert [row["table_name"] for row in tables] == ["alpha", "beta"]
        assert [(row["table_name"], row["field_name"]) for row in fields] == [
            ("alpha", "amount"),
            ("alpha", "record#"),
            ("beta", "status"),
        ]
        assert len({row["evidence"] for row in tables}) == 2
        assert len({row["evidence"] for row in fields}) == 3
        for row in [*tables, *fields]:
            assert row["evidence"]
            assert len(row["evidence"]) < len(text)
            offset = text.index(row["evidence"])
            assert row["start_line"] == text.count("\n", 0, offset) + 1
            assert (
                row["end_line"]
                == text.count("\n", 0, offset + len(row["evidence"]) - 1) + 1
            )
            assert row["source_hash"] == full_hash
            assert row["source_path"] == "app/source/common/dbschema.inc"
            assert row["source_commit_sha"] == target
            assert row["start_line"] <= row["end_line"]
        alpha_table = next(row for row in tables if row["table_name"] == "alpha")
        assert "'alpha' => array(" in alpha_table["evidence"]
        amount = next(row for row in fields if row["field_name"] == "amount")
        assert "'amount' => array('type' => 'decimal')" in amount["evidence"]
        assert alpha_table["fact_key"] == _key(
            "table", alpha_table["source_path"], alpha_table["source_pointer"]
        )
        assert amount["fact_key"] == _key(
            "field", amount["source_path"], amount["source_pointer"]
        )
    finally:
        conn.close()


def test_database_duplicate_assignments_use_final_value_and_span(
    tmp_path: Path,
) -> None:
    _root, manifest, target = _fixture(
        tmp_path,
        {
            "app/source/common/dbschema.inc": """<?php
$kTables = array(
  'same' => array('db_fieldinfo' => array('old' => array('type' => 'string'))),
);
$kTables = array(
  'same' => array('db_fieldinfo' => array('new' => array('type' => 'integer'))),
);
""",
            "app/source/apar/same.ent": "<?php\n$kSchemas['same'] = ['table' => 'same'];\n",
        },
    )
    db = tmp_path / "catalog.db"
    build_ia_main(manifest_path=manifest, active_db=db, target_sha=target)
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT table_name FROM dbschema_tables").fetchall() == [
            ("same",)
        ]
        assert conn.execute(
            "SELECT field_name FROM dbschema_fields ORDER BY field_name"
        ).fetchall() == [("new",)]
        evidence = conn.execute("SELECT evidence FROM dbschema_fields").fetchone()[0]
        assert "'new' =>" in evidence
        assert "'old' =>" not in evidence
    finally:
        conn.close()


def test_direct_entity_table_assignment_keeps_fact_identity_and_local_evidence(
    tmp_path: Path,
) -> None:
    _root, manifest, target = _fixture(
        tmp_path,
        {
            "app/source/common/dbschema.inc": "<?php\n$kTables = array();\n",
            "app/source/ims/imstransportpolicy.ent": """<?php
$kTables['imstransportpolicy'] = array(
  'primarykey' => array('record#'),
  'db_fieldinfo' => array('policyid' => array('type' => 'text')),
);
""",
        },
    )
    db = tmp_path / "catalog.db"
    build_ia_main(manifest_path=manifest, active_db=db, target_sha=target)
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute(
            "SELECT table_name,source_path,source_pointer,fact_key,evidence "
            "FROM dbschema_tables WHERE table_name IN ('primarykey','db_fieldinfo') "
            "ORDER BY table_name"
        ).fetchall()
        assert [row[0] for row in rows] == ["db_fieldinfo", "primarykey"]
        assert all(row[1] == "app/source/ims/imstransportpolicy.ent" for row in rows)
        assert "'db_fieldinfo' =>" in rows[0][4]
        assert "'primarykey' =>" in rows[1][4]
        for table_name, source_path, pointer, fact_key, _evidence in rows:
            assert fact_key == _key("table", "app/source/common/dbschema.inc", pointer)
    finally:
        conn.close()


def test_dynamic_field_map_diagnostic_is_unchanged_and_unique(tmp_path: Path) -> None:
    _root, manifest, target = _fixture(
        tmp_path,
        {
            "app/source/common/dbschema.inc": """<?php
$kTables = array('dynamic' => array('db_fieldinfo' => 'runtime'));
""",
            "app/source/apar/dynamic.ent": "<?php\n$kSchemas['dynamic'] = ['table' => 'dynamic'];\n",
        },
    )
    db = tmp_path / "catalog.db"
    build_ia_main(manifest_path=manifest, active_db=db, target_sha=target)
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM dbschema_fields").fetchone()[0] == 0
        diagnostics = conn.execute(
            "SELECT code,diagnostic_key,message FROM repo_v1_database_diagnostics"
        ).fetchall()
        assert len(diagnostics) == 1
        assert diagnostics[0][0] == "dbschema_dynamic_field_map"
        assert diagnostics[0][2] == "$kTables['dynamic']"
        assert len(
            {
                row[0]
                for row in conn.execute(
                    "SELECT diagnostic_key FROM repo_v1_database_diagnostics"
                )
            }
        ) == len(diagnostics)
    finally:
        conn.close()


def test_dirty_checkout_does_not_change_database_evidence(tmp_path: Path) -> None:
    root, manifest, target = _database_fixture(tmp_path)
    first = tmp_path / "first.db"
    second = tmp_path / "second.db"
    build_ia_main(manifest_path=manifest, active_db=first, target_sha=target)
    (root / "app/source/common/dbschema.inc").write_text(
        "<?php\n$kTables = array('dirty' => array());\n", encoding="utf-8"
    )
    build_ia_main(manifest_path=manifest, active_db=second, target_sha=target)
    columns = (
        "fact_key,table_name,properties_json,primary_keys_json,source_path,"
        "source_commit_sha,source_hash,source_pointer,start_line,end_line,evidence"
    )
    left = sqlite3.connect(first)
    right = sqlite3.connect(second)
    try:
        assert (
            left.execute(
                f"SELECT {columns} FROM dbschema_tables ORDER BY fact_key"
            ).fetchall()
            == right.execute(
                f"SELECT {columns} FROM dbschema_tables ORDER BY fact_key"
            ).fetchall()
        )
    finally:
        left.close()
        right.close()


def test_candidate_rejects_database_evidence_provenance_tampering(
    tmp_path: Path,
) -> None:
    _root, manifest, target = _database_fixture(tmp_path)
    db = tmp_path / "catalog.db"
    build_ia_main(manifest_path=manifest, active_db=db, target_sha=target)
    conn = sqlite3.connect(db)
    try:
        repo_id = conn.execute("SELECT id FROM repos").fetchone()[0]
        conn.execute("UPDATE dbschema_tables SET evidence='' ")
        with pytest.raises(RuntimeError, match="database candidate provenance"):
            validate_database_candidate(conn, repo_id=repo_id, target_commit_sha=target)
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
