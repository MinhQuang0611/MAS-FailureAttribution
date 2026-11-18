"""
evaluation/distribution_report.py

The framework-attributed stage distribution, per dataset x system x backbone.

WHY THIS IS SEPARATE FROM cross_system_report.py
------------------------------------------------
The two reports answer different questions and must not be conflated.

    cross_system_report.py   needs construction-time ground truth and reports
                             RECALL: does attribution name the stage where the
                             fault was actually injected? Only Dr.Spider can
                             answer it.

    this file                needs no ground truth and reports the DISTRIBUTION:
                             which stages does attribution *claim*? Every
                             dataset can answer it, BIRD included.

The distinction is the paper's central methodological point. A distribution is
what a heuristic says; recall is whether it is right. Reporting the first while
implying the second is exactly the error this study documents, so a dataset
without injected faults is reported here and is silently absent from recall —
never given a fabricated label to be scored against.

Read the two together: if the attributed distribution has the same downstream
shape on BIRD as on Dr.Spider, where recall is measurably zero, that shape is a
property of state-differencing rather than of one corpus.

Usage:
    python evaluation/distribution_report.py
"""

from __future__ import annotations

import glob
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOGS = ROOT / "output_logs"

# <system>_<dataset>/<backbone>/*.json
SYSTEM_DIRS = {
    "macsql": "MAC-SQL",
    "magsql": "MAG-SQL",
    "dinsql": "DIN-SQL",
    "chess": "CHESS",
}

STAGES = ["Intent", "Schema", "Skeleton", "Execution"]


def _attribute(d: dict) -> str:
    """Import the one attribution rule, rather than re-implementing it.

    A second copy would drift, and a drifted copy would make the distribution
    and the recall tables describe different heuristics.
    """
    from cross_system_report import attribute_baseline
    return attribute_baseline(d)


def collect() -> dict:
    """(dataset, system, backbone) -> Counter of attributed stage / Correct."""
    out: dict[tuple[str, str, str], Counter] = defaultdict(Counter)
    for path in sorted(LOGS.glob("*_*/*/")):
        # Skip archived trees: quarantined, superseded and pre-migration
        # backups all live under an underscore-prefixed directory. They are
        # kept on disk for provenance, and reporting them beside live runs
        # invites exactly the confusion this audit exists to prevent.
        if any(p.startswith('_') for p in path.parts):
            continue
        sys_ds = path.parent.name
        if "_" not in sys_ds:
            continue
        sys_key, _, dataset = sys_ds.partition("_")
        system = SYSTEM_DIRS.get(sys_key)
        if not system:
            continue
        backbone = path.name

        for f in glob.glob(str(path / "*.json")):
            name = Path(f).name
            if name.startswith("_") or "api_trace" in name or "resume" in name:
                continue
            try:
                d = json.loads(Path(f).read_text(encoding="utf-8"))
            except Exception:
                continue
            key = (dataset, system, backbone)
            out[key]["_n"] += 1
            if d.get("execution_match"):
                out[key]["Correct"] += 1
            else:
                out[key][_attribute(d)] += 1
    return out


def main() -> None:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))

    print("=" * 78)
    print("FRAMEWORK-ATTRIBUTED STAGE DISTRIBUTION")
    print("=" * 78)
    print("What attribution CLAIMS, not whether it is right. Recall is only")
    print("defined where faults were injected by construction (Dr.Spider);")
    print("see cross_system_report.py for that half.")

    data = collect()
    if not data:
        print("\n   no logs yet")
        return

    for dataset in sorted({k[0] for k in data}):
        rows = {k: v for k, v in data.items() if k[0] == dataset}
        print(f"\n--- dataset: {dataset} " + "-" * (60 - len(dataset)))
        head = f"   {'system / backbone':<32}{'n':>6}{'EX':>8}"
        head += "".join(f"{s:>11}" for s in STAGES)
        print(head)
        print("   " + "-" * (len(head) - 3))

        for (_, system, backbone), c in sorted(rows.items()):
            n = c["_n"]
            correct = c["Correct"]
            failed = n - correct
            line = f"   {system + ' / ' + backbone:<32}{n:>6}"
            line += f"{correct / n * 100:>7.1f}%" if n else f"{'—':>8}"
            for st in STAGES:
                # Percentages are over FAILURES, not over all instances: the
                # question is how the heuristic divides the failures it sees.
                line += f"{c[st] / failed * 100:>10.1f}%" if failed else f"{'—':>11}"
            print(line)
        print(f"   {'':<32}{'':>6}{'':>8}" + "  (% of failures)")


if __name__ == "__main__":
    main()
