from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from catalog.api_registry import (
    REGISTRY_SOURCES,
    RegistryExtractionError,
    build_api_registry,
    extract_registry_entries,
    read_registry_entries,
)
from scripts.build_api_registry import build as build_api_registry_standalone

SOURCE_ROOT = Path("/Users/aritra.ghosh/projects/main")


def test_current_registry_corpus_has_the_expected_closed_leaf_counts() -> None:
    if not SOURCE_ROOT.is_dir():
        pytest.skip("Intacct source root is not available")

    entries = read_registry_entries(SOURCE_ROOT)
    assert {release: sum(e.registry_release == release for e in entries) for release, _ in REGISTRY_SOURCES} == {
        "V1": 649,
        "Beta": 1518,
        "V2i": 449,
    }
    assert sum(entry.source_optional for entry in entries) == 18


def test_leaf_payload_and_pointer_are_preserved_deterministically() -> None:
    entries = extract_registry_entries(
        "V1",
        "app/source/api/registries/RegistryV1.json",
        {
            "accounts-payable": {
                "objects": {
                    "bill": {
                        "hash": "abc",
                        "revision": "s1",
                        "type": "rootObject",
                        "runtimeOwner": "php",
                    }
                }
            }
        },
    )

    assert len(entries) == 1
    entry = entries[0]
    assert entry.json_pointer == "/accounts-payable/objects/bill"
    assert entry.resource_path == "bill"
    assert json.loads(entry.payload_json) == {
        "hash": "abc",
        "revision": "s1",
        "runtimeOwner": "php",
        "type": "rootObject",
    }


@pytest.mark.parametrize(
    ("leaf", "expected_problem"),
    (
        ({"revision": "s1"}, "missing required field 'hash'"),
        ({"hash": "abc"}, "missing required field 'revision'"),
        ({"revision": 1, "hash": "abc"}, "field 'revision' must be a string"),
        ({"revision": "s1", "hash": None}, "field 'hash' must be a string"),
    ),
)
def test_incomplete_or_invalid_registry_leaf_fails_closed(
    leaf: dict[str, object], expected_problem: str
) -> None:
    registry_path = "app/source/api/registries/RegistryV1.json"
    document = {"accounts-payable": {"objects": {"bill": leaf}}}

    with pytest.raises(RegistryExtractionError) as exc_info:
        extract_registry_entries("V1", registry_path, document)

    message = str(exc_info.value)
    assert f"{registry_path}/accounts-payable/objects/bill" in message
    assert expected_problem in message


def test_build_is_registry_local_and_requires_component_provenance(tmp_path: Path) -> None:
    root = tmp_path / "main"
    registry_dir = root / "app/source/api/registries"
    registry_dir.mkdir(parents=True)
    payloads = {
        "RegistryV1.json": {
            "accounts-payable": {
                "objects": {"bill": {"revision": "s1", "hash": "a", "type": "rootObject"}}
            }
        },
        "RegistryBeta.json": {
            "company-config": {
                "services": {
                    "settings": {
                        "read": {"revision": "s1", "hash": "b", "type": "functionService"}
                    }
                }
            }
        },
        "RegistryV2i.json": {
            "general-ledger": {
                "workflows": {
                    "journal": {
                        "post": {"revision": "s1", "hash": "c", "type": "workflow"}
                    }
                }
            }
        },
    }
    for filename, payload in payloads.items():
        (registry_dir / filename).write_text(json.dumps(payload), encoding="utf-8")

    conn = sqlite3.connect(tmp_path / "catalog.db")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(Path("catalog/schema.sql").read_text(encoding="utf-8"))
    conn.execute(
        "INSERT INTO repos(repo_key,local_root,tracked_branch) VALUES ('ia-main', ?, 'main')",
        (str(root),),
    )
    repo_id = int(conn.execute("SELECT id FROM repos").fetchone()[0])
    paths = [path for _release, path in REGISTRY_SOURCES]
    paths.extend(
        (
            "app/source/openapispec/ap/models/objects.accounts-payable.bill.s1.schema.yaml",
            "app/source/openapispec/co/paths/services.company-config.settings.read.s1.api.yaml",
            "app/source/openapispec/gl/paths/workflows.general-ledger.journal.post.s1.api.yaml",
        )
    )
    conn.executemany(
        "INSERT INTO files(repo_id,path) VALUES (?,?)",
        ((repo_id, path) for path in paths),
    )
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    stats = build_api_registry(conn, repo_id=repo_id, repo_root=root)
    conn.commit()

    assert stats.entries_written == 3
    assert stats.links_written == 3
    assert stats.issues_written == 0
    assert not stats.diagnostics
    assert conn.execute("SELECT COUNT(*) FROM api_registry_entries").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM api_registry_entry_links").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM rest_endpoints").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM entity_nodes").fetchone()[0] == 0
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []

    registry_file_id = int(
        conn.execute(
            "SELECT id FROM files WHERE repo_id=? AND path=?",
            (repo_id, "app/source/api/registries/RegistryV1.json"),
        ).fetchone()[0]
    )
    conn.execute(
        """INSERT INTO api_registry_issues(
               repo_id, entry_id, source_file_id, source_pointer, issue_key,
               severity, issue_code, message
           ) VALUES (?, NULL, ?, '', 'stale-registry-issue', 'error', 'stale', 'stale')""",
        (repo_id, registry_file_id),
    )
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    rebuilt = build_api_registry(conn, repo_id=repo_id, repo_root=root)
    conn.commit()
    assert rebuilt.issues_written == 0
    assert conn.execute("SELECT COUNT(*) FROM api_registry_issues").fetchone()[0] == 0

    (registry_dir / "RegistryV1.json").write_text(
        json.dumps({"accounts-payable": {"objects": {"bill": {"revision": "s1"}}}}),
        encoding="utf-8",
    )
    conn.execute("BEGIN IMMEDIATE")
    with pytest.raises(RegistryExtractionError, match="invalid Registry leaf"):
        build_api_registry(conn, repo_id=repo_id, repo_root=root)
    assert conn.execute("SELECT COUNT(*) FROM api_registry_entries").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM api_registry_entry_links").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM api_registry_issues").fetchone()[0] == 1
    conn.rollback()
    conn.close()


