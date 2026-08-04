#!/usr/bin/env python3
"""Read-only operational status for the isolated Gateway sidecar."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def query(db: Path) -> dict:
    connection = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        build = connection.execute("SELECT * FROM gateway_sidecar_builds ORDER BY id DESC LIMIT 1").fetchone()
        if build is None:
            return {"status": "capability_unavailable", "reason": "no_sidecar_build"}
        build_id = int(build["id"])
        definition_count = int(connection.execute("SELECT COUNT(*) FROM gateway_definitions WHERE build_id=?", (build_id,)).fetchone()[0])
        approved_mappings = int(connection.execute("SELECT COUNT(*) FROM gateway_entity_links gel JOIN gateway_definitions gd ON gd.id=gel.definition_id WHERE gd.build_id=?", (build_id,)).fetchone()[0])
        return {"status": "ok", "provenance": dict(build), "definitions": definition_count, "xml": {row[0]: row[1] for row in connection.execute("SELECT parse_status,COUNT(*) FROM gateway_xml_artifacts WHERE build_id=? GROUP BY parse_status", (build_id,))}, "unresolved_references": int(connection.execute("SELECT COUNT(*) FROM gateway_definitions WHERE build_id=? AND reference_state!='resolved'", (build_id,)).fetchone()[0]), "approved_mappings": approved_mappings, "diagnostics": {row[0]: row[1] for row in connection.execute("SELECT code,COUNT(*) FROM gateway_diagnostics WHERE build_id=? GROUP BY code", (build_id,))}}
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("catalog/sidecars/ia-gwdata-gl.db"))
    args = parser.parse_args()
    print(json.dumps(query(args.db), sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
