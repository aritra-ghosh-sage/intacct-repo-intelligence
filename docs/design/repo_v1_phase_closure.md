# Repo Intelligence V1 Phase Closure

Reviewer: Codex (automated acceptance), 2026-08-06

Implementation commits:

- `7fafcd0d777af333252396714a8ff9416d248c6a` — V1 implementation and oracle
  acceptance work;
- `ee333a93fdf64369d6b3674f429ab277cdf7d73b` — canonical active database path
  normalization.

All acceptance evidence below was rerun against the canonical-path follow-up
commit.

Target `ia-main` commit: `173a9b1fccd0fc046cedee6756dd7ef8f922627d`

Canonical V1 active database path: `catalog/catalog.db`. The V1 library
default and `--active-db` CLI default use this same path, matching the
promoted database recorded below and the repository-wide `config.CATALOG_DB`
default.

The target checkout was verified at `/Users/aritra.ghosh/projects/main`, on
branch `main`, with a clean status before acceptance. No production refresh,
legacy catalog refresh, graph build, or main-branch modification was run.

The promoted V1 database evidence was independently verified read-only:

```json
{"active_db":"/Users/aritra.ghosh/projects/intacct-repo-intelligence/catalog/catalog.db","build_token":"e0c64df8dfeb4e31af5e1254dac21576","file_count":23879,"promoted":true,"target_commit_sha":"173a9b1fccd0fc046cedee6756dd7ef8f922627d"}
```

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

Deferred decisions: symbols, relationships, entity occurrences, OpenAPI/REST,
UI, workflow/security, graph, MCP, and delta refresh remain outside this
closure.

## Repository-scan boundary

V1 uses the following path only:

```text
target Git commit
  -> catalog.source_snapshot GitTreeEntry/blob validation and V1 path filter
  -> V1 candidate repos/files inventory
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
No extraction or delta behavior was added.

## Required acceptance commands

```text
./.venv/bin/python -m pytest -q tests/test_repo_v1.py validation/test_source_snapshot.py
42 passed in 10.26s

./.venv/bin/python -m pytest -q tests/test_archive_repository.py
8 passed in 1.60s

./.venv/bin/python -m pytest -q validation/test_multi_repo_migration.py
24 passed, 27 subtests passed in 0.80s

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
10 passed in 4.10s

git diff --check
passed

git status --short --branch
## repo-v1...origin/repo-v1 [ahead 1]
```

The worktree was clean at evidence time.
