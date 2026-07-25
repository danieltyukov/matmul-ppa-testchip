#!/usr/bin/env python3
# Copyright 2026 Daniel Tyukov
# SPDX-License-Identifier: Apache-2.0
"""PPA charts, all from committed measurement data under results/.

  ppa_area.png        cell count and gate equivalents per candidate
  ppa_depth.png       longest topological path per candidate
  ppa_cycles.png      cycles and throughput per candidate, from the chip's counters
  ppa_pareto.png      area against switching activity, the view this chip exists for

Every chart refuses to draw if the data it needs has not been produced, rather than
inventing a placeholder. Run `make synth`, `make sim` and `make power` first.
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
RESULTS = REPO / "results"

ORDER = ["infer", "wallace", "booth4", "signmag", "bitserial"]
COLOURS = {
    "infer": "#2f6fb3",
    "wallace": "#2e7d4f",
    "booth4": "#b5761f",
    "signmag": "#6b4fa8",
    "bitserial": "#b03d3d",
}
LABELS = {
    "infer": "0 infer\n(synthesiser's choice)",
    "wallace": "1 wallace\n(3:2 CSA tree)",
    "booth4": "2 booth4\n(radix-4 Booth)",
    "signmag": "3 signmag\n(sign-magnitude)",
    "bitserial": "4 bitserial\n(8 cycles/tile)",
}


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
        "grid.linestyle": "-",
        "axes.axisbelow": True,
        "figure.facecolor": "white",
        "savefig.facecolor": "white",
        "savefig.bbox": "tight",
    })


def need(path: pathlib.Path, how: str) -> dict:
    if not path.exists():
        raise SystemExit(
            f"plot_ppa: {path.relative_to(REPO)} is missing. Run `{how}` first; "
            f"this script will not invent data."
        )
    return json.loads(path.read_text())


def annotate(ax, xs, ys, fmt="{:,.0f}", dy=0.02):
    span = max(ys) if ys else 1
    for x, y in zip(xs, ys):
        ax.annotate(fmt.format(y), (x, y), textcoords="offset points",
                    xytext=(0, 4), ha="center", fontsize=9)
    ax.set_ylim(0, span * 1.16)


def plot_area(synth: dict):
    tops = synth["tops"]
    names = [n for n in ORDER if f"engine_{n}" in tops]
    cells = [tops[f"engine_{n}"]["total_cells"] for n in names]
    ge = [tops[f"engine_{n}"]["gate_equivalents"] for n in names]
    flops = [tops[f"engine_{n}"]["flip_flops"] for n in names]
    comb = [tops[f"engine_{n}"]["combinational_cells"] for n in names]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.2, 5.0))
    x = range(len(names))

    ax1.bar(x, comb, color=[COLOURS[n] for n in names], label="combinational")
    ax1.bar(x, flops, bottom=comb, color=[COLOURS[n] for n in names], alpha=0.42,
            label="flip-flops")
    ax1.set_xticks(list(x))
    ax1.set_xticklabels([LABELS[n] for n in names], fontsize=8.5)
    ax1.set_ylabel("generic cells")
    ax1.set_title("Cell count per candidate")
    ax1.legend(frameon=False, fontsize=9)
    annotate(ax1, list(x), cells)

    ax2.bar(x, ge, color=[COLOURS[n] for n in names])
    ax2.set_xticks(list(x))
    ax2.set_xticklabels([LABELS[n] for n in names], fontsize=8.5)
    ax2.set_ylabel("gate equivalents (NAND2 = 1)")
    ax2.set_title("Gate equivalents per candidate")
    annotate(ax2, list(x), ge)

    fig.suptitle(
        f"Yosys generic synthesis, {gm.TILE_M}x{gm.TILE_N}x{gm.TILE_K} tile "
        f"({gm.TILE_M * gm.TILE_N * gm.TILE_K} MACs per launch). "
        f"Unit-cost gates, not PDK area.",
        fontsize=11, y=1.02,
    )
    out = IMG / "ppa_area.png"
    fig.savefig(out)
    plt.close(fig)
    print(f"wrote {out}")


def plot_depth(synth: dict):
    tops = synth["tops"]
    names = [n for n in ORDER if f"engine_{n}" in tops]
    depth = [tops[f"engine_{n}"]["logic_depth"] or 0 for n in names]

    fig, ax = plt.subplots(figsize=(7.4, 4.6))
    ax.bar(range(len(names)), depth, color=[COLOURS[n] for n in names])
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels([LABELS[n] for n in names], fontsize=8.5)
    ax.set_ylabel("longest topological path (gate levels)")
    ax.set_title("Logic depth per candidate")
    annotate(ax, range(len(names)), depth)
    ax.text(0.0, -0.24,
            "Combinational depth through the mapped netlist, as reported by Yosys "
            "ltp. A shorter path means a higher\nachievable clock, but this is a "
            "gate count not a delay: no cell timing is involved.",
            transform=ax.transAxes, fontsize=9, color="#5a6672", va="top")
    out = IMG / "ppa_depth.png"
    fig.savefig(out)
    plt.close(fig)
    print(f"wrote {out}")


def plot_cycles(perf: dict):
    cands = perf["candidates"]
    names = [n for n in ORDER if n in cands]
    cycles = [cands[n]["cycles"] for n in names]
    throughput = [cands[n]["macs_per_cycle"] for n in names]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.2, 4.8))
    ax1.bar(range(len(names)), cycles, color=[COLOURS[n] for n in names])
    ax1.set_xticks(range(len(names)))
    ax1.set_xticklabels([LABELS[n] for n in names], fontsize=8.5)
    ax1.set_ylabel("core cycles per full run")
    ax1.set_title(f"Cycles for one {gm.MAT_M}x{gm.MAT_N}x{gm.MAT_K} product")
    annotate(ax1, range(len(names)), cycles)

    ax2.bar(range(len(names)), throughput, color=[COLOURS[n] for n in names])
    ax2.set_xticks(range(len(names)))
    ax2.set_xticklabels([LABELS[n] for n in names], fontsize=8.5)
    ax2.set_ylabel("MACs per cycle")
    ax2.set_title("Throughput")
    annotate(ax2, range(len(names)), throughput, fmt="{:.2f}")

    fig.suptitle(
        f"Read out of the chip's own performance counters over SPI after a full "
        f"run. Every candidate retires {perf['candidates'][names[0]]['macs']:,} MACs.",
        fontsize=11, y=1.03,
    )
    out = IMG / "ppa_cycles.png"
    fig.savefig(out)
    plt.close(fig)
    print(f"wrote {out}")


def plot_pareto(synth: dict, activity: dict, perf: dict):
    """Area against switching activity: the trade-off the chip exists to measure."""
    tops = synth["tops"]
    # Use the sweep point closest to an even mix of signs, which is the realistic
    # operating point for activations and weights that straddle zero.
    point = min(activity["points"],
                key=lambda p: abs(p["operand_stats"]["measured_neg_fraction"] - 0.5))
    per_candidate = point["per_candidate"]
    neg = point["operand_stats"]["measured_neg_fraction"]

    names = [n for n in ORDER if f"engine_{n}" in tops and n in per_candidate]
    fig, ax = plt.subplots(figsize=(8.4, 6.0))

    for n in names:
        x = tops[f"engine_{n}"]["total_cells"]
        y = per_candidate[n] / point["tiles"]
        ax.scatter(x, y, s=190, color=COLOURS[n], edgecolor="white", linewidth=1.6,
                   zorder=3)
        cycles = perf["candidates"][n]["cycles"] if n in perf["candidates"] else None
        note = f"{n}" + (f"\n{cycles:,} cycles" if cycles else "")
        ax.annotate(note, (x, y), textcoords="offset points", xytext=(12, 6),
                    fontsize=10, color=COLOURS[n], fontweight="600")

    ax.set_xlabel("generic cells (area proxy)")
    ax.set_ylabel("bit transitions per tile (dynamic power proxy)")
    ax.set_title("Area against switching activity")
    ax.text(0.0, -0.16,
            f"Gate level transition counts at {neg:.0%} negative operands, one point "
            f"per candidate. Down and to the left is better.\nNeither axis is a "
            f"physical unit: cells are unit-cost generic gates and transitions are a "
            f"proxy for dynamic power,\nnot power. Cycle counts are measured and "
            f"labelled because a candidate can buy area with time.",
            transform=ax.transAxes, fontsize=9, color="#5a6672", va="top")
    out = IMG / "ppa_pareto.png"
    fig.savefig(out)
    plt.close(fig)
    print(f"wrote {out}")


def main() -> int:
    style()
    IMG.mkdir(parents=True, exist_ok=True)

    synth = need(RESULTS / "synth" / "generic" / "summary.json", "make synth")
    perf = need(RESULTS / "perf" / "cycle_counts.json", "make sim")
    activity = need(RESULTS / "activity" / "gate_summary.json", "make power")

    plot_area(synth)
    plot_depth(synth)
    plot_cycles(perf)
    plot_pareto(synth, activity, perf)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
