from __future__ import annotations

import sqlite3
from pathlib import Path

from catalog.repo_v1 import build_ia_main
from tests.test_repo_v1_entities import _fixture

_APPLICABILITY_QUERY = """
WITH applicability AS (
    SELECT
        eo.repo_id AS repo_id,
        en.name AS entity_name,
        eo.id AS occurrence_id,
        eo.source_file_id AS entity_source_file_id,
        eo.source_key AS entity_source_key,
        eo.source_commit_sha AS entity_source_commit_sha,
        eo.evidence AS entity_evidence,
        eo.extractor AS entity_extractor,
        'database_table' AS applicability_kind,
        l.id AS fact_id,
        l.resolution_status AS resolution_status,
        l.entity_table AS declared_name,
        l.db_table_id AS database_id,
        t.table_name AS database_name,
        l.source_file_id AS applicability_source_file_id,
        l.source_path AS source_path,
        l.source_hash AS source_hash,
        l.source_pointer AS source_pointer,
        l.start_line AS start_line,
        l.end_line AS end_line,
        l.evidence AS applicability_evidence,
        l.source_commit_sha AS source_commit_sha,
        l.extractor AS extractor,
        l.extractor_version AS extractor_version,
        r.repo_key AS repo_key,
        r.target_commit_sha AS repo_target_commit_sha,
        cb.id AS active_build_id,
        cb.build_token AS active_build_token,
        cb.source_revisions_json AS active_source_revisions_json
    FROM entity_occurrences AS eo
    JOIN entity_nodes AS en
      ON en.id = eo.entity_id
    JOIN entity_db_table_links AS l
      ON l.repo_id = eo.repo_id
     AND l.occurrence_id = eo.id
    LEFT JOIN dbschema_tables AS t
      ON t.repo_id = l.repo_id
     AND t.id = l.db_table_id
    JOIN repos AS r
      ON r.id = eo.repo_id
    JOIN catalog_builds AS cb
      ON cb.id = r.build_id
     AND cb.status = 'active'
    UNION ALL
    SELECT
        eo.repo_id AS repo_id,
        en.name AS entity_name,
        eo.id AS occurrence_id,
        eo.source_file_id AS entity_source_file_id,
        eo.source_key AS entity_source_key,
        eo.source_commit_sha AS entity_source_commit_sha,
        eo.evidence AS entity_evidence,
        eo.extractor AS entity_extractor,
        'database_field' AS applicability_kind,
        l.id AS fact_id,
        l.resolution_status AS resolution_status,
        l.target_field AS declared_name,
        l.db_field_id AS database_id,
        f.field_name AS database_name,
        l.source_file_id AS applicability_source_file_id,
        l.source_path AS source_path,
        l.source_hash AS source_hash,
        l.source_pointer AS source_pointer,
        l.start_line AS start_line,
        l.end_line AS end_line,
        l.evidence AS applicability_evidence,
        l.source_commit_sha AS source_commit_sha,
        l.extractor AS extractor,
        l.extractor_version AS extractor_version,
        r.repo_key AS repo_key,
        r.target_commit_sha AS repo_target_commit_sha,
        cb.id AS active_build_id,
        cb.build_token AS active_build_token,
        cb.source_revisions_json AS active_source_revisions_json
    FROM entity_occurrences AS eo
    JOIN entity_nodes AS en
      ON en.id = eo.entity_id
    JOIN entity_db_field_links AS l
      ON l.repo_id = eo.repo_id
     AND l.occurrence_id = eo.id
    LEFT JOIN dbschema_fields AS f
      ON f.repo_id = l.repo_id
     AND f.id = l.db_field_id
    JOIN repos AS r
      ON r.id = eo.repo_id
    JOIN catalog_builds AS cb
      ON cb.id = r.build_id
     AND cb.status = 'active'
)
SELECT
    repo_id,
    entity_name,
    occurrence_id,
    entity_source_file_id,
    entity_source_key,
    entity_source_commit_sha,
    entity_evidence,
    entity_extractor,
    applicability_kind,
    fact_id,
    resolution_status,
    declared_name,
    database_id,
    database_name,
    applicability_source_file_id,
    source_path,
    source_hash,
    source_pointer,
    start_line,
    end_line,
    applicability_evidence,
    source_commit_sha,
    extractor,
    extractor_version,
    repo_key,
    repo_target_commit_sha,
    active_build_id,
    active_build_token,
    active_source_revisions_json
FROM applicability
WHERE repo_id = :repo_id
  AND (:entity_name IS NULL OR entity_name = :entity_name)
ORDER BY entity_name, occurrence_id, applicability_kind, fact_id
"""


