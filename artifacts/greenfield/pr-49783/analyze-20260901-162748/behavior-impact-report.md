# Greenfield Behavior Handbook

## Run Summary

- Repository: `intacct/ia-app` (`ia-main`)
- Revision: `071f786e237c0c92b385a19314f9a804c90ffedc`
- Status: **partial**
- Behaviors: 7
- Unassigned evidence rows: 16

## Behavior Register

| Behavior | Status | Sources | Impact | Tests | Actions |
|---|---:|---:|---:|---:|---:|
| behavior:0fb1098b6305204245d430f8 | partial | 1 | 7 | 14 | 7 |
| behavior:1e7ff88ebe50c86009b0a5c1 | partial | 1 | 7 | 14 | 7 |
| behavior:57fe53265e84cb8a329dc6a3 | partial | 1 | 7 | 14 | 7 |
| behavior:78a210c603a56f7c3729c160 | partial | 1 | 7 | 14 | 7 |
| behavior:98e95371fbd924c066d129d7 | partial | 1 | 7 | 14 | 7 |
| behavior:b1ba10943885b56eb43564df | partial | 1 | 7 | 14 | 7 |
| behavior:d6b7ac3dc5715b655c8543fb | partial | 1 | 7 | 14 | 7 |

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

## behavior:0fb1098b6305204245d430f8

Provides location and department hierarchy data used to group ledger output, including recursive parent, child, and path calculations.

Status: **partial**

### Implementation

- `app/source/gl/GLLedgerReporter.cls` (path-only, revision `071f786e237c0c92b385a19314f9a804c90ffedc`)

### Impact

- ia-app / behavior:0fb1098b6305204245d430f8 / confirmed
- ia-app / behavior:1e7ff88ebe50c86009b0a5c1 / confirmed
- ia-app / behavior:57fe53265e84cb8a329dc6a3 / confirmed
- ia-app / behavior:78a210c603a56f7c3729c160 / confirmed
- ia-app / behavior:98e95371fbd924c066d129d7 / confirmed
- ia-app / behavior:b1ba10943885b56eb43564df / confirmed
- ia-app / behavior:d6b7ac3dc5715b655c8543fb / confirmed

### Coverage

- ia-app / behavior:0fb1098b6305204245d430f8 / unavailable
- ia-app / behavior:1e7ff88ebe50c86009b0a5c1 / unavailable
- ia-app / behavior:57fe53265e84cb8a329dc6a3 / unavailable
- ia-app / behavior:78a210c603a56f7c3729c160 / unavailable
- ia-app / behavior:98e95371fbd924c066d129d7 / unavailable
- ia-app / behavior:b1ba10943885b56eb43564df / unavailable
- ia-app / behavior:d6b7ac3dc5715b655c8543fb / unavailable
- ia-app / behavior:0fb1098b6305204245d430f8 / candidate
- ia-app / behavior:1e7ff88ebe50c86009b0a5c1 / candidate
- ia-app / behavior:57fe53265e84cb8a329dc6a3 / candidate
- ia-app / behavior:78a210c603a56f7c3729c160 / candidate
- ia-app / behavior:98e95371fbd924c066d129d7 / candidate
- ia-app / behavior:b1ba10943885b56eb43564df / candidate
- ia-app / behavior:d6b7ac3dc5715b655c8543fb / candidate

### Actions

- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)

### Behavior Gaps

- `step3_test_suites:behavior:0fb1098b6305204245d430f8:unavailable`
- `step3_test_suites:behavior:1e7ff88ebe50c86009b0a5c1:unavailable`
- `step3_test_suites:behavior:57fe53265e84cb8a329dc6a3:unavailable`
- `step3_test_suites:behavior:78a210c603a56f7c3729c160:unavailable`
- `step3_test_suites:behavior:98e95371fbd924c066d129d7:unavailable`
- `step3_test_suites:behavior:b1ba10943885b56eb43564df:unavailable`
- `step3_test_suites:behavior:d6b7ac3dc5715b655c8543fb:unavailable`

## behavior:1e7ff88ebe50c86009b0a5c1

Instantiates the reporter and merges memorized user preferences with request parameters, including drill-filter and dimension defaults.

