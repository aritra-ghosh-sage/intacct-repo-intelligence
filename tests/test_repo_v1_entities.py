from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from catalog import repo_v1_entities
from catalog.repo_v1 import SCHEMA_PATH, RepoV1Error, _validate_candidate, build_ia_main


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _fixture(tmp_path: Path, files: dict[str, str]) -> tuple[Path, Path, str]:
    root = tmp_path / "source"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.test")
    _git(root, "config", "user.name", "Test")
    for relative, source in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "entity fixture")
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


def _entity_rows(db: Path) -> list[tuple]:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(
            """SELECT f.path,eo.source_key,eo.module,eo.table_name,eo.view_name,eo.dummy,
                      eo.source_commit_sha,eo.evidence,eo.extractor
               FROM entity_occurrences eo JOIN files f ON f.id=eo.source_file_id
               ORDER BY f.path,eo.source_key"""
        ).fetchall()
    finally:
        conn.close()


def _diagnostics(db: Path) -> list[tuple]:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(
            """SELECT f.path,d.source_key,d.code,d.message,d.evidence,d.extractor
               FROM entity_diagnostics d JOIN files f ON f.id=d.file_id
               ORDER BY f.path,d.source_key,d.code,d.diagnostic_key"""
        ).fetchall()
    finally:
        conn.close()


def test_source_shaped_identity_metadata_comments_and_escaped_keys(tmp_path: Path) -> None:
    _root, manifest, target = _fixture(
        tmp_path,
        {
            "app/source/apar/appostedpayment.ent": r'''<?php
// $kSchemas['commented'] = [];
$quoted = "not $kSchemas['a']";
$kSchemas['appostedpayment'] = array(
    'module' => 'accounts-payable',
    'table' => 'appostedpayment',
    'view' => 'posted_view',
    'dummy' => true,
);
$kSchemas['appostedpayment']['module'] = 'accounts-payable';
$kSchemas['appostedpayment']['table'] = 'appostedpayment';
$kSchemas['single\'quote'] = ['module' => 'm'];
$kSchemas["double\"quote"] = array('module' => 'n');
$kSchemas['appostedpayment']['fieldinfo'] = array('text' => "$kSchemas['not-an-identity']");
''',
            "app/source/apar/releasetopay.ent": "<?php\n$kSchemas['releasetopay'] = [];\n",
        },
    )
    db = tmp_path / "catalog.db"
    build_ia_main(manifest_path=manifest, active_db=db, target_sha=target)

    rows = _entity_rows(db)
    by_key = {row[1]: row for row in rows}
    assert by_key["appostedpayment"][2:6] == (
        "accounts-payable",
        "appostedpayment",
        "posted_view",
        1,
    )
    assert {"appostedpayment", "releasetopay", "single'quote", 'double"quote'} <= set(by_key)
    assert all(row[6] == target and row[8] == "repo_v1_entities_v1" for row in rows)
    assert not any(row[1] == "commented" for row in rows)


def test_multiple_files_repeated_keys_and_partial_metadata_are_source_backed(tmp_path: Path) -> None:
    _root, manifest, target = _fixture(
        tmp_path,
        {
            "app/source/apar/one.ent": "<?php\n$kSchemas['same'] = ['module'=>'m'];\n$kSchemas['same']['module']='m';\n$kSchemas['one'] = ['table'=>'t'];\n",
            "app/source/apar/two.ent": "<?php\n$kSchemas['same'] = ['module'=>'m'];\n",
        },
    )
    db = tmp_path / "catalog.db"
    build_ia_main(manifest_path=manifest, active_db=db, target_sha=target)
    rows = _entity_rows(db)
    same = [row for row in rows if row[1] == "same"]
    assert len(same) == 2
    assert len({row[0] for row in same}) == 2
    one = next(row for row in rows if row[1] == "one")
    assert one[2:6] == (None, "t", None, None)
    missing = [row for row in _diagnostics(db) if row[1] == "one" and row[2] == "entity_metadata_missing"]
    assert len(missing) == 1
    assert json.loads(missing[0][3])["missing"] == ["dummy", "module", "view"]
    assert all(row[6] == target for row in rows)


