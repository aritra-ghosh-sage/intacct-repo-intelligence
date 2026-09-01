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
| behavior:456b124545a723809a382731 | partial | 1 | 7 | 14 | 7 |
| behavior:57fe53265e84cb8a329dc6a3 | partial | 1 | 7 | 14 | 7 |
| behavior:78a210c603a56f7c3729c160 | partial | 1 | 7 | 14 | 7 |
| behavior:98e95371fbd924c066d129d7 | partial | 1 | 7 | 14 | 7 |
| behavior:ae03d1eb8cf41afac12a4c8e | partial | 1 | 7 | 14 | 7 |
| behavior:dfb0f8b38f9e3f4651944fb2 | partial | 1 | 7 | 14 | 7 |

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

Builds department and location hierarchies and derives parent, child, display-name, and record-path information used by grouped report output.

Status: **partial**

### Implementation

- `app/source/gl/GLLedgerReporter.cls` (path-only, revision `071f786e237c0c92b385a19314f9a804c90ffedc`)

### Impact

- ia-app / behavior:0fb1098b6305204245d430f8 / confirmed
- ia-app / behavior:456b124545a723809a382731 / confirmed
- ia-app / behavior:57fe53265e84cb8a329dc6a3 / confirmed
- ia-app / behavior:78a210c603a56f7c3729c160 / confirmed
- ia-app / behavior:98e95371fbd924c066d129d7 / confirmed
- ia-app / behavior:ae03d1eb8cf41afac12a4c8e / confirmed
- ia-app / behavior:dfb0f8b38f9e3f4651944fb2 / confirmed

### Coverage

- ia-app / behavior:0fb1098b6305204245d430f8 / unavailable
- ia-app / behavior:456b124545a723809a382731 / unavailable
- ia-app / behavior:57fe53265e84cb8a329dc6a3 / unavailable
- ia-app / behavior:78a210c603a56f7c3729c160 / unavailable
- ia-app / behavior:98e95371fbd924c066d129d7 / unavailable
- ia-app / behavior:ae03d1eb8cf41afac12a4c8e / unavailable
- ia-app / behavior:dfb0f8b38f9e3f4651944fb2 / unavailable
- ia-app / behavior:0fb1098b6305204245d430f8 / candidate
- ia-app / behavior:456b124545a723809a382731 / candidate
- ia-app / behavior:57fe53265e84cb8a329dc6a3 / candidate
- ia-app / behavior:78a210c603a56f7c3729c160 / candidate
- ia-app / behavior:98e95371fbd924c066d129d7 / candidate
- ia-app / behavior:ae03d1eb8cf41afac12a4c8e / candidate
- ia-app / behavior:dfb0f8b38f9e3f4651944fb2 / candidate

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
- `step3_test_suites:behavior:456b124545a723809a382731:unavailable`
- `step3_test_suites:behavior:57fe53265e84cb8a329dc6a3:unavailable`
- `step3_test_suites:behavior:78a210c603a56f7c3729c160:unavailable`
- `step3_test_suites:behavior:98e95371fbd924c066d129d7:unavailable`
- `step3_test_suites:behavior:ae03d1eb8cf41afac12a4c8e:unavailable`
- `step3_test_suites:behavior:dfb0f8b38f9e3f4651944fb2:unavailable`

## behavior:456b124545a723809a382731

Groups report transactions into monthly or custom accounting-period structures and recursively rewrites report arrays to contain period subtotals.

Status: **partial**

### Implementation

- `app/source/gl/GLLedgerReporter.cls` (path-only, revision `071f786e237c0c92b385a19314f9a804c90ffedc`)

### Impact

- ia-app / behavior:0fb1098b6305204245d430f8 / confirmed
- ia-app / behavior:456b124545a723809a382731 / confirmed
- ia-app / behavior:57fe53265e84cb8a329dc6a3 / confirmed
- ia-app / behavior:78a210c603a56f7c3729c160 / confirmed
- ia-app / behavior:98e95371fbd924c066d129d7 / confirmed
- ia-app / behavior:ae03d1eb8cf41afac12a4c8e / confirmed
- ia-app / behavior:dfb0f8b38f9e3f4651944fb2 / confirmed

### Coverage