Status: **partial**

### Implementation

- `app/source/gl/GLLedgerReporter.cls` (path-only, revision `071f786e237c0c92b385a19314f9a804c90ffedc`)

### Impact

- ia-app / behavior:0fb1098b6305204245d430f8 / confirmed
- ia-app / behavior:1e7ff88ebe50c86009b0a5c1 / confirmed
- ia-app / behavior:57fe53265e84cb8a329dc6a3 / confirmed
- ia-app / behavior:78a210c603a56f7c3729c160 / confirmed
- ia-app / behavior:98e95371fbd924c066d129d7 / confirmed
- ia-app / behavior:b1ba10943885b56eb43564df / confirmed
- ia-app / behavior:d6b7ac3dc5715b655c8543fb / confirmed

### Coverage

- ia-app / behavior:0fb1098b6305204245d430f8 / unavailable
- ia-app / behavior:1e7ff88ebe50c86009b0a5c1 / unavailable
- ia-app / behavior:57fe53265e84cb8a329dc6a3 / unavailable
- ia-app / behavior:78a210c603a56f7c3729c160 / unavailable
- ia-app / behavior:98e95371fbd924c066d129d7 / unavailable
- ia-app / behavior:b1ba10943885b56eb43564df / unavailable
- ia-app / behavior:d6b7ac3dc5715b655c8543fb / unavailable
- ia-app / behavior:0fb1098b6305204245d430f8 / candidate
- ia-app / behavior:1e7ff88ebe50c86009b0a5c1 / candidate
- ia-app / behavior:57fe53265e84cb8a329dc6a3 / candidate
- ia-app / behavior:78a210c603a56f7c3729c160 / candidate
- ia-app / behavior:98e95371fbd924c066d129d7 / candidate
- ia-app / behavior:b1ba10943885b56eb43564df / candidate
- ia-app / behavior:d6b7ac3dc5715b655c8543fb / candidate

### Actions

- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)

### Behavior Gaps

- `step3_test_suites:behavior:0fb1098b6305204245d430f8:unavailable`
- `step3_test_suites:behavior:1e7ff88ebe50c86009b0a5c1:unavailable`
- `step3_test_suites:behavior:57fe53265e84cb8a329dc6a3:unavailable`
- `step3_test_suites:behavior:78a210c603a56f7c3729c160:unavailable`
- `step3_test_suites:behavior:98e95371fbd924c066d129d7:unavailable`
- `step3_test_suites:behavior:b1ba10943885b56eb43564df:unavailable`
- `step3_test_suites:behavior:d6b7ac3dc5715b655c8543fb:unavailable`

## behavior:57fe53265e84cb8a329dc6a3

Creates report XML by preprocessing, querying, rendering headers, grouping transactions, calculating totals, applying document-column processing, and serializing output.

Status: **partial**

### Implementation

- `app/source/gl/GLLedgerReporter.cls` (path-only, revision `071f786e237c0c92b385a19314f9a804c90ffedc`)

### Impact

- ia-app / behavior:0fb1098b6305204245d430f8 / confirmed
- ia-app / behavior:1e7ff88ebe50c86009b0a5c1 / confirmed
- ia-app / behavior:57fe53265e84cb8a329dc6a3 / confirmed
- ia-app / behavior:78a210c603a56f7c3729c160 / confirmed
- ia-app / behavior:98e95371fbd924c066d129d7 / confirmed
- ia-app / behavior:b1ba10943885b56eb43564df / confirmed
- ia-app / behavior:d6b7ac3dc5715b655c8543fb / confirmed

### Coverage

