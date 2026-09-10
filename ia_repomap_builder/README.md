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
the current binary would replace with a cold parse. Readiness gates on the
doctor report's named `index-cache` row (`source="cache-flag"` and
`lean="ok"`); unrelated doctor failures remain visible as warnings rather
than invalidating an otherwise consumable named cache. See the canonical
contract for the manifest and normalized result formats.

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

Normalized changed-symbol seeds use `hunk-enclosing-v1`: Git's positive-side
hunks are attributed to spans inferred from Ripwire definition start lines.
The filtered definitions remain `candidate` evidence. Git hunk failures retain
file-wide candidates with an explicit gap; deletion-only changes, unattributed
hunks, and missing symbol lines are also disclosed rather than silently
treated as complete evidence. Canonical Ripwire XML is retained verbatim.

The adapter also exposes `direct-callers-v1` evidence for callers nested under
hunk-selected symbols. These one-hop relationships remain `candidate` evidence;
caller caps, malformed locations, and out-of-scope rows are reported as gaps.
Aggregate impact, affected tests, owners, and co-change data remain in the raw
XML and are not normalized as complete blast-radius results.

The current implementation status is recorded in the canonical contract. A
local `ia-app` onboarding commit prepares the marker and agent guidance, but it
is not live until reviewed and merged. Hunk attribution has been validated on
an isolated, exact-revision PR #50176 checkout with an external index; it is not
generally available. A local Python module interface is available for explicit
preparation and PR-context retrieval; MCP, editor, and harness integrations
remain deferred.

For a developer or coding harness, use the module interface:

Run these commands from the builder repository root (or add this repository to
`PYTHONPATH`); the project does not install a console entry point.

```shell
./.venv/bin/python -m ia_repomap_builder prepare \
  --repo /path/to/ia-app \
  --artifact-root /safe/external/ia-repomap-artifacts

./.venv/bin/python -m ia_repomap_builder pr-context \
  --repo /path/to/ia-app \
  --artifact-root /safe/external/ia-repomap-artifacts \
  --base origin/main \
  --output /safe/external/pr-context.json
```

Both commands emit one JSON result to stdout. `--output` is optional and, when
provided, receives the same JSON outside the target checkout. `pr-context`
checks readiness and never prepares an index implicitly. Exit code `0` means
`ok`, `3` means `unavailable` with remediation, and `2` means invalid input or
an execution error. The checked-out clean `HEAD` is the PR head; provide its
base as a local Git ref with `--base`. A GitHub PR number is not accepted or
resolved by this local interface, so callers must create or select the desired
PR checkout first. For example, a PR #50176 test must use a checkout of its
exact head and the intended base commit; the number is descriptive metadata,
not a command argument.

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

The release-pinned consolidated patch used by the evaluation is recorded at
`patches/ripwire-v0.4.0-intacct-repomap.patch`. It maps the configured
Intacct PHP-family suffixes to Ripwire's existing PHP grammar and adds the
bounded `--pr-history-commits` capability; it does not add a parser. `.map` is
included only under `app/source`; third-party source maps under `app/resources`
remain excluded. See
[`patches/README.md`](patches/README.md) for the full build-from-source,
patch-application, and CLI setup instructions.

The live `ia-app` smoke tests are deliberately opt-in. The repository scope
smoke requires `IA_APP_REPO`; the Ripwire scope smoke additionally requires
`RIPWIRE_BIN`; and the PR-context smoke requires all of
`IA_APP_REPO`, `IA_REPOMAP_ARTIFACT_ROOT`, `IA_APP_BASE_REF`, and
`RIPWIRE_BIN`. These checks are read-only and must not be used to commit
source excerpts or generated maps.

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
