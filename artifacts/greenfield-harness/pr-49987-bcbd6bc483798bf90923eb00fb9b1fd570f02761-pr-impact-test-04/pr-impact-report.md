# Harness PR-impact report

Canonical analysis: `pr-impact-analysis.json`

```json
{
  "coverage": [
    {
      "behavior_id": "behavior:GLSetupAtEntityManager:changed",
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
      "behavior_id": "behavior:GLSetupManager:changed",
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
      "id": "recommendation:behavior:GLSetupAtEntityManager:changed",
      "status": "candidate",
      "behavior_id": "behavior:GLSetupAtEntityManager:changed",
      "recommendation": "Add a revision-pinned test that exercises: GLSetupAtEntityManager class was modified in this PR; its role in GL entity-level setup flows warrants source-level review.",
      "source_evidence_ids": [
        "extract:app/source/gl/GLSetupAtEntityManager.cls:symbol:GLSetupAtEntityManager:11"
      ],
      "reason": "no_matching_pinned_test_evidence"
    },
    {
      "id": "recommendation:behavior:GLSetupManager:changed",
      "status": "candidate",
      "behavior_id": "behavior:GLSetupManager:changed",
      "recommendation": "Add a revision-pinned test that exercises: GLSetupManager class was modified in this PR; its role in GL setup flows warrants source-level review.",
      "source_evidence_ids": [
        "extract:app/source/gl/GLSetupManager.cls:symbol:GLSetupManager:18"
      ],
      "reason": "no_matching_pinned_test_evidence"
    }
  ]
}
```
