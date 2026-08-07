# Pending Work

This is the active backlog for the repo-v1 implementation. Phase 0 through
Phase 6 and Phase 7A acceptance work is complete; the remaining Phase 7
components and later components remain outside this backlog.

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
      accepted. PHP, JavaScript, NextGen UI, event-call resolution, UI/entity
      links, and other Phase 7 slices remain open.

- [ ] Phase 8 workflow and security facts.
