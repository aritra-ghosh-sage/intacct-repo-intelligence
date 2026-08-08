# Intacct Repository Intelligence: Project Purpose

## Status

This document captures the product purpose and target outcome of Intacct
Repository Intelligence. It is intentionally broader than the current
`repo-v1` implementation plan. The repo-v1 design documents define the current
implementation boundary; this document defines why the project exists and what
it should eventually enable.

## Problem

Intacct functionality is distributed across many repositories, technologies,
and test systems. Repositories may be related directly or indirectly:

- `ia-app` contains core product behavior, entities, APIs, UI, configuration,
  permissions, and database-facing implementation.
- `ia-gwdata-*` repositories provide Gateway integration and regression
  scenarios.
- `ia-restapi-automation-tests` provides REST API behavior and regression
  scenarios.
- `ia-test-automation` provides broader UI and API automation capabilities.
- Other data, configuration, service, and domain repositories may participate
  in the same user-facing behavior.

A change in one repository can therefore affect implementation, API contracts,
permissions, UI behavior, integrations, and tests in other repositories.
Today, much of this relationship is implicit, tribal knowledge or expensive
for an engineer or AI agent to rediscover. Agentic systems such as GHCP,
Claude Code, and Codex do not automatically have a reliable, shared view of
these relationships.

## Primary objective

Build an evidence-backed repository intelligence layer that makes the
functional and technical structure of Intacct understandable to humans and
agentic systems.

The system should reduce repeated codebase discovery while preserving a clear
boundary between:

- facts proven by source, Git, SQLite, APIs, configuration, or tests;
- relationships explicitly supplied and reviewed by humans;
- analysis inferred from those facts; and
- information that is missing, stale, ambiguous, or unsupported.

## Target users

- Developers building features or fixing defects.
- QA engineers assessing regression risk and test adequacy.
- Product managers evaluating technical and functional feasibility.
- Agentic systems assisting any of these users.

## Core capabilities

### 1. Repository topology and integration contracts

Humans define repository relationships through manifests and reviewed mapping
registries. These contracts should identify relationships such as:

- API behavior tested by another repository;
- Gateway behavior tested by another repository;
- UI behavior automated by another repository;
- configuration or schema dependency;
- generated or consumed artifact;
- source repository and downstream test obligation.

Relationships must include evidence paths, owners, triggers, status, and
freshness expectations. A generic `depends_on` field is useful for build order
but is not sufficient to describe test or behavioral obligations.

### 2. Deterministic intelligence

The catalog should represent source-backed facts from each onboarded
repository, including:

- repository identity, revision, and provenance;
- files, symbols, and relationships;
- business entities and entity occurrences;
- APIs, OpenAPI documents, and REST endpoints;
- database tables, fields, indexes, relationships, and migrations where
  extractable;
- permissions, menus, and security policy;
- UI surfaces, fields, events, includes, and NextGen artifacts;
- workflows and configuration;
- unit, integration, REST, Gateway, UI, and API test evidence;
- diagnostics and extraction coverage.

Facts must remain repository-scoped and source-traceable. Same names,
filenames, directory conventions, or semantic similarity must not silently
create cross-repository links.

### 3. Agent-facing understanding and navigation

Agentic systems should be able to ask focused, structured questions instead of
repeatedly searching entire repositories. The query surfaces should expose:

- entity and domain context;
- symbol and file references;
- API and UI surfaces;
- security and workflow context;
- repository relationships;
- test coverage;
- PR and file impact;
- provenance, freshness, and diagnostic status.

Responses must be self-describing and must distinguish an empty result from
unavailable, stale, ambiguous, or unresolved evidence.

### 4. Developer and QA assistance

For a PR or proposed code change, the system should help identify:

- affected entities, symbols, APIs, UI surfaces, permissions, and data;
- affected database tables and fields, including declarations in sources such
  as `app/source/common/dbschema.inc`;
- database consumers such as entity mappings, queries, persistence logic,
  APIs, UI fields, permissions, migrations, and test data;
- related repositories and the reason for each relationship;
- existing unit, integration, REST, Gateway, UI, and API tests;
- missing, weak, stale, or unresolved coverage;
- likely regression scenarios and test obligations;
- source files and evidence that an agent should inspect next.

The system may propose use cases and tests, but generated changes require
normal engineering and QA review. A coverage gap is not proof that behavior is
untested unless the relevant repository and coverage contract are current and
available.

### 5. Product-manager feasibility and requirements assistance

The system should help a product manager assess whether a proposed feature is
functionally and technically supported by the existing codebase.

For a feature idea, it should inspect the available evidence for:

