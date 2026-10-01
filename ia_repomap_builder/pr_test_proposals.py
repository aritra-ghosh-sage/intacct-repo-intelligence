"""Post-analysis, evidence-bound API test proposals for an external test repo.

This module intentionally has no connection to the PR-analysis coordinator. It
consumes its persisted report and inventory, then makes one bounded model call.
Generated material is a draft and always has ``execution_status=not_run``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .identity import git_revision, is_dirty
from .pr_analysis import MAX_AGENT_RESPONSE_TOKENS, PRAnalysisReportV1, load_bedrock_settings
from .test_inventory import SCHEMA as INVENTORY_SCHEMA
from .test_inventory import _suite_dict, TestInventory

MAX_FILE_BYTES = 80_000
MAX_CONTEXT_CHARS = 100_000
MAX_FEATURE_EXAMPLES = 8
MAX_FIXTURE_EXAMPLES = 8
MAX_PROPOSALS = 20
PROMPT_VERSION = "pr-test-proposals-v1"
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_JIRA_RE = re.compile(r"^IA-[0-9]+$")
_METHODS = {"get", "post", "put", "patch", "delete"}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProposalDraft(_Strict):
    title: str = Field(min_length=1, max_length=160)
    method: str = Field(min_length=3, max_length=8)
    route: str = Field(min_length=1, max_length=300)
    changed_field: str = Field(min_length=1, max_length=200)
    expected_behavior: str = Field(min_length=1, max_length=500)
    prerequisites: list[str] = Field(default_factory=list, max_length=20)
    gaps: list[str] = Field(default_factory=list, max_length=20)
    evidence_paths: list[str] = Field(default_factory=list, max_length=20)
    feature_path: str = Field(min_length=1, max_length=300)
    scenario_name: str = Field(min_length=1, max_length=160)
    tag: Literal["sanity", "regression"] | None = None
    test_repo_object_alias: str | None = Field(default=None, max_length=160)
    test_repo_uri: str | None = Field(default=None, max_length=300)
    response_status: int | None = Field(default=None, ge=100, le=599)
    request_case: str | None = Field(default=None, max_length=80)
    expected_case: str | None = Field(default=None, max_length=80)
    request_fixture: dict[str, Any] | None = None
    expected_fixture: dict[str, Any] | None = None


class _ProposalDrafts(_Strict):
    proposals: list[ProposalDraft] = Field(default_factory=list, max_length=MAX_PROPOSALS)


def _json(path: Path, *, limit: int = MAX_FILE_BYTES) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    if len(raw) > limit:
        raise ValueError(f"input exceeds {limit} bytes: {path.name}")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path.name}")
    return value, raw


def _safe_relative(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or not value or ".." in path.parts or "\\" in value:
        raise ValueError(f"unsafe repository-relative path: {value!r}")
    return path


def _read_checkout_file(root: Path, relative: str) -> str:
    rel = _safe_relative(relative)
    path = (root / rel).resolve(strict=True)
    if root.resolve() not in path.parents or not path.is_file():
        raise ValueError(f"repository file escapes checkout or is not regular: {relative}")
    data = path.read_bytes()
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"repository file exceeds {MAX_FILE_BYTES} bytes: {relative}")
    return data.decode("utf-8")


def _inventory_from_payload(payload: dict[str, Any]) -> TestInventory:
    if payload.get("schema") != INVENTORY_SCHEMA:
        raise ValueError("unsupported target test-inventory schema")
    from . import test_inventory as ti

    try:
        repository = ti.InventoryRepository(**payload["repository"])
        suites = tuple(
            ti.InventorySuite(
                suite_id=s["suite_id"], module=s["module"], category=s["category"],
                feature_files=tuple(s["feature_files"]), input_files=tuple(s["input_files"]),
                output_files=tuple(s["output_files"]),
                scenarios=tuple(ti.InventoryScenario(
                    name=x["name"], tags=tuple(x["tags"]), fixtures=tuple(x["fixtures"]),
                    methods=tuple(x["methods"]),
                ) for x in s["scenarios"]),
                tags=tuple(s["tags"]), api_objects=tuple(s["api_objects"]),
                methods=tuple(s["methods"]),
            ) for s in payload["suites"]
        )
        gaps = tuple(ti.InventoryGap(**x) for x in payload["gaps"])
        return ti.TestInventory(payload["schema"], payload["status"], repository,
                                suites, gaps, dict(payload.get("metrics", {})))
    except (KeyError, TypeError) as exc:
        raise ValueError(f"malformed target test inventory: {exc}") from exc


def _canonical_inventory_digest(inventory: TestInventory) -> str:
    encoded = json.dumps([_suite_dict(s) for s in inventory.suites], ensure_ascii=False,
                         sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_bundle(report_dir: Path, app_repo: Path, test_repo: Path) -> tuple[dict[str, Any], TestInventory, str, str]:
    report_path = report_dir / "pr-analysis.json"
    report, _ = _json(report_path)
    parsed = PRAnalysisReportV1.model_validate(report)
    if parsed.status != "ok":
        raise ValueError("test proposals require a successful saved PR analysis")

    inventory_evidence = [e for e in report["evidence"] if e.get("kind") == "test_inventory"]
    if len(inventory_evidence) != 1 or inventory_evidence[0].get("relative_path") != "evidence/test-inventory.json":
        raise ValueError("report must reference exactly one evidence/test-inventory.json")
    inventory_path = report_dir / "evidence" / "test-inventory.json"
    raw_inventory = inventory_path.read_bytes()
    if hashlib.sha256(raw_inventory).hexdigest() != inventory_evidence[0].get("sha256"):
        raise ValueError("test inventory evidence hash does not match the saved report")
    inventory_payload = json.loads(raw_inventory)
    if not isinstance(inventory_payload, dict):
        raise ValueError("test inventory evidence must be a JSON object")
    inventory = _inventory_from_payload(inventory_payload)
    if inventory.status != "ok":
        raise ValueError("test proposals require a successful persisted test inventory")
    if inventory.repository.dirty is not False:
        raise ValueError("persisted test inventory must record a clean repository")
    expected = (report.get("test_inventory_coverage") or {}).get("inventory_identity") or {}
    ident = inventory.repository
    if ident.head != expected.get("head") or ident.repository_id != expected.get("repository_id"):
        raise ValueError("test inventory identity differs from report coverage identity")
    if ident.inventory_digest != expected.get("inventory_digest") or _canonical_inventory_digest(inventory) != ident.inventory_digest:
        raise ValueError("test inventory canonical digest is invalid")

    app_repo = app_repo.expanduser().resolve(strict=True)
    test_repo = test_repo.expanduser().resolve(strict=True)
    app_head = git_revision(app_repo)
    test_head = git_revision(test_repo)
    if not _SHA_RE.fullmatch(str(report.get("identity", {}).get("head", ""))) or app_head != report["identity"]["head"]:
        raise ValueError("app checkout HEAD does not match the PR analysis revision")
    if is_dirty(app_repo) is not False:
        raise ValueError("app checkout must be clean and have a verifiable Git status")
    from .identity import repository_id
    if repository_id(app_repo) != report.get("identity", {}).get("repository"):
        raise ValueError("app checkout repository identity does not match the saved report")
    if test_repo != Path(ident.root).expanduser().resolve(strict=True):
        raise ValueError("test checkout path does not match the inventory repository root")
    if test_head != ident.head or is_dirty(test_repo) is not False:
        raise ValueError("test checkout must be clean and match the inventory HEAD")
    from . import test_inventory as ti
    if ti._repository_id(test_repo) != ident.repository_id:
        raise ValueError("test checkout repository identity does not match the inventory")
    return report, inventory, hashlib.sha256(report_path.read_bytes()).hexdigest(), hashlib.sha256(raw_inventory).hexdigest()


def _changed_openapi_paths(report: dict[str, Any], app_repo: Path) -> tuple[list[str], set[str], set[str], dict[str, list[int]], dict[str, Any]]:
    changed = {f["path"] for f in report["changed_files"] if f.get("scope") == "in_scope"}
    elements = [e for f in report["changed_files"] if f.get("scope") == "in_scope" for e in f.get("changed_elements", [])]
    schema_paths = {p for p in changed if "/openapispec/" in p and "/models/" in p}
    names = set()
    for element in elements:
        name = element.get("name", "")
        if ".schema." in name:
            name = name.split(".schema.", 1)[1]
        names.add(name.replace(".properties.", "."))
    methods: set[str] = set()
    changed_fields: set[str] = set()
    response_statuses: dict[str, list[int]] = {}
    field_examples: dict[str, Any] = {}
    route_context: list[str] = []
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML is required to inspect OpenAPI evidence") from exc
    api_documents: dict[str, str | None] = {}
    for schema_path in schema_paths:
        stem = Path(schema_path).name.replace(".schema.yaml", ".api.yaml")
        api_path = (Path(schema_path).parent.parent / "paths" / stem).as_posix()
        if (app_repo / api_path).is_file():
            api_documents[api_path] = schema_path
    for changed_path in changed:
        if "/openapispec/" in changed_path and "/paths/" in changed_path and changed_path.endswith(".api.yaml"):
            api_documents.setdefault(changed_path, None)

    for api_rel, schema_path in api_documents.items():
        try:
            api = yaml.safe_load(_read_checkout_file(app_repo, api_rel))
            schema = yaml.safe_load(_read_checkout_file(app_repo, schema_path)) if schema_path else None
        except (yaml.YAMLError, ValueError):
            continue
        relevant_names: set[str] = set()
        if isinstance(schema, dict):
            properties = schema.get("properties", {})
            present_names: set[str] = set()
            def visit(value: Any, prefix: str = "") -> None:
                if isinstance(value, dict):
                    normalized_prefix = prefix.replace(".properties.", ".")
                    if normalized_prefix in names and "example" in value:
                        field_examples[normalized_prefix] = value["example"]
                    for key, child in value.items():
                        current = f"{prefix}.{key}" if prefix else str(key)
                        present_names.add(current.replace(".properties.", "."))
                        visit(child, current)
                elif isinstance(value, list):
                    for child in value:
                        visit(child, prefix)
            visit(properties)
            relevant_names = names & present_names if names else set()
            changed_fields.update(relevant_names)
        paths = api.get("paths", {}) if isinstance(api, dict) else {}
        for route, operations in paths.items() if isinstance(paths, dict) else ():
            if not isinstance(operations, dict):
                continue
            for method, operation in operations.items():
                method = str(method).lower()
                if method in _METHODS and isinstance(operation, dict):
                    operation_key = f"{method.upper()} {route}"
                    methods.add(operation_key)
                    responses = operation.get("responses", {})
                    response_statuses[operation_key] = [int(status) for status in responses if str(status).isdigit()]
                    route_context.append(f"{method.upper()} {route}: {json.dumps(operation, sort_keys=True)[:4000]}")
        if schema_path and isinstance(schema, dict):
            route_context.append(f"OPENAPI_SCHEMA {schema_path}: changed fields={sorted(relevant_names)}; schema={json.dumps(schema, sort_keys=True)[:12000]}")
    return route_context, methods, changed_fields, response_statuses, field_examples


def _contains_changed_example(payload: Any, field_path: str, expected: Any) -> bool:
    parts = field_path.split(".")
    def visit(value: Any, index: int) -> bool:
        if index == len(parts):
            return value == expected
        if isinstance(value, dict):
            if parts[index] in value and visit(value[parts[index]], index + 1):
                return True
            return any(visit(child, index) for child in value.values())
        if isinstance(value, list):
            return any(visit(child, index) for child in value)
        return False
    return visit(payload, 0)


def _target_examples(
    test_repo: Path,
    inventory: TestInventory,
    changed_paths: set[str],
    allowed_operations: set[str],
) -> list[dict[str, str]]:
    schema_names = [Path(path).name.lower().replace(".s1.schema.yaml", "").replace(".schema.yaml", "")
                    for path in changed_paths if "/openapispec/" in path and "/models/" in path]
    resource_tokens = set(re.findall(r"[a-z0-9]+", " ".join(schema_names)))
    resource_tokens -= {"objects", "general", "ledger", "s1"}
    allowed_methods = {item.split(" ", 1)[0] for item in allowed_operations}
    candidates: list[tuple[int, str, Any]] = []
    for suite in inventory.suites:
        if suite.module != "gl" or not suite.feature_files:
            continue
        for feature_path in suite.feature_files:
            path_tokens = set(re.findall(r"[a-z0-9]+", feature_path.lower()))
            overlap_tokens = resource_tokens & path_tokens
            overlap = len(overlap_tokens)
            method_overlap = len(allowed_methods & set(suite.methods))
            if overlap or method_overlap:
                candidates.append((overlap * 100 + method_overlap, feature_path, suite))
    candidates.sort(key=lambda row: (-row[0], row[1]))
    selected = candidates[:MAX_FEATURE_EXAMPLES]
    examples: list[dict[str, str]] = []
    selected_suites: set[str] = set()
    for score, feature_path, suite in selected:
        path_tokens = set(re.findall(r"[a-z0-9]+", feature_path.lower()))
        role = "same_resource_style" if resource_tokens and resource_tokens <= path_tokens else "style_only"
        examples.append({"path": feature_path, "kind": "feature", "evidence_role": role,
                         "content": _read_checkout_file(test_repo, feature_path)})
        selected_suites.add(suite.suite_id)
    fixtures: list[str] = []
    for suite in inventory.suites:
        if suite.suite_id in selected_suites:
            fixtures.extend((*suite.input_files, *suite.output_files))
    for path in sorted(set(fixtures))[:MAX_FIXTURE_EXAMPLES]:
        examples.append({"path": path, "kind": "fixture", "evidence_role": "style_only",
                         "content": _read_checkout_file(test_repo, path)})
    return examples


def _feature_example_rows(content: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    header: list[str] | None = None
    for line in content.splitlines():
        value = line.strip()
        if not value.startswith("|"):
            if value:
                header = None
            continue
        cells = [cell.strip() for cell in value.strip("|").split("|")]
        lowered = [cell.lower() for cell in cells]
        if "operation" in lowered and any(name in lowered for name in ("object", "uri", "url")):
            header = lowered
            continue
        if header is not None and len(cells) == len(header):
            rows.append(dict(zip(header, cells)))
    return rows


def _known_object_routes(test_repo: Path, inventory: TestInventory) -> set[tuple[str, str, str | None]]:
    """Read method, object, and optional URI pairs from GL example tables."""
    aliases: set[tuple[str, str, str | None]] = set()
    paths = sorted({p for suite in inventory.suites if suite.module == "gl" for p in suite.feature_files})
    for relative in paths[:300]:
        for row in _feature_example_rows(_read_checkout_file(test_repo, relative)):
            method = row.get("operation", "").upper()
            if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                continue
            object_alias = row.get("object", "")
            uri_alias = row.get("uri", row.get("url", ""))
            if (object_alias or uri_alias) and "<" not in object_alias and "<" not in uri_alias:
                aliases.add((method, object_alias, uri_alias or None))
    return aliases


def _build_agent(settings: Any) -> Any:
    try:
        from strands import Agent
        from strands.models import BedrockModel
        from strands.tools.executors import SequentialToolExecutor
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError("strands-agents is required for test proposals") from exc
    boto_session = None
    if settings.access_key_id and settings.secret_access_key:
        import boto3
        boto_session = boto3.Session(aws_access_key_id=settings.access_key_id,
            aws_secret_access_key=settings.secret_access_key, aws_session_token=settings.session_token,
            region_name=settings.region)
    elif settings.profile:
        import boto3
        boto_session = boto3.Session(profile_name=settings.profile, region_name=settings.region)
    kwargs = {} if boto_session else {"region_name": settings.region}
    model = BedrockModel(boto_session=boto_session, model_id=settings.model_id,
                         temperature=0, max_tokens=MAX_AGENT_RESPONSE_TOKENS, **kwargs)
    return Agent(model=model, tools=[], structured_output_model=_ProposalDrafts,
                 system_prompt=("Propose only API-observable tests supported by supplied OpenAPI and target test-repo evidence. "
                                "Never infer SQL coverage, unsupported routes/methods, or test execution. "
                                "Return gaps where prerequisites or behavior are unknown."),
                 tool_executor=SequentialToolExecutor())


def _safe_feature_path(value: str, module: str) -> str | None:
    try:
        path = _safe_relative(value)
    except ValueError:
        return None
    parts = path.parts
    if len(parts) < 3 or parts[0] != "features" or parts[1] != module or path.suffix != ".feature":
        return None
    return path.as_posix()


def _alias_matches_route(alias: str, route: str) -> bool:
    resource = route.rstrip("/").rsplit("/", 1)[-1]
    if resource == "{key}":
        resource = route.rstrip("/").rsplit("/", 2)[-2]
    normalized_alias = alias.lower().replace("_", "-").strip("/")
    resource = resource.lower().replace("_", "-")
    return normalized_alias == resource or normalized_alias.endswith("-" + resource)


def _uri_matches_route(uri: str, route: str) -> bool:
    normalize = lambda value: re.sub(r"\{\{?(\w+)\}?\}", r"{\1}", value.split("?", 1)[0].rstrip("/")).lower()
    return normalize(uri) == normalize(route)


def _safe_rendered_line(value: str) -> bool:
    return bool(value.strip()) and not any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value)


def _target_artifacts_available(test_repo: Path, relative_paths: list[str]) -> bool:
    root = test_repo.resolve(strict=True)
    for relative in relative_paths:
        try:
            rel = _safe_relative(relative)
        except ValueError:
            return False
        current = root
        for index, part in enumerate(rel.parts):
            current = current / part
            if current.is_symlink():
                return False
            if index < len(rel.parts) - 1 and current.exists() and not current.is_dir():
                return False
        if current.exists():
            return False
    return True


def _render_scaffold(proposal: ProposalDraft, jira_key: str, module: str, known_aliases: set[tuple[str, str, str | None]]) -> tuple[str, bytes, str, bytes, str, bytes] | None:
    if (proposal.gaps or proposal.prerequisites or proposal.tag is None or proposal.request_fixture is None
            or proposal.expected_fixture is None or proposal.response_status is None
            or not proposal.test_repo_object_alias or not proposal.request_case or not proposal.expected_case
            or not _safe_rendered_line(proposal.title) or not _safe_rendered_line(proposal.scenario_name)):
        return None
    feature_path = _safe_feature_path(proposal.feature_path, module)
    object_alias = proposal.test_repo_object_alias
    alias_segment = Path(object_alias).name
    keyed_route = bool(re.search(r"\{key\}", proposal.route, re.IGNORECASE))
    has_object_evidence = any(method == proposal.method.upper() and obj == object_alias
                              and (proposal.test_repo_uri is None or uri == proposal.test_repo_uri)
                              for method, obj, uri in known_aliases)
    if (feature_path is None or not _JIRA_RE.fullmatch(jira_key)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", object_alias) or alias_segment in {".", ".."}
            or not has_object_evidence
            or (proposal.test_repo_uri is not None and not _uri_matches_route(proposal.test_repo_uri, proposal.route))
            or (proposal.test_repo_uri is None and not _alias_matches_route(object_alias, proposal.route))
            or keyed_route):
        return None
    action = {"GET": "query", "POST": "create", "PUT": "update", "PATCH": "update", "DELETE": "delete"}.get(proposal.method.upper())
    if action is None:
        return None
    fixture_stem = f"{action}-{alias_segment}"
    input_path = f"features/{module}/input/{alias_segment}/{fixture_stem}.json"
    output_path = f"features/{module}/output/{alias_segment}/res-{fixture_stem}.json"
    if not re.fullmatch(r"[A-Za-z0-9_-]+", proposal.request_case) or not re.fullmatch(r"[A-Za-z0-9_-]+", proposal.expected_case):
        return None
    if proposal.method.lower() not in _METHODS:
        return None
    lines = ["@" + module, "Feature: " + proposal.title, "", f"  @{module} @{jira_key} @{proposal.tag}",
             "  Scenario: " + proposal.scenario_name, "    Given user copies company",
             "    Given user generates token",
             f'    When "{proposal.method.upper()}" to "{object_alias}" with key "" and file "{Path(input_path).name}:{proposal.request_case}" get variable ""',
             f'    Then response code is "{proposal.response_status}"',
             f'    And response matches "{Path(output_path).name}:{proposal.expected_case}"']
    feature = "\n".join(lines) + "\n"
    return (feature_path, feature.encode(), input_path,
            (json.dumps({proposal.request_case: proposal.request_fixture}, indent=2, sort_keys=True) + "\n").encode(),
            output_path,
            (json.dumps({proposal.expected_case: proposal.expected_fixture}, indent=2, sort_keys=True) + "\n").encode())


def _write_new_file(root: Path, relative: str, content: bytes) -> None:
    rel = _safe_relative(relative)
    dest = root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() or dest.is_symlink():
        raise ValueError(f"refusing to overwrite output artifact: {relative}")
    resolved_parent = dest.parent.resolve(strict=True)
    if root.resolve() not in resolved_parent.parents and resolved_parent != root.resolve():
        raise ValueError("output path escapes output directory")
    with tempfile.NamedTemporaryFile(dir=dest.parent, prefix=".proposal-", delete=False) as handle:
        temp = Path(handle.name)
        try:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        except Exception:
            temp.unlink(missing_ok=True)
            raise
    try:
        os.replace(temp, dest)
    except Exception:
        temp.unlink(missing_ok=True)
        raise


def run_test_proposals(
    report_dir: Path,
    app_repo: Path,
    test_repo: Path,
    output_dir: Path,
    jira_key: str | None = None,
) -> dict[str, object]:
    """Create a separate proposal report from a saved PR report and checkouts."""
    if jira_key is not None and not _JIRA_RE.fullmatch(jira_key):
        raise ValueError("jira_key must match IA-XXXXX")
    report_dir = report_dir.expanduser().resolve(strict=True)
    app_repo = app_repo.expanduser().resolve(strict=True)
    test_repo = test_repo.expanduser().resolve(strict=True)
    raw_output_dir = output_dir.expanduser()
    if raw_output_dir.is_symlink():
        raise ValueError("output directory cannot be a symlink")
    output_dir = raw_output_dir.resolve(strict=False)
    if output_dir.exists() and (not output_dir.is_dir() or any(output_dir.iterdir())):
        raise ValueError("output directory must be a new or empty directory")
    if any(root == output_dir or root in output_dir.parents or output_dir in root.parents
           for root in (report_dir, app_repo, test_repo)):
        raise ValueError("output directory must be outside the report and source checkouts")

    report, inventory, report_hash, inventory_hash = _validate_bundle(report_dir, app_repo, test_repo)
    route_context, allowed_operations, changed_fields, response_statuses, field_examples = _changed_openapi_paths(report, app_repo)
    changed_paths = {f["path"] for f in report["changed_files"] if f.get("scope") == "in_scope"}
    examples = _target_examples(test_repo, inventory, changed_paths, allowed_operations)
    known_object_routes = _known_object_routes(test_repo, inventory)
    api_files = [f for f in report["changed_files"] if f.get("scope") == "in_scope" and "/openapispec/" in f.get("path", "")]
    api_paths = {f["path"] for f in api_files}
    context: dict[str, Any] = {"api_changed_files": api_files,
        "changed_elements": [e for f in api_files for e in f.get("changed_elements", [])],
        "openapi": route_context, "allowed_operations": sorted(allowed_operations),
        "documented_response_statuses": response_statuses,
        "changed_openapi_fields": sorted(changed_fields), "changed_field_examples": field_examples,
        "known_gl_method_object_uri_rows": sorted(known_object_routes), "test_examples": examples}
    bounded = json.dumps(context, sort_keys=True)
    while len(bounded) > MAX_CONTEXT_CHARS and context["test_examples"]:
        context["test_examples"].pop()
        bounded = json.dumps(context, sort_keys=True)
    if len(bounded) > MAX_CONTEXT_CHARS:
        raise ValueError("OpenAPI and PR evidence exceed the bounded proposal context")
    from .pr_analysis import load_bedrock_settings
    agent = _build_agent(load_bedrock_settings())
    prompt = ("Create API-observable test proposals for this saved PR impact. Only methods/routes in allowed_operations are valid. "
              "Use exact target-repository conventions from examples. Do not treat SQL as REST-testable. "
              "Do not claim execution or coverage. If required behavior, setup, or fixture values are not evidenced, list gaps "
              "and leave scaffolding fields absent. Scaffold only with a method/object/URI pair in known_gl_method_object_uri_rows, "
              "an evidenced response status, caller Jira key, and complete fixtures. Keyed routes remain proposals with gaps because "
              "this command cannot invoke setup from another feature. "
              "Feature paths must be under features/<module>/. "
              "Return at most 20 proposals.\n" + bounded)
    result = agent(prompt)
    structured = getattr(result, "structured_output", result)
    drafts = structured if isinstance(structured, _ProposalDrafts) else _ProposalDrafts.model_validate(structured)

    created: list[dict[str, Any]] = []
    output_dir.mkdir(parents=True, exist_ok=True)
    module = "gl" if any("/gl/" in f.get("path", "") for f in report["changed_files"]) else "api-team"
    for draft in drafts.proposals:
        operation = f"{draft.method.upper()} {draft.route}"
        gaps = list(draft.gaps)
        if operation not in allowed_operations:
            gaps.append("method and route are not present in the changed OpenAPI evidence")
        if not changed_fields or draft.changed_field not in changed_fields:
            gaps.append("changed field is not an exact changed OpenAPI field")
        keyed_route = bool(re.search(r"\{key\}", draft.route, re.IGNORECASE))
        if keyed_route:
            gaps.append("keyed route lacks executable setup in the generated scenario; setup from another feature is not invoked")
        if draft.response_status not in response_statuses.get(operation, []):
            gaps.append("response status is not documented for this OpenAPI operation")
        expected_value = field_examples.get(draft.changed_field)
        if expected_value is None or not _contains_changed_example(draft.expected_fixture, draft.changed_field, expected_value):
            gaps.append("expected fixture does not contain the changed field with its documented example value")
        target_feature_paths = {path for suite in inventory.suites for path in suite.feature_files}
        valid_evidence_paths = api_paths | {e.get("relative_path") for e in report["evidence"]} | target_feature_paths | {item["path"] for item in examples}
        if not draft.evidence_paths or any(p not in valid_evidence_paths for p in draft.evidence_paths):
            gaps.append("proposal evidence paths do not resolve to PR or target-repository evidence")
        if jira_key is None:
            gaps.append("caller must supply a Jira key before scaffold generation")
        clean_draft = draft.model_dump(mode="json")
        clean_draft["gaps"] = list(dict.fromkeys(gaps))
        scaffold = _render_scaffold(draft, jira_key or "", module, known_object_routes) if not gaps and jira_key else None
        if not gaps and jira_key and scaffold is None:
            clean_draft["gaps"].append("target-repository scaffold structure, URL alias, or fixture references could not be validated")
        artifacts = []
        if scaffold:
            feature_path, feature_bytes, input_path, input_bytes, output_path, output_bytes = scaffold
            candidates = {feature_path: feature_bytes, input_path: input_bytes, output_path: output_bytes}
            if not _target_artifacts_available(test_repo, list(candidates)):
                clean_draft["gaps"].append("target test repository already contains a scaffold destination or uses a symlink path")
            elif any((output_dir / relative).exists() for relative in candidates):
                clean_draft["gaps"].append("scaffold output path collides with another proposal")
            else:
                try:
                    for relative, content in candidates.items():
                        _write_new_file(output_dir, relative, content)
                except Exception:
                    for relative in candidates:
                        (output_dir / relative).unlink(missing_ok=True)
                    raise
                artifacts = list(candidates)
        artifact_hashes = {path: hashlib.sha256((output_dir / path).read_bytes()).hexdigest() for path in artifacts}
        created.append({"proposal": clean_draft, "scaffold_status": "ready" if artifacts else "proposal_with_gaps",
                        "artifacts": artifacts, "artifact_sha256": artifact_hashes, "execution_status": "not_run"})

    result_obj: dict[str, Any] = {"schema": "ia-repomap.pr-test-proposal/v1", "status": "ok",
        "prompt_version": PROMPT_VERSION, "identity": {"app_head": report["identity"]["head"],
        "test_head": inventory.repository.head, "test_repository_id": inventory.repository.repository_id,
        "inventory_digest": inventory.repository.inventory_digest, "report_sha256": report_hash,
        "inventory_sha256": inventory_hash}, "jira_key": jira_key, "proposals": created,
        "execution_status": "not_run"}
    _write_new_file(output_dir, "test-proposals.json", (json.dumps(result_obj, indent=2, sort_keys=True) + "\n").encode())
    markdown = ["# API test proposals", "", f"Status: `{result_obj['status']}`; execution: `not_run`.", ""]
    for index, item in enumerate(created, 1):
        p = item["proposal"]
        markdown.extend([f"## {index}. {p['title']}", "", f"- Operation: `{p['method'].upper()} {p['route']}`",
                         f"- Changed field: `{p['changed_field']}`", f"- Expected behavior: {p['expected_behavior']}",
                         f"- Scaffold: `{item['scaffold_status']}`"])
        if p["gaps"]:
            markdown.append("- Gaps: " + "; ".join(p["gaps"]))
        if p["evidence_paths"]:
            markdown.append("- Evidence: " + ", ".join(f"`{x}`" for x in p["evidence_paths"]))
        if item["artifacts"]:
            markdown.append("- Draft files: " + ", ".join(f"`{x}`" for x in item["artifacts"]))
        markdown.append("")
    _write_new_file(output_dir, "test-proposals.md", ("\n".join(markdown) + "\n").encode())
    return result_obj
