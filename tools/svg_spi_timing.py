#!/usr/bin/env python3
# Copyright 2026 Daniel Tyukov
# SPDX-License-Identifier: Apache-2.0
"""Write docs/img/spi_frame_timing.svg from a real captured frame.

The waveform is drawn from results/trace/spi_frame.json, which
tb/test_spi_protocol.py::test_capture_timing_trace produces by sampling the chip's
pins on every core clock edge. Cycle numbers on the axis are the actual simulation
cycles, so the figure cannot describe timing the RTL does not have.
"""

from __future__ import annotations

import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
TRACE = REPO / "results" / "trace" / "spi_frame.json"

INK = "#12181f"
MUTED = "#5a6672"
GRID = "#dfe4ea"
SCK = "#2f6fb3"
CS = "#b03d3d"
MOSI = "#2e7d4f"
MISO = "#6b4fa8"
HOT = "#f97316"
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"


def esc(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def text(x, y, s, size=12, colour=INK, anchor="start", weight="400", mono=False):
    family = f' font-family="{MONO}"' if mono else ""
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{colour}" '
            f'text-anchor="{anchor}" font-weight="{weight}"{family}>{esc(s)}</text>')


def digital(samples, key, x0, y0, xscale, height, colour, invert_none=None):
    """One digital waveform. A None sample is drawn as a mid-level dashed segment."""
    segments = []
    hi = y0
    lo = y0 + height
    mid = y0 + height / 2
    prev = None
    path = []
    for i, s in enumerate(samples):
        value = s[key]
        x = x0 + i * xscale
        y = mid if value is None else (hi if value else lo)
        if prev is None:
            path.append(f"M {x:.1f} {y:.1f}")
        else:
            if y != prev:
                path.append(f"L {x:.1f} {prev:.1f}")
                path.append(f"L {x:.1f} {y:.1f}")
            else:
                path.append(f"L {x:.1f} {y:.1f}")
        prev = y
    segments.append(f'<path d="{" ".join(path)}" fill="none" stroke="{colour}" '
                    f'stroke-width="2" stroke-linejoin="miter"/>')
    return "\n".join(segments)


