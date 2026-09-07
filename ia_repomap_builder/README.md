# `ia_repomap`

This folder is the smallest implementation of the Intacct repository-context
evaluation contract. It compares a deterministic lexical baseline with
optional Aider RepoMap and Ripwire engines. The optional engines are never
silently substituted: an absent installation is returned as
`status: unavailable`.

## Input and output

```python
from pathlib import Path
from ia_repomap_builder import BuildRequest, build

result = build(BuildRequest(
    repo_root=Path("/path/to/ia-app"),
    scope=("app/source",),
    query="GLSetupManager preferences",
    engine="lexical",
    token_budget=4000,
))
print(result.as_dict())
```

The request is revision-aware and scope-relative. The result contains ranked
items, rendered context, diagnostics, runtime metrics, and a cache identity.
Generated context is intentionally not written into the repository.

For a participating Intacct repository, copy the small
`templates/.ia-repomap.toml` declaration and the
`templates/AGENTS.md.snippet` section. The declaration is a discoverable
machine-readable marker; the Markdown is guidance for agents that honor
repository instructions. Neither file is a generated map.

## Optional engines

- `engine="aider"` requires a pinned Aider installation. Intacct PHP-family
  extensions are mapped only for the current process; this is an adaptation,
  not a claim about upstream Aider support.
- `engine="ripwire"` requires `RIPWIRE_BIN` or a `ripwire` executable on
  `PATH`. The binary must contain the Intacct extension aliases; the current
  `.ripwire_config` format cannot provide them. The adapter restricts Ripwire
  scopes to `app/source`, because the binary cannot exclude third-party `.map`
  files in a broader directory.

The release-pinned alias patch used by the evaluation is recorded at
`patches/ripwire-v0.4.0-intacct-php-aliases.patch`. It maps the configured
Intacct PHP-family suffixes to Ripwire's existing PHP grammar; it does not add
a parser. `.map` is included only under `app/source`; third-party source maps
under `app/resources` remain excluded.

The live `ia-app` smoke test is deliberately opt-in through `IA_APP_REPO` and
must not be used to commit source excerpts or generated maps.

For the bakeoff, keep known-answer labels outside the source tree and run one
report per engine:

```python
from pathlib import Path
from ia_repomap_builder import evaluate, load_tasks

tasks = load_tasks(Path("/secure/ia-repomap-tasks.json"))
report = evaluate(Path("/path/to/ia-app"), tasks, "ripwire")
print(report["strict_file_recall_at_10"], report["mrr"])
```

Compare `lexical`, `aider`, and `ripwire` reports on the same task file. An
`unavailable` or `error` status is retained in the report and is not treated
as a successful zero-score result.

## Tests

```shell
python3 -m unittest discover -s tests -p 'test_*.py'
```