- existing business entities and domain behavior;
- current user workflows and operations;
- UI surfaces and NextGen components;
- REST and other API contracts;
- database tables, views, fields, and migrations;
- permissions, menus, policies, and security constraints;
- feature flags and configuration;
- related repositories and integration contracts;
- existing tests and expected regression coverage.

The result should classify each important conclusion, for example:

- supported by existing behavior;
- partially supported and extensible;
- technically feasible but high-impact;
- not observed in the indexed evidence;
- blocked by a missing dependency, mapping, or owner decision;
- unknown because the relevant repository or evidence is unavailable or stale.

The system should then be able to draft an evidence-backed feature
requirements document containing:

- problem statement and intended user outcome;
- current supported behavior;
- proposed behavior and affected domains;
- functional requirements;
- UI, API, data, permission, configuration, and feature-flag requirements;
- impacted repositories and integration obligations;
- reuse and extension points;
- risks, constraints, dependencies, and open questions;
- existing and required tests;
- source evidence and confidence for major claims.

This is decision support, not autonomous product approval. The system should
not decide customer priority, product desirability, UX acceptance, business
policy, or release approval.

## Canonical PR and feature workflows

### PR impact and test-gap workflow

```text
PR or changed files
    -> resolve repository and source revision
    -> identify symbols, entities, APIs, UI, permissions, and data
    -> apply reviewed cross-repository integration contracts
    -> find existing tests and coverage
    -> report impact, obligations, gaps, diagnostics, and confidence
    -> propose tests or use cases for human review
```

### PM feasibility and requirements workflow

```text
Feature idea
    -> identify business concepts and desired behavior
    -> inspect current functional and technical evidence
    -> inspect UI, API, data, permission, configuration, and test surfaces
    -> identify reuse, extension points, dependencies, and gaps
    -> classify feasibility and uncertainty
    -> draft an evidence-backed feature requirements document
    -> collect human product, engineering, and QA decisions
```

## Evidence and trust rules

- Read source facts from a known committed revision whenever possible.
- Preserve repository, build, commit, file, symbol, and source-location
  provenance.
- Keep logical identity separate from repository-local occurrences.
- Use explicit typed mappings for cross-repository relationships.
- Preserve unresolved, dynamic, ambiguous, cyclic, unsupported, and stale
  states as visible diagnostics.
- Never merge same-named entities or infer a test obligation from a filename
  alone.
- Never treat an unavailable catalog as evidence that a capability does not
  exist.
- Report confidence and evidence scope with every material conclusion.

## Current state and boundary

The current `repo-v1` implementation is an evidence substrate, not yet the
complete multi-repository product described above. Its accepted direction is a
deterministic, immutable, Git-backed SQLite build with source provenance,
candidate validation, and atomic promotion. It currently provides substantial
`ia-main` inventory and source-backed extraction, with later slices for API and
UI evidence.

The following remain broader product work or explicit boundaries rather than
assumptions:

- expanding repository selection beyond the initial `ia-main` focus;
- onboarding and refreshing external automation repositories;
- typed cross-repository behavioral links and reviewed mappings;
- complete workflow and security evidence;
- UI-to-entity and dynamic handler resolution;
- test semantic coverage across Gateway, REST, UI, and API systems;
- PR impact orchestration across multiple repositories;
- PM feasibility and requirements-document generation;
- freshness, ownership, and evidence contracts for every repository.

The implementation should preserve KISS/YAGNI: add each capability as a
narrow, evidence-backed vertical slice with focused validation rather than
building speculative generic infrastructure.

## First practical product slice

Start with one bounded workflow across:

- `ia-app`;
- `ia-restapi-automation-tests`;
- `ia-gwdata-gl`.

Given an `ia-app` PR that changes an entity, REST schema, endpoint, or related
implementation, database table, or database field, produce a machine-readable
report that identifies:

- affected concepts and source evidence;
- affected database tables and fields, plus their proven consumers;
- linked external test repositories and relationship reasons;
- existing tests and coverage;
- missing, stale, or unresolved mappings;
- required human decisions;
- confidence and limitations.

Add `ia-test-automation` after its repository ownership and integration
contracts are explicitly onboarded.

## Fallback when evidence is incomplete

Incomplete automation is an expected state, not a reason to invent answers.
When a relationship or capability cannot be proven, the system should:

1. retain the unresolved state and exact evidence examined;
2. identify the missing repository, mapping, or contract;
3. provide bounded candidate evidence for human review;
4. allow an approved mapping or contract to be added;
5. rerun the same analysis deterministically.

This gives agents a trustworthy bounded answer while allowing the intelligence
layer to improve incrementally.
