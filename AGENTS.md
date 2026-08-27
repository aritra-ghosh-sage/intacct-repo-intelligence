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
- `catalog/catalog.db` is the canonical path used by the repository's existing
  general catalog workflow and by the repo-v1 CLI default. These are different
  schemas and workflows; do not run a general refresh against a repo-v1 active
  database (or vice versa). Use an explicit alternate `--db`/`--active-db` path
  when both workflows must coexist.

## Required Local Assumptions

- Most scripts assume the source repository lives at `$HOME/projects/main`.
- `config.py` sets `REPO_PATH` to `$HOME/projects/main`.
- Some scripts also hard-code `--repo-root "$HOME/projects/main"` in examples and shell helpers.
- Before running pipeline commands on another machine, verify the source repo path first. If the path differs, update `config.py` and any explicit `--repo-root` arguments you use.
- Repo-root preflight is mandatory before proposing commands: verify the effective path from `config.py` and the current workspace, then echo the exact resolved repo-root path in the response before giving pipeline or scan commands.

## General catalog commands

The commands in this section are for the existing general/legacy catalog
workflow. They are not the repo-v1 rebuild path and must not be mixed with a
repo-v1 active database.

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

The command above is the general workspace/legacy refresh compatibility entry
point. It does not invoke the repo-v1 immutable entity extractor. For the
repo-v1 full snapshot build, use the dedicated entry point instead:

```bash
PYTHONPATH=. ./.venv/bin/python -m catalog.repo_v1 \
    --manifest config/workspace_repos.yaml \
    --active-db catalog/catalog.db
```

Repo-v1 extraction is full-snapshot based: it reads committed Git tree/blob
bytes, validates the candidate, and atomically promotes it. It has no delta
mode and does not build mappings, graph data, MCP compatibility, or legacy
JSONL entity intermediates.

The general `validation/validate_catalog_integrity.py` validator targets the
workspace catalog schema and is not a repo-v1 validator. Do not use it as the
acceptance check for a V1 database; repo-v1 performs its own candidate
ownership/provenance/integrity validation, supplemented by the repo-v1 test
slice and direct `PRAGMA integrity_check`/foreign-key checks.

An absent `--active-db` path is valid for first initialization. An existing
empty, malformed, or incompatible active file fails closed; do not replace it
automatically. Verify `catalog/catalog.db.previous` and either restore a
known-good artifact or remove the invalid file deliberately before retrying.

- Run the pipeline stepwise when you need narrower validation:

The following stepwise commands are for the general workspace/legacy builder
pipeline only. They are not a substitute for the repo-v1 command above and do
not exercise `catalog.repo_v1_entities`.

