# `ia_repomap`

This folder is the smallest implementation of the Intacct repository-context
evaluation contract. It compares a deterministic lexical baseline with
optional Aider RepoMap and Ripwire engines. The optional engines are never
silently substituted: an absent installation is returned as
`status: unavailable`.

The canonical repository-map contract, including configuration, artifact
identity, PR-context output, assumptions, and acceptance criteria, is in
[`docs/design/ia-repomap-context-contract.md`](../docs/design/ia-repomap-context-contract.md).
Its [execution status](../docs/design/ia-repomap-context-contract.md#execution-status)
records the completed implementation slices and their validation evidence.

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

## Repository declaration and readiness

Participating repositories commit this root declaration:

```toml
schema_version = 1
engine = "ripwire"
scope = ["app/source"]
token_budget = 4000
php_family_extensions = [
  ".php", ".phtml", ".cls", ".ent", ".inc", ".cqry", ".rpt",
  ".menu", ".pol", ".wfl", ".shortcuts", ".qry", ".bin", ".map",
]
map_php_scope = ["app/source"]
```

The declaration is only a discovery marker. Before PR-context analysis, a
clean exact-head checkout must have a matching Ripwire index and manifest in a
caller-provided artifact root outside the repository. Missing or stale map
context is reported as `unavailable`; the adapter does not silently cold-build
an index. Readiness uses Ripwire `--doctor` to reject a named lean cache that
the current binary would replace with a cold parse. See the canonical contract
for the manifest and normalized result formats.

The Python-only readiness and PR-context APIs are:

```python
from pathlib import Path
from ia_repomap_builder import (
    PrepareRepoMapRequest,
    PrContextRequest,
    build_pr_context,
    prepare_repomap,
)

repo = Path("/path/to/ia-app")
artifacts = Path("/safe/external/ia-repomap-artifacts")
prepare_repomap(PrepareRepoMapRequest(repo, artifacts))
result = build_pr_context(PrContextRequest(repo, artifacts, base_ref="origin/main"))
```

`prepare_repomap` requires a valid root marker and a clean checkout. It writes
only external artifacts. `build_pr_context` requires that exact prepared
artifact and returns `ok`, `unavailable`, or `error` without falling back to a
cold index build.

The current implementation status is recorded in the canonical contract. A
local `ia-app` onboarding commit prepares the marker and agent guidance, but it
is not live until reviewed and merged; an external exact-revision index is still
required. MCP, CLI, editor, and harness integrations remain deferred.

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
under `app/resources` remain excluded. See
[`patches/README.md`](patches/README.md) for the full build-from-source,
patch-application, and CLI setup instructions.

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
./.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```
