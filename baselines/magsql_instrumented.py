"""
Instrument MAG-SQL with the Logging Matrix and run it over Dr.Spider.

MAG-SQL is the finest-grained of the three systems under study. Where MAC-SQL
folds intent and query construction into one Decomposer, MAG-SQL separates
them, so its four agents line up one-to-one with the Intent / Schema / Skeleton
/ Execution taxonomy:

    Soft_Schema_linker -> chosen_db_schem_dict, matched_list, summary_str
    Decomposer         -> subquery_list          (intent, before any SQL exists)
    Generator          -> old_chain_of_thoughts, final_sql
    Refiner            -> pred, sub_sql, fixed, try_times

`subquery_list` is the cleanest intent-stage observation available in any of the
three systems: a decomposition of the question into sub-questions, produced
before a query is written, so diffing it against the question is a genuine
upstream measurement rather than a re-reading of the output.

COST WARNING
------------
Soft_Schema_linker runs two whole-corpus preprocessing passes before the first
instance is scored: coarse value matching against database contents, and one
LLM call per table per database to build table summaries. Both are cached to
disk, so the cost is paid once per set, but on the DB_* sets (40-48 databases
each) it is substantial. Budget for it, or run --groups NLQ SQL first.

Usage:
    python baselines/magsql_instrumented.py --groups NLQ --limit 5
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

EXP = Path(__file__).resolve().parent.parent
load_dotenv(EXP / ".env")
sys.path.insert(0, str(EXP))
sys.path.insert(0, str(EXP / "baselines" / "MAG-SQL"))

from baselines.macsql_instrumented import (  # noqa: E402
    BENCH_DIR, SPIDER_TABLES, ensure_tables_json, execution_match, tables_in_sql,
)

OUT_DIR = EXP / "output_logs" / "magsql_drspider"
ADAPTED = EXP / "data" / "drspider_benchmark" / "_magsql"

TRACKED = ("pruned", "desc_str", "fk_str", "pk_str", "extracted_schema",
           "chosen_db_schem_dict", "matched_list", "summary_str",
           "subquery_list", "initial_state", "old_chain_of_thoughts",
           "final_sql", "sub_sql", "last_subquery", "pred", "fixed",
           "try_times")


def adapt_dataset(bench_file: Path, set_name: str) -> Path:
    """MAG-SQL's preprocessing indexes the corpus by `question_id`, which our
    benchmark records do not carry. Emit an adapted copy rather than mutating
    the committed benchmark, so both stay reproducible."""
    ADAPTED.mkdir(parents=True, exist_ok=True)
    out = ADAPTED / f"{set_name}.json"
    recs = json.loads(bench_file.read_text(encoding="utf-8"))
    adapted = []
    for i, r in enumerate(recs):
        a = dict(r)
        a["question_id"] = i
        a["query"] = r["gold_sql"]
        adapted.append(a)
    out.write_text(json.dumps(adapted, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    return out


def instrument(chat_manager, sink: list):
    for agent in chat_manager.chat_group:
        original = agent.talk
        name = agent.name

        def wrapped(message, _orig=original, _name=name):
            if message.get("send_to") != _name:
                return _orig(message)
            before = {k: copy.deepcopy(message.get(k)) for k in TRACKED}
            t0 = time.time()
            err = None
            try:
                return _orig(message)
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
                raise
            finally:
                after = {k: copy.deepcopy(message.get(k)) for k in TRACKED}
                sink.append({
                    "agent": _name,
                    "elapsed_s": round(time.time() - t0, 3),
                    "error": err,
                    "wrote": {k: after[k] for k in TRACKED if after[k] != before[k]},
                })

        agent.talk = wrapped


def to_log_entry(item, msg, trace, match, exec_err, elapsed, model):
    by_agent = {t["agent"]: t.get("wrote", {}) for t in trace}
    # agent names come from MAG-SQL's const module; match defensively
    schema_w = next((v for k, v in by_agent.items() if "chema" in k), {})
    decomp_w = next((v for k, v in by_agent.items() if "ecompos" in k), {})
    gen_w = next((v for k, v in by_agent.items() if "enerat" in k), {})
    ref_w = next((v for k, v in by_agent.items() if "efin" in k), {})

    chosen = msg.get("chosen_db_schem_dict") or {}
    selected_tables = sorted(chosen.keys()) if isinstance(chosen, dict) else []
    gold_tables = sorted(tables_in_sql(item["gold_sql"]))
    missing_gold_tables = sorted(
        set(gold_tables) - {t.lower() for t in selected_tables})

    return {
        "entry_id": f"magsql_{item['id']}",
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "system": "MAG-SQL",
        "model_name": model,
        "benchmark_id": item["id"],
        "source": item["source"],
        "perturbation_group": item.get("perturbation_group"),
        "target_error_type": item["target_error_type"],
        "gold_sql": item["gold_sql"],

        "x1": {"question": item["question"], "db_id": item["db_id"]},
        "x2": {"matched_list": msg.get("matched_list"),
               "summary_present": bool(msg.get("summary_str"))},
        # Intent: a real sub-question decomposition, produced pre-SQL
        "x3": {"subquery_list": decomp_w.get("subquery_list")
                                or msg.get("subquery_list"),
               "produced_by": "Decomposer"},

        "x4": {"tables_json": "spider-format"},
        "x5": {"selected_tables": selected_tables,
               "extracted_schema": msg.get("extracted_schema") or {},
               "pruned": msg.get("pruned"),
               "missing_gold_tables": missing_gold_tables,
               "produced_by": "Soft_Schema_linker"},
        "x6": {"fk_str": msg.get("fk_str"), "pk_str": msg.get("pk_str"),
               "produced_by": "Soft_Schema_linker"},

        # Skeleton: the Generator's chain of thought and its pre-refine SQL
        "x7": {"chain_of_thoughts": gen_w.get("old_chain_of_thoughts")
                                    or msg.get("old_chain_of_thoughts"),
               "produced_by": "Generator"},
        "x8": {"pre_refine_sql": msg.get("final_sql"), "produced_by": "Generator"},
        "x9": {"selected_sql": msg.get("final_sql")},

        "x10": {"final_sql": msg.get("pred"), "has_error": exec_err is not None,
                "error_message": exec_err},
        "x11": {"execution_match": match},
        "x12": {"refiner_fired": bool(msg.get("fixed")),
                "try_times": msg.get("try_times"),
                "sub_sql": msg.get("sub_sql")},
        "x13": {"repaired_sql": msg.get("pred") if msg.get("fixed") else None,
                "produced_by": "Refiner"},

        "gold_tables": gold_tables,
        "final_sql": msg.get("pred"),
        "execution_match": match,
        "elapsed_s": round(elapsed, 2),
        "agent_trace": trace,
        "_schema_writes": list(schema_w.keys()),
        "_refiner_writes": list(ref_w.keys()),
    }


def run_set(s, limit, model, done: set) -> tuple[int, int]:
    from main_scripts.chat_manager import ChatManager
    from main_scripts.const import SYSTEM_NAME

    set_name, group = s["set"], s["group"]
    tables = ensure_tables_json(set_name, group, s.get("namespaced_suffix"))
    db_dir = EXP / s["db_dir"]
    bench_file = EXP / s["benchmark_file"]

    recs = json.loads(bench_file.read_text(encoding="utf-8"))
    todo = [r for r in recs if r["id"] not in done]
    if limit:
        todo = todo[:limit]
    if not todo:
        print("    already complete")
        return 0, 0

    adapted = adapt_dataset(bench_file, set_name)
    print("    preprocessing (value matching + per-table LLM summaries)...",
          flush=True)

    cm = ChatManager(data_path=str(db_dir), tables_json_path=str(tables),
                     log_path=str(OUT_DIR / f"_magsql_{set_name}.log"),
                     model_name=model, dataset_name="spider",
                     dataset_path=str(adapted), lazy=True)

    ok = fail = 0
    for i, item in enumerate(todo, 1):
        trace: list = []
        msg = {"idx": i, "db_id": item["db_id"], "query": item["question"],
               "evidence": "", "extracted_schema": {},
               "ground_truth": item["gold_sql"], "difficulty": "unknown",
               "question_id": i - 1, "send_to": SYSTEM_NAME}
        t0 = time.time()
        try:
            instrument(cm, trace)
            cm.start(msg)
            db = db_dir / item["db_id"] / f"{item['db_id']}.sqlite"
            match, exec_err = execution_match(db, msg.get("pred") or "",
                                              item["gold_sql"])
        except Exception:
            match, exec_err = False, traceback.format_exc(limit=3)
            fail += 1
        else:
            ok += 1

        entry = to_log_entry(item, msg, trace, match, exec_err,
                             time.time() - t0, model)
        (OUT_DIR / f"{item['id']}.json").write_text(
            json.dumps(entry, ensure_ascii=False, indent=1), encoding="utf-8")

        if i % 10 == 0 or i == len(todo):
            print(f"    {i}/{len(todo)}  ok={ok} fail={fail}", flush=True)
    return ok, fail


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--groups", nargs="*", default=["NLQ", "SQL", "DB"])
    ap.add_argument("--sets", nargs="*", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--model", default=os.getenv("MAGSQL_MODEL", "gpt-4o"))
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    done = {p.stem for p in OUT_DIR.glob("drspider_*.json")}
    if done:
        print(f"resuming — {len(done)} instances already logged\n")

    man = json.loads((BENCH_DIR / "manifest.json").read_text(encoding="utf-8"))
    sets = [s for s in man["sets"]
            if (args.sets and s["set"] in args.sets)
            or (not args.sets and s["group"] in args.groups)]
    sets.sort(key=lambda s: ({"NLQ": 0, "SQL": 1, "DB": 2}[s["group"]], s["set"]))

    print(f"MAG-SQL over {len(sets)} Dr.Spider sets | model={args.model}\n")
    t0 = time.time()
    tot_ok = tot_fail = 0
    for i, s in enumerate(sets, 1):
        print(f"[{i}/{len(sets)}] {s['set']} ({s['group']} -> {s['target_stage']})",
              flush=True)
        try:
            ok, fail = run_set(s, args.limit, args.model, done)
        except Exception as e:
            print(f"    !! set failed: {type(e).__name__}: {e}", flush=True)
            continue
        tot_ok += ok
        tot_fail += fail
        print(f"    elapsed {(time.time()-t0)/60:.1f} min\n", flush=True)

    print(f"done: {tot_ok} ok, {tot_fail} failed, {(time.time()-t0)/60:.1f} min")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
