# PR Impact Step 3

This is the current bounded traversal contract. The CLI prompt surface composes
the standalone Steps 1–3 reports with explicit symbol-to-entity ownership,
test evidence, and reviewed downstream-contract gaps where available; those
integrations are not silently implied by this standalone report.

Step 3 is a standalone, read-only repo-v1 analysis for `ia-main`. It validates
the fixture's exact committed Git diff, opens the active repo-v1 SQLite file
through one `mode=ro`, `query_only=ON` connection, and traces incoming callers
from all target-revision symbols in changed target files.

It does not run Step 1, consume a Step 1 report or hash, refresh a catalog,
process deltas, write SQLite, build a graph, call MCP, traverse another
repository, or infer entity ownership/business impact.

The manifest-resolved `ia-main` source root for this analysis is
`/Users/aritra.ghosh/projects/main`. Step 3 is bounded to persisted incoming
symbol relationships. When the repo-v1 candidate contains reviewed
`symbol_entity_links`, Step 3 joins them by persisted `symbol_id` only and
reports the linked entity occurrence plus existing entity metadata, database,
OpenAPI, and workflow facts. It never infers ownership from names or paths.

## Command

```bash
PYTHONPATH=. ./.venv/bin/python -m scripts.trace_pr_impact_step3 \
  --fixture <fixture.yaml> \
  --manifest config/workspace_repos.yaml \
  --active-db catalog/catalog.db \
  --repo-key ia-main \
  --max-hops 2 \
  --min-confidence 0.7
```

JSON is the only output. Exit status is `0` for `complete`, `partial`, or
`empty`, and `2` for `blocked`. Validate a materialized report with:

```bash
PYTHONPATH=. ./.venv/bin/python -m scripts.validate_pr_impact_step3 \
  --report report.json
```

The validator exits `0` only for a structurally valid report and `2` for an
invalid or unreadable report.

## Evidence contract

The seed basis is fixed to `target_file_all_symbols`: every symbol in each
available changed target file is a depth-zero seed. Deleted files, parser
failures, symbol-less files, and missing target files are represented in
`seed_files`; a non-delete file missing from the target catalog blocks because
it cannot be proven from target-revision evidence.

For each frontier symbol, Step 3 queries rows where
`relationships.target_symbol_id` is exactly that persisted ID. Only exact,
case-sensitive `CALLS` and `STATIC_CALLS` rows with
`resolution_class='project_resolved'` and `confidence > min_confidence` are
traversed. Every persisted matching relationship row is retained either in
`transitive_edges` or in `skipped_edges`; null-target rows are never
name-matched. Batches stay below SQLite's variable limit and fan-out is never
truncated. The default threshold is `0.7`; equality is skipped with reason
`below_confidence`.

Seeds and reached symbols retain catalog IDs, repository/file/blob identity,
declaration ranges, symbol identity, and source revisions. Edges retain both
symbol IDs and names/kinds, relationship evidence, confidence, resolution,
extractor, file/blob identity, hop, and both catalog and fixture revisions.
Relationship evidence is not presented as a call-site line range.

Without reviewed links, `entity_context` remains unavailable with reason
`repo_v1_symbol_entity_mapping_not_modelled`. With links, it reports resolved,
unresolved, ambiguous, stale, or missing mapping states and their contract
provenance. `business_impact` is always
deferred because verified caller evidence is not a business-impact mapping.
Reports without reviewed links include the deterministic gap
`entity_context:repo_v1_symbol_entity_mapping_not_modelled`; consumers must not
interpret unavailable entity mappings or zero callers as no business impact.
Entity occurrences in the same file, matching entity names, matching
filenames, modules, basenames, and legacy mapping tables do not create a
symbol-to-entity mapping.

The report preserves relationship IDs, repository/file/blob identity, target
and source symbol identity, target-revision provenance, evidence, confidence,
resolution class and reason, extractor, and hop. The validator rejects empty
or invalid evidence/provenance fields and identity mismatches. Non-call rows
remain explicit in `skipped_edges`; unresolved or missing-source rows remain
explicit and can make the report `partial`.

## Statuses and failures

`empty` means every changed target file is `symbol_less` and there are no
seeds. `partial` means traversal ran but a deleted/parser-failed/symbol-less
seed file or attributable unusable edge remains. Intentional non-call filtering
does not lower status. `complete` permits zero callers: no callers is valid
evidence, not proof of no business impact. Fixture, Git, schema, SQLite,
ownership, target-revision, and provenance failures are `blocked`.

Run the focused and regression checks from the repository root after confirming
the source root above:

```bash
PYTHONPATH=. ./.venv/bin/pytest -q tests/test_pr_impact_step3.py
PYTHONPATH=. ./.venv/bin/pytest -q \
  tests/test_pr_impact_step1.py \
  tests/test_pr_impact_step2.py \
  tests/test_pr_impact_step3.py
```
