"""
evaluation/paper_figures.py

Every figure in paper/soict2026.tex, regenerated from the logs so that no
figure is a file whose provenance has been forgotten. Writes vector PDFs to
paper/figs/.

    python evaluation/paper_figures.py            # all
    python evaluation/paper_figures.py activity   # one, by name

Palette: Okabe–Ito (colour-blind safe), assigned by entity in fixed order and
never cycled. Backbone identity: luna = blue, Qwen = orange. Stage activity:
inert = grey, active = blue. One axis per panel; no dual axes.
"""
from __future__ import annotations

import glob
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib
import matplotlib.ticker
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evaluation"))
from cross_system_report import GT_STAGE, attribute_baseline, columns_kept, gold_column_refs  # noqa: E402
from label_validity import classify, VERDICTS  # noqa: E402

OUT = ROOT / "paper" / "figs"
OUT.mkdir(exist_ok=True)

STAGES = ["Intent", "Schema", "Skeleton", "Execution"]
BACKBONES = [("gpt-5.6-luna", "openai-gpt-5.6-luna"),
             ("Qwen2.5-14B", "ollama-qwen2.5-14b")]

# Okabe–Ito
C_BLUE, C_ORANGE, C_GREEN, C_VERM, C_GREY, C_SKY, C_PURPLE = (
    "#0072B2", "#E69F00", "#009E73", "#D55E00", "#8C8C8C", "#56B4E9", "#CC79A7")
C_BB = {"gpt-5.6-luna": C_BLUE, "Qwen2.5-14B": C_ORANGE}
C_ACT = {"inert": C_GREY, "pruned": C_BLUE}
INK, INK2 = "#222222", "#555555"

plt.rcParams.update({
    "font.family": "serif",
    "font.size": 8,
    "axes.linewidth": 0.6,
    "axes.edgecolor": INK2,
    "xtick.major.width": 0.5,
    "ytick.major.width": 0.5,
    "xtick.color": INK2,
    "ytick.color": INK2,
    "axes.labelcolor": INK,
    "text.color": INK,
    "pdf.fonttype": 42,
    "legend.frameon": False,
    "legend.fontsize": 7,
})


# ----------------------------------------------------------------------------
# data
# ----------------------------------------------------------------------------
def _read_json_objects(path: Path) -> list:
    """Some counterfactual files hold several concatenated JSON objects."""
    txt = path.read_text(encoding="utf-8")
    dec = json.JSONDecoder()
    i, out = 0, []
    while i < len(txt):
        while i < len(txt) and txt[i] in " \n\r\t":
            i += 1
        if i >= len(txt):
            break
        try:
            d, i = dec.raw_decode(txt, i)
        except json.JSONDecodeError:
            break
        out.append(d)
    return out


def load_drspider(backbone_dir: str) -> list:
    path = ROOT / "output_logs" / "macsql_drspider" / backbone_dir
    out = []
    for f in sorted(glob.glob(str(path / "drspider_*.json"))):
        try:
            out.append(json.loads(Path(f).read_text(encoding="utf-8")))
        except Exception:
            continue
    return out


def load_bird(system: str) -> list:
    path = ROOT / "output_logs" / f"{system}_bird" / "openai-gpt-5.6-luna"
    out = []
    for f in sorted(glob.glob(str(path / "bird_*.json"))):
        try:
            out.append(json.loads(Path(f).read_text(encoding="utf-8")))
        except Exception:
            continue
    return out


def load_counterfactual(arm: str) -> dict:
    path = ROOT / "output_logs" / "counterfactual" / "openai-gpt-5.6-luna" / arm
    m = {}
    for f in glob.glob(str(path / "*.json")):
        for d in _read_json_objects(Path(f)):
            if isinstance(d, dict) and "benchmark_id" in d:
                m[d["benchmark_id"]] = d
    return m


def pruned(d: dict) -> bool:
    return bool((d.get("x5") or {}).get("pruned"))


def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (c - h, c + h)


