"""
evaluation/failure_stats.py

Everything the completed runs say about failures, in one place.

Four views, each answering a different question:

  1. Confusion matrix   injected stage x attributed stage. The paper's central
                        table: not just how often attribution is right, but
                        where it goes when it is wrong.
  2. Per-family         failure rate and recall for each of the 17 Dr.Spider
                        perturbation families. Aggregates hide that families
                        within one group behave very differently.
  3. Failure modes      what "failure" actually consists of — an engine
                        rejection, an empty result, wrong rows, or a
                        column-order mismatch. These are not interchangeable
                        and only the first is unambiguous.
  4. Selector activity  split by whether the schema stage made any decision at
                        all. MAC-SQL prunes only above a size threshold, so on
                        most Spider databases the stage is inert, and a stage
                        that does not act cannot be blamed.

Reads whatever logs exist; runs no queries beyond re-executing predictions for
the failure-mode breakdown.

Usage:
    python evaluation/failure_stats.py
    python evaluation/failure_stats.py --system magsql
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from cross_system_report import GT_STAGE, attribute_baseline  # noqa: E402

STAGES = ["Intent", "Schema", "Skeleton", "Execution"]


def load(system: str, backbone: str) -> list:
    path = ROOT / "output_logs" / f"{system}_drspider" / backbone
    out = []
    for f in glob.glob(str(path / "drspider_*.json")):
        p = Path(f)
        if p.name.startswith("_"):
            continue
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    return out


def confusion(logs: list) -> None:
    print("\n" + "=" * 74)
    print("1. CONFUSION MATRIX — injected stage (row) x attributed stage (column)")
    print("=" * 74)
    print("   Passed = pipeline answered correctly despite perturbation;")
    print("   excluded from recall, since there is no failure to attribute.\n")

    tab = defaultdict(Counter)
    for d in logs:
        truth = GT_STAGE.get(d.get("target_error_type"))
        if not truth:
            continue
        tab[truth]["_n"] += 1
        if d.get("execution_match"):
            tab[truth]["Passed"] += 1
        else:
            tab[truth][attribute_baseline(d)] += 1

    hdr = f"   {'injected':<10}{'n':>6}" + "".join(f"{s:>11}" for s in STAGES)
    hdr += f"{'passed':>9}{'recall':>10}"
    print(hdr)
    print("   " + "-" * (len(hdr) - 3))
    for truth in STAGES:
        if truth not in tab:
            continue
        c = tab[truth]
        failed = c["_n"] - c["Passed"]
        row = f"   {truth:<10}{c['_n']:>6}"
        for s in STAGES:
            row += f"{c[s]:>11}"
        row += f"{c['Passed']:>9}"
        row += f"{c[truth]/failed*100:>9.1f}%" if failed else f"{'n/a':>10}"
        print(row)

    tot_f = sum(c["_n"] - c["Passed"] for c in tab.values())
    tot_h = sum(c[t] for t, c in tab.items())
    if tot_f:
        print(f"\n   overall: {tot_h}/{tot_f} = {tot_h/tot_f*100:.1f}% stage recall")


def per_family(logs: list) -> None:
    print("\n" + "=" * 74)
    print("2. PER-FAMILY — failure rate and recall for each perturbation family")
    print("=" * 74)

    fam = defaultdict(lambda: {"n": 0, "fail": 0, "hit": 0})
    for d in logs:
        truth = GT_STAGE.get(d.get("target_error_type"))
        if not truth:
            continue
        k = (d["perturbation_group"], d["source"].replace("drspider_", ""))
        fam[k]["n"] += 1
        if d.get("execution_match"):
            continue
        fam[k]["fail"] += 1
        if attribute_baseline(d) == truth:
            fam[k]["hit"] += 1

    print(f"   {'grp':<5}{'family':<28}{'n':>5}{'failed':>8}{'fail%':>8}{'recall':>10}")
    print("   " + "-" * 61)
    for k in sorted(fam):
        v = fam[k]
        r = f"{v['hit']/v['fail']*100:.1f}%" if v["fail"] else "n/a"
        print(f"   {k[0]:<5}{k[1]:<28}{v['n']:>5}{v['fail']:>8}"
              f"{v['fail']/v['n']*100:>7.1f}%{r:>10}")


def failure_modes(logs: list) -> None:
    print("\n" + "=" * 74)
    print("3. FAILURE MODES — what a 'failure' actually is")
    print("=" * 74)
    print("   Only an engine rejection is unambiguous. A wrong result set may")
    print("   still be a defensible reading of the question.\n")

    c = Counter()
    for d in logs:
        if d.get("execution_match"):
            continue
        rs = d.get("_rescore") or {}
        err = str((d.get("x10") or {}).get("error_message") or "")
        pred = d.get("final_sql") or ""
        if not pred.strip():
            c["no prediction produced"] += 1
        elif rs.get("would_match_unordered"):
            c["right values, wrong column order"] += 1
        elif "engine rejected" in rs.get("reason", ""):
            c["engine rejected the query"] += 1
        elif err and "gold failed" not in err:
            c["exception during the run"] += 1
        else:
            c["executed, different result"] += 1

    tot = sum(c.values()) or 1
    for k, v in c.most_common():
        print(f"   {k:<40}{v:>6}{v/tot*100:>8.1f}%")
    print(f"   {'total failures':<40}{tot:>6}")


def selector_activity(logs: list) -> None:
    print("\n" + "=" * 74)
    print("4. SCHEMA STAGE — did it decide anything?")
    print("=" * 74)
    print("   MAC-SQL prunes only when avg columns > 6 or total columns > 30.")
    print("   Below that the whole schema is passed through unchanged, so the")
    print("   stage emits no decision that attribution could be about.\n")

    st = defaultdict(lambda: {"n": 0, "fail": 0, "hit": 0, "fired": 0})
    for d in logs:
        truth = GT_STAGE.get(d.get("target_error_type"))
        if not truth:
            continue
        pruned = bool((d.get("x5") or {}).get("extracted_schema"))
        k = (d["perturbation_group"], "pruned" if pruned else "passed through")
        st[k]["n"] += 1
        if d.get("execution_match"):
            continue
        st[k]["fail"] += 1
        st[k]["fired"] += bool((d.get("x5") or {}).get("missing_gold_tables"))
        if attribute_baseline(d) == truth:
            st[k]["hit"] += 1

    print(f"   {'grp':<5}{'selector':<16}{'n':>6}{'failed':>8}{'recall':>9}"
          f"{'schema detector fired':>24}")
    print("   " + "-" * 68)
    for k in sorted(st):
        v = st[k]
        r = f"{v['hit']/v['fail']*100:.1f}%" if v["fail"] else "n/a"
        print(f"   {k[0]:<5}{k[1]:<16}{v['n']:>6}{v['fail']:>8}{r:>9}"
              f"{v['fired']:>24}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--system", default="macsql")
    ap.add_argument("--backbone", default="gpt-4o-mini")
    args = ap.parse_args()

    logs = load(args.system, args.backbone)
    if not logs:
        print(f"no logs for {args.system}/{args.backbone}")
        return

    passed = sum(1 for d in logs if d.get("execution_match"))
    print("=" * 74)
    print(f"FAILURE STATISTICS — {args.system} / {args.backbone} / Dr.Spider")
    print("=" * 74)
    print(f"   instances {len(logs)}   EX pass {passed} ({passed/len(logs)*100:.1f}%)"
          f"   failed {len(logs)-passed}")

    confusion(logs)
    per_family(logs)
    failure_modes(logs)
    if args.system == "macsql":
        selector_activity(logs)


if __name__ == "__main__":
    main()
