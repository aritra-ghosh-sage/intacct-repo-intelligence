# ============================================================
# benchmark_score.py -- intacct-repo-intelligence, commit 1dab607
# Run:  python benchmark_score.py \
#         --db catalog.sqlite \
#         --goldset apbill_goldset.json \
#         --manifest manifest.yaml \
#         --out scorecard/
# ============================================================
import argparse
import json
import sqlite3
import statistics as st
import time
from pathlib import Path


# --------- Pillar 1: Discovery Quality (APBill P/R/F1) --------
def pillar1_discovery(conn, gold):
    """P/R/F1 per entity_root vs. gold set."""
    scores = []
    for row in gold:
        er = row["entity"]
        # predicted classes, .ent files, workflows, endpoints for this root
        pred_classes = {
            r[0]
            for r in conn.execute(
                """
            SELECT s.name FROM entity_mappings em
            JOIN entity_nodes en ON en.id = em.entity_id
            JOIN symbols s ON s.id = em.symbol_id
            WHERE en.name = ? and s.kind = \"class\"""",
                (er,),
            )
        }
        pred_ent = {
            r[0]
            for r in conn.execute(
                """
            SELECT f.path FROM entity_mappings em
            JOIN entity_nodes en ON en.id = em.entity_id
            JOIN symbols s ON s.id = em.symbol_id
            JOIN files f ON f.id = s.file_id
            WHERE s.name = ? AND f.path like '%.ent'""",
                (er,),
            )
        }
        pred_wf = {
            r[0]
            for r in conn.execute(
                """
            SELECT w.name FROM workflows w
            JOIN entity_nodes en ON en.id = w.entity_id
            WHERE en.name = ?""",
                (er,),
            )
        }
        pred_rest = {
            f"{r[0]} {r[1]}"
            for r in conn.execute(
                """
            SELECT re.method, re.path FROM rest_endpoints re
            LEFT JOIN symbols s ON s.id = re.handler_symbol_id
            JOIN entity_mappings em ON em.symbol_id = s.id
            JOIN entity_nodes en ON en.id = em.entity_id
            WHERE en.name = ?""",
                (er,),
            )
        }
        for facet, pred, expected in [
            ("class", pred_classes, set(row["expected_classes"])),
            ("ent", pred_ent, set(row["expected_ent_files"])),
            ("workflow", pred_wf, set(row["expected_workflows"])),
            ("rest", pred_rest, set(row["expected_rest_endpoints"])),
        ]:
            tp = len(pred & expected)
            fp = len(pred - expected)
            fn = len(expected - pred)
            p = tp / (tp + fp) if (tp + fp) else 0.0
            r = tp / (tp + fn) if (tp + fn) else 0.0
            f = 2 * p * r / (p + r) if (p + r) else 0.0
            scores.append(
                {
                    "entity": er,
                    "facet": facet,
                    "tp": tp,
                    "fp": fp,
                    "fn": fn,
                    "precision": p,
                    "recall": r,
                    "f1": f,
                }
            )
    # aggregate
    agg = {}
    for facet in {s["facet"] for s in scores}:
        f_scores = [s for s in scores if s["facet"] == facet]
        agg[facet] = {
            "macro_f1": st.fmean(s["f1"] for s in f_scores),
            "micro_p": sum(s["tp"] for s in f_scores)
            / max(1, sum(s["tp"] + s["fp"] for s in f_scores)),
            "micro_r": sum(s["tp"] for s in f_scores)
            / max(1, sum(s["tp"] + s["fn"] for s in f_scores)),
        }
    return {"per_entity_facet": scores, "aggregate": agg}


