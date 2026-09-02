# Harness PR-impact report

Canonical analysis: `pr-impact-analysis.json`

```json
{
  "coverage": [
    {
      "behavior_id": "behavior:gl-setup-manager-classes-changed",
      "status": "no_evidence",
      "source_evidence_ids": [
        "extract:app/source/gl/GLSetupAtEntityManager.cls:symbol:GLSetupAtEntityManager:11",
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
      "id": "recommendation:behavior:gl-setup-manager-classes-changed",
      "status": "candidate",
      "behavior_id": "behavior:gl-setup-manager-classes-changed",
      "recommendation": "Add a revision-pinned test that exercises: PR modifies GL setup manager classes (GLSetupAtEntityManager and GLSetupManager); source-level flow of data/control into and out of these classes needs investigation to characterize the change.",
      "source_evidence_ids": [
        "extract:app/source/gl/GLSetupAtEntityManager.cls:symbol:GLSetupAtEntityManager:11",
        "extract:app/source/gl/GLSetupManager.cls:symbol:GLSetupManager:18"
      ],
      "reason": "no_matching_pinned_test_evidence"
    }
  ]
}
```
