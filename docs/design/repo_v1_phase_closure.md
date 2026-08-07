# Repo Intelligence V1 Phase Closure

Reviewer: Codex (automated acceptance), 2026-08-06

Implementation commits:

- `7fafcd0d777af333252396714a8ff9416d248c6a` — V1 implementation and oracle
  acceptance work;
- `ee333a93fdf64369d6b3674f429ab277cdf7d73b` — canonical active database path
  normalization;
- `4010479` — V1 Phase 2 Symbols implementation and plan update;
- `d1d230a` — Symbols candidate-validation regression coverage.
- `7b8162e` — complete Phase 2 symbol acceptance evidence.
- `6205308` — harden Phase 2 parser diagnostics and snapshot-I/O failure handling.
- `6684f8df6b3f18df7fe884c7dfafd8abb2a11d6e` — Phase 3 Relationships
  implementation, schema, validation, tests, and acceptance integration.
- `0462142` — ambiguity-safe relationship resolution and parser-failure,
  diagnostic-ownership, and ambiguity regression evidence.
- `fd909d7` — Phase 5/Entity Occurrences implementation, schema, snapshot
  extraction, candidate validation, tests, and closure evidence.

The Phase 5 Entity Occurrences implementation is committed in `fd909d7`.
The closure section below retains the historical Phase 4 label used by this
document for the Entity Occurrences slice.

Phase 0 and Phase 1 evidence below was rerun against the committed Phase 2
implementation. Phase 2 evidence was rerun against the latest implementation
and test commits.

Target `ia-main` commit: `e7fbab69da69cd605076eec74ee456066514adaf`

Canonical V1 active database path: `catalog/catalog.db`. The V1 library
default and `--active-db` CLI default use this same path, matching the
promoted database recorded below and the repository-wide `config.CATALOG_DB`
default.

The target checkout was verified at `/Users/aritra.ghosh/projects/main`, on
branch `main`, with a clean status before acceptance. The active V1 database
was subsequently rebuilt and promoted through the V1 path at the target commit;
no legacy catalog refresh, graph build, or main-branch modification was run.

The promoted V1 database evidence was independently verified read-only:

```json
{"active_db":"/Users/aritra.ghosh/projects/intacct-repo-intelligence/catalog/catalog.db","build_token":"6552fdea0c004013a98f4bffb4001dc0","file_count":23877,"promoted":true,"relationship_count":174560,"symbol_count":166280,"symbol_diagnostic_count":456,"target_commit_sha":"e7fbab69da69cd605076eec74ee456066514adaf"}
```

This replaces the stale primary evidence from the earlier Phase 0/1 build.

## Current promoted evidence by phase

Exact Phase 2 Symbols evidence JSON, independently verified read-only:

```json
{"active_db":"/Users/aritra.ghosh/projects/intacct-repo-intelligence/catalog/catalog.db","build_token":"6552fdea0c004013a98f4bffb4001dc0","file_count":23877,"promoted":true,"symbol_count":166280,"symbol_diagnostic_count":456,"target_commit_sha":"e7fbab69da69cd605076eec74ee456066514adaf"}
```

Exact Phase 3 Relationships evidence JSON, independently verified read-only:

```json
{"active_db":"/Users/aritra.ghosh/projects/intacct-repo-intelligence/catalog/catalog.db","build_token":"6552fdea0c004013a98f4bffb4001dc0","file_count":23877,"promoted":true,"relationship_count":174560,"target_commit_sha":"e7fbab69da69cd605076eec74ee456066514adaf"}
```

The active database contains 174,560 relationship rows, all owned by the
single `ia-main` repository, with extractor provenance `phase2_regex_mvp`;
the active build validation summary records the same relationship count and
target commit.

## Phase 0 — Foundation and provenance

Scope: fresh V1 SQLite candidate, build provenance, candidate lifecycle, CAS,
and atomic first/replacement promotion for `ia-main`.

Status: **accepted**

| Acceptance requirement | Exact evidence | Observed result |
| --- | --- | --- |
| Fresh candidate creation is isolated from the active database | `./.venv/bin/python -m pytest -q tests/test_repo_v1.py::test_unpromoted_candidate_does_not_touch_active` | Passed; active bytes were unchanged and no candidate remained. |
| Target commit is recorded and validated | `./.venv/bin/python -m pytest -q tests/test_repo_v1.py::test_same_commit_uses_committed_blobs_and_is_deterministic tests/test_repo_v1.py::test_inventory_matches_filtered_git_tree_oracle` | Passed; repository and retained file provenance matched the requested full commit. |
| Failed source preparation preserves active and previous databases | `./.venv/bin/python -m pytest -q tests/test_repo_v1.py::test_failed_source_preparation_preserves_active_and_previous` | Passed; both filesystem artifacts were byte-for-byte unchanged and the candidate was deleted. |
| Unpromoted builds leave no temporary candidate database | `./.venv/bin/python -m pytest -q tests/test_repo_v1.py::test_unpromoted_candidate_does_not_touch_active` | Passed. |
| Injected backup and promotion failures preserve recoverable state | `./.venv/bin/python -m pytest -q tests/test_repo_v1.py::test_injected_backup_failure_preserves_active_and_previous tests/test_repo_v1.py::test_injected_candidate_replace_failure_preserves_active_and_previous` | Passed; active and `.previous` remained unchanged and candidates were deleted. |
| CAS detects an active-generation change before promotion | `./.venv/bin/python -m pytest -q tests/test_repo_v1.py::test_cas_detects_active_generation_change` | Passed with `CatalogPromotionError` and compare-and-swap diagnostic. |
| First promotion is atomic | `./.venv/bin/python -m pytest -q tests/test_repo_v1.py::test_first_promotion_creates_active_catalog_without_previous` | Passed; active was created without a V1 `.previous` database state. |
| Replacement promotion is atomic and retains only the filesystem previous artifact | `./.venv/bin/python -m pytest -q tests/test_repo_v1.py::test_replacement_promotion_retains_only_filesystem_previous_artifact` | Passed; previous contained the prior logical inventory and active contained the replacement. |
| V1 schema contains no mode planning, diagnostics, previous, or failed build state | `./.venv/bin/python -m pytest -q tests/test_repo_v1.py::test_v1_schema_has_only_minimal_build_lifecycle` | Passed; only `building`, `validated`, and `active` are allowed. |
| Lifecycle is `building -> validated -> active` | `./.venv/bin/python -m pytest -q tests/test_repo_v1.py::test_v1_schema_has_only_minimal_build_lifecycle tests/test_repo_v1.py::test_first_promotion_creates_active_catalog_without_previous` | Passed; no `previous` or `failed` status exists in the V1 schema or promoted database. |
| Promoted active V1 database records the supplied build evidence | Read-only SQLite verification shown above | Passed; active build token, target commit provenance, file count, and active status matched the supplied evidence. |
| CLI and library use the canonical active database path | `./.venv/bin/python -m pytest -q tests/test_repo_v1.py::test_v1_cli_and_library_use_canonical_active_database_path` | Passed; both defaults resolve to `catalog/catalog.db`. |

