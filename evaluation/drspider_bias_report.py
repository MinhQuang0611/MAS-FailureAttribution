"""
evaluation/drspider_bias_report.py

Score attribution against Dr.Spider's construction-time ground truth.

Two questions, one report:

  1. Does the downstream bias measured on the 400-instance mini_benchmark hold
     across 17 independent fault families and three stages? (per-set recall)

  2. Is the bias caused by the SCAN ORDER or by the DETECTORS? Re-scoring the
     same saved logs under alternative strategies answers this without a single
     extra API call, because attribution is a pure function of x1..x13.

Run after scripts_drspider/run_drspider.py:
    python evaluation/drspider_bias_report.py
"""

from __future__ import annotations

import json
import glob
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / "data" / "drspider_benchmark"
LOGS = ROOT / "output_logs" / "drspider"

STAGE_OF_LABEL = {
    "intent_missing_constraint": "Intent",
    "intent_misinterpreted": "Intent",
    "intent_hallucinated": "Intent",
    "schema_missing_table": "Schema",
    "schema_missing_column": "Schema",
    "schema_hallucinated_element": "Schema",
    "schema_wrong_column_mapping": "Schema",
    "sql_wrong_aggregation": "Skeleton",
    "sql_missing_clause": "Skeleton",
    "sql_wrong_join": "Skeleton",
    "exec_wrong_result": "Skeleton",
    "exec_empty_result": "Skeleton",
    "exec_syntax_error": "Execution",
    "repair_introduced_new_error": "Execution",
    "correct": "Correct",
    "unknown": "Unknown",
    "none": "Unknown",
}

GT_STAGE = {
    "Intent / Ambiguity Error": "Intent",
    "Schema Linking Error": "Schema",
    "Skeleton / Structural Error": "Skeleton",
}

STAGES = ["Intent", "Schema", "Skeleton", "Execution"]


def norm(s) -> str:
    return " ".join(str(s or "").split()).lower()


def load_ground_truth() -> dict:
    """(db_id, question) -> (set_name, group, target_stage)"""
    man = json.loads((BENCH / "manifest.json").read_text(encoding="utf-8"))
    gt = {}
    for s in man["sets"]:
        recs = json.loads((ROOT / s["benchmark_file"]).read_text(encoding="utf-8"))
        for r in recs:
            gt[(norm(r["db_id"]), norm(r["question"]))] = (
                s["set"], s["group"], GT_STAGE[r["target_error_type"]])
    return gt


def load_logs(gt: dict) -> list:
    """Return [(set_name, group, truth_stage, label, log_dict)]"""
    out = []
    for f in glob.glob(str(LOGS / "*.json")):
        if "resume_checkpoint" in f:
            continue
        try:
            d = json.loads(Path(f).read_text(encoding="utf-8"))
        except Exception:
            continue
        x1 = d.get("x1") or {}
        k = (norm(x1.get("db_id")), norm(x1.get("question")))
        if k not in gt:
            continue
        set_name, group, truth = gt[k]
        label = str(d.get("annotated_root_cause") or "none").lower()
        out.append((set_name, group, truth, label, d))
    return out


def table(rows, headers):
    w = [max(len(str(h)), *(len(str(r[i])) for r in rows)) if rows else len(str(h))
         for i, h in enumerate(headers)]
    line = "  ".join(str(h).ljust(w[i]) for i, h in enumerate(headers))
    print(line)
    print("-" * len(line))
    for r in rows:
        print("  ".join(str(c).ljust(w[i]) for i, c in enumerate(r)))


def report_per_set(records):
    print("=" * 78)
    print("PER-SET RECALL — did the heuristic name the stage the fault was injected at?")
    print("=" * 78)
    by_set = defaultdict(list)
    for set_name, group, truth, label, _ in records:
        by_set[set_name].append((truth, label))

    rows = []
    for set_name in sorted(by_set, key=lambda s: (s.split("_")[0], s)):
        items = by_set[set_name]
        truth = items[0][0]
        passed = sum(1 for _, l in items if STAGE_OF_LABEL.get(l) == "Correct")
        failed = [l for _, l in items if STAGE_OF_LABEL.get(l) != "Correct"]
        hit = sum(1 for l in failed if STAGE_OF_LABEL.get(l) == truth)
        rec = f"{hit/len(failed)*100:.1f}%" if failed else "n/a"
        rows.append([set_name, truth, len(items), passed, len(failed), hit, rec])
    table(rows, ["perturbation set", "injected", "n", "passed", "failed", "hit", "recall"])


def report_confusion(records):
    print()
    print("=" * 78)
    print("CONFUSION MATRIX — injected stage vs attributed stage (failures only)")
    print("=" * 78)
    m = defaultdict(Counter)
    for _, _, truth, label, _ in records:
        st = STAGE_OF_LABEL.get(label, "Unknown")
        if st == "Correct":
            continue
        m[truth][st] += 1

    cols = STAGES + ["Unknown"]
    rows = []
    for truth in STAGES:
        if truth not in m:
            continue
        row = m[truth]
        tot = sum(row.values())
        cells = [f"{row.get(c,0)} ({row.get(c,0)/tot*100:.0f}%)" if tot else "0" for c in cols]
        rows.append([truth, *cells, tot])
    table(rows, ["injected \\ attributed", *cols, "total"])

    print()
    for truth in STAGES:
        if truth not in m:
            continue
        tot = sum(m[truth].values())
        hit = m[truth].get(truth, 0)
        print(f"  recall({truth:<9}) = {hit}/{tot} = {hit/tot*100:6.2f}%" if tot else "")


def report_strategies(records):
    """Re-score the same logs under alternative attribution strategies."""
    try:
        import sys
        sys.path.insert(0, str(ROOT))
        from logging_core.log_models import PipelineLogEntry
        from evaluation.attribution_strategies import STRATEGIES
    except Exception as e:
        print(f"\n(skipping strategy ablation: {e})")
        return

    print()
    print("=" * 78)
    print("STRATEGY ABLATION — same logs, different attribution rule, zero API cost")
    print("=" * 78)

    rows = []
    for name, fn in STRATEGIES.items():
        per_stage = defaultdict(lambda: [0, 0])  # truth -> [hit, total]
        errors = 0
        for _, _, truth, label, d in records:
            if STAGE_OF_LABEL.get(label) == "Correct":
                continue
            try:
                entry = PipelineLogEntry.model_validate(d)
                pred, _ = fn(entry)
            except Exception:
                errors += 1
                continue
            per_stage[truth][1] += 1
            if pred == truth:
                per_stage[truth][0] += 1
        cells = []
        for st in STAGES:
            hit, tot = per_stage.get(st, [0, 0])
            cells.append(f"{hit/tot*100:.1f}%" if tot else "-")
        tot_all = sum(v[1] for v in per_stage.values())
        hit_all = sum(v[0] for v in per_stage.values())
        overall = f"{hit_all/tot_all*100:.1f}%" if tot_all else "-"
        rows.append([name, *cells, overall, errors])
    table(rows, ["strategy", *STAGES, "overall", "err"])
    print("\n  Reading: if upstream_first merely mirrors downstream_first's failure,")
    print("  the bias is caused by the detectors, not by the scan order.")


def main():
    gt = load_ground_truth()
    records = load_logs(gt)
    print(f"matched {len(records)} logs against Dr.Spider ground truth "
          f"({len(gt)} benchmark instances)\n")
    if not records:
        print("No logs yet — run scripts_drspider/run_drspider.py first.")
        return
    report_per_set(records)
    report_confusion(records)
    report_strategies(records)


if __name__ == "__main__":
    main()
