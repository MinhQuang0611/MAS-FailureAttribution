"""
Counterfactual attribution: repair one stage, re-execute the rest, ask if the
failure survives.

WHY THIS EXISTS
---------------
Every attribution result in this study — ours and the literature's — rests on a
premise nobody has tested: that an instance built by perturbing stage X is an
instance where stage X is what broke. Dr.Spider certifies where a fault was
*injected*. It does not certify where the pipeline *failed*. The two are
routinely conflated, and every recall number inherits the conflation.

A counterfactual settles it without reference to any detector:

    replace stage i's output with its gold value, re-execute stages i+1..n

    failure disappears  ->  stage i was causally responsible
    failure survives    ->  stage i was not, and any ground truth naming it
                            is wrong, along with every recall computed from it

This matters most exactly where attribution scores zero. MAC-SQL attributes
0/89 DB-perturbed failures to Schema, and its Selector demonstrably keeps every
gold table in all 300 DB instances. Two readings remain, and they are opposite:
attribution missed a schema fault, or there was no schema fault to find. Only
this experiment separates them.

INTERVENTIONS
-------------
schema   Seed the Selector with the gold tables. MAC-SQL already exposes this
         as `use_gold_schema`: a non-empty `extracted_schema` in the message
         makes it skip its own pruning and describe exactly those tables. The
         intervention therefore uses the pipeline's own interface rather than
         patching its internals.

intent   Hand the Decomposer the pre-perturbation question. Dr.Spider's NLQ
         families rewrite the question while preserving the gold SQL, so the
         original wording is a meaning-preserving version of the same request —
         precisely "what if intent understanding had not been derailed". The
         Selector still sees the perturbed question, so only the intent stage
         is repaired. Restricted to NLQ sets, where gold SQL is invariant; on
         SQL_* families the pre-perturbation instance is a different query and
         the substitution would not isolate a stage.

none     No intervention: re-run the same failed instances unchanged. This
         control is not optional. MAC-SQL samples at temperature 0.1, so some
         failures repair themselves on a second run, and a repair rate is only
         interpretable as the excess over this baseline.

Usage:
    python baselines/counterfactual.py --intervention schema --groups DB
    python baselines/counterfactual.py --intervention none   --groups DB
"""

from __future__ import annotations

import argparse
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
sys.path.insert(0, str(EXP / "baselines" / "MAC-SQL"))

from baselines.macsql_instrumented import (  # noqa: E402
    AbsPath, DATASETS, ensure_tables_json, execution_match, model_slug,
    tables_in_sql,
)

DRSPIDER = EXP / "data" / "raw" / "drspider"


# ---------------------------------------------------------------------------
# Pre-perturbation questions, recovered by position
# ---------------------------------------------------------------------------

def pre_perturbation_map(set_name: str) -> dict:
    """(db_id, post-perturbation question) -> pre-perturbation question.

    Dr.Spider ships the two question files as parallel arrays over the same
    instances, so position is the join key. The map is keyed on the question
    text because our sampled benchmark does not retain the source index.
    """
    d = DRSPIDER / set_name
    pre_f, post_f = d / "questions_pre_perturbation.json", \
        d / "questions_post_perturbation.json"
    if not (pre_f.exists() and post_f.exists()):
        return {}
    pre = json.loads(pre_f.read_text(encoding="utf-8"))
    post = json.loads(post_f.read_text(encoding="utf-8"))
    if len(pre) != len(post):
        raise SystemExit(f"{set_name}: pre/post length mismatch, cannot align")

    out = {}
    for a, b in zip(pre, post):
        key = (b["db_id"], " ".join(b["question"].split()).lower())
        # A question appearing twice with different originals would make the
        # join ambiguous; drop it rather than guess.
        out[key] = None if key in out else a["question"]
    return {k: v for k, v in out.items() if v}


# ---------------------------------------------------------------------------
# Intervention wiring
# ---------------------------------------------------------------------------

def install(chat_manager, intervention: str, gold_tables: list, pre_question,
            sink: dict) -> None:
    """Wrap each agent so one stage's input/output is replaced with gold."""
    for agent in chat_manager.chat_group:
        original = agent.talk
        name = agent.name

        def wrapped(message, _orig=original, _name=name):
            if message.get("send_to") == _name:
                if intervention == "schema" and _name == "Selector":
                    # Non-empty extracted_schema switches MAC-SQL to its own
                    # gold-schema path and skips LLM pruning entirely.
                    message["extracted_schema"] = {t: "keep_all"
                                                   for t in gold_tables}
                    sink["schema_injected"] = list(gold_tables)
                if intervention == "intent" and _name == "Decomposer" \
                        and pre_question:
                    sink["question_before"] = message.get("query")
                    message["query"] = pre_question
                    sink["question_injected"] = pre_question
            return _orig(message)

        agent.talk = wrapped


