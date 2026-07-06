# Intacct Repository Intelligence

## Overview

Intacct Repository Intelligence is a code intelligence and knowledge graph platform for the Sage Intacct codebase.

The project transforms source code, metadata, configuration assets, and extracted relationships into a structured, queryable representation of the system.

Its purpose is to help engineers and AI systems understand:

- Business entities
- Code structure
- Cross-module dependencies
- APIs
- Configuration artifacts
- Workflows
- Impact relationships

without relying on tribal knowledge or manual repository exploration.

The repository is designed to provide an evidence-based understanding of the Intacct codebase. Every fact in the graph should be traceable back to source artifacts.

---

## Why This Exists

The Intacct codebase contains a large amount of business logic spread across multiple technologies and repositories.

Examples include:

- PHP
- JavaScript
- SQL
- XML
- XSLT
- OpenAPI specifications
- YAML configurations
- Entity definition files
- Domain service repositories

Understanding a business concept such as:

- APBill
- Vendor
- Customer
- GLBatch

typically requires navigating many files, modules, and technologies.

Traditional approaches depend on:

- tribal knowledge
- manual searches
- knowledge transfer
- historical context

These approaches do not scale.

This project creates a machine-readable representation of the codebase that can be queried by both humans and AI systems.

---

## Repository Goals

The project has three goals, in priority order.

### Goal 1: Build a Searchable Code Catalog

Create a complete inventory of repository assets.

Examples:

- files
- symbols
- relationships
- metadata
- entity mappings

---

### Goal 2: Build a Knowledge Graph

Transform extracted information into a graph containing:

- business entities
- code symbols
- dependencies
- workflows
- integrations
- APIs
- configuration artifacts

The graph becomes the primary navigation layer over the codebase.

---

### Goal 3: Enable AI-Assisted Understanding

Allow AI systems to answer questions such as:

- What impacts APBill?
- Where is Vendor approval implemented?
- Which APIs expose this entity?
- What modules consume this workflow?
- What breaks if this field changes?

using evidence instead of assumptions.

---

## Core Philosophy

### Evidence First

Every node, edge, mapping, or relationship should answer:

> Where did this come from?

If provenance cannot be established, the graph should not contain it.

---

### Deterministic Over Heuristic

Prefer extraction techniques that produce repeatable results.

Avoid relying solely on assumptions, naming conventions, or inferred file locations.

---

### Precision Before Recall

A missing relationship is preferable to an incorrect relationship.

The graph should prioritize correctness over completeness.

Recall can be improved incrementally.

Incorrect data damages trust.

---

## Sources of Truth

The graph is assembled from multiple verified sources.

No single source is authoritative for the entire system.

Current sources include:

- Entity definition files (`*.ent`)
- PHP source code
- JavaScript source code
- SQL
- XML
- XSLT
- OpenAPI specifications
- YAML configuration files
- Extracted code relationships

Entities may derive information from multiple sources simultaneously.

Contributors should avoid assuming that any single artifact fully describes an entity.

---

## High-Level Architecture

```text
Repository Sources
        │
        ▼
 File Discovery
        │
        ▼
   File Catalog
        │
        ▼
Symbol Extraction
        │
        ▼
Relationship Extraction
        │
        ▼
 Entity Construction
        │
        ▼
 Configuration Mapping
        │
        ▼
 Knowledge Graph
        │
        ▼
 Query Layer
        │
        ▼
 Human & AI Consumers