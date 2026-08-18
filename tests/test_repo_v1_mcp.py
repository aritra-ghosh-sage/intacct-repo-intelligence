from __future__ import annotations

import json
import threading
import time
from hashlib import sha256
from pathlib import Path

from catalog import pr_review_prompt
from catalog.pr_review_catalog import CatalogResolution
from repo_v1_mcp.server import SECTIONS, PrReviewState, create_server

BASE = "a" * 40
TARGET = "b" * 40


def prepared_envelope(*, prompt_text: str | None = None) -> dict:
    if prompt_text is None:
        metadata = {
            "reviews": [
                {
                    "id": 1,
                    "html_url": "https://example/review/1",
                    "state": "COMMENTED",
                    "commit_id": TARGET,
                    "body": "Check this",
                }
            ],
            "inline_comments": [],
            "issue_comments": [
                {
                    "id": 2,
                    "html_url": "https://example/comment/2",
                    "body": "Please add tests",
                }
            ],
        }
        prompt_text = (
            "BEGIN UNTRUSTED GITHUB METADATA\n"
            + json.dumps(metadata)
            + "\nEND UNTRUSTED GITHUB METADATA"
        )
    return {
        "status": "ready",
        "input": {
            "repository": "intacct/ia-app",
            "repo_key": "ia-main",
            "pr_number": 49156,
            "request": "Review correctness.",
            "base_revision": BASE,
            "target_revision": TARGET,
            "catalog_resolution": "cache_hit",
            "source_resolution": "configured_checkout",
        },
        "step0": {
            "changed_files": [{"path": "app/source/example.cls", "status": "modified"}],
            "review_evidence": {},
        },
        "reports": {
            "step1": {"status": "complete", "gaps": [], "warnings": []},
            "step2": {"status": "partial", "gaps": ["one gap"], "warnings": []},
            "step3": {"status": "complete", "gaps": [], "warnings": ["one warning"]},
        },
        "prompt_text": prompt_text,
        "provenance": {
            "catalog_revision": TARGET,
            "catalog_mutation": "none",
        },
    }


def test_prepare_validates_inputs_before_backend(monkeypatch) -> None:
    called = False

    def backend(**_kwargs):
        nonlocal called
        called = True
        return prepared_envelope()

    monkeypatch.setattr("repo_v1_mcp.server.generate_prompt", backend)
    state = PrReviewState()

    response = state.prepare(0, "Review")

    assert response["status"] == "error"
    assert response["error"]["code"] == "pr_number_invalid"
    assert called is False

    response = state.prepare(49156, " ")
    assert response["error"]["code"] == "request_missing"


def test_prepare_returns_envelope_and_exact_provenance(monkeypatch) -> None:
    backend = lambda **_kwargs: prepared_envelope()

    response = PrReviewState(preparation_function=backend).prepare(
        49156, "Review correctness."
    )

    assert response["contract_version"] == 1
    assert response["operation"] == "pr_review_prepare"
    assert response["status"] == "ok"
    assert response["data"]["analysis_id"]
    assert response["snapshot"]["target_revision"] == TARGET
    assert response["snapshot"]["catalog_revision"] == TARGET
    assert response["snapshot"]["revision_relation"] == "exact"
    assert "prompt_text" not in response["data"]


def test_prepare_rejects_catalog_revision_mismatch() -> None:
    envelope = prepared_envelope()
    envelope["provenance"]["catalog_revision"] = BASE

    response = PrReviewState(
        preparation_function=lambda **_kwargs: envelope,
    ).prepare(49156, "Review correctness.")

    assert response["status"] == "error"
    assert response["error"]["code"] == "catalog_revision_mismatch"
    assert response["error"]["details"] == {
        "target_revision": TARGET,
        "catalog_revision": BASE,
        "fix": "Build or select an isolated catalog for the exact PR head SHA and retry.",
    }


