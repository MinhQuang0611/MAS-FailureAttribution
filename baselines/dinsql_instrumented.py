"""
Instrument DIN-SQL with the Logging Matrix and run it over Dr.Spider.

Why this system, and why third
------------------------------
MAC-SQL and MAG-SQL each gave one inert stage, and in both cases the mechanism
was conditional execution: prune only above a size threshold, decompose only
when the question warrants it. DIN-SQL is the sharpest available test of
whether that is a pattern rather than two coincidences, because its gate is
*explicit, three-valued and logged by construction*:

    schema linking -> query classification -> generation -> self-correction
                          |
                          +-- EASY        -> easy_prompt_maker    (no sub-questions)
                          +-- NON-NESTED  -> medium_prompt_maker  (no sub-questions)
                          +-- NESTED      -> hard_prompt_maker    (sub-questions)

The decomposition artifact — the Intent-stage evidence this study reads —
exists *only* on the NESTED branch. On the other two the stage emits nothing,
by design and not by accident. So alpha_k for DIN-SQL's decomposer is exactly
the NESTED rate, measurable with no detector at all, and it lands on a
different stage of a different system from MAC-SQL's Selector.

DIN-SQL also runs on Spider, which is what makes it worth the API budget:
Dr.Spider's construction-time stage labels apply, so both alpha_k AND recall
are defined here. CHESS would have been the other candidate and is BIRD-only.

Two artifact-semantics warnings, stated up front
------------------------------------------------
1. DIN-SQL's schema stage emits *schema links* (a column-level annotation),
   not a pruned schema. The full schema is still placed in every downstream
   prompt. So "a gold column missing from the links" is NOT the same event as
   MAC-SQL's Selector withholding a column: one is advisory, the other is a
   physical restriction. The projection below records
   `x5.artifact_kind = "schema_links"` so nothing downstream pools the two
   silently. The A1/A2/A3 conditions apply to both; the mechanism differs and
   must be reported as differing.

2. DIN-SQL's self-correction stage runs on *every* instance and never consults
   the database — unlike MAC-SQL's Refiner, which loops until the engine
   accepts the query. Its inactivity is therefore a text-level no-op
   (output identical to input), not an unfired gate.

Deviations from the pinned upstream, all recorded per instance
--------------------------------------------------------------
* The published file cannot be imported (module-level argv parsing; the literal
  `API_KEY = #key` is a syntax error; openai<1.0 transport). setup_server.sh
  replaces exactly that block — see baselines/overrides/DIN-SQL/_harness_shim.py.
* Upstream retries a failed LLM call forever (`while x is None: ... sleep(3)`).
  Unbounded retry inside a 1,700-instance sweep is a hang, so retries are
  capped at MAX_RETRIES and the count is written to the log.
* Upstream parses `sub_questions` on the NESTED branch with no guard, so a
  malformed classifier reply raises and kills the run. That parse is guarded,
  and a failure is logged as `sub_questions_parse_failed` rather than being
  silently indistinguishable from "no decomposition" — the two mean opposite
  things for A1.
* Upstream was written for 2023 GPT-4 in prompt-continuation mode: it takes
  `reply.split("SQL: ")[1]`, and its debug prompt ends on a bare `SELECT` that
  the completion continued. A 2026 chat model answers with prose plus a ```sql
  fence and writes "### SQL Query:", so those parsers miss and fall back to the
  literal string "SELECT" -- the generation stage's real output is discarded,
  and the concatenation yields `SELECT ```sql SELECT ...`, a syntax error.
  Left alone this reports DIN-SQL near 0% EX and measures format drift rather
  than the pipeline. Upstream's parser still runs first on the untouched reply;
  only where it fails does extract_sql() take over, and every instance records
  `upstream_parse_ok` and `extraction_used` per stage, so the rate at which the
  published parser fails against this backbone is a reported number. That rate
  belongs in the paper's measurement disclosures.
* `find_fields_MYSQL_like("college_2")` prepends a train-split database to
  three of the prompts as a worked example. Spider's full tables.json is not
  available here (the upstream zip 404s), so that lookup returns an empty
  string. Every entry records `exemplar_schema_present` so the deviation is
  visible in the logs instead of being absorbed.

Usage:
    python baselines/dinsql_instrumented.py --sets NLQ_keyword_synonym --limit 5
    python baselines/dinsql_instrumented.py --groups NLQ SQL DB
    python baselines/dinsql_instrumented.py --dry-run --limit 3   # no API calls
"""