def _style(ax):
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", color="#DDDDDD", lw=0.5, zorder=0)
    ax.set_axisbelow(True)


# ----------------------------------------------------------------------------
# Fig A: inertness bounds attributability, not accuracy
# ----------------------------------------------------------------------------
def fig_activity() -> None:
    """DB group, split by whether the Selector pruned: EX and schema recall.

    Left panel says the inert instances are the *easier* ones; right panel
    says every correct schema attribution sits on the pruned ones.
    """
    rows = {}
    for bb, bdir in BACKBONES:
        logs = [d for d in load_drspider(bdir) if d.get("perturbation_group") == "DB"]
        for act in ("inert", "pruned"):
            sub = [d for d in logs if pruned(d) == (act == "pruned")]
            fails = [d for d in sub if not d.get("execution_match")]
            ex = sum(bool(d.get("execution_match")) for d in sub)
            hit = sum(attribute_baseline(d) == "Schema" for d in fails)
            rows[(bb, act)] = dict(n=len(sub), ex=ex, nf=len(fails), hit=hit)

    fig, axes = plt.subplots(1, 2, figsize=(6.4, 2.3))
    w = 0.36
    for ax, key, ylabel, title in (
        (axes[0], "ex", "execution accuracy (%)", "(a) accuracy is higher when the stage is inert"),
        (axes[1], "rec", "schema recall on failures (%)", "(b) attribution exists only when it acted"),
    ):
        for j, (bb, _) in enumerate(BACKBONES):
            for k, act in enumerate(("inert", "pruned")):
                r = rows[(bb, act)]
                if key == "ex":
                    num, den = r["ex"], r["n"]
                else:
                    num, den = r["hit"], r["nf"]
                v = 100 * num / den if den else 0
                lo, hi = wilson(num, den)
                x = j + (k - 0.5) * (w + 0.04)
                ax.bar(x, v, w, color=C_ACT[act], edgecolor="white", lw=1, zorder=3)
                ax.errorbar(x, v, yerr=[[v - 100 * lo], [100 * hi - v]],
                            fmt="none", ecolor=INK, elinewidth=0.7, capsize=2, zorder=4)
                ax.text(x, 100 * hi + 1.5, f"{num}/{den}", ha="center",
                        va="bottom", fontsize=6.5, color=INK)
        ax.set_xticks([0, 1])
        ax.set_xticklabels([bb for bb, _ in BACKBONES])
        ax.set_ylabel(ylabel)
        ax.set_ylim(0, 100 if key == "ex" else 72)
        ax.set_title(title, fontsize=7.5, loc="left")
        _style(ax)
    fig.legend(handles=[Patch(color=C_ACT["inert"], label="Selector inert ($x_5 = x_4$)"),
                        Patch(color=C_ACT["pruned"], label="Selector pruned")],
               ncol=2, loc="lower center", bbox_to_anchor=(0.5, -0.06))
    fig.tight_layout()
    fig.savefig(OUT / "fig_activity.pdf", bbox_inches="tight")
    print("wrote", OUT / "fig_activity.pdf")


