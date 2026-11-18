"""
evaluation/rescore.py

Re-score stored predictions with a corrected result comparator. No API calls:
every log already contains its predicted SQL, so the verdict is recomputable.

THE BUG THIS FIXES
------------------
`execution_match` compared whole rows after `str()` on the tuple:

    sorted(map(str, pred_rows)) == sorted(map(str, gold_rows))

Stringifying the tuple embeds Python's repr of each value, so a column typed
TEXT in one table and INTEGER in another makes identical answers differ:

    "('3', 'Arjen Robben', 1)"   !=   "(3, 'Arjen Robben', 1)"

Both queries return visitor 3 with the same name and level. The pipeline was
right and the evaluator said otherwise. Every such instance entered the failure
denominator of every recall and distribution number in this study.

WHAT COUNTS AS A MATCH
----------------------
Rows are compared as a multiset. Within a row, values are normalised before
comparison: numeric strings and numbers unify, floats round to 6 places, NULL
is its own token. Column ORDER remains significant, following Spider's
convention that the SELECT list is part of what was asked. Because that choice
is contestable, a permissive variant ignoring column order is computed
alongside it and reported, so the sensitivity of every result to that decision
is visible rather than assumed.

Usage:
    python evaluation/rescore.py --dry-run     # report only
    python evaluation/rescore.py               # rewrite verdicts in place
"""

from __future__ import annotations

import argparse
import glob
import json
import sqlite3
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPARATOR_VERSION = 2


def norm(v) -> str:
    """One value, comparably.

    The point is to unify representations of the same answer without unifying
    genuinely different answers: '3' and 3 are the same visitor id, 3 and 3.5
    are not.
    """
    if v is None:
        return "\x00NULL"
    if isinstance(v, bytes):
        try:
            v = v.decode(errors="ignore")
        except Exception:
            return repr(v)
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        f = float(v)
        return str(int(f)) if f.is_integer() else f"{f:.6f}"
    s = str(v).strip()
    try:                       # numeric strings unify with numbers
        f = float(s)
        return str(int(f)) if f.is_integer() else f"{f:.6f}"
    except ValueError:
        return s


def rows(db: Path, sql: str, timeout: float = 30.0):
    try:
        con = sqlite3.connect(str(db), timeout=timeout)
        con.text_factory = lambda b: b.decode(errors="ignore")
        con.execute("PRAGMA busy_timeout = 15000")
        return con.execute(sql).fetchall(), None
    except Exception as e:
        return None, str(e)


def compare(p, g) -> tuple[bool, bool]:
    """-> (ordered, unordered) column-order sensitive and permissive verdicts."""
    ordered = sorted(tuple(norm(v) for v in r) for r in p) == \
        sorted(tuple(norm(v) for v in r) for r in g)
    unordered = sorted(tuple(sorted(norm(v) for v in r)) for r in p) == \
        sorted(tuple(sorted(norm(v) for v in r)) for r in g)
    return ordered, unordered


def db_path_for(d: dict) -> Path | None:
    db_id = (d.get("x1") or {}).get("db_id")
    if not db_id:
        return None
    for base in ("data/databases",
                 "data/raw/drspider/Spider-dev/databases",
                 "data/raw/bird/dev_20240627/dev_databases"):
        p = ROOT / base / db_id / f"{db_id}.sqlite"
        if p.exists():
            return p
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    c = Counter()
    flips = []
    for f in sorted(glob.glob(str(ROOT / "output_logs" / "*_*" / "*" / "*.json"))):
        p = Path(f)
        if p.name.startswith("_") or "api_trace" in p.name:
            continue
        # Archived trees (quarantined, superseded, pre-migration backups) live
        # under an underscore-prefixed directory and are not part of any
        # result. Every other analysis script skips them; re-scoring them here
        # inflated the reported verdict denominator with retired runs.
        if any(part.startswith("_") for part in p.parts):
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if "x1" not in d:
            continue

        c["logs"] += 1
        old = bool(d.get("execution_match"))
        pred = d.get("final_sql") or ""
        db = db_path_for(d)

        if not pred.strip() or db is None:
            new = False
            reason = "no prediction" if not pred.strip() else "database not found"
            unordered = False
        else:
            pr, pe = rows(db, pred)
            gr, ge = rows(db, d.get("gold_sql") or "")
            if pe:
                new, unordered, reason = False, False, f"engine rejected: {pe[:80]}"
            elif ge:
                new, unordered, reason = False, False, f"gold failed: {ge[:80]}"
            else:
                new, unordered = compare(pr, gr)
                reason = "match" if new else (
                    "same values, different column order" if unordered
                    else "different result")

        c[f"{old} -> {new}"] += 1
        if unordered and not new:
            c["would match if column order ignored"] += 1
        if new != old:
            flips.append((p.name, old, new, reason))
            if not args.dry_run:
                d["execution_match_original"] = old
                d["execution_match"] = new
                d["x11"] = {"execution_match": new}
                d["_rescore"] = {"comparator": COMPARATOR_VERSION,
                                 "reason": reason,
                                 "column_order_sensitive": True,
                                 "would_match_unordered": unordered}
                p.write_text(json.dumps(d, ensure_ascii=False, indent=1),
                             encoding="utf-8")

    print("=" * 70)
    print("RE-SCORE" + ("  (dry run — nothing written)" if args.dry_run else ""))
    print("=" * 70)
    for k, v in c.most_common():
        print(f"  {k:<46} {v}")
    print(f"\n  verdicts changed: {len(flips)}")
    for name, old, new, reason in flips[:10]:
        print(f"    {name:<44} {old} -> {new}   {reason}")
    if len(flips) > 10:
        print(f"    ... and {len(flips) - 10} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