Remaining gaps: none for Phase 0.

Deferred decisions: automatic recovery, generic stale restoration, delta
refresh, fingerprints as readiness/promotion gates, graph recovery, and legacy
catalog compatibility remain deferred by the V1 plan.

## Phase 1 — Immutable Git inventory

Scope: complete committed-tree inventory for `ia-main`, including path, Git
blob identity, mode, size, language classification, target commit provenance,
and V1-only committed-tree filtering.

Status: **accepted**

| Acceptance requirement | Exact evidence | Observed result |
| --- | --- | --- |
| Inventory uses committed Git tree/blob data and ignores mutable checkout bytes | `./.venv/bin/python -m pytest -q tests/test_repo_v1.py::test_same_commit_uses_committed_blobs_and_is_deterministic validation/test_source_snapshot.py::SourceSnapshotTests::test_materializes_raw_commit_bytes_and_mode_then_cleans_up` | Passed; dirty working-tree bytes and untracked files were excluded. |
| Every retained V1 file row matches the filtered Git-tree oracle | `./.venv/bin/python -m pytest -q tests/test_repo_v1.py::test_inventory_matches_filtered_git_tree_oracle` | Passed for every retained target-tree row: path, blob ID, mode, size, language, and source commit SHA; the oracle independently owns language expectations and loads manifest filtering policy. |
| V1 filtering excludes dot-directories, manifest-configured filenames/prefixes/suffixes, and manifest-configured paths before blob materialization | `./.venv/bin/python -m pytest -q tests/test_repo_v1.py::test_inventory_applies_v1_tree_filters_and_manifest_ignore_paths tests/test_repo_v1.py::test_manifest_normalizes_v1_ignore_lists validation/test_multi_repo_migration.py::MultiRepoMigrationTests::test_manifest_rejects_ineffective_ignore_path_syntax` | Passed; case-insensitive configured suffixes, `Makefile`, hidden directory components, `.env*`, and normalized `app/resources/thirdparty/` were absent while ineffective root-only and Windows-separator paths were rejected. |
| Ordinary, executable, empty, and binary files are covered | `./.venv/bin/python -m pytest -q tests/test_repo_v1.py::test_inventory_matches_filtered_git_tree_oracle` | Passed; executable mode, zero size, and unknown binary language were verified. |
| Genuine deletion commits are reflected | `./.venv/bin/python -m pytest -q tests/test_repo_v1.py::test_deletion_commit_removes_deleted_path_from_full_inventory` | Passed; deleted path was absent from the full target inventory. |
| Rename coverage is retained | `./.venv/bin/python -m pytest -q tests/test_repo_v1.py::test_inventory_follows_target_tree_for_renamed_paths` | Passed; old path absent and new path present. |
| Symlinks and gitlinks fail closed | `./.venv/bin/python -m pytest -q tests/test_repo_v1.py::test_failed_source_preparation_preserves_active_and_previous tests/test_repo_v1.py::test_gitlink_is_rejected_by_v1_inventory validation/test_source_snapshot.py::SourceSnapshotTests::test_rejects_symlink_before_yield validation/test_source_snapshot.py::SourceSnapshotTests::test_rejects_gitlink_before_materialization` | Passed; Git modes `120000` and `160000` were rejected before inventory materialization/promotion. |
| Repeated builds have equivalent normalized immutable repository/file fields | `./.venv/bin/python -m pytest -q tests/test_repo_v1.py::test_same_commit_uses_committed_blobs_and_is_deterministic` | Passed; generated IDs, tokens, timestamps, and catalog paths were excluded from comparison. |
| Language classification covers representative and unknown extensions | `./.venv/bin/python -m pytest -q tests/test_repo_v1.py -k 'language_classification or legacy_parser_does_not_define'` | Passed for lowercase, uppercase/mixed-case, V1-local XML/PHP mappings, and unknown extensions. |

Remaining gaps: none for Phase 1.

Deferred decisions: relationships, entity occurrences, OpenAPI/REST, UI,
workflow/security, graph, MCP, and delta refresh remain outside this closure.

## Phase 2 — Symbols

Scope: extract deterministic symbols and parser diagnostics from immutable
target-commit snapshot bytes into the V1 candidate, with file provenance and
candidate ownership/integrity validation. Legacy symbol orchestration and
mutable-checkout reads remain outside the V1 path.

