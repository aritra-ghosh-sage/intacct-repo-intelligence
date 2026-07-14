# Phase 2D.1 Validation Report

## xslt_coverage

```json
{
  "xslt_file_count": 301,
  "xslt_mapping_count": 0,
  "status": "SKIP",
  "reason": "XSLT extraction not yet implemented: 301 files exist but entity mapping discovery is disabled. See scripts/build_entities.py lines 645-665 (commented as dead code). Requires uncommenting XSLT discovery or implementing filename-based matching."
}
```

## openapi_linkage

```json
{
  "total_openapispec_files": 3731,
  "linked_files": 2157,
  "linkage_percent": 57.81291878852854,
  "threshold_percent": 30.0,
  "with_explicit_mapping": 779,
  "without_explicit_mapping": 2952,
  "kind_distribution": {
    "operations": 935,
    "history": 923,
    "schema": 918,
    "view": 575,
    "uimeta": 371,
    "unknown": 8,
    "actions": 1
  },
  "status": "PASS"
}
```

## rest_endpoints_coverage

```json
{
  "total_endpoints": 2804,
  "entities_with_endpoints": 680,
  "total_entities": 1897,
  "coverage_percent": 35.84607274644175,
  "status": "REPORT",
  "note": "No threshold set. Reported for awareness."
}
```

## entity_recall_v2

```json
{
  "gold_size": 27,
  "discovered_size": 1897,
  "matched": 27,
  "missing": [],
  "recall_percent": 100.0,
  "status": "PASS"
}
```