def test_prepare_normalizes_existing_untrusted_comment_context() -> None:
    metadata = {
        "reviews": [
            {
                "id": 1,
                "body": {
                    "untrusted": True,
                    "encoding": "verbatim_github_text",
                    "text": "",
                    "availability": "unavailable",
                },
            },
            {
                "id": 2,
                "body": {
                    "untrusted": True,
                    "encoding": "verbatim_github_text",
                    "text": "Use evidence.",
                    "availability": "present",
                },
            },
        ],
        "inline_comments": [],
        "issue_comments": [],
    }
    prompt_text = (
        "BEGIN UNTRUSTED GITHUB METADATA\n"
        + json.dumps(metadata)
        + "\nEND UNTRUSTED GITHUB METADATA"
    )
    envelope = prepared_envelope(prompt_text=prompt_text)
    state = PrReviewState(preparation_function=lambda **_kwargs: envelope)

    analysis_id = state.prepare(49156, "Review")["data"]["analysis_id"]
    response = state.evidence(analysis_id, "comments")

    assert response["status"] == "ok"
    assert response["data"]["items"][0]["body"] == {
        "untrusted": True,
        "encoding": "verbatim_github_text",
        "text": "",
        "availability": "unavailable",
    }
    assert response["data"]["items"][1]["body"]["text"] == "Use evidence."


def test_prepare_maps_backend_failure_to_structured_redacted_error() -> None:
    class BackendError(RuntimeError):
        code = "catalog_build_failed"
        fix = "remove /private/tmp/pr-review and retry"

    def backend(**_kwargs):
        raise BackendError("failed at /Users/example/catalog.db")

    response = PrReviewState(preparation_function=backend).prepare(49156, "Review")

    assert response["status"] == "error"
    assert response["error"]["code"] == "catalog_build_failed"
    assert "/Users/" not in json.dumps(response)
    assert "/private/" not in json.dumps(response)
    assert "<internal-path>" in response["error"]["message"]


def test_evidence_paginates_with_opaque_cursor(monkeypatch) -> None:
    envelope = prepared_envelope()
    envelope["prompt_text"] = "BEGIN UNTRUSTED GITHUB METADATA\n" + json.dumps(
        {
            "reviews": [{"id": i, "body": f"comment-{i}"} for i in range(3)],
            "inline_comments": [],
            "issue_comments": [],
        }
    )
    state = PrReviewState(preparation_function=lambda **_kwargs: envelope)
    prepared = state.prepare(49156, "Review")
    analysis_id = prepared["data"]["analysis_id"]

    first = state.evidence(analysis_id, "comments", None, 2)
    assert first["status"] == "ok"
    assert len(first["data"]["items"]) == 2
    cursor = first["page"]["next_cursor"]
    assert isinstance(cursor, str) and cursor
    assert cursor != analysis_id

    second = state.evidence(analysis_id, "comments", cursor, 2)
    assert [item["id"] for item in second["data"]["items"]] == [2]
    assert second["page"]["next_cursor"] is None


def test_structured_step_report_is_paged_as_field_items() -> None:
    envelope = prepared_envelope()
    envelope["reports"]["step1"] = {
        "status": "complete",
        "direct_traces": [{"id": index} for index in range(3)],
    }
    state = PrReviewState(preparation_function=lambda **_kwargs: envelope)
    analysis_id = state.prepare(49156, "Review")["data"]["analysis_id"]

    first = state.evidence(analysis_id, "step1", limit=2)
    assert first["data"]["items"] == [
        {"field": "status", "value": "complete"},
        {"field": "direct_traces", "index": 0, "value": {"id": 0}},
    ]
    cursor = first["page"]["next_cursor"]
    second = state.evidence(analysis_id, "step1", cursor, 2)
    assert second["data"]["items"] == [
        {"field": "direct_traces", "index": 1, "value": {"id": 1}},
        {"field": "direct_traces", "index": 2, "value": {"id": 2}},
    ]
    assert second["page"]["next_cursor"] is None


def test_empty_step_fields_are_explicit_evidence_items() -> None:
    envelope = prepared_envelope()
    envelope["reports"]["step3"] = {
        "status": "complete",
        "reached_symbols": [],
        "transitive_edges": [],
        "skipped_edges": [],
        "warnings": [],
    }
    state = PrReviewState(preparation_function=lambda **_kwargs: envelope)
    analysis_id = state.prepare(49156, "Review")["data"]["analysis_id"]

    response = state.evidence(analysis_id, "step3", limit=10)

    assert response["data"]["items"] == [
        {"field": "status", "value": "complete"},
        {"field": "reached_symbols", "value": []},
        {"field": "transitive_edges", "value": []},
        {"field": "skipped_edges", "value": []},
        {"field": "warnings", "value": []},
    ]


