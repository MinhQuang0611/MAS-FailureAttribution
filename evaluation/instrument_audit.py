"""
evaluation/instrument_audit.py

Audit the INSTRUMENT before trusting anything it reports.

THE CONFOUND THIS RESOLVES
--------------------------
A system scoring 0% stage recall admits two incompatible readings:

    (a) attribution genuinely cannot recover the faulty stage, or
    (b) our detector cannot read that system's artifacts.

Reading (b) is a defect in the measuring apparatus, and publishing (a) while
(b) is live would repeat precisely the error this study documents in others.
The two are separable, but only by measuring the instrument itself.

For every stage this reports four numbers, in causal order:

    present    the system emitted an artifact for this stage at all
    varies     that artifact is not degenerate — it actually differs across
               instances in the way the detector keys on
    fires      the detector assigned this stage to a failure
    correct    ... and the ground truth agreed

Reading the chain left to right localises any zero:

    present=0                 the system exposes no such artifact. Nothing can
                              be attributed here by ANY detector. A property of
                              the pipeline, honestly reportable as such.
    present>0, varies=0       the artifact exists but never exhibits the defect
                              the detector keys on. The ceiling is zero for any
                              detector of this form — not a detector bug, but a
                              statement about artifact resolution.
    varies>0, fires=0         OUR BUG. The signal is in the log and the detector
                              missed it. Nothing downstream may be published
                              until this is fixed.
    fires>0, correct=0        attribution genuinely misfires. This, and only
                              this, is evidence about attribution.

SEPARABILITY CHECK
------------------
`varies` above is necessary but not sufficient. A detector can only separate
failures from successes if the artifact itself differs between them. The final
section compares each artifact's distribution across instances that passed and
failed. An artifact that looks identical in both carries no signal about
failure, whatever its internal variety — so the ceiling is zero regardless of
how cleverly the detector is written.

Usage:
    python evaluation/instrument_audit.py
"""

from __future__ import annotations

import glob
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from cross_system_report import GT_STAGE, attribute_baseline  # noqa: E402

STOP = {"the", "a", "an", "of", "in", "on", "for", "to", "and", "or", "what",
        "which", "who", "how", "many", "much", "is", "are", "list", "show",
        "give", "find", "all", "with", "by", "that"}


# The column-level schema reconstruction is shared with the report so the two
# cannot drift; a divergence there would make the audit describe a detector
# that is not the one being audited.
from cross_system_report import columns_kept, dropped_gold_columns  # noqa: E402


# --------------------------------------------------------------------------
# Per-stage predicates: does the artifact exist, and does it carry the defect
# the detector looks for? Kept in lockstep with attribute_baseline() — if the
# detector changes, these must change with it or the audit lies.
# --------------------------------------------------------------------------

def schema_present(d):
    """The Selector emitted a decision we can read.

    `selected_tables` is NOT usable here: MAC-SQL writes an entry to
    `chosen_db_schem_dict` for every table in the database, so that field is
    always the full table list and `present` would read 100% even when the
    Selector never ran. What is present-or-absent is the pruning decision
    itself, plus a schema we can resolve the column order from.
    """
    return columns_kept(d) is not None


def schema_varies(d):
    """The detector's precondition: a gold COLUMN the Selector withheld.

    Was `missing_gold_tables`, which is empty by construction (see the note on
    `db_columns` in cross_system_report). Keeping that here made the audit
    share the detector's blind spot and report a dead branch as a property of
    the pipeline -- the one failure mode this audit exists to catch.
    """
    return bool(dropped_gold_columns(d))


def intent_present(d):
    x3 = d.get("x3") or {}
    return bool(x3.get("subquery_list") or x3.get("qa_pairs"))


def intent_varies(d):
    x3 = d.get("x3") or {}
    decomp = x3.get("subquery_list") or x3.get("qa_pairs")
    q = (d.get("x1") or {}).get("question", "").lower()
    if not (decomp and q):
        return False
    qw = {w for w in re.findall(r"[a-z]{3,}", q) if w not in STOP}
    dw = set(re.findall(r"[a-z]{3,}", json.dumps(decomp).lower()))
    return bool(qw) and len(qw & dw) / len(qw) < 0.5


def exec_present(d):
    return (d.get("x10") or {}) != {}


def exec_varies(d):
    err = (d.get("x10") or {}).get("error_message")
    return bool(err) and "gold failed" not in str(err)


STAGE_TESTS = {
    "Intent": (intent_present, intent_varies),
    "Schema": (schema_present, schema_varies),
    "Execution": (exec_present, exec_varies),
    # Skeleton is the residual branch: it fires when no upstream test does, so
    # it has no precondition of its own and no meaningful `varies`.
    "Skeleton": (lambda d: True, lambda d: True),
}


def load_logs(path: Path):
    for f in glob.glob(str(path / "*.json")):
        name = Path(f).name
        if name.startswith("_") or "api_trace" in name or "resume" in name:
            continue
        try:
            yield json.loads(Path(f).read_text(encoding="utf-8"))
        except Exception:
            continue


