"""
Instrument MAC-SQL with the Logging Matrix and run it over the Dr.Spider
perturbation benchmark.

Why this exists
---------------
The nlsql pipeline records 13 state variables, but several of them are
re-derived from the final SQL (x7.uses_join and x8.expected_clauses are regex
extractions of x9.selected_sql), so the "skeleton stage" detector can never
fire and stage attribution collapses onto a residual bucket. MAC-SQL does not
have that defect: its Selector emits a pruned schema BEFORE any SQL exists, and
its Decomposer emits a natural-language decomposition separate from the query
it later produces. Those are genuinely independent per-stage observations, so
attribution scored on them is a real measurement rather than a tautology.

What is captured
----------------
The agents communicate through one mutable `message` dict. We snapshot it
before and after each agent, so every artifact is attributed to the agent that
actually wrote it:

    Selector   -> extracted_schema, desc_str, fk_str, pruned      (x5, x6)
    Decomposer -> qa_pairs (CoT decomposition), final_sql          (x3, x7, x8)
    Refiner    -> pred, fixed, try_times, execution error          (x10, x11, x13)

Nothing in MAC-SQL's prompts or control flow is modified; the wrapper only
observes.

Usage:
    python baselines/macsql_instrumented.py --sets NLQ_keyword_synonym
    python baselines/macsql_instrumented.py --groups NLQ SQL
    python baselines/macsql_instrumented.py --limit 5        # smoke
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sqlite3
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

EXP = Path(__file__).resolve().parent.parent
load_dotenv(EXP / ".env")
sys.path.insert(0, str(EXP))
sys.path.insert(0, str(EXP / "baselines" / "MAC-SQL"))

BENCH_DIR = EXP / "data" / "drspider_benchmark"
OUT_DIR = EXP / "output_logs" / "macsql_drspider"
SPIDER_TABLES = EXP / "data/raw/spider_unzipped/spider_data/tables.json"
DRSPIDER = EXP / "data" / "raw" / "drspider"

TRACKED = ("pruned", "desc_str", "fk_str", "extracted_schema",
           "chosen_db_schem_dict", "qa_pairs", "final_sql", "pred",
           "fixed", "try_times")


# ---------------------------------------------------------------------------
# tables.json for the namespaced DB_* databases
# ---------------------------------------------------------------------------

def ensure_tables_json(set_name: str, group: str, suffix: str | None) -> Path:
    """DB_* sets ship their own tables json, but our db_ids are namespaced
    (battle_death_0 -> battle_death_0__synonym) to stop the three DB_* sets
    from overwriting each other's databases. Rewrite the db_id accordingly."""
    if group != "DB":
        return SPIDER_TABLES

    out = BENCH_DIR / f"tables_{set_name}.json"
    if out.exists():
        return out

    src = DRSPIDER / set_name / "tables_post_perturbation.json"
    tabs = json.loads(src.read_text(encoding="utf-8"))
    for t in tabs:
        t["db_id"] = f"{t['db_id']}__{suffix}"
    out.write_text(json.dumps(tabs, ensure_ascii=False), encoding="utf-8")
    print(f"    wrote {out.name} ({len(tabs)} dbs, namespaced)")
    return out


# ---------------------------------------------------------------------------
# Execution-based evaluation (independent of MAC-SQL)
# ---------------------------------------------------------------------------

def exec_rows(db: Path, sql: str, timeout: float = 30.0):
    con = sqlite3.connect(str(db), timeout=timeout)
    try:
        con.execute("PRAGMA busy_timeout = 15000")
        return con.execute(sql).fetchall(), None
    except Exception as e:
        return None, str(e)
    finally:
        con.close()


def execution_match(db: Path, pred: str, gold: str):
    """Order-insensitive result-set comparison, plus the engine error if any."""
    if not pred or not pred.strip() or pred.strip().lower().startswith("error"):
        return False, "empty or error prediction"
    p, perr = exec_rows(db, pred)
    if perr is not None:
        return False, perr
    g, gerr = exec_rows(db, gold)
    if gerr is not None:
        return False, f"gold failed: {gerr}"
    return sorted(map(str, p)) == sorted(map(str, g)), None


# ---------------------------------------------------------------------------
# Agent instrumentation
# ---------------------------------------------------------------------------

def instrument(chat_manager, sink: list):
    """Wrap each agent's talk() so we snapshot what it changed."""
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
                out = _orig(message)
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
                raise
            finally:
                after = {k: copy.deepcopy(message.get(k)) for k in TRACKED}
                sink.append({
                    "agent": _name,
                    "elapsed_s": round(time.time() - t0, 3),
                    "error": err,
                    "wrote": {k: after[k] for k in TRACKED
                              if after[k] != before[k]},
                })
            return out

        agent.talk = wrapped


# ---------------------------------------------------------------------------
# Logging Matrix projection
# ---------------------------------------------------------------------------