# --------- Pillar 2: Coverage --------------------------------
def pillar2_coverage(conn):
    q = """SELECT f.path, COUNT(*) AS n,
                  SUM(CASE WHEN EXISTS(SELECT 1 FROM symbols s WHERE s.file_id=f.id) THEN 1 ELSE 0 END) AS parsed
           FROM files f GROUP BY f.path"""
    per_ext = [dict(zip(("extension", "n", "parsed"), r)) for r in conn.execute(q)]
    for row in per_ext:
        row["parse_rate"] = row["parsed"] / row["n"] if row["n"] else 0.0
    n_ent_files = conn.execute(
        "SELECT COUNT(*) FROM files WHERE path like '%.ent'"
    ).fetchone()[0]
    n_entity_roots = conn.execute("SELECT COUNT(*) FROM entity_roots").fetchone()[0]
    return {
        "per_extension": per_ext,
        "entity_root_coverage": n_entity_roots / max(1, n_ent_files),
    }


# --------- Pillar 3: Output Accuracy (structural sanity) -----
def pillar3_accuracy(conn):
    checks = {}
    checks["orphan_symbols_pct"] = (
        conn.execute("""
        SELECT 1.0*COUNT(*)/NULLIF((SELECT COUNT(*) FROM symbols),0)
        FROM symbols s WHERE NOT EXISTS(
            SELECT 1 FROM relationships r
            WHERE r.source_symbol_id=s.id OR r.target_symbol_id=s.id)""").fetchone()[0]
        or 0.0
    )
    checks["rest_endpoints_with_handler"] = (
        conn.execute("""
        SELECT 1.0*SUM(CASE WHEN handler_symbol_id IS NOT NULL THEN 1 ELSE 0 END)/NULLIF(COUNT(*),0)
        FROM rest_endpoints""").fetchone()[0]
        or 0.0
    )
    checks["workflows_with_root"] = (
        conn.execute("""
        SELECT 1.0*SUM(CASE WHEN entity_id IS NOT NULL THEN 1 ELSE 0 END)/NULLIF(COUNT(*),0)
        FROM workflows""").fetchone()[0]
        or 0.0
    )
    checks["duplicate_entity_roots_pct"] = (
        conn.execute("""
        SELECT 1.0*(COUNT(*) - COUNT(DISTINCT name))/NULLIF(COUNT(*),0)
        FROM entity_roots""").fetchone()[0]
        or 0.0
    )
    return checks


# --------- Pillar 4: Performance -----------------------------
def pillar4_performance(manifest, conn):
    # requires manifest.pipeline_wall_clock_seconds; else N/A
    return {
        "wall_clock_s": manifest.get("pipeline_wall_clock_seconds"),
        "per_stage_s": manifest.get("per_stage_wall_clock_seconds", {}),
        "sqlite_size_mb": Path(manifest["db_path"]).stat().st_size / 1e6
        if manifest.get("db_path")
        else None,
    }


# --------- Driver --------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--goldset", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--out", default="scorecard/")
    args = ap.parse_args()

    import yaml

    manifest = yaml.safe_load(open(args.manifest))
    gold = (
        [json.loads(line) for line in open(args.goldset)]
        if args.goldset.endswith(".jsonl")
        else json.load(open(args.goldset))
    )

    t0 = time.time()
    conn = sqlite3.connect(args.db)
    result = {
        "commit_sha": manifest["commit_sha"],
        "snapshot_ts": manifest["snapshot_ts"],
        "pillar_1_discovery": pillar1_discovery(conn, gold),
        "pillar_2_coverage": pillar2_coverage(conn),
        "pillar_3_accuracy": pillar3_accuracy(conn),
        "pillar_4_performance": pillar4_performance(manifest, conn),
        "elapsed_s": round(time.time() - t0, 2),
    }
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "scorecard.json").write_text(json.dumps(result, indent=2))
    print(
        json.dumps(
            {k: v for k, v in result.items() if not k.startswith("pillar_1")}, indent=2
        )
    )
    print(f"\nWrote {outdir / 'scorecard.json'}  in {result['elapsed_s']} s")


if __name__ == "__main__":
    main()
