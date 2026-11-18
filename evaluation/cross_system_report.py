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

If stage recall improves along that ordering, attribution bias is a property of
instrumentation quality rather than an inherent limit of state-differencing —
which is the claim this report is built to test.

Reads whatever logs exist; systems with no logs are skipped.
"""

from __future__ import annotations

import json
import glob
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# MAG-SQL is deliberately absent. Its Dr.Spider sweep reached 320/1700 before
# the backbone endpoint went down and was retired on 2026-08-29 without being
# resumed; the logs live in output_logs/_superseded_partial/. At that n the
# sweep covered only Intent-perturbed instances, so the row it produced was
# 0/102 = 0.0% recall over a single injected stage -- a number that reads as a
# finding but is an artefact of where the run stopped. Restore this entry only
# alongside a completed sweep.
SYSTEMS = {
    "nlsql":   ROOT / "output_logs" / "drspider",
    "MAC-SQL": ROOT / "output_logs" / "macsql_drspider",
    "DIN-SQL": ROOT / "output_logs" / "dinsql_drspider",
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


# ---------------------------------------------------------------------------
# Column-level schema evidence.
#
# WHY THIS REPLACES THE TABLE-LEVEL TEST
# --------------------------------------
# The previous Schema branch fired on `x5.missing_gold_tables`, i.e. a gold
# table the Selector failed to pass downstream. That test can never fire.
# MAC-SQL builds `chosen_db_schem_dict[table_name]` for EVERY table in the
# database (core/agents.py:505); its only `continue` needs use_gold_schema,
# which Dr.Spider runs never set. `drop_all` does not drop the table either --
# it keeps the first six columns (core/agents.py:472). So selected_tables is
# always the full table list (verified: 1400/1400 instances whose db_id
# resolves), making `missing_gold_tables` empty by construction rather than by
# observation, and the Schema branch dead code.
#
# MAC-SQL's Selector prunes COLUMNS. The detector must therefore read columns.
# We reconstruct what actually reached the Decomposer from the Selector's own
# decision (`x5.extracted_schema`) plus the database's column order:
#
#     ['a','b']  -> exactly those columns
#     'keep_all' -> every column
#     'drop_all' -> the first six columns, matching agents.py:472
#     absent     -> every column (no decision was taken for that table)
#
# and blame the schema stage when a column the gold query needs did not
# survive. This is still an artifact test: it reads only what the Selector
# emitted, never the generated SQL.
# ---------------------------------------------------------------------------

DRSPIDER_RAW = ROOT / "data" / "raw" / "drspider"
BENCH_DIR = ROOT / "data" / "drspider_benchmark"
DROP_ALL_KEEPS = 6          # core/agents.py:472  columns_desc[:6]

_COLS_CACHE: dict = {}


def _tables_files():
    """Every tables.json that could describe a logged db_id, DB_* first: the
    perturbed sets namespace their db_ids, so they cannot collide with base
    Spider, and a later file must not shadow an earlier one."""
    out = []
    out.extend(sorted(BENCH_DIR.glob("tables_*.json")))
    for p in (DRSPIDER_RAW / "Spider-dev" / "tables.json",
              ROOT / "data/raw/spider_unzipped/spider_data/tables.json",
              ROOT / "data/raw/bird/dev_20240627/dev_tables.json"):
        if p.exists():
            out.append(p)
    return out


def db_columns(db_id: str):
    """db_id -> {table_lower: [column names, in schema order]}, or None."""
    if not _COLS_CACHE:
        for f in _tables_files():
            try:
                for t in json.loads(f.read_text(encoding="utf-8")):
                    key = t["db_id"]
                    if key in _COLS_CACHE:
                        continue
                    names = t["table_names_original"]
                    per: dict = {n.lower(): [] for n in names}
                    for ti, cn in t["column_names_original"]:
                        if ti >= 0:
                            per[names[ti].lower()].append(cn)
                    _COLS_CACHE[key] = per
            except Exception:
                continue
        _COLS_CACHE.setdefault("__loaded__", {})
    return _COLS_CACHE.get(db_id)


def columns_kept(d: dict):
    """What the schema stage actually passed on, or None if unknowable."""
    x5 = d.get("x5") or {}
    schema = db_columns((d.get("x1") or {}).get("db_id", ""))
    if not schema:
        return None

    # DIN-SQL's schema stage emits schema LINKS, not a pruned schema: it
    # annotates which columns matter while the full schema still reaches every
    # downstream prompt. Reading its (absent) `extracted_schema` through the
    # MAC-SQL branch below would return the whole schema as "kept" on every
    # instance, so `dropped_gold_columns` could never fire -- a constant read
    # as an observation, which is the exact defect this study exists to catch.
    # Branch explicitly instead, and keep the two mechanisms distinguishable:
    # for MAC-SQL a missing column was WITHHELD, for DIN-SQL it was merely NOT
    # LINKED. Both are column-level schema-stage artifacts; they are not the
    # same event and must not be pooled in prose.
    if x5.get("artifact_kind") == "schema_links":
        linked = {t.lower(): {str(c).lower() for c in cols}
                  for t, cols in (x5.get("linked_columns") or {}).items()}
        return {table: linked.get(table, set()) for table in schema}

    decision = x5.get("extracted_schema") or {}
    kept: dict = {}
    for table, cols in schema.items():
        dec = next((v for k, v in decision.items() if k.lower() == table), None)
        if isinstance(dec, list):
            kept[table] = {str(c).lower() for c in dec}
        elif dec == "drop_all":
            kept[table] = {c.lower() for c in cols[:DROP_ALL_KEEPS]}
        else:                       # 'keep_all', or no decision for this table
            kept[table] = {c.lower() for c in cols}
    return kept


def gold_column_refs(sql: str):
    """{table: {columns}} for `table.column` references in the gold query.

    Only qualified references are used. An unqualified column cannot be
    assigned to a table without resolving the query, and guessing would
    manufacture exactly the false schema faults this detector exists to avoid.
    """
    refs = defaultdict(set)
    for t, c in re.findall(r"([A-Za-z_][A-Za-z_0-9]*)\.([A-Za-z_][A-Za-z_0-9]*)",
                           sql or ""):
        refs[t.lower()].add(c.lower())
    return refs


def dropped_gold_columns(d: dict):
    """Gold columns the Selector withheld: [(table, [columns])]. [] if none,
    None if the schema stage's decision cannot be reconstructed."""
    kept = columns_kept(d)
    if kept is None:
        return None
    out = []
    for table, need in gold_column_refs(d.get("gold_sql") or "").items():
        if table not in kept:
            continue
        missing = sorted(need - kept[table])
        if missing:
            out.append((table, missing))
    return out


