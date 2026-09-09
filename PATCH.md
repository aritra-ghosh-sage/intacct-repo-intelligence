# Intacct Ripwire patch

This repository carries a reproducible adaptation of Ripwire v0.4.0 for the
Intacct repository-map and PR-context paths. The patch is
`ia_repomap_builder/patches/ripwire-v0.4.0-intacct-repomap.patch`.

## upstream_commit

The patch was cut from the exact Ripwire tag revision:

```text
e5d6205c11995ff27587fff5895ebd5a5255140f
```

This is the `v0.4.0` commit. Apply the patch only to that revision unless it
has been deliberately rebased and its identity regenerated.

## changed_files

The patch changes these Ripwire files:

- `src/cli.h`
- `src/gitmine.h`
- `src/ingest_cache.h`
- `src/ingest_crawl.h`
- `src/lintrules.h`
- `src/prcontext.h`
- `src/quality.h`
- `src/verbs_change.h`
- `test/intacctaliasescheck.sh`
- `test/prhistoryboundcheck.sh`
- `test/regression.sh`
- the Ripwire gate-count references in `README.md`, `docs/EVALS.md`, and
  `present/deck5_ripwire_build.js`

The source changes add Intacct extension aliases and the bounded PR-history
option. The test changes make those capabilities part of the Ripwire
regression surface.

## why

Upstream Ripwire v0.4.0 routes `.php` and `.phtml` through the PHP grammar but
does not know Intacct's PHP-family source suffixes. The patch routes `.cls`,
`.ent`, `.inc`, `.cqry`, `.rpt`, `.menu`, `.pol`, `.wfl`, `.shortcuts`, `.qry`,
`.bin`, and `.map` through the existing PHP grammar. `.map` is accepted only
under the configured `app/source` scope because other repository locations can
contain JavaScript or CSS source maps.

The patch also adds `--pr-history-commits=N`, bounding co-change and ownership
mining to the latest `N` commits from `HEAD`. This keeps PR-context retrieval
within a predictable runtime and discloses the limitation in the XML evidence.
It does not add a parser or change the static symbol/call-graph model.

## verify

From a clean Ripwire v0.4.0 checkout, apply and build the patch:

```shell
PATCH="$PWD/../intacct-repo-intelligence/ia_repomap_builder/patches/ripwire-v0.4.0-intacct-repomap.patch"
git rev-parse HEAD
git apply --check "$PATCH"
git apply "$PATCH"
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j
```

Run the focused tests:

```shell
bash test/intacctaliasescheck.sh build/ripwire
bash test/prhistoryboundcheck.sh build/ripwire
```

`intacctaliasescheck.sh` creates one fixture for every configured suffix —
`.php`, `.phtml`, `.cls`, `.ent`, `.inc`, `.cqry`, `.rpt`, `.menu`, `.pol`,
`.wfl`, `.shortcuts`, `.qry`, `.bin`, and `.map` — and verifies that each is
parsed as PHP rather than skipped as unsupported. `prhistoryboundcheck.sh`
verifies the bounded option, XML disclosure, deterministic output, invalid
value rejection, and the option's `--pr-context` guard.

Also confirm the capability is visible to the adapter:

```shell
build/ripwire --help | rg -- '--pr-history-commits='
git diff --check
git apply --reverse --check "$PATCH"
```

## upgrade_process

When Ripwire releases a new version:

1. Record the new upstream tag and its full commit SHA in a clean checkout.
2. Replay the extension rows, parser/cache version bookkeeping, and bounded
   PR-history changes onto the new source layout.
3. Update the focused tests and regression manifest, including any documented
   gate counts required by the new checkout.
4. Build the patched binary and run the extension, history-bound, CLI, XML,
   legend, and manifest checks.
5. Prove the new patch applies and reverse-applies against the exact upstream
   SHA; regenerate the tracked patch artifact rather than editing it by hand.
6. Capture the new patch and executable digests. Treat the resulting engine
   identity as new: existing revision-bound artifacts must not be reused.
7. Update this document, the patch README, and the repository-map contract
   with the new version, SHA, capabilities, and validation evidence.

## owner

Intacct Repo Intelligence / Ripwire integration maintainer.

The owner is responsible for reviewing upstream changes, carrying the patch
forward, rebuilding the binary, refreshing its identity, and preserving the
extension and bounded-history regression coverage.
