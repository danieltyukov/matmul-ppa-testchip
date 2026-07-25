#!/usr/bin/env python3
# Copyright 2026 Daniel Tyukov
# SPDX-License-Identifier: Apache-2.0
"""Charts of the routed results, and of the power proxy against measured power.

  pnr_area.png          synthesis cell area, routed cell area and die area per candidate
  pnr_fmax.png          post-route Fmax at all three PDK corners
  ppa_pareto_real.png   die area against measured power: the Pareto view in real units
  power_proxy_vs_real.png  the transition-count proxy against annotated power

Every chart refuses to draw if its input is missing rather than inventing a number.
Produce the inputs with `tools/run_pnr.py` and `tools/pdk_ppa.py`.
"""

from __future__ import annotations

import json
import pathlib

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent
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
    "infer": "0 infer",
    "wallace": "1 wallace",
    "booth4": "2 booth4",
    "signmag": "3 signmag",
    "bitserial": "4 bitserial",
}
CORNER_LABELS = {
    "nom_slow_1p08V_125C": "slow 1.08 V 125 C",
    "nom_typ_1p20V_25C": "typical 1.20 V 25 C",
    "nom_fast_1p32V_m40C": "fast 1.32 V -40 C",
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
        "axes.axisbelow": True,
        "figure.facecolor": "white",
        "savefig.facecolor": "white",
        "savefig.bbox": "tight",
    })


def load(path: pathlib.Path, how: str) -> dict | None:
    if not path.exists():
        print(f"plot_pnr: {path.relative_to(REPO)} is missing; run `{how}` first")
        return None
    return json.loads(path.read_text())


def names_in(pnr: dict) -> list[str]:
    return [n for n in ORDER if f"engine_{n}" in pnr]


def plot_area(pnr: dict, synth: dict | None):
    names = names_in(pnr)
    die = [pnr[f"engine_{n}"]["design__die__area"] / 1e6 for n in names]
    cells = [pnr[f"engine_{n}"]["design__instance__area__stdcell"] / 1e6 for n in names]
    pre = None
    if synth:
        pre = [synth[f"engine_{n}"]["cell_area_um2"] / 1e6 for n in names]

    fig, ax = plt.subplots(figsize=(9.2, 5.2))
    x = range(len(names))
    width = 0.27
    if pre:
        ax.bar([i - width for i in x], pre, width, color="#b9c2ca",
               label="cell area at synthesis")
    ax.bar(list(x), cells, width, color=[COLOURS[n] for n in names],
           label="cell area after routing")
    ax.bar([i + width for i in x], die, width,
           color=[COLOURS[n] for n in names], alpha=0.45, label="die area")
    ax.set_xticks(list(x))
    ax.set_xticklabels([LABELS[n] for n in names])
    ax.set_ylabel("area (square millimetres)")
    ax.set_title("Synthesis area against routed area, per candidate")
    ax.legend(frameon=False, fontsize=9.5)
    for i, (c, d) in enumerate(zip(cells, die)):
        ax.annotate(f"{d:.2f}", (i + width, d), textcoords="offset points",
                    xytext=(0, 4), ha="center", fontsize=8.5)
        ax.annotate(f"{c:.2f}", (i, c), textcoords="offset points",
                    xytext=(0, 4), ha="center", fontsize=8.5)
    ax.set_ylim(0, max(die) * 1.2)
    ax.text(0.0, -0.14,
            "LibreLane on IHP SG13G2, every candidate routed at the same 20 ns "
            "constraint and 40 percent target utilisation.\nRouted cell area is larger "
            "than synthesis because place and route adds the buffering the netlist "
            "needs; die area\nadds routing, the power grid and the fill the density "
            "rules require.",
            transform=ax.transAxes, fontsize=9, color="#5a6672", va="top")
    out = IMG / "pnr_area.png"
    fig.savefig(out)
    plt.close(fig)
    print(f"wrote {out.relative_to(REPO)}")


def plot_fmax(pnr: dict):
    names = names_in(pnr)
    corners = list(CORNER_LABELS)
    fig, ax = plt.subplots(figsize=(9.2, 5.0))
    x = range(len(names))
    width = 0.26
    shades = {"nom_slow_1p08V_125C": 1.0, "nom_typ_1p20V_25C": 0.62,
              "nom_fast_1p32V_m40C": 0.34}
    for index, corner in enumerate(corners):
        values = [pnr[f"engine_{n}"]["fmax_mhz_by_corner"][corner] for n in names]
        offset = (index - 1) * width
        ax.bar([i + offset for i in x], values, width,
               color=[COLOURS[n] for n in names], alpha=shades[corner],
               label=CORNER_LABELS[corner])
        if corner == "nom_slow_1p08V_125C":
            for i, v in zip(x, values):
                ax.annotate(f"{v:.0f}", (i + offset, v), textcoords="offset points",
                            xytext=(0, 4), ha="center", fontsize=8.5)
    ax.set_xticks(list(x))
    ax.set_xticklabels([LABELS[n] for n in names])
    ax.set_ylabel("maximum frequency (MHz)")
    ax.set_title("Post-route maximum frequency per candidate")
    ax.legend(frameon=False, fontsize=9.5, ncol=3)
    ax.text(0.0, -0.14,
            "Signoff static timing on the routed netlist with parasitics extracted from "
            "the routing, at all three PDK corners.\nFmax is 1/(period - worst setup "
            "slack) at a fixed 20 ns constraint. The slow corner is the one a tapeout "
            "closes at.",
            transform=ax.transAxes, fontsize=9, color="#5a6672", va="top")
    out = IMG / "pnr_fmax.png"
    fig.savefig(out)
    plt.close(fig)
    print(f"wrote {out.relative_to(REPO)}")