# ----------------------------------------------------------------------------
# Fig B: recall and skill are anti-correlated
# ----------------------------------------------------------------------------
def fig_chance() -> None:
    """Per stage and backbone: stage recall (x) against observed/expected (y).

    Expected correct under a random assignment with the observed marginals is
    fires_s * P(label = s). The stage with the lowest recall is the only one
    clearly above 1x. The y axis is logarithmic so that the chance line sits
    mid-panel and the 6x Schema points do not push everything else flat.
    """
    fig, ax = plt.subplots(figsize=(3.6, 2.6))
    marker = {"Intent": "o", "Schema": "s", "Skeleton": "^"}
    # label offset (points) and alignment per point, so nothing overlaps or
    # runs off the right edge
    place = {
        ("Intent", "luna"): (0, 7, "center", "bottom"),
        ("Intent", "Qwen"): (6, 4, "left", "bottom"),
        ("Schema", "luna"): (6, -5, "left", "top"),
        ("Schema", "Qwen"): (0, 7, "center", "bottom"),
        ("Skeleton", "luna"): (0, -9, "center", "top"),
        ("Skeleton", "Qwen"): (0, -9, "center", "top"),
    }
    for bb, bdir in BACKBONES:
        short = "luna" if "luna" in bb else "Qwen"
        logs = load_drspider(bdir)
        fails = [d for d in logs if not d.get("execution_match")
                 and GT_STAGE.get(d.get("target_error_type"))]
        n = len(fails)
        truth = [GT_STAGE[d["target_error_type"]] for d in fails]
        pred = [attribute_baseline(d) for d in fails]
        p_label = {s: truth.count(s) / n for s in STAGES}
        for s in ("Intent", "Schema", "Skeleton"):
            fires = sum(p == s for p in pred)
            correct = sum(p == s and t == s for p, t in zip(pred, truth))
            inj = truth.count(s)
            rec = 100 * correct / inj if inj else float("nan")
            exp = fires * p_label[s]
            ratio = correct / exp if exp else float("nan")
            ax.scatter(rec, ratio, marker=marker[s], s=38, color=C_BB[bb],
                       edgecolors="white", linewidths=0.8, zorder=3)
            dx, dy, ha, va = place[(s, short)]
            ax.annotate(f"{s} ({short})", (rec, ratio), xytext=(dx, dy),
                        textcoords="offset points", fontsize=6, color=INK2,
                        ha=ha, va=va)
    ax.axhline(1, color=INK2, lw=0.7, ls="--", zorder=1)
    ax.text(50, 1.07, "chance (1×)", fontsize=6.5, ha="center", va="bottom", color=INK2)
    ax.set_xlabel("stage recall (%)")
    ax.set_ylabel("correct ÷ expected under chance")
    ax.set_xlim(-3, 103)
    ax.set_yscale("log")
    ax.set_ylim(0.6, 12)
    ax.set_yticks([0.6, 1, 2, 5, 10])
    ax.set_yticklabels(["0.6×", "1×", "2×", "5×", "10×"])
    ax.yaxis.set_minor_locator(matplotlib.ticker.NullLocator())
    _style(ax)
    ax.grid(axis="y", which="minor", visible=False)
    bb_handles = [plt.Line2D([], [], marker="o", ls="", color=C_BB[bb], label=bb)
                  for bb, _ in BACKBONES]
    st_handles = [plt.Line2D([], [], marker=marker[s], ls="", color=INK2, label=s)
                  for s in ("Intent", "Schema", "Skeleton")]
    leg1 = ax.legend(handles=bb_handles, loc="upper right", bbox_to_anchor=(1.0, 1.0),
                     handletextpad=0.3)
    ax.add_artist(leg1)
    ax.legend(handles=st_handles, loc="upper right", bbox_to_anchor=(1.0, 0.80),
              handletextpad=0.3)
    fig.tight_layout()
    fig.savefig(OUT / "fig_chance.pdf", bbox_inches="tight")
    print("wrote", OUT / "fig_chance.pdf")


