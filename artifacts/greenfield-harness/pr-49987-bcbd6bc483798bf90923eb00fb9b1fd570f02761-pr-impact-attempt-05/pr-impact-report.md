# Harness PR-impact report

Canonical analysis: `pr-impact-analysis.json`

```json
{
  "coverage": [
    {
      "behavior_id": "behavior-1",
      "status": "no_evidence",
      "source_evidence_ids": [
        "extract:app/source/gl/GLSetupAtEntityManager.cls:symbol:GLSetupAtEntityManager:11"
      ],
      "test_evidence": [],
      "ci_execution": {
        "status": "unavailable",
        "reason": "live_ci_evidence_out_of_scope"
      }
    },
    {
      "behavior_id": "behavior-2",
      "status": "no_evidence",
      "source_evidence_ids": [
        "extract:app/source/gl/GLSetupManager.cls:symbol:GLSetupManager:18"
      ],
      "test_evidence": [],
      "ci_execution": {
        "status": "unavailable",
        "reason": "live_ci_evidence_out_of_scope"
      }
    }
  ],
  "recommendations": [
    {
      "id": "recommendation:behavior-1",
      "status": "candidate",
      "behavior_id": "behavior-1",
      "recommendation": "Add a revision-pinned test that exercises: Class GLSetupAtEntityManager was modified, potentially altering GL setup logic at the entity manager level",
      "source_evidence_ids": [
        "extract:app/source/gl/GLSetupAtEntityManager.cls:symbol:GLSetupAtEntityManager:11"
      ],
      "reason": "no_matching_pinned_test_evidence"
    },
    {
      "id": "recommendation:behavior-2",
      "status": "candidate",
      "behavior_id": "behavior-2",
      "recommendation": "Add a revision-pinned test that exercises: Class GLSetupManager was modified, potentially altering general ledger setup management logic",
      "source_evidence_ids": [
        "extract:app/source/gl/GLSetupManager.cls:symbol:GLSetupManager:18"
      ],
      "reason": "no_matching_pinned_test_evidence"
    }
  ]
}
```