def audit(system: str, path: Path) -> None:
    logs = [d for d in load_logs(path)]
    if not logs:
        return
    failed = [d for d in logs if not d.get("execution_match")]
    passed = [d for d in logs if d.get("execution_match")]
    if not failed:
        return

    print(f"\n### {system}   n={len(logs)}  (failed {len(failed)}, passed {len(passed)})")
    print(f"   {'stage':<11}{'present':>9}{'varies':>9}{'fires':>8}"
          f"{'correct':>9}   verdict")
    print("   " + "-" * 68)

    fires = defaultdict(int)
    correct = defaultdict(int)
    # Whether this cell has construction-time ground truth at all. BIRD carries
    # no `target_error_type`, so `correct` would sit at 0 for every stage and
    # the verdict would read "fires but never right" -- announcing that
    # attribution misfires when in fact nothing was ever compared against. A
    # cell with no ground truth reports correctness as n/a.
    has_gt = any(GT_STAGE.get(d.get("target_error_type")) for d in failed)
    for d in failed:
        pred = attribute_baseline(d)
        fires[pred] += 1
        truth = GT_STAGE.get(d.get("target_error_type"))
        if truth and pred == truth:
            correct[pred] += 1

    for stage, (present_fn, varies_fn) in STAGE_TESTS.items():
        p = sum(1 for d in failed if present_fn(d))
        v = sum(1 for d in failed if varies_fn(d))
        f = fires[stage]
        c = correct[stage]

        if p == 0:
            verdict = "artifact absent — unattributable by any detector"
        elif v == 0:
            verdict = "artifact never shows the defect — ceiling is 0"
        elif f == 0:
            verdict = "!! DETECTOR BUG — signal present, detector silent"
        elif not has_gt:
            verdict = "no ground truth in this cell — correctness not assessable"
        elif c == 0:
            verdict = "fires but never right — attribution misfires"
        else:
            verdict = f"working ({c}/{f} = {c/f*100:.0f}% precision)"

        c_col = f"{c:>9}" if has_gt else f"{'n/a':>9}"
        print(f"   {stage:<11}{p/len(failed)*100:>8.1f}%{v/len(failed)*100:>8.1f}%"
              f"{f:>8}{c_col}   {verdict}")


def separability(system: str, path: Path) -> None:
    """Does the artifact look different when the pipeline fails?

    If a stage's artifact has the same distribution among passes and failures,
    no detector reading only that artifact can separate them, however it is
    written. This bounds every possible detector, not just ours.
    """
    logs = list(load_logs(path))
    failed = [d for d in logs if not d.get("execution_match")]
    passed = [d for d in logs if d.get("execution_match")]
    if not failed or not passed:
        return

    print(f"\n   separability of the schema artifact ({system}):")
    rates = {}
    for label, group in (("failed", failed), ("passed", passed)):
        if not group:
            continue
        miss = sum(1 for d in group if schema_varies(d))
        kept = columns_kept(group[0]) if group else None
        cols = []
        for d in group:
            k = columns_kept(d)
            if k:
                cols.append(sum(len(v) for v in k.values()))
        avg = sum(cols) / len(cols) if cols else 0
        rates[label] = miss / len(group)
        print(f"     {label:<7} n={len(group):<5} "
              f"withheld a gold column: {rates[label]*100:5.1f}%   "
              f"mean columns kept: {avg:.1f}")

    # The verdict is COMPUTED, never asserted. An earlier version printed
    # "identical rows => no failure signal" unconditionally, which would have
    # reported a separable artifact as inseparable -- and did, for the whole
    # time the schema test was reading a field that is empty by construction.
    f, ps = rates.get("failed"), rates.get("passed")
    if f is None or ps is None:
        return
    if max(f, ps) < 0.005:
        print("     neither group shows the defect => the artifact carries no "
              "failure signal, and no detector of this form can separate them")
    elif f <= ps:
        print(f"     failures show it no more often than passes "
              f"({f*100:.1f}% vs {ps*100:.1f}%) => no usable signal")
    elif ps == 0:
        print(f"     only failures show it ({f*100:.1f}% vs 0.0%) => the "
              f"artifact separates cleanly; the ceiling is set by how rarely "
              f"it fires")
    else:
        print(f"     failures show it {f/ps:.1f}x more often than passes "
              f"({f*100:.1f}% vs {ps*100:.1f}%) => the artifact does separate, "
              f"weakly; the ceiling is set by how rarely it fires")


def main() -> None:
    print("=" * 78)
    print("INSTRUMENT AUDIT — can the detector see, before asking if it is right")
    print("=" * 78)

    found = False
    for path in sorted((ROOT / "output_logs").glob("*_*/*/")):
        # Skip archived trees: quarantined, superseded and pre-migration
        # backups all live under an underscore-prefixed directory. They are
        # kept on disk for provenance, and reporting them beside live runs
        # invites exactly the confusion this audit exists to prevent.
        if any(p.startswith('_') for p in path.parts):
            continue
        sys_ds = path.parent.name
        if "_" not in sys_ds:
            continue
        name = f"{sys_ds} / {path.name}"
        before = found
        audit(name, path)
        separability(name, path)
        found = True
    if not found:
        print("\n   no logs yet")


if __name__ == "__main__":
    main()
