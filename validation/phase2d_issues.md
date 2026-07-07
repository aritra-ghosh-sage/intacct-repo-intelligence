# Phase 2D Remediation Instructions

## Document Purpose

This document is a **machine-readable remediation specification** for the
`intacct-repo-intelligence` project.

It is intended to be consumed by an LLM-based coding agent
(GitHub Copilot, Claude Code, Cursor, Cline, or similar) operating inside
the repository at `~/projects/intacct-repo-intelligence`.

The agent MUST:

- Follow every rule in the `constraints` block.
- Address every item in the `issues` block.
- Use the exact SQL, file paths, and command signatures provided.
- Verify each fix with the specified verification query.
- Refuse to guess, infer, or fabricate any fact not present in the repository.

---

## Repository Context

```yaml
project:
  name: intacct-repo-intelligence
  root: ~/projects/intacct-repo-intelligence
  database: catalog/catalog.db
  language: python
  python_version: ">=3.12"
  primary_dependencies:
    - sqlite3
    - tree-sitter

verified_state:
  tables_present:
    - entity_mappings
    - entity_nodes
    - entity_roots
    - files
    - index_runs
    - relationships
    - sqlite_sequence
    - symbol_extraction_runs
    - symbols
    - workflow_steps
    - workflows
  tables_missing:
    - rest_endpoints
    - ui_companions
    - repos
    - services
    - knowledge
  row_counts:
    files: 45435
    symbols: 120499
    relationships: 234906
    entity_nodes: 1807
    entity_roots: 3811
    entity_mappings: 4366
    workflows: 302
    workflow_steps: 302

evidence_sources:
  - project_inventory.json
  - schema.sql
  - all_tables_row_count.txt
  - entity_mapping.txt
  - repo_tree.txt
  - code_files.txt
  - m365_cp_output.txt
```

---

## Constraints

The agent MUST obey these rules for every change:

```yaml
constraints:
  evidence_first:
    - Every relationship or mapping added to the database MUST be traceable
      to a source artifact.
    - Do NOT invent entities, workflows, or mappings.
    - Do NOT infer relationships from naming patterns alone.

  determinism:
    - Extraction must be repeatable.
    - Given identical inputs, output rows must be identical.

  precision_over_recall:
    - A missing mapping is preferable to an incorrect mapping.
    - When in doubt, skip the row and log the skipped case.

  no_schema_regressions:
    - Do NOT drop or rename existing tables.
    - Do NOT change existing column semantics.
    - Add new columns only via a new migration file.

  provenance:
    - Every new mapping row MUST populate source_text (or equivalent)
      with a reference to the originating file path or symbol.

  migrations:
    - New migrations MUST be added with the next available sequence number.
    - Do NOT re-use existing migration numbers.
    - Existing migrations MUST NOT be modified.

  validation:
    - Every fix MUST include a verification SQL query.
    - The verification query MUST return a non-zero, explainable result
      before the issue is considered resolved.

  no_fiction:
    - If evidence for a change cannot be found in the codebase, the agent
      MUST stop and report the missing evidence rather than proceeding.
```

---

## Issue Index

```yaml
issues:
  - id: ISSUE-001
    title: ".cqry files have zero symbol extraction"
    severity: P0
    category: extraction_gap

  - id: ISSUE-002
    title: "Phase 2D migrations 007–010 not applied to catalog.db"
    severity: P0
    category: pipeline_incomplete

  - id: ISSUE-003
    title: "Phase 2D build scripts not executed"
    severity: P0
    category: pipeline_incomplete

  - id: ISSUE-004
    title: "workflows and workflow_steps have suspicious 1:1 ratio"
    severity: P0
    category: modeling_decision

  - id: ISSUE-005
    title: "entity_mappings missing declared source types"
    severity: P0
    category: coverage_gap

  - id: ISSUE-006
    title: "Phase 2D validator does not exist"
    severity: P0
    category: validation_gap

  - id: ISSUE-007
    title: "Entity recall is unmeasured"
    severity: P1
    category: validation_gap

  - id: ISSUE-008
    title: "project_inventory.json drifts from actual catalog.db state"
    severity: P1
    category: documentation_drift
```

---

# ISSUE-001

## Title

`.cqry` files have zero symbol extraction.

## Evidence

