# Greenfield PR Impact Step 2

Step 2 resolves cross-repository impact candidates from an immutable Step 1
report, repository-local contracts, and normalized CI evidence. It is additive
to the repo-v1 Step 2 availability audit; it does not read or modify repo-v1
SQLite, legacy catalog tables, graph data, or MCP state.

## Resolution order

1. An active contract at the exact Step 1 source revision produces
   `confirmed` impact when an exact changed path is covered.
2. Normalized CI evidence for the exact source revision produces a candidate
   with executed-test evidence.
3. Read-only repository/workflow inventory produces a `candidate` only. It
   never proves that a test executed.
4. Stale, unavailable, empty, mismatched, and missing evidence remains an
   explicit gap. It is never interpreted as no impact.

The report is deterministic: inputs are normalized, candidates are
deduplicated by stable identity, evidence is retained, and output is sorted.

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
tree, workflow files, exact-source-revision workflow runs/checks, and retained
artifacts through read-only GitHub APIs. It records workflow classification,
inventory paths, artifact linkage, and the inspected revision.

Inventory evidence can produce reasons such as:

- `repository_inventory_only`
- `workflow_has_no_test_execution`
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

## Manual mapping boundary

The initial slice resolves impact at the declared interface/contract level and
does not require a manual symbol-to-entity mapping for every changed symbol.
Future symbol-level relationships may be supplied by generated, revision-pinned
indexes and then reviewed. Names, basenames, filenames, modules, and semantic
similarity are never authoritative identity.

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

Static-code relationships, historical co-change, persistent storage, MCP,
AI ranking, downstream workflow changes, and automated downstream PR creation
remain deferred.