def test_prepare_treats_blank_comment_bodies_as_unavailable() -> None:
    prompt_text = "BEGIN UNTRUSTED GITHUB METADATA\n" + json.dumps(
        {
            "reviews": [{"id": 1, "body": ""}, {"id": 2, "body": "  "}],
            "inline_comments": [],
            "issue_comments": [],
        }
    )
    state = PrReviewState(
        preparation_function=lambda **_kwargs: prepared_envelope(
            prompt_text=prompt_text
        )
    )
    analysis_id = state.prepare(49156, "Review")["data"]["analysis_id"]

    response = state.evidence(analysis_id, "comments")

    assert [item["body"] for item in response["data"]["items"]] == [
        {
            "untrusted": True,
            "encoding": "verbatim_github_text",
            "text": "",
            "availability": "unavailable",
        },
        {
            "untrusted": True,
            "encoding": "verbatim_github_text",
            "text": "",
            "availability": "unavailable",
        },
    ]


def test_prepare_treats_missing_comment_body_as_unavailable() -> None:
    prompt_text = "BEGIN UNTRUSTED GITHUB METADATA\n" + json.dumps(
        {
            "reviews": [{"id": 1, "state": "COMMENTED"}],
            "inline_comments": [],
            "issue_comments": [],
        }
    )
    state = PrReviewState(
        preparation_function=lambda **_kwargs: prepared_envelope(
            prompt_text=prompt_text
        )
    )
    analysis_id = state.prepare(49156, "Review")["data"]["analysis_id"]

    response = state.evidence(analysis_id, "comments")

    assert response["data"]["items"][0]["body"] == {
        "untrusted": True,
        "encoding": "verbatim_github_text",
        "text": "",
        "availability": "unavailable",
    }


def test_unknown_handle_and_invalid_section_are_structured_errors() -> None:
    state = PrReviewState()

    unknown = state.evidence("missing", "summary")
    assert unknown["status"] == "error"
    assert unknown["error"]["code"] == "unknown_analysis_id"

    invalid = state.evidence("missing", "not-a-section")
    assert invalid["error"]["code"] == "invalid_section"


def test_evidence_rejects_invalid_limits_and_cross_section_cursors() -> None:
    state = PrReviewState(preparation_function=lambda **_kwargs: prepared_envelope())
    analysis_id = state.prepare(49156, "Review")["data"]["analysis_id"]

    assert (
        state.evidence(analysis_id, "summary", limit=0)["error"]["code"]
        == "limit_invalid"
    )
    assert (
        state.evidence(analysis_id, "summary", limit=101)["error"]["code"]
        == "limit_invalid"
    )

    first = state.evidence(analysis_id, "comments", limit=1)
    cursor = first["page"]["next_cursor"]
    assert cursor
    invalid = state.evidence(analysis_id, "step1", cursor=cursor)
    assert invalid["error"]["code"] == "cursor_invalid"


def test_evidence_timeout_is_structured(monkeypatch) -> None:
    state = PrReviewState(preparation_function=lambda **_kwargs: prepared_envelope())
    analysis_id = state.prepare(49156, "Review")["data"]["analysis_id"]

    def timeout(*_args, **_kwargs):
        raise TimeoutError("deadline")

    monkeypatch.setattr("repo_v1_mcp.server._section_items", timeout)
    response = state.evidence(analysis_id, "step1")

    assert response["status"] == "timeout"
    assert response["error"]["code"] == "deadline_exceeded"
    assert response["error"]["details"]["phase"] == "evidence"
    assert response["error"]["details"]["stage"] == "flattening"