Status: **accepted**

| Acceptance requirement | Exact evidence | Observed result |
| --- | --- | --- |
| Successful supported-language files produce deterministic symbols from committed snapshot bytes | `./.venv/bin/python -m pytest -q tests/test_repo_v1_symbols.py::test_symbols_use_committed_snapshot_bytes_and_are_deterministic` | Passed; PHP and JavaScript symbols matched across repeated builds while the checkout was mutated after the target commit. |
| Symbols retain file/repository ownership and commit provenance | Same focused test; SQL ownership/provenance assertions | Passed; every symbol joined to its candidate file and repository, and file provenance matched the repository target commit. |
| Parser-failed JavaScript, Java, and PHP files retain inventory and record diagnostics | `./.venv/bin/python -m pytest -q tests/test_repo_v1_symbols.py -k parser_failure` | Passed; malformed JavaScript, Java, and PHP rows remained and diagnostics carried the target SHA, including Tree-sitter `ERROR` and `MISSING` cases. |
| Parser-failed files produce zero symbols and do not reject the candidate | Same focused test | Passed; both repeated candidates promoted and the malformed file had zero symbols. |
| Repeated builds produce equivalent normalized symbol and diagnostic facts | Same focused test | Passed; repeated symbol rows/stable keys and diagnostic facts matched; generated IDs were excluded. |
| Candidate validation rejects invalid symbol facts and unexpected ownership/integrity failures | `./.venv/bin/python -m pytest -q tests/test_repo_v1_symbols.py::test_candidate_validation_rejects_invalid_symbol_line_ranges tests/test_repo_v1_symbols.py::test_candidate_validation_rejects_orphan_symbols` | Passed; invalid line ranges and foreign-key/orphan ownership were rejected before promotion. |
| Unsupported inventory languages produce no symbol facts | Same deterministic-symbol test | Passed; `notes.unknown` remained an inventory row with `unknown` language and zero symbols. |
| Candidate failure cannot change the active database | `./.venv/bin/python -m pytest -q tests/test_repo_v1_symbols.py::test_invalid_symbol_candidate_leaves_active_database_unchanged` | Passed; injected invalid symbol ownership rejected validation, active bytes were unchanged, and the candidate was deleted. |
| Snapshot read failures fail closed rather than becoming parser diagnostics | `./.venv/bin/python -m pytest -q tests/test_repo_v1_symbols.py::test_snapshot_read_failure_rejects_candidate_and_preserves_active` | Passed; the read error propagated as `RepoV1Error`, active bytes were unchanged, and the candidate was deleted. |
| Symbols do not expand the repository-scan boundary | `rg -n "\\b(scan|walk_repo|apply_changed_paths)\\s*\\(" catalog/repo_v1.py catalog/repo_v1_symbols.py scripts/refresh_repo_v1.py` | Passed; no prohibited legacy scan calls were found. Symbol extraction reads only `SourceSnapshot.snapshot_root` bytes. |

Remaining gaps: none for the implemented Phase 2 Symbols slice.

Operational note: the ignored canonical database `catalog/catalog.db` was
rebuilt and promoted through the normal V1 path. Read-only verification on
2026-08-06 found the active build targeting `ia-main` commit
`e7fbab69da69cd605076eec74ee456066514adaf`, with 23,877 files, 166,280 symbol
rows, 456 symbol-diagnostic rows, and 174,560 relationship rows. The Symbols
and Relationships tables are therefore present in the active database; no
migration was used or added.

Deferred decisions: OpenAPI/REST, UI, workflow/security, graph, MCP/query
compatibility, and delta refresh remain deferred.

## Phase 3 — Relationships

Scope: snapshot-scoped relationship extraction for `ia-main`, using candidate
symbols and only target-commit bytes, with explicit unresolved targets,
relationship provenance, candidate validation, and atomic promotion.

Promoted evidence JSON:

```json
{"active_db":"/Users/aritra.ghosh/projects/intacct-repo-intelligence/catalog/catalog.db","build_token":"6552fdea0c004013a98f4bffb4001dc0","file_count":23877,"promoted":true,"relationship_count":174560,"target_commit_sha":"e7fbab69da69cd605076eec74ee456066514adaf"}
```

KISS/YAGNI admission gate:

- KISS passed: one V1-local adapter performs one sequential relationship pass
  inside the existing snapshot/candidate transaction; it reuses only the
  existing relationship leaf extractors, model shapes, and resolution logic.
  No legacy orchestration, mutable-checkout read, migration, delta mode,
  graph, MCP, query compatibility, parser framework, or dependency was added.
- YAGNI passed: relationships are the current Phase 3 acceptance component
  and are required by the V1 plan before Entity Occurrences. No generic
  builder, recovery, or compatibility surface was admitted.

Status: **accepted**