# ----------------------------------------------------------------------------
# Fig C: confusion matrix (replaces the table cut for space)
# ----------------------------------------------------------------------------
def fig_confusion() -> None:
    fig, axes = plt.subplots(1, 2, figsize=(6.4, 2.2))
    for ax, (bb, bdir) in zip(axes, BACKBONES):
        logs = load_drspider(bdir)
        fails = [d for d in logs if not d.get("execution_match")
                 and GT_STAGE.get(d.get("target_error_type"))]
        rows = ["Intent", "Schema", "Skeleton"]
        M = [[0] * 4 for _ in rows]
        for d in fails:
            M[rows.index(GT_STAGE[d["target_error_type"]])][STAGES.index(attribute_baseline(d))] += 1
        tot = [sum(r) for r in M]
        norm = [[c / t if t else 0 for c in r] for r, t in zip(M, tot)]
        ax.imshow(norm, cmap="Blues", vmin=0, vmax=1, aspect="auto")
        for i, r in enumerate(M):
            for j, c in enumerate(r):
                col = "white" if norm[i][j] > 0.55 else INK
                ax.text(j, i, f"{c}\n{100 * norm[i][j]:.0f}%", ha="center", va="center",
                        fontsize=6.5, color=col,
                        fontweight="bold" if STAGES[j] == rows[i] else "normal")
        ax.set_xticks(range(4)); ax.set_xticklabels(["Intent", "Schema", "Skel.", "Exec."])
        ax.set_yticks(range(3)); ax.set_yticklabels([f"{r} (n={t})" for r, t in zip(rows, tot)])
        ax.set_xlabel("attributed to")
        ax.set_title(f"{bb} — {sum(tot)} failures", fontsize=7.5, loc="left")
        for s in ax.spines.values():
            s.set_visible(False)
        ax.tick_params(length=0)
    axes[0].set_ylabel("stage stressed by construction")
    fig.tight_layout()
    fig.savefig(OUT / "fig_confusion.pdf", bbox_inches="tight")
    print("wrote", OUT / "fig_confusion.pdf")


# ----------------------------------------------------------------------------
# Fig D: the labels do not name the failing stage
# ----------------------------------------------------------------------------
def fig_labels() -> None:
    """100% stacked bars: observed failure signature by assigned label."""
    fig, axes = plt.subplots(1, 2, figsize=(6.4, 1.9), sharex=True)
    colors = [C_VERM, C_SKY, C_GREY, "#CCCCCC"]
    short = ["wrong columns", "wrong shape", "both match", "both differ"]
    for ax, (bb, bdir) in zip(axes, BACKBONES):
        logs = load_drspider(bdir)
        fails = [d for d in logs if not d.get("execution_match")
                 and GT_STAGE.get(d.get("target_error_type"))]
        cnt = defaultdict(Counter)
        for d in fails:
            v = classify(d)
            if v is not None:
                cnt[GT_STAGE[d["target_error_type"]]][v] += 1
        labels = ["Intent", "Schema", "Skeleton"]
        for i, lab in enumerate(labels):
            n = sum(cnt[lab].values())
            left = 0
            for v, c, name in zip(VERDICTS, colors, short):
                share = 100 * cnt[lab][v] / n if n else 0
                ax.barh(i, share, left=left, color=c, edgecolor="white", lw=1, height=0.62, zorder=3)
                if share >= 6:
                    ax.text(left + share / 2, i, f"{share:.0f}%", ha="center", va="center",
                            fontsize=6.5 if share >= 9 else 5.5,
                            color="white" if c in (C_VERM, C_SKY) else INK)
                left += share
            ax.text(101, i, f"n={n}", va="center", fontsize=6.5, color=INK2)
        ax.set_yticks(range(3)); ax.set_yticklabels([f"{l} label" for l in labels])
        ax.invert_yaxis()
        ax.set_xlim(0, 112)
        ax.set_xticks([0, 25, 50, 75, 100])
        ax.set_title(bb, fontsize=7.5, loc="left")
        ax.spines[["top", "right", "left"]].set_visible(False)
        ax.tick_params(axis="y", length=0)
    axes[0].set_xlabel("share of failures (%)"); axes[1].set_xlabel("share of failures (%)")
    fig.legend(handles=[Patch(color=c, label=s) for c, s in zip(colors, short)],
               ncol=4, loc="lower center", bbox_to_anchor=(0.5, -0.08))
    fig.tight_layout()
    fig.savefig(OUT / "fig_labels.pdf", bbox_inches="tight")
    print("wrote", OUT / "fig_labels.pdf")