```sql
-- 325 .cqry files exist and are cataloged as php
SELECT language, COUNT(*)
FROM files
WHERE path LIKE '%.cqry'
GROUP BY language;
-- Result: php|325

-- Zero symbols extracted from any .cqry file
SELECT kind, COUNT(*)
FROM symbols s
JOIN files f ON f.id = s.file_id
WHERE f.path LIKE '%.cqry'
GROUP BY kind;
-- Result: (empty)
```

```bash
# No extractor references .cqry
grep -i cqry parser/extractors/*.py
# Result: (empty)
```

## Declared Authority

`project_inventory.json` declares `.cqry` as a **HIGH-authority source of
truth** under `sources_of_truth[type=code_symbols]`.

## Root Cause

- The PHP extractor is triggered on `.cqry` files because they are
  classified as `php`.
- The PHP tree-sitter grammar does not parse `.cqry` files because their
  outer structure is XML-wrapped (typically `<query>...</query>`),
  not raw PHP.
- As a result, no symbols are emitted and no downstream mappings are
  produced.

## Required Actions

1. Confirm the internal structure of `.cqry` files by sampling 5 files
   in the repository. Do NOT assume format. Read them.
2. Choose one of the two paths below and document the choice in a
   comment at the top of the extractor:

   Path A — Extend `parser/extractors/php_extractor.py`:
   - Detect `.cqry` files by file extension.
   - Parse the outer XML shell.
   - Extract embedded PHP/SQL blocks.

   Path B — Create `parser/extractors/cqry_extractor.py`:
   - Own the `.cqry` extension exclusively.
   - Register in `parser/extract_symbols.py` dispatch table.

3. Emit symbols with these `kind` values:
   - `cqry_query` (top-level query definition)
   - `cqry_field` (field references)
   - `cqry_table` (table references)
   - `cqry_join` (join declarations, if present)

4. Populate `symbols.signature` with a compact representation of the
   query definition.

5. Update `scripts/build_entities.py` to add a new mapping type:
   - `mapping_type = 'cqry'` when linking a `.cqry` file to an entity.
   - Set `source_text` to the relative file path.

6. Add a migration only if new columns are required (they should not
   be). Do NOT modify existing tables.

## Verification

```sql
-- Must return > 0 rows
SELECT kind, COUNT(*)
FROM symbols s
JOIN files f ON f.id = s.file_id
WHERE f.path LIKE '%.cqry'
GROUP BY kind;

-- Must contain a 'cqry' row
SELECT mapping_type, COUNT(*)
FROM entity_mappings
GROUP BY mapping_type
ORDER BY 2 DESC;
```

## Definition of Done

- `.cqry` symbol count > 0.
- `entity_mappings.mapping_type = 'cqry'` count > 0.
- Every `cqry` mapping row has a populated `source_text`.
- Extractor is deterministic across two consecutive runs.

---

# ISSUE-002

## Title

Phase 2D migrations 007 through 010 have not been applied to `catalog.db`.

## Evidence

```bash
sqlite3 catalog/catalog.db ".tables"
```

Returns only:

```text
entity_mappings   index_runs              workflow_steps
entity_nodes      relationships           workflows
entity_roots      symbol_extraction_runs
files             symbols
```

But `migrations/` contains:

```text
007_phase2c_rest.sql
008_phase2c_ui.sql
009_phase2c_repos.sql
010_phase2c_knowledge.sql
```

## Required Actions

1. Apply each migration in ascending order:

   ```bash
   sqlite3 catalog/catalog.db < migrations/007_phase2c_rest.sql
   sqlite3 catalog/catalog.db < migrations/008_phase2c_ui.sql
   sqlite3 catalog/catalog.db < migrations/009_phase2c_repos.sql
   sqlite3 catalog/catalog.db < migrations/010_phase2c_knowledge.sql
   ```

2. If any migration fails, STOP. Do not proceed. Report the failure.
3. Do NOT modify migration file contents.

## Verification

```sql
SELECT name
FROM sqlite_master
WHERE type='table'
ORDER BY name;
```

Result MUST include all of:

- `rest_endpoints`
- `ui_companions`
- `repos`
- `services`
- `knowledge`

(The exact table names should match those defined by the migrations.
Verify against the migration file DDL, not against assumed names.)