def test_evidence_deadline_includes_lock_acquisition(monkeypatch) -> None:
    state = PrReviewState(preparation_function=lambda **_kwargs: prepared_envelope())
    analysis_id = state.prepare(49156, "Review")["data"]["analysis_id"]
    monkeypatch.setattr("repo_v1_mcp.server.EVIDENCE_TIMEOUT_SECONDS", 0.001)

    ready = threading.Event()
    release = threading.Event()

    def hold_lock() -> None:
        with state._lock:
            ready.set()
            release.wait()

    thread = threading.Thread(target=hold_lock)
    thread.start()
    assert ready.wait(1)
    try:
        response = state.evidence(analysis_id, "summary")
    finally:
        release.set()
        thread.join(1)

    assert response["status"] == "timeout"
    assert response["error"]["code"] == "deadline_exceeded"
    assert response["error"]["details"]["stage"] == "lock_acquisition"


def test_handle_ttl_expires(monkeypatch) -> None:
    state = PrReviewState(
        ttl_seconds=0.01,
        preparation_function=lambda **_kwargs: prepared_envelope(),
    )
    prepared = state.prepare(49156, "Review")
    time.sleep(0.02)
    response = state.evidence(prepared["data"]["analysis_id"], "summary")
    assert response["error"]["code"] == "unknown_analysis_id"


def test_template_resource_and_server_registrations() -> None:
    mcp, state = create_server()

    template = Path("docs/review/pr-review-template.md").read_text(encoding="utf-8")
    assert "## 🎯 Findings" in template
    assert "repo-v1://review/pr-template" in str(mcp._resource_manager._resources)
    resource = mcp._resource_manager._resources["repo-v1://review/pr-template"]
    assert resource.fn() == template
    assert state.template() == template
    assert "pr_review_prepare" in mcp._tool_manager._tools
    assert "pr_review_evidence" in mcp._tool_manager._tools
    assert "pr_review" in mcp._prompt_manager._prompts
    assert set(SECTIONS) == {
        "summary",
        "step0",
        "comments",
        "step1",
        "step2",
        "step3",
        "step4",
        "metrics",
    }


def test_preparation_timeout_is_structured_and_not_claimed_as_cancellation(
    monkeypatch,
) -> None:
    def slow_backend(**_kwargs):
        time.sleep(0.05)
        return prepared_envelope()

    response = PrReviewState(
        preparation_timeout_seconds=0.001,
        preparation_function=slow_backend,
    ).prepare(49156, "Review")

    assert response["status"] == "timeout"
    assert response["error"]["code"] == "deadline_exceeded"
    assert response["error"]["details"]["phase"] == "preparation"


def test_preparation_deadline_covers_post_processing_and_keeps_no_handle(
    monkeypatch,
) -> None:
    state = PrReviewState(
        preparation_timeout_seconds=0.001,
        preparation_function=lambda **_kwargs: prepared_envelope(),
    )
    from repo_v1_mcp.server import _redact_paths

    def slow_redact(value, *, deadline=None):
        time.sleep(0.01)
        return _redact_paths(value, deadline=deadline)

    monkeypatch.setattr("repo_v1_mcp.server._redact_paths", slow_redact)
    response = state.prepare(49156, "Review")

    assert response["status"] == "timeout"
    assert response["error"]["code"] == "deadline_exceeded"
    assert not state._handles


def test_worker_bounds_post_processing_before_returning_result(monkeypatch) -> None:
    from repo_v1_mcp import server

    class Connection:
        def __init__(self) -> None:
            self.messages: list[dict] = []

        def send(self, value: dict) -> None:
            self.messages.append(value)

        def close(self) -> None:
            pass

    original = server._redact_paths

    def slow_redact(value, *, deadline=None):
        time.sleep(0.01)
        return original(value, deadline=deadline)

    monkeypatch.setattr(
        server, "generate_prompt", lambda **_kwargs: prepared_envelope()
    )
    monkeypatch.setattr(server, "_redact_paths", slow_redact)
    connection = Connection()

    server._preparation_worker(connection, 49156, "Review", time.monotonic() + 0.001)

    assert connection.messages[0]["ok"] is False
    assert connection.messages[0]["error"]["code"] == "deadline_exceeded"


def test_preparation_deadline_covers_handle_storage() -> None:
    state = PrReviewState(
        preparation_timeout_seconds=0.001,
        preparation_function=lambda **_kwargs: prepared_envelope(),
    )
    ready = threading.Event()
    release = threading.Event()

    def hold_lock() -> None:
        with state._lock:
            ready.set()
            release.wait()

    thread = threading.Thread(target=hold_lock)
    thread.start()
    assert ready.wait(1)
    try:
        response = state.prepare(49156, "Review")
    finally:
        release.set()
        thread.join(1)

    assert response["status"] == "timeout"
    assert response["error"]["code"] == "deadline_exceeded"
    assert not state._handles


