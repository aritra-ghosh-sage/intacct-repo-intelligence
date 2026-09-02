# Greenfield Behavior Handbook

## Run Summary

- Repository: `intacct/ia-app` (`ia-main`)
- Revision: `071f786e237c0c92b385a19314f9a804c90ffedc`
- Status: **partial**
- Behaviors: 4
- Unassigned evidence rows: 16

## Behavior Register

| Behavior | Status | Sources | Impact | Tests | Actions |
|---|---:|---:|---:|---:|---:|
| behavior:05f71af8be6d4647568d98b3 | partial | 1 | 4 | 8 | 4 |
| behavior:202dbdf9ff662ed9b8b55f34 | partial | 1 | 4 | 8 | 4 |
| behavior:2729cb7133b4dc75c18fed6d | partial | 1 | 4 | 8 | 4 |
| behavior:2ffa741f79174aa89766f6e8 | partial | 1 | 4 | 8 | 4 |

## Global Gaps

- `ci_artifact_unavailable:intacct/ia-gwdata-ap`
- `ci_artifact_unavailable:intacct/ia-gwdata-contract`
- `ci_artifact_unavailable:intacct/ia-gwdata-gl`
- `ci_artifact_unavailable:intacct/ia-gwdata-project`
- `ci_artifact_unavailable:intacct/ia-restapi-automation-tests`
- `ci_evidence_not_provided`
- `ci_linkage_unavailable:intacct/ia-gwdata-ap`
- `ci_linkage_unavailable:intacct/ia-gwdata-contract`
- `ci_linkage_unavailable:intacct/ia-gwdata-gl`
- `ci_linkage_unavailable:intacct/ia-gwdata-project`
- `ci_linkage_unavailable:intacct/ia-restapi-automation-tests`
- `intacct/ia-gwdata-ap:ci_linkage_unavailable:target_repository_has_no_source_revision`
- `intacct/ia-gwdata-ap:repos/intacct/ia-gwdata-ap/git/trees/3a412ac4d3792b49d9b13acfd27163e55baeae35?recursive=1: response_truncated`
- `intacct/ia-gwdata-contract:ci_linkage_unavailable:target_repository_has_no_source_revision`
- `intacct/ia-gwdata-contract:repos/intacct/ia-gwdata-contract/git/trees/9e23a432633c6b2a7d84390561a17a364662a823?recursive=1: response_truncated`
- `intacct/ia-gwdata-gl:ci_linkage_unavailable:target_repository_has_no_source_revision`
- `intacct/ia-gwdata-project:ci_linkage_unavailable:target_repository_has_no_source_revision`
- `intacct/ia-restapi-automation-tests:ci_linkage_unavailable:target_repository_has_no_source_revision`
- `related_pull_requests_not_modelled:revision_pinned_artifact_not_provided`
- `repository_inventory_not_provided`
- `semantic_index_not_provided:direct_semantic_components`
- `test_suites_unavailable:no_normalized_test_evidence`
- `workflow_has_no_test_execution:intacct/ia-gwdata-ap`
- `workflow_has_no_test_execution:intacct/ia-gwdata-contract`
- `workflow_has_no_test_execution:intacct/ia-gwdata-gl`
- `workflow_has_no_test_execution:intacct/ia-gwdata-project`
- `workflow_has_no_test_execution:intacct/ia-restapi-automation-tests`
- `workflow_metadata_only:intacct/ia-gwdata-ap`
- `workflow_metadata_only:intacct/ia-gwdata-contract`
- `workflow_metadata_only:intacct/ia-gwdata-gl`
- `workflow_metadata_only:intacct/ia-gwdata-project`

## behavior:05f71af8be6d4647568d98b3

Returns false unconditionally, indicating the GL ledger report does not support a matching-letter column. No calls made.

Status: **partial**

### Implementation

- `app/source/gl/GLLedgerReporter.cls` (path-only, revision `071f786e237c0c92b385a19314f9a804c90ffedc`)

