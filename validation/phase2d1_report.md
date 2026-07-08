# Phase 2D.1 Validation Report

## xslt_coverage

```json
{
  "xslt_file_count": 301,
  "xslt_mapping_count": 0,
  "status": "SKIP",
  "reason": "XSLT files exist but no XSLT-linked entity mappings were produced in the current run"
}
```

## openapi_linkage

```json
{
  "total_openapispec_files": 3959,
  "linked_files": 1454,
  "linkage_percent": 36.72644607224046,
  "threshold_percent": 30.0,
  "status": "PASS"
}
```

## entity_recall_v2

```json
{
  "gold_size": 54,
  "discovered_size": 1897,
  "matched": 40,
  "missing": [
    "Account",
    "AccountAllocation",
    "AccountAllocationBasis",
    "AccountAllocationBasisAdjustmentBook",
    "AccountAllocationGroup",
    "AccountAllocationGroupMember",
    "AccountAllocationReverse",
    "AccountAllocationRun",
    "AccountAllocationSource",
    "AccountAllocationSourceAdjustmentBook",
    "AccountAllocationTarget",
    "AccountBalance",
    "AccountBalanceByDimension",
    "AccountCategory"
  ],
  "recall_percent": 74.07407407407408,
  "status": "PASS"
}
```