def _applicability_rows(
    conn: sqlite3.Connection,
    *,
    repo_id: int,
    entity_name: str | None = None,
) -> list[sqlite3.Row]:
    return conn.execute(
        _APPLICABILITY_QUERY,
        {"repo_id": repo_id, "entity_name": entity_name},
    ).fetchall()


def _applicability_fixture(tmp_path: Path) -> tuple[Path, str, str]:
    _root, manifest, target = _fixture(
        tmp_path,
        {
            "app/source/common/dbschema.inc": """<?php
$kTables = array(
  'resolved_table' => array(
    'db_fieldinfo' => array('record#' => array('type' => 'integer')),
  ),
);
""",
            "app/source/apar/resolved.ent": """<?php
$kSchemas['Resolved'] = array(
  'table' => 'resolved_table',
  'schema' => array('RECORDNO' => 'record#'),
);
""",
            "app/source/apar/unresolved_table.ent": """<?php
$kSchemas['UnresolvedTable'] = array('table' => 'resolved_tabl');
""",
            "app/source/apar/unresolved_field.ent": """<?php
$kSchemas['UnresolvedField'] = array(
  'table' => 'resolved_table',
  'schema' => array('MISSING' => 'missing_field'),
);
""",
            "app/source/apar/first_duplicate.ent": """<?php
$kSchemas['Duplicate'] = array('table' => 'resolved_table');
""",
            "app/source/apar/second_duplicate.ent": """<?php
$kSchemas['Duplicate'] = array('table' => 'resolved_table');
""",
            "app/source/apar/unlinked.ent": """<?php
$kSchemas['Unlinked'] = array('module' => 'accounts-payable');
""",
        },
    )
    return manifest, target, "resolved_table"


def _repo_id(conn: sqlite3.Connection) -> int:
    return int(
        conn.execute("SELECT id FROM repos WHERE repo_key='ia-main'").fetchone()[0]
    )


def test_applicability_query_returns_resolved_table_and_field_rows(
    tmp_path: Path,
) -> None:
    manifest, target, _ = _applicability_fixture(tmp_path)
    db = tmp_path / "catalog.db"
    build_ia_main(manifest_path=manifest, active_db=db, target_sha=target)

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        rows = _applicability_rows(conn, repo_id=_repo_id(conn), entity_name="Resolved")
        assert {
            (row["applicability_kind"], row["resolution_status"]) for row in rows
        } == {
            ("database_table", "resolved"),
            ("database_field", "resolved"),
        }
        by_kind = {row["applicability_kind"]: row for row in rows}
        assert by_kind["database_table"]["database_name"] == "resolved_table"
        assert by_kind["database_field"]["database_name"] == "record#"
        assert all(row["database_id"] is not None for row in rows)
        assert all(row["entity_name"] == "Resolved" for row in rows)
    finally:
        conn.close()