def attribute_baseline(d: dict) -> str:
    """Stage attribution using the baseline's REAL per-agent artifacts.

    Deliberately simple and upstream-first: each test looks at the artifact the
    stage itself produced, not at the final query. A stage is only blamed when
    its own output is demonstrably wrong.
    """
    x10 = d.get("x10") or {}
    x5 = d.get("x5") or {}

    # Schema: a gold column the Selector withheld. Checked first because it is
    # the only test whose evidence is fully independent of the generated SQL.
    # The old table-level form of this test (`x5.missing_gold_tables`) was dead
    # code -- see the note above `db_columns` for why it can never fire.
    if dropped_gold_columns(d):
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
    """-> [(backbone, truth, predicted)].

    Logs live under <system>/<model-slug>/, one directory per backbone, because
    the runners' resume check keys on the instance id alone. The backbone is
    read from the log body rather than the directory name, so the grouping
    survives a renamed directory.
    """
    out = []
    for f in glob.glob(str(path / "**" / "*.json"), recursive=True):
        if "resume_checkpoint" in f or Path(f).name.startswith("_"):
            continue
        try:
            d = json.loads(Path(f).read_text(encoding="utf-8"))
        except Exception:
            continue
        model = d.get("model_name") or "unknown"
        truth = GT_STAGE.get(d.get("target_error_type"))
        if not truth:
            x1 = d.get("x1") or {}
            truth = gt.get((_norm(x1.get("db_id")), _norm(x1.get("question"))))
        if not truth:
            continue
        if d.get("execution_match"):
            out.append((model, truth, "Correct"))
            continue
        if system == "nlsql":
            pred = NLSQL_LABEL_STAGE.get(
                str(d.get("annotated_root_cause") or "none").lower(), "Unknown")
        else:
            pred = attribute_baseline(d)
        out.append((model, truth, pred))
    return out


