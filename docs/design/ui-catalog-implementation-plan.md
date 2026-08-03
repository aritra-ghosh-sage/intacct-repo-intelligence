# UI Catalog Implementation Plan

## Purpose

Add deterministic, source-provenanced UI evidence to the catalog for both
legacy Sage Intacct actionUI XML forms and NextGen UI metadata. The result must
answer UI impact for an entity without conflating legacy actionUI with NextGen
surfaces, and must retain evidence for fields, events, loaded scripts, and
statically resolvable JavaScript handlers.

## Scope

Included:

- actionUI `*_form.xml` and `*_2012_form.xml` files;
- NextGen `uimeta*`, `viewmeta*`, and view YAML families;
- bounded PHP loader discovery, including inherited loaders;
- XML fields, includes, and event calls;
- JavaScript callable extraction and handler resolution only for scripts proven
  to load for a surface;
- stable SQLite synchronization, validation, CLI, and MCP query surfaces.

Excluded unless a separately approved phase adds them:

- JavaScript call-graph extraction beyond callable declarations;
- XML event-handler execution resolution beyond exact loaded-script callables;
- dynamic PHP/JavaScript expression evaluation;
- XInclude expansion (the include relationship itself is retained);
- Ladybug graph projection;
- `qa_impact` and `entity_context` integration;
- per-file UI delta mutation;
- compatibility mappings from actionUI form names;
- automatic screen-kind inference beyond actionUI form and NextGen family.

## Evidence Rules

- Never infer an entity, script, include, field, event, or handler from a
  similar name.
- Every persisted row has repository and source-file provenance.
- Dynamic or unsupported source constructs become resolution issues, not facts.
- `actionui` and `nextgen` remain distinct `surface_family` values in all
  tables and query contracts.
- Entity mappings are owned by one extractor type only. Entity-building code
  owns source mappings; OpenAPI linking owns the explicit OpenAPI mapping-type
  allowlist.

## Stable Identity

- `ui_surfaces.surface_key`: `actionui:<xml-relative-path>` for legacy forms,
  `nextgen:<family-key>` for NextGen families.
- `ui_artifacts`: `(surface_id, source_file_id, artifact_role)`.
- XML field/event/include/script and issue rows use their source location and
  normalized payload as their stable natural key.
- Repeated synchronization with unchanged input must preserve row IDs and the
  catalog content fingerprint.

## Data Model

Create repo-scoped tables:

- `ui_surfaces`: one row per legacy form or NextGen family.
- `ui_artifacts`: all source artifacts participating in a surface.
- `ui_entity_references`: direct entity evidence plus relationship role.
- `ui_artifact_includes`: unresolved/resolved XML include evidence.
- `ui_fields`: extracted actionUI fields and provenance.
- `ui_events`: actionUI event declarations and direct event calls.
- `ui_script_dependencies`: script load evidence and activation state.
- `ui_event_calls`: resolved or unresolved event-call outcomes.
- `ui_resolution_issues`: warnings/errors with machine-readable code,
  source location, and payload.

Use repo-owned composite foreign keys where the child requires the parent;
delete those rows with `ON DELETE CASCADE`. Optional cross-reference values may
be nullable but must not use `ON DELETE SET NULL` on a composite key containing
a non-null `repo_id`. Add SQLite triggers where a composite foreign key cannot
enforce symbol or entity ownership. Add an index supporting each query filter
and every stable key.

## Extractors

Extraction APIs are immutable dataclasses. At minimum provide facts for
diagnostics, entity references, includes, fields, event calls, events, actionUI
artifacts, loaders, script dependencies, JavaScript symbols/results, and a
desired UI snapshot.

### PHP Loader Discovery

Use tree-sitter PHP AST only. Accept statically provable literals, assignments,
returns, arrays, and direct calls. Resolve inheritance through the existing
`relationships` `INHERITS` evidence, never `symbols.parent_symbol`.

Capture explicit actionUI form loader facts, base `FormEditor` conventions, and
the static `jsCommonIncludes()` call tree. Normalize `../resources/...` to
`app/resources/...`; record external, bare, dynamic, missing, and ambiguous
paths as issues.

### XML

Use a safe Expat-based parser. Extract only real `<field>` elements, direct
`<events>` children, direct `<path>` values, and XInclude namespace elements.
Field value precedence is attribute path, direct child `<path>`, then direct
field text. Retain source locations. Do not use regex fallback or expand
XIncludes.

### JavaScript

Add JavaScript to symbol extraction using tree-sitter. Record parser error
ranges in `outputs/javascript_parse_failures.jsonl`; never resolve a handler
from a script whose declaration intersects `ERROR` or `MISSING`. Extract only
top-level exact callable declarations and supported object-property methods.
Resolve an XML event call only against scripts linked to that surface: a unique
active exact callable resolves; conditional-only is conditional; mixed active
and conditional or multiple active candidates is ambiguous; unsupported member
expressions remain unresolved.

## Synchronization

1. Build the desired snapshot completely before opening a write transaction.
2. Reject duplicate natural keys with different payloads.
3. Start `BEGIN IMMEDIATE`.
4. Load desired keys into temporary tables.
5. Upsert parents then children in a stable sorted order.
6. Delete stale children before stale parents using `NOT EXISTS`.
7. Run foreign-key and repo-ownership validation, then commit.

Do not implement this as delete-and-reinsert. A failed extraction leaves the
prior catalog generation intact. Schema/read/duplicate-key/cross-repo failures
are fatal; incomplete evidence is a persisted issue. Strict resolution is an
optional validator mode.