## Definition of Done

- All four migrations applied without error.
- All declared tables present.
- No existing table modified.

---

# ISSUE-003

## Title

Phase 2D build scripts have not been executed against the catalog.

## Evidence

The following scripts exist in `scripts/` but their target tables are
empty or absent:

- `scripts/build_rest_endpoints.py`
- `scripts/build_ui_companions.py`
- `scripts/link_openapispec.py`
- `scripts/scan_openapispec.py`

`entity_mappings` currently contains OpenAPI-related mapping types
(`openapispec_operations`, `openapispec_history`, `openapispec_schema`),
which indicates OpenAPI data is being stuffed into `entity_mappings`
instead of dedicated Phase 2D tables.

## Prerequisite

ISSUE-002 must be resolved first.

## Required Actions

1. Execute in this order:

   ```bash
   python scripts/scan_openapispec.py
   python scripts/link_openapispec.py
   python scripts/build_rest_endpoints.py
   python scripts/build_ui_companions.py
   ```

2. Capture stdout and stderr for each command.
3. Do NOT re-run `build_entities.py` unless the script itself
   documents idempotency.

## Verification

```sql
SELECT 'rest_endpoints', COUNT(*) FROM rest_endpoints
UNION ALL
SELECT 'ui_companions', COUNT(*) FROM ui_companions;
```

Both counts MUST be > 0 and explainable relative to the number of
`.ent` files and `.xslt` / `.js` companion files in the repository.

## Definition of Done

- `rest_endpoints` and `ui_companions` populated.
- Every row has provenance (file path or symbol reference).
- Idempotent: two consecutive runs produce identical row counts.

---

# ISSUE-004

## Title

`workflows` and `workflow_steps` have a suspicious 1:1 ratio.

## Evidence

```sql
SELECT workflow_id, COUNT(*)
FROM workflow_steps
GROUP BY workflow_id
ORDER BY 2 DESC
LIMIT 20;
```

Every returned row has `COUNT(*) = 1`. Total: `workflows = 302`,
`workflow_steps = 302`.

## Interpretation

This may be a **modeling choice** rather than a bug. Each workflow
currently represents a single atomic action (e.g., `submit`, `approve`,
`post`) rather than a multi-step business flow.

## Required Actions

The agent MUST NOT silently restructure this data.

Instead, produce a design document at:

```text
docs/design/workflows.md
```

Containing:

1. A statement of the current model (flat, single-action workflows).
2. Two options:
   - Option A: Keep flat model. Rename to reflect single-action semantics.
   - Option B: Introduce composite workflows grouping actions in
     sequence (e.g., `bill_lifecycle` → `submit → approve → post`).
3. The impact on Kùzu graph modeling for each option.
4. A recommendation with justification.

Do NOT change `build_workflows.py` until a decision is recorded in
that document.

## Verification

- File `docs/design/workflows.md` exists.
- Both options are documented.
- Decision field is present (may be `pending`).

## Definition of Done

- Design document committed.
- Decision recorded (even if `pending`).

---

# ISSUE-005

## Title

`entity_mappings` is missing several declared sources of truth.

## Evidence

Declared in `project_inventory.json` under `sources_of_truth`:

```text
.ent    (HIGH)
.cls    (HIGH)
.inc    (HIGH)
.cqry   (HIGH)
.yaml   (HIGH)
.sql    (MEDIUM)
.xslt   (MEDIUM)
.html   (MEDIUM)
.phtml  (MEDIUM)
```

Present in `entity_mappings` (from `entity_mapping.txt`):

```text
manager, editor, lister, picker,
openapispec_operations, openapispec_history, openapispec_schema,
allowed_operations_handler,
entry_manager, form_editor, pick_manager, pick_picker,
item_manager, reverse_manager,
batch_manager, approval_manager, batch_picker, entity_manager
```

Missing extension-derived mapping types:

- `cqry` (see ISSUE-001)
- `inc`
- `yaml`
- `sql`
- `xslt`
- `html`
- `phtml`

## Required Actions

For each missing extension, the agent MUST:

1. Confirm files with that extension exist in the `files` table:

   ```sql
   SELECT COUNT(*) FROM files WHERE path LIKE '%.<ext>';
   ```