- ia-app / behavior:0fb1098b6305204245d430f8 / unavailable
- ia-app / behavior:456b124545a723809a382731 / unavailable
- ia-app / behavior:57fe53265e84cb8a329dc6a3 / unavailable
- ia-app / behavior:78a210c603a56f7c3729c160 / unavailable
- ia-app / behavior:98e95371fbd924c066d129d7 / unavailable
- ia-app / behavior:ae03d1eb8cf41afac12a4c8e / unavailable
- ia-app / behavior:dfb0f8b38f9e3f4651944fb2 / unavailable
- ia-app / behavior:0fb1098b6305204245d430f8 / candidate
- ia-app / behavior:456b124545a723809a382731 / candidate
- ia-app / behavior:57fe53265e84cb8a329dc6a3 / candidate
- ia-app / behavior:78a210c603a56f7c3729c160 / candidate
- ia-app / behavior:98e95371fbd924c066d129d7 / candidate
- ia-app / behavior:ae03d1eb8cf41afac12a4c8e / candidate
- ia-app / behavior:dfb0f8b38f9e3f4651944fb2 / candidate

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
- `step3_test_suites:behavior:456b124545a723809a382731:unavailable`
- `step3_test_suites:behavior:57fe53265e84cb8a329dc6a3:unavailable`
- `step3_test_suites:behavior:78a210c603a56f7c3729c160:unavailable`
- `step3_test_suites:behavior:98e95371fbd924c066d129d7:unavailable`
- `step3_test_suites:behavior:ae03d1eb8cf41afac12a4c8e:unavailable`
- `step3_test_suites:behavior:dfb0f8b38f9e3f4651944fb2:unavailable`

## behavior:57fe53265e84cb8a329dc6a3

Builds the GL report data XML by initializing reporting context, running pre-query and query processing, constructing report headers and transaction XML, and returning either inline XML or a temporary-file path.

Status: **partial**

### Implementation

- `app/source/gl/GLLedgerReporter.cls` (path-only, revision `071f786e237c0c92b385a19314f9a804c90ffedc`)

### Impact

- ia-app / behavior:0fb1098b6305204245d430f8 / confirmed
- ia-app / behavior:456b124545a723809a382731 / confirmed
- ia-app / behavior:57fe53265e84cb8a329dc6a3 / confirmed
- ia-app / behavior:78a210c603a56f7c3729c160 / confirmed
- ia-app / behavior:98e95371fbd924c066d129d7 / confirmed
- ia-app / behavior:ae03d1eb8cf41afac12a4c8e / confirmed
- ia-app / behavior:dfb0f8b38f9e3f4651944fb2 / confirmed

### Coverage

- ia-app / behavior:0fb1098b6305204245d430f8 / unavailable
- ia-app / behavior:456b124545a723809a382731 / unavailable
- ia-app / behavior:57fe53265e84cb8a329dc6a3 / unavailable
- ia-app / behavior:78a210c603a56f7c3729c160 / unavailable
- ia-app / behavior:98e95371fbd924c066d129d7 / unavailable
- ia-app / behavior:ae03d1eb8cf41afac12a4c8e / unavailable
- ia-app / behavior:dfb0f8b38f9e3f4651944fb2 / unavailable
- ia-app / behavior:0fb1098b6305204245d430f8 / candidate
- ia-app / behavior:456b124545a723809a382731 / candidate
- ia-app / behavior:57fe53265e84cb8a329dc6a3 / candidate
- ia-app / behavior:78a210c603a56f7c3729c160 / candidate
- ia-app / behavior:98e95371fbd924c066d129d7 / candidate
- ia-app / behavior:ae03d1eb8cf41afac12a4c8e / candidate
- ia-app / behavior:dfb0f8b38f9e3f4651944fb2 / candidate

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
- `step3_test_suites:behavior:456b124545a723809a382731:unavailable`
- `step3_test_suites:behavior:57fe53265e84cb8a329dc6a3:unavailable`
- `step3_test_suites:behavior:78a210c603a56f7c3729c160:unavailable`
- `step3_test_suites:behavior:98e95371fbd924c066d129d7:unavailable`
- `step3_test_suites:behavior:ae03d1eb8cf41afac12a4c8e:unavailable`
- `step3_test_suites:behavior:dfb0f8b38f9e3f4651944fb2:unavailable`

## behavior:78a210c603a56f7c3729c160

Prepares GL report filters and accounting context, resolves period and account/location/department data, retrieves balances and transaction records, and prepares drill-down state for report generation.

Status: **partial**

### Implementation

- `app/source/gl/GLLedgerReporter.cls` (path-only, revision `071f786e237c0c92b385a19314f9a804c90ffedc`)

### Impact

- ia-app / behavior:0fb1098b6305204245d430f8 / confirmed
- ia-app / behavior:456b124545a723809a382731 / confirmed
- ia-app / behavior:57fe53265e84cb8a329dc6a3 / confirmed
- ia-app / behavior:78a210c603a56f7c3729c160 / confirmed
- ia-app / behavior:98e95371fbd924c066d129d7 / confirmed
- ia-app / behavior:ae03d1eb8cf41afac12a4c8e / confirmed
- ia-app / behavior:dfb0f8b38f9e3f4651944fb2 / confirmed

### Coverage

