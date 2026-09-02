from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from greenfield_harness.engine import (
    HarnessError,
    behavior_packet,
    capture_context,
    l2_inspect,
    l3_resolve,
    run_harness,
)
from greenfield_harness.handoff import HarnessHandoff, HarnessHandoffError
from greenfield_harness.provider import FakeInvestigatorProvider


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _repo(tmp_path: Path) -> tuple[Path, str, str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    repo = tmp_path / "source"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "harness@example.invalid")
    _git(repo, "config", "user.name", "Harness")
    (repo / "service.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "service.txt").write_text("one\nchanged\nthree\n", encoding="utf-8")
    _git(repo, "commit", "-am", "head", "-q")
    head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "remote", "add", "origin", "git@github.com:example/source.git")
    return repo, base, head


def _run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **kwargs: object
) -> tuple[dict[str, object], Path]:
    repo, base, head = _repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "artifacts" / "greenfield-harness" / "run-1"
    paths = run_harness(
        source_root=repo,
        output_dir=output,
        pr=9,
        base_revision=base,
        target_revision=head,
        **kwargs,
    )
    return json.loads(paths["analysis"].read_text()), output


def test_identity_packet_and_no_external_writes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    analysis, output = _run(monkeypatch, tmp_path)
    context = json.loads((output / "harness-run-context.json").read_text())
    packet = json.loads((output / "behavior-packet.json").read_text())
    assert context["source"]["pr_number"] == 9 and context["source"][
        "changed_paths"
    ] == ["service.txt"]
    assert (
        packet["paths"][0]["target_blob_sha256"] and packet["paths"][0]["context_gaps"]
    )
    assert analysis["provenance"] == {
        "read_only": True,
        "github_writes": "none",
        "catalog_mutation": "none",
        "model_calls": "none",
    }
    assert (
        json.loads((output / "l1-locate.json").read_text())["locators"][0]["status"]
        == "unavailable"
    )
    assert {path.name for path in output.iterdir()} >= {
        "harness-run-context.json",
        "harness-analysis.json",
        "harness-flow-handoff.json",
    }
    with pytest.raises(HarnessError, match="output-dir"):
        run_harness(
            source_root=tmp_path / "source",
            output_dir=tmp_path / "outside",
            pr=9,
            base_revision=context["source"]["base_revision"],
            target_revision=context["source"]["target_revision"],
        )


def test_l1_success_unavailable_and_unassigned_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo, base, head = _repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    handbook = {
        "sections": {
            "behavior:a": {
                "locators": [
                    {"path": "service.txt", "line": 2, "source_revision": head},
                    {"path": "other.txt", "line": 1, "source_revision": head},
                ]
            }
        }
    }
    output = tmp_path / "artifacts" / "greenfield-harness" / "run-1"
    paths = run_harness(
        source_root=repo,
        output_dir=output,
        pr=9,
        base_revision=base,
        target_revision=head,
        handbook=handbook,
    )
    packet = json.loads(paths["packet"].read_text())
    l1 = json.loads(paths["l1"].read_text())
    assert l1["locators"][0]["status"] == "available"
    assert packet["unassigned_evidence"][0]["reason"] == "no_exact_changed_path_join"


def test_l2_revision_hash_and_budget_gap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    analysis, output = _run(monkeypatch, tmp_path, budgets={"max_bytes": 1})
    l2 = json.loads((output / "l2-inspect.json").read_text())
    assert l2["ledger"] == [] and any(
        row["reason"] == "context_budget_exhausted" for row in analysis["gaps"]
    )
    repo, base, head = _repo(tmp_path / "second")
    context = capture_context(
        source_root=repo, pr=1, base_revision=base, target_revision=head
    )
    assert context["source"]["target_revision"] == head
    monkeypatch.chdir(tmp_path / "second")
    output = tmp_path / "second" / "artifacts" / "greenfield-harness" / "run-2"
    paths = run_harness(
        source_root=repo,
        output_dir=output,
        pr=1,
        base_revision=base,
        target_revision=head,
    )
    result = json.loads(paths["l2"].read_text())["ledger"][0]["result"]
    assert (
        result["source_revision"] == head
        and result["path"] == "service.txt"
        and result["source_blob_sha256"]
        == json.loads(paths["packet"].read_text())["paths"][0]["target_blob_sha256"]
        and result["excerpt_sha256"]
        == hashlib.sha256(result["excerpt"].encode("utf-8")).hexdigest()
    )


