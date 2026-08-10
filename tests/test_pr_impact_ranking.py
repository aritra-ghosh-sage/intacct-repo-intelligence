from __future__ import annotations

from catalog.pr_impact_ranking import rank_direct_traces


def test_ranking_orders_by_surface_count_then_fact_count_then_changed_file() -> None:
    traces = [
        {"surface": "symbols", "status": "available", "facts": [{"catalog_record_id": 1, "source_path": "changed.php"}]},
        {"surface": "outgoing_relationships", "status": "unresolved", "facts": [{"catalog_record_id": 2, "source_path": "changed.php"}]},
        {"surface": "files", "status": "available", "facts": [{"catalog_record_id": 3, "source_path": "other.php"}]},
    ]
    ranking = rank_direct_traces(traces, [{"path": "changed.php", "status": "modified"}])
    assert ranking[0]["source_path"] == "changed.php"
    assert ranking[0]["distinct_surface_count"] == 2
    assert ranking[0]["statuses"] == ["available", "unresolved"]


def test_ranking_is_deterministic_on_ties_and_excludes_deferred() -> None:
    traces = [
        {"surface": "tests", "status": "deferred", "facts": [{"fact_key": "t", "source_path": "deferred"}]},
        {"surface": "symbols", "status": "available", "facts": [{"catalog_record_id": 2, "source_path": "b.php"}]},
        {"surface": "files", "status": "available", "facts": [{"catalog_record_id": 1, "source_path": "a.php"}]},
    ]
    first = rank_direct_traces(traces, [])
    second = rank_direct_traces(list(reversed(traces)), [])
    assert first == second
    assert [item["source_path"] for item in first] == ["a.php", "b.php"]


def test_ranking_deduplicates_fact_keys() -> None:
    traces = [
        {"surface": "symbols", "status": "available", "facts": [{"catalog_record_id": 1, "source_path": "a.php"}]},
        {"surface": "symbols", "status": "available", "facts": [{"catalog_record_id": 1, "source_path": "a.php"}]},
    ]
    ranking = rank_direct_traces(traces, [])
    assert ranking[0]["fact_count"] == 1