| Acceptance requirement | Exact evidence | Observed result |
| --- | --- | --- |
| Resolved relationships reference valid candidate symbols and retain source/repository ownership | `./.venv/bin/python -m pytest -q tests/test_repo_v1_relationships.py::test_relationships_resolve_and_preserve_unresolved_targets` | Passed; resolved target IDs join candidate symbols, every row joins its candidate file and repository, and source/target names remain present. |
| Unresolved relationships remain explicit without guessed target IDs | Same focused test | Passed; `MissingClass` retained `target_symbol_id IS NULL`, `target_name`, `project_unresolved`, and `unresolved_project_symbol`. |
| Evidence and file provenance come from the target commit, independent of mutable checkout bytes | `./.venv/bin/python -m pytest -q tests/test_repo_v1_relationships.py::test_relationships_are_snapshot_provenance_and_repetition_stable` | Passed; normalized rows repeated identically after checkout mutation, evidence matched committed `child.php`, and repository provenance matched the requested target SHA. |
| Repeated builds produce equivalent normalized relationship facts | Same focused test | Passed; generated IDs were excluded and normalized relationship rows matched. |
| Duplicate relationship handling is deterministic | `./.venv/bin/python -m pytest -q tests/test_repo_v1_relationships.py::test_duplicate_relationships_are_deterministically_deduplicated` | Passed; duplicated extractor output produced one normalized row per relationship fact. |
| Unsupported languages emit no invented relationships | `./.venv/bin/python -m pytest -q tests/test_repo_v1_relationships.py::test_relationships_resolve_and_preserve_unresolved_targets` | Passed; the unsupported `notes.unknown` inventory row had no relationship rows. |
| A failed file emits no partial relationships and cannot replace the active database | `./.venv/bin/python -m pytest -q tests/test_repo_v1_relationships.py::test_failed_relationship_file_has_no_partial_rows_and_preserves_active` | Passed; injected failure rolled back the candidate and preserved active bytes. |
| Parser-failed relationship files retain diagnostics but emit no relationships | `./.venv/bin/python -m pytest -q tests/test_repo_v1_relationships.py::test_parser_failed_relationship_file_retains_diagnostic_without_relationships` | Passed; the malformed PHP file retained a target-commit diagnostic, produced zero symbols, and produced zero relationships. |
| Ambiguous target resolution remains explicit and does not guess | `./.venv/bin/python -m pytest -q tests/test_repo_v1_relationships.py::test_ambiguous_target_resolution_remains_explicitly_unresolved` | Passed; duplicate `SharedClass` candidates retained the target name with `target_symbol_id IS NULL` and `ambiguous_project_symbol`. |
| Invalid ownership and target references reject the candidate | `./.venv/bin/python -m pytest -q tests/test_repo_v1_relationships.py::test_candidate_validation_rejects_cross_file_relationship_source tests/test_repo_v1_relationships.py::test_invalid_relationship_target_reference_rejects_candidate` | Passed; cross-file source ownership failed semantic validation and an invalid target ID failed SQLite foreign-key validation before promotion. |
| Diagnostic ownership rejects the candidate | `./.venv/bin/python -m pytest -q tests/test_repo_v1_relationships.py::test_candidate_validation_rejects_orphan_diagnostic_ownership` | Passed; an orphan diagnostic failed candidate foreign-key validation. |
| Snapshot read failure preserves the active database | `./.venv/bin/python -m pytest -q tests/test_repo_v1_relationships.py::test_snapshot_relationship_read_failure_preserves_active` | Passed; the read error rejected the candidate, deleted its temporary database, and left active bytes unchanged. |
| Existing Phase 0, Phase 1, and Phase 2 Symbols behavior remains green | `./.venv/bin/python -m pytest -q tests/test_repo_v1.py validation/test_source_snapshot.py tests/test_repo_v1_symbols.py` | Passed; all existing V1 foundation, snapshot, inventory, and symbol tests remained green. |

Full Phase 0–3 evidence command:

```text
./.venv/bin/python -m pytest -q tests/test_repo_v1.py validation/test_source_snapshot.py tests/test_repo_v1_symbols.py tests/test_repo_v1_relationships.py
60 passed, 1 warning in 16.44s
```

Reused leaf logic: `parser.extract_relationships.EXTRACTORS`, its
`Relationship`, `FileRow`, and `SymbolRow` model shapes, and the extractor-call
path's existing symbol resolution/classification behavior. The V1 adapter
loads symbols from the candidate, reads only `SourceSnapshot.snapshot_root`,
uses per-file savepoints, and stores the existing `phase2_regex_mvp` extractor
provenance without calling legacy relationship orchestration or persistence.

Remaining gaps: none for the implemented Phase 3 Relationships slice.

Deferred decisions: OpenAPI/REST, UI, workflow/security, graph, MCP/query
compatibility, and delta refresh remain deferred. No legacy catalog refresh,
graph build, production-data migration, or `main`-branch modification was run.

## Phase 4 — Minimal Entity Occurrences

Scope: immutable, source-backed `.ent` declarations only. The extractor reads
retained inventory paths and bytes from `SourceSnapshot.snapshot_root`, writes
canonical `entity_nodes`, repository/file-owned `entity_occurrences`, and
stable `entity_diagnostics` into the candidate, validates ownership and
provenance, and participates in the existing atomic V1 promotion. Entity
mappings, roots, companion mappings, OpenAPI/REST, workflow/service mappings,
UI, graph, MCP/query compatibility, delta refresh, migrations,
multi-repository support, JSONL intermediates, and legacy entity-builder
orchestration remain outside this slice.

Target `ia-main` commit used for acceptance: `e7fbab69da69cd605076eec74ee456066514adaf`.
The effective configured source root was verified as
`/Users/aritra.ghosh/projects/main`; the checkout was on `main` and clean.
The acceptance databases were fresh files under `/private/tmp`, so the
repository's existing `catalog/catalog.db` was not replaced.

Status: **accepted**

Promoted isolated-build evidence:

```json
{"active_db":"/private/tmp/repo-v1-phase4-review-a.db","build_token":"a9464e11c72f4316a104e32988b8e0a6","file_count":23877,"promoted":true,"target_commit_sha":"e7fbab69da69cd605076eec74ee456066514adaf","entity_node_count":1857,"entity_occurrence_count":1859,"entity_diagnostic_count":2139}
```

Canonical active-database evidence, independently verified read-only after
the Phase 5 promotion:

```json
{"active_db":"/Users/aritra.ghosh/projects/intacct-repo-intelligence/catalog/catalog.db","build_token":"2862530f960a422e8d74fb55958450d6","file_count":23877,"promoted":true,"target_commit_sha":"e7fbab69da69cd605076eec74ee456066514adaf","entity_node_count":1857,"entity_occurrence_count":1859,"entity_diagnostic_count":2139}
```