- ia-app / behavior:0fb1098b6305204245d430f8 / unavailable
- ia-app / behavior:1e7ff88ebe50c86009b0a5c1 / unavailable
- ia-app / behavior:57fe53265e84cb8a329dc6a3 / unavailable
- ia-app / behavior:78a210c603a56f7c3729c160 / unavailable
- ia-app / behavior:98e95371fbd924c066d129d7 / unavailable
- ia-app / behavior:b1ba10943885b56eb43564df / unavailable
- ia-app / behavior:d6b7ac3dc5715b655c8543fb / unavailable
- ia-app / behavior:0fb1098b6305204245d430f8 / candidate
- ia-app / behavior:1e7ff88ebe50c86009b0a5c1 / candidate
- ia-app / behavior:57fe53265e84cb8a329dc6a3 / candidate
- ia-app / behavior:78a210c603a56f7c3729c160 / candidate
- ia-app / behavior:98e95371fbd924c066d129d7 / candidate
- ia-app / behavior:b1ba10943885b56eb43564df / candidate
- ia-app / behavior:d6b7ac3dc5715b655c8543fb / candidate

### Actions

- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)

### Behavior Gaps

- `step3_test_suites:behavior:0fb1098b6305204245d430f8:unavailable`
- `step3_test_suites:behavior:1e7ff88ebe50c86009b0a5c1:unavailable`
- `step3_test_suites:behavior:57fe53265e84cb8a329dc6a3:unavailable`
- `step3_test_suites:behavior:78a210c603a56f7c3729c160:unavailable`
- `step3_test_suites:behavior:98e95371fbd924c066d129d7:unavailable`
- `step3_test_suites:behavior:b1ba10943885b56eb43564df:unavailable`
- `step3_test_suites:behavior:d6b7ac3dc5715b655c8543fb:unavailable`

## behavior:78a210c603a56f7c3729c160

Builds GL query filters, period and balance-forward data, transaction records, account maps, reporting-book state, and drill-down context.

Status: **partial**

### Implementation

- `app/source/gl/GLLedgerReporter.cls` (path-only, revision `071f786e237c0c92b385a19314f9a804c90ffedc`)

### Impact

- ia-app / behavior:0fb1098b6305204245d430f8 / confirmed
- ia-app / behavior:1e7ff88ebe50c86009b0a5c1 / confirmed
- ia-app / behavior:57fe53265e84cb8a329dc6a3 / confirmed
- ia-app / behavior:78a210c603a56f7c3729c160 / confirmed
- ia-app / behavior:98e95371fbd924c066d129d7 / confirmed
- ia-app / behavior:b1ba10943885b56eb43564df / confirmed
- ia-app / behavior:d6b7ac3dc5715b655c8543fb / confirmed

### Coverage

- ia-app / behavior:0fb1098b6305204245d430f8 / unavailable
- ia-app / behavior:1e7ff88ebe50c86009b0a5c1 / unavailable
- ia-app / behavior:57fe53265e84cb8a329dc6a3 / unavailable
- ia-app / behavior:78a210c603a56f7c3729c160 / unavailable
- ia-app / behavior:98e95371fbd924c066d129d7 / unavailable
- ia-app / behavior:b1ba10943885b56eb43564df / unavailable
- ia-app / behavior:d6b7ac3dc5715b655c8543fb / unavailable
- ia-app / behavior:0fb1098b6305204245d430f8 / candidate
- ia-app / behavior:1e7ff88ebe50c86009b0a5c1 / candidate
- ia-app / behavior:57fe53265e84cb8a329dc6a3 / candidate
- ia-app / behavior:78a210c603a56f7c3729c160 / candidate
- ia-app / behavior:98e95371fbd924c066d129d7 / candidate
- ia-app / behavior:b1ba10943885b56eb43564df / candidate
- ia-app / behavior:d6b7ac3dc5715b655c8543fb / candidate

### Actions

- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)

### Behavior Gaps

- `step3_test_suites:behavior:0fb1098b6305204245d430f8:unavailable`
- `step3_test_suites:behavior:1e7ff88ebe50c86009b0a5c1:unavailable`
- `step3_test_suites:behavior:57fe53265e84cb8a329dc6a3:unavailable`
- `step3_test_suites:behavior:78a210c603a56f7c3729c160:unavailable`
- `step3_test_suites:behavior:98e95371fbd924c066d129d7:unavailable`
- `step3_test_suites:behavior:b1ba10943885b56eb43564df:unavailable`
- `step3_test_suites:behavior:d6b7ac3dc5715b655c8543fb:unavailable`

## behavior:98e95371fbd924c066d129d7

