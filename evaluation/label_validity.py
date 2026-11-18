"""
evaluation/label_validity.py

Does a Dr.Spider failure actually fail at the stage its label names?

WHY THIS EXISTS
---------------
Dr.Spider fixes the stressed stage at construction time, and every recall
figure in this study treats that as ground truth. The assumption is load
bearing and, until now, untested: a DB-perturbed instance is labelled
"Schema Linking Error" because the *database* was perturbed, not because the
schema stage was observed to fail. If the model links the renamed columns
correctly and then writes the wrong query shape, the instance is a Skeleton
failure carrying a Schema label, and scoring attribution against that label
punishes the detector for being right.

WHAT IT MEASURES
----------------
For each failure we compare the predicted query against the gold query on two
axes that are cheap and independent of any attribution rule:

    columns   the set of real column names each query references
    shape     (has join, group by, having, order by, aggregate)

    columns differ, shape same   -> the query selected the wrong fields
                                    => consistent with a Schema/linking fault
    columns same, shape differs  -> right fields, wrong construction
                                    => consistent with a Skeleton fault
    both same                    -> a value-level or semantic difference that
                                    neither axis captures
    both differ                  -> ambiguous; reported, never assigned

LIMITS, STATED PLAINLY
----------------------
This is a triage heuristic, not a parser. Column names are matched globally
rather than resolved per table, so a name occurring in two tables is counted
once; aliases introduced by AS are excluded but alias-qualified references are
not resolved back to their table. It is therefore evidence about the *rate* at
which labels and observed failures disagree, and is not reliable on any single
instance. It reads the generated SQL, so it is NOT an attribution rule and must
never be used as one -- it exists to audit the labels, not to replace them.

Usage:
    python evaluation/label_validity.py
"""
from __future__ import annotations

import glob
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evaluation"))
from cross_system_report import db_columns, GT_STAGE  # noqa: E402

KW = set("""select from where group by order having limit join on as and or not in is
null asc desc count sum avg min max distinct left right inner outer union all between
like exists case when then else end intersect except cast substr lower upper abs round
strftime date julianday length coalesce iif""".split())


def _clean(sql: str) -> str:
    return re.sub(r"'[^']*'", " ", sql or "").replace("`", " ").replace('"', " ")


def columns_referenced(sql: str, cols: set) -> set:
    """Real column names the query mentions, aliases excluded."""
    s = _clean(sql)
    alias = set(re.findall(r"\bas\s+([a-z_][a-z_0-9]*)", s.lower()))
    toks = {t.lower() for t in re.findall(r"[A-Za-z_][A-Za-z_0-9]*", s)}
    return (toks - KW - alias) & cols


def shape(sql: str) -> tuple:
    low = _clean(sql).lower()
    return ("join" in low, "group by" in low, "having" in low, "order by" in low,
            bool(re.search(r"\b(count|sum|avg|min|max)\s*\(", low)))


VERDICTS = ["wrong columns, same shape", "same columns, wrong shape",
            "columns and shape both match", "both differ"]


def classify(d: dict):
    sch = db_columns((d.get("x1") or {}).get("db_id", ""))
    if not sch:
        return None
    cols = {c.lower() for v in sch.values() for c in v}
    same_c = columns_referenced(d.get("gold_sql"), cols) == \
        columns_referenced(d.get("final_sql"), cols)
    same_s = shape(d.get("gold_sql")) == shape(d.get("final_sql"))
    if not same_c and same_s:
        return VERDICTS[0]
    if same_c and not same_s:
        return VERDICTS[1]
    if same_c and same_s:
        return VERDICTS[2]
    return VERDICTS[3]


CONSISTENT = {"Schema": VERDICTS[0], "Skeleton": VERDICTS[1]}


def main() -> None:
    print("=" * 78)
    print("LABEL VALIDITY — does the failure look like the stage its label names")
    print("=" * 78)
    print(__doc__.split("LIMITS")[1].split("Usage")[0].strip()[:0] or "", end="")

    for path in sorted((ROOT / "output_logs").glob("macsql_drspider/*/")):
        if any(p.startswith("_") for p in path.parts):
            continue
        rows = []
        for f in sorted(glob.glob(str(path / "*.json"))):
            p = Path(f)
            if p.name.startswith("_") or "api_trace" in p.name:
                continue
            d = json.loads(p.read_text(encoding="utf-8"))
            if "x1" not in d or d.get("execution_match"):
                continue
            truth = GT_STAGE.get(d.get("target_error_type"))
            v = classify(d)
            if truth and v:
                rows.append((truth, v))
        if not rows:
            continue

        print(f"\n### {path.parent.name} / {path.name}   {len(rows)} failures")
        print(f"   {'label':<10}{'n':>5}  " +
              "".join(f"{v[:26]:>28}" for v in VERDICTS))
        for truth in ["Intent", "Schema", "Skeleton"]:
            sub = [v for t, v in rows if t == truth]
            if not sub:
                continue
            c = Counter(sub)
            line = f"   {truth:<10}{len(sub):>5}  "
            for v in VERDICTS:
                line += f"{c[v]:>20} ({c[v]/len(sub)*100:4.0f}%)"[-28:]
            print(line)
            want = CONSISTENT.get(truth)
            if want:
                print(f"   {'':<10}{'':>5}  -> consistent with its own label: "
                      f"{c[want]}/{len(sub)} = {c[want]/len(sub)*100:.0f}%")


if __name__ == "__main__":
    main()