The canonical active database records the same target commit and entity
counts as the isolated acceptance builds. Read-only checks returned
`PRAGMA integrity_check = ok` and zero foreign-key violations.

Normalized entity facts from the two promoted isolated builds had the same
SHA-256 `5540f672fd6b73a01511721c84ff5f7b9306b2a2ccffa45978b97ad8d85ef124`.

| Acceptance requirement | Exact evidence | Observed result |
| --- | --- | --- |
| Focused entity behavior passes | `PYTHONPATH=. ./.venv/bin/pytest -q tests/test_repo_v1_entities.py` | Passed: 18 tests, 1 warning. |
| Existing V1 behavior remains green | `PYTHONPATH=. ./.venv/bin/pytest -q tests/test_repo_v1.py validation/test_source_snapshot.py tests/test_repo_v1_symbols.py tests/test_repo_v1_relationships.py tests/test_repo_v1_entities.py` | Passed: 78 tests, 1 warning. |
| Immutable target-commit build promotes with entity counts | `PYTHONPATH=. ./.venv/bin/python -m catalog.repo_v1 --target-sha e7fbab69da69cd605076eec74ee456066514adaf --active-db /private/tmp/repo-v1-phase4-review-a.db --no-progress` | Promoted; 23,877 files, 1,857 nodes, 1,859 occurrences, and 2,139 diagnostics. |
| Canonical active catalog retains the supplied Phase 5 evidence | Read-only SQLite verification of `catalog/catalog.db` | Passed; build token `2862530f960a422e8d74fb55958450d6`, target commit, promotion state, file count, and entity counts matched the evidence above. |
| Repeated full builds are equivalent | Same command with `/private/tmp/repo-v1-phase4-review-b.db`, followed by normalized SQLite comparison | Promoted second build matched the first fact-for-fact; normalized SHA-256s matched at `5540f672fd6b73a01511721c84ff5f7b9306b2a2ccffa45978b97ad8d85ef124`. |
| Candidate failure preserves active bytes | `PYTHONPATH=. ./.venv/bin/pytest -q tests/test_repo_v1_entities.py::test_entity_candidate_failure_preserves_active_database` | Passed; invalid entity ownership was rejected and active bytes/candidate cleanup were verified. |
| SQLite and foreign-key checks pass | Read-only Python query: `PRAGMA integrity_check`, `PRAGMA foreign_key_check` on both isolated promoted databases | Both returned `ok` and zero FK violations. |
| Formatting and static syntax checks pass | `git diff --check`; `./.venv/bin/python -m py_compile catalog/repo_v1_entities.py catalog/repo_v1.py` | Passed. |
| Dirty checkout cannot alter facts | `PYTHONPATH=. ./.venv/bin/pytest -q tests/test_repo_v1_entities.py::test_dirty_checkout_does_not_change_entity_facts` | Passed; committed snapshot facts matched after mutable checkout edits. |
| Legacy entity/mapping flows remain unused | `PYTHONPATH=. ./.venv/bin/pytest -q tests/test_repo_v1_entities.py::test_phase4_file_has_no_legacy_entity_or_mapping_or_jsonl_flow` | Passed; no legacy scanner, builder, JSONL, mapping, or root flow is referenced. |

Remaining gaps: none for the Phase 5 / Phase 4 Minimal Entity Occurrences
slice.

The isolated acceptance builds did not run a legacy refresh, build/promote the
Ladybug graph, migrate production data, modify `main`, or replace the existing
active catalog. Later mapping and compatibility work remains explicitly
deferred.

### Post-acceptance entity hardening note — 2026-08-07

The current repo-v1 entity extractor retains the same full-snapshot boundary
and adds two fail-closed protections validated by the focused entity tests:

- An unknown or dynamic `inheritEnts` overlay emits
  `entity_reference_dynamic` and does not merge metadata from a known base;
  known static overlays and safe empty/null/self/fallback overlays retain their
  prior behavior.
- Candidate `.ent` rows are inventoried before retained-path filtering. If a
  candidate `.ent` path is absent from `SourceSnapshot.entries`, extraction
  raises `SourceSnapshotError` instead of silently omitting entity facts.

The historical acceptance counts above are intentionally preserved. Current
verification should use the working-tree focused tests and the dedicated
`python -m catalog.repo_v1` entry point; the general `scripts/refresh.sh`
compatibility pipeline is not the repo-v1 entity builder.

The current focused entity test file has 23 tests, and the complete repo-v1
slice has 89 passing tests plus the existing Tree-sitter deprecation warning.

For first initialization, the repo-v1 active path may be absent. An existing
empty, malformed, or incompatible active file fails closed; verify
`catalog/catalog.db.previous` and restore it or deliberately remove the invalid
file before retrying. The historical canonical-database evidence below does not
override the current filesystem state.

## Repository-scan boundary

V1 uses the following path only:

```text
target Git commit
  -> catalog.source_snapshot GitTreeEntry/blob validation and V1 path filter
  -> V1 candidate repos/files inventory
  -> V1 snapshot-based symbols
  -> V1 snapshot-based relationships
  -> V1 snapshot-based entity nodes/occurrences/diagnostics
  -> candidate validation and atomic promotion
```

V1 owns a local language-classification helper in `catalog/repo_v1.py` and
does not import the legacy parser for inventory classification:

```text
catalog/repo_v1.py:_v1_detect_language
```

The command below produced no prohibited V1 call sites:

```bash
rg -n "\\b(scan|walk_repo|apply_changed_paths)\\s*\\(" \
  catalog/repo_v1.py scripts/refresh_repo_v1.py
```

