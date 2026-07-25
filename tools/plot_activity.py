#!/usr/bin/env python3
# Copyright 2026 Daniel Tyukov
# SPDX-License-Identifier: Apache-2.0
"""Switching-activity charts, all from committed data under results/activity.

  activity_totals.png     transitions per candidate for a fixed workload
  activity_vs_signs.png   transitions against the fraction of negative operands,
                          which is the mechanism sign-magnitude encoding exploits
  activity_modules.png    per-module breakdown inside the structural candidates

The sign sweep is the chart that settles the sign-magnitude hypothesis one way or
the other, so it is drawn with the crossing point marked rather than smoothed over.
"""

from __future__ import annotations

import json
import pathlib
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tb"))

import gemm_model as gm  # noqa: E402

IMG = REPO / "docs" / "img"
ACT = REPO / "results" / "activity"

ORDER = ["infer", "wallace", "booth4", "signmag", "bitserial"]
COLOURS = {
    "infer": "#2f6fb3",
    "wallace": "#2e7d4f",
    "booth4": "#b5761f",
    "signmag": "#6b4fa8",
    "bitserial": "#b03d3d",
}
MARKERS = {"infer": "o", "wallace": "s", "booth4": "^", "signmag": "D",
           "bitserial": "v"}


def style():
    plt.rcParams.update({
        "figure.dpi": 130,
        "font.size": 10.5,
        "axes.titlesize": 13,
        "axes.titleweight": "600",
        "axes.labelsize": 11,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "grid.alpha": 0.25,
        "axes.axisbelow": True,
        "figure.facecolor": "white",
        "savefig.facecolor": "white",
        "savefig.bbox": "tight",
    })


def need(path: pathlib.Path, how: str) -> dict:
    if not path.exists():
        raise SystemExit(
            f"plot_activity: {path.relative_to(REPO)} is missing. Run `{how}` first."
        )
    return json.loads(path.read_text())


def plot_totals(gate: dict):
    point = min(gate["points"],
                key=lambda p: abs(p["operand_stats"]["measured_neg_fraction"] - 0.5))
    per = point["per_candidate"]
    neg = point["operand_stats"]["measured_neg_fraction"]
    names = [n for n in ORDER if n in per]
    values = [per[n] / point["tiles"] for n in names]

    fig, ax = plt.subplots(figsize=(8.0, 4.8))
    bars = ax.bar(range(len(names)), values, color=[COLOURS[n] for n in names])
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels(names)
    ax.set_ylabel("bit transitions per tile")
    ax.set_title(f"Gate level switching activity at {neg:.0%} negative operands")
    for bar, value in zip(bars, values):
        ax.annotate(f"{value:,.0f}", (bar.get_x() + bar.get_width() / 2, value),
                    textcoords="offset points", xytext=(0, 4), ha="center",
                    fontsize=9)
    ax.set_ylim(0, max(values) * 1.16)

    baseline = per.get("wallace")
    if baseline:
        sm = per.get("signmag")
        if sm:
            delta = (sm - baseline) / baseline * 100
            ax.text(0.0, -0.20,
                    f"signmag against wallace, which is the controlled comparison "
                    f"(same reduction tree, different operand encoding): "
                    f"{delta:+.1f}%.",
                    transform=ax.transAxes, fontsize=9.5, color="#5a6672", va="top")
    ax.text(0.0, -0.28,
            f"{point['tiles']} tile launches per candidate on identical operands, "
            f"counted on Yosys generic gate netlists.\nA proxy for dynamic power, "
            f"not power: see docs/PPA_METHODOLOGY.md.",
            transform=ax.transAxes, fontsize=9, color="#5a6672", va="top")
    out = IMG / "activity_totals.png"
    fig.savefig(out)
    plt.close(fig)
    print(f"wrote {out}")


