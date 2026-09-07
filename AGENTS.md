# Repository Guidelines

## Project Structure & Module Organization

This branch is a documentation-focused research workspace. Design notes live in
`docs/design/`; the current primary artifact is
`docs/design/llm-agent-pr-blast-radius-research.md`. Keep new research documents
in that directory and use descriptive, kebab-case filenames. The repository does
not currently contain application source, generated assets, or a test suite. If
code is introduced, add an obvious top-level source directory and mirror its
layout under `tests/`.

## Development and Validation Commands

There is no build system or automated test command on this branch. Use lightweight
checks from the repository root:

- `git diff --check` detects whitespace errors before a commit.
- `rg -n '^#{1,6} ' docs/` reviews heading structure across the documentation.
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
When executable code is added, include automated tests in the same change and
document the exact command required to run them.

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
