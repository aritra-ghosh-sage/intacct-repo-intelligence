# Repo Intelligence V1: KISS Full-Rebuild Plan

## Decision

Build on the `repo-v1` branch as a bounded refactor of the existing repository.
Reuse proven leaf components, but introduce a new explicit full-refresh path
and a fresh development database/schema. The existing `main` branch and its
refresh path remain untouched by this redesign.

There is no production-data migration requirement. Query and MCP compatibility
is deferred until the core facts are stable.

## Scope

The first target is `ia-main` only.

The authoritative model is:

```text
one immutable Git commit
    -> immutable source inventory
    -> source-backed facts
    -> SQLite candidate
    -> validation
    -> atomic promotion
    -> read-only queries
```

Full candidate rebuilds are the correctness oracle.

Parser failures retain inventory and diagnostics but produce no derived facts
for the failed file. Semantic and catalog-integrity failures reject the
candidate. UI last-known-good retention, when eventually needed, is a narrow
UI-specific exception and not a generic stale-data mechanism.

## Explicitly deferred

- delta refresh;
- mixed per-builder planning or execution modes;
- generic stale restoration;
- fingerprints as promotion or readiness gates;
- automatic SQLite or graph recovery;
- broad MCP stale decoration;
- cross-repository link extraction;
- Ladybug graph construction and promotion.

PR impact Step 1 follows the same boundary: Git diff validation only; no
catalog delta processing. It is a read-only trace over the active repo-v1
snapshot and does not add delta planning or execution to the full-rebuild
path.
Git diff validation only; no catalog delta processing.

## Reusable existing components

- `catalog/source_snapshot.py` for exact committed Git source materialization;
- `catalog/refresh_transaction.py` for locking and atomic candidate promotion;
- existing parser language extractors;
- existing relationship extraction, resolution, and classification leaf logic;
  V1 owns the snapshot-scoped relationship persistence and validation step;
- SQLite connection and integrity-validation helpers;
- focused parser, catalog, and source-snapshot tests;
- existing query scripts after the core schema stabilizes.

The current `scripts/refresh_workspace.py` is a reference for useful
validation and provenance behavior, not the new orchestration foundation. Its
delta, fingerprint, stale-evidence, recovery, quality-contract, and graph
coordination responsibilities are excluded from the new path.

## V1 tool decision

Market-tool research was performed against the V1 constraints: Python 3.12,
`ia-main` only, immutable Git-commit evidence, fresh SQLite candidates, local
deterministic execution, and no delta, graph, or MCP requirements.

### Use the existing project-compatible stack

- Native Git CLI through `catalog/source_snapshot.py` for commit resolution,
  tree enumeration, blob reads, mode validation, and exact source bytes.
- `catalog/refresh_transaction.py` for the refresh lock, parent CAS, SQLite
  backup, and atomic candidate promotion.
- `load_workspace_manifest()` for the existing repository configuration
  contract.
- A V1-local language classification map in `catalog/repo_v1.py`, including
  `ia-main`-specific mappings for `.wfl`, `.map`, `.shortcuts`, `.xsd`, and
  `.wsdl`.
- Python standard-library `sqlite3` for the V1 candidate database.
- Existing Tree-sitter bindings for later symbol extraction.
- Existing `pytest` test infrastructure and temporary Git fixtures.

This stack is already present in the checkout, has local tests, and avoids
introducing another source or database abstraction.

### Do not add new runtime tools to V1

- Dulwich: retain only as a possible fallback if Git CLI availability becomes
  a real constraint.
- pygit2/libgit2: defer because native library and FFI installation add
  complexity without a current need.
- GitPython: already available but redundant with the proven subprocess Git
  implementation.
- Universal Ctags: defer; its JSONL tags are useful for possible later symbol
  coverage but would introduce a second symbol model.
- SCIP: defer; it is a code-navigation interchange protocol, not the V1
  committed-file inventory contract.
