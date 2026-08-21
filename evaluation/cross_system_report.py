"""
evaluation/cross_system_report.py

Compare failure attribution across the three pipelines on the same instances.

The three systems differ in one respect that this study argues is decisive —
whether their intermediate artifacts are real observations or re-derivations of
the final SQL:

    nlsql    4 nominal stages, but x7/x8 are regex extractions of the final
             query, so the skeleton detector cannot fire and attribution
             collapses onto a residual bucket
    MAC-SQL  3 agents; the Selector's pruned schema is real (produced before
             any SQL), but intent and query construction share one Decomposer
    MAG-SQL  4 agents; schema, sub-question decomposition, generation and
             repair are all separate artifacts

If stage recall improves along that ordering, attribution bias is a property of
instrumentation quality rather than an inherent limit of state-differencing —
which is the claim this report is built to test.

Reads whatever logs exist; systems with no logs are skipped.
"""

from __future__ import annotations

import json
import glob
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SYSTEMS = {
    "nlsql":   ROOT / "output_logs" / "drspider",
    "MAC-SQL": ROOT / "output_logs" / "macsql_drspider",
    "MAG-SQL": ROOT / "output_logs" / "magsql_drspider",
}

GT_STAGE = {
    "Intent / Ambiguity Error": "Intent",
    "Schema Linking Error": "Schema",
    "Skeleton / Structural Error": "Skeleton",
}
STAGES = ["Intent", "Schema", "Skeleton", "Execution"]

# nlsql logs carry a classifier label; the baseline logs carry raw artifacts.
NLSQL_LABEL_STAGE = {
    "intent_missing_constraint": "Intent", "intent_misinterpreted": "Intent",
    "intent_hallucinated": "Intent",
    "schema_missing_table": "Schema", "schema_missing_column": "Schema",
    "schema_hallucinated_element": "Schema",
    "schema_wrong_column_mapping": "Schema",
    "sql_wrong_aggregation": "Skeleton", "sql_missing_clause": "Skeleton",
    "sql_wrong_join": "Skeleton", "exec_wrong_result": "Skeleton",
    "exec_empty_result": "Skeleton",
    "exec_syntax_error": "Execution", "repair_introduced_new_error": "Execution",
    "correct": "Correct", "unknown": "Unknown", "none": "Unknown",
}


def attribute_baseline(d: dict) -> str:
    """Stage attribution using the baseline's REAL per-agent artifacts.

    Deliberately simple and upstream-first: each test looks at the artifact the
    stage itself produced, not at the final query. A stage is only blamed when
    its own output is demonstrably wrong.
    """
    x10 = d.get("x10") or {}
    x5 = d.get("x5") or {}

    # Schema: a gold table the selector never passed downstream. Checked first
    # because it is the only test whose evidence is fully independent of the
    # generated SQL.
    if x5.get("missing_gold_tables"):
        return "Schema"

    # Execution: the engine itself rejected the query, or the refiner had to
    # intervene and still failed.
    err = x10.get("error_message")
    if err and "gold failed" not in str(err):
        return "Execution"

    # Intent vs Skeleton: if an explicit decomposition exists and omits the
    # question's content words, the misunderstanding is upstream of generation.
    x3 = d.get("x3") or {}
    decomp = x3.get("subquery_list") or x3.get("qa_pairs")
    q = (d.get("x1") or {}).get("question", "").lower()
    if decomp and q:
        import re
        stop = {"the", "a", "an", "of", "in", "on", "for", "to", "and", "or",
                "what", "which", "who", "how", "many", "much", "is", "are",
                "list", "show", "give", "find", "all", "with", "by", "that"}
        qw = {w for w in re.findall(r"[a-z]{3,}", q) if w not in stop}
        dw = set(re.findall(r"[a-z]{3,}", json.dumps(decomp).lower()))
        if qw and len(qw & dw) / len(qw) < 0.5:
            return "Intent"

    return "Skeleton"


def _norm(s) -> str:
    return " ".join(str(s or "").split()).lower()


