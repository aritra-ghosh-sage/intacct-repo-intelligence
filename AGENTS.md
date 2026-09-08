# Repository Guidelines

## Project Structure & Module Organization

This repository contains the `ia_repomap_builder/` Python package, its tests in
`tests/`, and design notes in `docs/design/`. Keep new research documents in
that directory and use descriptive, kebab-case filenames. The canonical
repository-map contract is
`docs/design/ia-repomap-context-contract.md`; the independent blast-radius
research is `docs/design/llm-agent-pr-blast-radius-research.md`.

## Development and Validation Commands

There is no packaging build step. Use lightweight checks from the repository
root:

- `git diff --check` detects whitespace errors before a commit.
- `rg -n '^#{1,6} ' docs/` reviews heading structure across the documentation.
- `./.venv/bin/python -m unittest discover -s tests -p 'test_*.py'` runs the package tests.
- `git status --short` confirms that only intended files are included.

Also preview edited Markdown in a renderer that supports tables and Mermaid, and
manually verify links, diagrams, and list numbering.

## Writing Style & Naming Conventions

Write concise Markdown with one H1, sentence-case headings, short paragraphs, and
focused lists. Wrap prose at roughly 80 characters where practical. Use fenced
code blocks with a language identifier, such as `mermaid` or `shell`. Define
specialized terms on first use and separate confirmed evidence from candidates,
unresolved questions, and unavailable data. Research documents should identify
their date and scope near the top.

## Testing Guidelines

Treat rendered-document review as the current acceptance check. Confirm that
tables remain readable, Mermaid syntax renders, examples match the surrounding
claims, and revision- or file-specific assertions include traceable citations.
Executable changes require automated tests in the same change. Document the
exact command required to run them, and keep repository-map indexes, caches,
context bundles, proprietary source excerpts, and credentials out of tracked
source.

## Commit & Pull Request Guidelines

History favors short, imperative summaries, sometimes with a Conventional Commit
prefix (for example, `feat: Add initial research document`). Keep each commit to
one coherent change. Pull requests should state the problem, summarize the
approach, list validation performed, and link the relevant issue or research
source. Include screenshots only when rendered output changes materially.

## Security & Agent Guidance

Do not commit credentials or proprietary source excerpts. Prefer revision-pinned
references and sanitized examples. If a `.codegraph/` index exists, use
CodeGraph exploration before text search when locating or tracing code; indexing
remains an explicit repository-owner decision.
