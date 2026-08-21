"""
evaluation/attribution_strategies.py

Alternative attribution orderings, scored on the SAME saved logs.

This is the cheap half of the bias experiment. RootCauseClassifier makes its
decision purely from the recorded state variables x1..x13, so any alternative
ordering can be scored by re-reading logs that already exist. No pipeline
re-execution, no API calls. Only the Dr.Spider logs themselves have to be
generated once.

Strategies
----------
downstream_first : the shipped ordering (D -> C -> B -> A). Reproduces
                   RootCauseClassifier and serves as the baseline.
upstream_first   : the mirror image (A -> B -> C -> D). Tests the hypothesis
                   in the paper that reversing the scan does not remove the
                   bias but inverts it.
evidence_ranked  : ordering-free. Collect the signal from every stage, then
                   pick the stage with the strongest independent evidence,
                   falling back to UNKNOWN when no stage has any. Tests
                   whether the bias is caused by precedence or by the
                   detectors themselves being weak.

The third strategy is the one that discriminates between the two competing
explanations for the 0% recall, so it is the point of the exercise.
"""

from __future__ import annotations

import re
from typing import Optional

from logging_core.log_models import PipelineLogEntry

STAGES = ("Intent", "Schema", "Skeleton", "Execution")


# ---------------------------------------------------------------------------
# Per-stage evidence extraction — each returns (fired, strength, reason)
# strength is a rough confidence in [0, 1]; only used by evidence_ranked.
# ---------------------------------------------------------------------------

def _tables_in_sql(sql: str) -> set[str]:
    sql = (sql or "").lower()
    toks = re.findall(r"(?:from|join)\s+([a-z_][a-z0-9_]*)", sql)
    return set(toks)


def evidence_intent(e: PipelineLogEntry) -> tuple[bool, float, str]:
    x1, x3 = e.x1, e.x3
    if not (x1 and x3):
        return False, 0.0, ""
    if getattr(x3, "is_ambiguous", False):
        return True, 0.9, "clarified intent flagged ambiguous"

    q = (x1.question or "").lower()
    cq = (x3.clarified_question or "").lower()
    if not q:
        return False, 0.0, ""

    # length collapse (the shipped heuristic)
    if cq and len(cq) < len(q) * 0.6:
        return True, 0.7, "clarified intent dropped significant content"

    # content-word coverage: which of the question's content words survive?
    stop = {"the", "a", "an", "of", "in", "on", "for", "to", "and", "or",
            "what", "which", "who", "how", "many", "much", "is", "are",
            "list", "show", "give", "find", "all", "with", "by", "that"}
    qw = {w for w in re.findall(r"[a-z]{3,}", q) if w not in stop}
    cw = set(re.findall(r"[a-z]{3,}", cq))
    if qw:
        cov = len(qw & cw) / len(qw)
        if cov < 0.5:
            return True, 0.5 + (0.5 - cov), f"content-word coverage {cov:.2f}"

    # conditions detected vs conditions implied by gold
    gold = (e.gold_sql or "").lower()
    if gold and "where" in gold:
        det = getattr(x3, "detected_conditions", None) or []
        if not det:
            return True, 0.45, "gold requires a filter; no condition detected"
    return False, 0.0, ""


def evidence_schema(e: PipelineLogEntry) -> tuple[bool, float, str]:
    if e.x6:
        hall = [a for a in e.x6.alignments if getattr(a, "is_hallucinated", False)]
        if hall:
            return True, 0.9, f"hallucinated schema tokens: {[a.token for a in hall][:3]}"
    if e.x4 and e.x5 and e.gold_sql:
        selected = {t.table_name.lower() for t in e.x5.selected_tables}
        needed = _tables_in_sql(e.gold_sql)
        missing = needed - selected
        if missing:
            return True, 0.8, f"gold tables pruned from schema: {sorted(missing)[:3]}"
        # plausible-but-wrong: selected tables that gold never touches
        extra = selected - needed
        if extra and len(extra) >= max(2, len(needed)):
            return True, 0.4, f"schema selection largely off-target: {sorted(extra)[:3]}"
    return False, 0.0, ""


def evidence_skeleton(e: PipelineLogEntry) -> tuple[bool, float, str]:
    final = ""
    if e.x9:
        final = (e.x9.selected_sql or "").upper()
    if e.x8 and final:
        missing = [c for c in e.x8.expected_clauses if c.upper() not in final]
        if missing:
            return True, 0.75, f"clauses in skeleton absent from final SQL: {missing[:3]}"
    if e.x7 and final:
        if getattr(e.x7, "uses_join", False) and "JOIN" not in final:
            return True, 0.7, "plan requires JOIN, final SQL has none"
    # structural divergence from gold
    gold = (e.gold_sql or "").upper()
    if gold and final:
        for kw in ("GROUP BY", "HAVING", "ORDER BY", "JOIN", "DISTINCT"):
            if (kw in gold) != (kw in final):
                return True, 0.5, f"structural mismatch on {kw}"
    return False, 0.0, ""


def evidence_execution(e: PipelineLogEntry) -> tuple[bool, float, str]:
    if e.x10 and e.x10.has_error:
        msg = (e.x10.error_message or "")
        low = msg.lower()
        # engine errors naming a missing object are schema evidence, not execution
        if any(s in low for s in ("no such column", "no such table", "does not exist")):
            return False, 0.0, ""
        return True, 0.95, f"engine error: {msg[:60]}"
    if e.x13 and getattr(e.x13, "introduced_new_error", False):
        return True, 0.6, "repair introduced a new error"
    return False, 0.0, ""


EVIDENCE = {
    "Intent": evidence_intent,
    "Schema": evidence_schema,
    "Skeleton": evidence_skeleton,
    "Execution": evidence_execution,
}


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

def _first_firing(e: PipelineLogEntry, order) -> tuple[str, str]:
    for stage in order:
        fired, _, reason = EVIDENCE[stage](e)
        if fired:
            return stage, reason
    return "Unknown", "no stage detector fired"


def downstream_first(e: PipelineLogEntry) -> tuple[str, str]:
    return _first_firing(e, ("Execution", "Schema", "Skeleton", "Intent"))


def upstream_first(e: PipelineLogEntry) -> tuple[str, str]:
    return _first_firing(e, ("Intent", "Schema", "Skeleton", "Execution"))


def evidence_ranked(e: PipelineLogEntry) -> tuple[str, str]:
    scored = []
    for stage in STAGES:
        fired, strength, reason = EVIDENCE[stage](e)
        if fired:
            scored.append((strength, stage, reason))
    if not scored:
        return "Unknown", "no stage detector fired"
    scored.sort(reverse=True)
    top = scored[0]
    if len(scored) > 1 and abs(scored[0][0] - scored[1][0]) < 0.1:
        return "Ambiguous", f"{scored[0][1]} vs {scored[1][1]} (tie)"
    return top[1], top[2]


STRATEGIES = {
    "downstream_first": downstream_first,
    "upstream_first": upstream_first,
    "evidence_ranked": evidence_ranked,
}