## Query Contracts

`ui_impact(entity_name, repo_key, limit=25, cursor=None)` returns entity UI
summary and surfaces connected by direct ownership and supported related roles
(`editor`, `form_editor`, `lister`, `manager`).

`ui_surface_detail(surface_key, repo_key, record_kind, limit=25, cursor=None)`
returns one paged record family: `artifacts`, `fields`, `events`, `scripts`,
`includes`, `references`, or `issues`. Use existing cursor envelope rules and a
maximum nested-call limit of 100. Add matching CLI commands and two read-only
MCP tools; the expected MCP tool count becomes 24.

## Task Breakdown

### Task 0: Canonical Plan

Input: this approved plan and current repository conventions.

Output: this source-of-truth document.

Acceptance: every implementation task cites this document; no later addendum
is required to understand scope, exclusions, identities, contracts, or tests.

Files: `docs/design/ui-catalog-implementation-plan.md`.

### Task 1: Mapping Ownership

Input: current entity and OpenAPI mapping writers.

Output: explicit owned mapping-type constants and scoped deletion/upsert logic.

Acceptance: entity build cannot delete OpenAPI mappings; OpenAPI linker cannot
delete source mappings; focused regression test proves both.

Files: `scripts/build_entities.py`, `scripts/link_openapispec.py`, shared
mapping ownership module/tests as needed.

### Task 2: Schema and Migration

Input: canonical schema, migration runner, catalog integrity conventions.

Output: tables, constraints, indexes, migration, trigger-install support, and
migration tests.

Acceptance: fresh and upgraded databases converge; rerunning migration is
idempotent; FK and ownership violations fail.

Files: `catalog/schema.sql`, `catalog/migrations.py`, new migration files,
schema/migration tests.

### Task 3: NextGen Family Extraction

Input: `uimeta*`, `viewmeta*`, and view YAML source evidence.

Output: deterministic NextGen family and entity-reference facts.

Acceptance: journal-entry NextGen family is not falsely mapped to `Journal`
when repository evidence maps its schema to `GLBatch`; unknown mapping remains
an issue, not a guessed entity.

Files: dedicated UI extractor modules and focused tests; do not overload
OpenAPI linker heuristics.

### Task 4: PHP Loader Facts

Input: PHP source and existing symbols/relationships.

Output: immutable loader facts with unresolved evidence.

Acceptance: GLBatch explicit/base/2012 loader paths are captured from source;
dynamic paths are unresolved.

Files: dedicated `parser/actionui` model/PHP modules and tests.

### Task 5: Loader and Script Dependencies

Input: Task 4 facts, inheritance relationships, `FormEditor` static helpers.

Output: active/conditional script dependency facts.

Acceptance: `glbatch.js` is linked through source evidence; no global
same-name handler lookup is possible.

Files: dedicated loader/script modules and tests.

### Task 6: actionUI XML Extraction

Input: form XML files.

Output: form artifact, field, include, event, event-call, and issue facts.

Acceptance: parser extracts representative GLBatch/AP print-check form fields
and events with locations; malformed XML is an issue and cannot corrupt prior
rows.

Files: dedicated XML extractor modules and tests.

### Task 7: JavaScript and Handler Resolution

Input: linked script facts and event calls.

Output: JavaScript symbols, parse diagnostics, and event-call resolution facts.

Acceptance: GLBatch handlers resolve only through loaded `glbatch.js`; duplicate
names elsewhere do not create false matches; parse-error scripts cannot resolve.

Files: `parser/extract_symbols.py`, JavaScript extractor/resolver modules,
tests and output contract tests.

### Task 8: Stable UI Synchronization

Input: all desired extraction facts and Task 2 schema.

Output: transactional snapshot synchronizer.

Acceptance: unchanged repeated sync preserves IDs/fingerprint; stale child rows
are removed before parents; injected conflict/failure leaves active data intact.

Files: UI build/sync module, supporting DB code, tests.

### Task 9: Refresh Registration

Input: synchronizer and builder registry contract.

Output: `ui_surfaces` builder with dependencies `relationships`, `entities`,
and `openapi_link`, correct profile/scope/invalidation contract, and delta
version bump.

Acceptance: planner schedules the builder for relevant XML/PHP/JS/ENT/OpenAPI
changes and not unrelated paths.

Files: `scripts/builder_registry.py`, refresh orchestration/tests.

### Task 10: Validation

Input: populated UI tables.

Output: focused integrity and optional strict-resolution validator.

Acceptance: it detects broken provenance, ownership, invalid keys, and blocking
issues; non-strict incomplete evidence is reported but not treated as a false
fact.

Files: `validation/validate_ui_catalog.py`, tests/docs if operator behavior
changes.

### Task 11: CLI Query Surface

Input: query contracts and populated catalog.

Output: stable JSON CLI commands for impact and surface detail.

Acceptance: pagination/envelope/error behavior matches existing query scripts.

Files: query module/script and tests.

### Task 12: MCP Query Surface

Input: CLI-equivalent query functions.

Output: read-only `ui_impact` and `ui_surface_detail` tools with schemas and
examples.

Acceptance: tool metadata, expected count (24), error envelope, pagination,
and representative GLBatch response tests pass.

Files: `intacct_mcp/server.py`, MCP tests.

## Final Verification

Run focused unit tests for every task, schema/migration idempotence, mapping
ownership regression, snapshot failure injection, validation, CLI/MCP metadata,
and a read-only GLBatch evidence query. Run a candidate refresh only if the
operator-approved source/database workflow is available. Do not build or
promote Ladybug graph in this implementation session.