def test_deleted_path_and_l2_per_excerpt_budget_are_explicit_gaps(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo, base, head = _repo(tmp_path)
    _git(repo, "rm", "service.txt")
    _git(repo, "commit", "-qm", "delete")
    deleted_head = _git(repo, "rev-parse", "HEAD")
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "artifacts" / "greenfield-harness" / "deleted"
    paths = run_harness(
        source_root=repo,
        output_dir=output,
        pr=1,
        base_revision=base,
        target_revision=deleted_head,
    )
    packet = json.loads(paths["packet"].read_text())
    analysis = json.loads(paths["analysis"].read_text())
    l2 = json.loads(paths["l2"].read_text())
    assert packet["paths"][0]["target_blob_sha256"] is None
    assert any(
        row["reason"] == "target_path_absent_at_revision" for row in analysis["gaps"]
    )
    assert any(
        row["reason"] == "target_path_absent_at_revision" for row in l2["gaps"]
    )

    context = capture_context(
        source_root=repo,
        pr=1,
        base_revision=base,
        target_revision=head,
        budgets={"max_tool_calls": 1},
    )
    packet = {
        "paths": [
            {
                "path": "service.txt",
                "target_blob_sha256": "bound",
                "excerpt_ranges": [
                    {"start_line": 1, "end_line": 1},
                    {"start_line": 2, "end_line": 2},
                ],
            }
        ]
    }
    l2 = l2_inspect(context, packet)
    assert len(l2["ledger"]) == 1
    assert any(row.get("budget") == "max_tool_calls" for row in l2["gaps"])


def test_l2_prioritizes_application_excerpt_ahead_of_large_github_metadata(
    tmp_path: Path,
) -> None:
    repo, base, _ = _repo(tmp_path)
    (repo / ".github").mkdir()
    (repo / ".github" / "CODEOWNERS").write_text("x" * 130_000, encoding="utf-8")
    (repo / "app.py").write_text("before\nchanged\nafter\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "metadata and application change")
    head = _git(repo, "rev-parse", "HEAD")
    context = capture_context(
        source_root=repo,
        pr=1,
        base_revision=base,
        target_revision=head,
        budgets={"max_bytes": 21},
    )
    packet = behavior_packet(context)
    l2 = l2_inspect(context, packet)
    assert [entry["result"]["path"] for entry in l2["ledger"]] == ["app.py"]
    assert l2["used_bytes"] == len(l2["ledger"][0]["result"]["excerpt"].encode("utf-8"))
    assert any(row["path"] == ".github/CODEOWNERS" for row in l2["gaps"])
    ranked = l2_inspect(
        {**context, "evidence_budgets": {**context["evidence_budgets"], "max_bytes": 140_000}},
        packet,
    )
    assert [entry["result"]["path"] for entry in ranked["ledger"]] == [
        "app.py",
        "service.txt",
        ".github/CODEOWNERS",
    ]


def test_l2_counts_small_excerpt_not_large_target_blob(tmp_path: Path) -> None:
    repo, base, _ = _repo(tmp_path)
    (repo / "app.py").write_text("padding\n" * 20_000 + "old\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-qm", "large source base")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "app.py").write_text(
        "padding\n" * 20_000 + "changed\n", encoding="utf-8"
    )
    _git(repo, "commit", "-am", "large source change", "-q")
    head = _git(repo, "rev-parse", "HEAD")
    context = capture_context(
        source_root=repo,
        pr=1,
        base_revision=base,
        target_revision=head,
        budgets={"max_bytes": 40},
    )
    l2 = l2_inspect(context, behavior_packet(context))
    assert len(l2["ledger"]) == 1
    assert l2["used_bytes"] < 40
    assert l2["ledger"][0]["result"]["path"] == "app.py"


def test_l2_oversized_excerpt_is_local_and_provenance_is_bound(tmp_path: Path) -> None:
    repo, base, _ = _repo(tmp_path)
    (repo / "oversized.txt").write_text(
        "one\n" + "x" * 30 + "\nthree\n", encoding="utf-8"
    )
    _git(repo, "add", "oversized.txt")
    _git(repo, "commit", "-qm", "oversized context")
    head = _git(repo, "rev-parse", "HEAD")
    context = capture_context(
        source_root=repo,
        pr=1,
        base_revision=base,
        target_revision=head,
        budgets={"max_bytes": 20},
    )
    packet = behavior_packet(context)
    l2 = l2_inspect(context, packet)
    assert [row["result"]["path"] for row in l2["ledger"]] == ["service.txt"]
    gap = next(row for row in l2["gaps"] if row["path"] == "oversized.txt")
    assert (gap["start_line"], gap["end_line"], gap["budget"]) == (1, 3, "max_bytes")
    result = l2["ledger"][0]["result"]
    expected_blob = next(row for row in packet["paths"] if row["path"] == "service.txt")
    assert result["source_revision"] == head
    assert result["source_blob_sha256"] == expected_blob["target_blob_sha256"]
    assert result["excerpt_sha256"] == hashlib.sha256(result["excerpt"].encode()).hexdigest()


def test_l2_retains_exact_crlf_excerpt_bytes(tmp_path: Path) -> None:
    repo, base, _ = _repo(tmp_path)
    target_bytes = b"one\r\nchanged\r\nthree\r\n"
    (repo / "service.txt").write_bytes(target_bytes)
    _git(repo, "commit", "-am", "crlf target", "-q")
    head = _git(repo, "rev-parse", "HEAD")
    context = capture_context(
        source_root=repo, pr=1, base_revision=base, target_revision=head
    )
    l2 = l2_inspect(context, behavior_packet(context))
    result = l2["ledger"][0]["result"]
    assert result["excerpt"] == target_bytes.decode("utf-8")
    assert result["excerpt_sha256"] == hashlib.sha256(target_bytes).hexdigest()
    assert l2["used_bytes"] == len(target_bytes)


def test_empty_target_path_is_an_unavailable_l2_gap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo, base, _ = _repo(tmp_path)
    (repo / "service.txt").write_text("", encoding="utf-8")
    _git(repo, "commit", "-am", "empty target", "-q")
    head = _git(repo, "rev-parse", "HEAD")
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "artifacts" / "greenfield-harness" / "empty"
    paths = run_harness(
        source_root=repo,
        output_dir=output,
        pr=1,
        base_revision=base,
        target_revision=head,
    )
    packet = json.loads(paths["packet"].read_text())
    l2 = json.loads(paths["l2"].read_text())
    assert packet["paths"][0]["excerpt_ranges"] == []
    assert any(row["reason"] == "target_path_empty_at_revision" for row in l2["gaps"])


def test_l3_runs_only_for_material_explicit_gap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, output = _run(
        monkeypatch, tmp_path, gap_requests=[{"material": False, "literal": "changed"}]
    )
    assert json.loads((output / "l3-resolve.json").read_text())["ledger"] == []
    _, output = _run(
        monkeypatch,
        tmp_path / "second",
        gap_requests=[
            {"material": True, "literal": "changed", "path_prefix": "service.txt"}
        ],
    )
    assert json.loads((output / "l3-resolve.json").read_text())["ledger"][0]["result"][
        "matches"
    ]


def test_l3_enforces_unique_file_and_total_result_budgets(tmp_path: Path) -> None:
    repo, base, _ = _repo(tmp_path)
    (repo / "other.txt").write_text("changed\n", encoding="utf-8")
    _git(repo, "add", "other.txt")
    _git(repo, "commit", "-qm", "second-match")
    head = _git(repo, "rev-parse", "HEAD")
    context = capture_context(
        source_root=repo,
        pr=1,
        base_revision=base,
        target_revision=head,
        budgets={"max_files": 1},
    )
    l3 = l3_resolve(context, {}, {}, [{"material": True, "literal": "changed"}])
    assert len(l3["matched_files"]) == 1
    assert any(row.get("budget") == "max_files" for row in l3["gaps"])
    result_limited = l3_resolve(
        capture_context(
            source_root=repo,
            pr=1,
            base_revision=base,
            target_revision=head,
            budgets={"max_results": 1},
        ),
        {},
        {},
        [{"material": True, "literal": "changed"}] * 2,
    )
    assert result_limited["used_results"] == 1
    assert any(row.get("budget") == "max_results" for row in result_limited["gaps"])


def test_projections_reproduce_canonical_decisions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    analysis, output = _run(monkeypatch, tmp_path)
    report = json.loads((output / "behavior-impact-report.json").read_text())
    assert report["analysis_sha256"] == analysis["analysis_sha256"]
    assert report["decisions"] == {key: analysis[key] for key in report["decisions"]}
    assert "Harness review" in (output / "review.md").read_text()


def test_handoff_rejects_out_of_order_and_sha_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, output = _run(monkeypatch, tmp_path)
    assert HarnessHandoff.validate(output)["status"] in {"complete", "degraded"}
    with pytest.raises(HarnessHandoffError, match="SHA mismatch"):
        (output / "l2-inspect.json").write_text("{}", encoding="utf-8")
        HarnessHandoff.validate(output)
    handoff = HarnessHandoff(tmp_path / "new", {"repository": "example/source"})
    with pytest.raises(HarnessHandoffError, match="out of order"):
        handoff.complete("analyze", inputs={}, outputs={})


def test_fake_provider_cannot_promote_claim(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    provider = FakeInvestigatorProvider([{"claim": "impact", "status": "confirmed"}])
    analysis, _ = _run(monkeypatch, tmp_path)
    assert provider.calls == []
    assert analysis["repository_impacts"] == []