def load_ground_truth() -> dict:
    """(db_id, question) -> target stage.

    The baseline runners stamp `target_error_type` into each log, but the nlsql
    pipeline writes StateTracker logs that carry no benchmark metadata, so its
    ground truth has to be joined back through the benchmark. Without this the
    nlsql rows silently vanish from the comparison — and nlsql is the control
    the whole comparison rests on.
    """
    man = json.loads(
        (ROOT / "data/drspider_benchmark/manifest.json").read_text(encoding="utf-8"))
    gt = {}
    for s in man["sets"]:
        for r in json.loads((ROOT / s["benchmark_file"]).read_text(encoding="utf-8")):
            gt[(_norm(r["db_id"]), _norm(r["question"]))] = \
                GT_STAGE[r["target_error_type"]]
    return gt


def load(system: str, path: Path, gt: dict) -> list:
    out = []
    for f in glob.glob(str(path / "*.json")):
        if "resume_checkpoint" in f or Path(f).name.startswith("_"):
            continue
        try:
            d = json.loads(Path(f).read_text(encoding="utf-8"))
        except Exception:
            continue
        truth = GT_STAGE.get(d.get("target_error_type"))
        if not truth:
            x1 = d.get("x1") or {}
            truth = gt.get((_norm(x1.get("db_id")), _norm(x1.get("question"))))
        if not truth:
            continue
        if d.get("execution_match"):
            out.append((truth, "Correct"))
            continue
        if system == "nlsql":
            pred = NLSQL_LABEL_STAGE.get(
                str(d.get("annotated_root_cause") or "none").lower(), "Unknown")
        else:
            pred = attribute_baseline(d)
        out.append((truth, pred))
    return out


def main() -> None:
    print("=" * 78)
    print("CROSS-SYSTEM ATTRIBUTION — Dr.Spider, construction-time ground truth")
    print("=" * 78)

    gt = load_ground_truth()
    summary = []
    for system, path in SYSTEMS.items():
        if not path.exists():
            continue
        recs = load(system, path, gt)
        if not recs:
            continue

        failed = [(t, p) for t, p in recs if p != "Correct"]
        passed = len(recs) - len(failed)
        per = defaultdict(lambda: [0, 0])
        for t, p in failed:
            per[t][1] += 1
            if p == t:
                per[t][0] += 1

        print(f"\n### {system}   n={len(recs)}  "
              f"(EX pass {passed}, failed {len(failed)})")
        if not failed:
            print("   no failures yet")
            continue

        dist = Counter(p for _, p in failed)
        print("   attributed to: " + "  ".join(
            f"{s}={dist.get(s,0)} ({dist.get(s,0)/len(failed)*100:.0f}%)"
            for s in STAGES + ["Unknown"] if dist.get(s)))

        cells = []
        for st in STAGES:
            hit, tot = per.get(st, [0, 0])
            cells.append(f"{st}={hit}/{tot}={hit/tot*100:.1f}%" if tot else f"{st}=n/a")
        print("   recall: " + "  ".join(cells))

        hit_all = sum(v[0] for v in per.values())
        tot_all = sum(v[1] for v in per.values())
        overall = hit_all / tot_all * 100 if tot_all else 0.0
        print(f"   OVERALL RECALL: {hit_all}/{tot_all} = {overall:.1f}%")
        summary.append((system, len(recs), overall))

    if len(summary) > 1:
        print("\n" + "=" * 78)
        print("SUMMARY — recall vs artifact independence")
        print("=" * 78)
        print(f"   {'system':<10} {'n':>6} {'stage recall':>14}")
        print("   " + "-" * 32)
        for s, n, r in summary:
            print(f"   {s:<10} {n:>6} {r:>13.1f}%")
        print("\n   Ordering by artifact independence: nlsql (re-derived) <")
        print("   MAC-SQL (schema real, intent merged) < MAG-SQL (all separate).")
        print("   Recall rising along that order supports the instrumentation")
        print("   hypothesis; flat recall supports the inherent-limit hypothesis.")


if __name__ == "__main__":
    main()