- Semgrep, CodeQL, hosted code-intelligence services, and similar analyzers:
  defer to later semantic/security phases.
- APSW: defer; standard `sqlite3` is sufficient for current candidate and
  promotion behavior.
- DuckDB: defer; V1 does not require analytical reporting.

Hypothesis may be considered later as a test-only dependency for generated
Git-path and provenance properties. It is not required for the first slice.

### V1 boundaries

Do not use the legacy `parser.scan_repo.scan()` for V1 inventory because it
walks mutable checkout bytes and writes legacy-schema facts. Use committed
`GitTreeEntry` records from `source_snapshot.py` instead.

Do not use the full `catalog/db.py:init_db()` or
`validation.validate_catalog_connection()` for V1 because they target the
large legacy catalog schema. V1 uses its focused schema and direct SQLite
validation.

### V1 inventory inclusion policy

The V1 inventory is derived only from the target commit's Git tree. Before
reading or materializing blob bytes, V1 excludes:

- any path containing a dot-directory component, such as `.github`, `.idea`,
  or `.vscode`;
- the configured `ia-main.ignore_filenames` basenames, currently including
  `.gitignore`, `.gitkeep`, `.gitattributes`, and `Makefile`;
- filenames matching the configured `ia-main.ignore_filename_prefixes`,
  currently `.env`;
- files with suffixes in the configured `ia-main.ignore_suffixes`, matched
  case-insensitively. The current list includes binary, generated, document,
  data, and presentation assets such as `.bin`, `.csv`, `.doc`, `.docx`,
  `.md`, `.pdf`, `.png`, `.xls`, and `.xlsx`.

Each repository may additionally define optional `ignore_paths`,
`ignore_filenames`, `ignore_filename_prefixes`, and `ignore_suffixes` lists in
`config/workspace_repos.yaml`. Paths are repository-relative prefixes;
filenames are basenames; suffixes are dot-prefixed and matched
case-insensitively. All lists are validated, normalized, deduplicated after
normalization, and stored in deterministic sorted order. `ignore_paths` must be
relative Git/POSIX paths below the repository root; root-only (`.`/`./`) and
Windows-separator forms are rejected. For `ia-main`, the current path entries
include:

```yaml
ignore_paths:
  - app/resources/thirdparty
```

These rules are V1-local. Legacy `parser.scan_repo` and delta behavior are not
changed. V1 uses its local language map rather than the legacy parser helper.
The V1 CLI displays a file-progress indicator while writing retained inventory
rows; library callers remain quiet unless they pass `show_progress=True`.

### V1 build metadata is deliberately minimal

V1 is full-rebuild-only. The focused `catalog_builds` table must not contain
mode-planning fields such as `requested_mode` or `effective_mode`.

Its lifecycle is limited to:

```text
building -> validated -> active
```

Failed candidates are rolled back, closed, and deleted; they are not retained
as `failed` rows. The promoted catalog's `.previous` file is a filesystem
promotion artifact only and is not represented as a `previous` build state in
the V1 database. Automatic recovery remains deferred.

Official references for deferred alternatives:

