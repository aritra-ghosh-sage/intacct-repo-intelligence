def check_entity_recall_v2(conn):
    """
    Measure entity recall using normalized name matching.

    Normalization strips case and underscores/hyphens because
    entity_nodes.name may store 'BS_ActivityLog' while gold set
    may store 'BSActivityLog' — same entity, different convention.
    """
    import json
    from pathlib import Path

    # 1. Load gold set from independent-source JSONL
    gold_path = Path("gold_entities_v2.jsonl")
    gold = set()
    with gold_path.open() as f:
        for line in f:
            entry = json.loads(line)
            gold.add(entry["name"])

    # 2. Load discovered entities from the database
    discovered = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM entity_nodes WHERE name IS NOT NULL"
        ).fetchall()
    }

    # 3. Normalize for matching
    def normalize(name):
        return name.lower().replace("_", "").replace("-", "")

    # Map normalized -> original for both sets
    gold_norm_to_orig = {normalize(n): n for n in gold}
    disc_norm_to_orig = {normalize(n): n for n in discovered}

    gold_norm_keys = set(gold_norm_to_orig)
    disc_norm_keys = set(disc_norm_to_orig)

    # 4. Compute intersection and difference on normalized keys
    matched_norm = gold_norm_keys & disc_norm_keys
    missing_norm = gold_norm_keys - disc_norm_keys

    # 5. Report using original gold-set spellings
    matched_names = sorted(gold_norm_to_orig[n] for n in matched_norm)
    missing_names = sorted(gold_norm_to_orig[n] for n in missing_norm)

    # 6. Show what discovered name each gold entry matched to
    match_details = {
        gold_norm_to_orig[n]: disc_norm_to_orig[n]
        for n in matched_norm
        if gold_norm_to_orig[n] != disc_norm_to_orig[n]
    }

    recall = len(matched_norm) / len(gold_norm_keys) if gold_norm_keys else 0.0

    return {
        "gold_size": len(gold),
        "discovered_size": len(discovered),
        "matched": len(matched_norm),
        "matched_names": matched_names,
        "missing_names": missing_names,
        "renamed_matches": match_details,  # gold_name -> discovered_name
        "recall_percent": round(recall * 100, 2),
    }


import sqlite3

path = "../catalog/catalog.db"
conn = sqlite3.connect(path)
conn.row_factory = sqlite3.Row

print(check_entity_recall_v2(conn))
