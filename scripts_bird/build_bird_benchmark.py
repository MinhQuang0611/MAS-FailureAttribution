"""
Build a BIRD benchmark in the same record shape the Dr.Spider runners consume.

WHAT BIRD ADDS, AND WHAT IT CANNOT
----------------------------------
BIRD carries no construction-time ground truth for *which stage* failed: its
instances are natural, not perturbed, so nothing certifies where a fault was
injected. Recall is therefore undefined on BIRD, and every record here is
emitted with `target_error_type: null` so the recall report skips it rather
than silently scoring against a label that does not exist.

What BIRD does give is the other half of the argument. The framework-attributed
stage distribution — the table whose reading this study disputes — has so far
been measured on one dataset, one engine, and one pipeline the authors wrote
themselves. Reproducing it on BIRD, on two published baselines, tests whether
the downstream concentration is a property of state-differencing or of that
particular setup.

BIRD also activates the `x2` external-knowledge slot, dormant under Spider:
its `evidence` field is exactly the domain hint that slot was designed for.

Usage:
    python scripts_bird/build_bird_benchmark.py --n 0      # all 1,534 (default)
    python scripts_bird/build_bird_benchmark.py --n 500    # stratified subsample
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "bird"
OUT = ROOT / "data" / "bird_benchmark"


def locate() -> tuple[Path, Path, Path]:
    """Find dev.json, dev_tables.json and dev_databases/ inside the extract.

    The BIRD release has shipped under several directory names (`dev/`,
    `dev_20240627/`); globbing for the files themselves survives that.
    """
    def one(pattern: str, kind: str) -> Path:
        hits = sorted(RAW.rglob(pattern))
        # A nested re-zip can leave a second copy; the shallowest is the real one.
        hits = [h for h in hits if "__MACOSX" not in str(h)]
        if not hits:
            raise SystemExit(f"{kind} not found under {RAW} (looked for {pattern})")
        return min(hits, key=lambda p: len(p.parts))

    dev = one("dev.json", "dev.json")
    tables = one("dev_tables.json", "dev_tables.json")
    dbs = one("dev_databases", "dev_databases/")
    return dev, tables, dbs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=0,
                    help="instances to sample, stratified by difficulty; 0 = all")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    dev, tables, dbs = locate()
    recs = json.loads(dev.read_text(encoding="utf-8"))
    print(f"  source: {dev.relative_to(ROOT)}  ({len(recs)} instances)")

    # Keep only instances whose database is actually present, so a missing
    # download surfaces here rather than as a run of unexplained failures.
    present = {p.name for p in dbs.iterdir() if p.is_dir()}
    usable = [r for r in recs if r["db_id"] in present]
    if len(usable) != len(recs):
        print(f"  !! {len(recs) - len(usable)} instances dropped: database missing")

    by_diff: dict[str, list] = {}
    for r in usable:
        by_diff.setdefault(r.get("difficulty", "unknown"), []).append(r)

    rng = random.Random(args.seed)
    if args.n and args.n < len(usable):
        # Stratified: preserve BIRD's difficulty mix instead of letting a
        # uniform sample drift toward whichever tier is largest.
        chosen = []
        for diff, items in sorted(by_diff.items()):
            k = round(args.n * len(items) / len(usable))
            chosen += rng.sample(items, min(k, len(items)))
        rng.shuffle(chosen)
        chosen = chosen[:args.n]
    else:
        chosen = usable

    out_recs = []
    for i, r in enumerate(chosen):
        # Id from BIRD's own question_id, NOT from the position in this sample.
        # A positional id silently changes meaning whenever --n or --seed
        # changes: rebuilding 499 -> 1,534 reassigned every id, so each stored
        # log would have been paired with a different question, with no error
        # raised. Keying on question_id makes an id mean one instance forever,
        # so a sample can be widened and previously collected logs stay valid.
        qid = r.get("question_id", i)
        out_recs.append({
            "id": f"bird_dev_q{qid}",
            "source": "bird_dev",
            "perturbation_group": "BIRD",
            # No perturbation was injected, so no stage is under stress by
            # construction. Null is load-bearing: it keeps these records out
            # of the recall denominator.
            "target_error_type": None,
            "db_id": r["db_id"],
            "question": r["question"],
            "evidence": r.get("evidence", ""),
            "gold_sql": r["SQL"],
            "query": r["SQL"],
            "difficulty": r.get("difficulty", "unknown"),
            "bird_question_id": r.get("question_id", i),
        })

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "bird_dev.json").write_text(
        json.dumps(out_recs, ensure_ascii=False, indent=1), encoding="utf-8")

    manifest = {
        "dataset": "bird",
        "seed": args.seed,
        "total_instances": len(out_recs),
        "sets": [{
            "set": "bird_dev",
            "group": "BIRD",
            "target_stage": None,
            "available": len(usable),
            "sampled": len(out_recs),
            "db_dir": str(dbs.relative_to(ROOT)),
            "tables_json": str(tables.relative_to(ROOT)),
            "benchmark_file": "data/bird_benchmark/bird_dev.json",
        }],
    }
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    mix = Counter(r["difficulty"] for r in out_recs)
    print(f"  wrote {len(out_recs)} instances to {OUT.relative_to(ROOT)}")
    print(f"  difficulty mix: {dict(mix)}")
    print(f"  databases: {len(present)}  at {dbs.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