2. If count is zero:
   - Update `project_inventory.json` to move the extension into an
     `out_of_scope` block with justification.
   - Do NOT add mapping logic.

3. If count is greater than zero:
   - Extend `scripts/build_entities.py` to emit a mapping row with
     `mapping_type = '<ext>'` when the file can be linked to an entity.
   - Populate `source_text` with the relative file path.
   - Do NOT create mappings without evidence linking the file to a
     specific entity.

## Verification

```sql
SELECT mapping_type, COUNT(*)
FROM entity_mappings
GROUP BY mapping_type
ORDER BY 2 DESC;
```

For each in-scope extension, `mapping_type` MUST appear at least once.

## Definition of Done

- Every declared HIGH-authority source is either represented in
  `entity_mappings` or explicitly marked out-of-scope with a reason.
- Every MEDIUM-authority source is either represented or marked
  out-of-scope with a reason.

---

# ISSUE-006

## Title

Phase 2D validator does not exist.

## Evidence

`validation/` contains:

```text
validate_phase2b.py
validate_phase2c1.py
phase2b_report.md
phase2c1_report.md
```

No `validate_phase2d.py` exists. Phase 2D is currently marked
`in_progress (needs_verification)` in `project_inventory.json` with
no automated validator to close it out.

## Required Actions

Create `validation/validate_phase2d.py` with the following checks:

```yaml
checks:
  - id: tables_exist
    description: Confirm Phase 2D tables exist.
    query: SELECT name FROM sqlite_master WHERE type='table';
    assertion: >
      Set includes rest_endpoints, ui_companions, repos, services,
      knowledge (subject to migration DDL).

  - id: declared_vs_actual_mapping_types
    description: Compare declared sources of truth against actual
      mapping types.
    inputs:
      - project_inventory.json (sources_of_truth block)
      - entity_mappings.mapping_type distinct values
    assertion: Every declared in-scope source appears at least once.

  - id: mapping_provenance
    description: Every mapping row has non-null source_text or file_id.
    query: >
      SELECT COUNT(*) FROM entity_mappings
      WHERE (source_text IS NULL OR source_text = '')
        AND file_id IS NULL;
    assertion: Result equals 0.

  - id: workflow_step_ratio
    description: Report the workflow-to-step ratio.
    query: >
      SELECT
        (SELECT COUNT(*) FROM workflows) AS workflows,
        (SELECT COUNT(*) FROM workflow_steps) AS steps,
        ROUND(
          1.0 * (SELECT COUNT(*) FROM workflow_steps) /
          NULLIF((SELECT COUNT(*) FROM workflows), 0),
          2
        ) AS avg_steps;
    assertion: Report only. Value is a modeling decision (ISSUE-004).

  - id: rest_endpoint_coverage
    description: Percentage of entities with at least one REST endpoint.
    assertion: Report only. Threshold to be set by owner.

  - id: ui_companion_coverage
    description: Percentage of entities with at least one UI companion.
    assertion: Report only. Threshold to be set by owner.

  - id: cqry_coverage
    description: Confirm .cqry symbols and mappings exist.
    assertion: Both counts > 0 (see ISSUE-001).
```

The validator MUST:

- Emit `validation/phase2d_report.md` as output.
- Return non-zero exit code if any `assertion` fails.
- Never modify the database.

## Verification

```bash
python validation/validate_phase2d.py
ls -la validation/phase2d_report.md
```

## Definition of Done

- `validate_phase2d.py` exists and runs without error.
- `phase2d_report.md` is produced.
- Failing assertions are clearly labeled.

---

# ISSUE-007

## Title

Entity recall is unmeasured.

## Evidence

`entity_nodes` contains 1,807 rows. No ground-truth reference set
exists to measure whether this is complete, incomplete, or noisy.

## Required Actions

1. Create `validation/gold_entities.jsonl` with a manually curated set
   of 30–50 well-known Intacct entities. Format:

   ```json
   {"name":"APBill","module":"AP","authority":"human_curated"}
   {"name":"APPayment","module":"AP","authority":"human_curated"}
   {"name":"Vendor","module":"AP","authority":"human_curated"}
   {"name":"Customer","module":"AR","authority":"human_curated"}
   {"name":"GLBatch","module":"GL","authority":"human_curated"}
   ```

