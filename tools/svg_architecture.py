#!/usr/bin/env python3
# Copyright 2026 Daniel Tyukov
# SPDX-License-Identifier: Apache-2.0
"""Write docs/img/architecture.svg.

The SVG markup is written by hand here rather than exported from a drawing tool, so
the diagram is a source file: it lives in version control, it diffs, and the labels
are generated from the same geometry parameters as the RTL, which means it cannot
drift out of date the way a screenshot does.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tb"))

import gemm_model as gm  # noqa: E402

W, H = 1300, 1020

INK = "#12181f"
MUTED = "#5a6672"
EDGE = "#8b98a5"
HOST = "#e8f1fb"
HOST_LINE = "#2f6fb3"
MEM = "#eaf6ec"
MEM_LINE = "#2e7d4f"
CTRL = "#fff3e0"
CTRL_LINE = "#b5761f"
ENG = "#f3ecfb"
ENG_LINE = "#6b4fa8"
MEAS = "#fdecec"
MEAS_LINE = "#b03d3d"
PAD = "#f4f6f8"
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"


def esc(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def box(x, y, w, h, fill, stroke, title, lines=(), rx=8, title_size=17,
        line_size=12.5):
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" '
           f'fill="{fill}" stroke="{stroke}" stroke-width="1.8"/>']
    cx = x + w / 2
    out.append(f'<text x="{cx:.0f}" y="{y + 24}" text-anchor="middle" '
               f'font-size="{title_size}" font-weight="600" fill="{INK}" '
               f'font-family="{MONO}">{esc(title)}</text>')
    for i, line in enumerate(lines):
        out.append(f'<text x="{cx:.0f}" y="{y + 43 + i * 16}" text-anchor="middle" '
                   f'font-size="{line_size}" fill="{MUTED}">{esc(line)}</text>')
    return "\n".join(out)


def frame(x, y, w, h, label, stroke):
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="none" '
            f'stroke="{stroke}" stroke-width="1.2" stroke-dasharray="6 4" '
            f'opacity="0.8"/>\n'
            f'<text x="{x + 14}" y="{y + 19}" font-size="12.5" font-weight="700" '
            f'letter-spacing="1.2" fill="{stroke}">{esc(label.upper())}</text>')


def path(points, colour=EDGE, dash=False, both=False, width=1.8):
    d = " ".join(("M" if i == 0 else "L") + f" {x} {y}"
                 for i, (x, y) in enumerate(points))
    style = f'stroke="{colour}" stroke-width="{width}" fill="none"'
    if dash:
        style += ' stroke-dasharray="5 4"'
    markers = f'marker-end="url(#arrow-{colour[1:]})"'
    if both:
        markers += f' marker-start="url(#arrow-{colour[1:]})"'
    return f'<path d="{d}" {style} {markers}/>'


def label(x, y, text, colour=MUTED, size=11.5, anchor="middle", weight="400"):
    return (f'<text x="{x}" y="{y}" text-anchor="{anchor}" font-size="{size}" '
            f'fill="{colour}" font-weight="{weight}">{esc(text)}</text>')


def build():
    a_kib = gm.A_BYTES / 1024
    c_kib = gm.C_BYTES / 1024
    p = []
    p.append(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
             f'width="{W}" height="{H}" font-family="Inter, Segoe UI, Helvetica, '
             f'Arial, sans-serif" role="img" aria-label="Block diagram of the '
             f'matmul PPA test chip">')
    defs = ['<defs>']
    for colour in (EDGE, HOST_LINE, MEM_LINE, CTRL_LINE, ENG_LINE, MEAS_LINE):
        defs.append(f'<marker id="arrow-{colour[1:]}" viewBox="0 0 10 10" refX="9" '
                    f'refY="5" markerWidth="7" markerHeight="7" '
                    f'orient="auto-start-reverse">'
                    f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{colour}"/></marker>')
    defs.append('</defs>')
    p.append("".join(defs))
    p.append(f'<rect width="{W}" height="{H}" fill="#ffffff"/>')

    p.append(f'<text x="32" y="42" font-size="25" font-weight="700" fill="{INK}">'
             f'matmul-ppa-testchip</text>')
    p.append(f'<text x="32" y="66" font-size="13.5" fill="{MUTED}">'
             f'INT8 GEMM candidate benchmark, IHP SG13G2 130 nm target. '
             f'A is {gm.MAT_M}x{gm.MAT_K}, B is {gm.MAT_K}x{gm.MAT_N}, '
             f'C = A B in INT{gm.ACC_W}. Tile {gm.TILE_M}x{gm.TILE_N}x{gm.TILE_K}, '
             f'tile grid {gm.GRID_M}x{gm.GRID_N}x{gm.GRID_K}, '
             f'{gm.MAT_M * gm.MAT_N * gm.MAT_K} MACs per run.</text>')

    # Pad frame
    p.append(f'<rect x="24" y="86" width="{W - 48}" height="864" rx="16" '
             f'fill="{PAD}" stroke="{MUTED}" stroke-width="2"/>')
    p.append(f'<text x="42" y="110" font-size="12.5" font-weight="700" '
             f'letter-spacing="1.2" fill="{MUTED}">PAD FRAME '
             f'(pad_frame.sv, IHP IO cell hook)</text>')

    # Input pads
    for i, (name, note) in enumerate([
            ("pad_clk_i", "core clock"),
            ("pad_rst_ni", "async reset, active low"),
            ("pad_test_mode_i", "ungate every clock"),
            ("pad_spi_sck_i", "SPI clock, Mode 0"),
            ("pad_spi_cs_ni", "chip select, active low"),
            ("pad_spi_mosi_i", "host to chip"),
            ("pad_spi_miso_io", "chip to host, tristate")]):
        y = 140 + i * 36
        p.append(f'<rect x="40" y="{y}" width="14" height="14" rx="3" fill="#ffffff" '
                 f'stroke="{MUTED}" stroke-width="1.5"/>')
        p.append(f'<text x="62" y="{y + 12}" font-size="12.5" fill="{INK}" '
                 f'font-family="{MONO}">{esc(name)}</text>')
        p.append(label(62, y + 26, note, anchor="start", size=10.5))

    # Host interface
    p.append(frame(196, 122, 236, 248, "host interface", HOST_LINE))
    p.append(box(212, 148, 204, 86, HOST, HOST_LINE, "spi_target",
                 ["Mode 0, MSB first, 8 bit",
                  "pins oversampled in the core domain",
                  "f_spi <= f_core / 8"]))
    p.append(path([(314, 234), (314, 254)], HOST_LINE))
    p.append(box(212, 254, 204, 100, HOST, HOST_LINE, "frame_router",
                 ["opcode, 16 bit addr, payload",
                  "auto-increment, prefetched reads",
                  "range check, truncation detect",
                  "refuses store access while busy"]))

    # Storage
    p.append(frame(456, 122, 268, 378, "operand and result storage", MEM_LINE))
    stores = [
        ("store A", [f"{gm.MAT_M}x{gm.MAT_K} INT8, {a_kib:.0f} KiB",
                     f"{gm.MAT_M * gm.GRID_K} x {gm.TILE_K * 8} bit words"]),
        ("store B", [f"{gm.MAT_K}x{gm.MAT_N} INT8, {a_kib:.0f} KiB",
                     f"{gm.MAT_K * gm.GRID_N} x {gm.TILE_N * 8} bit words"]),
        ("store C", [f"{gm.MAT_M}x{gm.MAT_N} INT{gm.ACC_W}, {c_kib:.0f} KiB",
                     f"{gm.MAT_M * gm.GRID_N} x {gm.TILE_N * gm.ACC_W} bit words"]),
        ("store REF", ["golden C, loaded by the host",
                       "read by the on-chip comparator"]),
    ]
    for i, (name, lines) in enumerate(stores):
        y = 150 + i * 82
        p.append(box(472, y, 236, 64, MEM, MEM_LINE, name, lines, title_size=15,
                     line_size=11.5))

    # host byte-port bus into all four stores
    p.append(path([(416, 300), (444, 300)], HOST_LINE))
    p.append(f'<path d="M 444 178 L 444 460" stroke="{HOST_LINE}" '
             f'stroke-width="1.8" fill="none"/>')
    for i in range(4):
        y = 150 + i * 82 + 28
        p.append(path([(444, y), (472, y)], HOST_LINE, both=True))
    p.append(label(444, 484, "host byte port", HOST_LINE, size=10.5))

    p.append(label(600, 481, "sram_1rw: one port, byte enables, one cycle read",
                   MEM_LINE, size=10.5, anchor="start"))
    p.append(label(600, 494, "PDK macro hook at this boundary", MEM_LINE, size=10.5,
                   anchor="start"))

    # Sequencer
    p.append(frame(456, 528, 268, 182, "sequencing", CTRL_LINE))
    p.append(box(472, 556, 236, 140, CTRL, CTRL_LINE, "gemm_sequencer",
                 ["output-stationary tile loops",
                  f"{gm.GRID_M} x {gm.GRID_N} output tiles,",
                  f"{gm.GRID_K} K tiles accumulated each",
                  "fetch, launch, wait, write back",
                  "ready/valid: no assumed latency"]))
    p.append(path([(590, 500), (590, 556)], MEM_LINE, both=True))
    p.append(label(602, 520, "tile words, C write back", MEM_LINE, size=10.5,
                   anchor="start"))
    p.append(path([(314, 354), (314, 620), (472, 620)], CTRL_LINE))
    p.append(label(392, 612, "run, clear, verify", CTRL_LINE, size=11))

    # Engines
    p.append(frame(748, 122, 336, 588, "candidate engines", ENG_LINE))
    engines = [
        ("engine_infer", "inferred * and +, synthesiser's choice", 1),
        ("engine_wallace", "signed partial products, 3:2 CSA tree", 1),
        ("engine_booth4", "radix-4 Booth recoding, 3:2 CSA tree", 1),
        ("engine_signmag", "sign-magnitude datapath, unsigned array", 1),
        ("engine_bitserial", f"Horner over {gm.OPERAND_W} bit planes", gm.OPERAND_W),
    ]
    for i, (name, note, lat) in enumerate(engines):
        y = 150 + i * 60
        p.append(box(790, y, 278, 50, "#ffffff", ENG_LINE, name,
                     [f"{note}, {lat} cycle" + ("s" if lat > 1 else "")],
                     rx=6, title_size=13.5, line_size=10.5))
        p.append(label(766, y + 18, str(i), ENG_LINE, size=10.5, weight="700"))
    p.append(f'<path d="M 778 178 L 778 612" stroke="{ENG_LINE}" stroke-width="1.8" '
             f'fill="none"/>')
    for i in range(5):
        y = 150 + i * 60 + 25
        p.append(path([(778, y), (790, y)], ENG_LINE, both=True))
    p.append(box(764, 612, 304, 84, ENG, ENG_LINE, "engine_array",
                 ["runtime select, one of five",
                  "integrated clock gate per candidate",
                  "operand and control isolation"], title_size=15, line_size=11.5))
    p.append(path([(708, 654), (764, 654)], CTRL_LINE, both=True))
    p.append(label(736, 636, "a, b", CTRL_LINE, size=9.5))
    p.append(label(736, 646, "launch", CTRL_LINE, size=9.5))
    p.append(label(736, 674, "ready", CTRL_LINE, size=9.5))
    p.append(label(736, 684, "valid", CTRL_LINE, size=9.5))
    p.append(label(916, 532, "one identical interface for every candidate:",
                   ENG_LINE, size=10.5))
    p.append(label(916, 546, "launch, ready, valid, mac_tick, a_tile, b_tile, c_tile",
                   ENG_LINE, size=10.5))
    p.append(label(916, 566, "shared acc_bank keeps the output tile resident "
                             "across the K loop", ENG_LINE, size=10.5))
    p.append(label(916, 586, "adding a sixth candidate touches only this column",
                   ENG_LINE, size=10.5, weight="600"))

    # Measurement
    p.append(frame(196, 752, 888, 170, "measurement", MEAS_LINE))
    p.append(box(212, 778, 252, 84, MEAS, MEAS_LINE, "cycle_meter",
                 ["counts while the sequencer runs",
                  "cleared by a run, saturating"], title_size=15, line_size=11.5))
    p.append(box(480, 778, 252, 84, MEAS, MEAS_LINE, "mac_meter",
                 ["sums mac_tick every cycle",
                  f"expected {gm.MAT_M * gm.MAT_N * gm.MAT_K} per run"],
                 title_size=15, line_size=11.5))
    p.append(box(748, 778, 320, 84, MEAS, MEAS_LINE, "result_checker",
                 ["walks store C against store REF",
                  "mismatch count and first index"], title_size=15, line_size=11.5))
    p.append(label(640, 900, "read back over SPI with OP_RD_PERF and OP_RD_STATUS; "
                             "also exposed on four status pads", MEAS_LINE, size=11))

    # Corridors into measurement, each on its own y so nothing overlaps
    p.append(path([(456, 460), (436, 460), (436, 722), (830, 722), (830, 778)],
                  MEM_LINE, dash=True))
    p.append(label(468, 715, "C and REF words", MEM_LINE, size=10.5, anchor="start"))
    p.append(path([(540, 696), (540, 736), (338, 736), (338, 778)], CTRL_LINE))
    p.append(label(430, 730, "busy", CTRL_LINE, size=11))
    p.append(path([(916, 696), (916, 750), (606, 750), (606, 778)], ENG_LINE))
    p.append(label(760, 744, "mac_tick", ENG_LINE, size=11))

    # Status pads
    for i, name in enumerate(["pad_stat_busy_o", "pad_stat_done_o",
                              "pad_stat_vfy_done_o", "pad_stat_mismatch_o"]):
        y = 790 + i * 26
        p.append(f'<rect x="{W - 54}" y="{y}" width="14" height="14" rx="3" '
                 f'fill="#ffffff" stroke="{MUTED}" stroke-width="1.5"/>')
        p.append(f'<text x="{W - 62}" y="{y + 12}" text-anchor="end" font-size="11" '
                 f'fill="{INK}" font-family="{MONO}">{esc(name)}</text>')
    p.append(path([(1068, 828), (1140, 828)], MEAS_LINE))

    # Legend
    for i, (colour, text) in enumerate([
            (HOST_LINE, "host interface"), (MEM_LINE, "storage"),
            (CTRL_LINE, "sequencing"), (ENG_LINE, "candidate engines"),
            (MEAS_LINE, "measurement")]):
        x = 212 + i * 150
        p.append(f'<rect x="{x}" y="{H - 40}" width="13" height="13" rx="3" '
                 f'fill="{colour}" opacity="0.9"/>')
        p.append(label(x + 20, H - 29, text, MUTED, size=12, anchor="start"))
    p.append(label(W - 32, H - 29, "double-headed arrows are bidirectional", MUTED,
                   size=11, anchor="end"))

    p.append("</svg>")
    return "\n".join(p) + "\n"


def main() -> int:
    out = REPO / "docs" / "img" / "architecture.svg"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build())
    print(f"wrote {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