def build() -> str:
    if not TRACE.exists():
        raise SystemExit(
            f"svg_spi_timing: {TRACE.relative_to(REPO)} is missing. Run\n"
            f"  make -C tb MODULE=test_spi_protocol TOPLEVEL=gemm_bench_chip "
            f"TESTCASE=test_capture_timing_trace\n"
            f"first; this figure is only ever drawn from a real capture."
        )
    data = json.loads(TRACE.read_text())
    samples = data["samples"]
    half = data["cycles_per_spi_half_period"]

    # The whole frame is five bytes, which is too wide to read. Show the opcode byte
    # and the first response byte, which is where all the interesting timing is.
    bits_shown = 17
    cycles_shown = min(len(samples), bits_shown * 2 * half + 4 * half)
    view = samples[:cycles_shown]

    xscale = 7.0
    left = 130
    W = int(left + cycles_shown * xscale + 200)
    H = 580

    p = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
         f'width="{W}" height="{H}" font-family="Inter, Segoe UI, Helvetica, Arial, '
         f'sans-serif" role="img" aria-label="Captured SPI Mode 0 frame timing">',
         f'<rect width="{W}" height="{H}" fill="#ffffff"/>']

    p.append(text(32, 40, "SPI Mode 0 frame, captured from simulation", 22, INK,
                  weight="700"))
    mosi_bytes = " ".join(f"0x{b:02X}" for b in data["frame_bytes_mosi"])
    miso_bytes = " ".join(f"0x{b:02X}" for b in data["frame_bytes_miso"])
    p.append(text(32, 64,
                  f"OP_RD_ID frame. MOSI: {mosi_bytes}. MISO: {miso_bytes}. "
                  f"Core clock {data['core_clock_period_ns']} ns, "
                  f"{half} core cycles per SPI half period, so "
                  f"f_spi = f_core/{2 * half}.", 13, MUTED))

    rows = [
        ("pad_spi_cs_ni", "cs_n", CS),
        ("pad_spi_sck_i", "sck", SCK),
        ("pad_spi_mosi_i", "mosi", MOSI),
        ("pad_spi_miso_io", "miso", MISO),
    ]
    row_h = 46
    gap = 30
    top = 118

    # Cycle grid every four core cycles, labelled every eight.
    for i in range(0, cycles_shown, 4):
        x = left + i * xscale
        p.append(f'<line x1="{x:.1f}" y1="{top - 12}" x2="{x:.1f}" '
                 f'y2="{top + len(rows) * (row_h + gap)}" stroke="{GRID}" '
                 f'stroke-width="1"/>')
    for i in range(0, cycles_shown, 8):
        x = left + i * xscale
        p.append(text(x, top - 18, str(view[i]["cycle"]), 9.5, MUTED,
                      anchor="middle", mono=True))
    p.append(text(left - 8, top - 18, "core cycle", 10, MUTED, anchor="end"))

    for idx, (pin, key, colour) in enumerate(rows):
        y0 = top + idx * (row_h + gap)
        p.append(f'<line x1="{left}" y1="{y0 + row_h}" '
                 f'x2="{left + cycles_shown * xscale:.1f}" y2="{y0 + row_h}" '
                 f'stroke="{GRID}" stroke-width="1"/>')
        p.append(text(left - 10, y0 + row_h / 2 + 4, pin, 11.5, colour, anchor="end",
                      mono=True, weight="600"))
        p.append(digital(view, key, left, y0, xscale, row_h, colour))

    # Mark the sampling and shifting edges the protocol depends on.
    sck_row = 1
    sck_y = top + sck_row * (row_h + gap)
    rises = []
    falls = []
    for i in range(1, len(view)):
        if view[i]["cs_n"] == 0:
            if view[i]["sck"] == 1 and view[i - 1]["sck"] == 0:
                rises.append(i)
            if view[i]["sck"] == 0 and view[i - 1]["sck"] == 1:
                falls.append(i)

    for i in rises[:16]:
        x = left + i * xscale
        p.append(f'<line x1="{x:.1f}" y1="{sck_y - 10}" x2="{x:.1f}" '
                 f'y2="{top + 3 * (row_h + gap) + row_h + 10}" stroke="{HOT}" '
                 f'stroke-width="1" stroke-dasharray="3 3" opacity="0.8"/>')
    if rises:
        x = left + rises[0] * xscale
        p.append(text(x, top + row_h + 20, "controller samples MISO and the chip "
                                           "samples MOSI on every rising edge",
                      10.5, HOT))

    if falls:
        base_y = top + 4 * (row_h + gap) + 6
        p.append(text(left, base_y,
                      "The chip loads its outgoing byte on the first falling edge of "
                      "each byte, which gives the command router half an SPI period "
                      "to fetch the answer.", 10.5, MUTED))

    # Byte boundaries.
    for byte_index in range(1, 3):
        if len(rises) >= byte_index * 8:
            i = rises[byte_index * 8 - 1]
            x = left + i * xscale
            p.append(f'<line x1="{x:.1f}" y1="{top - 6}" x2="{x:.1f}" '
                     f'y2="{top + 4 * (row_h + gap)}" stroke="{INK}" '
                     f'stroke-width="1.4" stroke-dasharray="6 3" opacity="0.55"/>')
            p.append(text(x + 6, top + 4 * (row_h + gap) - 12,
                          f"byte {byte_index - 1} complete", 10.5, INK))

    p.append(text(32, H - 46,
                  "MISO reads 0x00 during the opcode byte because no opcode has been "
                  "decoded yet, then carries the answer to the byte before it: "
                  "standard one byte command latency.", 11.5, MUTED))
    p.append(text(32, H - 26,
                  f"Drawn by tools/svg_spi_timing.py from "
                  f"results/trace/spi_frame.json, captured by "
                  f"tb/test_spi_protocol.py::test_capture_timing_trace.", 11, MUTED))
    p.append("</svg>")
    return "\n".join(p) + "\n"


def main() -> int:
    out = REPO / "docs" / "img" / "spi_frame_timing.svg"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build())
    print(f"wrote {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