def test_fixture_orchestration_exposes_all_mcp_sections_without_mutation(
    monkeypatch, tmp_path: Path
) -> None:
    canonical = tmp_path / "catalog.db"
    canonical.write_bytes(b"canonical catalog is immutable")
    canonical_before = sha256(canonical.read_bytes()).hexdigest()
    metadata = {
        "schema_version": "0.1",
        "analysis_kind": "pr_impact_metadata",
        "repo_key": "ia-main",
        "repository": "intacct/ia-app",
        "pull_request": {
            "number": 49156,
            "url": "https://github.com/intacct/ia-app/pull/49156",
            "base_revision": BASE,
            "target_revision": TARGET,
        },
        "changed_files": [{"filename": "app/source/example.cls", "status": "modified"}],
        "reviews": [{"id": 1, "state": "COMMENTED", "body": None}],
        "inline_comments": [
            {
                "id": 2,
                "path": "app/source/example.cls",
                "body": "Check /Users/private/repo",
            }
        ],
        "issue_comments": [],
        "check_runs": [],
        "provenance": {
            "provider": "fixture",
            "fetched_at": "2026-08-14T00:00:00+00:00",
        },
    }
    reports = {
        "step1": {
            "status": "complete",
            "gaps": [],
            "direct_traces": [{"path": "/private/tmp/evidence"}],
        },
        "step2": {
            "status": "partial",
            "gaps": ["unavailable"],
            "surface_audit": [{"id": 2}],
        },
        "step3": {"status": "complete", "gaps": [], "reached_symbols": [{"id": 3}]},
    }
    resolution = CatalogResolution(
        target_revision=TARGET,
        active_db=tmp_path / "isolated.db",
        manifest=tmp_path / "manifest.yaml",
        resolution="fixture_cache",
        source_resolution="fixture_source",
    )
    monkeypatch.setattr(
        pr_review_prompt, "fetch_pr_metadata", lambda **_kwargs: metadata
    )
    monkeypatch.setattr(
        pr_review_prompt, "resolve_exact_catalog", lambda **_kwargs: resolution
    )
    monkeypatch.setattr(pr_review_prompt, "_run_analysis", lambda *_args: reports)

    def backend(**kwargs):
        return pr_review_prompt.generate_prompt(
            pr_number=kwargs["pr_number"], request=kwargs["request"], manifest="fixture"
        )

    state = PrReviewState(preparation_function=backend)
    prepared = state.prepare(49156, "Review correctness")
    assert prepared["status"] == "ok"
    assert prepared["snapshot"]["target_revision"] == TARGET
    assert prepared["snapshot"]["catalog_revision"] == TARGET
    assert sha256(canonical.read_bytes()).hexdigest() == canonical_before

    analysis_id = prepared["data"]["analysis_id"]
    all_items: dict[str, list[dict]] = {}
    for section in SECTIONS:
        cursor = None
        all_items[section] = []
        while True:
            page = state.evidence(analysis_id, section, cursor, limit=1)
            assert page["status"] == "ok"
            all_items[section].extend(page["data"]["items"])
            cursor = page["page"]["next_cursor"]
            if cursor is None:
                break

    assert set(all_items) == set(SECTIONS)
    bodyless = all_items["comments"][0]["body"]
    assert bodyless["untrusted"] is True
    assert bodyless["availability"] == "unavailable"
    assert bodyless["text"] == ""
    assert "/Users/" not in json.dumps(all_items)
    assert "/private/" not in json.dumps(all_items)
    assert "<internal-path>" in json.dumps(all_items)
    assert state.template() == Path("docs/review/pr-review-template.md").read_text(
        encoding="utf-8"
    )


def test_production_preparation_worker_enforces_deadline() -> None:
    response = PrReviewState(preparation_timeout_seconds=0.001).prepare(
        49156,
        "Review correctness.",
    )

    assert response["status"] == "timeout"
    assert response["error"]["code"] == "deadline_exceeded"