V1 does not call `parser.scan_repo.scan()`, `walk_repo()`, or
`apply_changed_paths()`, and does not read mutable checkout bytes or
filesystem metadata for inventory facts. V1 language mappings are local to the
V1 implementation, including `.wfl`, `.map`, `.shortcuts`, `.xsd`, and `.wsdl`.
Symbols and relationships read only materialized Git blob bytes from the same
snapshot. Relationship extraction uses the V1 adapter and compatible leaf
logic only; no legacy scan/relationship orchestration, mutable-checkout read,
or delta behavior was added.

## Required acceptance commands

```text
./.venv/bin/python -m pytest -q tests/test_repo_v1.py validation/test_source_snapshot.py
42 passed, 1 warning in 11.01s

./.venv/bin/python -m pytest -q tests/test_repo_v1_symbols.py
8 passed, 1 warning in 2.91s

./.venv/bin/python -m pytest -q tests/test_repo_v1_relationships.py
10 passed, 1 warning in 3.17s

./.venv/bin/python -m pytest -q tests/test_archive_repository.py
8 passed in 1.25s

./.venv/bin/python -m pytest -q validation/test_multi_repo_migration.py
24 passed, 27 subtests passed in 0.77s

./.venv/bin/python -m pytest -q \
  tests/test_repo_v1.py::test_unpromoted_candidate_does_not_touch_active \
  tests/test_repo_v1.py::test_same_commit_uses_committed_blobs_and_is_deterministic \
  tests/test_repo_v1.py::test_failed_source_preparation_preserves_active_and_previous \
  tests/test_repo_v1.py::test_injected_backup_failure_preserves_active_and_previous \
  tests/test_repo_v1.py::test_injected_candidate_replace_failure_preserves_active_and_previous \
  tests/test_repo_v1.py::test_cas_detects_active_generation_change \
  tests/test_repo_v1.py::test_first_promotion_creates_active_catalog_without_previous \
  tests/test_repo_v1.py::test_replacement_promotion_retains_only_filesystem_previous_artifact \
  tests/test_repo_v1.py::test_v1_schema_has_only_minimal_build_lifecycle \
  tests/test_repo_v1.py::test_v1_cli_and_library_use_canonical_active_database_path
10 passed in 3.92s

git diff --check
passed

git status --short --branch
## repo-v1...origin/repo-v1 [ahead 2]
```

The worktree was clean at evidence time.

## Phase 6 — OpenAPI/REST

Phase 6 adds three sequential immutable slices: 6A indexes successfully
parsed `.yaml` documents below `app/source/openapispec` with exact exclusions
and deterministic kinds; 6B links only a direct top-level string
`x-mappedTo` to exactly one committed `.ent` filename stem represented by
`entity_occurrences`; and 6C extracts only direct HTTP operation facts from
indexed documents under `/paths/`, preserving exact path templates, lower-case
methods, direct scalar `operationId` values, and RFC 6901 source pointers.

The implementation reads committed `SourceSnapshot` bytes only. It adds no
mutable checkout read, parser scan call, mapping manifest, legacy OpenAPI
table, heuristic fallback, `$ref` traversal, graph projection, MCP/query
compatibility, delta refresh, or in-place migration. A valid pre-Phase-6
repo-v1 active catalog may be missing only the four additive Phase 6 tables;
the complete current-schema candidate replaces it atomically and preserves the
legacy file as `.previous`.

Target `ia-main` commit: `e7fbab69da69cd605076eec74ee456066514adaf`.
The configured source root was verified as `/Users/aritra.ghosh/projects/main`;
that checkout was on `main` at the target commit and clean. The two current
acceptance databases were isolated under `/private/tmp`; the canonical
`catalog/catalog.db` was not targeted and its observed digest was
`413037f172e3f7394abfb399b6dee7649634ff7a4d7ff66e81e56b861bdc8c97`.

Status: **accepted**

Final post-hardening promoted isolated-build evidence:

```json
{"active_db":"/private/tmp/repo-v1-phase6-opid-a.DrXed8/catalog.db","build_token":"141080f2954843f9a10d2c55162ac36c","file_count":23877,"promoted":true,"target_commit_sha":"e7fbab69da69cd605076eec74ee456066514adaf"}
{"active_db":"/private/tmp/repo-v1-phase6-opid-b.8McsrS/catalog.db","build_token":"9b7ba2cd2624476cba555358a5f74b17","file_count":23877,"promoted":true,"target_commit_sha":"e7fbab69da69cd605076eec74ee456066514adaf"}
```

Both isolated databases contained:

```text
files=23877
entity_occurrences=1859
entity_diagnostics=407
openapi_documents=3774
openapi_entity_links=713
rest_endpoints=2823
openapi_diagnostics=3061
```

OpenAPI diagnostic counts were `OPENAPI_X_MAPPEDTO_BLANK=2988`,
`OPENAPI_X_MAPPEDTO_CUSTOM=51`, and `OPENAPI_X_MAPPEDTO_ZERO_MATCHES=22`.
Normalized hashes matched between builds. Each hash is the SHA-256 of the
canonical JSON row arrays after excluding generated `id` values, ordering
columns by schema order, and sorting rows by all projected columns:

```text
openapi_documents=02ddde643d0a1176f489d8bf7f21b62e532a61afc7e2d2b328c286fbe90f4409
openapi_entity_links=bfcd757bf117f47d6e177e18a4eb612d2a6fea2b555acf3f82d8734d7f529b71
rest_endpoints=6ededfbcd3c7cd364e9bf92b70ed81239457ef878a3dc7c224c1bcc838afea97
openapi_diagnostics=2075a575d4fd77dcb1c54f9ddc03cb05b2e0772fe71022ef4cec11500984af5d
```