# ----------------------------------------------------------------------------
# Fig E: the interventional test
# ----------------------------------------------------------------------------
def fig_counterfactual() -> None:
    none = load_counterfactual("none")
    arms = [("schema", "DB", "gold schema → Selector"),
            ("intent", "NLQ", "original question → Decomposer")]
    fig, ax = plt.subplots(figsize=(3.6, 2.3))
    w = 0.36
    for j, (arm, grp, label) in enumerate(arms):
        m = load_counterfactual(arm)
        ctl = [v for v in none.values() if v.get("perturbation_group") == grp]
        for k, (name, vals, col) in enumerate((
                ("re-run, no change", ctl, C_GREY),
                ("stage repaired", list(m.values()), C_GREEN))):
            n = len(vals); r = sum(bool(v.get("repaired")) for v in vals)
            p = 100 * r / n if n else 0
            lo, hi = wilson(r, n)
            x = j + (k - 0.5) * (w + 0.04)
            ax.bar(x, p, w, color=col, edgecolor="white", lw=1, zorder=3)
            ax.errorbar(x, p, yerr=[[p - 100 * lo], [100 * hi - p]], fmt="none",
                        ecolor=INK, elinewidth=0.7, capsize=2, zorder=4)
            ax.text(x, 100 * hi + 1.5, f"{r}/{n}", ha="center", fontsize=6.5, color=INK)
    ax.set_xticks([0, 1])
    ax.set_xticklabels(["DB-perturbed failures\n(label: Schema)", "NLQ-perturbed failures\n(label: Intent)"])
    ax.set_ylabel("failures repaired (%)")
    ax.set_ylim(0, 62)
    _style(ax)
    ax.legend(handles=[Patch(color=C_GREY, label="re-run, no intervention (noise floor)"),
                       Patch(color=C_GREEN, label="labelled stage repaired with gold")],
              loc="upper left")
    fig.tight_layout()
    fig.savefig(OUT / "fig_counterfactual.pdf", bbox_inches="tight")
    print("wrote", OUT / "fig_counterfactual.pdf")


# ----------------------------------------------------------------------------
# Fig F: BIRD — the Selector acts and still separates nothing
# ----------------------------------------------------------------------------
def fig_bird() -> None:
    logs = load_bird("macsql")
    kept_pass, kept_fail = [], []
    for d in logs:
        kept = columns_kept(d)
        if kept is None:
            continue
        n = sum(len(v) for v in kept.values())
        (kept_pass if d.get("execution_match") else kept_fail).append(n)
    if not kept_pass or not kept_fail:
        print("fig_bird: no BIRD column data; skipped")
        return
    fig, ax = plt.subplots(figsize=(3.6, 2.2))
    for vals, col, ls, name in ((kept_pass, C_BLUE, "-", "passes"),
                                (kept_fail, C_VERM, "--", "failures")):
        xs = sorted(vals)
        ys = [(i + 1) / len(xs) for i in range(len(xs))]
        ax.step(xs, ys, where="post", color=col, ls=ls, lw=1.4, zorder=3,
                label=f"{name} (n={len(xs)}, mean {sum(xs)/len(xs):.1f})")
    ax.set_xlabel("columns forwarded by the Selector (BIRD dev)")
    ax.set_ylabel("cumulative share of instances")
    ax.set_ylim(0, 1.02)
    _style(ax)
    ax.legend(loc="lower right")
    fig.tight_layout()
    fig.savefig(OUT / "fig_bird.pdf", bbox_inches="tight")
    print("wrote", OUT / "fig_bird.pdf")


