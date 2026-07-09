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

- Most scripts assume the source repository lives at `/home/aritraghosh/projects/main`.
- `config.py` sets `REPO_PATH` to `/home/aritraghosh/projects/main`.
- Some scripts also hard-code `--repo-root "/home/aritraghosh/projects/main"` in examples and shell helpers.
- Before running pipeline commands on another machine, verify the source repo path first. If the path differs, update `config.py` and any explicit `--repo-root` arguments you use.

## Canonical Commands

- Initialize a fresh database:

```bash
python -c "from catalog.db import init_db; init_db()"
```

- Run the full rebuild pipeline:

```bash
bash scripts/refresh.sh
```

- Run the pipeline stepwise when you need narrower validation:

```bash
python -m parser.scan_repo
python -m parser.extract_symbols --full
python scripts/scan_ent_files.py scan --repo-root "/home/aritraghosh/projects/main"
python scripts/build_entities.py build
python scripts/build_entity_roots.py build
python -m parser.extract_relationships --repo-root "/home/aritraghosh/projects/main"
python scripts/build_workflows.py build --db catalog/catalog.db --repo-root "/home/aritraghosh/projects/main"
python scripts/build_ui_companions.py --db catalog/catalog.db
python scripts/scan_openapispec.py scan --db catalog/catalog.db --repo-root "/home/aritraghosh/projects/main"
python scripts/link_openapispec.py link --db catalog/catalog.db
python scripts/build_rest_endpoints.py build --db catalog/catalog.db --repo-root "/home/aritraghosh/projects/main"
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
```

## Working Conventions

- Prefer SQLite evidence over prose. If a claim can be checked in `catalog/catalog.db`, check it.
- Preserve provenance. New extracted facts should trace back to a real file, symbol, or source text.
- Precision is more important than recall. Remove misleading data rather than keeping weak mappings.
- Do not treat semantically similar mapping types as interchangeable unless code and validation rules do so explicitly.
- Prefer narrow rebuilds and validation commands before running the full refresh.

## Common Pitfalls

- Path defaults are inconsistent in places. Do not assume every script uses the same repo root without checking.
- Query scripts expect a populated `catalog/catalog.db`; they are not setup commands.
- `parser.extract_symbols` is incremental unless `--full` is passed.
- Validation documents may be more operationally accurate than the README for current edge cases and failure modes.

## Data Quality Hotspots

- `openapispec_index.canonical_name` can incorrectly include path-like values (for example, `accounts-payable/account-label`) when slug/path inference leaks into canonical labels.
- `openapispec_index.x_mapped_to` must match a valid `.ent` stem from `app/source/**/*.ent`; treat values outside this set as invalid metadata.
- `openapispec_index.module` should exclude template-only files (`template*`) from graph/index flows unless explicitly modeled.
- `openapispec_index.kind='unknown'` should be treated as triage-required and not silently considered equivalent to known kinds.
- `entity_nodes.module` must represent normalized business module semantics, not raw folder names.
- `relationships.target_kind` values like `unknown` and `cqry` require explicit evidence and should be audited for extraction misclassification.

See [validation/phase2d1_remediation.md](validation/phase2d1_remediation.md) for current remediation rules and acceptance boundaries.

## When Editing

- Keep changes evidence-backed and minimal.
- Update docs only when behavior or operator guidance changes.
- If you touch extraction logic, run the narrowest validator or query that can falsify the change.
- If you need broader project context, read the linked docs instead of copying them here.

## Useful Validation Commands

```bash
python validation/validate_phase2b.py --db catalog/catalog.db
python validation/validate_phase2c1.py --db catalog/catalog.db
python validation/validate_phase2d.py --db catalog/catalog.db
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
	"SELECT id, name, module FROM entity_nodes ORDER BY id LIMIT 200"

# Relationship target_kind distribution to inspect unknown/cqry leakage.
python scripts/query_catalog.py sql \
	"SELECT target_kind, COUNT(*) AS cnt FROM relationships GROUP BY target_kind ORDER BY cnt DESC"
```

## Next Customizations To Consider

- Add a `.github/instructions/*.instructions.md` file scoped to `scripts/**` with script-specific validation expectations.
- Add a small skill for catalog triage that runs the common query and validator commands in the right order.
- Use `/chronicle improve` after a few coding sessions to refine these instructions from repeated friction.