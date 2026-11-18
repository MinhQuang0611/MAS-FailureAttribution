"""
evaluation/make_figures.py

Regenerate the paper's figures from the logs, so no figure is a file whose
provenance has been forgotten. Writes vector PDFs into paper/.

    python evaluation/make_figures.py
"""
from __future__ import annotations

import glob
import json
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evaluation"))
from cross_system_report import GT_STAGE, attribute_baseline  # noqa: E402

OUT = ROOT / "paper"
STAGES = ["Intent", "Schema", "Skeleton", "Execution"]
# The three live MAC-SQL Dr.Spider sweeps. MAG-SQL is absent because its
# Dr.Spider run was retired at 320/1700 (output_logs/_superseded_partial/),
# and Dr.Spider is the only benchmark here with construction-time stage
# labels, so it is the only one from which recall can be plotted.
CELLS = [
    ("MAC-SQL / gpt-4o", "macsql", "gpt-4o"),
    ("MAC-SQL / gpt-5.6-luna", "macsql", "openai-gpt-5.6-luna"),
    ("MAC-SQL / Qwen2.5-14B", "macsql", "ollama-qwen2.5-14b"),
]

plt.rcParams.update({
    "font.family": "serif",
    "font.size": 8,
    "axes.linewidth": 0.6,
    "xtick.major.width": 0.6,
    "ytick.major.width": 0.6,
    "pdf.fonttype": 42,
})


def load(system: str, backbone: str) -> list:
    path = ROOT / "output_logs" / f"{system}_drspider" / backbone
    out = []
    for f in glob.glob(str(path / "drspider_*.json")):
        p = Path(f)
        if p.name.startswith("_"):
            continue
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    return out


def _varies(d: dict, stage: str) -> bool:
    """Did this stage's artifact exhibit the property the detector keys on?"""
    if stage == "Schema":
        return bool((d.get("x5") or {}).get("missing_gold_tables"))
    if stage == "Execution":
        err = (d.get("x10") or {}).get("error_message")
        return bool(err and "gold failed" not in str(err))
    if stage == "Intent":
        return attribute_baseline(d) == "Intent"
    return True


def fig_attributability() -> None:
    """Artifact variation vs stage recall, per system and stage.

    The claim under test is that recall tracks artifact variation, so the two
    series are drawn side by side and read as a pair, not separately.
    """
    fig, axes = plt.subplots(1, 3, figsize=(6.4, 2.1), sharey=True)
    for ax, (label, system, backbone) in zip(axes, CELLS):
        logs = load(system, backbone)
        fails = [d for d in logs if not d.get("execution_match")
                 and GT_STAGE.get(d.get("target_error_type"))]
        varies, hit, inj = {}, defaultdict(int), defaultdict(int)
        for s in STAGES:
            varies[s] = (100.0 * sum(_varies(d, s) for d in fails) / len(fails)
                         if fails else 0.0)
        for d in fails:
            truth = GT_STAGE[d["target_error_type"]]
            inj[truth] += 1
            hit[truth] += attribute_baseline(d) == truth
        rec = [100.0 * hit[s] / inj[s] if inj[s] else float("nan") for s in STAGES]

        x = range(len(STAGES))
        ax.bar([i - 0.19 for i in x], [varies[s] for s in STAGES], 0.36,
               color="#b0b7c3", edgecolor="black", linewidth=0.4,
               label="artifact varies")
        ax.bar([i + 0.19 for i in x], rec, 0.36,
               color="#2f4b7c", edgecolor="black", linewidth=0.4,
               label="stage recall")
        for i, s in enumerate(STAGES):
            if inj.get(s, 0) == 0:
                ax.text(i + 0.19, 2, "n/a", ha="center", fontsize=6, rotation=90)
        ax.set_xticks(list(x))
        ax.set_xticklabels(["Intent", "Schema", "Skel.", "Exec."], fontsize=7)
        ax.set_title(f"{label}\n({len(fails)} failures)", fontsize=7.5)
        ax.set_ylim(0, 105)
        ax.spines[["top", "right"]].set_visible(False)
    axes[0].set_ylabel("percent of failures")
    axes[0].legend(fontsize=6.5, frameon=False, loc="upper left")
    fig.tight_layout()
    fig.savefig(OUT / "fig_attributability.pdf", bbox_inches="tight")
    print("wrote", OUT / "fig_attributability.pdf")


def fig_family() -> None:
    """Per-family failure rate against per-family recall.

    Aggregates hide that the families disagree; the spread is the point.
    """
    logs = load("macsql", "gpt-4o-mini")
    fam = defaultdict(lambda: {"n": 0, "fail": 0, "hit": 0})
    for d in logs:
        truth = GT_STAGE.get(d.get("target_error_type"))
        if not truth:
            continue
        k = (d["perturbation_group"], d["source"].replace("drspider_", ""))
        fam[k]["n"] += 1
        if d.get("execution_match"):
            continue
        fam[k]["fail"] += 1
        fam[k]["hit"] += attribute_baseline(d) == truth

    colour = {"NLQ": "#2f4b7c", "DB": "#c1121f", "SQL": "#5b8c5a"}
    marker = {"NLQ": "o", "DB": "s", "SQL": "^"}
    fig, ax = plt.subplots(figsize=(3.3, 2.5))
    for (grp, name), v in sorted(fam.items()):
        if not v["fail"]:
            continue
        ax.scatter(100 * v["fail"] / v["n"], 100 * v["hit"] / v["fail"],
                   c=colour[grp], marker=marker[grp], s=22, linewidths=0.4,
                   edgecolors="black", zorder=3)
    for grp in ("NLQ", "DB", "SQL"):
        ax.scatter([], [], c=colour[grp], marker=marker[grp], s=22,
                   linewidths=0.4, edgecolors="black", label=grp)
    ax.axhline(0, color="black", lw=0.5, ls=":")
    ax.set_xlabel("failure rate of the family (%)")
    ax.set_ylabel("stage recall (%)")
    ax.set_ylim(-5, 75)
    ax.legend(fontsize=6.5, frameon=False, loc="upper left", title="group",
              title_fontsize=6.5)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(OUT / "fig_family.pdf", bbox_inches="tight")
    print("wrote", OUT / "fig_family.pdf")


if __name__ == "__main__":
    fig_attributability()
    fig_family()