2. The agent MUST NOT invent entity names. If the agent cannot verify
   an entity name from the codebase (e.g., by searching `.ent` files
   or existing `entity_nodes.name`), it MUST leave the gold set
   incomplete and flag it for human review.

3. Add a validator function inside `validate_phase2d.py` (or a new
   `validate_recall.py`) that computes:

   ```text
   precision = |discovered ∩ gold| / |discovered near gold|
   recall    = |discovered ∩ gold| / |gold|
   ```

4. Emit numeric results into `phase2d_report.md`.

## Verification

- `validation/gold_entities.jsonl` exists with at least 30 entries.
- Precision and recall are computed and reported.

## Definition of Done

- Gold set exists.
- Recall is a measured number, not "unknown".
- Every gold entry references a real `.ent` file or existing symbol.

---

# ISSUE-008

## Title

`project_inventory.json` drifts from actual `catalog.db` state.

## Evidence

- Inventory declares Phase 2D `in_progress`.
- Migrations 007–010 have not been applied.
- Inventory declares `.cqry` a HIGH-authority source.
- No `.cqry` symbols exist.
- Inventory lists build scripts for `rest_endpoints` and `ui_companions`.
- Those tables do not exist.

## Root Cause

`project_inventory.json` is authored by hand and does not reflect the
actual state of `catalog.db`.

## Required Actions

Create `scripts/generate_inventory.py` that:

1. Reads the actual state of `catalog.db`:
   - Table list.
   - Row counts per table.
   - Distinct `mapping_type` values in `entity_mappings`.
   - Distinct `kind` values in `symbols`.
   - Distinct `relationship_type` values in `relationships`.
   - Distinct `workflow_type` values in `workflows`.

2. Reads the file system:
   - `migrations/` directory listing.
   - `parser/`, `scripts/`, `validation/` directory listings.

3. Emits a regenerated `project_inventory.json` with:
   - A `generated_at` timestamp.
   - A `verified_state` block reflecting only what is present.
   - A `declared_state` block reflecting design intent.
   - A `drift` block listing declared-but-missing items.

4. The generator MUST NOT invent phases, tables, or scripts.

## Verification

```bash
python scripts/generate_inventory.py
git diff project_inventory.json
```

The regenerated file MUST match reality. Manual edits must be limited
to the `declared_state` section.

## Definition of Done

- `scripts/generate_inventory.py` exists and is deterministic.
- Regenerated `project_inventory.json` reflects `catalog.db` state.
- Drift between declared and verified states is explicit.

---

# Execution Order

The agent MUST execute issues in this dependency order:

```yaml
order:
  - ISSUE-002    # Apply migrations first (unblocks 003, 006)
  - ISSUE-003    # Populate Phase 2D tables
  - ISSUE-001    # Fix .cqry extraction
  - ISSUE-005    # Fill remaining mapping type gaps
  - ISSUE-004    # Document workflow modeling decision
  - ISSUE-006    # Build the Phase 2D validator
  - ISSUE-007    # Introduce gold set and measure recall
  - ISSUE-008    # Regenerate inventory from reality
```

Do NOT reorder unless a dependency is discovered that makes the current
order infeasible. If reordering is required, log the reason.

---

# Global Definition Of Done

Phase 2D is considered complete when:

- All eight issues are resolved.
- `validate_phase2d.py` exits with code 0.
- `phase2d_report.md` is committed.
- `project_inventory.json` is regenerated from `catalog.db` and
  contains no drift entries under `declared_state` vs `verified_state`.
- No mapping row exists without provenance.
- No table exists without a corresponding migration.
- No extractor references a source not present in the repository.

---

# Forbidden Actions

The agent MUST NOT:

- Delete existing rows from `catalog.db`.
- Drop or rename existing tables.
- Modify existing migration files.
- Invent entities, workflows, mappings, or relationships.
- Populate rows without provenance.
- Skip verification queries.
- Mark an issue resolved without meeting its `Definition of Done`.
- Modify this document.

---

# Reporting

After each issue is addressed, the agent MUST append an entry to
`validation/phase2d_progress.md`:

```yaml
- issue_id: ISSUE-XXX
  status: resolved | blocked | deferred
  verification_output: <paste of verification query result>
  files_changed:
    - <path>
  notes: <free text>
```

This log MUST be maintained even if a fix is partial.
