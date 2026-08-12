# PR Impact Step 3

Step 3 is a standalone, read-only repo-v1 analysis for `ia-main`. It validates
the fixture's exact committed Git diff, opens the active repo-v1 SQLite file
through one `mode=ro`, `query_only=ON` connection, and traces incoming callers
from all target-revision symbols in changed target files.

It does not run Step 1, consume a Step 1 report or hash, refresh a catalog,
process deltas, write SQLite, build a graph, call MCP, traverse another
repository, or infer entity ownership/business impact.

## Command

```bash
PYTHONPATH=. ./.venv/bin/python -m scripts.trace_pr_impact_step3 \
  --fixture <fixture.yaml> \
  --manifest config/workspace_repos.yaml \
  --active-db catalog/catalog.db \
  --repo-key ia-main \
  --max-hops 2
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
`resolution_class='project_resolved'` are traversed. Every persisted matching
relationship row is retained either in `transitive_edges` or in
`skipped_edges`; null-target rows are never name-matched. Batches stay below
SQLite's variable limit and fan-out is never truncated.

Seeds and reached symbols retain catalog IDs, repository/file/blob identity,
declaration ranges, symbol identity, and source revisions. Edges retain both
symbol IDs and names/kinds, relationship evidence, confidence, resolution,
extractor, file/blob identity, hop, and both catalog and fixture revisions.
Relationship evidence is not presented as a call-site line range.

`entity_context` is always unavailable with reason
`repo_v1_symbol_entity_mapping_not_modelled`. `business_impact` is always
deferred because verified caller evidence is not a business-impact mapping.

## Statuses and failures

`empty` means every changed target file is `symbol_less` and there are no
seeds. `partial` means traversal ran but a deleted/parser-failed/symbol-less
seed file or attributable unusable edge remains. Intentional non-call filtering
does not lower status. `complete` permits zero callers: no callers is valid
evidence, not proof of no business impact. Fixture, Git, schema, SQLite,
ownership, target-revision, and provenance failures are `blocked`.