def plot_vs_signs(gate: dict):
    points = sorted(gate["points"],
                    key=lambda p: p["operand_stats"]["measured_neg_fraction"])
    xs = [p["operand_stats"]["measured_neg_fraction"] for p in points]
    names = [n for n in ORDER if n in points[0]["per_candidate"]]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.6, 5.2))

    for n in names:
        ys = [p["per_candidate"][n] / p["tiles"] for p in points]
        ax1.plot(xs, ys, marker=MARKERS[n], color=COLOURS[n], label=n, linewidth=2,
                 markersize=6)
    ax1.set_xlabel("fraction of operands that are negative")
    ax1.set_ylabel("bit transitions per tile")
    ax1.set_title("Absolute activity against operand sign mix")
    ax1.legend(frameon=False, fontsize=9.5)

    # Normalised against the controlled comparison, so the encoding effect is the
    # only thing left in the picture.
    base = "wallace"
    if base in names:
        for n in names:
            if n == base:
                continue
            ys = [p["per_candidate"][n] / p["per_candidate"][base] for p in points]
            ax2.plot(xs, ys, marker=MARKERS[n], color=COLOURS[n], label=n,
                     linewidth=2, markersize=6)
        ax2.axhline(1.0, color=COLOURS[base], linewidth=2, label=f"{base} (reference)")
        ax2.set_xlabel("fraction of operands that are negative")
        ax2.set_ylabel(f"activity relative to {base}")
        ax2.set_title(f"Relative to {base}: below 1.0 is less activity")
        ax2.legend(frameon=False, fontsize=9.5)

    fig.suptitle(
        "Does sign-magnitude encoding reduce switching activity?", fontsize=14,
        fontweight="600", y=1.02,
    )
    fig.text(0.09, -0.03,
             "engine_signmag and engine_wallace share the same 3:2 reduction tree "
             "and differ only in operand encoding, so the gap between them is the "
             "encoding effect and nothing else.",
             fontsize=9.5, color="#5a6672")
    out = IMG / "activity_vs_signs.png"
    fig.savefig(out)
    plt.close(fig)
    print(f"wrote {out}")


def plot_modules(rtl: dict):
    """Per-module breakdown inside each structural candidate, from the RTL sweep."""
    point = min(rtl["points"],
                key=lambda p: abs(p["operand_stats"]["measured_neg_fraction"] - 0.5))
    tag = point["tag"]
    detail = need(ACT / f"engines_{tag}.json", "make power")
    breakdown = detail["per_candidate_module_breakdown"]

    # engine_infer has almost no RTL submodule structure, so it is left out here
    # rather than shown as a misleadingly empty bar.
    names = [n for n in ORDER if n in breakdown and n != "infer"]
    children = sorted({child for n in names for child in breakdown[n]})

    fig, ax = plt.subplots(figsize=(9.6, 5.2))
    bottoms = [0.0] * len(names)
    palette = plt.get_cmap("tab20").colors
    for i, child in enumerate(children):
        values = [breakdown[n].get(child, 0) / point["tiles"] for n in names]
        ax.bar(range(len(names)), values, bottom=bottoms, label=child,
               color=palette[i % len(palette)])
        bottoms = [b + v for b, v in zip(bottoms, values)]

    ax.set_xticks(range(len(names)))
    ax.set_xticklabels(names)
    ax.set_ylabel("RTL bit transitions per tile")
    ax.set_title("Where the activity is, inside each structural candidate")
    ax.legend(frameon=False, fontsize=8.5, ncol=2, loc="upper left")
    ax.text(0.0, -0.16,
            "RTL nets, so these are internal proportions rather than numbers "
            "comparable with the gate level chart. engine_infer is\nomitted: it is "
            "a behavioural multiply with no submodule structure to break down.",
            transform=ax.transAxes, fontsize=9, color="#5a6672", va="top")
    out = IMG / "activity_modules.png"
    fig.savefig(out)
    plt.close(fig)
    print(f"wrote {out}")


def main() -> int:
    style()
    IMG.mkdir(parents=True, exist_ok=True)
    gate = need(ACT / "gate_summary.json", "make power")
    rtl = need(ACT / "summary.json", "make power")
    plot_totals(gate)
    plot_vs_signs(gate)
    plot_modules(rtl)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
