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

## Reusable existing components

- `catalog/source_snapshot.py` for exact committed Git source materialization;
- `catalog/refresh_transaction.py` for locking and atomic candidate promotion;
- existing parser language extractors;
- existing relationship resolution and persistence logic;
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

Run existing relationship extraction against the candidate symbols and
immutable source. Preserve explicit resolution classes and source evidence.

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

Add repository-local entity occurrences and their source-backed mappings as a
separate complete component. Keep canonical entity identity separate from
repository-local occurrence facts.

### 6. OpenAPI/REST

Add OpenAPI indexing, reviewed mappings, and REST endpoint facts only after
entity occurrences are accepted.

### 7. UI

Add UI facts with UI-specific diagnostics and, only where demonstrated by
source evidence, narrowly scoped last-known-good behavior.

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
