#!/usr/bin/env python3
"""
Progress of every experiment run, read straight off disk.

Counts logs actually written rather than trusting a runner's stdout, so it is
correct after a crash, a kill, or a resume, and it works whether or not any
chain is currently running.

    ./check-progress            once
    ./check-progress -w         refresh every 20s
    ./check-progress -w -n 5    refresh every 5s
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

C = {"g": "\033[32m", "y": "\033[33m", "r": "\033[31m", "b": "\033[36m",
     "d": "\033[2m", "B": "\033[1m", "0": "\033[0m"}
if not sys.stdout.isatty() or os.getenv("NO_COLOR"):
    C = {k: "" for k in C}


def totals() -> dict:
    """Expected instance count per dataset, from the benchmark manifests."""
    out = {}
    for ds, man in (("drspider", "data/drspider_benchmark/manifest.json"),
                    ("bird", "data/bird_benchmark/manifest.json")):
        p = ROOT / man
        if p.exists():
            m = json.loads(p.read_text(encoding="utf-8"))
            out[ds] = sum(s.get("sampled", 0) for s in m.get("sets", []))
    return out


def bar(done: int, total: int, w: int = 26) -> str:
    if not total:
        return " " * w
    f = min(done / total, 1.0)
    n = int(f * w)
    col = C["g"] if f >= 1 else (C["y"] if f > 0 else C["d"])
    return f"{col}{'█' * n}{C['d']}{'·' * (w - n)}{C['0']}"


ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def rate_and_eta(files: list, done: int, total: int, live: bool) -> str:
    """ETA from the last 40 completed files, which tracks the current pace.

    `live` says whether any runner process is up. Without it a finished-but-
    partial run from days ago reads as "stalled", which is alarming and wrong:
    nothing is stuck, the run simply ended early.
    """
    if done >= total:
        return ""
    if not live:
        return f"{C['d']}partial, idle{C['0']}"
    if done < 3:
        return ""
    ts = sorted(f.stat().st_mtime for f in files)[-40:]
    if len(ts) < 3:
        return ""
    # Median inter-arrival, not mean. MAG-SQL front-loads a whole-corpus
    # preprocessing pass (one LLM call per table per database) that shows up as
    # a single ~1.5h gap between two instances; a mean over the span turns that
    # one-off into an "11 days" ETA. The median ignores it and reports the pace
    # the run is actually holding.
    deltas = sorted(b - a for a, b in zip(ts, ts[1:]) if b > a)
    if not deltas:
        return ""
    per = deltas[len(deltas) // 2]
    if time.time() - ts[-1] > 900:          # no recent write: don't project
        return f"{C['r']}no write in {int((time.time()-ts[-1])/60)}m{C['0']}"
    left = timedelta(seconds=int(per * (total - done)))
    return f"{per:5.1f}s/it  eta {str(left).split('.')[0]}"


def scan_runs(tot: dict) -> list:
    rows = []
    for sysdir in sorted((ROOT / "output_logs").glob("*_*")):
        if not sysdir.is_dir() or sysdir.name.startswith("_"):
            continue
        try:
            system, dataset = sysdir.name.rsplit("_", 1)
        except ValueError:
            continue
        if dataset not in tot:
            continue
        for bk in sorted(p for p in sysdir.iterdir() if p.is_dir()):
            files = [f for f in bk.glob(f"{dataset if dataset=='bird' else 'drspider'}_*.json")
                     if not f.name.startswith("_")]
            if not files:
                files = [f for f in bk.glob("*.json")
                         if not f.name.startswith("_") and "api_trace" not in f.name]
            if not files:
                continue
            rows.append((system, dataset, bk.name, len(files), tot[dataset], files))
    return rows


def scan_counterfactual() -> list:
    base = ROOT / "output_logs" / "counterfactual"
    rows = []
    if not base.exists():
        return rows
    for bk in sorted(p for p in base.iterdir() if p.is_dir()):
        for arm in sorted(p for p in bk.iterdir() if p.is_dir()):
            files = [f for f in arm.glob("*.json") if not f.name.startswith("_")]
            if files:
                rep = 0
                for f in files:
                    try:
                        rep += bool(json.loads(f.read_text(encoding="utf-8")).get("repaired"))
                    except Exception:
                        pass
                rows.append((bk.name, arm.name, len(files), rep))
    return rows


def running() -> list:
    try:
        out = subprocess.run(["ps", "-eo", "pid=,etime=,args="],
                             capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return []
    keep = []
    for ln in out.splitlines():
        a = ln.strip()
        if ("_instrumented.py" in a or "counterfactual.py" in a
                or "luna_chain.sh" in a or "full_experiments.sh" in a):
            if "check_progress" in a or " ps " in a:
                continue
            p = a.split(None, 2)
            if len(p) == 3:
                keep.append((p[0], p[1], p[2]))
    return keep


def render() -> None:
    tot = totals()
    print(f"\n{C['B']}experiment progress{C['0']}  "
          f"{C['d']}{datetime.now():%Y-%m-%d %H:%M:%S}{C['0']}")

    procs = running()
    rows = scan_runs(tot)
    if rows:
        print(f"\n  {C['B']}{'system':<8}{'dataset':<10}{'backbone':<22}"
              f"{'done':>12}  {'':<26} {C['0']}")
        for system, dataset, bkname, done, total, files in rows:
            pct = f"{done/total*100:5.1f}%" if total else "  n/a"
            mark = f"{C['g']}✓{C['0']}" if done >= total else " "
            live = any(system[:3] in cmd and dataset in cmd for _, _, cmd in procs)
            print(f"  {system:<8}{dataset:<10}{bkname:<22}"
                  f"{done:>5}/{total:<6}{mark} {bar(done, total)} "
                  f"{pct}  {rate_and_eta(files, done, total, live)}")
    else:
        print(f"\n  {C['d']}no run logs yet{C['0']}")

    cf = scan_counterfactual()
    print(f"\n  {C['B']}counterfactual{C['0']}")
    if cf:
        for bkname, arm, n, rep in cf:
            pc = f"{rep/n*100:.1f}%" if n else "n/a"
            print(f"  {'':<8}{arm:<10}{bkname:<22}{n:>5} run    "
                  f"repaired {rep:>4}  ({pc})")
    else:
        print(f"  {C['d']}  not started — needs the Dr.Spider sweep on the "
              f"same backbone first{C['0']}")

    print(f"\n  {C['B']}processes{C['0']}")
    if procs:
        for pid, et, cmd in procs:
            short = cmd.replace(str(ROOT) + "/", "")
            if len(short) > 88:
                short = short[:85] + "..."
            print(f"  {C['g']}●{C['0']} pid {pid:<8} up {et:<12} {C['d']}{short}{C['0']}")
    else:
        print(f"  {C['y']}○ nothing running{C['0']}")

    # systemd user service (survives SSH logout and reboot)
    try:
        st = subprocess.run(["systemctl", "--user", "is-active", "mas-experiments"],
                            capture_output=True, text=True, timeout=10).stdout.strip()
        en = subprocess.run(["systemctl", "--user", "is-enabled", "mas-experiments"],
                            capture_output=True, text=True, timeout=10).stdout.strip()
        col = C["g"] if st == "active" else (C["y"] if st == "inactive" else C["r"])
        print(f"\n  {C['B']}service{C['0']}  mas-experiments: {col}{st}{C['0']}"
              f"  {C['d']}({en}; journalctl --user -u mas-experiments -f){C['0']}")
    except Exception:
        pass

    log = ROOT / "output_logs" / "chain.log"
    if log.exists():
        try:
            tail = [ANSI.sub("", l).rstrip()
                    for l in log.read_text(errors="ignore").splitlines()
                    if ANSI.sub("", l).strip()]
            for l in tail[-3:]:
                print(f"  {C['d']}{l[:100]}{C['0']}")
        except Exception:
            pass
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description="experiment progress")
    ap.add_argument("-w", "--watch", action="store_true", help="refresh continuously")
    ap.add_argument("-n", "--interval", type=int, default=20, help="seconds between refreshes")
    a = ap.parse_args()
    if not a.watch:
        render()
        return 0
    try:
        while True:
            print("\033[2J\033[H", end="")
            render()
            time.sleep(a.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