| Acceptance requirement | Evidence and result |
| --- | --- |
| Focused 6A/6B/6C behavior | `PYTHONPATH=. ./.venv/bin/pytest -q tests/test_repo_v1_openapi.py`: 12 passed, 1 warning. |
| Existing Phase 0–5 behavior | `PYTHONPATH=. ./.venv/bin/pytest -q tests/test_repo_v1.py validation/test_source_snapshot.py tests/test_repo_v1_symbols.py tests/test_repo_v1_relationships.py tests/test_repo_v1_entities.py tests/test_repo_v1_openapi.py`: 101 passed, 1 warning. |
| Exact scope, malformed YAML, links, and endpoints | Focused tests passed; `.yaml`/template exclusions, duplicate/non-mapping diagnostics, required mapping diagnostics, exact stems, literal lower-case methods, uppercase/unsupported method exclusion, pointers, and `$ref` non-traversal were verified. |
| Candidate failure and active preservation | `test_phase6_failure_preserves_active_database` passed; active bytes were unchanged and temporary candidates were removed. |
| Legacy active-schema accommodation | `test_phase6_additive_schema_upgrade_promotes_over_legacy_active` and the default smoke command | Passed; a valid active catalog missing only the four Phase 6 tables was upgraded atomically, with the old schema retained as `.previous`. |
| Stable keys, operation provenance, and ownership | Endpoint-key and `operation_id` tampering were rejected; both builds passed candidate validation with all Phase 6 facts owned by repo `1` and commit `e7fbab69...`. |
| SQLite/FK integrity | Both builds returned `PRAGMA integrity_check = ok` and zero `PRAGMA foreign_key_check` rows. |
| Dirty checkout and repeatability | `test_dirty_checkout_bytes_do_not_change_snapshot_facts` passed; the two current promoted builds matched all four normalized hashes. |
| Legacy-flow boundary | `rg` over repo-v1 OpenAPI/pipeline files found no parser scan, legacy OpenAPI scanner/linker/builder, legacy table, mapping manifest, or heuristic call. |
| Formatting and syntax | Ruff, `py_compile`, and `git diff --check` passed. |

This Phase 6 verification recorded the canonical database at target commit
`e7fbab69da69cd605076eec74ee456066514adaf` with build token
`6288fde678ea41099dec435354998939` and digest
`413037f172e3f7394abfb399b6dee7649634ff7a4d7ff66e81e56b861bdc8c97`; those
values are historical Phase 6 evidence. Counts, hashes,
operation-id provenance, target commit, integrity, FK, dirty-checkout,
candidate-failure, legacy active-schema accommodation, and active-preservation
evidence above are the current Phase 6 closure record.

## Phase 7A — Immutable ActionUI XML Facts

Phase 7A is accepted for immutable ActionUI XML facts only. The implementation
reads committed snapshot bytes for sorted paths ending exactly in
`_form.xml`, calls the shared ActionUI XML parser without changing it, and
materializes exactly six tables: `ui_surfaces`, `ui_artifacts`, `ui_fields`,
`ui_events`, `ui_includes`, and `ui_diagnostics`. It does not invoke
`catalog/ui_sync.py`, `parser.scan_repo`, legacy migrations, the legacy schema,
or any deferred Phase 7 behavior.

Target and source evidence:

- Branch: `repo-v1`.
- Resolved source checkout: `/Users/aritra.ghosh/projects/main`.
- Source branch/status: `main`, clean at the target commit.
- Target commit: `e7fbab69da69cd605076eec74ee456066514adaf`.
- Active database: `/Users/aritra.ghosh/projects/intacct-repo-intelligence/catalog/catalog.db`.
- Active build token: `3f05b871acc04e2bb368f46a74c3ffa2`.
- Operator command:
  `PYTHONPATH=. ./.venv/bin/python -m catalog.repo_v1 --manifest config/workspace_repos.yaml --active-db catalog/catalog.db`.
- Operator result: promoted atomically with `file_count=23877` and the target
  commit above.

The active build validation summary and read-only normalized projection were:

```text
ui_surfaces=581
ui_artifacts=581
ui_fields=18743
ui_events=4269
ui_includes=133
ui_diagnostics=49
```

Each normalized hash below is the SHA-256 of canonical JSON row arrays with
generated `id` values excluded, schema-order projected columns retained, and
rows sorted by all projected columns:

```text
ui_surfaces=eeaf48d426d0dc2a1a6b38ef43deb303108c31be4a8ef7a3c4a5793c57a97f2d
ui_artifacts=9ee6fc25ba099eee97ca857bbc4332c676bd2a8e6d19c6c4d1e294f0a1cff4f6
ui_fields=3255509632517e2d874e4d3bd5de67d51a682a47fde90ab3a4c91edd4312a840
ui_events=c43ebd9bb6ba43a7108623b224de9f14cb31989c8fc3462f85481851ad62ad9e
ui_includes=22ad59d7a1cab7a11f6047e1c3ce7055f11d3623b2c276b9efaa195a86d27aa1
ui_diagnostics=ff1f90bcd26f92719cfe31c9e4f32dcb2444c0cd9da5ae06996216c2ff7f984d
```

The isolated repeat build at `/private/tmp/repo-v1-phase7a-repeat/catalog.db`
used the same target commit, promoted with token
`36aeb573c25243ceb1257c2e042c9874`, and produced the same six counts and
normalized hashes.

