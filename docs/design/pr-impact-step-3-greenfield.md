# Greenfield PR Impact Step 3

Step 3 assembles a deterministic blast-radius outcome from a validated
greenfield Step 2 report. It is read-only and does not query GitHub, mutate
SQLite, refresh a catalog, run delta processing, or infer relationships from
names or paths.

## Inputs

The required input is a greenfield Step 2 JSON report. Optional inputs are:

- a revision-pinned semantic index for exact direct component evidence;
- a normalized `related_pull_requests` artifact tied to the source repository
  and source head SHA.

Without the optional artifacts, Step 3 still reports changed files as direct
components, while semantic components and related PRs remain explicit partial
or not-modelled surfaces.

## Outcome surfaces

The schema is `0.1`, with status-bearing surfaces so an empty `items` list is
not interpreted as proof that no impact exists:

- `direct_components`
- `potentially_affected_repositories`
- `interfaces`
- `owners`
- `test_suites`
- `related_pull_requests`
- `impact`

Every populated item retains exact source revision and evidence references.
Owners and tests are emitted only when declared or observed by Step 2 input
evidence. Missing owners and tests remain unavailable; they are not inferred.

Repository inventory is contextual evidence, not changed-file evidence. Step 3
does not copy inventory paths, workflow paths, or workflow definitions into the
impact surface. It retains bounded observation metadata instead: inspected and
source revisions, path/workflow counts, artifact and CI-linkage status, and the
inventory response hash. The full inventory remains recoverable from the Step 2
artifact referenced by `provenance.step2_report_sha256`.

An inventory-only repository candidate is represented in
`potentially_affected_repositories` and in a repository-scoped `impact` item.
It does not create a synthetic `repository:<name>` interface. The `interfaces`,
`owners`, and `test_suites` surfaces require declared interface evidence.

Semantic components are promoted only for `explicit_source` or
`resolved_exact` edges with exact changed-path evidence. Convention-based,
candidate-static, ambiguous, dynamic, unresolved, and unavailable edges remain
explicit gaps and are not reported as direct components.

## Blast-radius rules

- `local`: no external candidates and no unresolved external evidence;
- `boundary`: external impact is candidate-only;
- `multi_repo`: an exact active contract confirms a consumer repository;
- `systemic`: evidence explicitly identifies shared infrastructure, schema,
  build, or deployment impact;
- `unknown`: evidence is insufficient, contradictory, stale, or inaccessible.

Systemic impact cannot be derived from repository names, interface names,
directory names, or semantic similarity.

## Related PR evidence

Step 3 accepts only a normalized artifact with source repository, source head
SHA, source PR number, related repository/PR number, `open` or `merged` state,
head/base SHAs, relation type, and evidence identity. The artifact must match
the Step 2 source repository and target revision. Live GitHub discovery is
intentionally outside this slice.

## CLI

```bash
PYTHONPATH=. ./.venv/bin/python scripts/trace_greenfield_step3.py \
  --step2-report step2.json \
  --semantic-index artifacts/greenfield/ia-main/<sha>/semantic-index.json \
  --related-pr-evidence related-prs.json \
  --output step3.json

PYTHONPATH=. ./.venv/bin/python scripts/validate_greenfield_step3.py \
  --report step3.json
```

The report records the Step 2 hash, optional evidence hashes, rule-set
version, and explicit `catalog_mutation: none` and `github_writes: none`
provenance.
