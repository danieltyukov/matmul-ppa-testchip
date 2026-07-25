#!/usr/bin/env python3
# Copyright 2026 Daniel Tyukov
# SPDX-License-Identifier: Apache-2.0
"""Run Yosys over every candidate and the whole chip, then collect the reports.

Two modes, both committed:

  generic   No PDK needed, so it runs anywhere including CI. Reports cell counts,
            flip-flop counts, longest topological path (a proxy for logic depth)
            and gate equivalents. A gate equivalent here is the cell's static CMOS
            transistor count divided by four, so a two input NAND is 1.0 GE. Those
            transistor counts are properties of static CMOS, not of any process,
            which is what makes the number technology independent. It is not area.

  sg13g2    Maps to the IHP SG13G2 standard cell library and reports real cell
            area in square micrometres, straight out of the liberty file. Needs
            SG13G2_LIB; tools/fetch_pdk.sh downloads it.

Outputs:
  results/synth/<mode>/<top>_stat.txt   raw Yosys reports, committed as evidence
  results/synth/<mode>/summary.json     parsed numbers
  results/synth/summary.csv             one row per top per mode
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent

# Transistor counts for static CMOS implementations of the generic gates Yosys
# emits. A two input NAND is four transistors and defines one gate equivalent.
# Inverters and buffers are counted as they are built, not as they are idealised.
TRANSISTORS = {
    "$_NOT_": 2,
    "$_BUF_": 4,
    "$_AND_": 6,
    "$_NAND_": 4,
    "$_OR_": 6,
    "$_NOR_": 4,
    "$_XOR_": 12,
    "$_XNOR_": 12,
    "$_ANDNOT_": 6,
    "$_ORNOT_": 6,
    "$_MUX_": 12,
    "$_NMUX_": 10,
    "$_AOI3_": 6,
    "$_OAI3_": 6,
    "$_AOI4_": 8,
    "$_OAI4_": 8,
    "$_MUX4_": 20,
    "$_MUX8_": 36,
    "$_MUX16_": 68,
    # A transmission gate level sensitive latch plus its enable inverter. The only
    # latches in this design are inside clock_gate.sv, where they belong.
    "$_DLATCH_N_": 10,
    "$_DLATCH_P_": 10,
}
# Flip-flops, in all the reset and enable flavours Yosys emits. A plain D
# flip-flop built from two transmission gate latches plus clock inverters is about
# 24 transistors; reset and enable add a couple of gates each.
FF_TRANSISTORS = {"plain": 24, "reset": 28, "enable": 32, "enable_reset": 36}

ENGINE_TOPS = [
    "engine_infer",
    "engine_wallace",
    "engine_booth4",
    "engine_signmag",
    "engine_bitserial",
]
SUPPORT_TOPS = ["engine_array", "bench_core", "gemm_bench_chip"]


def ff_transistors(cell: str) -> int:
    """Classify a Yosys flip-flop cell name into a transistor count."""
    has_enable = "E" in cell.split("_")[1] if "_" in cell else False
    # Yosys names look like $_DFF_P_, $_DFFE_PP_, $_SDFFE_PN0P_, $_DFF_PN0_.
    body = cell.strip("$_")
    has_enable = "DFFE" in body or "SDFFE" in body or "ADFFE" in body
    has_reset = any(token in body for token in ("PN0", "PN1", "NN0", "NN1",
                                               "PP0", "PP1", "NP0", "NP1",
                                               "SDFF", "ADFF"))
    if has_enable and has_reset:
        return FF_TRANSISTORS["enable_reset"]
    if has_enable:
        return FF_TRANSISTORS["enable"]
    if has_reset:
        return FF_TRANSISTORS["reset"]
    return FF_TRANSISTORS["plain"]


_CELL_RE = re.compile(r"^\s+(\$?[\w$.]+)\s+(\d+)\s*$")
_TOTAL_RE = re.compile(r"^\s+Number of cells:\s+(\d+)\s*$")
_WIRE_RE = re.compile(r"^\s+Number of wire bits:\s+(\d+)\s*$")
_AREA_RE = re.compile(r"Chip area for (?:top )?module '.*?':\s+([\d.]+)")
_MEM_RE = re.compile(r"^\s+Number of memories:\s+(\d+)\s*$")
_MEMBIT_RE = re.compile(r"^\s+Number of memory bits:\s+(\d+)\s*$")
_LTP_RE = re.compile(r"Longest topological path in \S+ \(length=(\d+)\)")


def parse_stat(text: str) -> dict:
    """Pull cell counts, wire bits and (for a liberty run) chip area out of `stat`.

    Yosys prints one block per module, and with `-top` it appends a design hierarchy
    roll-up whose totals cover the whole design. Taking the last block therefore
    gives the flattened module when the design was flattened and the design-wide
    roll-up when it was not, which is what both cases want.
    """
    cells: dict[str, int] = {}
    total = 0
    wire_bits = 0
    area = None
    memories = 0
    memory_bits = 0
    for line in text.splitlines():
        match = _MEM_RE.match(line)
        if match:
            memories = int(match.group(1))
            continue
        match = _MEMBIT_RE.match(line)
        if match:
            memory_bits = int(match.group(1))
            continue
        match = _TOTAL_RE.match(line)
        if match:
            total = int(match.group(1))
            cells = {}
            continue
        match = _WIRE_RE.match(line)
        if match:
            wire_bits = int(match.group(1))
            continue
        match = _AREA_RE.search(line)
        if match:
            area = float(match.group(1))
            continue
        match = _CELL_RE.match(line)
        if match and not line.strip().startswith("Number of"):
            cells[match.group(1)] = int(match.group(2))
    return {"cells": cells, "total_cells": total, "wire_bits": wire_bits,
            "chip_area_um2": area, "memories": memories,
            "memory_bits": memory_bits}


def parse_ltp(text: str) -> int | None:
    match = _LTP_RE.search(text)
    return int(match.group(1)) if match else None


def gate_equivalents(cells: dict[str, int]) -> tuple[float, int, int]:
    """Total gate equivalents, combinational cell count and flip-flop count."""
    total_transistors = 0
    comb = 0
    flops = 0
    for cell, count in cells.items():
        if cell.startswith("$_DLATCH_"):
            # Counted towards area but not towards either the combinational or the
            # sequential total, because a clock gate latch is neither.
            total_transistors += TRANSISTORS[cell] * count
        elif cell in TRANSISTORS:
            total_transistors += TRANSISTORS[cell] * count
            comb += count
        elif "DFF" in cell or "SDFF" in cell or "ADFF" in cell:
            total_transistors += ff_transistors(cell) * count
            flops += count
        elif cell.startswith("$"):
            # An unrecognised primitive would silently distort the total, so make
            # the omission visible instead of guessing.
            print(f"synth_collect: unrecognised cell {cell} x{count}, "
                  f"not counted in gate equivalents", file=sys.stderr)
    return total_transistors / 4.0, comb, flops


def run_yosys(top: str, mode: str, out_dir: pathlib.Path, netlist: bool,
              extra_sources: str = "") -> None:
    env = dict(os.environ)
    env.update({
        "TOP": top,
        "MODE": mode,
        "OUT_DIR": str(out_dir),
        "FILELIST": str(REPO / "rtl" / "filelist.f"),
        "WRITE_NETLIST": "1" if netlist else "0",
        "EXTRA_SOURCES": extra_sources,
        # Leaf candidates are flattened so their cell counts are directly
        # comparable and the netlist is self contained. Aggregate tops keep their
        # hierarchy: flattening five 40 000 cell candidates into one module makes
        # ABC's mapping time explode for nothing.
        "FLATTEN": "1" if top in ENGINE_TOPS else "0",
        # The only intentional latch in the design is inside clock_gate.sv, which is
        # an integrated clock gate and is supposed to be one. The candidates contain
        # no clock gate, so they must have none. The aggregate tops keep their
        # hierarchy, so clock_gate appears once as a module however many times it is
        # instantiated, and the expected count is one rather than ENGINE_COUNT.
        "EXPECT_LATCHES": "0" if top in ENGINE_TOPS else "1",
        # Memories stay memories in the generic report and become flip-flops for the
        # standard cell flow, which has no macros to bind.
        "MAP_MEMORY": "1" if mode == "sg13g2" else "0",
        # JSON netlists are build artefacts, not reports; keep them out of results/.
        "JSON_DIR": str(REPO / "build" / "synth"),
    })
    (REPO / "build" / "synth").mkdir(parents=True, exist_ok=True)
    # The full Yosys transcript for the whole chip is 60 MB, almost all of it net
    # names, so it goes to build/ and only the trimmed reports are committed.
    log = REPO / "build" / "synth" / f"{top}_{mode}_yosys.log"
    out_dir.mkdir(parents=True, exist_ok=True)
    with log.open("w") as handle:
        result = subprocess.run(
            ["yosys", "-c", str(REPO / "flow" / "yosys" / "synth.tcl")],
            cwd=REPO, env=env, stdout=handle, stderr=subprocess.STDOUT,
        )
    if result.returncode != 0:
        tail = "\n".join(log.read_text().splitlines()[-25:])
        raise RuntimeError(f"yosys failed for {top} in mode {mode}:\n{tail}")


def trim_ltp(path: pathlib.Path) -> str:
    """Keep the ltp headline and drop the path listing.

    `ltp -noff` prints every cell on the longest path, which for the whole chip is
    64 MB. The length is the number; the listing is only useful while debugging a
    specific path, and it is still in build/ for that.
    """
    text = path.read_text()
    lines = text.splitlines()
    keep = []
    for line in lines:
        keep.append(line)
        if "Longest topological path" in line:
            break
    keep.append("")
    keep.append(f"(path listing of {len(lines)} lines omitted; the full report is in "
                f"build/synth/)")
    trimmed = "\n".join(keep) + "\n"
    path.write_text(trimmed)
    return trimmed


def collect(top: str, mode: str, out_dir: pathlib.Path) -> dict:
    stat = (out_dir / f"{top}_{mode}_stat.txt").read_text()
    ltp = trim_ltp(out_dir / f"{top}_{mode}_ltp.txt")
    parsed = parse_stat(stat)
    ge, comb, flops = gate_equivalents(parsed["cells"])
    return {
        "top": top,
        "mode": mode,
        "total_cells": parsed["total_cells"],
        "combinational_cells": comb,
        "flip_flops": flops,
        "wire_bits": parsed["wire_bits"],
        "gate_equivalents": round(ge, 1),
        "logic_depth": parse_ltp(ltp),
        "chip_area_um2": parsed["chip_area_um2"],
        "memories": parsed["memories"],
        "memory_bits": parsed["memory_bits"],
        "cell_histogram": dict(sorted(parsed["cells"].items())),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--mode", choices=["generic", "sg13g2", "both"],
                        default="generic")
    parser.add_argument("--tops", nargs="+", default=ENGINE_TOPS + SUPPORT_TOPS)
    parser.add_argument("--out-dir", type=pathlib.Path,
                        default=REPO / "results" / "synth")
    parser.add_argument("--netlist-dir", type=pathlib.Path,
                        default=REPO / "build" / "synth",
                        help="where gate level netlists are written, not committed")
    parser.add_argument("--netlists", action="store_true",
                        help="also write gate level netlists for the engines")
    args = parser.parse_args(argv)

    if shutil.which("yosys") is None:
        print("synth_collect: yosys is required", file=sys.stderr)
        return 1

    modes = ["generic", "sg13g2"] if args.mode == "both" else [args.mode]
    if "sg13g2" in modes and not os.environ.get("SG13G2_LIB"):
        print("synth_collect: SG13G2_LIB is not set, skipping the sg13g2 mode "
              "(run tools/fetch_pdk.sh to get the liberty file)", file=sys.stderr)
        modes = [m for m in modes if m != "sg13g2"]
        if not modes:
            return 1

    rows = []
    for mode in modes:
        mode_dir = args.out_dir / mode
        mode_dir.mkdir(parents=True, exist_ok=True)
        results = {}
        for top in args.tops:
            want_netlist = args.netlists and top in ENGINE_TOPS
            target_dir = args.netlist_dir if want_netlist else mode_dir
            print(f"yosys {top} ({mode}) ...", flush=True)
            run_yosys(top, mode, target_dir, want_netlist)
            if want_netlist:
                # Keep the reports with the other reports, the netlist in build/.
                for suffix in ("stat.txt", "ltp.txt"):
                    src = target_dir / f"{top}_{mode}_{suffix}"
                    if src.exists():
                        shutil.copy(src, mode_dir / src.name)
            entry = collect(top, mode, mode_dir)
            results[top] = entry
            rows.append(entry)
            area = (f"{entry['chip_area_um2']:.1f} um2"
                    if entry["chip_area_um2"] else f"{entry['gate_equivalents']} GE")
            mem = (f"  {entry['memory_bits']:>6} mem bits"
                   if entry["memory_bits"] else "")
            print(f"  {top:<20} {entry['total_cells']:>7} cells  "
                  f"{entry['flip_flops']:>5} FF  depth {entry['logic_depth']:>4}  "
                  f"{area}{mem}")

        payload = {
            "source": "tools/synth_collect.py",
            "mode": mode,
            "yosys": subprocess.run(["yosys", "-V"], capture_output=True,
                                    text=True).stdout.strip(),
            "note": (
                "Unit-cost generic gate counts, plus gate equivalents derived from "
                "static CMOS transistor counts (NAND2 = 1 GE). This is not PDK "
                "area. The candidates are flattened so their counts are directly "
                "comparable; the aggregate tops keep their hierarchy and their "
                "matrix stores are left as memory cells rather than mapped to "
                "flip-flops, because those stores are SRAM in any real build and "
                "counting 74 kbit of storage as flip-flops would overstate the "
                "chip's logic by an order of magnitude. memory_bits reports that "
                "storage separately. One consequence of keeping the hierarchy: "
                "logic_depth for the aggregate tops is the longest path through the "
                "top module only, not through the submodules below it, so it is only "
                "meaningful for the flattened candidate reports."
                if mode == "generic" else
                "Cell area in square micrometres from the IHP SG13G2 typical "
                "liberty file, standard cells only. No place and route, so no "
                "routing, filler, tap or pad area is included: this is a cell area "
                "sum, not a die area. Memories are mapped to flip-flops because "
                "this build binds no SRAM macros, so any memory bearing top is far "
                "larger here than a macro backed implementation would be."
            ),
            "tops": results,
        }
        (mode_dir / "summary.json").write_text(json.dumps(payload, indent=2) + "\n")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    with (args.out_dir / "summary.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["mode", "top", "total_cells", "combinational_cells",
                         "flip_flops", "gate_equivalents", "logic_depth",
                         "memory_bits", "chip_area_um2"])
        for row in rows:
            writer.writerow([row["mode"], row["top"], row["total_cells"],
                             row["combinational_cells"], row["flip_flops"],
                             row["gate_equivalents"], row["logic_depth"],
                             row["memory_bits"],
                             "" if row["chip_area_um2"] is None
                             else f"{row['chip_area_um2']:.3f}"])
    print(f"\nwrote {args.out_dir / 'summary.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