| Acceptance requirement | Evidence and observed result |
| --- | --- |
| Focused Phase 7A behavior | `PYTHONPATH=. ./.venv/bin/pytest -q tests/test_repo_v1_ui.py` — 14 passed, 1 warning. |
| Full requested regression | `PYTHONPATH=. ./.venv/bin/pytest -q tests/test_repo_v1.py validation/test_source_snapshot.py tests/test_repo_v1_symbols.py tests/test_repo_v1_relationships.py tests/test_repo_v1_entities.py tests/test_repo_v1_openapi.py tests/test_repo_v1_ui.py` — 115 passed, 1 warning. |
| Exact six-table schema and indexes | Focused schema test and active SQLite inspection — passed; no additional UI tables were created. |
| Root-only, empty, whitespace, and malformed XML behavior | Focused tests — valid root produced one surface/artifact; empty and malformed files produced only file-attached parse errors with no UI facts. |
| Provenance, source hash, nullable `field_path`, diagnostic JSON nulls, and parser severity remapping | Focused provenance/diagnostic test — passed against target commit and SHA-256 raw snapshot bytes. |
| Include normalization and warning behavior | Focused tests — resolved, unresolved, invalid, relative, dot, dot-dot, backslash, absolute, drive-prefixed, and traversal cases passed; warnings did not reject candidates. |
| Stable collision ordinals and dirty-checkout immunity | Focused deterministic/dirty-checkout tests plus the two real-repository builds — passed; normalized hashes matched exactly. |
| Ownership, provenance, orphan, line-range, evidence, include-consistency, and diagnostic validation | Candidate validation tests and the promoted real build — passed; invalid candidates fail closed. |
| Phase 6 -> Phase 7A atomic upgrade | `test_phase6_upgrade_and_partial_schema_rejection` — passed; complete Phase 6 parent upgraded, partial UI schema rejected without changing active bytes. |
| Failed-candidate active preservation | `test_candidate_validation_failure_preserves_active_and_previous` — passed; active and `.previous` remained unchanged and the candidate was removed. |
| `.previous` preservation and atomic promotion | Real operator result was `promoted=true` with `.previous` present; replacement and injected-failure tests passed. |
| SQLite integrity and foreign keys | Read-only active check — `PRAGMA integrity_check = ok`; `PRAGMA foreign_key_check` returned no rows. |
| Formatting and syntax | Ruff check/format, `py_compile`, and `git diff --check` — passed. |

Phase 7A remaining gaps: none within the accepted six-table immutable XML
scope. Phase 7B below promotes the immutable NextGen UI fact extension.

## Phase 7B — Immutable NextGen UI Facts

Phase 7B is accepted for immutable NextGen family, artifact, and YAML/family
diagnostic facts. It reads only retained `SourceSnapshot` bytes and calls
`parser.ui.nextgen.extract_nextgen_families` without explicit entity mappings.
Entity references and entity-mapping diagnostics are discarded. No PHP,
JavaScript, event-call, UI/entity-link, legacy synchronization, graph, MCP,
delta, or in-place migration behavior is included.

Target and source evidence:

- Branch: `repo-v1`.
- Resolved source checkout: `/Users/aritra.ghosh/projects/main`.
- Target commit: `e7fbab69da69cd605076eec74ee456066514adaf`.
- Canonical active database: `catalog/catalog.db`.
- Promoted build token: `3c22c4cb00e0443fbe160c7ec01f8419`.
- Previous active build token preserved in `.previous`:
  `b2b059f0f3ff4a67872fc18eb57d1b10`.

The promoted active build records:

```text
nextgen_families=393
nextgen_artifacts=1002
nextgen_diagnostics=13
```

Normalized SHA-256 hashes of schema-order projected rows, excluding generated
IDs and sorting by all projected columns, are:

```text
nextgen_families=f3d55abe2014a3e679d2e21e20da7fec893ff353e07a347e9d31d915a863af99
nextgen_artifacts=f9dbe4980a4c0aa3aa40d40f1eccb8b41b222d5c274242cd833f6ef430d2d24f
nextgen_diagnostics=99f4f8368a1eba8f43f41acc95930095179336de248c7eaa1fa244433117c799
```

The two isolated repeat builds and the canonical active build produced the
same three hashes. Raw SHA-256 source hashes were independently verified for
all 1,015 retained NextGen-like files against the target Git commit.

| Acceptance requirement | Evidence and observed result |
| --- | --- |
| Focused Phase 7B behavior | `PYTHONPATH=. ./.venv/bin/pytest -q tests/test_repo_v1_nextgen.py` — 7 passed, 1 warning. |
| Full requested regression | 121 passed, 1 failed, 1 warning; the known Phase 6 upgrade fixture fails because it removes Phase 7A tables while retaining complete Phase 7B tables. This is tracked as deferred migration-scheduling work. |
| Exact three-table schema, indexes, and composite FKs | Focused schema test and active SQLite inspection — passed. |
| YAML/family diagnostics and parser severity | Focused tests — passed; only the four allowlisted codes persisted, with YAML errors and family warnings preserved. |
| Canonical evidence, JSON null, stable keys, and collision ordinals | Focused tests — passed. |
| Dirty-checkout immunity and raw source hashes | Focused tests and independent verification — passed for all 1,015 retained inputs. |
| Candidate ownership, provenance, evidence, hash, line-range, key, and FK validation | Focused candidate tests and promoted build — passed. |
| Atomic promotion and active/.previous preservation | Canonical result `promoted=true`; the prior active token remained available in `.previous`. |
| SQLite integrity and foreign keys | `PRAGMA integrity_check = ok`; `PRAGMA foreign_key_check` returned no rows. |
| Formatting and syntax | `py_compile` and `git diff --check` — passed. |

Phase 7B deferred work: schedule and repair the legacy Phase 6 upgrade fixture
for the ordered Phase 7A/7B parent boundary. No in-place migration is added;
the supported path remains a complete candidate rebuild and atomic promotion.
