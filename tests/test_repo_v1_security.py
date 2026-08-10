from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from catalog.repo_v1_security import (
    extract_snapshot_security,
    validate_security_candidate,
)
from catalog.source_snapshot import GitTreeEntry, SourceSnapshot


def _fixture(tmp_path: Path, files: dict[str, bytes]):
    root = tmp_path / "snapshot"
    root.mkdir()
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(
        (Path(__file__).parents[1] / "catalog/repo_v1_schema.sql").read_text()
    )
    sha = "a" * 40
    build = conn.execute(
        "INSERT INTO catalog_builds(build_token,catalog_path,status,source_revisions_json) VALUES('b','x','validated',?)",
        ('{"ia-main":"' + sha + '"}',),
    ).lastrowid
    repo = conn.execute(
        "INSERT INTO repos(repo_key,local_root,tracked_branch,target_commit_sha,build_id) VALUES('ia-main',?,'main',?,?)",
        (str(tmp_path), sha, build),
    ).lastrowid
    entries = []
    for path, raw in sorted(files.items()):
        destination = root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
        blob = hashlib.sha1(raw).hexdigest()
        entries.append(GitTreeEntry(path, 0o100644, blob, len(raw)))
        conn.execute(
            "INSERT INTO files(repo_id,path,blob_object_id,file_mode,size_bytes,language,source_commit_sha) VALUES(?,?,?,?,?,?,?)",
            (repo, path, blob, 0o100644, len(raw), "php", sha),
        )
    return conn, SourceSnapshot(
        "ia-main", tmp_path, sha, root, 1, len(entries), tuple(entries)
    )


def test_security_operations_policies_menus_and_resolution(tmp_path: Path) -> None:
    conn, snapshot = _fixture(
        tmp_path,
        {
            "app/source/common/security.inc": b"<?php $kElements=array(array('key'=>'read','id'=>1,'allowops'=>array(2)),array('key'=>'write','id'=>2));",
            "app/source/common/Policies/ap.pol": b"<?php $kPolicy=array('bill'=>array('label'=>'Bill','values'=>array('on'=>array('value'=>'On','eops'=>array('read')))));",
            "app/source/common/Menus/ap.menu": b"<?php $menu=array('BILL'=>array('MENU_KEY'=>'read','MENU_ID'=>'bill'));",
        },
    )
    stats = extract_snapshot_security(conn, repo_id=1, snapshot=snapshot)
    assert stats.operation_count == 2
    assert stats.policy_count == 1
    assert stats.policy_value_count == 1
    assert stats.menu_count == 1
    assert stats.menu_item_count == 1
    assert (
        conn.execute(
            "SELECT resolution_status FROM security_operation_allowops"
        ).fetchone()[0]
        == "resolved"
    )
    assert conn.execute("SELECT COUNT(*) FROM security_menu_items").fetchone()[0] == 1
    assert (
        conn.execute("SELECT COUNT(*) FROM security_menu_op_links").fetchone()[0] == 1
    )
    validate_security_candidate(conn, repo_id=1, target_commit_sha="a" * 40)
    conn.execute("UPDATE security_menu_op_links SET operation_id=2")
    with pytest.raises(RuntimeError):
        validate_security_candidate(conn, repo_id=1, target_commit_sha="a" * 40)


def test_security_nested_menu_items_and_container_traversal(tmp_path: Path) -> None:
    conn, snapshot = _fixture(
        tmp_path,
        {
            "app/source/common/security.inc": b"<?php $kElements=array(array('key'=>'read','id'=>1));",
            "app/source/common/Menus/ap.menu": (
                b"<?php $menu=array("
                b"'Tasks'=>array("
                b"'Resolved'=>array('key'=>'read','script'=>'resolved.phtml'),"
                b"'Missing'=>array('key'=>'missing','script'=>'missing.phtml'),"
                b"'Group'=>array('Nested'=>array('MENU_KEY'=>'read','MENU_SCRIPT'=>'nested.phtml'))"
                b"),"
                b"'Direct'=>array('MENU_KEY'=>'read','MENU_SCRIPT'=>'direct.phtml'),"
                b"'Dynamic'=>array('key'=>$dynamic,'script'=>'dynamic.phtml')"
                b");"
            ),
        },
    )

    stats = extract_snapshot_security(conn, repo_id=1, snapshot=snapshot)

    assert stats.menu_item_count == 5
    assert stats.menu_op_link_count == 4
    items = conn.execute(
        "SELECT item_name,item_path,source_pointer,menu_key FROM security_menu_items "
        "ORDER BY id"
    ).fetchall()
    assert [(row[0], row[1], row[2]) for row in items] == [
        ("Resolved", "0/0", "/menu/0/0"),
        ("Missing", "0/1", "/menu/0/1"),
        ("Nested", "0/2/0", "/menu/0/2/0"),
        ("Direct", "1", "/menu/1"),
        ("Dynamic", "2", "/menu/2"),
    ]
    assert items[-1][3] is None
    assert [
        tuple(row)
        for row in conn.execute(
            "SELECT resolution_status,resolution_reason FROM security_menu_op_links "
            "WHERE op_key='read' ORDER BY id"
        ).fetchall()
    ] == [("resolved", "unique_operation_key")] * 3
    assert tuple(
        conn.execute(
            "SELECT resolution_status,resolution_reason FROM security_menu_op_links "
            "WHERE op_key='missing'"
        ).fetchone()
    ) == ("unresolved", "missing_operation_key")
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM security_diagnostics WHERE code='security.value.dynamic'"
        ).fetchone()[0]
        == 1
    )
    validate_security_candidate(conn, repo_id=1, target_commit_sha="a" * 40)