@pytest.mark.parametrize(
    "source",
    [
        "<?php\n$kSchemas[$dynamic] = [];\n",
        "<?php\n$kSchemas[''] = [];\n",
        "<?php\n$kSchemas['bad\\nkey'] = [];\n",
    ],
)
def test_invalid_keys_emit_diagnostics_without_identity_facts(tmp_path: Path, source: str) -> None:
    _root, manifest, _target = _fixture(tmp_path, {"app/source/apar/bad.ent": source})
    db = tmp_path / "catalog.db"
    build_ia_main(manifest_path=manifest, active_db=db)
    assert _entity_rows(db) == []
    codes = [row[2] for row in _diagnostics(db)]
    assert "entity_identity_invalid" in codes


def test_malformed_lexical_state_suppresses_all_file_facts(tmp_path: Path) -> None:
    _root, manifest, _target = _fixture(
        tmp_path,
        {
            "app/source/apar/bad.ent": "<?php\n$kSchemas['before'] = [];\n/* never closes\n$kSchemas['after'] = [];\n",
        },
    )
    db = tmp_path / "catalog.db"
    build_ia_main(manifest_path=manifest, active_db=db)
    assert _entity_rows(db) == []
    diagnostics = _diagnostics(db)
    assert [row[2] for row in diagnostics] == ["entity_identity_invalid"]


def test_rhs_references_inherit_ent_and_literal_include_are_snapshot_resolved(tmp_path: Path) -> None:
    _root, manifest, target = _fixture(
        tmp_path,
        {
            "app/source/apar/base.ent": "<?php\n$kSchemas['base'] = ['module'=>'m','table'=>'base','dummy'=>false];\n",
            "app/source/apar/other.ent": "<?php\n$kSchemas['other'] = ['view'=>'v','table'=>'other'];\n",
            "app/source/apar/appostedpayment.ent": "<?php\nrequire 'base.ent';\n$kSchemas['appostedpayment'] = $kSchemas['base'];\n$kSchemas['release'] = EntityManager::inheritEnts($kSchemas['base'], $kSchemas['other']);\n",
            "app/source/apar/releasetopay.ent": "<?php\n$kSchemas['releasetopay'] = [];\n",
        },
    )
    db = tmp_path / "catalog.db"
    build_ia_main(manifest_path=manifest, active_db=db, target_sha=target)
    by_key = {row[1]: row for row in _entity_rows(db)}
    assert by_key["appostedpayment"][2:6] == ("m", "base", None, 0)
    assert by_key["release"][2:6] == ("m", None, "v", 0)
    assert not any(row[2] in {"entity_reference_missing", "entity_reference_dynamic", "entity_reference_ambiguous", "entity_reference_cycle"} for row in _diagnostics(db) if row[1] in {"appostedpayment", "release"})


def test_repeated_full_assignments_process_later_reference(tmp_path: Path) -> None:
    _root, manifest, _target = _fixture(
        tmp_path,
        {
            "app/source/apar/base.ent": "<?php\n$kSchemas['base'] = ['module'=>'m','table'=>'base','dummy'=>false];\n",
            "app/source/apar/repeated.ent": "<?php\n$kSchemas['repeated'] = [];\n$kSchemas['repeated'] = EntityManager::inheritEnts($kSchemas['base']);\n",
        },
    )
    db = tmp_path / "catalog.db"
    build_ia_main(manifest_path=manifest, active_db=db)
    row = next(row for row in _entity_rows(db) if row[1] == "repeated")
    assert row[2:6] == ("m", "base", None, 0)
    assert not any(row[1] == "repeated" and row[2].startswith("entity_reference_") for row in _diagnostics(db))


def test_include_and_reference_failures_are_explicit(tmp_path: Path) -> None:
    _root, manifest, _target = _fixture(
        tmp_path,
        {
            "app/source/apar/one.ent": "<?php\nrequire 'missing.ent';\n$kSchemas['one'] = $kSchemas['missing'];\n$kSchemas['ambiguous-dest'] = $kSchemas['ambiguous'];\n",
            "app/source/apar/two.ent": "<?php\n$kSchemas['ambiguous'] = [];\n",
            "app/source/apar/three.ent": "<?php\n$kSchemas['ambiguous'] = [];\n$kSchemas['dynamic'] = $kSchemas[$x];\n",
            "app/source/apar/cycle.ent": "<?php\nrequire 'cycle-two.ent';\n$kSchemas['cycle-a'] = $kSchemas['cycle-b'];\n$kSchemas['cycle-b'] = $kSchemas['cycle-a'];\n",
            "app/source/apar/cycle-two.ent": "<?php\nrequire 'cycle.ent';\n$kSchemas['cycle-two'] = [];\n",
            "app/source/apar/dynamic-include.ent": "<?php\nrequire($includePath);\n$kSchemas['dynamic-include'] = [];\n",
        },
    )
    db = tmp_path / "catalog.db"
    build_ia_main(manifest_path=manifest, active_db=db)
    codes = {(row[1], row[2]) for row in _diagnostics(db)}
    assert (None, "entity_include_missing") in codes
    assert ("one", "entity_reference_missing") in codes
    assert ("dynamic", "entity_reference_dynamic") in codes
    assert ("ambiguous-dest", "entity_reference_ambiguous") in codes
    assert ("cycle-a", "entity_reference_cycle") in codes
    assert (None, "entity_include_dynamic") in codes
    assert (None, "entity_include_cycle") in codes