def plot_real_pareto(pnr: dict, pdk: dict, perf: dict, routed_power: dict | None):
    """Routed die area against energy per tile.

    Both axes have to come from the same stage or the chart is comparing a routed die
    against a synthesis netlist's power. Energy is taken from the post-route
    measurement where it exists and only falls back to the synthesis one otherwise,
    which the caption then says.
    """
    measured = (routed_power or {}).get("candidates", {})
    names = [n for n in names_in(pnr) if f"engine_{n}" in pdk]
    stage = "post-route" if all(f"engine_{n}" in measured for n in names) else "mixed"
    fig, ax = plt.subplots(figsize=(8.6, 6.0))
    points = []
    for n in names:
        x = pnr[f"engine_{n}"]["design__die__area"] / 1e6
        post = measured.get(f"engine_{n}")
        y = (post or pdk[f"engine_{n}"])["energy_per_tile_pj"]
        ax.scatter(x, y, s=200, color=COLOURS[n], edgecolor="white", linewidth=1.6,
                   zorder=3)
        cycles = perf["candidates"].get(n, {}).get("cycles") if perf else None
        points.append((x, y, n + (f"\n{cycles:,} cycles" if cycles else "")))
    # Candidates with the same cycle count land almost on top of each other, so the
    # labels have to be pushed apart or they overprint and none of them is readable.
    order = sorted(range(len(points)), key=lambda i: points[i][0])
    for rank, i in enumerate(order):
        x, y, note = points[i]
        offset = (12, 8) if rank % 2 == 0 else (12, -30)
        ax.annotate(note, (x, y), textcoords="offset points", xytext=offset,
                    fontsize=10, color=COLOURS[names[i]], fontweight="600")
    ax.margins(x=0.18, y=0.14)
    ax.set_xlabel("routed die area (square millimetres)")
    ax.set_ylabel("energy per tile launch (picojoules)")
    ax.set_title("Routed die area against measured energy")
    source = ("Energy is measured on the routed netlist, with the parasitics extracted "
              "from the routing, so both\naxes describe the same physical object."
              if stage == "post-route" else
              "Energy for candidates without a post-route measurement falls back to "
              "their synthesis netlist.")
    ax.text(0.0, -0.16,
            f"Both axes are physical units. Area is the routed die from LibreLane. "
            f"{source}\nSwitching activity is annotated from a gate level VCD under an "
            "identical operand stream at an even sign mix, at 100\npercent annotation "
            "coverage, times the time the candidate takes for one tile. Energy rather "
            "than power, because\na candidate that spreads the same work over eight "
            "cycles draws less power for longer. Down and to the left is\nbetter.",
            transform=ax.transAxes, fontsize=9, color="#5a6672", va="top")
    out = IMG / "ppa_pareto_real.png"
    fig.savefig(out)
    plt.close(fig)
    print(f"wrote {out.relative_to(REPO)}")


def plot_proxy_vs_real(pdk: dict, activity: dict):
    """How well does counting transitions predict watts?"""
    point = min(activity["points"],
                key=lambda p: abs(p["operand_stats"]["measured_neg_fraction"] - 0.5))
    per_candidate = point["per_candidate"]
    names = [n for n in ORDER if f"engine_{n}" in pdk and n in per_candidate]
    base = "wallace" if "wallace" in names else names[0]

    proxy = [per_candidate[n] / per_candidate[base] for n in names]
    energy = [pdk[f"engine_{n}"]["energy_per_tile_pj"]
              / pdk[f"engine_{base}"]["energy_per_tile_pj"] for n in names]
    total = [pdk[f"engine_{n}"]["power"]["total"]["total_w"]
             / pdk[f"engine_{base}"]["power"]["total"]["total_w"] for n in names]

    fig, ax = plt.subplots(figsize=(9.4, 5.2))
    x = range(len(names))
    width = 0.27
    ax.bar([i - width for i in x], proxy, width, color="#b9c2ca",
           label="transitions per tile (proxy)")
    ax.bar(list(x), energy, width, color=[COLOURS[n] for n in names],
           label="energy per tile (measured)")
    ax.bar([i + width for i in x], total, width,
           color=[COLOURS[n] for n in names], alpha=0.45,
           label="average power (measured)")
    for i, values in enumerate(zip(proxy, energy, total)):
        for offset, value in zip((-width, 0, width), values):
            ax.annotate(f"{value:.2f}", (i + offset, value),
                        textcoords="offset points", xytext=(0, 3), ha="center",
                        fontsize=8)
    ax.axhline(1.0, color="#5a6672", linewidth=0.9, linestyle="--", zorder=1)
    ax.set_xticks(list(x))
    ax.set_xticklabels([LABELS[n] for n in names])
    ax.set_ylabel(f"relative to {base} (= 1.00)")
    ax.set_title("The switching-activity proxy against measured energy")
    ax.legend(frameon=False, fontsize=9.5)
    ax.set_ylim(0, max(max(proxy), max(energy), max(total)) * 1.18)
    ax.text(0.0, -0.14,
            "Same netlists, same operand stream, an even sign mix. The first two bars "
            "answer the same question, energy for\none tile, so they are directly "
            "comparable. The proxy counts bit transitions with every net weighted "
            "equally, which\nmisses the internal power of the cells; that is why it "
            "overstates what the encodings buy and understates what an\neight-cycle "
            "candidate costs. Average power is the third bar and is a different "
            "question: a slower engine spreads the\nsame work over more time.",
            transform=ax.transAxes, fontsize=9, color="#5a6672", va="top")
    out = IMG / "power_proxy_vs_real.png"
    fig.savefig(out)
    plt.close(fig)
    print(f"wrote {out.relative_to(REPO)}")