Transforms queried ledger entries into report rows, optionally adds subtransactions, drill-down metadata, dimensions, and currency information.

Status: **partial**

### Implementation

- `app/source/gl/GLLedgerReporter.cls` (path-only, revision `071f786e237c0c92b385a19314f9a804c90ffedc`)

### Impact

- ia-app / behavior:0fb1098b6305204245d430f8 / confirmed
- ia-app / behavior:1e7ff88ebe50c86009b0a5c1 / confirmed
- ia-app / behavior:57fe53265e84cb8a329dc6a3 / confirmed
- ia-app / behavior:78a210c603a56f7c3729c160 / confirmed
- ia-app / behavior:98e95371fbd924c066d129d7 / confirmed
- ia-app / behavior:b1ba10943885b56eb43564df / confirmed
- ia-app / behavior:d6b7ac3dc5715b655c8543fb / confirmed

### Coverage

- ia-app / behavior:0fb1098b6305204245d430f8 / unavailable
- ia-app / behavior:1e7ff88ebe50c86009b0a5c1 / unavailable
- ia-app / behavior:57fe53265e84cb8a329dc6a3 / unavailable
- ia-app / behavior:78a210c603a56f7c3729c160 / unavailable
- ia-app / behavior:98e95371fbd924c066d129d7 / unavailable
- ia-app / behavior:b1ba10943885b56eb43564df / unavailable
- ia-app / behavior:d6b7ac3dc5715b655c8543fb / unavailable
- ia-app / behavior:0fb1098b6305204245d430f8 / candidate
- ia-app / behavior:1e7ff88ebe50c86009b0a5c1 / candidate
- ia-app / behavior:57fe53265e84cb8a329dc6a3 / candidate
- ia-app / behavior:78a210c603a56f7c3729c160 / candidate
- ia-app / behavior:98e95371fbd924c066d129d7 / candidate
- ia-app / behavior:b1ba10943885b56eb43564df / candidate
- ia-app / behavior:d6b7ac3dc5715b655c8543fb / candidate

### Actions

- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)

### Behavior Gaps

- `step3_test_suites:behavior:0fb1098b6305204245d430f8:unavailable`
- `step3_test_suites:behavior:1e7ff88ebe50c86009b0a5c1:unavailable`
- `step3_test_suites:behavior:57fe53265e84cb8a329dc6a3:unavailable`
- `step3_test_suites:behavior:78a210c603a56f7c3729c160:unavailable`
- `step3_test_suites:behavior:98e95371fbd924c066d129d7:unavailable`
- `step3_test_suites:behavior:b1ba10943885b56eb43564df:unavailable`
- `step3_test_suites:behavior:d6b7ac3dc5715b655c8543fb:unavailable`

## behavior:b1ba10943885b56eb43564df

Builds the primary and detailed GL transaction queries and populates transaction and subtransaction result state.

Status: **partial**

### Implementation

- `app/source/gl/GLLedgerReporter.cls` (path-only, revision `071f786e237c0c92b385a19314f9a804c90ffedc`)

### Impact

- ia-app / behavior:0fb1098b6305204245d430f8 / confirmed
- ia-app / behavior:1e7ff88ebe50c86009b0a5c1 / confirmed
- ia-app / behavior:57fe53265e84cb8a329dc6a3 / confirmed
- ia-app / behavior:78a210c603a56f7c3729c160 / confirmed
- ia-app / behavior:98e95371fbd924c066d129d7 / confirmed
- ia-app / behavior:b1ba10943885b56eb43564df / confirmed
- ia-app / behavior:d6b7ac3dc5715b655c8543fb / confirmed

### Coverage

