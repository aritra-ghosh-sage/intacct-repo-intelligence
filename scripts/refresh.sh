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
# Compatibility default: callers can override this repo key explicitly.
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

exec "$PYTHON_BIN" -m scripts.refresh_workspace --db "$DB" --manifest "$MANIFEST" --repo "$REPO_KEY" --mode full