```bash
python -m parser.scan_repo
python -m parser.extract_symbols --full
python scripts/scan_ent_files.py --db catalog/catalog.db --repo ia-main --out catalog/entity_definitions.jsonl
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
- Do not use `scripts/refresh.sh` or `scripts.refresh_workspace --mode full` to
  validate repo-v1 entity extraction; those routes use the legacy/general
  entity builder. Use `python -m catalog.repo_v1` for repo-v1.
- In repo-v1, an unknown or dynamic `inheritEnts` overlay emits
  `entity_reference_dynamic` and does not merge metadata from a known base.
  Static direct references and safe empty/null/self/fallback overlays remain
  eligible for inherited metadata.
- Every candidate `.ent` row must have a matching committed snapshot entry.
  A missing entry raises `SourceSnapshotError`; it must not be silently
  omitted. Include resolution uses only retained snapshot paths.

## Data Quality Hotspots

- `openapispec_index.canonical_name` can incorrectly include path-like values (for example, `accounts-payable/account-label`) when slug/path inference leaks into canonical labels.
- `openapispec_index.x_mapped_to` must match a valid `.ent` stem from `app/source/**/*.ent`; treat values outside this set as invalid metadata.
- `openapispec_index.module` should exclude template-only files (`template*`) from graph/index flows unless explicitly modeled.
- `openapispec_index.kind='unknown'` should be treated as triage-required and not silently considered equivalent to known kinds.
- `entity_nodes` is canonical identity only. Repo-local entity facts such as `ent_file`, `module`, `table_name`, `view_name`, `dummy`, and `source_file_id` belong in `entity_occurrences`.
- `relationships.target_kind` values like `unknown` and `cqry` require explicit evidence and should be audited for extraction misclassification.

See [validation/phase2d1_remediation.md](validation/phase2d1_remediation.md) for current remediation rules and acceptance boundaries.

Repo-v1 `entity_diagnostics` rows are source-backed audit signals. Missing,
dynamic, ambiguous, and cyclic entity resolution means the extractor did not
assert the affected fact; those rows do not alone make a candidate invalid.
Snapshot, source-read, provenance, ownership, integrity, and candidate
validation failures remain fail-closed promotion errors.

PR-impact Steps 0–3 are read-only and repo-v1-only: Step 0 captures the exact
revision-pinned fixture, Step 1 performs direct tracing, Step 2 audits Step 1
evidence availability, and Step 3 performs bounded incoming-caller tracing.
Git diff validation only; no catalog delta processing. `catalog.delta.collect_changed_paths`
may be used only as a raw Git diff/path-status parser, never for catalog
change-set processing, delta planning, delta refresh, or delta builder
execution. See [docs/design/repo_v1_current_contract.md](docs/design/repo_v1_current_contract.md)
for the current repo-v1 status and gaps.

PR reviews must follow the canonical format in
[docs/review/pr-review-template.md](docs/review/pr-review-template.md). Agents
and MCP/query review surfaces should use that file as the source template and
preserve its sections for review summaries, findings, checklist, confidence,
recommendation, and assumptions.

## Greenfield Strands Flow

The supported Greenfield entry point is:

```bash
PYTHONPATH=. ./.venv/bin/python scripts/run_greenfield.py \
  --source-root "$HOME/projects/main" \
  --output-dir <immutable-bundle> \
  --pr <number>
```

`scripts/run_greenfield_strands.py` is the implementation entry point behind
that command. `scripts/run_greenfield_codex.py` is a deprecated compatibility
shim and must not acquire independent flow behavior.

The operator-facing flow has four phases:

1. `Capture` writes `run-context.json` with source identity, immutable evidence,
   candidate scope, repository handbook revisions, and tool budgets.
2. `Analyze` lets Strands navigate repository handbooks and approved read-only
   tools, then writes `analysis-report.json` with ranked impact, coverage,
   actions, citations, and explicit gaps.
3. `Remediate and validate` converts an eligible analysis action into the
   retained Step 6-7 compatibility artifacts and runs an enabled central
   validation profile.
4. `Publish` writes `publication.json`, optionally creates a validated draft
   test PR, and creates or updates the canonical GitHub Check and one marker-bound
   PR comment.

The retained Step 1-8 artifacts are internal compatibility, replay, and audit
views. They are not manual workflow gates. The per-PR projection formerly named
the behavior handbook is `behavior-impact-report.json`; a repository behavior
handbook is a separate revision-bound L1/L2/L3 artifact.

Strands is the primary discovery and recommendation agent. It may infer and rank
relationships by using captured handbooks, revision-bound source, CodeGraph,
contracts, CI evidence, test inventories, and related-PR evidence. Every
`confirmed` or `strong_candidate` claim must cite a recorded source or tool
result. Repository eligibility and semantic naming alone never prove impact.

Allowed evidence states are `confirmed`, `strong_candidate`, `candidate`,
`unresolved`, `unavailable`, and `no_evidence`. Only `confirmed` and
`strong_candidate` remediation may reach automatic draft creation, and only
with an exact target repository, target SHA, bounded existing file paths,
recorded edit operations, and an enabled validation profile.

Draft PR creation does not require owner approval. Exact source/target identity,
blob evidence, path policy, clean application, validation, base-movement checks,
and service authorization remain mandatory. Human approval is required before
the draft becomes ready for review or is merged. The system must never approve
or merge its own PR.

The GitHub Check is the canonical user-facing status. The PR comment is an
idempotent human-readable projection of the same `publication.json`; do not
create an independent dashboard data model.

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