### Impact

- ia-app / behavior:05f71af8be6d4647568d98b3 / confirmed
- ia-app / behavior:202dbdf9ff662ed9b8b55f34 / confirmed
- ia-app / behavior:2729cb7133b4dc75c18fed6d / confirmed
- ia-app / behavior:2ffa741f79174aa89766f6e8 / confirmed

### Coverage

- ia-app / behavior:05f71af8be6d4647568d98b3 / unavailable
- ia-app / behavior:202dbdf9ff662ed9b8b55f34 / unavailable
- ia-app / behavior:2729cb7133b4dc75c18fed6d / unavailable
- ia-app / behavior:2ffa741f79174aa89766f6e8 / unavailable
- ia-app / behavior:05f71af8be6d4647568d98b3 / candidate
- ia-app / behavior:202dbdf9ff662ed9b8b55f34 / candidate
- ia-app / behavior:2729cb7133b4dc75c18fed6d / candidate
- ia-app / behavior:2ffa741f79174aa89766f6e8 / candidate

### Actions

- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)

### Behavior Gaps

- `step3_test_suites:behavior:05f71af8be6d4647568d98b3:unavailable`
- `step3_test_suites:behavior:202dbdf9ff662ed9b8b55f34:unavailable`
- `step3_test_suites:behavior:2729cb7133b4dc75c18fed6d:unavailable`
- `step3_test_suites:behavior:2ffa741f79174aa89766f6e8:unavailable`

## behavior:202dbdf9ff662ed9b8b55f34

Builds select/from/where clause fragments for dimension display, including special-case handling for the employee dimension (joining contact/employeemst tables) and generic name/id column selection for other dimensions. Body is only partially shown (excerpt is cut off before completion at line 1236). Calls to the global helper isl_strpos() appear but its definition/target path is not present in the provided evidence, so no edge is emitted for it.

Status: **partial**

### Implementation

- `app/source/gl/GLLedgerReporter.cls` (path-only, revision `071f786e237c0c92b385a19314f9a804c90ffedc`)

### Impact

- ia-app / behavior:05f71af8be6d4647568d98b3 / confirmed
- ia-app / behavior:202dbdf9ff662ed9b8b55f34 / confirmed
- ia-app / behavior:2729cb7133b4dc75c18fed6d / confirmed
- ia-app / behavior:2ffa741f79174aa89766f6e8 / confirmed

### Coverage

- ia-app / behavior:05f71af8be6d4647568d98b3 / unavailable
- ia-app / behavior:202dbdf9ff662ed9b8b55f34 / unavailable
- ia-app / behavior:2729cb7133b4dc75c18fed6d / unavailable
- ia-app / behavior:2ffa741f79174aa89766f6e8 / unavailable
- ia-app / behavior:05f71af8be6d4647568d98b3 / candidate
- ia-app / behavior:202dbdf9ff662ed9b8b55f34 / candidate
- ia-app / behavior:2729cb7133b4dc75c18fed6d / candidate
- ia-app / behavior:2ffa741f79174aa89766f6e8 / candidate

### Actions

- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)

### Behavior Gaps

- `step3_test_suites:behavior:05f71af8be6d4647568d98b3:unavailable`
- `step3_test_suites:behavior:202dbdf9ff662ed9b8b55f34:unavailable`
- `step3_test_suites:behavior:2729cb7133b4dc75c18fed6d:unavailable`
- `step3_test_suites:behavior:2ffa741f79174aa89766f6e8:unavailable`

## behavior:2729cb7133b4dc75c18fed6d

Empty override hook for query-build customization prior to executing the report query; body contains only a comment and performs no calls.

Status: **partial**

### Implementation

- `app/source/gl/GLLedgerReporter.cls` (path-only, revision `071f786e237c0c92b385a19314f9a804c90ffedc`)

### Impact