from __future__ import annotations

import argparse
import importlib.util
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

BENCH_DIR = EXP / "data" / "drspider_benchmark"
DRSPIDER = EXP / "data" / "raw" / "drspider"
SPIDER_TABLES = EXP / "data/raw/spider_unzipped/spider_data/tables.json"
if not SPIDER_TABLES.exists():
    SPIDER_TABLES = DRSPIDER / "Spider-dev" / "tables.json"

DINSQL_DIR = EXP / "baselines" / "DIN-SQL"
MAX_RETRIES = 4
OUT_DIR = EXP / "output_logs" / "dinsql_drspider"

# DIN-SQL.py is a script, not a package module, and its name is not importable
# as an identifier. Load it by path once and keep the module object.
_MOD = None


def din_sql():
    """Import the patched upstream module, once."""
    global _MOD
    if _MOD is None:
        path = DINSQL_DIR / "DIN-SQL.py"
        if not path.exists():
            raise SystemExit(
                f"DIN-SQL not cloned at {path}.\n"
                "Run: bash experiments/setup_server.sh   (or WITH_DINSQL=1)")
        src = path.read_text(encoding="utf-8")
        if "harness shim (attributability audit)" not in src:
            raise SystemExit(
                "DIN-SQL.py is unpatched and cannot be imported "
                "(upstream parses argv at import time and its API_KEY line is "
                "not valid Python). Run experiments/setup_server.sh.")
        spec = importlib.util.spec_from_file_location("din_sql_upstream", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _MOD = mod
    return _MOD


def model_slug(model: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "-" for c in model)


# ---------------------------------------------------------------------------
# tables.json for the namespaced DB_* databases (same rule as MAC-SQL's runner:
# our db_ids are namespaced so the three DB_* sets cannot overwrite each other)
# ---------------------------------------------------------------------------

def ensure_tables_json(set_name: str, group: str, suffix: str | None,
                       tables_json: str | None = None) -> Path:
    if tables_json:
        return EXP / tables_json
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


def bind_schema(mod, tables_path: Path) -> bool:
    """Populate the three module globals DIN-SQL's prompt makers read.

    Upstream does this inside `__main__`; the functions reference the globals.
    Returns whether the `college_2` few-shot exemplar resolved — see the module
    docstring for why that is recorded rather than fixed.
    """
    schema, primary, foreign = mod.creatiing_schema(str(tables_path))
    mod.spider_schema, mod.spider_primary, mod.spider_foreign = (
        schema, primary, foreign)
    # Real table names per database, used to drop unresolvable gold references
    # (see gold_column_refs). Built from the same tables.json the prompts use.
    tables_by_db: dict = {}
    for db, tbl in zip(schema["Database name"], schema[" Table Name"]):
        tables_by_db.setdefault(db, set()).add(str(tbl).lower())
    return bool(mod.find_fields_MYSQL_like("college_2")), tables_by_db


# ---------------------------------------------------------------------------
# Execution-based evaluation. Byte-identical to the MAC-SQL runner's, so the
# two systems are scored by the same rule at write time; evaluation/rescore.py
# re-scores everything afterwards with the element-wise comparator.
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
# The four stages, transcribed from DIN-SQL.py's __main__ (pinned commit
# 0474801) with the retry bound and the sub-question guard noted above. The
# transcription IS the instrumentation point: upstream's loop writes only a
# flat predicted_sql.txt and keeps no per-stage artifact, so there is nothing
# to wrap. Every prompt maker, every fallback string and the branch order are
# reproduced exactly.
# ---------------------------------------------------------------------------

FENCED = re.compile(r"```(?:sqlite|sql)?\s*(.+?)```", re.S | re.I)
STATEMENT = re.compile(r"(?is)\b(?:select|with)\b.*")


def extract_sql(text: str):
    """Pull an executable statement out of a chat-formatted reply, or None.

    Upstream's parsers are string surgery written against 2023 GPT-4 in
    prompt-continuation mode: `reply.split("SQL: ")[1]`, and a debug prompt
    ending on a bare `SELECT` that the completion continued. A 2026 chat model
    answers with prose plus a ```sql fence and writes "### SQL Query:", so
    those parsers miss and fall back to the literal string "SELECT" — the
    generation stage's real output is discarded on most instances.

    Running the pipeline that way would report DIN-SQL near 0% EX and measure
    format drift rather than the pipeline, so this extractor is the fallback.
    It never replaces upstream's parser: it runs only where that parser
    already failed, and every instance records `upstream_parse_ok` so the
    failure rate is a reported number rather than an absorbed one.
    """
    if not text:
        return None
    m = FENCED.search(text)
    body = m.group(1) if m else text
    m2 = STATEMENT.search(body)
    if not m2:
        return None
    sql = m2.group(0).split(";")[0]
    return re.sub(r"\s+", " ", sql).strip() or None


def call(mod, fn, prompt, trace, stage):
    """One upstream LLM call with a bounded version of upstream's retry.

    The reply is stored verbatim: normalisation happens in run_instance, after
    upstream's own parser has had its turn on the untouched text.
    """
    last = None
    for attempt in range(MAX_RETRIES):
        t0 = time.time()
        try:
            out = fn(prompt)
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            time.sleep(3)
            continue
        trace.append({"stage": stage, "attempts": attempt + 1,
                      "elapsed_s": round(time.time() - t0, 3), "error": None,
                      "prompt_chars": len(prompt), "raw": out})
        return out
    trace.append({"stage": stage, "attempts": MAX_RETRIES, "elapsed_s": None,
                  "error": last, "prompt_chars": len(prompt), "raw": None})
    return None


def normalise(art: dict, stage: str, parsed, ok: bool, source=None):
    """Record whether upstream's own parser succeeded; fall back if it did not.

    `ok` is the verdict on upstream's result. When it holds, upstream's output
    is returned untouched. When it does not, extract_sql runs on the raw reply
    and the substitution is recorded per stage, so the rate at which a 2023
    parser fails against a 2026 backbone is a number in the logs rather than a
    silent contribution to the execution-accuracy figure.
    """
    art["upstream_parse_ok"][stage] = ok
    if ok:
        return parsed
    fallback = extract_sql(source if source is not None else parsed)
    art["extraction_used"][stage] = fallback is not None
    return fallback if fallback is not None else parsed


def run_instance(mod, question: str, db_id: str, trace: list) -> dict:
    """Return every artifact the four stages emitted, in upstream's order."""
    art: dict = {"schema_links_raw": None, "schema_links": None,
                 "classification_raw": None, "predicted_class": None,
                 "branch": None, "sub_questions": None,
                 "sub_questions_parse_failed": False,
                 "sql_pre_debug": None, "debug_raw": None, "sql": None,
                 "slicing_errors": [], "upstream_parse_ok": {},
                 "extraction_used": {}}

    # --- Stage 1: schema linking ------------------------------------------
    raw = call(mod, mod.GPT4_generation,
               mod.schema_linking_prompt_maker(question, db_id),
               trace, "schema_linking")
    art["schema_links_raw"] = raw
    try:
        schema_links = raw.split("Schema_links: ")[1]
    except Exception:
        art["slicing_errors"].append("schema_linking")
        schema_links = "[]"
    art["schema_links"] = schema_links

    # --- Stage 2: query classification (the gate) -------------------------
    raw = call(mod, mod.GPT4_generation,
               mod.classification_prompt_maker(question, db_id,
                                               schema_links[1:]),
               trace, "classification")
    art["classification_raw"] = raw
    try:
        predicted_class = raw.split("Label: ")[1]
    except Exception:
        art["slicing_errors"].append("classification")
        predicted_class = '"NESTED"'
    art["predicted_class"] = predicted_class

    # --- Stage 3: generation, on the branch the gate chose ----------------
    if '"EASY"' in predicted_class:
        art["branch"] = "EASY"
        sql = call(mod, mod.GPT4_generation,
                   mod.easy_prompt_maker(question, db_id, schema_links),
                   trace, "generation_easy")
        # Upstream does not split "SQL: " on this branch: it takes the whole
        # reply. That was a raw continuation in 2023 and is prose now, so the
        # reply counts as parsed only if it already reads as a statement.
        sql = normalise(art, "generation_easy", sql,
                        ok=bool(re.match(r"(?i)^\s*(select|with)\b",
                                         (sql or "").strip())))
    elif '"NON-NESTED"' in predicted_class:
        art["branch"] = "NON-NESTED"
        sql = call(mod, mod.GPT4_generation,
                   mod.medium_prompt_maker(question, db_id, schema_links),
                   trace, "generation_medium")
        raw_gen = sql
        try:
            sql = sql.split("SQL: ")[1]
            ok = True
        except Exception:
            art["slicing_errors"].append("generation_medium")
            sql, ok = "SELECT", False
        sql = normalise(art, "generation_medium", sql, ok=ok, source=raw_gen)
    else:
        art["branch"] = "NESTED"
        # Upstream leaves this parse unguarded; a malformed reply would abort
        # the sweep. Guarded, and the failure is distinguished from "the stage
        # emitted no decomposition" — they mean opposite things for A1.
        try:
            art["sub_questions"] = (raw.split('questions = ["')[1]
                                       .split('"]')[0])
        except Exception:
            art["sub_questions_parse_failed"] = True
        sql = call(mod, mod.GPT4_generation,
                   mod.hard_prompt_maker(question, db_id, schema_links,
                                         art["sub_questions"]),
                   trace, "generation_hard")
        raw_gen = sql
        try:
            sql = sql.split("SQL: ")[1]
            ok = True
        except Exception:
            art["slicing_errors"].append("generation_hard")
            sql, ok = "SELECT", False
        sql = normalise(art, "generation_hard", sql, ok=ok, source=raw_gen)
    art["sql_pre_debug"] = sql

    # --- Stage 4: self-correction (unconditional, never consults the DB) ---
    debugged = call(mod, mod.GPT4_debug, mod.debuger(question, db_id, sql),
                    trace, "self_correction")
    art["debug_raw"] = debugged
    # Upstream: SQL = "SELECT " + debugged_SQL, because its prompt ends on a
    # bare SELECT that a 2023 completion continued. That concatenation is only
    # correct while the reply is a continuation; a chat model returns either a
    # whole statement or prose. Apply upstream's rule, then check the result.
    reply = (debugged or "").replace("\n", " ").strip()
    # The concatenation is correct only while the reply really is a
    # continuation of the prompt's trailing SELECT: no code fence, and not a
    # statement in its own right. Anything else would double the keyword or
    # splice a fence into the query.
    is_continuation = bool(reply) and "```" not in reply and not re.match(
        r"(?i)^(select|with)\b", reply)
    art["sql"] = normalise(art, "self_correction", "SELECT " + reply,
                           ok=is_continuation, source=debugged)
    return art


# ---------------------------------------------------------------------------
# Logging Matrix projection
# ---------------------------------------------------------------------------

def norm_sql(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower().rstrip(";")


def tables_in_sql(sql: str) -> set[str]:
    return set(re.findall(r"(?:from|join)\s+([a-zA-Z_][\w]*)",
                          (sql or "").lower()))


def parse_links(schema_links: str) -> dict:
    """{table: {columns}} from DIN-SQL's `[t.c, t.c = t.c, ...]` link string."""
    out: dict = {}
    for t, c in re.findall(r"([A-Za-z_][A-Za-z_0-9]*)\.([A-Za-z_*][A-Za-z_0-9]*)",
                           schema_links or ""):
        out.setdefault(t.lower(), set()).add(c.lower())
    return out


ALIAS_STOP = {"on", "where", "group", "order", "join", "inner", "left",
              "right", "outer", "limit", "having", "as", "union", "except",
              "intersect", "select"}


def alias_map(sql: str) -> dict:
    """{alias: real table} from `FROM t AS a`, `JOIN t AS a`, `FROM t a`."""
    out = {}
    for t, a in re.findall(
            r"(?:from|join)\s+([A-Za-z_][\w]*)\s+(?:as\s+)?([A-Za-z_][\w]*)",
            (sql or "").lower()):
        if a not in ALIAS_STOP:
            out[a] = t
    return out


def gold_column_refs(sql: str, db_tables: set) -> dict:
    """{real table: {columns}} for qualified `table.column` gold references.

    Two guards, both load-bearing. An unqualified column cannot be assigned to
    a table without resolving the query, so those are skipped -- guessing would
    manufacture exactly the false schema faults this study is about. And Spider
    gold queries alias almost everything (`FROM cars_data AS T1`), so a raw
    `t1.model` reference matches no real table: aliases are resolved first, and
    anything still unresolvable against the database's own table list is
    dropped rather than counted as unlinked. Without that second guard every
    aliased query would report its whole gold column set as missing and the
    schema detector would fire on nearly all of them.
    """
    aliases = alias_map(sql)
    out: dict = {}
    for t, c in re.findall(r"([A-Za-z_][A-Za-z_0-9]*)\.([A-Za-z_][A-Za-z_0-9]*)",
                           sql or ""):
        table = aliases.get(t.lower(), t.lower())
        if db_tables and table not in db_tables:
            continue
        out.setdefault(table, set()).add(c.lower())
    return out


def to_log_entry(item, art, trace, match, exec_err, elapsed, model,
                 exemplar_ok, db_tables):
    linked = parse_links(art["schema_links"])
    gold_refs = gold_column_refs(item["gold_sql"], db_tables)
    unlinked_gold_columns = sorted(
        f"{t}.{c}"
        for t, cols in gold_refs.items()
        for c in sorted(cols - linked.get(t, set())))
    gold_tables = sorted(tables_in_sql(item["gold_sql"]))

    branch = art["branch"]
    decomposed = bool(art["sub_questions"])
    corrected = norm_sql(art["sql"]) != norm_sql(art["sql_pre_debug"])

    return {
        "entry_id": f"dinsql_{item['id']}",
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "system": "DIN-SQL",
        "model_name": model,
        "benchmark_id": item["id"],
        "source": item["source"],
        "perturbation_group": item.get("perturbation_group"),
        "target_error_type": item["target_error_type"],
        "gold_sql": item["gold_sql"],

        # --- Intent: the decomposition, which exists ONLY on NESTED ---------
        # Written under `subquery_list` so evaluation/instrument_audit.py's
        # intent_present/intent_varies read it without a special case.
        "x1": {"question": item["question"], "db_id": item["db_id"]},
        "x2": {"evidence": item.get("evidence", "")},
        "x3": {"subquery_list": ([art["sub_questions"]] if decomposed else None),
               "branch": branch,
               "parse_failed": art["sub_questions_parse_failed"],
               "produced_by": "QueryClassifier"},

        # --- Schema: schema LINKS, not a pruned schema. See module docstring.
        "x4": {"tables_json": "spider-format",
               "exemplar_schema_present": exemplar_ok},
        "x5": {"artifact_kind": "schema_links",
               "schema_links": art["schema_links"],
               "linked_tables": sorted(linked),
               "linked_columns": {t: sorted(c) for t, c in linked.items()},
               "unlinked_gold_columns": unlinked_gold_columns,
               "gold_columns_resolved": sum(len(v) for v in gold_refs.values()),
               "produced_by": "SchemaLinker"},
        "x6": {"schema_links_raw_len": len(art["schema_links_raw"] or ""),
               "produced_by": "SchemaLinker"},

        # --- Skeleton: the routing decision and the pre-correction SQL ------
        "x7": {"predicted_class": art["predicted_class"],
               "branch": branch,
               "decomposition_present": decomposed},
        "x8": {"pre_refine_sql": art["sql_pre_debug"],
               "produced_by": f"Generator[{branch}]"},
        "x9": {"selected_sql": art["sql"]},

        # --- Execution ------------------------------------------------------
        "x10": {"final_sql": art["sql"], "has_error": exec_err is not None,
                "error_message": exec_err},
        "x11": {"execution_match": match},
        "x12": {"refiner_fired": corrected, "try_times": 1,
                "consults_db": False},
        "x13": {"repaired_sql": art["sql"] if corrected else None,
                "produced_by": "SelfCorrector"},

        # --- Stage activity: chốt ① computed at write time, no detector,
        #     no reference label. alpha_k for each stage on this instance.
        "stage_activity": {
            "schema_linker_acted": art["schema_links"] not in (None, "", "[]"),
            "classifier_acted": "classification" not in art["slicing_errors"],
            "decomposer_acted": decomposed,
            "self_corrector_acted": corrected,
        },

        "gold_tables": gold_tables,
        "final_sql": art["sql"],
        "execution_match": match,
        "elapsed_s": round(elapsed, 2),
        "slicing_errors": art["slicing_errors"],
        "upstream_parse_ok": art.get("upstream_parse_ok", {}),
        "extraction_used": art.get("extraction_used", {}),
        "agent_trace": trace,
        "_control_flow": "transcribed from DIN-SQL.py __main__ @ 0474801",
    }


# ---------------------------------------------------------------------------
# Dry run: exercise every branch and the whole projection without an API call.
# ---------------------------------------------------------------------------

class StubLLM:
    """Cycles the three classifier branches so plumbing is testable offline."""

    def __init__(self):
        self.n = 0

    def generation(self, prompt):
        if "Find the schema_links" in prompt:
            return 'A: ... Schema_links: [singer.name,singer.age,concert.singer_id = singer.id]'
        if "classify it as EASY" in prompt:
            self.n += 1
            label = ['"EASY"', '"NON-NESTED"', '"NESTED"'][self.n % 3]
            return (f'A: ... The question can be solved by knowing sub questions = '
                    f'["What is the maximum age?"]. Label: {label}')
        return "A: ... SQL: SELECT name FROM singer"

    def debug(self, prompt):
        return " name FROM singer"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_set(s, limit, model, done: set, label=None, dry=False):
    mod = din_sql()
    set_name, group = s["set"], s["group"]
    tables = ensure_tables_json(set_name, group, s.get("namespaced_suffix"),
                                s.get("tables_json"))
    db_dir = EXP / s["db_dir"]
    exemplar_ok, tables_by_db = bind_schema(mod, tables)
    if not exemplar_ok:
        print("    note: college_2 exemplar schema absent from tables.json — "
              "three prompts lose their worked-example header (logged per entry)")

    if dry:
        stub = StubLLM()
        mod.GPT4_generation, mod.GPT4_debug = stub.generation, stub.debug
    else:
        os.environ["DINSQL_MODEL"] = model

    recs = json.loads((EXP / s["benchmark_file"]).read_text(encoding="utf-8"))
    todo = [r for r in recs if r["id"] not in done]
    if limit:
        todo = todo[:limit]
    if not todo:
        print("    already complete")
        return 0, 0

    ok = fail = 0
    for i, item in enumerate(todo, 1):
        trace: list = []
        t0 = time.time()
        try:
            art = run_instance(mod, item["question"], item["db_id"], trace)
            db = db_dir / item["db_id"] / f"{item['db_id']}.sqlite"
            match, exec_err = execution_match(db, art["sql"], item["gold_sql"])
        except Exception:
            art = {"schema_links_raw": None, "schema_links": None,
                   "classification_raw": None, "predicted_class": None,
                   "branch": None, "sub_questions": None,
                   "sub_questions_parse_failed": False, "sql_pre_debug": None,
                   "debug_raw": None, "sql": None, "slicing_errors": ["crash"],
                   "upstream_parse_ok": {}, "extraction_used": {}}
            match, exec_err = False, traceback.format_exc(limit=3)
            fail += 1
        else:
            ok += 1

        entry = to_log_entry(item, art, trace, match, exec_err,
                             time.time() - t0, label or model, exemplar_ok,
                             tables_by_db.get(item["db_id"], set()))
        (OUT_DIR / f"{item['id']}.json").write_text(
            json.dumps(entry, ensure_ascii=False, indent=1), encoding="utf-8")

        if i % 10 == 0 or i == len(todo):
            print(f"    {i}/{len(todo)}  ok={ok} fail={fail}", flush=True)
    return ok, fail


def main() -> int:
    ap = argparse.ArgumentParser()
    # Dr.Spider only, deliberately. Upstream ships a separate DIN-SQL_BIRD.py
    # whose prompts carry BIRD's `evidence` field; running the Spider script on
    # BIRD would silently drop that hint and measure a pipeline nobody
    # published. Adding BIRD means wrapping that second file.
    ap.add_argument("--dataset", default="drspider", choices=["drspider"])
    ap.add_argument("--groups", nargs="*", default=["NLQ", "SQL", "DB"])
    ap.add_argument("--sets", nargs="*", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--model", default=os.getenv("DINSQL_MODEL", "gpt-4o-mini"))
    ap.add_argument("--label", default=None)
    ap.add_argument("--dry-run", action="store_true",
                    help="stub the LLM: exercises every branch, spends nothing")
    args = ap.parse_args()

    global OUT_DIR
    label = args.label or args.model
    # Dry runs land under an underscore-prefixed tree, the same convention the
    # quarantined and superseded logs use: evaluation/instrument_audit.py skips
    # any path with an underscore-prefixed component, so stub output can never
    # be reported beside a real run.
    OUT_DIR = (EXP / "output_logs" / "_dryrun" / "dinsql_drspider"
               / model_slug(label) if args.dry_run else
               EXP / "output_logs" / "dinsql_drspider" / model_slug(label))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    done = {p.stem for p in OUT_DIR.glob("*.json") if not p.name.startswith("_")}
    if done:
        print(f"resuming — {len(done)} instances already logged\n")

    man = json.loads((BENCH_DIR / "manifest.json").read_text(encoding="utf-8"))
    sets = [s for s in man["sets"]
            if (args.sets and s["set"] in args.sets)
            or (not args.sets and s["group"] in args.groups)]
    sets.sort(key=lambda s: ({"NLQ": 0, "SQL": 1, "DB": 2}.get(s["group"], 9),
                             s["set"]))

    print(f"DIN-SQL over {len(sets)} drspider sets | model={args.model}"
          f"{'  [DRY RUN — no API calls]' if args.dry_run else ''}\n")
    if not args.dry_run:
        print("NOTE: DIN-SQL issues 4 LLM calls per instance (schema linking,\n"
              "      classification, generation, self-correction) on long\n"
              "      few-shot prompts. Budget accordingly before a full sweep.\n")
    t0 = time.time()
    tot_ok = tot_fail = 0
    for i, s in enumerate(sets, 1):
        print(f"[{i}/{len(sets)}] {s['set']} "
              f"({s['group']} -> {s.get('target_stage') or 'no stage ground truth'})",
              flush=True)
        try:
            ok, fail = run_set(s, args.limit, args.model, done, label,
                               args.dry_run)
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
