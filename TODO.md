# Pending Work

This is the repository's active lightweight backlog. Items remain here until
implemented, explicitly deferred, or accepted by the owner. Runtime states in
the catalog and historical validation issue reports are not backlog items.

## Parser Correctness

- [ ] Add an explicit per-repository parser/language allowlist. `ia-main`
      should not run the Java parser unless that language is enabled; the
      current `language: php` manifest value is metadata, not an allowlist.
      See `config/workspace_repos.yaml`, `parser/extract_symbols.py`, and
      `scripts/refresh_workspace.py`.
- [ ] Validate `tree.root_node.has_error` (and error descendants) in the PHP,
      Java, and security parsers. Record a diagnostic or fail closed instead
      of treating partial trees as successful evidence.
- [ ] Correct the security parser documentation or implement the claimed
      regex fallback. `parse_all_assigned_arrays()` currently returns an empty
      result after a parser exception.
- [ ] Add parser regression tests for named PHP arguments, partial trees,
      `.cls`, `.ent`, `.cqry`, `.menu`, `.pol`, and `.rpt` routing.
- [ ] Add `.yml` consistently to scan scope and language detection. The
      `ia-main` checkout currently contains two `.yml` files that are skipped.
- [ ] Audit non-tree-sitter extractor coverage. In particular, XSLT extraction
      currently misses single-quoted template attributes, and YAML extraction
      has real syntax-failure cases that need an explicit disposition.
- [ ] Document or implement relationship extraction coverage for SQL, XSLT,
      and nonstandard PHP-family files such as `.menu`, `.pol`, `.ent`, and
      `.rpt`.
- [ ] Refactor relationship extraction to reduce refresh time. This is
      currently the slowest extraction stage and needs profiling plus a
      performance-focused redesign without weakening provenance or validation.

## Catalog Semantics

- [x] Add an explicit archived-repository handling policy. Reading an archived
      repository, extracting its code structure, or querying its SQLite or
      graph representation can create accuracy and correctness risks; detect
      and surface that state, and fail closed or clearly mark the affected
      evidence until it is revalidated.
- [ ] Add a `tqdm`/progress indicator for the repository archival workflow so
      long-running ownership checks and evidence removal expose operator
      progress.
- [ ] Complete OpenAPI, configuration, and override extraction for entity
      semantics. See `scripts/build_entity_semantics.py`.
- [ ] Decide whether to implement the deferred partial-tree snapshot
      optimization described in the refresh contract.
- [ ] Create the follow-up migration for complete legacy parent/child family
      rebuilding, if still required. See `catalog/migrations.py`.

## UI Catalog

- [x] Implement the source-provenanced actionUI/NextGen UI catalog, including
      XML extraction, bounded PHP loader resolution, JavaScript handler
      resolution, SQLite synchronization, validation, query tooling, MCP
      surfaces, and focused tests. See
      `docs/design/ui-catalog-implementation-plan.md`.
- [ ] Consider explicitly approved follow-up scope: Ladybug projection,
      per-file UI delta mutation, compatibility mappings, runtime event
      execution, broader JavaScript call-graph analysis, and semantic
      UI-field-to-entity-field mapping.

## Review And Approval

- [ ] Obtain stakeholder approval for the flat workflow model decision. See
      `docs/design/workflows.md`.
