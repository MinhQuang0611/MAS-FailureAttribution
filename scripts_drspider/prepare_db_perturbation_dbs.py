"""
Stage the perturbed SQLite databases for the three Dr.Spider DB_* sets.

The three DB_* sets reuse the same db_id names (e.g. concert_singer_0) but ship
DIFFERENT database content for each — verified by checksum. Copying them into a
shared directory under their original names would therefore silently overwrite
one set's schema with another's, so every database is namespaced by its set:

    data/databases/<db_id>__<set_suffix>/<db_id>__<set_suffix>.sqlite

The corresponding benchmark JSON is rewritten to reference the namespaced id,
and the original id is preserved in `orig_db_id` for traceability.

Only databases actually referenced by the sampled benchmark records are copied,
which keeps this to a few hundred MB instead of ~690 MB.

Run AFTER build_drspider_benchmark.py:
    python scripts_drspider/prepare_db_perturbation_dbs.py
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / "data" / "drspider_benchmark"
DEST = ROOT / "data" / "databases"

SUFFIX = {
    "DB_DBcontent_equivalence": "dbcontent",
    "DB_schema_abbreviation": "abbrev",
    "DB_schema_synonym": "synonym",
}


def main() -> None:
    man = json.loads((BENCH / "manifest.json").read_text(encoding="utf-8"))
    changed = False
    total_copied = total_bytes = 0

    for s in man["sets"]:
        if s["group"] != "DB":
            continue
        set_name = s["set"]
        suffix = SUFFIX[set_name]
        src_root = ROOT / s["db_dir"]
        bench_file = ROOT / s["benchmark_file"]
        recs = json.loads(bench_file.read_text(encoding="utf-8"))

        copied = skipped = 0
        for r in recs:
            orig = r.get("orig_db_id") or r["db_id"]
            new_id = f"{orig}__{suffix}"

            src = src_root / orig / f"{orig}.sqlite"
            dst_dir = DEST / new_id
            dst = dst_dir / f"{new_id}.sqlite"

            if not src.exists():
                skipped += 1
                continue
            if not dst.exists():
                dst_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                copied += 1
                total_bytes += dst.stat().st_size

            r["orig_db_id"] = orig
            r["db_id"] = new_id

        bench_file.write_text(json.dumps(recs, indent=2, ensure_ascii=False),
                              encoding="utf-8")
        # these sets now resolve against the shared pipeline db dir
        s["db_dir"] = "data/databases"
        s["namespaced_suffix"] = suffix
        changed = True
        total_copied += copied
        print(f"  {set_name:<28} copied {copied:>3} db(s)"
              + (f", {skipped} missing" if skipped else ""))

    if changed:
        (BENCH / "manifest.json").write_text(
            json.dumps(man, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n  {total_copied} databases staged, {total_bytes/1e6:.1f} MB")
    print(f"  all 17 sets now resolve against {DEST.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
