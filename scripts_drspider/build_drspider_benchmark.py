"""
Build benchmark JSON files for the Dr.Spider attribution-bias experiment.

Dr.Spider ships 17 perturbation families in three groups. We map each group to
the pipeline stage it places under stress, which gives construction-time ground
truth on THREE stages (the existing mini_benchmark covers only two):

    DB_*   -> the schema is renamed / abbreviated / content-shifted, so the
              schema-linking agent is the stage under stress   -> Schema
    NLQ_*  -> the question is reworded while the gold SQL is preserved, so the
              intent-understanding agent is under stress        -> Intent
    SQL_*  -> the required query structure changes (comparison, sort order,
              numeric handling), so the skeleton agent is under stress
                                                                -> Skeleton

IMPORTANT — this mapping is our design assumption, not a label supplied by the
Dr.Spider authors. It has the same epistemic status as the perturbation design
in data/mini_benchmark.json: it certifies where the fault was *injected*, not
that the pipeline necessarily failed there. State it as such in the paper.

Databases:
  - DB_*  sets carry their own perturbed SQLite files (db_ids are suffixed,
          e.g. concert_singer_0) under <set>/database_post_perturbation/
  - NLQ_* and SQL_* sets reuse the unmodified Spider-dev databases.
Both already follow the {db_dir}/{db_id}/{db_id}.sqlite layout the pipeline
expects, so no file copying is required — only a per-set --db-dir.

Usage:
    python scripts_drspider/build_drspider_benchmark.py --per-set 100
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "raw" / "drspider"
OUT = ROOT / "data" / "drspider_benchmark"

STAGE_OF_GROUP = {
    "DB": "Schema Linking Error",
    "NLQ": "Intent / Ambiguity Error",
    "SQL": "Skeleton / Structural Error",
}


def discover() -> list[tuple[str, str, Path]]:
    """Return (set_name, group, path) for each perturbation set."""
    out = []
    for d in sorted(SRC.iterdir()):
        if not d.is_dir() or d.name == "Spider-dev":
            continue
        if not (d / "questions_post_perturbation.json").exists():
            continue
        group = d.name.split("_")[0]
        if group not in STAGE_OF_GROUP:
            print(f"  ! skipping {d.name}: unknown group '{group}'")
            continue
        out.append((d.name, group, d))
    return out


def db_dir_for(set_name: str, group: str) -> str:
    if group == "DB":
        return f"data/raw/drspider/{set_name}/database_post_perturbation"
    return "data/raw/drspider/Spider-dev/databases"


def build(per_set: int, seed: int) -> None:
    rng = random.Random(seed)
    OUT.mkdir(parents=True, exist_ok=True)

    manifest = []
    total = 0

    for set_name, group, path in discover():
        items = json.loads((path / "questions_post_perturbation.json")
                           .read_text(encoding="utf-8"))
        n_avail = len(items)
        sample = items if n_avail <= per_set else rng.sample(items, per_set)

        db_root = ROOT / db_dir_for(set_name, group)
        records, skipped = [], 0
        for i, it in enumerate(sample):
            db_id = it["db_id"]
            if not (db_root / db_id / f"{db_id}.sqlite").exists():
                skipped += 1
                continue
            gold = (it.get("query") or "").strip()
            records.append({
                "id": f"drspider_{set_name}_{i}",
                "source": f"drspider_{set_name}",
                "perturbation_group": group,
                "db_id": db_id,
                "question": it["question"],
                "gold_sql": gold,
                "query": gold,
                "target_error_type": STAGE_OF_GROUP[group],
                "q_id_spider_dev": it.get("q_id_spider_dev"),
            })

        out_file = OUT / f"{set_name}.json"
        out_file.write_text(json.dumps(records, indent=2, ensure_ascii=False),
                            encoding="utf-8")
        total += len(records)
        manifest.append({
            "set": set_name,
            "group": group,
            "target_stage": STAGE_OF_GROUP[group],
            "available": n_avail,
            "sampled": len(records),
            "skipped_missing_db": skipped,
            "db_dir": db_dir_for(set_name, group),
            "benchmark_file": str(out_file.relative_to(ROOT)).replace("\\", "/"),
        })
        flag = f"  ({skipped} skipped: db missing)" if skipped else ""
        print(f"  {set_name:<28} {group:<4} {len(records):>4}/{n_avail:<5}{flag}")

    (OUT / "manifest.json").write_text(
        json.dumps({"seed": seed, "per_set": per_set,
                    "total_instances": total, "sets": manifest},
                   indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n  total instances: {total}")
    print(f"  manifest       : {OUT / 'manifest.json'}")
    by_stage: dict[str, int] = {}
    for m in manifest:
        by_stage[m["target_stage"]] = by_stage.get(m["target_stage"], 0) + m["sampled"]
    print("\n  ground truth coverage by stage:")
    for k, v in sorted(by_stage.items()):
        print(f"    {k:<30} {v:>5}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-set", type=int, default=100,
                    help="max instances sampled from each perturbation set")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    print(f"Building Dr.Spider benchmark (per-set={args.per_set}, seed={args.seed})\n")
    build(args.per_set, args.seed)