def tables_in_sql(sql: str) -> set[str]:
    return set(re.findall(r"(?:from|join)\s+([a-zA-Z_][\w]*)", (sql or "").lower()))


def to_log_entry(item, msg, trace, match, exec_err, elapsed, model):
    """Project MAC-SQL artifacts onto the 13-variable Logging Matrix.

    Each x_i is filled from the agent that actually produced it. Nothing is
    back-derived from the final SQL — that is the whole point of using a
    pipeline with real stages.
    """
    by_agent = {t["agent"]: t for t in trace}
    sel = by_agent.get("Selector", {}).get("wrote", {})
    dec = by_agent.get("Decomposer", {}).get("wrote", {})
    ref = by_agent.get("Refiner", {}).get("wrote", {})

    # `chosen_db_schem_dict` is the authoritative table->columns selection and
    # is written on BOTH Selector branches. `extracted_schema` holds only the
    # LLM's raw pruning decision and stays {} when the schema was small enough
    # to keep whole — reading that empty dict as "no tables selected" would
    # manufacture a false schema-stage fault, which is precisely the class of
    # bug this study is about.
    chosen = msg.get("chosen_db_schem_dict") or {}
    extracted = msg.get("extracted_schema") or {}
    selected_tables = sorted(chosen.keys()) if isinstance(chosen, dict) else []
    gold_tables = sorted(tables_in_sql(item["gold_sql"]))
    missing_gold_tables = sorted(
        set(gold_tables) - {t.lower() for t in selected_tables})

    return {
        "entry_id": f"macsql_{item['id']}",
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "system": "MAC-SQL",
        "model_name": model,
        "benchmark_id": item["id"],
        "source": item["source"],
        "perturbation_group": item.get("perturbation_group"),
        "target_error_type": item["target_error_type"],
        "gold_sql": item["gold_sql"],

        # --- Intent (Decomposer's natural-language decomposition) ---
        "x1": {"question": item["question"], "db_id": item["db_id"]},
        "x2": {"evidence": msg.get("evidence") or ""},
        "x3": {"qa_pairs": dec.get("qa_pairs"),
               "produced_by": "Decomposer"},

        # --- Schema (Selector, produced BEFORE any SQL exists) ---
        "x4": {"tables_json": "spider-format"},
        "x5": {"selected_tables": selected_tables,
               "extracted_schema": extracted,
               "pruned": msg.get("pruned"),
               "missing_gold_tables": missing_gold_tables,
               "produced_by": "Selector"},
        "x6": {"desc_str_len": len(msg.get("desc_str") or ""),
               "fk_str": msg.get("fk_str"),
               "produced_by": "Selector"},

        # --- Skeleton (Decomposer's pre-refinement SQL) ---
        "x7": {"decomposition_present": bool(dec.get("qa_pairs"))},
        "x8": {"pre_refine_sql": msg.get("final_sql"),
               "produced_by": "Decomposer"},
        "x9": {"selected_sql": msg.get("final_sql")},

        # --- Execution (Refiner) ---
        "x10": {"final_sql": msg.get("pred"), "has_error": exec_err is not None,
                "error_message": exec_err},
        "x11": {"execution_match": match},
        "x12": {"refiner_fired": bool(msg.get("fixed")),
                "try_times": msg.get("try_times")},
        "x13": {"repaired_sql": msg.get("pred") if msg.get("fixed") else None,
                "produced_by": "Refiner"},

        "gold_tables": gold_tables,
        "final_sql": msg.get("pred"),
        "execution_match": match,
        "elapsed_s": round(elapsed, 2),
        "agent_trace": trace,
    }


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_set(s, limit, model, done: set):
    from core.chat_manager import ChatManager
    from core.const import SYSTEM_NAME

    set_name, group = s["set"], s["group"]
    suffix = s.get("namespaced_suffix")
    tables = ensure_tables_json(set_name, group, suffix)
    db_dir = EXP / s["db_dir"]

    recs = json.loads((EXP / s["benchmark_file"]).read_text(encoding="utf-8"))
    todo = [r for r in recs if r["id"] not in done]
    if limit:
        todo = todo[:limit]
    if not todo:
        print("    already complete")
        return 0, 0

    cm = ChatManager(data_path=str(db_dir), tables_json_path=str(tables),
                     log_path=str(OUT_DIR / f"_macsql_{set_name}.log"),
                     model_name=model, dataset_name="spider", lazy=True)

    ok = fail = 0
    for i, item in enumerate(todo, 1):
        trace: list = []
        msg = {"idx": i, "db_id": item["db_id"], "query": item["question"],
               "evidence": "", "extracted_schema": {},
               "ground_truth": item["gold_sql"], "difficulty": "unknown",
               "send_to": SYSTEM_NAME}
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
    ap.add_argument("--model", default=os.getenv("MACSQL_MODEL", "gpt-4o"))
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

    print(f"MAC-SQL over {len(sets)} Dr.Spider sets | model={args.model}\n")
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