def test_security_dynamic_reference_and_tamper_rejected(tmp_path: Path) -> None:
    conn, snapshot = _fixture(
        tmp_path,
        {
            "app/source/common/security.inc": b"<?php $x='read'; $kElements=array(array('key'=>'read','allowops'=>array($x)));"
        },
    )
    stats = extract_snapshot_security(conn, repo_id=1, snapshot=snapshot)
    assert stats.allowop_count == 0
    assert (
        conn.execute("SELECT COUNT(*) FROM security_operation_allowops").fetchone()[0]
        == 0
    )
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM security_diagnostics WHERE code='security.value.dynamic'"
        ).fetchone()[0]
        == 1
    )
    conn.execute("UPDATE security_operations SET fact_key=?", ("b" * 64,))
    with pytest.raises(RuntimeError):
        validate_security_candidate(conn, repo_id=1, target_commit_sha="a" * 40)


def test_security_dynamic_policy_value_identity_is_omitted(tmp_path: Path) -> None:
    conn, snapshot = _fixture(
        tmp_path,
        {
            "app/source/common/security.inc": b"<?php $kElements=array(array('key'=>'read','id'=>1));",
            "app/source/common/Policies/ap.pol": b"<?php $kPolicy=array('bill'=>array('values'=>array($dynamic=>array('value'=>'On','eops'=>array('read')))));",
        },
    )
    stats = extract_snapshot_security(conn, repo_id=1, snapshot=snapshot)
    assert stats.policy_count == 1
    assert stats.policy_value_count == 0
    assert stats.policy_eop_count == 0
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM security_diagnostics "
            "WHERE code='security.value.dynamic'"
        ).fetchone()[0]
        == 1
    )
    validate_security_candidate(conn, repo_id=1, target_commit_sha="a" * 40)


def test_security_provenance_evidence_hash_and_diagnostic_key_tampering_rejected(
    tmp_path: Path,
) -> None:
    cases = (
        (
            "evidence",
            "UPDATE security_operations SET evidence=?",
            '{"fact_type":"operation","fields":{}}',
        ),
        ("hash", "UPDATE security_operations SET source_hash=?", "b" * 64),
    )
    for name, statement, value in cases:
        case_root = tmp_path / name
        case_root.mkdir()
        conn, snapshot = _fixture(
            case_root,
            {
                "app/source/common/security.inc": b"<?php $kElements=array(array('key'=>'read','id'=>1));",
            },
        )
        extract_snapshot_security(conn, repo_id=1, snapshot=snapshot)
        conn.execute(statement, (value,))
        with pytest.raises(RuntimeError):
            validate_security_candidate(conn, repo_id=1, target_commit_sha="a" * 40)

    diagnostic_root = tmp_path / "diagnostic"
    diagnostic_root.mkdir()
    conn, snapshot = _fixture(
        diagnostic_root,
        {
            "app/source/common/security.inc": b"<?php $x='read'; $kElements=array(array('key'=>'read','allowops'=>array($x)));",
        },
    )
    extract_snapshot_security(conn, repo_id=1, snapshot=snapshot)
    conn.execute("UPDATE security_diagnostics SET diagnostic_key=?", ("c" * 64,))
    with pytest.raises(RuntimeError):
        validate_security_candidate(conn, repo_id=1, target_commit_sha="a" * 40)
