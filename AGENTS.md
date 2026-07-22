# AGENTS.md

This repository builds an evidence-backed SQLite catalog of the Intacct codebase. Agents should prefer deterministic extraction, preserve provenance, and stop when evidence is missing instead of inferring facts.

## Read First

- [README.md](README.md) for project goals and the evidence-first rules.
- [docs/design/workflows.md](docs/design/workflows.md) for the current workflow model.
- [validation/phase2d1_remediation.md](validation/phase2d1_remediation.md) for the strongest statement of repository quality constraints.

## Environment Setup

- Python requirement: `>=3.12`.
- Install dependencies with `uv sync` if `.venv` is missing or stale.
- Activate the environment with `source .venv/bin/activate`.
- The main working database is `catalog/catalog.db`.

## Required Local Assumptions

- Most scripts assume the source repository lives at `$HOME/projects/main`.
- `config.py` sets `REPO_PATH` to `$HOME/projects/main`.
- Some scripts also hard-code `--repo-root "$HOME/projects/main"` in examples and shell helpers.
- Before running pipeline commands on another machine, verify the source repo path first. If the path differs, update `config.py` and any explicit `--repo-root` arguments you use.
- Repo-root preflight is mandatory before proposing commands: verify the effective path from `config.py` and the current workspace, then echo the exact resolved repo-root path in the response before giving pipeline or scan commands.

## Canonical Commands

- Initialize a fresh database:

```bash
python -c "from catalog.db import init_db; init_db()"
```

- Apply graph metadata migrations to an existing catalog before using safe graph promotion:

```bash
python -c "import sqlite3; from pathlib import Path; c=sqlite3.connect('catalog/catalog.db'); [c.executescript(Path(m).read_text()) for m in ('migrations/017_graph_builds.sql', 'migrations/018_graph_build_status_previous.sql')]; c.close()"
```

- Run the full rebuild pipeline:

```bash
bash scripts/refresh.sh
```

- Run the pipeline stepwise when you need narrower validation:

```bash
python -m parser.scan_repo
python -m parser.extract_symbols --full
python scripts/scan_ent_files.py --repo-root "$HOME/projects/main" --out catalog/entity_definitions.jsonl
python scripts/build_entities.py build --entities catalog/entity_definitions.jsonl --db catalog/catalog.db
python scripts/build_entity_roots.py build
python -m parser.extract_relationships --repo-root "$HOME/projects/main"
python scripts/build_workflows.py build --db catalog/catalog.db --repo-root "$HOME/projects/main"
python scripts/build_security_mappings.py build --db catalog/catalog.db --repo-root "$HOME/projects/main"
python scripts/scan_openapispec.py scan --db catalog/catalog.db --repo-root "$HOME/projects/main"
python scripts/link_openapispec.py link --db catalog/catalog.db
python scripts/build_rest_endpoints.py build --db catalog/catalog.db --repo-root "$HOME/projects/main"
python scripts/build_entity_access_links.py build --db catalog/catalog.db --reset
```

OpenAPI linking order matters:
- Run `scan_openapispec.py` first to refresh `openapispec_index`.
- Run `link_openapispec.py` before `build_rest_endpoints.py` so entity mappings are present when endpoints are materialized.

## Primary Entry Points

- `main.py` is only a placeholder and is not the real application entry point.
- Core catalog code lives in `catalog/` and `parser/`.
- Operational entry points live in `scripts/`.
- Query surfaces for day-to-day inspection are:

```bash
python scripts/query_entity.py entity APBill
python scripts/query_entity.py root-symbols APBill
python scripts/query_relationships.py stats
python scripts/query_security.py op ee/lists/employee
python scripts/query_security.py menu ee/lists/employee
python scripts/query_rest.py
python scripts/query_workflow.py
python scripts/query_catalog.py sql "SELECT COUNT(*) FROM files"
```

Read-only graph query commands:

```bash
python scripts/query_graph.py file-impact app/source/apar/SomeFile.cls
python scripts/query_graph.py entity-context APBill
python scripts/query_graph.py who-uses create --symbol-id 6361
python scripts/query_graph.py security-surface APBill
```

`who-uses` names must be unique; use `--symbol-id` when the command returns ambiguity candidates. Add `--json` for the stable JSON v1 envelope.

## Working Conventions

- Prefer SQLite evidence over prose. If a claim can be checked in `catalog/catalog.db`, check it.
- Preserve provenance. New extracted facts should trace back to a real file, symbol, or source text.
- Precision is more important than recall. Remove misleading data rather than keeping weak mappings.
- Do not treat semantically similar mapping types as interchangeable unless code and validation rules do so explicitly.
- Prefer narrow rebuilds and validation commands before running the full refresh.

## Common Pitfalls