def test_ambiguous_include_resolution_is_fail_closed() -> None:
    target, state = repo_v1_entities._include_target(
        "app/source/apar/current.ent", "shared.ent", {"app/source/apar/shared.ent": [1, 2]}
    )
    assert target == "app/source/apar/shared.ent"
    assert state == "ambiguous"


def test_conflicts_dynamic_metadata_and_evidence_are_deterministic(tmp_path: Path) -> None:
    _root, manifest, target = _fixture(
        tmp_path,
        {
            "app/source/apar/conflict.ent": "<?php\n$kSchemas['conflict'] = ['module'=>'a'];\n$kSchemas['conflict']['module'] = 'b';\n$kSchemas['conflict']['table'] = $dynamic;\n",
        },
    )
    first = tmp_path / "first.db"
    second = tmp_path / "second.db"
    build_ia_main(manifest_path=manifest, active_db=first, target_sha=target)
    build_ia_main(manifest_path=manifest, active_db=second, target_sha=target)
    assert _entity_rows(first) == _entity_rows(second)
    assert _diagnostics(first) == _diagnostics(second)
    row = next(row for row in _entity_rows(first) if row[1] == "conflict")
    assert row[2:6] == (None, None, None, None)
    codes = [row[2] for row in _diagnostics(first) if row[1] == "conflict"]
    assert "entity_metadata_conflict" in codes
    assert "entity_metadata_dynamic" in codes
    evidence = json.loads(row[7])
    assert set(evidence) == {"path", "start_line", "start_column", "end_line", "end_column", "text"}
    assert evidence["text"].startswith("$kSchemas['conflict']")
    assert hashlib.sha256(first.read_bytes()).hexdigest() != ""


def test_mixed_literal_and_dynamic_metadata_fails_closed(tmp_path: Path) -> None:
    _root, manifest, _target = _fixture(
        tmp_path,
        {
            "app/source/apar/mixed.ent": "<?php\n$kSchemas['mixed'] = ['module'=>'literal','table'=>'t','view'=>'v','dummy'=>true];\n$kSchemas['mixed']['module'] = $runtime;\n",
        },
    )
    db = tmp_path / "catalog.db"
    build_ia_main(manifest_path=manifest, active_db=db)
    row = next(row for row in _entity_rows(db) if row[1] == "mixed")
    assert row[2:6] == (None, "t", "v", 1)
    assert sum(row[1] == "mixed" and row[2] == "entity_metadata_dynamic" for row in _diagnostics(db)) == 1


def test_duplicate_array_metadata_keys_are_conflicts(tmp_path: Path) -> None:
    _root, manifest, _target = _fixture(
        tmp_path,
        {
            "app/source/apar/duplicate.ent": "<?php\n$kSchemas['duplicate'] = ['module'=>'a','module'=>'b','table'=>'t','view'=>'v','dummy'=>false];\n",
        },
    )
    db = tmp_path / "catalog.db"
    build_ia_main(manifest_path=manifest, active_db=db)
    row = next(row for row in _entity_rows(db) if row[1] == "duplicate")
    assert row[2:6] == (None, "t", "v", 0)
    assert sum(row[1] == "duplicate" and row[2] == "entity_metadata_conflict" for row in _diagnostics(db)) == 1


def test_dynamic_nested_key_does_not_create_identity_fact(tmp_path: Path) -> None:
    _root, manifest, _target = _fixture(
        tmp_path,
        {
            "app/source/apar/dynamic-nested.ent": "<?php\n$field = 'module';\n$kSchemas['not-an-identity'][$field] = 'value';\n",
        },
    )
    db = tmp_path / "catalog.db"
    build_ia_main(manifest_path=manifest, active_db=db)
    assert _entity_rows(db) == []
    assert any(row[2] == "entity_identity_missing" for row in _diagnostics(db))