def plot_sign_sweep(sweep: dict, activity: dict):
    """The sign-magnitude hypothesis in watts, next to the same test in transitions."""
    tops = sorted({p["top"] for p in sweep["points"]})
    fractions = sorted({p["neg_fraction"] for p in sweep["points"]})
    value = {(p["top"], p["neg_fraction"]): p for p in sweep["points"]}

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.4, 5.2))
    for top in tops:
        name = top.replace("engine_", "")
        ax1.plot(fractions, [value[(top, f)]["total_w"] * 1e3 for f in fractions],
                 marker="o", color=COLOURS.get(name, "#444"), label=name)
    ax1.set_xlabel("fraction of negative operands")
    ax1.set_ylabel(f"power at {1000.0 / sweep['clock_ns']:.0f} MHz (milliwatts)")
    ax1.set_title("Measured power against operand sign mix")
    ax1.legend(frameon=False, fontsize=9.5)

    if "engine_signmag" in tops and "engine_wallace" in tops:
        for key, label in (("total_w", "total power"),
                           ("switching_w", "switching power only")):
            ratio = [100.0 * (value[("engine_signmag", f)][key]
                              / value[("engine_wallace", f)][key] - 1.0)
                     for f in fractions]
            ax2.plot(fractions, ratio, marker="o", label=label)
        proxy = []
        for f in fractions:
            point = min(activity["points"],
                        key=lambda p: abs(
                            p["operand_stats"]["measured_neg_fraction"] - f))
            per = point["per_candidate"]
            proxy.append(100.0 * (per["signmag"] / per["wallace"] - 1.0))
        ax2.plot(fractions, proxy, marker="s", linestyle="--", color="#8a949c",
                 label="transition count (proxy)")
        ax2.axhline(0.0, color="#5a6672", linewidth=0.9)
        ax2.set_xlabel("fraction of negative operands")
        ax2.set_ylabel("signmag against wallace (percent)")
        ax2.set_title("What the encoding buys, measured three ways")
        ax2.legend(frameon=False, fontsize=9.5)

    fig.text(0.012, -0.02,
             "engine_signmag and engine_wallace share the same reduction tree and the "
             "same final adder, so the gap between them is the operand encoding and "
             "nothing else. Below zero means sign-magnitude is cheaper.",
             fontsize=9, color="#5a6672")
    out = IMG / "power_vs_signs_real.png"
    fig.savefig(out)
    plt.close(fig)
    print(f"wrote {out.relative_to(REPO)}")


def main() -> int:
    style()
    IMG.mkdir(parents=True, exist_ok=True)

    pnr_doc = load(RESULTS / "pnr" / "summary.json", "tools/run_pnr.py")
    pdk_doc = load(RESULTS / "pdk" / "summary.json", "tools/pdk_ppa.py")
    activity = load(RESULTS / "activity" / "gate_summary.json", "make power")
    perf = load(RESULTS / "perf" / "cycle_counts.json", "make sim")
    routed_power = load(RESULTS / "pnr" / "routed_power.json",
                        "tools/verify_routed.py")

    if pnr_doc:
        pnr = pnr_doc["candidates"]
        plot_area(pnr, pdk_doc["candidates"] if pdk_doc else None)
        plot_fmax(pnr)
    if pnr_doc and pdk_doc:
        pdk = dict(pdk_doc["candidates"])
        pdk["_clock_ns"] = next(iter(pdk_doc["candidates"].values()))["power_clock_ns"]
        plot_real_pareto(pnr_doc["candidates"], pdk, perf, routed_power)
    if pdk_doc and activity:
        plot_proxy_vs_real(pdk_doc["candidates"], activity)

    sweep = RESULTS / "pdk" / "sign_sweep.json"
    if sweep.exists() and activity:
        plot_sign_sweep(json.loads(sweep.read_text()), activity)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