# ----------------------------------------------------------------------------
# Fig G: per-family failure rate vs recall (both backbones)
# ----------------------------------------------------------------------------
def fig_family() -> None:
    fig, axes = plt.subplots(1, 2, figsize=(6.4, 2.4), sharey=True)
    colour = {"NLQ": C_BLUE, "DB": C_VERM, "SQL": C_GREEN}
    marker = {"NLQ": "o", "DB": "s", "SQL": "^"}
    for ax, (bb, bdir) in zip(axes, BACKBONES):
        fam = defaultdict(lambda: {"n": 0, "fail": 0, "hit": 0})
        for d in load_drspider(bdir):
            truth = GT_STAGE.get(d.get("target_error_type"))
            if not truth:
                continue
            k = (d["perturbation_group"], d["source"].replace("drspider_", ""))
            fam[k]["n"] += 1
            if d.get("execution_match"):
                continue
            fam[k]["fail"] += 1
            fam[k]["hit"] += attribute_baseline(d) == truth
        for (grp, name), v in sorted(fam.items()):
            if not v["fail"]:
                continue
            ax.scatter(100 * v["fail"] / v["n"], 100 * v["hit"] / v["fail"],
                       c=colour[grp], marker=marker[grp], s=26, linewidths=0.6,
                       edgecolors="white", zorder=3)
        ax.set_xlabel("failure rate of the family (%)")
        ax.set_title(bb, fontsize=7.5, loc="left")
        ax.set_xlim(0, 70)
        _style(ax)
    axes[0].set_ylabel("stage recall (%)")
    axes[0].set_ylim(-4, 104)
    fig.legend(handles=[plt.Line2D([], [], marker=marker[g], ls="", color=colour[g],
                                   label=f"{g}-perturbed families")
                        for g in ("NLQ", "DB", "SQL")],
               ncol=3, loc="lower center", bbox_to_anchor=(0.5, -0.06))
    fig.tight_layout()
    fig.savefig(OUT / "fig_family.pdf", bbox_inches="tight")
    print("wrote", OUT / "fig_family.pdf")


# ----------------------------------------------------------------------------
# Fig H: the four-number audit as a picture (varies vs fires vs correct)
# ----------------------------------------------------------------------------
def fig_audit() -> None:
    fig, axes = plt.subplots(1, 2, figsize=(6.4, 2.2), sharey=True)
    for ax, (bb, bdir) in zip(axes, BACKBONES):
        logs = load_drspider(bdir)
        fails = [d for d in logs if not d.get("execution_match")
                 and GT_STAGE.get(d.get("target_error_type"))]
        n = len(fails)
        pred = [attribute_baseline(d) for d in fails]
        truth = [GT_STAGE[d["target_error_type"]] for d in fails]
        for i, s in enumerate(("Intent", "Schema", "Skeleton")):
            if s == "Schema":
                varies = sum(bool(gold_column_refs(d.get("gold_sql", "")) and attribute_baseline(d) == "Schema") for d in fails)
            elif s == "Intent":
                varies = sum(p == "Intent" for p in pred)
            else:
                varies = n
            fires = sum(p == s for p in pred)
            correct = sum(p == s and t == s for p, t in zip(pred, truth))
            vals = [100 * varies / n, 100 * fires / n, 100 * correct / n]
            for k, (v, col, lab) in enumerate(zip(vals, (C_GREY, C_SKY, C_BLUE), ("varies", "fires", "correct"))):
                ax.bar(i + (k - 1) * 0.26, v, 0.24, color=col, edgecolor="white", lw=0.8, zorder=3)
                ax.text(i + (k - 1) * 0.26, v + 1.5, f"{v:.0f}", ha="center", fontsize=6, color=INK2)
        ax.set_xticks(range(3)); ax.set_xticklabels(["Intent", "Schema", "Skeleton"])
        ax.set_title(f"{bb} — {n} failures", fontsize=7.5, loc="left")
        ax.set_ylim(0, 110)
        _style(ax)
    axes[0].set_ylabel("percent of failures")
    fig.legend(handles=[Patch(color=C_GREY, label="artifact varies"),
                        Patch(color=C_SKY, label="detector fires"),
                        Patch(color=C_BLUE, label="fires and correct")],
               ncol=3, loc="lower center", bbox_to_anchor=(0.5, -0.06))
    fig.tight_layout()
    fig.savefig(OUT / "fig_audit.pdf", bbox_inches="tight")
    print("wrote", OUT / "fig_audit.pdf")