- ia-app / behavior:05f71af8be6d4647568d98b3 / confirmed
- ia-app / behavior:202dbdf9ff662ed9b8b55f34 / confirmed
- ia-app / behavior:2729cb7133b4dc75c18fed6d / confirmed
- ia-app / behavior:2ffa741f79174aa89766f6e8 / confirmed

### Coverage

- ia-app / behavior:05f71af8be6d4647568d98b3 / unavailable
- ia-app / behavior:202dbdf9ff662ed9b8b55f34 / unavailable
- ia-app / behavior:2729cb7133b4dc75c18fed6d / unavailable
- ia-app / behavior:2ffa741f79174aa89766f6e8 / unavailable
- ia-app / behavior:05f71af8be6d4647568d98b3 / candidate
- ia-app / behavior:202dbdf9ff662ed9b8b55f34 / candidate
- ia-app / behavior:2729cb7133b4dc75c18fed6d / candidate
- ia-app / behavior:2ffa741f79174aa89766f6e8 / candidate

### Actions

- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)

### Behavior Gaps

- `step3_test_suites:behavior:05f71af8be6d4647568d98b3:unavailable`
- `step3_test_suites:behavior:202dbdf9ff662ed9b8b55f34:unavailable`
- `step3_test_suites:behavior:2729cb7133b4dc75c18fed6d:unavailable`
- `step3_test_suites:behavior:2ffa741f79174aa89766f6e8:unavailable`

## behavior:2ffa741f79174aa89766f6e8

Builds the SELECT clause fragment for user-chosen dimension columns based on the SHOWDIMENSIONVALUES parameter. Iterates configured dimensions and appends gl_info column references (optionally aliased) to the select string; no calls to other resolvable internal or external symbols are made within the shown hunk.

Status: **partial**

### Implementation

- `app/source/gl/GLLedgerReporter.cls` (path-only, revision `071f786e237c0c92b385a19314f9a804c90ffedc`)

### Impact

- ia-app / behavior:05f71af8be6d4647568d98b3 / confirmed
- ia-app / behavior:202dbdf9ff662ed9b8b55f34 / confirmed
- ia-app / behavior:2729cb7133b4dc75c18fed6d / confirmed
- ia-app / behavior:2ffa741f79174aa89766f6e8 / confirmed

### Coverage

- ia-app / behavior:05f71af8be6d4647568d98b3 / unavailable
- ia-app / behavior:202dbdf9ff662ed9b8b55f34 / unavailable
- ia-app / behavior:2729cb7133b4dc75c18fed6d / unavailable
- ia-app / behavior:2ffa741f79174aa89766f6e8 / unavailable
- ia-app / behavior:05f71af8be6d4647568d98b3 / candidate
- ia-app / behavior:202dbdf9ff662ed9b8b55f34 / candidate
- ia-app / behavior:2729cb7133b4dc75c18fed6d / candidate
- ia-app / behavior:2ffa741f79174aa89766f6e8 / candidate

### Actions

- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)

### Behavior Gaps

- `step3_test_suites:behavior:05f71af8be6d4647568d98b3:unavailable`
- `step3_test_suites:behavior:202dbdf9ff662ed9b8b55f34:unavailable`
- `step3_test_suites:behavior:2729cb7133b4dc75c18fed6d:unavailable`
- `step3_test_suites:behavior:2ffa741f79174aa89766f6e8:unavailable`

## Unassigned Evidence

- `step2_candidates`: 5 row(s)
- `step3_impact`: 5 row(s)
- `step3_repositories`: 6 row(s)
- `step3_interfaces`: 0 row(s)
- `step3_owners`: 0 row(s)
- `step3_test_suites`: 0 row(s)
- `step3_related_pull_requests`: 0 row(s)
- `step4_coverage`: 0 row(s)
- `step4_obligations`: 0 row(s)
- `step5_actions`: 0 row(s)

This handbook is a derived location index. The retained Greenfield artifacts and revision-bound source remain authoritative.