def test_dirty_checkout_does_not_change_entity_facts(tmp_path: Path) -> None:
    root, manifest, target = _fixture(
        tmp_path,
        {"app/source/apar/appostedpayment.ent": "<?php\n$kSchemas['appostedpayment'] = ['module'=>'committed'];\n"},
    )
    first = tmp_path / "first.db"
    second = tmp_path / "second.db"
    build_ia_main(manifest_path=manifest, active_db=first, target_sha=target)
    (root / "app/source/apar/appostedpayment.ent").write_text(
        "<?php\n$kSchemas['appostedpayment'] = ['module'=>'dirty'];\n$kSchemas['untracked'] = [];\n",
        encoding="utf-8",
    )
    build_ia_main(manifest_path=manifest, active_db=second, target_sha=target)
    assert _entity_rows(first) == _entity_rows(second)


def test_candidate_validation_rejects_entity_ownership_and_provenance(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.db"
    conn = sqlite3.connect(candidate)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA_PATH.read_text())
    build_id = conn.execute(
        "INSERT INTO catalog_builds(build_token,catalog_path,status,source_revisions_json) VALUES(?,?,?,?)",
        ("token", str(candidate), "validated", '{"ia-main":"target"}'),
    ).lastrowid
    repo_id = conn.execute(
        "INSERT INTO repos(repo_key,local_root,tracked_branch,target_commit_sha,build_id) VALUES(?,?,?,?,?)",
        ("ia-main", str(tmp_path), "main", "target", build_id),
    ).lastrowid
    file_id = conn.execute(
        "INSERT INTO files(repo_id,path,blob_object_id,file_mode,size_bytes,language,source_commit_sha) VALUES(?,?,?,?,?,?,?)",
        (repo_id, "bad.php", "blob", 0o100644, 1, "php", "target"),
    ).lastrowid
    node_id = conn.execute("INSERT INTO entity_nodes(name) VALUES('Bad')").lastrowid
    conn.execute(
        """INSERT INTO entity_occurrences(repo_id,entity_id,source_file_id,source_key,source_commit_sha,evidence,extractor)
           VALUES(?,?,?,?,?,?,?)""",
        (repo_id, node_id, file_id, "Bad", "target", "{}", "repo_v1_entities_v1"),
    )
    conn.commit()
    conn.close()
    with pytest.raises(RepoV1Error, match="entity ownership"):
        _validate_candidate(candidate, target_commit_sha="target", build_token="token")


def test_entity_candidate_failure_preserves_active_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _root, manifest, target = _fixture(
        tmp_path,
        {
            "app/source/apar/appostedpayment.ent": "<?php\n$kSchemas['appostedpayment'] = [];\n",
            "app/source/apar/other.php": "<?php\n",
        },
    )
    active = tmp_path / "active.db"
    build_ia_main(manifest_path=manifest, active_db=active, target_sha=target)
    before = active.read_bytes()
    original = repo_v1_entities.extract_snapshot_entity_occurrences

    def inject_invalid_occurrence(*args: object, **kwargs: object):
        stats = original(*args, **kwargs)
        conn = args[0]
        repo_id = kwargs["repo_id"]
        non_ent_file_id = conn.execute(
            "SELECT id FROM files WHERE repo_id=? AND path='app/source/apar/other.php'",
            (repo_id,),
        ).fetchone()[0]
        conn.execute(
            "UPDATE entity_occurrences SET source_file_id=?,source_key='wrong' WHERE repo_id=?",
            (non_ent_file_id, repo_id),
        )
        return stats

    monkeypatch.setattr(repo_v1_entities, "extract_snapshot_entity_occurrences", inject_invalid_occurrence)
    with pytest.raises(RepoV1Error, match="entity ownership"):
        build_ia_main(manifest_path=manifest, active_db=active, target_sha=target)
    assert active.read_bytes() == before
    assert not list(active.parent.glob(f".{active.name}.candidate.*"))


def test_phase4_file_has_no_legacy_entity_or_mapping_or_jsonl_flow() -> None:
    source = Path(repo_v1_entities.__file__).read_text(encoding="utf-8").lower()
    assert "scan_ent_files" not in source
    assert "build_entities" not in source
    assert "jsonl" not in source
    assert "entity_mappings" not in source
    assert "entity_roots" not in source
