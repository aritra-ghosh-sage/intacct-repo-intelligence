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

Phase 4 Entity Occurrences implementation is present in the current
`repo-v1` working tree; no commit was created by this acceptance run.

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

Normalized entity facts from the two promoted isolated builds had the same
SHA-256 `5540f672fd6b73a01511721c84ff5f7b9306b2a2ccffa45978b97ad8d85ef124`.

| Acceptance requirement | Exact evidence | Observed result |
| --- | --- | --- |
| Focused entity behavior passes | `PYTHONPATH=. ./.venv/bin/pytest -q tests/test_repo_v1_entities.py` | Passed: 18 tests, 1 warning. |
| Existing V1 behavior remains green | `PYTHONPATH=. ./.venv/bin/pytest -q tests/test_repo_v1.py validation/test_source_snapshot.py tests/test_repo_v1_symbols.py tests/test_repo_v1_relationships.py tests/test_repo_v1_entities.py` | Passed: 78 tests, 1 warning. |
| Immutable target-commit build promotes with entity counts | `PYTHONPATH=. ./.venv/bin/python -m catalog.repo_v1 --target-sha e7fbab69da69cd605076eec74ee456066514adaf --active-db /private/tmp/repo-v1-phase4-review-a.db --no-progress` | Promoted; 23,877 files, 1,857 nodes, 1,859 occurrences, and 2,139 diagnostics. |
| Repeated full builds are equivalent | Same command with `/private/tmp/repo-v1-phase4-review-b.db`, followed by normalized SQLite comparison | Promoted second build matched the first fact-for-fact; normalized SHA-256s matched at `5540f672fd6b73a01511721c84ff5f7b9306b2a2ccffa45978b97ad8d85ef124`. |
| Candidate failure preserves active bytes | `PYTHONPATH=. ./.venv/bin/pytest -q tests/test_repo_v1_entities.py::test_entity_candidate_failure_preserves_active_database` | Passed; invalid entity ownership was rejected and active bytes/candidate cleanup were verified. |
| SQLite and foreign-key checks pass | Read-only Python query: `PRAGMA integrity_check`, `PRAGMA foreign_key_check` on both isolated promoted databases | Both returned `ok` and zero FK violations. |
| Formatting and static syntax checks pass | `git diff --check`; `./.venv/bin/python -m py_compile catalog/repo_v1_entities.py catalog/repo_v1.py` | Passed. |
| Dirty checkout cannot alter facts | `PYTHONPATH=. ./.venv/bin/pytest -q tests/test_repo_v1_entities.py::test_dirty_checkout_does_not_change_entity_facts` | Passed; committed snapshot facts matched after mutable checkout edits. |
| Legacy entity/mapping flows remain unused | `PYTHONPATH=. ./.venv/bin/pytest -q tests/test_repo_v1_entities.py::test_phase4_file_has_no_legacy_entity_or_mapping_or_jsonl_flow` | Passed; no legacy scanner, builder, JSONL, mapping, or root flow is referenced. |

Remaining gaps: none for the Phase 4 Minimal Entity Occurrences slice.

The isolated acceptance builds did not run a legacy refresh, build/promote the
Ladybug graph, migrate production data, modify `main`, or replace the existing
active catalog. Later mapping and compatibility work remains explicitly
deferred.

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
