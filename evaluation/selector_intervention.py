#!/usr/bin/env python3
"""Paired analysis of the Selector-intervention arms against the default sweep.

Tests the inert-stage claim interventionally, on the same instances and the
same backbone, with one bit of the architecture changed:

  force  MAC-SQL's pruning threshold overridden so the Selector acts on every
         database (macsql_instrumented.py --selector force).
  off    MAC-SQL's own without_selector mode (--selector off).

Two predictions, fixed before the arm was run:

  P1 (attributability)  On instances where the default Selector was inert,
     schema recall on failures moves from 0 to the level of the pruned
     regime (17-42% in the default sweep). If it stays at 0 the claim that
     inertness bounds attributability is wrong.
  P2 (not a cause)      EX on those instances changes by less than the 9-point
     re-run noise floor. A large drop would mean an active Selector *causes*
     failures, i.e. inertness is about accuracy, not only attributability.

Usage:
  python evaluation/selector_intervention.py \
      --default output_logs/macsql_drspider/ollama-qwen2.5-14b \
      --arm     output_logs/macsql_drspider/ollama-qwen2.5-14b_selector-force
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(__file__))
from cross_system_report import attribute_baseline  # noqa: E402

try:
    from scipy.stats import fisher_exact
    from statsmodels.stats.contingency_tables import mcnemar
except ImportError:  # keep the script usable without statsmodels
    fisher_exact = mcnemar = None


def load(d):
    out = {}
    for f in glob.glob(os.path.join(d, "drspider_*.json")) + glob.glob(os.path.join(d, "bird_*.json")):
        r = json.load(open(f))
        out[r["benchmark_id"]] = r
    return out


def ok(r):
    em = r["execution_match"]
    return em if isinstance(em, bool) else bool(em.get("match"))


def pruned(r):
    return bool((r.get("x5") or {}).get("extracted_schema"))


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / den
    return (c - h, c + h)


def pct(k, n):
    return f"{k}/{n} ({100 * k / n:.1f}%)" if n else f"{k}/0"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--default", required=True)
    ap.add_argument("--arm", required=True)
    ap.add_argument("--group", default="DB", help="perturbation group to analyse; 'all' for every instance")
    args = ap.parse_args()

    base, arm = load(args.default), load(args.arm)
    ids = sorted(set(base) & set(arm))
    if args.group != "all":
        ids = [i for i in ids if base[i].get("perturbation_group") == args.group]
    if not ids:
        sys.exit("no paired instances")
    print(f"paired instances: {len(ids)}  (default={args.default}, arm={args.arm})\n")

    # --- activity under each arm ---
    act = Counter((pruned(base[i]), pruned(arm[i])) for i in ids)
    print("Selector activity (default pruned?, arm pruned?):", dict(act))

    inert_ids = [i for i in ids if not pruned(base[i])]
    active_ids = [i for i in ids if pruned(base[i])]

    # --- P2: EX, paired ---
    print("\nP2  execution accuracy on the same instances")
    for name, sub in (("default-inert", inert_ids), ("default-pruned", active_ids), ("all", ids)):
        if not sub:
            continue
        b = sum(ok(base[i]) for i in sub)
        a = sum(ok(arm[i]) for i in sub)
        both = sum(ok(base[i]) and ok(arm[i]) for i in sub)
        b_only = b - both
        a_only = a - both
        line = f"  {name:15s} n={len(sub):4d}  default EX {pct(b, len(sub))}  arm EX {pct(a, len(sub))}  discordant {b_only}->fail / {a_only}->pass"
        if mcnemar is not None and (b_only + a_only) > 0:
            res = mcnemar([[both, b_only], [a_only, len(sub) - b - a_only]], exact=True)
            line += f"  McNemar p={res.pvalue:.3g}"
        print(line)

    # --- P1: schema recall on failures, on the default-inert instances ---
    print("\nP1  schema attribution on failures (Attr rule (i)), default-inert instances")
    for name, logs in (("default arm", base), ("intervention arm", arm)):
        fails = [i for i in inert_ids if not ok(logs[i])]
        key = [i for i in fails if attribute_baseline(logs[i]) == "Schema"]
        lo, hi = wilson(len(key), len(fails))
        print(f"  {name:17s} failures={len(fails):3d}  attributed Schema={len(key):3d}  recall {pct(len(key), len(fails))}  Wilson [{100*lo:.1f}, {100*hi:.1f}]")
    fails_b = [i for i in inert_ids if not ok(base[i])]
    fails_a = [i for i in inert_ids if not ok(arm[i])]
    kb = sum(attribute_baseline(base[i]) == "Schema" for i in fails_b)
    ka = sum(attribute_baseline(arm[i]) == "Schema" for i in fails_a)
    if fisher_exact is not None:
        p = fisher_exact([[kb, len(fails_b) - kb], [ka, len(fails_a) - ka]])[1]
        print(f"  Fisher (default vs arm, schema-attributed among failures): p={p:.3g}")

    # reference: the default-pruned regime the arm is predicted to reach
    fails_p = [i for i in active_ids if not ok(base[i])]
    kp = sum(attribute_baseline(base[i]) == "Schema" for i in fails_p)
    print(f"  reference, default-pruned regime: schema recall {pct(kp, len(fails_p))}")

    # --- keyable rate: does the forced Selector withhold gold columns at all? ---
    print("\nkeyable (gold column withheld) among passes vs failures, intervention arm, default-inert instances")
    kp_pass = sum(attribute_baseline(arm[i]) == "Schema" for i in inert_ids if ok(arm[i]))
    n_pass = sum(ok(arm[i]) for i in inert_ids)
    print(f"  passes   {pct(kp_pass, n_pass)}")
    print(f"  failures {pct(ka, len(fails_a))}")
    if fisher_exact is not None and n_pass and fails_a:
        p = fisher_exact([[kp_pass, n_pass - kp_pass], [ka, len(fails_a) - ka]])[1]
        print(f"  Fisher (separability): p={p:.3g}")

    # --- full Attr distribution on failures, both arms ---
    print("\nAttr label distribution on failures, default-inert instances")
    for name, logs in (("default", base), ("arm", arm)):
        c = Counter(attribute_baseline(logs[i]) for i in inert_ids if not ok(logs[i]))
        print(f"  {name:8s} {dict(c)}")


if __name__ == "__main__":
    main()
