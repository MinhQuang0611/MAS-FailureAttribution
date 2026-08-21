"""
Run the Dr.Spider attribution-bias experiment set by set.

Ordering is deliberate. NLQ_* runs first because those 9 sets carry Intent-stage
ground truth, and Intent is the stage where the shipped heuristic scores 0%
recall — so the sets that can overturn (or confirm) the central finding are the
ones that finish first. SQL_* follows (Skeleton ground truth, a stage the
heuristic over-attributes to), and DB_* last (Schema ground truth, already
covered at n=198 by the existing mini_benchmark).

Resume is on: main.py checkpoints every sample keyed by "<dataset>::<id>", so
re-running this script after an interruption picks up where it stopped and
re-executes nothing. Killing it mid-run is safe.

    python scripts_drspider/run_drspider.py            # all 17 sets
    python scripts_drspider/run_drspider.py --groups NLQ
    python scripts_drspider/run_drspider.py --limit 10 # smoke test per set
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / "data" / "drspider_benchmark"
OUTPUT = "output_logs/drspider"

GROUP_ORDER = {"NLQ": 0, "SQL": 1, "DB": 2}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--groups", nargs="*", default=["NLQ", "SQL", "DB"])
    ap.add_argument("--limit", type=int, default=None,
                    help="cap instances per set (smoke test)")
    ap.add_argument("--model", default="gpt-4o")
    args = ap.parse_args()

    man = json.loads((BENCH / "manifest.json").read_text(encoding="utf-8"))
    sets = [s for s in man["sets"] if s["group"] in args.groups]
    sets.sort(key=lambda s: (GROUP_ORDER.get(s["group"], 9), s["set"]))

    planned = sum(min(s["sampled"], args.limit or s["sampled"]) for s in sets)
    print(f"Dr.Spider run — {len(sets)} sets, {planned} instances planned")
    print(f"output: {OUTPUT}\n")

    t0 = time.time()
    failed = []
    for i, s in enumerate(sets, 1):
        name = s["set"]
        print(f"[{i}/{len(sets)}] {name}  ({s['group']} -> {s['target_stage']})",
              flush=True)
        cmd = [sys.executable, "main.py",
               "--benchmark", s["benchmark_file"],
               "--db-dir", s["db_dir"],
               "--output-dir", OUTPUT,
               "--model", args.model,
               "--use-nlsql",
               "--resume"]
        cmd += ["--limit", str(args.limit)] if args.limit else ["--all"]

        r = subprocess.run(cmd, cwd=ROOT)
        if r.returncode != 0:
            print(f"    !! exit {r.returncode}", flush=True)
            failed.append(name)

        el = time.time() - t0
        print(f"    elapsed {el/60:.1f} min\n", flush=True)

    print(f"done in {(time.time()-t0)/60:.1f} min")
    if failed:
        print(f"failed sets: {failed}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
