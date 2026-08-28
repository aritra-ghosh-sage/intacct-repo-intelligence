# Greenfield Repository Behavior Handbook

## Purpose

The repository behavior handbook is a revision-bound L1/L2/L3 navigation
package used by Strands before and during PR analysis. It organizes source by
runtime behavior rather than by file and gives the agent progressively disclosed
routes to current implementation locations.

The handbook guides discovery; it does not replace source inspection. Strands
must verify candidate locations against the captured repository revision before
using them as confirmed or strong-candidate evidence.

This follows the Harness Handbook pattern:

- L1 is a compact system and behavior-stage index.
- L2 lists behavior components and their relationships.
- L3 contains source-backed behavior entries with paths, optional lines,
  symbols, excerpts, and source hashes.

## Contract

The machine-readable artifact uses:

```text
schema_version: "0.1"
artifact_kind: "greenfield_repository_behavior_handbook"
repository: <repository identity>
revision: <lowercase 40-character Git SHA>
leaf_mode: "behavior"
sections:
  index: <L1>
  behaviors: <L2>
  behavior:<id>: <L3>
```

Every L3 locator contains the repository-relative path, captured revision,
source SHA-256, current excerpt, and excerpt line range. A line locator is
accepted only when it resolves inside the source blob at that revision.

The initial builder consumes a generated behavior contract and revision-bound
Git source:

```bash
PYTHONPATH=. ./.venv/bin/python \
  scripts/build_greenfield_repository_handbook.py \
  --contract <step1.5.contract.json> \
  --source-root <repository-checkout> \
  --output <repository-handbook.json>
```

## Strands Navigation

The captured `run-context.json` identifies available repository handbooks and
their fingerprints. The Strands `read_handbook` tool exposes one section at a
time:

1. Read `index` to select relevant behaviors.
2. Read `behaviors` to narrow the implementation area.
3. Read only the selected `behavior:<id>` L3 entries.
4. Open the real source through `read_source` or `codegraph_explore`.
5. Cite the resulting tool call when making a confirmed or strong-candidate
   claim.

Missing, stale, or unresolvable handbook entries remain explicit unavailable
evidence. The agent may continue with other approved repository tools, but it
must not present an unavailable handbook as proof of no impact.

## Resynchronization

Every non-empty repository diff triggers handbook resynchronization. The current
implementation rebuilds the affected behavior projection, records the previous
handbook fingerprint and sorted changed paths, and verifies all resulting
locators against the new revision. An empty diff reuses the existing handbook.

Production storage, retention, and asynchronous resynchronization workers remain
infrastructure decisions. The artifact contract is independent of that storage
choice.

## Behavior Impact Report

The per-PR artifact previously called `behavior-handbook.json` is not a
repository handbook. It is now:

- `behavior-impact-report.json`
- `behavior-impact-report.md`
- `analysis_kind: "greenfield_behavior_impact_report"`

This report is built after compatibility Steps 2-5. It joins the current PR's
behavior, impact, coverage, action, gap, and provenance evidence for review and
replay. It cannot route the earlier analysis and cannot authorize writes.

The standalone compatibility renderer is:

```bash
PYTHONPATH=. ./.venv/bin/python scripts/render_greenfield_behavior_impact.py \
  --contract <bundle>/step1.5.contract.json \
  --step2 <bundle>/step2.json \
  --step3 <bundle>/step3.json \
  --step4 <bundle>/step4.json \
  --step5 <bundle>/step5.json \
  --output-json <bundle>/behavior-impact-report.json \
  --output-markdown <bundle>/behavior-impact-report.md
```