def test_applicability_query_preserves_unresolved_links_without_inference(
    tmp_path: Path,
) -> None:
    manifest, target, _ = _applicability_fixture(tmp_path)
    db = tmp_path / "catalog.db"
    build_ia_main(manifest_path=manifest, active_db=db, target_sha=target)

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        table_rows = _applicability_rows(
            conn, repo_id=_repo_id(conn), entity_name="UnresolvedTable"
        )
        assert len(table_rows) == 1
        assert table_rows[0]["applicability_kind"] == "database_table"
        assert table_rows[0]["declared_name"] == "resolved_tabl"
        assert table_rows[0]["resolution_status"] == "unresolved"
        assert table_rows[0]["database_id"] is None
        assert table_rows[0]["database_name"] is None

        field_rows = _applicability_rows(
            conn, repo_id=_repo_id(conn), entity_name="UnresolvedField"
        )
        assert {row["applicability_kind"] for row in field_rows} == {
            "database_table",
            "database_field",
        }
        unresolved_field = next(
            row for row in field_rows if row["applicability_kind"] == "database_field"
        )
        assert unresolved_field["declared_name"] == "missing_field"
        assert unresolved_field["resolution_status"] == "unresolved"
        assert unresolved_field["database_id"] is None
        assert unresolved_field["database_name"] is None

        all_rows = _applicability_rows(conn, repo_id=_repo_id(conn))
        assert {row["resolution_status"] for row in all_rows} <= {
            "resolved",
            "unresolved",
        }
    finally:
        conn.close()


def test_applicability_query_keeps_same_name_occurrences_distinct(
    tmp_path: Path,
) -> None:
    manifest, target, _ = _applicability_fixture(tmp_path)
    db = tmp_path / "catalog.db"
    build_ia_main(manifest_path=manifest, active_db=db, target_sha=target)

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        rows = _applicability_rows(
            conn, repo_id=_repo_id(conn), entity_name="Duplicate"
        )
        assert len(rows) == 2
        assert {row["entity_name"] for row in rows} == {"Duplicate"}
        assert len({row["occurrence_id"] for row in rows}) == 2
        assert {row["source_path"] for row in rows} == {
            "app/source/apar/first_duplicate.ent",
            "app/source/apar/second_duplicate.ent",
        }
        assert all(row["fact_id"] is not None for row in rows)
    finally:
        conn.close()


def test_applicability_query_retains_provenance_and_integrity_checks_pass(
    tmp_path: Path,
) -> None:
    manifest, target, _ = _applicability_fixture(tmp_path)
    db = tmp_path / "catalog.db"
    build_ia_main(manifest_path=manifest, active_db=db, target_sha=target)

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        repo_id = _repo_id(conn)
        rows = _applicability_rows(conn, repo_id=repo_id, entity_name="Resolved")
        required = {
            "occurrence_id",
            "entity_name",
            "fact_id",
            "resolution_status",
            "entity_source_file_id",
            "entity_source_key",
            "entity_source_commit_sha",
            "entity_evidence",
            "entity_extractor",
            "applicability_source_file_id",
            "source_path",
            "source_hash",
            "source_pointer",
            "start_line",
            "end_line",
            "applicability_evidence",
            "source_commit_sha",
            "extractor",
            "extractor_version",
            "repo_key",
            "repo_target_commit_sha",
            "active_build_id",
            "active_build_token",
            "active_source_revisions_json",
        }
        assert required <= set(rows[0].keys())
        assert all(row["source_path"].endswith("resolved.ent") for row in rows)
        assert all(row["source_pointer"] for row in rows)
        assert all(row["source_commit_sha"] == target for row in rows)
        assert all(row["extractor"] == "repo_v1_database_v1" for row in rows)
        assert all(row["entity_source_commit_sha"] == target for row in rows)
        assert all(row["repo_key"] == "ia-main" for row in rows)
        assert all(row["repo_target_commit_sha"] == target for row in rows)
        assert all(row["active_build_id"] is not None for row in rows)
        assert all(row["active_build_token"] for row in rows)
        assert all(row["active_source_revisions_json"] for row in rows)

        assert _applicability_rows(conn, repo_id=repo_id, entity_name="Unlinked") == []
        assert (
            _applicability_rows(conn, repo_id=repo_id, entity_name="resolved_table")
            == []
        )
        assert not any(
            row["database_name"] == "resolved_table"
            and row["declared_name"] == "resolved_tabl"
            for row in _applicability_rows(conn, repo_id=repo_id)
        )

        conn.execute("PRAGMA foreign_keys = ON")
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()