- [Dulwich](https://www.dulwich.io/)
- [pygit2](https://www.pygit2.org/)
- [Tree-sitter](https://tree-sitter.github.io/tree-sitter/index.html)
- [Universal Ctags JSON output](https://docs.ctags.io/en/stable/man/ctags-json-output.5.html)
- [SCIP](https://scip-code.org/)
- [SQLite Online Backup API](https://www.sqlite.org/backup.html)
- [APSW SQLite differences](https://rogerbinns.github.io/apsw/pysqlite.html)
- [pytest](https://docs.pytest.org/en/stable/contents.html)
- [Hypothesis](https://hypothesis.readthedocs.io/en/latest/)

## Component delivery rule

Each component must be complete in itself before the next component begins.
Every component requires:

1. an explicit input/output contract;
2. storage and extraction behavior;
3. diagnostic and failure semantics;
4. focused unit and falsifying tests;
5. integration into the candidate pipeline;
6. an acceptance result recorded before proceeding.

Do not add speculative scaffolding for later phases.

## Phase closure evidence

Record accepted phase evidence in one committed companion document:
`docs/design/repo_v1_phase_closure.md`.

Each phase record must include:

- phase and scope;
- the `repo-v1` implementation commit and target `ia-main` commit;
- each acceptance requirement;
- the exact test or command that proves it;
- the observed result;
- remaining gaps and explicitly deferred decisions;
- reviewer and date.

A phase is accepted only when every required acceptance row has passing
evidence and the remaining-gap list is empty. If a certified implementation
file changes afterward, that phase returns to `in_progress` until its evidence
is rerun.

The closure record is audit evidence, not runtime state. Any digest recorded in
it is for review and repeatability only; repository fingerprints are not V1
promotion or readiness gates.

Phase 0 evidence must cover fresh candidate creation, target-commit recording,
candidate isolation, failed-candidate cleanup, active-database preservation,
CAS protection, atomic first and replacement promotion, and the minimal
`building -> validated -> active` lifecycle.

Phase 1 evidence must cover filtered target-tree equivalence, path/blob/mode/
size/language/commit provenance for retained rows, working-tree independence,
repeated-build equivalence, rename and deletion behavior, unsupported Git
objects, exclusion-policy coverage, and language-classification coverage.

The minimum repeatable evidence commands are:

```text
pytest -q tests/test_repo_v1.py validation/test_source_snapshot.py
git diff --check
git status --short --branch
```

The separate closure document is created only when the first phase is actually
accepted; do not add placeholder acceptance records.

## KISS/YAGNI admission gate

Before designing or implementing any component, answer both questions with
source-backed evidence:

### KISS

- Is this the smallest clear component that solves the current problem?
- Can an existing module or simple sequential step solve it?
- Does it introduce a new abstraction, state machine, mode, or dependency that
  is not strictly required?

### YAGNI

- Is the component required by the current accepted slice or an actual current
  consumer?
- What concrete failure occurs if it is omitted now?
- Is there evidence in the source, schema, tests, or operator workflow that it
  is needed today?

A component is admitted only when it passes both KISS and YAGNI. A future
possibility is not sufficient justification. Failed or unproven proposals are
recorded as deferred decisions without placeholder schema, interfaces, or
builder registration.

This gate applies to infrastructure as well as domain features. In particular,
do not add generic builder frameworks, delta support, graph plumbing, broad
diagnostic abstractions, compatibility layers, or recovery machinery merely
because a later phase might need them.

## Phases

### 0. Foundation and provenance

Define the fresh core database initialization, build metadata, repository
identity, target commit, source provenance, candidate lifecycle, and promotion
boundary.

Keep the candidate lifecycle minimal: `building`, `validated`, and `active`.
Do not add mode state, failed-build persistence, or generic recovery metadata
to the V1 schema.

Acceptance:

- a candidate can be created without touching the active database;
- the target commit is recorded and validated;
- failed preparation deletes the candidate and leaves the active database
  unchanged;
- successful promotion is atomic.

### 1. Immutable Git inventory

Build deterministic `repos` and `files` facts from the target commit. Record
repository-relative paths, Git blob/object identity, file mode, size, and
language classification for retained rows. Apply the V1 inventory inclusion
policy before blob materialization, and do not use mutable checkout bytes as
evidence.

Acceptance:

- same commit produces equivalent inventory on repeated builds;
- deleted and renamed paths are represented correctly;
- unsupported Git objects fail closed;
- excluded dot-directories, filenames, suffixes, and configured paths are
  absent from the retained inventory;
- inventory facts have commit provenance.

### 2. Symbols

Adapt existing extractors to read the immutable snapshot and write only to the
candidate. Persist symbols with file provenance. Persist parser diagnostics in
the candidate. The V1 adapter in `catalog/repo_v1_symbols.py` reuses only the
leaf extractors from `parser/extractors`; it does not reuse the legacy symbol
orchestration or read the mutable checkout. Each file is isolated by a
savepoint. Parser failures retain the inventory row, record an error with the
target-commit provenance, and emit no symbols. Stable keys are derived from
symbol facts and duplicate order, so repeated builds do not depend on row IDs.
Tree-sitter `ERROR` and `MISSING` nodes in Java, JavaScript, and PHP are parser
failures, including files that would otherwise emit partial symbols. Snapshot
read failures and SQLite write failures fail the candidate; only extractor
exceptions are retained as non-blocking parser diagnostics.
Candidate validation checks symbol/diagnostic ownership, diagnostic
provenance, symbol fact shape, and SQLite integrity before promotion.

Acceptance:

- parser-failed files retain inventory and diagnostics;
- parser-failed files produce no symbols;
- successful files produce deterministic symbols;
- candidate validation rejects unexpected ownership or integrity errors.

### 3. Relationships

Run the V1 relationship adapter in `catalog/repo_v1_relationships.py` after
V1 symbols have been extracted, while the same immutable `SourceSnapshot` is
materialized. The adapter reads source bytes only from
`SourceSnapshot.snapshot_root`, loads symbols from the candidate, and reuses
only the compatible leaf extractor/model/resolution logic from
`parser/extract_relationships.py`. It writes repository- and file-owned
relationships to the V1 candidate, retains resolved IDs and explicit
unresolved targets, preserves evidence/language/confidence/resolution and
extractor provenance, isolates each file with a savepoint, and rejects the
candidate on snapshot, write, ownership, reference, provenance, or integrity
failure. The legacy relationship orchestration and persistence path is not
called.

Acceptance:

- relationships reference valid candidate files/symbols where resolved;
- unresolved relationships remain explicit diagnostics/evidence, not guesses;
- repeated builds produce equivalent relationship facts;
- foreign-key and semantic validation pass.

### 4. Core validation, promotion, and read-only queries

Complete the core candidate gate and expose only the minimal read-only query
surface needed to inspect repositories, files, symbols, relationships, and
diagnostics. Query/MCP compatibility with the old catalog is not required yet.

Acceptance:

- SQLite integrity and foreign-key checks pass;
- semantic validation passes;
- failed candidates cannot replace the active database;
- core read-only queries return source-grounded facts.

### 5. Entity occurrences

Implement only immutable, source-backed `.ent` declarations in the V1 full
rebuild path:

```text
target Git commit
  -> SourceSnapshot.snapshot_root retained `.ent` bytes
  -> entity_nodes / entity_occurrences / entity_diagnostics
  -> candidate ownership, provenance, uniqueness, FK, and integrity validation
  -> atomic promotion
```

The extractor is a V1-local `catalog/repo_v1_entities.py` adapter. It scans
retained `.ent` inventory rows and reads only target-commit snapshot bytes. Its
lexical grammar recognizes top-level `$kSchemas[quoted-literal-key] =`
declarations, preserves exact source evidence, and suppresses all facts from a
file with an unclosed lexical state or unbalanced delimiter. It extracts only
literal `module`, `table`, `view`, and lowercase boolean `dummy` metadata from
initial arrays and direct nested updates. Repeated keys coalesce within a file;
the natural occurrence key is `(repo_id, source_file_id, source_key)`, while
identical keys in different files remain separate occurrences.

Literal snapshot-only include/require resolution and direct RHS references,
including constrained `EntityManager::inheritEnts`, may index declarations
from retained `.ent` files. Missing, dynamic, ambiguous, and cyclic
include/reference cases emit stable error diagnostics and never create guessed
facts. Static direct `inheritEnts` bases and overlays may contribute inherited
metadata, but an unknown or dynamic overlay emits `entity_reference_dynamic`
and never merges a known base; only directly proven literals remain eligible.
Missing metadata may retain a partial occurrence with NULL fields;
conflicting literal metadata is NULL with one conflict diagnostic.

Every candidate `.ent` inventory row must correspond to a retained
`SourceSnapshot.entries` path. A candidate/snapshot mismatch raises
`SourceSnapshotError` rather than silently omitting the entity. Include
resolution is limited to retained snapshot paths and does not use basename
fallbacks or mutable checkout files.

The schema uses canonical entity identity in `entity_nodes` and repository/file
facts in `entity_occurrences`, with source-commit, extractor, and canonical JSON
evidence on every fact and diagnostic. Diagnostic identity is the SHA-256 of
canonical JSON containing repository, file, source key, code, and evidence.
`repo_v1_entities_v1` is the only extractor value. Candidate validation checks
entity ownership, `.ent` file scope, source-commit parity, name/key parity,
natural uniqueness, diagnostic ownership/code/provenance, foreign keys, and
SQLite integrity. Per-file parser, identity, metadata, include, and reference
issues are source-backed non-blocking diagnostics; snapshot/global read errors
and candidate integrity/provenance failures preserve the active database and
remove the candidate.

For promotion, an absent active database is the only valid fresh-initialization
state. Existing empty, malformed, or otherwise incompatible active files fail
closed. The repo-v1 Phase 6 workflow has one explicit additive upgrade boundary:
an otherwise valid pre-Phase-6 repo-v1 active catalog may be missing only the
four new OpenAPI/REST tables; the workflow builds a complete current-schema
candidate and atomically promotes it while preserving the old file as
`catalog.db.previous`. No in-place migration or broad schema bypass is used.

Do not add entity mappings, entity roots, companion/OpenAPI/REST/workflow/UI
facts, graph, MCP/query compatibility, delta refresh, migrations,
multi-repository support, JSONL intermediates, or legacy entity-builder
orchestration in this phase.

Acceptance:

- focused and existing V1 tests pass;
- repeated full builds produce equivalent normalized entity facts;
- dirty checkout changes do not affect entity facts;
- malformed lexical state suppresses facts but retains diagnostics;
- candidate ownership, provenance, FK, integrity, and active-preservation
  failures are covered;
- promoted build evidence records target commit and entity counts;
- closure records exact commands/results and leaves later mappings and
  compatibility work explicitly deferred.

### 6. OpenAPI/REST

Status: **accepted** on `repo-v1` for target commit
`e7fbab69da69cd605076eec74ee456066514adaf`; final evidence is recorded in
`docs/design/repo_v1_phase_closure.md`.

Implement Phase 6 as three sequential, independently accepted slices after
entity occurrences are accepted:

#### 6A — immutable OpenAPI document index

Read only committed `SourceSnapshot` bytes. Include `.yaml` files below
`app/source/openapispec`; exclude every `.yml`, paths containing `template`,
and filenames beginning with `template` (case-insensitive). Do not call
`parser.scan_repo`, read mutable checkout files, or reuse legacy OpenAPI
tables/scanners. Create only:

```text
openapi_documents(
  id, repo_id, file_id, path, kind, document_key,
  source_commit_sha, evidence, extractor
)
```

Index one successfully parsed, in-scope YAML mapping per file. `document_key`
is the SHA-256 of canonical JSON containing `repo_key` and the repository
relative path. `kind` is exactly one of `history`, `schema`, `operations`,
`view`, `uimeta`, `viewmeta`, `paths`, `components`, `security`, `resource`,
`actions`, `events`, or `unknown`. Do not extract operations, entity mappings,
`$ref` facts, or relationships. Malformed, non-mapping, invalid-UTF-8, or
duplicate-key YAML emits a diagnostic and no document row. Snapshot, Git,
database, and integrity failures abort the candidate.

#### 6B — exact OpenAPI entity links

Read only successfully indexed documents and only their direct top-level
`x-mappedTo` scalar. Create only:

```text
openapi_entity_links(
  id, repo_id, document_id, entity_occurrence_id, mapped_value,
  match_key, link_key, source_commit_sha, evidence, extractor
)
```

Trim and case-fold the scalar, then match exactly against committed `.ent`
filename stems represented by `entity_occurrences`. Create a link only for
exactly one occurrence. Never use filename, slug, path, canonical-name,
module, title, fuzzy, or manifest fallbacks, and never create entities or
occurrences. Blank, non-string, path-like, malformed, `__custom__`,
zero-match, and multiple-match values emit the required non-blocking source
diagnostics: `OPENAPI_X_MAPPEDTO_BLANK`, `OPENAPI_X_MAPPEDTO_CUSTOM`,
`OPENAPI_X_MAPPEDTO_INVALID`, `OPENAPI_X_MAPPEDTO_ZERO_MATCHES`, and
`OPENAPI_X_MAPPEDTO_MULTIPLE_MATCHES`.

#### 6C — REST endpoint facts

Read committed snapshot bytes from indexed documents under `/paths/` and
create only:

```text
rest_endpoints(
  id, repo_id, document_id, endpoint_key, path_template, http_method,
  operation_id, source_pointer, source_commit_sha, evidence, extractor
)
```

Extract only `get`, `post`, `put`, `patch`, `delete`, `head`, `options`, and
`trace`. Preserve leading-slash path templates exactly, store lower-case
methods, and read `operationId` only when it is a direct non-empty scalar.
Never infer operation IDs or entity ownership. Use RFC 6901 pointers such as
`/paths/~1bills/get`. `endpoint_key` is the SHA-256 of canonical JSON
containing repository key, document path, path template, lower-case method,
and source pointer. Duplicate operations in one document fail uniqueness
validation; identical routes in different documents remain separate facts.
Invalid paths, path keys, and operation objects emit diagnostics and no facts.
Ignore and do not traverse or store `$ref`, external references, or transitive
schema facts.

#### Shared diagnostics and acceptance boundary

Create `openapi_diagnostics(id, repo_id, file_id, document_id, phase,
diagnostic_key, severity, code, message, source_commit_sha, evidence,
extractor)`, with nullable `document_id` for parse failures. A diagnostic key
is the SHA-256 of canonical JSON containing repository, path, phase, code, and
normalized evidence. Diagnostics are deterministic and ordered by path, phase,
code, and evidence. Every fact belongs to the candidate repository and file;
every `source_commit_sha` equals the target commit; and all stable keys are
recomputable from stored fields. Foreign-key, ownership, provenance,
uniqueness, malformed-fact, and SQLite integrity failures reject the
candidate. Parser and unresolved-source diagnostics suppress affected facts
but do not reject an otherwise valid candidate. Failed candidates preserve
active bytes, remove temporary candidates, and successful promotion remains
atomic.

Explicitly deferred: legacy OpenAPI scanners/linkers/REST builders,
`catalog/schema.sql` migrations, mapping manifests, filename/name/module
heuristics, `$ref` traversal/reference tables, query/MCP compatibility, graph
projection, delta refresh, automatic recovery, multi-repository behavior, UI,
workflow, security, and production database migration/replacement.

Acceptance is sequential. 6A must prove exact YAML scope, deterministic
indexed rows, malformed-file diagnostics, dirty-checkout immunity, isolated
repeatability, and no legacy/mutable reads. 6B must prove exact unique
`x-mappedTo` links and all five required diagnostics without heuristic links.
6C must prove deterministic endpoint facts, invalid-input diagnostics,
duplicate rejection, `$ref` non-traversal, provenance/pointer/key validity,
repeatability, dirty-checkout immunity, candidate failure, and active
preservation. Phase 6 is complete only after the Phase 0–5 regression suite,
two isolated full-build normalized hashes, table/diagnostic counts, SQLite and
foreign-key checks, and closure evidence are recorded in
`docs/design/repo_v1_phase_closure.md`, with `TODO.md` and this plan updated.

### 7. UI

#### 7A — Immutable ActionUI XML facts

Phase 7A is accepted as a narrow snapshot-only slice. It materializes exactly
`ui_surfaces`, `ui_artifacts`, `ui_fields`, `ui_events`, `ui_includes`, and
`ui_diagnostics` from committed `*_form.xml` bytes. Facts use deterministic
stable keys, canonical evidence, SHA-256 source evidence, normalized include
paths, and candidate ownership/provenance/integrity validation. Parser errors
are persisted as errors at the V1 boundary; field identity, missing XInclude
href, unresolved include, and invalid include conditions remain warnings.

The accepted upgrade boundary preserves the existing Phase 5 -> Phase 6
additive allowance and adds the complete Phase 7A table family. A valid
pre-Phase-7A catalog missing all six UI tables can upgrade atomically; a
partial UI table family is incompatible. No in-place migration, mutable
checkout read, legacy UI synchronization, recursive include parsing, stale
retention, event-call resolution, or UI/entity link is included.

#### 7B — Immutable NextGen UI facts

Phase 7B is accepted as a snapshot-only extension of Phase 7A. It materializes
`nextgen_families`, `nextgen_artifacts`, and `nextgen_diagnostics` from the
retained bytes of `.uimeta`, `.viewmeta`, and `.view` YAML artifacts through
`parser.ui.nextgen.extract_nextgen_families`. It preserves canonical stable
keys, canonical evidence, raw SHA-256 source evidence, parser severity, and
candidate ownership/provenance/integrity validation. Entity references,
entity-mapping diagnostics, PHP loaders, JavaScript handlers, event-call
resolution, and UI/entity links remain outside this slice.

The accepted parent boundary is ordered and additive: a complete Phase 6
parent may build the complete Phase 7A/7B candidate; a complete Phase 7A
parent may build the complete Phase 7B candidate; a complete Phase 7B parent
must contain both complete table families. Partial families and a Phase 7B
family without Phase 7A are incompatible. No in-place migration is added.
Scheduling and repairing the legacy Phase 6 upgrade fixture are deferred as
operator migration work.

Explicitly deferred from Phase 7: PHP loaders, JavaScript handlers, event-call
resolution, UI/entity links, legacy `catalog/ui_sync.py`, last-known-good/stale
retention, migration scheduling, delta refresh, CLI/MCP, graph,
multi-repository support, workflow, and security.

### Pre-Phase-8 closure remediation

Before Phase 8 implementation, repair the parent-boundary regression coverage
without changing `catalog/repo_v1.py` or the V1 schema. Keep
`test_phase6_upgrade_and_partial_schema_rejection` limited to two cases:

- a valid Phase 6 parent with both the complete Phase 7A and complete Phase 7B
  table families absent, which must rebuild and promote successfully;
- a partial Phase 7A parent with complete Phase 7B retained, which must raise
  `CatalogPromotionError` and preserve the active and `.previous` databases.

Add the separate test
`test_phase7b_parent_without_phase7a_rejected` for a parent with complete
Phase 7B tables and no Phase 7A tables. It must raise
`CatalogPromotionError`, preserve active and `.previous` bytes, and leave no
candidate database. Both test selectors are required for closure; there is no
optional test split or alternate acceptance path.

The remediation is test-only for the parent boundary. Phase 8 remains blocked
until both selectors and the repo-v1 regression suite pass with no unexpected
failures.

### 8. Workflow and security

Add workflow and security facts as independent builders with independent
validation and acceptance tests.

### 9. Retirement

After the new path is accepted, remove or disable the old delta-heavy
orchestration, update operator documentation, and make the V1 full rebuild the
canonical path.

## First implementation slice

Start with foundation/provenance plus immutable `repos`/`files` inventory for
`ia-main`. Do not add symbols, relationships, graph, delta, or MCP work until
that slice is complete and accepted.
