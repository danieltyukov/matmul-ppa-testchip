#!/usr/bin/env python3
# Copyright 2026 Daniel Tyukov
# SPDX-License-Identifier: Apache-2.0
"""Write docs/img/memory_map.svg: the SPI command set and the register bit fields.

Everything drawn here is read out of tb/gemm_model.py, which in turn mirrors
rtl/pkg/gemm_pkg.sv, so the figure and the RTL cannot drift apart silently: the
protocol tests assert the same constants against the chip.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tb"))

import gemm_model as gm  # noqa: E402

W, H = 1240, 800
INK = "#12181f"
MUTED = "#5a6672"
WR = "#2f6fb3"
WR_FILL = "#e8f1fb"
RD = "#2e7d4f"
RD_FILL = "#eaf6ec"
FIELD = "#6b4fa8"
FIELD_FILL = "#f3ecfb"
BAD = "#b03d3d"
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"


def esc(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def text(x, y, s, size=13, colour=INK, anchor="start", weight="400", mono=False):
    family = f' font-family="{MONO}"' if mono else ""
    return (f'<text x="{x}" y="{y}" font-size="{size}" fill="{colour}" '
            f'text-anchor="{anchor}" font-weight="{weight}"{family}>{esc(s)}</text>')


def build() -> str:
    p = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
         f'width="{W}" height="{H}" font-family="Inter, Segoe UI, Helvetica, Arial, '
         f'sans-serif" role="img" aria-label="SPI command set and register map">',
         f'<rect width="{W}" height="{H}" fill="#ffffff"/>']

    p.append(text(32, 42, "SPI command set and register map", 24, INK, weight="700"))
    p.append(text(32, 66, "Mode 0, MSB first. Opcode bit 7 selects direction: "
                          "0 means the host writes, 1 means the chip answers. "
                          "Addresses are big endian on the wire, INT32 values are "
                          "little endian.", 13, MUTED))

    # Frame layouts
    y = 100
    p.append(text(32, y + 18, "Frame layouts", 16, INK, weight="700"))
    frames = [
        ("memory", [("opcode", WR_FILL, WR), ("addr hi", WR_FILL, WR),
                    ("addr lo", WR_FILL, WR), ("data 0", WR_FILL, WR),
                    ("data 1", WR_FILL, WR), ("...", "#ffffff", MUTED)],
         "byte address auto-increments; the frame is as long as chip select stays low"),
        ("register write", [("opcode", WR_FILL, WR), ("value", WR_FILL, WR)],
         "a frame that ends before the value sets the frame error flag"),
        ("register read", [("opcode", RD_FILL, RD), ("dummy", RD_FILL, RD),
                           ("dummy", RD_FILL, RD), ("...", "#ffffff", MUTED)],
         "MISO returns 0x00, then payload byte 0, byte 1, ...: one byte of latency"),
    ]
    row = y + 34
    for name, cells, note in frames:
        p.append(text(40, row + 24, name, 13, INK, weight="600"))
        x = 190
        for cellname, fill, stroke in cells:
            w = 92
            p.append(f'<rect x="{x}" y="{row}" width="{w}" height="34" rx="4" '
                     f'fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>')
            p.append(text(x + w / 2, row + 22, cellname, 12, INK, anchor="middle",
                          mono=True))
            x += w + 6
        p.append(text(190, row + 48, note, 11, MUTED))
        row += 62

    # Command table
    y = row + 16
    p.append(text(32, y + 18, "Opcodes", 16, INK, weight="700"))
    cmds = [
        (gm.OP_NOP, "OP_NOP", "-", "accepted, does nothing", WR),
        (gm.OP_WR_A, "OP_WR_A", f"addr + up to {gm.A_BYTES} B", "write operand A", WR),
        (gm.OP_WR_B, "OP_WR_B", f"addr + up to {gm.B_BYTES} B", "write operand B", WR),
        (gm.OP_WR_REF, "OP_WR_REF", f"addr + up to {gm.C_BYTES} B",
         "write the golden reference", WR),
        (gm.OP_WR_ENGINE, "OP_WR_ENGINE", "1 B",
         f"select candidate 0..{gm.ENGINE_COUNT - 1}", WR),
        (gm.OP_WR_TRIG, "OP_WR_TRIG", "1 B", "trigger bits, see below", WR),
        (gm.OP_SOFT_RST, "OP_SOFT_RST", f"1 B = 0x{gm.SOFT_RST_KEY:02X}",
         "soft reset the datapath", WR),
        (gm.OP_RD_ID, "OP_RD_ID", "4 B", f"0x{gm.CHIP_ID:08X}", RD),
        (gm.OP_RD_STATUS, "OP_RD_STATUS", "1 B", "status byte, see below", RD),
        (gm.OP_RD_PERF, "OP_RD_PERF", "12 B",
         "cycles, MACs, mismatches, first index", RD),
        (gm.OP_RD_C, "OP_RD_C", f"addr + up to {gm.C_BYTES} B", "read the result", RD),
        (gm.OP_RD_A, "OP_RD_A", f"addr + up to {gm.A_BYTES} B", "read back operand A", RD),
        (gm.OP_RD_B, "OP_RD_B", f"addr + up to {gm.B_BYTES} B", "read back operand B", RD),
        (gm.OP_RD_CFG, "OP_RD_CFG", "10 B", "geometry discovery", RD),
        (gm.OP_RD_REF, "OP_RD_REF", f"addr + up to {gm.C_BYTES} B",
         "read back the reference", RD),
    ]
    row = y + 34
    p.append(text(40, row, "opcode", 11.5, MUTED, weight="600"))
    p.append(text(112, row, "name", 11.5, MUTED, weight="600"))
    p.append(text(290, row, "payload", 11.5, MUTED, weight="600"))
    p.append(text(500, row, "effect", 11.5, MUTED, weight="600"))
    row += 8
    for code, name, payload, effect, colour in cmds:
        p.append(f'<rect x="34" y="{row}" width="640" height="22" rx="3" '
                 f'fill="{WR_FILL if colour == WR else RD_FILL}" '
                 f'fill-opacity="0.55" stroke="none"/>')
        p.append(text(40, row + 16, f"0x{code:02X}", 12, colour, weight="700",
                      mono=True))
        p.append(text(112, row + 16, name, 12, INK, mono=True))
        p.append(text(290, row + 16, payload, 11.5, MUTED))
        p.append(text(500, row + 16, effect, 11.5, INK))
        row += 24
    p.append(text(40, row + 18, "Any other opcode sets the sticky command error bit "
                                "and the rest of the frame is ignored.", 11.5, BAD))

    # Bit fields
    fx = 712
    fy = 100
    p.append(text(fx, fy, "Bit fields", 16, INK, weight="700"))

    def bitfield(x, y, title, bits, note=""):
        out = [text(x, y, title, 13.5, INK, weight="600", mono=True)]
        cell = 56
        for i in range(8):
            bit = 7 - i
            name = bits.get(bit)
            fill = FIELD_FILL if name else "#f4f6f8"
            out.append(f'<rect x="{x + i * cell}" y="{y + 12}" width="{cell}" '
                       f'height="34" fill="{fill}" stroke="{FIELD if name else MUTED}" '
                       f'stroke-width="1.4"/>')
            out.append(text(x + i * cell + cell / 2, y + 10, str(bit), 10, MUTED,
                            anchor="middle"))
            if name:
                out.append(text(x + i * cell + cell / 2, y + 33, name, 9.5, FIELD,
                                anchor="middle", weight="600"))
            else:
                out.append(text(x + i * cell + cell / 2, y + 33, "0", 10, MUTED,
                                anchor="middle"))
        if note:
            out.append(text(x, y + 62, note, 11, MUTED))
        return "\n".join(out)

    p.append(bitfield(fx, fy + 40, "OP_RD_STATUS", {
        0: "busy", 1: "done", 2: "vfy busy", 3: "vfy done",
        4: "mismatch", 5: "cmd err", 6: "frame err",
    }, "cmd err and frame err are sticky; clear them with TRIG_CLR_STICKY"))

    p.append(bitfield(fx, fy + 144, "OP_WR_TRIG", {
        0: "run", 1: "clr C", 2: "verify", 3: "clr perf", 4: "clr sticky",
    }, "run, verify and clr C are refused while the chip is busy and set cmd err"))

    # OP_RD_PERF byte layout
    py = fy + 254
    p.append(text(fx, py, "OP_RD_PERF payload, 12 bytes", 13.5, INK, weight="600",
                  mono=True))
    perf_fields = [("cycle count", 4, "#dbeafe", WR),
                   ("MAC count", 4, "#dcfce7", RD),
                   ("mismatch", 2, "#fee2e2", BAD),
                   ("first idx", 2, "#fef3c7", "#b5761f")]
    x = fx
    for name, nbytes, fill, stroke in perf_fields:
        w = nbytes * 51
        p.append(f'<rect x="{x}" y="{py + 12}" width="{w}" height="34" rx="3" '
                 f'fill="{fill}" stroke="{stroke}" stroke-width="1.4"/>')
        p.append(text(x + w / 2, py + 28, name, 10.5, INK, anchor="middle"))
        p.append(text(x + w / 2, py + 40, f"{nbytes} B", 9, MUTED, anchor="middle"))
        x += w + 4
    p.append(text(fx, py + 62, "each field little endian; counters saturate rather "
                               "than wrap", 11, MUTED))

    # OP_RD_CFG
    cy = py + 96
    p.append(text(fx, cy, "OP_RD_CFG payload, 10 bytes", 13.5, INK, weight="600",
                  mono=True))
    cfg = [("MAT_M", gm.MAT_M), ("MAT_N", gm.MAT_N), ("MAT_K", gm.MAT_K),
           ("TILE_M", gm.TILE_M), ("TILE_N", gm.TILE_N), ("TILE_K", gm.TILE_K),
           ("OPERAND_W", gm.OPERAND_W), ("ACC_W", gm.ACC_W),
           ("ENGINES", gm.ENGINE_COUNT), ("SELECTED", 0)]
    for i, (name, value) in enumerate(cfg):
        col = i % 5
        rowi = i // 5
        x = fx + col * 90
        yy = cy + 16 + rowi * 46
        p.append(f'<rect x="{x}" y="{yy}" width="84" height="38" rx="4" '
                 f'fill="{FIELD_FILL}" stroke="{FIELD}" stroke-width="1.3"/>')
        p.append(text(x + 42, yy + 16, name, 9.5, FIELD, anchor="middle",
                      weight="600"))
        p.append(text(x + 42, yy + 31, str(value), 13, INK, anchor="middle",
                      mono=True))
    p.append(text(fx, cy + 124, "a host reads this first and sizes its transfers from "
                                "it, so host tooling survives a geometry change",
                  11, MUTED))

    p.append(text(32, H - 20, "Generated by tools/svg_memory_map.py from the same "
                              "constants the protocol tests assert against the chip",
                  11.5, MUTED))
    p.append("</svg>")
    return "\n".join(p) + "\n"


def main() -> int:
    out = REPO / "docs" / "img" / "memory_map.svg"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build())
    print(f"wrote {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