def run(args) -> int:
    # MAC-SQL resolves its model, API key and base URL at import time from
    # MACSQL_MODEL (core/api_config.py). ChatManager's `model_name=` argument
    # does not reach that global, so without this the run would silently
    # execute on the default backbone against the wrong provider key --
    # confounding the intervention with a change of model, which is exactly
    # what the control arm exists to rule out. Set before the import below.
    os.environ["MACSQL_MODEL"] = args.model

    from core.chat_manager import ChatManager
    from core.const import SYSTEM_NAME

    bench = EXP / DATASETS["drspider"]["bench"]
    man = json.loads((bench / "manifest.json").read_text(encoding="utf-8"))
    sets = [s for s in man["sets"] if s["group"] in args.groups]
    sets.sort(key=lambda s: s["set"])

    label = args.label or args.model
    suffix = "" if args.selector == "default" else f"_selector-{args.selector}"
    # Failures come from the sweep named by --base-logs (default: the default
    # sweep of this backbone); the forced-Selector arm names its own directory
    # so its failures, and its control re-runs, never mix with the default's.
    base_dir = EXP / "output_logs" / "macsql_drspider" / (
        args.base_logs or (model_slug(label) + suffix))
    out_dir = (EXP / "output_logs" / "counterfactual" / (model_slug(label) + suffix)
               / args.intervention)
    out_dir.mkdir(parents=True, exist_ok=True)
    done = {p.stem for p in out_dir.glob("*.json")}

    print(f"counterfactual: intervention={args.intervention}  "
          f"groups={' '.join(args.groups)}  model={args.model}")
    print(f"  base logs : {base_dir.relative_to(EXP)}")
    print(f"  writing to: {out_dir.relative_to(EXP)}\n")

    tot_rep = tot_run = 0
    t0 = time.time()

    for si, s in enumerate(sets, 1):
        set_name, group = s["set"], s["group"]
        recs = {r["id"]: r for r in
                json.loads((EXP / s["benchmark_file"]).read_text(encoding="utf-8"))}

        # Only failures are worth re-running: a counterfactual on an instance
        # that already passed answers nothing.
        failures = []
        for rid, r in recs.items():
            f = base_dir / f"{rid}.json"
            if not f.exists():
                continue
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not d.get("execution_match") and rid not in done:
                failures.append(r)

        if args.intervention == "intent" and group != "NLQ":
            print(f"[{si}/{len(sets)}] {set_name}: skipped "
                  f"(intent intervention is defined on NLQ families only)")
            continue
        if args.limit:
            failures = failures[:args.limit]
        if not failures:
            print(f"[{si}/{len(sets)}] {set_name}: nothing to do")
            continue

        pre_map = pre_perturbation_map(set_name) if args.intervention == "intent" else {}
        tables = ensure_tables_json(set_name, group, s.get("namespaced_suffix"),
                                    s.get("tables_json"))
        db_dir = EXP / s["db_dir"]

        cm = ChatManager(data_path=AbsPath(db_dir), tables_json_path=str(tables),
                         log_path=str(out_dir / f"_cf_{set_name}.log"),
                         model_name=args.model, dataset_name="spider", lazy=True)
        if args.selector == "force":
            # Same override as macsql_instrumented.py --selector force, so the
            # control arm ("none") re-runs the forced pipeline and the schema
            # arm's gold path is compared against it.
            cm.chat_group[0]._is_need_prune = lambda db_id, db_schema: True

        print(f"[{si}/{len(sets)}] {set_name} ({group}) — "
              f"{len(failures)} failed instances", flush=True)

        rep = 0
        for i, item in enumerate(failures, 1):
            gold_tables = sorted(tables_in_sql(item["gold_sql"]))
            key = (item["db_id"], " ".join(item["question"].split()).lower())
            pre_q = pre_map.get(key)

            if args.intervention == "intent" and not pre_q:
                continue

            sink: dict = {}
            msg = {"idx": i, "db_id": item["db_id"], "query": item["question"],
                   "evidence": item.get("evidence", ""), "extracted_schema": {},
                   "ground_truth": item["gold_sql"], "difficulty": "unknown",
                   "send_to": SYSTEM_NAME}

            # Fresh wrappers per instance: gold tables differ instance to
            # instance, and a stale closure would inject the previous one.
            group_backup = [a.talk for a in cm.chat_group]
            install(cm, args.intervention, gold_tables, pre_q, sink)
            try:
                cm.start(msg)
                db = db_dir / item["db_id"] / f"{item['db_id']}.sqlite"
                match, err = execution_match(db, msg.get("pred") or "",
                                             item["gold_sql"])
            except Exception:
                match, err = False, traceback.format_exc(limit=3)
            finally:
                for a, t in zip(cm.chat_group, group_backup):
                    a.talk = t

            rep += bool(match)
            (out_dir / f"{item['id']}.json").write_text(json.dumps({
                "entry_id": f"cf_{args.intervention}_{item['id']}",
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "system": "MAC-SQL",
                "model_name": label,
                "selector": args.selector,
                "benchmark_id": item["id"],
                "set": set_name,
                "perturbation_group": group,
                "target_error_type": item["target_error_type"],
                "intervention": args.intervention,
                "repaired": bool(match),
                "final_sql": msg.get("pred"),
                "gold_sql": item["gold_sql"],
                "error_message": err,
                "injected": sink,
            }, ensure_ascii=False, indent=1), encoding="utf-8")

            if i % 10 == 0 or i == len(failures):
                print(f"    {i}/{len(failures)}  repaired={rep}", flush=True)

        tot_rep += rep
        tot_run += len(failures)
        print(f"    set repair rate: {rep}/{len(failures)}"
              f" = {rep/len(failures)*100:.1f}%\n" if failures else "", flush=True)

    if tot_run:
        print(f"done: repaired {tot_rep}/{tot_run} = {tot_rep/tot_run*100:.1f}%"
              f"  ({(time.time()-t0)/60:.1f} min)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--intervention", required=True,
                    choices=["schema", "intent", "none"])
    ap.add_argument("--groups", nargs="*", default=["NLQ", "SQL", "DB"])
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--model", default=os.getenv("MACSQL_MODEL", "gpt-4o-mini"))
    ap.add_argument("--label", default=None,
                    help="id to file results under (see macsql_instrumented.py)")
    ap.add_argument("--base-logs", default=None,
                    help="directory under output_logs/macsql_drspider/ whose failures to re-run")
    ap.add_argument("--selector", default="default", choices=["default", "force"],
                    help="force: override the pruning threshold, as in the forced-Selector arm")
    return run(ap.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