- Path defaults are inconsistent in places. Do not assume every script uses the same repo root without checking.
- For SQL guidance, always verify referenced tables and columns against `catalog/schema.sql` and current migrations before proposing or running queries.
- If a column/table reference is uncertain after schema checks, stop and ask for confirmation instead of inferring a mapping.
- Query scripts expect a populated `catalog/catalog.db`; they are not setup commands.
- `parser.extract_symbols` is incremental unless `--full` is passed.
- Validation documents may be more operationally accurate than the README for current edge cases and failure modes.
- Do not build or promote the Ladybug graph during an agentic session. Full candidate construction and promotion are lengthy operator-run workflows; agents may run read-only validation, graph queries, and focused unit tests only.

## Data Quality Hotspots

- `openapispec_index.canonical_name` can incorrectly include path-like values (for example, `accounts-payable/account-label`) when slug/path inference leaks into canonical labels.
- `openapispec_index.x_mapped_to` must match a valid `.ent` stem from `app/source/**/*.ent`; treat values outside this set as invalid metadata.
- `openapispec_index.module` should exclude template-only files (`template*`) from graph/index flows unless explicitly modeled.
- `openapispec_index.kind='unknown'` should be treated as triage-required and not silently considered equivalent to known kinds.
- `entity_nodes` is canonical identity only. Repo-local entity facts such as `ent_file`, `module`, `table_name`, `view_name`, `dummy`, and `source_file_id` belong in `entity_occurrences`.
- `relationships.target_kind` values like `unknown` and `cqry` require explicit evidence and should be audited for extraction misclassification.

See [validation/phase2d1_remediation.md](validation/phase2d1_remediation.md) for current remediation rules and acceptance boundaries.

## When Editing

- Keep changes evidence-backed and minimal.
- Update docs only when behavior or operator guidance changes.
- If you touch extraction logic, run the narrowest validator or query that can falsify the change.
- If you need broader project context, read the linked docs instead of copying them here.
- When triaging runtime failures (tracebacks, SQL exceptions, HTTP errors), respond in this order: failing command, first failing file/line, likely root cause, smallest falsifying check, then narrowest repair path.

## Useful Validation Commands

```bash
python validation/validate_phase2b.py --db catalog/catalog.db
python validation/validate_phase2c1.py --db catalog/catalog.db
python validation/validate_phase2d.py --db catalog/catalog.db
python validation/validate_security_mappings.py --db catalog/catalog.db
PYTHONPATH=. python validation/validate_graph.py --db catalog/catalog.db --graph catalog/graph.lbug
```

## Troubleshooting Queries

Use these focused checks before broad rebuilds.

```bash
# OpenAPI rows that look path-like in canonical_name.
python scripts/query_catalog.py sql \
	"SELECT id, file_path, canonical_name FROM openapispec_index WHERE canonical_name LIKE '%/%' ORDER BY id LIMIT 200"

# OpenAPI rows with x_mapped_to values that fail valid .ent stem checks.
python scripts/query_catalog.py sql \
	"SELECT id, file_path, x_mapped_to FROM openapispec_index WHERE COALESCE(TRIM(x_mapped_to), '') <> '' ORDER BY id LIMIT 200"

# Template-classified OpenAPI files still present in active index rows.
python scripts/query_catalog.py sql \
	"SELECT id, file_path, module, kind FROM openapispec_index WHERE LOWER(file_path) LIKE '%template%' ORDER BY id LIMIT 200"

# Unknown kind rows in OpenAPI index.
python scripts/query_catalog.py sql \
	"SELECT id, file_path, module, kind FROM openapispec_index WHERE kind = 'unknown' ORDER BY id LIMIT 200"

# Entity module normalization spot-check.
python scripts/query_catalog.py sql \
	"SELECT en.id, en.name, eo.module FROM entity_nodes en JOIN entity_occurrences eo ON eo.entity_id = en.id ORDER BY en.id, eo.repo_id LIMIT 200"

# Relationship target_kind distribution to inspect unknown/cqry leakage.
python scripts/query_catalog.py sql \
	"SELECT target_kind, COUNT(*) AS cnt FROM relationships GROUP BY target_kind ORDER BY cnt DESC"

# Security policy eops keys with no operation mapping.
python scripts/query_catalog.py sql \
	"SELECT DISTINCT op_key FROM security_policy_eops WHERE op_key NOT IN (SELECT op_key FROM security_operations) ORDER BY op_key LIMIT 200"

# Menu keys with unresolved operation links.
python scripts/query_catalog.py sql \
	"SELECT m.source_file, i.item_path, i.menu_key FROM security_menu_items i JOIN security_menus m ON m.id = i.menu_id LEFT JOIN security_operations o ON o.op_key = i.menu_key WHERE i.menu_key IS NOT NULL AND o.id IS NULL ORDER BY m.source_file, i.item_path LIMIT 200"
```

## Next Customizations To Consider

- Add a `.github/instructions/*.instructions.md` file scoped to `scripts/**` with script-specific validation expectations.
- Add a small skill for catalog triage that runs the common query and validator commands in the right order.
- Use `/chronicle improve` after a few coding sessions to refine these instructions from repeated friction.
