#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
runtime_environment="${GREENFIELD_VENV:-${repo_root}/.venv-greenfield}"

if ! command -v uv >/dev/null 2>&1; then
    echo "greenfield runtime bootstrap failed: uv is required" >&2
    exit 2
fi

UV_PROJECT_ENVIRONMENT="${runtime_environment}" \
    uv sync --project "${repo_root}" --locked --no-group dev

echo "Greenfield runtime ready: ${runtime_environment}"
