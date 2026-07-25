#!/usr/bin/env python3
# Copyright 2026 Daniel Tyukov
# SPDX-License-Identifier: Apache-2.0
"""Write docs/img/floorplan_estimate.png.

This is an AREA ESTIMATE, not a layout. No place and route has been run, because
OpenROAD and the IHP SG13G2 physical views are not installed in the environment
this repository was developed in. Every block is drawn with an area proportional to
its synthesised cell count, arranged into a rectangle; the positions carry no
physical meaning at all.

The figure is labelled as an estimate on its face, in the caption and in the file
name, because presenting it as a layout would be a lie. If the PDK is available,
`make flow` produces a real GDS and tools/render_gds.py renders that instead.
"""

from __future__ import annotations

import json
import math
import pathlib

import matplotlib

matplotlib.use("Agg")
import matplotlib.patches as patches  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent

IMG = REPO / "docs" / "img"
SYNTH = REPO / "results" / "synth"

COLOURS = {
    "engine_infer": "#2f6fb3",
    "engine_wallace": "#2e7d4f",
    "engine_booth4": "#b5761f",
    "engine_signmag": "#6b4fa8",
    "engine_bitserial": "#b03d3d",
    "shared logic": "#7a8794",
    "storage (as flip-flops)": "#c2a83e",
}


def load_summary() -> dict:
    path = SYNTH / "generic" / "summary.json"
    if not path.exists():
        raise SystemExit(
            f"plot_floorplan: {path.relative_to(REPO)} is missing. Run `make synth`."
        )
    return json.loads(path.read_text())


def squarify(items, width, height, x=0.0, y=0.0):
    """Lay rectangles out to fill a box, area proportional to value.

    A simple alternating split rather than a real squarified treemap: with seven
    blocks the difference is not worth the extra code, and the point of the figure
    is the relative sizes, not the packing.
    """
    total = sum(value for _, value in items)
    out = []
    remaining = list(items)
    cx, cy, cw, ch = x, y, width, height
    left = total
    while remaining:
        name, value = remaining.pop(0)
        if not remaining:
            out.append((name, cx, cy, cw, ch))
            break
        share = value / left
        if cw >= ch:
            w = cw * share
            out.append((name, cx, cy, w, ch))
            cx += w
            cw -= w
        else:
            h = ch * share
            out.append((name, cx, cy, cw, h))
            cy += h
            ch -= h
        left -= value
    return out


def main() -> int:
    plt.rcParams.update({
        "figure.dpi": 130,
        "font.size": 10.5,
        "figure.facecolor": "white",
        "savefig.facecolor": "white",
        "savefig.bbox": "tight",
    })
    IMG.mkdir(parents=True, exist_ok=True)
    summary = load_summary()
    tops = summary["tops"]

    engines = [f"engine_{n}" for n in
               ["infer", "wallace", "booth4", "signmag", "bitserial"]
               if f"engine_{n}" in tops]
    engine_cells = {name: tops[name]["total_cells"] for name in engines}

    if "gemm_bench_chip" not in tops or "engine_array" not in tops:
        raise SystemExit("plot_floorplan: the chip and engine_array reports are "
                         "needed; run `make synth` without --tops")

    chip_cells = tops["gemm_bench_chip"]["total_cells"]
    array_cells = tops["engine_array"]["total_cells"]
    engines_total = sum(engine_cells.values())

    # engine_array minus the candidates is the clock gating and isolation overhead;
    # the chip minus engine_array is everything else, which at this level of
    # abstraction is dominated by the stores mapped to flip-flops.
    isolation = max(array_cells - engines_total, 0)
    rest = max(chip_cells - array_cells, 0)

    blocks = [(name, engine_cells[name]) for name in engines]
    blocks.append(("shared logic", isolation))
    blocks.append(("storage (as flip-flops)", rest))
    blocks.sort(key=lambda kv: -kv[1])

    side = math.sqrt(chip_cells)
    rects = squarify(blocks, side, side)

    fig, ax = plt.subplots(figsize=(9.2, 8.0))
    for name, x, y, w, h in rects:
        colour = COLOURS.get(name, "#999999")
        ax.add_patch(patches.Rectangle((x, y), w, h, facecolor=colour, alpha=0.72,
                                       edgecolor="white", linewidth=2))
        cells = dict(blocks)[name]
        share = cells / chip_cells * 100
        if w * h > side * side * 0.012:
            ax.text(x + w / 2, y + h / 2,
                    f"{name}\n{cells:,} cells\n{share:.1f}%",
                    ha="center", va="center", fontsize=9.5, color="white",
                    fontweight="600")

    ax.set_xlim(0, side)
    ax.set_ylim(0, side)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_edgecolor("#5a6672")
        spine.set_linewidth(1.6)

    ax.set_title("Area estimate, NOT a layout", fontsize=15, fontweight="700",
                 pad=14)
    fig.text(0.5, 0.055,
             f"Block areas are proportional to synthesised generic cell counts "
             f"({chip_cells:,} cells for the whole chip). Positions are arbitrary.\n"
             f"No place and route has been run: OpenROAD and the IHP SG13G2 "
             f"physical views are not installed here, so there is no layout to "
             f"show.\nThe four matrix stores are mapped to flip-flops in this build "
             f"because no SRAM macros are bound, which is why storage dominates. "
             f"With\nreal macros the arithmetic would dominate instead. See "
             f"docs/PPA_METHODOLOGY.md.",
             ha="center", fontsize=9.5, color="#5a6672")

    out = IMG / "floorplan_estimate.png"
    fig.savefig(out)
    plt.close(fig)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
