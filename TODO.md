# Pending Work

This is the active backlog for the repo-v1 implementation. Phase 0, Phase 1,
Phase 2 Symbols, and Phase 3 Relationships acceptance work is complete; later
components remain outside this backlog.

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
