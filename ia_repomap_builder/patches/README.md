# Building Ripwire for Intacct PHP support

This project requires a Ripwire binary built from source at the exact tag
`v0.4.0`, patched with `ripwire-v0.4.0-intacct-php-aliases.patch` in this
directory. The pinned prebuilt release binaries **cannot** be used: they do
not carry the Intacct PHP-family extension aliases (`.cls`, `.inc`, `.map`,
etc.), and there is no runtime config to add them (see
`ia_repomap_builder/README.md`, "Optional engines").

## Prerequisites (macOS)

- CMake 3.24+ (`brew install cmake`)
- A C++23 compiler — Apple Clang (Xcode Command Line Tools) is sufficient
- No network access is needed for the build itself; all grammar/tree-sitter
  dependencies are vendored in-tree

## 1. Clone Ripwire

```bash
git clone https://github.com/redhat-et/ripwire.git ~/src/ripwire
cd ~/src/ripwire
```

## 2. Check out the pinned tag

The patch's diff context is line-pinned to `v0.4.0`. A newer tag (e.g.
`v0.5.0`, which adds Elixir support and shifts `kLangTable`'s row order) will
**not** apply cleanly — see the note at the end of this document.

```bash
git checkout v0.4.0
```

## 3. Apply the Intacct alias patch

```bash
PATCH_FILE=/path/to/intacct-repo-intelligence/ia_repomap_builder/patches/ripwire-v0.4.0-intacct-php-aliases.patch
git apply --check "$PATCH_FILE"   # dry run
git apply "$PATCH_FILE"
```

This patch touches four files and adds twelve Intacct PHP-family extension
aliases (`.cls`, `.ent`, `.inc`, `.cqry`, `.rpt`, `.menu`, `.pol`, `.wfl`,
`.shortcuts`, `.qry`, `.bin`, `.map`) that map to the existing PHP grammar —
it does not add a new parser:

- `src/ingest_crawl.h` — extends `kLangTable` (40 → 52 rows)
- `src/lintrules.h` — extends the lint extension table (30 → 43 rows)
- `src/quality.h`, `src/ingest_cache.h` — parser-version bookkeeping

## 4. Build

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j
```

The resulting binary is `build/ripwire`.

## 5. Verify the build

```bash
./build/ripwire --version
./build/ripwire /path/to/some/repo --doctor
```

`--doctor` should report all checks passing. Note: its `grammars` row stays
`loaded="21" expected="21"` — unchanged — because the patch adds extension
*aliases* onto the existing PHP grammar rather than a new grammar. `--doctor`
does not itself confirm the aliases are compiled in.

To confirm the Intacct aliases are present, point the binary at a directory
containing an Intacct-suffixed file (e.g. `.cls`, `.inc`, `.map`) and check
it is parsed as PHP rather than skipped as unsupported:

```bash
./build/ripwire /path/to/ia-app/app/source --skipped
```

A `.cls`/`.inc`/`.map` file should **not** appear under the unsupported-
extension rows; if it does, the patch was not applied before building.

## 6. Make it available on the CLI

Two things consume this binary, and each wants a different mechanism:

- **This project's Python tooling** (`ia_repomap_builder`) reads the
  `RIPWIRE_BIN` env var first, via `_ripwire_binary()` in `engines.py`.
- **Direct CLI use** (`ripwire . --for=...`, `ripwire wrap claude`, etc.)
  needs a plain `ripwire` command resolvable on `$PATH`.

Set both by adding the build directory to `$PATH` and pointing `RIPWIRE_BIN`
at the same binary:

```bash
cat >> ~/.zshrc <<'EOF'
export PATH="$HOME/src/ripwire/build:$PATH"
export RIPWIRE_BIN="$HOME/src/ripwire/build/ripwire"
EOF
source ~/.zshrc
```

Confirm both resolve:

```bash
which ripwire            # should print .../build/ripwire
ripwire --version
echo "$RIPWIRE_BIN"
"$RIPWIRE_BIN" --version
```

Alternatively, symlink the binary into an existing `$PATH` directory (e.g.
`~/.local/bin`) instead of prepending the build folder itself:

```bash
ln -sf "$HOME/src/ripwire/build/ripwire" ~/.local/bin/ripwire
```

## Upgrading beyond v0.4.0

Ripwire's upstream `main`/`v0.5.0` adds Elixir (`.ex`/`.exs`) rows to
`kLangTable` immediately after the `.phtml` entry — exactly where this
patch's hunks are anchored — so `git apply` will reject the
`ingest_crawl.h` hunk on a newer tag. Upgrading requires hand-porting the same
alias rows onto the new file layout (adjusting the table's declared size
accordingly), producing a new patch file (e.g.
`ripwire-v0.5.0-intacct-php-aliases.patch`), and updating this document and
`ia_repomap_builder/README.md` to reference it. Do this deliberately — it is
not a drop-in `git apply` after a `git checkout` version bump.

## Language and format coverage (as of v0.4.0)

Full call-graph indexing: C++, C, Objective-C/C++, Metal, CUDA, Python, Go,
Rust, Swift, TypeScript, JavaScript, Java, Ruby, PHP (incl. the Intacct
aliases above), Lua, Bash, C#.

Structural, non-code indexing (config keys, zero call edges): JSON, TOML,
YAML — a YAML file's mapping keys become symbols (sequence-nested keys
included), but anchors/aliases/`<<:` merge keys are not expanded and block
scalars mint no symbols.

An OpenAPI spec authored in YAML or JSON gets only that generic shallow
key-indexing — no awareness of `$ref`, path templates, or operationIds as
callable symbols, and no edges.

Not supported at all: XML and anything XML-based (XAML, `.csproj`, SVG,
etc.) — no grammar exists, so such files are skipped as unsupported
extensions rather than indexed as opaque data.