- ia-app / behavior:0fb1098b6305204245d430f8 / unavailable
- ia-app / behavior:456b124545a723809a382731 / unavailable
- ia-app / behavior:57fe53265e84cb8a329dc6a3 / unavailable
- ia-app / behavior:78a210c603a56f7c3729c160 / unavailable
- ia-app / behavior:98e95371fbd924c066d129d7 / unavailable
- ia-app / behavior:ae03d1eb8cf41afac12a4c8e / unavailable
- ia-app / behavior:dfb0f8b38f9e3f4651944fb2 / unavailable
- ia-app / behavior:0fb1098b6305204245d430f8 / candidate
- ia-app / behavior:456b124545a723809a382731 / candidate
- ia-app / behavior:57fe53265e84cb8a329dc6a3 / candidate
- ia-app / behavior:78a210c603a56f7c3729c160 / candidate
- ia-app / behavior:98e95371fbd924c066d129d7 / candidate
- ia-app / behavior:ae03d1eb8cf41afac12a4c8e / candidate
- ia-app / behavior:dfb0f8b38f9e3f4651944fb2 / candidate

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
- `step3_test_suites:behavior:456b124545a723809a382731:unavailable`
- `step3_test_suites:behavior:57fe53265e84cb8a329dc6a3:unavailable`
- `step3_test_suites:behavior:78a210c603a56f7c3729c160:unavailable`
- `step3_test_suites:behavior:98e95371fbd924c066d129d7:unavailable`
- `step3_test_suites:behavior:ae03d1eb8cf41afac12a4c8e:unavailable`
- `step3_test_suites:behavior:dfb0f8b38f9e3f4651944fb2:unavailable`

## behavior:98e95371fbd924c066d129d7

Transforms GL and subledger query records into report transaction rows, including debit/credit values, document and drill-down data, dimensions, currencies, and subtransactions.

Status: **partial**

### Implementation

- `app/source/gl/GLLedgerReporter.cls` (path-only, revision `071f786e237c0c92b385a19314f9a804c90ffedc`)

### Impact

- ia-app / behavior:0fb1098b6305204245d430f8 / confirmed
- ia-app / behavior:456b124545a723809a382731 / confirmed
- ia-app / behavior:57fe53265e84cb8a329dc6a3 / confirmed
- ia-app / behavior:78a210c603a56f7c3729c160 / confirmed
- ia-app / behavior:98e95371fbd924c066d129d7 / confirmed
- ia-app / behavior:ae03d1eb8cf41afac12a4c8e / confirmed
- ia-app / behavior:dfb0f8b38f9e3f4651944fb2 / confirmed

### Coverage

- ia-app / behavior:0fb1098b6305204245d430f8 / unavailable
- ia-app / behavior:456b124545a723809a382731 / unavailable
- ia-app / behavior:57fe53265e84cb8a329dc6a3 / unavailable
- ia-app / behavior:78a210c603a56f7c3729c160 / unavailable
- ia-app / behavior:98e95371fbd924c066d129d7 / unavailable
- ia-app / behavior:ae03d1eb8cf41afac12a4c8e / unavailable
- ia-app / behavior:dfb0f8b38f9e3f4651944fb2 / unavailable
- ia-app / behavior:0fb1098b6305204245d430f8 / candidate
- ia-app / behavior:456b124545a723809a382731 / candidate
- ia-app / behavior:57fe53265e84cb8a329dc6a3 / candidate
- ia-app / behavior:78a210c603a56f7c3729c160 / candidate
- ia-app / behavior:98e95371fbd924c066d129d7 / candidate
- ia-app / behavior:ae03d1eb8cf41afac12a4c8e / candidate
- ia-app / behavior:dfb0f8b38f9e3f4651944fb2 / candidate

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
- `step3_test_suites:behavior:456b124545a723809a382731:unavailable`
- `step3_test_suites:behavior:57fe53265e84cb8a329dc6a3:unavailable`
- `step3_test_suites:behavior:78a210c603a56f7c3729c160:unavailable`
- `step3_test_suites:behavior:98e95371fbd924c066d129d7:unavailable`
- `step3_test_suites:behavior:ae03d1eb8cf41afac12a4c8e:unavailable`
- `step3_test_suites:behavior:dfb0f8b38f9e3f4651944fb2:unavailable`

## behavior:ae03d1eb8cf41afac12a4c8e

Populates report header fields, reporting titles, dimension headers, transaction-state filters, consolidation metadata, and transaction drill-down operation metadata.

Status: **partial**

### Implementation

- `app/source/gl/GLLedgerReporter.cls` (path-only, revision `071f786e237c0c92b385a19314f9a804c90ffedc`)

### Impact

