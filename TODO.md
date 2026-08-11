# Pending Work

This is the active backlog for the repo-v1 implementation. Phase 0 through
Phase 8 acceptance work is complete, and the Phase 9 database-facts extension
is implemented with current-target repeat-build evidence; formal Phase 9
closure remains explicitly tracked below.

## Repository Selection

- [ ] Make repository selection manifest-driven when V1 expands beyond
      `ia-main`. Remove the hardcoded `REPO_KEY`, select the requested
      manifest entry, and preserve that entry's target-commit provenance.
      Add focused coverage for an alternate repository key.

## Initial V1 Operation

- [x] Run the first approved V1 build to create and promote the V1 schema in
      `catalog/catalog.db` from the committed `config/workspace_repos.yaml`
      manifest. Do not initialize it through the legacy schema or a migration.

## V1 Inventory Scope

- [x] Add committed-Git-tree filtering before V1 blob materialization: skip any
      dot-directory component (`.github`, `.idea`, `.vscode`, etc.),
      `.gitignore`, and the case-insensitive suffixes `.jar`, `.po`, `.png`,
      `.svg`, `.gif`, `.exe`, `.dll`, `.deploy`, `.pdf`, `.eot`, `.ttf`,
      `.woff2`, and `.woff`. Add an optional per-repository manifest list of
      ignored relative directory prefixes (for example,
      `ia-main: app/resources/thirdparty`), with normalized-path validation and
      focused Git-tree/oracle tests. Keep the policy V1-local and preserve
      provenance for retained files.

## Phase 2 Symbols

- [x] Extract deterministic symbols and parser diagnostics from immutable
      target-commit snapshot bytes into the V1 candidate, validate ownership
      and integrity, and expose the accepted facts through atomic promotion.

## Phase 3 Relationships

- [x] Extract deterministic relationships from immutable target-commit
      snapshot bytes against candidate symbols, preserve resolved and explicit
      unresolved targets with evidence and extractor provenance, validate
      ownership/provenance/integrity, and include the pass before atomic
      promotion.

## Later V1 Components

- [x] Phase 4 Entity Occurrences implemented and accepted; the slice remains
      limited to immutable `.ent` declarations, candidate validation, and
      atomic promotion.

- [x] Phase 6 OpenAPI/REST implemented and accepted as sequential immutable
      document-index, exact entity-link, and REST-endpoint slices. Facts read
      committed `SourceSnapshot` bytes only; legacy OpenAPI flows, mappings,
      `$ref` traversal, graph/MCP/query compatibility, delta refresh,
      migrations, and production replacement remain deferred.

- [x] Phase 7A immutable ActionUI XML facts and diagnostics implemented and
      accepted.

- [x] Phase 7B immutable NextGen UI families, artifacts, and YAML/family
      diagnostics implemented and accepted. Entity references, PHP,
      JavaScript, event-call resolution, and UI/entity links remain deferred.

- [x] Reconcile the legacy Phase 6 upgrade fixture with the ordered Phase 6-8
      parent boundary; later families without their complete predecessors are
      rejected before candidate construction. No in-place migration is part of
      Phase 7B/8.

- [x] Phase 8A/8B immutable OpenAPI workflow endpoint and source-backed security
      facts implemented and accepted in the repo-v1 candidate path; provenance
      closure, nested menu traversal, focused tests, two isolated repeat builds,
      normalized Phase 8 parity, SQLite integrity, and foreign-key checks passed.

- [x] Phase 9 database-facts extraction implemented in the repo-v1 candidate
      path, including committed dbschema facts, entity metadata/schema mappings,
      database links, diagnostics, candidate validation, and ordered parent
      boundaries.

- [x] Record dedicated Phase 9 closure acceptance evidence, including the
      database-specific candidate-validation and snapshot-failure scenarios,
      active/previous preservation, candidate cleanup, and full regression.
