"""
Tải benchmark nhỏ cho pipeline (mini_benchmark.json).

- Nhánh "schema": mẫu từ Spider validation (xlangai/spider). Dr. Spider đầy đặn
  nằm trên GitHub awslabs/diagnostic-robustness-text-to-sql; dataset `awslabs/dr-spider`
  không tồn tại trên Hub — có thể thay thế sau khi bạn nhập JSON thủ công.
- Nhánh "intent": Spider-Syn (aherntech/spider-syn), dùng cột SpiderSynQuestion.

Chạy từ thư mục gốc project:
    pip install datasets pandas
    python download_data.py
"""
from __future__ import annotations

import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT_PATH = ROOT / "data" / "mini_benchmark.json"


def download_and_sample() -> None:
    from datasets import load_dataset

    random.seed(42)

    print("Đang tải Spider validation (xlangai/spider) — proxy cho mẫu schema/linking...")
    spider_val = load_dataset("xlangai/spider", "spider", split="validation")
    val_list = list(spider_val)
    sample_schema = random.sample(val_list, min(200, len(val_list)))

    print("Đang tải Spider-Syn (aherntech/spider-syn) — nhiễu intent...")
    spider_syn = load_dataset("aherntech/spider-syn", split="validation")
    syn_list = list(spider_syn)
    sample_syn = random.sample(syn_list, min(200, len(syn_list)))

    mini_benchmark: list[dict] = []

    for idx, item in enumerate(sample_schema):
        q = item["query"]
        mini_benchmark.append(
            {
                "id": f"spider_val_{idx}",
                "source": "xlangai_spider_validation_schema_proxy",
                "db_id": item["db_id"],
                "question": item["question"],
                "gold_sql": q,
                "query": q,
                "target_error_type": "Schema Linking Error",
            }
        )

    for idx, item in enumerate(sample_syn):
        q = item["query"]
        mini_benchmark.append(
            {
                "id": f"spider_syn_{idx}",
                "source": "spider_syn_intent_perturbation",
                "db_id": item["db_id"],
                "question": item["SpiderSynQuestion"],
                "gold_sql": q,
                "query": q,
                "target_error_type": "Intent / Ambiguity Error",
            }
        )

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(mini_benchmark, f, indent=4, ensure_ascii=False)

    print(f"Hoàn tất! Đã lưu {len(mini_benchmark)} mục vào {OUT_PATH}")


if __name__ == "__main__":
    download_and_sample()
