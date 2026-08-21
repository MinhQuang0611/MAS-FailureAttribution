"""
evaluation/attribution_bias.py

Đo độ chệch (bias) của backward-diffing attribution heuristic trên bộ
diagnostic benchmark 400 câu (data/mini_benchmark.json), nơi ground-truth
error type đã biết trước theo thiết kế perturbation.

Sinh confusion matrix giữa:
    ground truth  : target_error_type  (Schema Linking Error / Intent / Ambiguity Error)
    prediction    : annotated_root_cause  (nhãn do RootCauseClassifier gán)

Không gọi API. Chỉ đọc lại logs đã có trong output_logs/.
"""

from __future__ import annotations

import json
import glob
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MINI = ROOT / "data" / "mini_benchmark.json"

# Ưu tiên nguồn log: network_rerun > rerun_failed > raw_logs (post-rerun state)
SOURCE_PRIORITY = {"network_rerun": 3, "rerun_failed": 2, "raw_logs": 1}

# Gom nhãn chi tiết của classifier về đúng 4 stage của pipeline
STAGE_OF = {
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
    "exec_wrong_result": "Skeleton",  # semantic wrong result -> quy về Skeleton như trong bài
    "exec_empty_result": "Skeleton",
    "exec_syntax_error": "Execution",
    "repair_introduced_new_error": "Execution",
    "correct": "Correct",
    "unknown": "Unknown",
    "network_error": "Excluded (infrastructure)",
    "none": "Unknown",
}

GT_STAGE = {
    "Schema Linking Error": "Schema",
    "Intent / Ambiguity Error": "Intent",
}


def _norm(s: str) -> str:
    return " ".join(str(s or "").split()).lower()


def _key(db_id: str, question: str):
    return (str(db_id or "").strip().lower(), _norm(question))


def _source_of(path: str) -> str:
    p = Path(path).as_posix()
    for name in SOURCE_PRIORITY:
        if f"/{name}/" in p:
            return name
    return "raw_logs"


def collect():
    mini = json.loads(MINI.read_text(encoding="utf-8"))
    gt = {}
    for m in mini:
        gt[_key(m.get("db_id"), m.get("question"))] = m.get("target_error_type")

    # best[key] = (priority, timestamp, label, execution_match, path)
    best: dict = {}
    for f in glob.glob(str(ROOT / "output_logs" / "**" / "*.json"), recursive=True):
        if "resume_checkpoint" in f:
            continue
        try:
            d = json.loads(Path(f).read_text(encoding="utf-8"))
        except Exception:
            continue
        x1 = d.get("x1") or {}
        k = _key(x1.get("db_id"), x1.get("question"))
        if k not in gt:
            continue
        prio = SOURCE_PRIORITY.get(_source_of(f), 0)
        ts = str(d.get("timestamp") or "")
        cand = (prio, ts, d.get("annotated_root_cause"), d.get("execution_match"), f)
        if k not in best or cand[:2] > best[k][:2]:
            best[k] = cand
    return gt, best


def main():
    gt, best = collect()

    matrix = defaultdict(Counter)
    raw_labels = defaultdict(Counter)
    missing = 0

    for k, truth in gt.items():
        truth_stage = GT_STAGE.get(truth, truth)
        if k not in best:
            missing += 1
            matrix[truth_stage]["<no log>"] += 1
            continue
        label = str(best[k][2]).lower()
        raw_labels[truth_stage][label] += 1
        matrix[truth_stage][STAGE_OF.get(label, label)] += 1

    print("=" * 74)
    print("CONFUSION MATRIX — ground truth (perturbation design) vs predicted stage")
    print("=" * 74)
    all_pred = sorted({p for row in matrix.values() for p in row})
    header = f"{'Ground truth':<26}" + "".join(f"{p:>16}" for p in all_pred) + f"{'total':>8}"
    print(header)
    print("-" * len(header))
    for truth_stage in sorted(matrix):
        row = matrix[truth_stage]
        total = sum(row.values())
        line = f"{truth_stage:<26}" + "".join(f"{row.get(p, 0):>16}" for p in all_pred) + f"{total:>8}"
        print(line)

    print()
    print("=" * 74)
    print("PER-CLASS RECALL (đúng stage / tổng)")
    print("=" * 74)
    for truth_stage in sorted(matrix):
        row = matrix[truth_stage]
        total = sum(row.values())
        correct_stage = row.get(truth_stage, 0)
        # 'Correct' = pipeline chạy đúng, perturbation không gây lỗi -> tách riêng
        passed = row.get("Correct", 0)
        failed_total = total - passed
        rec = correct_stage / failed_total * 100 if failed_total else 0.0
        print(f"{truth_stage:<26} recall = {correct_stage}/{failed_total} = {rec:6.2f}%   "
              f"(+{passed} câu pipeline vẫn chạy đúng, không tính)")

    print()
    print("=" * 74)
    print("RAW LABEL BREAKDOWN")
    print("=" * 74)
    for truth_stage in sorted(raw_labels):
        print(f"\n[{truth_stage}]  (n={sum(raw_labels[truth_stage].values())})")
        for lab, n in raw_labels[truth_stage].most_common():
            print(f"    {lab:<34} {n:>5}")

    if missing:
        print(f"\n(!) {missing} câu không tìm thấy log tương ứng")


if __name__ == "__main__":
    main()
