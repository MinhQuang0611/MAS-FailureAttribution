"""Minimal MAC-SQL smoke test on one Dr.Spider instance.

Verifies the ported LLM transport works and that the three agents each emit
their own artifact into the shared `message` dict — the property that makes
MAC-SQL usable for stage-level attribution and that the nlsql pipeline lacks.
"""

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

EXP = Path(__file__).resolve().parent.parent
load_dotenv(EXP / ".env")
sys.path.insert(0, str(EXP / "baselines" / "MAC-SQL"))

from core.chat_manager import ChatManager  # noqa: E402
from core.const import SYSTEM_NAME  # noqa: E402

TABLES = EXP / "data/raw/spider_unzipped/spider_data/tables.json"
DBDIR = EXP / "data/databases"
BENCH = EXP / "data/drspider_benchmark/NLQ_keyword_synonym.json"

item = json.loads(BENCH.read_text(encoding="utf-8"))[0]
print(f"db_id   : {item['db_id']}")
print(f"question: {item['question']}")
print(f"gold    : {item['gold_sql'][:90]}\n")

# tables.json must describe this db
tabs = json.loads(TABLES.read_text(encoding="utf-8"))
ids = {t["db_id"] for t in tabs}
print(f"tables.json covers {len(ids)} dbs; has '{item['db_id']}': {item['db_id'] in ids}\n")

cm = ChatManager(
    data_path=str(DBDIR),
    tables_json_path=str(TABLES),
    log_path=str(EXP / "output_logs" / "macsql_smoke" / "log.txt"),
    model_name=os.getenv("MACSQL_MODEL", "gpt-4o"),
    dataset_name="spider",
    lazy=True,
)

msg = {
    "idx": 0,
    "db_id": item["db_id"],
    "query": item["question"],
    "evidence": "",
    "extracted_schema": {},
    "ground_truth": item["gold_sql"],
    "difficulty": "unknown",
    "send_to": SYSTEM_NAME,
}

cm.start(msg)

print("\n" + "=" * 70)
print("ARTIFACTS EMITTED PER AGENT")
print("=" * 70)
for key in ("pruned", "desc_str", "fk_str", "extracted_schema",
            "qa_pairs", "final_sql", "pred", "fixed", "try_times"):
    v = msg.get(key)
    if v is None:
        print(f"  {key:<18} <absent>")
        continue
    s = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v)
    s = " ".join(s.split())
    print(f"  {key:<18} {s[:110]}")
