#!/usr/bin/env python3
# Copyright 2026 Daniel Tyukov
# SPDX-License-Identifier: Apache-2.0
"""Write docs/img/dataflow_output_stationary.svg.

Shows the loop nest the sequencer runs and, next to it, why the dataflow is called
output stationary: the accumulator for one output tile stays put while the K tiles
stream through it. The numbers come from the same parameters as the RTL.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tb"))

import gemm_model as gm  # noqa: E402

W, H = 1240, 680
INK = "#12181f"
MUTED = "#5a6672"
A_FILL = "#dbeafe"
A_LINE = "#2f6fb3"
B_FILL = "#dcfce7"
B_LINE = "#2e7d4f"
C_FILL = "#f3e8ff"
C_LINE = "#6b4fa8"
HOT = "#f97316"
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"


def esc(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def text(x, y, s, size=13, colour=INK, anchor="start", weight="400", mono=False):
    family = f' font-family="{MONO}"' if mono else ""
    return (f'<text x="{x}" y="{y}" font-size="{size}" fill="{colour}" '
            f'text-anchor="{anchor}" font-weight="{weight}"{family}>{esc(s)}</text>')


def grid(x0, y0, cell, rows, cols, fill, line, highlight=None, hl_fill=HOT):
    """A rows x cols tile grid; highlight is a set of (row, col) tile coordinates."""
    out = []
    for r in range(rows):
        for c in range(cols):
            f = hl_fill if highlight and (r, c) in highlight else fill
            op = "0.9" if highlight and (r, c) in highlight else "0.65"
            out.append(
                f'<rect x="{x0 + c * cell}" y="{y0 + r * cell}" width="{cell}" '
                f'height="{cell}" fill="{f}" fill-opacity="{op}" stroke="{line}" '
                f'stroke-width="0.9"/>'
            )
    out.append(f'<rect x="{x0}" y="{y0}" width="{cols * cell}" '
               f'height="{rows * cell}" fill="none" stroke="{line}" '
               f'stroke-width="2"/>')
    return "\n".join(out)


def build() -> str:
    cell = 26
    gm_, gn, gk = gm.GRID_M, gm.GRID_N, gm.GRID_K
    p = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
         f'width="{W}" height="{H}" font-family="Inter, Segoe UI, Helvetica, Arial, '
         f'sans-serif" role="img" aria-label="Output-stationary tiled GEMM dataflow">',
         f'<rect width="{W}" height="{H}" fill="#ffffff"/>']

    p.append(text(32, 42, "Output-stationary tiled GEMM", 24, INK, weight="700"))
    p.append(text(32, 66, f"One trigger runs the whole product. Highlighted: "
                          f"output tile (mt=2, nt=3) being accumulated over all "
                          f"{gk} K tiles.", 13.5, MUTED))

    # A
    ax, ay = 60, 120
    p.append(text(ax, ay - 14, f"A   {gm.MAT_M} x {gm.MAT_K} INT8, drawn as its {gm.GRID_M} x {gm.GRID_K} tile grid", 13, A_LINE,
                  weight="600"))
    p.append(grid(ax, ay, cell, gm_, gk, A_FILL, A_LINE,
                  highlight={(2, k) for k in range(gk)}))
    p.append(text(ax + gk * cell / 2, ay + gm_ * cell + 20,
                  f"each cell is one {gm.TILE_M}x{gm.TILE_K} tile", 11.5, MUTED, anchor="middle"))
    p.append(text(ax - 10, ay + 2 * cell + 17, "mt=2", 11.5, HOT, anchor="end",
                  weight="600"))

    # B
    bx, by = ax + gk * cell + 90, 120
    p.append(text(bx, by - 14, f"B   {gm.MAT_K} x {gm.MAT_N} INT8, {gm.GRID_K} x {gm.GRID_N} tiles", 13, B_LINE,
                  weight="600"))
    p.append(grid(bx, by, cell, gk, gn, B_FILL, B_LINE,
                  highlight={(k, 3) for k in range(gk)}))
    p.append(text(bx + 3 * cell + cell / 2, by - 30, "nt=3", 11.5, HOT,
                  anchor="middle", weight="600"))

    # C
    cx, cy = bx + gn * cell + 90, 120
    p.append(text(cx, cy - 14, f"C = A B   {gm.MAT_M} x {gm.MAT_N} INT{gm.ACC_W}, {gm.GRID_M} x {gm.GRID_N} tiles",
                  13, C_LINE, weight="600"))
    p.append(grid(cx, cy, cell, gm_, gn, C_FILL, C_LINE, highlight={(2, 3)}))
    p.append(text(cx + gn * cell + 12, cy + 2 * cell + 17,
                  "this tile stays in acc_bank", 11.5, HOT, weight="600"))

    p.append(f'<path d="M {ax + gk * cell + 14} {ay + 2 * cell + 13} L '
             f'{bx - 14} {ay + 2 * cell + 13}" stroke="{MUTED}" stroke-width="1.6" '
             f'fill="none"/>')
    p.append(text((ax + gk * cell + bx) / 2, ay + 2 * cell + 6, "x", 16, MUTED,
                  anchor="middle", weight="700"))
    p.append(text((bx + gn * cell + cx) / 2, ay + 2 * cell + 6, "=", 16, MUTED,
                  anchor="middle", weight="700"))

    # Loop nest
    lx, ly = 60, 370
    p.append(text(lx, ly, "What gemm_sequencer runs", 16, INK, weight="700"))
    code = [
        (f"for mt in 0 .. {gm_ - 1}:", 0),
        (f"for nt in 0 .. {gn - 1}:", 1),
        ("acc_bank <= 0", 2),
        (f"for kt in 0 .. {gk - 1}:", 2),
        (f"fetch A tile (mt, kt)   {gm.TILE_M} reads from store A", 3),
        (f"fetch B tile (kt, nt)   {gm.TILE_K} reads from store B", 3),
        ("launch engine, wait for valid", 3),
        ("acc_bank += A tile x B tile", 3),
        (f"write acc_bank to store C (mt, nt)   {gm.TILE_M} writes", 2),
    ]
    for i, (line, depth) in enumerate(code):
        y = ly + 28 + i * 22
        colour = HOT if "acc_bank" in line else INK
        weight = "600" if "acc_bank" in line else "400"
        p.append(text(lx + 16 + depth * 22, y, line, 13, colour, weight=weight,
                      mono=True))
    p.append(f'<rect x="{lx}" y="{ly + 12}" width="560" height="{28 + len(code) * 22}" '
             f'rx="8" fill="none" stroke="{MUTED}" stroke-width="1.2" '
             f'stroke-dasharray="5 4"/>')

    # Why it is called output stationary
    rx_, ry = 680, 370
    p.append(text(rx_, ry, "Why output stationary", 16, INK, weight="700"))
    bullets = [
        f"The {gm.TILE_M}x{gm.TILE_N} accumulator tile is written once per output "
        f"tile position,",
        f"not once per K tile. {gk} partial products are summed in place, so no",
        "partial sum ever leaves the accumulator registers.",
        "",
        f"Each A tile is fetched {gn} times (once per nt) and each B tile {gm_} times",
        f"(once per mt). That reuse is the price paid for keeping C resident.",
        "",
        f"Operand traffic per run: {gm_ * gn * gk * gm.TILE_M} A word reads + "
        f"{gm_ * gn * gk * gm.TILE_K} B word reads",
        f"Result traffic per run: {gm_ * gn * gm.TILE_M} C word writes",
        f"Total MACs: {gm.MAT_M * gm.MAT_N * gm.MAT_K}",
    ]
    for i, line in enumerate(bullets):
        p.append(text(rx_, ry + 30 + i * 21, line, 13,
                      INK if line and not line.startswith("Total") else MUTED,
                      weight="600" if line.startswith("Total") else "400"))
    p.append(f'<rect x="{rx_ - 16}" y="{ry + 12}" width="500" '
             f'height="{30 + len(bullets) * 21}" rx="8" fill="none" '
             f'stroke="{MUTED}" stroke-width="1.2" stroke-dasharray="5 4"/>')

    p.append(text(60, H - 24,
                  f"Generated by tools/svg_dataflow.py from the same geometry "
                  f"parameters as rtl/pkg/gemm_pkg.sv", 11.5, MUTED))
    p.append("</svg>")
    return "\n".join(p) + "\n"


def main() -> int:
    out = REPO / "docs" / "img" / "dataflow_output_stationary.svg"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build())
    print(f"wrote {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
