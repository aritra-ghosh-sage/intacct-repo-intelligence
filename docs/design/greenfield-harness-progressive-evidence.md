# Greenfield Harness progressive evidence experiment

This is an experimental parallel flow, not a replacement for the supported Greenfield runner. Its only entry point is `scripts/run_greenfield_harness.py`; it is analyze-only and has no publish, draft, Step 6, Step 7, Step 8, GitHub, or catalog-mutation behavior.

The immutable source of truth is `harness-analysis.json` and its retained L1/L2/L3 ledger. `behavior-impact-report.json`, `behavior-impact-report.md`, and `review.md` are projections only and cannot make independent evidence or action decisions.

The flow captures a revision-bound identity, creates a deterministic behavior packet, locates exact L1 handbook/contract navigation entries, reads only revision-bound L2 excerpts, and performs literal L3 searches only for caller-supplied material gaps. L2 consumes its byte budget from the UTF-8 bytes of each retained excerpt, not the complete Git blob used to extract it. It orders non-empty excerpt ranges deterministically: application/test paths first, then other changed paths, then metadata/documentation (including `.github/**`), with lexical path tie-breaking. Each successful L2 read records the packet-bound `source_blob_sha256` and an `excerpt_sha256` for the retained excerpt itself. The handbook is navigation, never authoritative impact evidence. Claims remain `candidate`, `unresolved`, `unavailable`, or `no_evidence` unless retained revision-bound evidence supports promotion.

`max_evidence_reads` is the shared L2/L3 ledger-entry budget. It limits retained local source reads and searches; it is not a model-provider tool-call limit.

`harness-flow-handoff.json` binds `capture`, `behavior_packet`, `l1_locate`, `l2_inspect`, `l3_resolve`, `analyze`, and `project` in fixed order. Each reference has a SHA-256 digest. Missing identity, revisions, hashes, altered artifacts, or out-of-order stages fail closed.

Example:

```bash
PYTHONPATH=. ./.venv/bin/python scripts/run_greenfield_harness.py \
  --source-root "$HOME/projects/main" --pr 123 \
  --base-revision <40-char-sha> --target-revision <40-char-sha> \
  --output-dir artifacts/greenfield-harness/pr-123-<target-sha>
```