- ia-app / behavior:0fb1098b6305204245d430f8 / confirmed
- ia-app / behavior:456b124545a723809a382731 / confirmed
- ia-app / behavior:57fe53265e84cb8a329dc6a3 / confirmed
- ia-app / behavior:78a210c603a56f7c3729c160 / confirmed
- ia-app / behavior:98e95371fbd924c066d129d7 / confirmed
- ia-app / behavior:ae03d1eb8cf41afac12a4c8e / confirmed
- ia-app / behavior:dfb0f8b38f9e3f4651944fb2 / confirmed

### Coverage

- ia-app / behavior:0fb1098b6305204245d430f8 / unavailable
- ia-app / behavior:456b124545a723809a382731 / unavailable
- ia-app / behavior:57fe53265e84cb8a329dc6a3 / unavailable
- ia-app / behavior:78a210c603a56f7c3729c160 / unavailable
- ia-app / behavior:98e95371fbd924c066d129d7 / unavailable
- ia-app / behavior:ae03d1eb8cf41afac12a4c8e / unavailable
- ia-app / behavior:dfb0f8b38f9e3f4651944fb2 / unavailable
- ia-app / behavior:0fb1098b6305204245d430f8 / candidate
- ia-app / behavior:456b124545a723809a382731 / candidate
- ia-app / behavior:57fe53265e84cb8a329dc6a3 / candidate
- ia-app / behavior:78a210c603a56f7c3729c160 / candidate
- ia-app / behavior:98e95371fbd924c066d129d7 / candidate
- ia-app / behavior:ae03d1eb8cf41afac12a4c8e / candidate
- ia-app / behavior:dfb0f8b38f9e3f4651944fb2 / candidate

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
- `step3_test_suites:behavior:456b124545a723809a382731:unavailable`
- `step3_test_suites:behavior:57fe53265e84cb8a329dc6a3:unavailable`
- `step3_test_suites:behavior:78a210c603a56f7c3729c160:unavailable`
- `step3_test_suites:behavior:98e95371fbd924c066d129d7:unavailable`
- `step3_test_suites:behavior:ae03d1eb8cf41afac12a4c8e:unavailable`
- `step3_test_suites:behavior:dfb0f8b38f9e3f4651944fb2:unavailable`

## behavior:dfb0f8b38f9e3f4651944fb2

Conditionally replaces eligible document-column values with record identifiers for accrual detailed reports and recursively applies the transformation to transaction and subtransaction rows.

Status: **partial**

### Implementation

- `app/source/gl/GLLedgerReporter.cls` (path-only, revision `071f786e237c0c92b385a19314f9a804c90ffedc`)

### Impact

- ia-app / behavior:0fb1098b6305204245d430f8 / confirmed
- ia-app / behavior:456b124545a723809a382731 / confirmed
- ia-app / behavior:57fe53265e84cb8a329dc6a3 / confirmed
- ia-app / behavior:78a210c603a56f7c3729c160 / confirmed
- ia-app / behavior:98e95371fbd924c066d129d7 / confirmed
- ia-app / behavior:ae03d1eb8cf41afac12a4c8e / confirmed
- ia-app / behavior:dfb0f8b38f9e3f4651944fb2 / confirmed

### Coverage

- ia-app / behavior:0fb1098b6305204245d430f8 / unavailable
- ia-app / behavior:456b124545a723809a382731 / unavailable
- ia-app / behavior:57fe53265e84cb8a329dc6a3 / unavailable
- ia-app / behavior:78a210c603a56f7c3729c160 / unavailable
- ia-app / behavior:98e95371fbd924c066d129d7 / unavailable
- ia-app / behavior:ae03d1eb8cf41afac12a4c8e / unavailable
- ia-app / behavior:dfb0f8b38f9e3f4651944fb2 / unavailable
- ia-app / behavior:0fb1098b6305204245d430f8 / candidate
- ia-app / behavior:456b124545a723809a382731 / candidate
- ia-app / behavior:57fe53265e84cb8a329dc6a3 / candidate
- ia-app / behavior:78a210c603a56f7c3729c160 / candidate
- ia-app / behavior:98e95371fbd924c066d129d7 / candidate
- ia-app / behavior:ae03d1eb8cf41afac12a4c8e / candidate
- ia-app / behavior:dfb0f8b38f9e3f4651944fb2 / candidate

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
- `step3_test_suites:behavior:456b124545a723809a382731:unavailable`
- `step3_test_suites:behavior:57fe53265e84cb8a329dc6a3:unavailable`
- `step3_test_suites:behavior:78a210c603a56f7c3729c160:unavailable`
- `step3_test_suites:behavior:98e95371fbd924c066d129d7:unavailable`
- `step3_test_suites:behavior:ae03d1eb8cf41afac12a4c8e:unavailable`
- `step3_test_suites:behavior:dfb0f8b38f9e3f4651944fb2:unavailable`

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
