# PR review prompt surface

`catalog.pr_review_prompt` and `scripts/generate_pr_review_prompt.py` provide a
read-only orchestration surface for generating a complete LLM PR-review prompt.

```bash
PYTHONPATH=. ./.venv/bin/python scripts/generate_pr_review_prompt.py \
  --pr 48480 \
  --request "Review this PR for correctness and regressions." \
  --manifest config/workspace_repos.yaml \
  --repo-key ia-main \
  --active-db /path/to/exact-target-repo-v1/catalog.db
```

The command fetches PR files, reviews, inline comments, issue comments, and
check runs through the existing `gh`-first metadata intake. It builds and
validates Step 0 in memory, runs the current Step 1, Step 2, and Step 3
analyzers in bounded task order, and prints a JSON envelope containing
`prompt_text`, task contracts, and the generated reports. `--prompt-only`
prints only the prompt text. No fixture, prompt, catalog, graph, or Markdown
review artifact is written.

Comments are included in the LLM prompt as analysis context, with their
revision and source metadata where available. The prompt explicitly forbids
copying comment text into the final review Markdown. The final LLM response
must use `docs/review/pr-review-template.md` exactly.

The surface fails closed for an unavailable or non-exact target-revision
catalog. It still emits a blocked prompt envelope when the local analyzers
can represent the blocker, so the LLM can report the limitation without
claiming no impact.
