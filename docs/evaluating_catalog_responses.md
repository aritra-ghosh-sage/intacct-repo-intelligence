# Evaluating Catalog Responses

The evaluator scores the final natural-language answer, not the raw catalog query. The answer adapter is responsible for the production response path: it may call MCP, invoke a `query_* --json` script, or call a model service.

The adapter receives one JSON object on stdin per case:

```json
{"case_id":"stats_top_languages","prompt":"...","payload":{"...":"..."},"response_contract":"concise grounded answer"}
```

It must write one JSON object to stdout:

```json
{"case_id":"stats_top_languages","answer":"...","model":"optional-model-name"}
```

Gold references and evaluator-only constraints are not sent to the adapter.

Run deterministic scoring for a recorded answer:

```bash
./.venv/bin/python scripts/eval_catalog_response.py \
  --case-id stats_top_languages \
  --actual-output "The catalog has 52,104 files across 13 languages, with XML leading." \
  --json
```

Run the adapter once per case:

```bash
./.venv/bin/python scripts/eval_catalog_response.py \
  --adapter-command-json '["python","scripts/my_catalog_response_adapter.py"]' \
  --run-deepeval \
  --require-deepeval \
  --json
```

`hard_fail` means the answer contains an unsupported claim or contradicts computed evidence. `quality_fail` means deterministic checks passed but completeness, format, or Deepeval thresholds failed. `indeterminate` means Deepeval was requested but could not run. Regenerate or verify evidence snapshots with:

```bash
./.venv/bin/python scripts/verify_catalog_eval_dataset.py --verify
```