- ia-app / behavior:0fb1098b6305204245d430f8 / unavailable
- ia-app / behavior:1e7ff88ebe50c86009b0a5c1 / unavailable
- ia-app / behavior:57fe53265e84cb8a329dc6a3 / unavailable
- ia-app / behavior:78a210c603a56f7c3729c160 / unavailable
- ia-app / behavior:98e95371fbd924c066d129d7 / unavailable
- ia-app / behavior:b1ba10943885b56eb43564df / unavailable
- ia-app / behavior:d6b7ac3dc5715b655c8543fb / unavailable
- ia-app / behavior:0fb1098b6305204245d430f8 / candidate
- ia-app / behavior:1e7ff88ebe50c86009b0a5c1 / candidate
- ia-app / behavior:57fe53265e84cb8a329dc6a3 / candidate
- ia-app / behavior:78a210c603a56f7c3729c160 / candidate
- ia-app / behavior:98e95371fbd924c066d129d7 / candidate
- ia-app / behavior:b1ba10943885b56eb43564df / candidate
- ia-app / behavior:d6b7ac3dc5715b655c8543fb / candidate

### Actions

- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)

### Behavior Gaps

- `step3_test_suites:behavior:0fb1098b6305204245d430f8:unavailable`
- `step3_test_suites:behavior:1e7ff88ebe50c86009b0a5c1:unavailable`
- `step3_test_suites:behavior:57fe53265e84cb8a329dc6a3:unavailable`
- `step3_test_suites:behavior:78a210c603a56f7c3729c160:unavailable`
- `step3_test_suites:behavior:98e95371fbd924c066d129d7:unavailable`
- `step3_test_suites:behavior:b1ba10943885b56eb43564df:unavailable`
- `step3_test_suites:behavior:d6b7ac3dc5715b655c8543fb:unavailable`

## behavior:d6b7ac3dc5715b655c8543fb

Constructs the GL ledger reporter, normalizes report parameters, initializes localization, authorization, currency, and feature-dependent state.

Status: **partial**

### Implementation

- `app/source/gl/GLLedgerReporter.cls` (path-only, revision `071f786e237c0c92b385a19314f9a804c90ffedc`)

### Impact

- ia-app / behavior:0fb1098b6305204245d430f8 / confirmed
- ia-app / behavior:1e7ff88ebe50c86009b0a5c1 / confirmed
- ia-app / behavior:57fe53265e84cb8a329dc6a3 / confirmed
- ia-app / behavior:78a210c603a56f7c3729c160 / confirmed
- ia-app / behavior:98e95371fbd924c066d129d7 / confirmed
- ia-app / behavior:b1ba10943885b56eb43564df / confirmed
- ia-app / behavior:d6b7ac3dc5715b655c8543fb / confirmed

### Coverage

- ia-app / behavior:0fb1098b6305204245d430f8 / unavailable
- ia-app / behavior:1e7ff88ebe50c86009b0a5c1 / unavailable
- ia-app / behavior:57fe53265e84cb8a329dc6a3 / unavailable
- ia-app / behavior:78a210c603a56f7c3729c160 / unavailable
- ia-app / behavior:98e95371fbd924c066d129d7 / unavailable
- ia-app / behavior:b1ba10943885b56eb43564df / unavailable
- ia-app / behavior:d6b7ac3dc5715b655c8543fb / unavailable
- ia-app / behavior:0fb1098b6305204245d430f8 / candidate
- ia-app / behavior:1e7ff88ebe50c86009b0a5c1 / candidate
- ia-app / behavior:57fe53265e84cb8a329dc6a3 / candidate
- ia-app / behavior:78a210c603a56f7c3729c160 / candidate
- ia-app / behavior:98e95371fbd924c066d129d7 / candidate
- ia-app / behavior:b1ba10943885b56eb43564df / candidate
- ia-app / behavior:d6b7ac3dc5715b655c8543fb / candidate

### Actions

- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)
- `request_owner_review` for `ia-app` (recommended)

### Behavior Gaps

- `step3_test_suites:behavior:0fb1098b6305204245d430f8:unavailable`
- `step3_test_suites:behavior:1e7ff88ebe50c86009b0a5c1:unavailable`
- `step3_test_suites:behavior:57fe53265e84cb8a329dc6a3:unavailable`
- `step3_test_suites:behavior:78a210c603a56f7c3729c160:unavailable`
- `step3_test_suites:behavior:98e95371fbd924c066d129d7:unavailable`
- `step3_test_suites:behavior:b1ba10943885b56eb43564df:unavailable`
- `step3_test_suites:behavior:d6b7ac3dc5715b655c8543fb:unavailable`

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
