#!/usr/bin/env bash
# Compatibility entry point for the old full-refresh command.
#
# It deliberately delegates to the candidate-based workspace refresh.  It no
# longer deletes catalog.db, runs single-repository parsers, or builds/promotes
# a graph (graph construction remains an operator-owned workflow).
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

DB="catalog/catalog.db"
MANIFEST="config/workspace_repos.yaml"
REPO_KEY="ia-main"
if [[ -z "${PYTHON_BIN:-}" ]]; then
    if [[ -x .venv/bin/python ]]; then
        PYTHON_BIN=".venv/bin/python"
    else
        PYTHON_BIN="python3"
    fi
fi

while [[ $# -gt 0 ]]; do
    case "$1" in
        --db) DB="$2"; shift 2 ;;
        --manifest) MANIFEST="$2"; shift 2 ;;
        --repo) REPO_KEY="$2"; shift 2 ;;
        *) echo "usage: $0 [--db PATH] [--manifest PATH] [--repo KEY]" >&2; exit 2 ;;
    esac
done

if [[ ! -f "$DB" ]]; then
DB="$DB" "$PYTHON_BIN" -c "
import os
from pathlib import Path
from catalog import db
db.CATALOG_DB = Path(os.environ['DB'])
db.init_db()
"
fi

# Apply the table-rebuild migration before registering or refreshing.  The
# manifest supplies the legacy checkout root/branch so no machine-specific
# source path is embedded in this wrapper.
DB="$DB" MANIFEST="$MANIFEST" REPO_KEY="$REPO_KEY" "$PYTHON_BIN" -c "
import os
from catalog.db import migrate_multi_repo
from catalog.repositories import load_workspace_manifest
manifest = load_workspace_manifest(os.environ['MANIFEST'])
entry = next((r for r in manifest['repositories'] if r['repo_key'] == os.environ['REPO_KEY']), None)
if entry is None:
    raise SystemExit('repository not found in manifest: ' + os.environ['REPO_KEY'])
migrate_multi_repo(db_path=os.environ['DB'], local_root=entry['local_root'], tracked_branch=entry['tracked_branch'])
"

exec "$PYTHON_BIN" -m scripts.refresh_workspace --db "$DB" --manifest "$MANIFEST" --repo "$REPO_KEY"