def fig_forced() -> None:
    """DB group under the default sweep and the forced-Selector arm.

    Four bars per backbone: instances the default Selector left inert vs
    pruned, each under the default sweep and under the arm that forces the
    Selector to act. (a) EX barely moves; (b) schema recall on failures is
    zero on inert instances only while the Selector is inert. A backbone
    without a forced arm on disk is drawn with its default bars only.
    """
    rows = {}
    for bb, bdir in BACKBONES:
        base = {d["benchmark_id"]: d for d in load_drspider(bdir)
                if d.get("perturbation_group") == "DB"}
        arm = {d["benchmark_id"]: d for d in load_drspider(bdir + "_selector-force")
               if d.get("perturbation_group") == "DB"}
        for act in ("inert", "pruned"):
            ids = [i for i in base if pruned(base[i]) == (act == "pruned")]
            for arm_name, logs in (("default", base), ("forced", arm)):
                sub = [logs[i] for i in ids if i in logs]
                if len(sub) < len(ids):  # arm not run for this backbone
                    continue
                fails = [d for d in sub if not d.get("execution_match")]
                rows[(bb, act, arm_name)] = dict(
                    n=len(sub), ex=sum(bool(d.get("execution_match")) for d in sub),
                    nf=len(fails), hit=sum(attribute_baseline(d) == "Schema" for d in fails))

    fig, axes = plt.subplots(1, 2, figsize=(6.4, 2.3))
    w = 0.19
    order = [("inert", "default"), ("inert", "forced"), ("pruned", "default"), ("pruned", "forced")]
    for ax, key, ylabel, title in (
        (axes[0], "ex", "execution accuracy (%)", "(a) forcing the Selector to act leaves EX unchanged"),
        (axes[1], "rec", "schema recall on failures (%)", "(b) recall leaves zero only where the freed Selector prunes"),
    ):
        for j, (bb, _) in enumerate(BACKBONES):
            for k, (act, arm_name) in enumerate(order):
                r = rows.get((bb, act, arm_name))
                x = j + (k - 1.5) * (w + 0.03)
                if r is None:
                    continue
                num, den = (r["ex"], r["n"]) if key == "ex" else (r["hit"], r["nf"])
                v = 100 * num / den if den else 0
                lo, hi = wilson(num, den)
                ax.bar(x, v, w, color=C_ACT[act], edgecolor="white" if arm_name == "default" else INK,
                       hatch="" if arm_name == "default" else "////", lw=0.6, zorder=3)
                ax.errorbar(x, v, yerr=[[v - 100 * lo], [100 * hi - v]],
                            fmt="none", ecolor=INK, elinewidth=0.7, capsize=1.5, zorder=4)
                ax.text(x, 100 * hi + 1.5, f"{num}/{den}", ha="center", va="bottom",
                        fontsize=5.5, color=INK, rotation=90)
        ax.set_xticks([0, 1])
        ax.set_xticklabels([bb for bb, _ in BACKBONES])
        ax.set_ylabel(ylabel)
        ax.set_ylim(0, 112 if key == "ex" else 80)
        ax.set_title(title, fontsize=7.5, loc="left")
        _style(ax)
    fig.legend(handles=[Patch(color=C_ACT["inert"], label="Selector inert in the default sweep"),
                        Patch(color=C_ACT["pruned"], label="Selector pruned in the default sweep"),
                        Patch(facecolor="white", edgecolor=INK, hatch="////", label="forced to act")],
               ncol=3, loc="lower center", bbox_to_anchor=(0.5, -0.08), handlelength=1.4)
    fig.tight_layout()
    fig.savefig(OUT / "fig_forced.pdf", bbox_inches="tight")
    print("wrote", OUT / "fig_forced.pdf")


FIGS = {
    "activity": fig_activity,
    "forced": fig_forced,
    "chance": fig_chance,
    "confusion": fig_confusion,
    "labels": fig_labels,
    "counterfactual": fig_counterfactual,
    "bird": fig_bird,
    "family": fig_family,
    "audit": fig_audit,
}

if __name__ == "__main__":
    names = sys.argv[1:] or list(FIGS)
    for nm in names:
        FIGS[nm]()
