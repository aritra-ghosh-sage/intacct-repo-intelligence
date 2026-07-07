# Phase 2D Validation Report
**Generated:** 2026-07-07T07:40:43.184473
## Summary
- **Passed:** 4
- **Failed:** 0
- **Reported (informational):** 3

**Status: ✅ PASSED** - All assertions passed.

---
## Detailed Results

### ✅ tables_exist
**Status:** PASS
- Required: 8
- Present: 8
- Assertion: All required Phase 2D tables must exist.

### ✅ cqry_coverage
**Status:** PASS
- .cqry symbols: 7747
- .cqry mappings: 311
- Assertion: Both counts must be > 0
- Note: Both counts should be > 0 per ISSUE-001

### ✅ declared_vs_actual_mapping_types
**Status:** PASS
**Declared vs Actual Mapping Types:**
- .ent (HIGH): ✓ found (expected: manager)
- .cls (HIGH): ✓ found (expected: editor)
- .inc (HIGH): ✓ found (expected: inc)
- .cqry (HIGH): ✓ found (expected: cqry)
- .yaml (HIGH): ✓ found (expected: openapispec_schema, openapispec_operations, openapispec_history, yaml)
- .sql (MEDIUM): ✓ found (expected: sql)
- .xslt (MEDIUM): ⊘ out of scope (0 files found in repository)
- .html (MEDIUM): ✓ found (expected: html)
- .phtml (MEDIUM): ✓ found (expected: phtml)

### ✅ mapping_provenance
**Status:** PASS
- Total mappings: 8032
- Orphan mappings (no source_text/file_id): 0
- Assertion: All mappings must have provenance (orphan_count == 0)

### ℹ️ workflow_step_ratio
**Status:** REPORT
- Workflows: 302
- Workflow steps: 302
- Avg steps per workflow: 1.0
- Note: This is a modeling decision (see ISSUE-004). No assertion threshold.

### ℹ️ rest_endpoint_coverage
**Status:** REPORT
- Total entities: 1807
- Entities with REST endpoints: 0
- Coverage: 0.0%
- Note: No threshold set. Reported for awareness.

### ℹ️ ui_companion_coverage
**Status:** REPORT
- Total entities: 1807
- Entities with UI companions: 938
- Coverage: 51.91%
- Note: No threshold set. Reported for awareness.