def main() -> None:
    print("=" * 78)
    print("CROSS-SYSTEM ATTRIBUTION — Dr.Spider, construction-time ground truth")
    print("=" * 78)

    gt = load_ground_truth()
    summary = {}          # (system, backbone) -> (n, overall recall)
    backbones = []        # first-seen order, so the grid reads chronologically
    for system, path in SYSTEMS.items():
        if not path.exists():
            continue
        by_model = defaultdict(list)
        for model, truth, pred in load(system, path, gt):
            by_model[model].append((truth, pred))

        for model, recs in by_model.items():
            if model not in backbones:
                backbones.append(model)

            failed = [(t, p) for t, p in recs if p != "Correct"]
            passed = len(recs) - len(failed)
            per = defaultdict(lambda: [0, 0])
            for t, p in failed:
                per[t][1] += 1
                if p == t:
                    per[t][0] += 1

            print(f"\n### {system} / {model}   n={len(recs)}  "
                  f"(EX pass {passed}, failed {len(failed)})")
            if not failed:
                print("   no failures yet")
                continue

            dist = Counter(p for _, p in failed)
            print("   attributed to: " + "  ".join(
                f"{st}={dist.get(st,0)} ({dist.get(st,0)/len(failed)*100:.0f}%)"
                for st in STAGES + ["Unknown"] if dist.get(st)))

            cells = []
            for st in STAGES:
                hit, tot = per.get(st, [0, 0])
                cells.append(f"{st}={hit}/{tot}={hit/tot*100:.1f}%"
                             if tot else f"{st}=n/a")
            print("   recall: " + "  ".join(cells))

            hit_all = sum(v[0] for v in per.values())
            tot_all = sum(v[1] for v in per.values())
            overall = hit_all / tot_all * 100 if tot_all else 0.0
            print(f"   OVERALL RECALL: {hit_all}/{tot_all} = {overall:.1f}%")

            # Raw recall is uninterpretable on its own here. Dr.Spider's
            # families are unbalanced -- 9 of 17 are NLQ, so ~60% of failures
            # carry the Intent label -- and a detector that always answered
            # "Intent" would score that 60% without inspecting anything. What
            # follows is what the number looks like once chance is removed.
            truth_c = Counter(t for t, _ in failed)
            pred_c = Counter(p for _, p in failed)
            n = len(failed)
            best_const = max(truth_c.values()) / n * 100
            best_name = max(truth_c, key=truth_c.get)
            pe = sum(truth_c[k] / n * pred_c[k] / n
                     for k in set(truth_c) | set(pred_c))
            kappa = (overall / 100 - pe) / (1 - pe) if pe < 1 else float("nan")
            print(f"   always-\"{best_name}\" baseline: {best_const:.1f}%"
                  f"   chance agreement: {pe*100:.1f}%"
                  f"   Cohen kappa: {kappa:.3f}")
            lift = []
            for st in STAGES:
                hit, tot = per.get(st, [0, 0])
                if not tot or not pred_c.get(st):
                    continue
                exp = tot * pred_c[st] / n
                lift.append(f"{st}={hit/exp:.2f}x" if exp else f"{st}=n/a")
            if lift:
                print("   observed/chance per stage: " + "  ".join(lift))
            summary[(system, model)] = (len(recs), overall)

    if len(summary) > 1:
        print("\n" + "=" * 78)
        print("SUMMARY — stage recall by system and backbone")
        print("=" * 78)
        systems = [s for s in SYSTEMS if any(k[0] == s for k in summary)]
        w = max(12, max((len(b) for b in backbones), default=12) + 2)
        print(f"   {'backbone':<{w}}" + "".join(f"{s:>14}" for s in systems))
        print("   " + "-" * (w + 14 * len(systems)))
        for b in backbones:
            row = f"   {b:<{w}}"
            for s in systems:
                if (s, b) in summary:
                    n, r = summary[(s, b)]
                    row += f"{r:>12.1f}% "
                else:
                    row += f"{'—':>13} "
            print(row)

        print("\n   Two axes, two questions. Across a ROW: does attribution")
        print("   improve as per-stage artifacts become more independent")
        print("   (nlsql re-derived < MAC-SQL schema-real)?")
        print("   Down a COLUMN: does the bias survive a change of backbone?")
        print("   Flat rows support the inherent-limit hypothesis; flat columns")
        print("   make the bias architectural rather than model-specific.")


if __name__ == "__main__":
    main()
