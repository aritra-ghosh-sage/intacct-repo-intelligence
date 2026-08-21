# Greenfield PR Impact Step 2

Step 2 resolves cross-repository impact candidates from an immutable Step 1
report, repository-local contracts, and normalized CI evidence. It is additive
to the repo-v1 Step 2 availability audit; it does not read or modify repo-v1
SQLite, legacy catalog tables, graph data, or MCP state.

The Step 1 input is the greenfield `0.1` source-evidence artifact documented in
[Greenfield PR Impact Step 1](pr-impact-step-1-greenfield.md). Step 2 reads
`input.repository` (or `input.repo_key`), `input.target_revision` (or
`input.head_sha`), and the exact paths in `changed_files`. It does not require
repo-v1 direct traces, catalog revisions, or SQLite preflight fields.

## Resolution order

1. An active contract at the exact Step 1 source revision produces
   `confirmed` impact when an exact changed path is covered.
2. Normalized CI evidence for the exact source revision produces a candidate
   with executed-test evidence.
3. Read-only repository/workflow inventory produces a `candidate` only. It
   never proves that a test executed.
4. A revision-pinned `ia-main` semantic sidecar may support a `candidate` when
   an active contract supplies the cross-repository consumer. It never proves
   CI execution or upgrades a candidate to confirmed coverage.
5. Stale, unavailable, empty, mismatched, and missing evidence remains an
   explicit gap. It is never interpreted as no impact.

The report is deterministic: inputs are normalized, candidates are
deduplicated by stable identity, evidence is retained, and output is sorted.

## Source anchors and likely tests

When a revision-pinned semantic sidecar is supplied, Step 2 joins changed
source evidence to API objects through exact entity edges in that same
revision. The resulting candidate may include `source_anchors`, with the
changed source path, enclosing symbol, source lines, entity, API interface,
source revision, and evidence hashes. A semantic source anchor is candidate
evidence; it does not replace an active cross-repository contract or prove CI
execution.

Candidates may also include `likely_tests`. These are ranked, bounded test
paths from the inspected downstream inventory. Exact contract test
obligations rank highest. Source-backed interface/entity path signals may
rank inventory paths lower, but filename similarity alone never creates an
interface relationship. Every result includes a score rule version, confidence
band, reason codes, and evidence basis. `inventory_paths` remains the raw
repository inventory and is not an impacted-test assertion.

The Greenfield flow does not read `config/entity_definitions.jsonl`. That file
is legacy generated data outside this flow. Source mappings are built from
committed `.ent`, OpenAPI, PHP, XML, and related blobs at the exact Step 1
target revision.

## Contract input

Repository-local YAML uses schema `0.1`:

```yaml
schema_version: "0.1"
repository: ia-app
revision: <source revision>
relations:
  - interface_id: company.config.general-ledger-preference
    consumer_repository: ia-restapi-automation-tests
    relationship_type: api_contract
    source_paths:
      - app/source/company/CompanyConfig.cls
    status: active
```

Contracts use exact paths, not wildcards or inferred names. The repository
revision must equal the Step 1 target revision for a relation to be confirmed.

## CI input

Normalized JSON uses schema `0.1` and records the source revision, target
repository, interface ID, workflow evidence ID, and tests observed. Evidence
for another source revision is classified as stale.

## Repository inventory fallback

The two initial downstream repositories do not currently publish the expected
normalized Step 2 artifact. The fallback adapter reads their default-branch
tree, workflow files, workflow runs/checks at the downstream repository's own
inspected revision, and retained artifacts through read-only GitHub APIs. It
records workflow classification, inventory paths, artifact linkage, and the
inspected revision. A downstream run or artifact is cross-repository CI
evidence only when it explicitly binds both the source repository and source
revision; otherwise the report records `ci_linkage_unavailable` and keeps the
result at candidate/inventory strength.

Inventory evidence can produce reasons such as:

- `repository_inventory_only`
- `workflow_has_no_test_execution`
- `workflow_metadata_only`
- `ci_linkage_unavailable`
- `ci_artifact_unavailable`
- `ci_artifact_present_not_normalized`
- `repository_access_unavailable`

If GitHub returns a truncated repository tree, the inventory remains
available but records `response_truncated` as a provenance gap. The result is
therefore partial and must not be interpreted as a complete repository
inventory.

The local manifest's explicit `pr_impact_contracts` entries may select a
candidate repository. Generic `depends_on`, `enabled`, repository registration,
workflow names, and pass-only status checks do not establish impact or test
coverage. No changes are required in downstream repositories.

## Semantic sidecar input

Build a committed-revision sidecar for `ia-main` with:

```bash
PYTHONPATH=. ./.venv/bin/python scripts/build_greenfield_semantic_index.py \
  --repo-root "$HOME/projects/main" \
  --repository ia-main \
  --revision <40-character-commit-sha> \
  --output artifacts/greenfield/ia-main/<sha>/semantic-index.json
```

Pass it to Step 2 as optional static evidence:

```bash
PYTHONPATH=. ./.venv/bin/python scripts/trace_greenfield_step2.py \
  --step1-report step1.json \
  --semantic-index artifacts/greenfield/ia-main/<sha>/semantic-index.json \
  --output step2.json
```

The sidecar preserves typed entity, API object, workflow, ActionUI, NextGen,
import, and PHP-symbol edges. Unsupported XML and dynamic entity selection are
reported as diagnostics. A semantic edge is candidate evidence only; exact
active contracts and normalized CI evidence retain the stronger Step 2
classification boundary.

## Manual mapping boundary

The initial slice resolves impact at the declared interface/contract level and
does not require a manual symbol-to-entity mapping for every changed symbol.
Revision-pinned semantic source anchors may narrow the likely test surface;
explicit reviewed source-symbol-to-interface relationships remain available
for cases the semantic extractor cannot resolve. Names, basenames, filenames,
modules, and semantic similarity are never authoritative identity.

## CLI

```bash
PYTHONPATH=. ./.venv/bin/python scripts/trace_greenfield_step2.py \
  --step1-report step1.json \
  --contract contract.yaml \
  --ci-evidence ci-evidence.json \
  --repository intacct/ia-restapi-automation-tests \
  --repository intacct/ia-gwdata-gl \
  --output step2.json

PYTHONPATH=. ./.venv/bin/python scripts/validate_greenfield_step2.py \
  --report step2.json
```

Historical co-change, persistent storage, MCP, AI ranking, downstream workflow
changes, and automated downstream PR creation remain deferred. The semantic
sidecar is source evidence, not a replacement for repo-v1 or a general catalog
refresh.