def test_build_fails_closed_when_a_non_sentinel_has_no_component(tmp_path: Path) -> None:
    root = tmp_path / "main"
    registry_dir = root / "app/source/api/registries"
    registry_dir.mkdir(parents=True)
    for _release, relative_path in REGISTRY_SOURCES:
        (root / relative_path).write_text(
            json.dumps(
                {"accounts-payable": {"objects": {"bill": {"revision": "s1", "hash": "a"}}}}
            ),
            encoding="utf-8",
        )

    conn = sqlite3.connect(tmp_path / "catalog.db")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(Path("catalog/schema.sql").read_text(encoding="utf-8"))
    conn.execute(
        "INSERT INTO repos(repo_key,local_root,tracked_branch) VALUES ('ia-main', ?, 'main')",
        (str(root),),
    )
    repo_id = int(conn.execute("SELECT id FROM repos").fetchone()[0])
    conn.executemany(
        "INSERT INTO files(repo_id,path) VALUES (?,?)",
        ((repo_id, path) for _release, path in REGISTRY_SOURCES),
    )
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    with pytest.raises(RegistryExtractionError, match="no exact source component"):
        build_api_registry(conn, repo_id=repo_id, repo_root=root)
    issue = conn.execute(
        """SELECT issue.entry_id, issue.source_pointer, issue.issue_key,
                  issue.issue_code, issue.severity, issue.details_json, source.path
           FROM api_registry_issues issue
           JOIN files source ON source.id=issue.source_file_id
           WHERE issue.repo_id=?""",
        (repo_id,),
    ).fetchone()
    assert issue is not None
    assert issue["entry_id"] is None
    assert issue["path"] == "app/source/api/registries/RegistryV1.json"
    assert issue["source_pointer"] == "/accounts-payable/objects/bill"
    assert issue["issue_code"] == "unresolved_registry_component"
    assert issue["severity"] == "error"
    assert issue["issue_key"] == (
        "api_registry:V1:app/source/api/registries/RegistryV1.json:"
        "/accounts-payable/objects/bill:unresolved_registry_component"
    )
    assert json.loads(issue["details_json"]) == {
        "module": "accounts-payable",
        "registry_release": "V1",
        "resource_kind": "objects",
        "resource_path": "bill",
        "revision": "s1",
    }
    conn.rollback()
    assert conn.execute("SELECT COUNT(*) FROM api_registry_entries").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM api_registry_entry_links").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM api_registry_issues").fetchone()[0] == 0
    conn.close()


def test_standalone_build_persists_invalid_leaf_as_source_only_issue(tmp_path: Path) -> None:
    root = tmp_path / "main"
    registry_dir = root / "app/source/api/registries"
    registry_dir.mkdir(parents=True)
    (registry_dir / "RegistryV1.json").write_text(
        json.dumps(
            {
                "accounts-payable": {
                    "objects": {"bill": {"revision": "s1"}}
                }
            }
        ),
        encoding="utf-8",
    )
    for _release, relative_path in REGISTRY_SOURCES[1:]:
        (root / relative_path).write_text("{}", encoding="utf-8")

    db_path = tmp_path / "catalog.db"
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(Path("catalog/schema.sql").read_text(encoding="utf-8"))
    conn.execute(
        "INSERT INTO repos(repo_key,local_root,tracked_branch) VALUES ('ia-main', ?, 'main')",
        (str(root),),
    )
    repo_id = int(conn.execute("SELECT id FROM repos").fetchone()[0])
    conn.executemany(
        "INSERT INTO files(repo_id,path) VALUES (?,?)",
        ((repo_id, path) for _release, path in REGISTRY_SOURCES),
    )
    conn.commit()
    conn.close()

    with pytest.raises(RegistryExtractionError, match="invalid Registry leaf"):
        build_api_registry_standalone(str(db_path), root, "ia-main")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    issue = conn.execute(
        """SELECT issue.entry_id, issue.source_pointer, issue.issue_key,
                  issue.issue_code, issue.severity, issue.details_json, source.path
           FROM api_registry_issues issue
           JOIN files source ON source.id=issue.source_file_id
           WHERE issue.repo_id=?""",
        (repo_id,),
    ).fetchone()
    assert issue is not None
    assert issue["entry_id"] is None
    assert issue["path"] == "app/source/api/registries/RegistryV1.json"
    assert issue["source_pointer"] == "/accounts-payable/objects/bill"
    assert issue["issue_code"] == "invalid_registry_leaf"
    assert issue["severity"] == "error"
    assert issue["issue_key"] == (
        "api_registry:V1:app/source/api/registries/RegistryV1.json:"
        "/accounts-payable/objects/bill:invalid_registry_leaf"
    )
    assert json.loads(issue["details_json"]) == {
        "invalid_fields": [],
        "missing_fields": ["hash"],
        "registry_release": "V1",
    }
    assert conn.execute("SELECT COUNT(*) FROM api_registry_entries").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM api_registry_entry_links").fetchone()[0] == 0
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()
