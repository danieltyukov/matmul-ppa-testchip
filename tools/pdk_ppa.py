#!/usr/bin/env python3
# Copyright 2026 Daniel Tyukov
# SPDX-License-Identifier: Apache-2.0
"""Real PPA per candidate on the IHP SG13G2 PDK: um2, MHz and mW.

This is the measurement the chip exists to produce, and every number here comes from
the real target process rather than a technology-independent proxy.

For each candidate:

  1. Synthesise to SG13G2 standard cells at the slow corner, which is the corner a
     tapeout would close timing at. One netlist, then analysed at several corners:
     that is what a real flow does.
  2. Simulate that netlist against the PDK's own Verilog cell models with the shared
     operand stimulus. This doubles as post-synthesis functional verification on real
     cells, and produces the VCD that power annotation needs.
  3. Static timing analysis to find Fmax, closed loop: measure slack at a starting
     period, compute the period that would give zero slack, re-run there and confirm.
     Reporting `1 / (period - slack)` from a single run assumes the critical path delay
     is independent of the constraint, which is not exactly true, so the second run is
     the check.
  4. Power with the VCD annotated onto the netlist, split into internal, switching and
     leakage. `report_activity_annotation` gives the coverage, which is reported
     alongside the number rather than assumed to be complete.

Honest limits, all of which are in docs/PPA_METHODOLOGY.md:

  - Post-synthesis, not post-route. Parasitics are placement estimates from
    `set_wire_rc` plus `estimate_parasitics`, not extracted from routing. Real Fmax
    after routing is lower and real power is higher.
  - Area is standard cell area, not die area.
  - The gate level simulation is zero-delay, because the PDK's specify blocks have to
    be stripped for Icarus to parse them. Glitch power is therefore not in the VCD, and
    a design with a deep combinational tree is flattered.

    tools/pdk_ppa.py                 # every candidate
    tools/pdk_ppa.py --tops engine_booth4
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
sys.path.insert(0, str(REPO / "tb"))

import gemm_model as gm  # noqa: E402

PDK_DEFAULT = pathlib.Path.home() / ".local/share/pdk/IHP-Open-PDK/ihp-sg13g2"
BUILD = REPO / "build" / "pdk"
RESULTS = REPO / "results" / "pdk"

ENGINE_TOPS = [f"engine_{n}" for n in
               ["infer", "wallace", "booth4", "signmag", "bitserial"]]

# The corner a tapeout closes timing at, and the corner power is normally quoted at.
TIMING_CORNER = "slow_1p08V_125C"
POWER_CORNER = "typ_1p20V_25C"

# The activity bench runs a 10 ns clock, and power scales with frequency, so the SDC
# clock for the power run has to match the VCD or the annotation is inconsistent.
BENCH_CLOCK_NS = 10.0

# Starting point for the Fmax search. Generous enough that the first run has positive
# slack for the fast candidates and a large negative slack for the slow ones; either
# way the second iteration lands close.
FMAX_SEED_NS = 40.0

_SLACK_RE = re.compile(r"worst slack max\s+(-?[\d.]+)")
_AREA_RE = re.compile(r"Design area\s+([\d.]+)\s+um\^2")
_ANNOT_RE = re.compile(r"^\s*(vcd|unannotated)\s+(\d+)\s*$", re.M)
_POWER_ROW_RE = re.compile(
    r"^(Sequential|Combinational|Clock|Macro|Pad|Total)\s+"
    r"([\d.e+-]+)\s+([\d.e+-]+)\s+([\d.e+-]+)\s+([\d.e+-]+)", re.M)

NOISE = re.compile(r"unsupported expression|sg13g2_sdfrbpq|LEF file:|Features included|"
                   r"licensed under|OpenROAD 26Q3|Components of this program")


def lib(pdk: pathlib.Path, corner: str) -> pathlib.Path:
    return pdk / "libs.ref/sg13g2_stdcell/lib" / f"sg13g2_stdcell_{corner}.lib"


def run(cmd: list[str], log: pathlib.Path, cwd: pathlib.Path = REPO,
        env: dict | None = None) -> int:
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w") as handle:
        return subprocess.run(cmd, cwd=cwd, env=env, stdout=handle,
                              stderr=subprocess.STDOUT).returncode


def synthesise(top: str, pdk: pathlib.Path) -> pathlib.Path:
    """Map the candidate onto SG13G2 cells at the timing corner."""
    netlist = BUILD / f"{top}_sg13g2_netlist.v"
    env = dict(os.environ)
    env.update({
        "TOP": top, "MODE": "sg13g2", "OUT_DIR": str(BUILD),
        "WRITE_NETLIST": "1", "FLATTEN": "1", "EXPECT_LATCHES": "0",
        "MAP_MEMORY": "1", "JSON_DIR": str(BUILD),
        "FILELIST": str(REPO / "rtl" / "filelist.f"),
        "SG13G2_LIB": str(lib(pdk, TIMING_CORNER)),
    })
    code = run(["yosys", "-c", str(REPO / "flow" / "yosys" / "synth.tcl")],
               BUILD / f"{top}_synth.log", env=env)
    if code != 0 or not netlist.exists():
        raise RuntimeError(f"synthesis failed for {top}; see {BUILD}/{top}_synth.log")
    return netlist


def cell_area(top: str) -> float:
    text = (BUILD / f"{top}_sg13g2_stat.txt").read_text()
    match = re.search(r"Chip area for module '\\\\?" + re.escape(top) + r"':\s+([\d.]+)",
                      text)
    if not match:
        match = re.search(r"Chip area for .*?:\s+([\d.]+)", text)
    return float(match.group(1))


def strip_models(pdk: pathlib.Path) -> list[pathlib.Path]:
    """PDK cell models with their specify blocks removed, so Icarus can parse them."""
    out_dir = BUILD / "models"
    sources = [pdk / "libs.ref/sg13g2_stdcell/verilog/sg13g2_stdcell.v",
               pdk / "libs.ref/sg13g2_stdcell/verilog/sg13g2_udp.v"]
    if not all((out_dir / s.name).exists() for s in sources):
        subprocess.run(
            [sys.executable, str(REPO / "tools" / "strip_specify.py")]
            + [str(s) for s in sources] + ["--out-dir", str(out_dir)],
            check=True, capture_output=True,
        )
    return [out_dir / s.name for s in sources]


def write_stimulus(tiles: int, neg_fraction: float, seed: int) -> tuple[pathlib.Path,
                                                                       pathlib.Path]:
    import numpy as np
    rng = np.random.default_rng(seed)
    a = gm.random_int8_biased(rng, (tiles, gm.TILE_M * gm.TILE_K), neg_fraction)
    b = gm.random_int8_biased(rng, (tiles, gm.TILE_K * gm.TILE_N), neg_fraction)
    paths = []
    for name, data in (("a", a), ("b", b)):
        path = BUILD / f"stim_{name}.hex"
        path.write_text("".join(f"{gm.to_unsigned(int(v), 8):02x}\n"
                                for v in data.reshape(-1)))
        paths.append(path)
    return paths[0], paths[1]


def simulate(top: str, netlist: pathlib.Path, models: list[pathlib.Path],
             a_hex: pathlib.Path, b_hex: pathlib.Path, tiles: int,
             latency: int) -> pathlib.Path:
    """Simulate the PDK-mapped netlist and dump a VCD. Fails if it computes wrongly."""
    binary = BUILD / f"{top}.vvp"
    vcd = BUILD / f"{top}.vcd"
    cmd = ["iverilog", "-g2012", "-s", "tb_activity_gate",
           f"-DENGINE_MODULE={top}", "-o", str(binary), str(netlist)]
    cmd += [str(m) for m in models] + [str(REPO / "tb" / "tb_activity_gate.sv")]
    if run(cmd, BUILD / f"{top}_iverilog.log") != 0:
        raise RuntimeError(f"iverilog failed for {top}; see {BUILD}/{top}_iverilog.log")

    result = subprocess.run(
        ["vvp", str(binary), f"+a_hex={a_hex}", f"+b_hex={b_hex}", f"+vcd={vcd}",
         f"+tiles={tiles}", "+clear_every=8", f"+latency={latency}", f"+name={top}"],
        cwd=REPO, capture_output=True, text=True,
    )
    (BUILD / f"{top}_sim.log").write_text(result.stdout + result.stderr)
    if result.returncode != 0 or "errors=0" not in result.stdout:
        raise RuntimeError(
            f"the SG13G2 netlist for {top} does not match the reference:\n"
            f"{result.stdout[-800:]}"
        )
    return vcd


def sta_script(pdk: pathlib.Path, corner: str, netlist: pathlib.Path, top: str,
               period: float, vcd: pathlib.Path | None) -> str:
    stdcell = pdk / "libs.ref/sg13g2_stdcell"
    lines = [
        f"read_lef {stdcell}/lef/sg13g2_tech.lef",
        f"read_lef {stdcell}/lef/sg13g2_stdcell.lef",
        f"read_liberty {lib(pdk, corner)}",
        f"read_verilog {netlist}",
        f"link_design {top}",
        # Placement-estimated parasitics. Not extracted from routing, and the reports
        # say so.
        "set_wire_rc -signal -layer Metal3",
        "set_wire_rc -clock -layer Metal5",
        "estimate_parasitics -placement",
        f"create_clock -name clk -period {period} [get_ports clk_i]",
        # Operands arrive from the sequencer's tile registers, so they are launched by
        # the same clock rather than being asynchronous.
        "set_input_delay 0.0 -clock clk [get_ports rst_ni]",
        "set_input_delay 0.0 -clock clk [get_ports acc_clear_i]",
        "set_input_delay 0.0 -clock clk [get_ports launch_i]",
        "set_output_delay 0.0 -clock clk [get_ports ready_o]",
        "set_output_delay 0.0 -clock clk [get_ports valid_o]",
        "report_worst_slack -max -digits 4",
        "report_design_area",
    ]
    if vcd is not None:
        lines += [
            f"read_vcd -scope tb_activity_gate/u_dut {vcd}",
            "report_activity_annotation",
            "report_power -digits 8",
        ]
    return "\n".join(lines) + "\n"


def openroad(script: str, log: pathlib.Path) -> str:
    path = BUILD / f"{log.stem}.tcl"
    path.write_text(script)
    run(["openroad", "-no_init", "-exit", str(path.relative_to(REPO))], log)
    return log.read_text()


def parse_slack(text: str) -> float | None:
    match = _SLACK_RE.search(text)
    return float(match.group(1)) if match else None


def parse_power(text: str) -> dict:
    rows = {}
    for name, internal, switching, leakage, total in _POWER_ROW_RE.findall(text):
        rows[name.lower()] = {
            "internal_w": float(internal), "switching_w": float(switching),
            "leakage_w": float(leakage), "total_w": float(total),
        }
    annot = dict(_ANNOT_RE.findall(text))
    rows["annotation"] = {
        "annotated_pins": int(annot.get("vcd", 0)),
        "unannotated_pins": int(annot.get("unannotated", 0)),
    }
    return rows


def measure(top: str, pdk: pathlib.Path, tiles: int, neg_fraction: float,
            seed: int) -> dict:
    print(f"  {top}: synthesising at the {TIMING_CORNER} corner", flush=True)
    netlist = synthesise(top, pdk)
    area = cell_area(top)

    print(f"  {top}: simulating the PDK netlist ({area:,.0f} um2)", flush=True)
    models = strip_models(pdk)
    a_hex, b_hex = write_stimulus(tiles, neg_fraction, seed)
    engine_index = ENGINE_TOPS.index(top)
    latency = gm.ENGINE_LATENCY[engine_index]
    vcd = simulate(top, netlist, models, a_hex, b_hex, tiles, latency)

    # Fmax, closed loop.
    print(f"  {top}: timing at {TIMING_CORNER}", flush=True)
    text = openroad(sta_script(pdk, TIMING_CORNER, netlist, top, FMAX_SEED_NS, None),
                    BUILD / f"{top}_sta_seed.log")
    slack = parse_slack(text)
    if slack is None:
        raise RuntimeError(f"no slack reported for {top}; see {BUILD}/{top}_sta_seed.log")
    critical_ns = FMAX_SEED_NS - slack
    text2 = openroad(
        sta_script(pdk, TIMING_CORNER, netlist, top, round(critical_ns, 3), None),
        BUILD / f"{top}_sta_check.log")
    slack2 = parse_slack(text2)

    # Power, with the VCD annotated, at the corner power is normally quoted at.
    print(f"  {top}: power at {POWER_CORNER} with the VCD annotated", flush=True)
    ptext = openroad(
        sta_script(pdk, POWER_CORNER, netlist, top, BENCH_CLOCK_NS, vcd),
        BUILD / f"{top}_power.log")
    power = parse_power(ptext)

    annotated = power["annotation"]["annotated_pins"]
    unannotated = power["annotation"]["unannotated_pins"]
    coverage = annotated / (annotated + unannotated) if (annotated + unannotated) else 0.0

    return {
        "top": top,
        "timing_corner": TIMING_CORNER,
        "power_corner": POWER_CORNER,
        "cell_area_um2": area,
        "critical_path_ns": round(critical_ns, 4),
        "fmax_mhz": round(1000.0 / critical_ns, 2),
        "fmax_check_slack_ns": slack2,
        "seed_period_ns": FMAX_SEED_NS,
        "seed_slack_ns": slack,
        "power_clock_ns": BENCH_CLOCK_NS,
        "power": {k: v for k, v in power.items() if k != "annotation"},
        "activity_annotation": {
            "annotated_pins": annotated,
            "unannotated_pins": unannotated,
            "coverage": round(coverage, 6),
        },
        "stimulus": {"tiles": tiles, "neg_fraction": neg_fraction, "seed": seed},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--pdk", type=pathlib.Path, default=PDK_DEFAULT)
    parser.add_argument("--tops", nargs="+", default=ENGINE_TOPS)
    parser.add_argument("--tiles", type=int, default=48)
    parser.add_argument("--neg-fraction", type=float, default=0.5)
    parser.add_argument("--seed", type=int, default=20260725)
    parser.add_argument("--out-dir", type=pathlib.Path, default=RESULTS)
    args = parser.parse_args(argv)

    for tool in ("yosys", "iverilog", "vvp", "openroad"):
        if shutil.which(tool) is None:
            print(f"pdk_ppa: {tool} is required", file=sys.stderr)
            return 1
    if not lib(args.pdk, TIMING_CORNER).exists():
        print(f"pdk_ppa: no liberty at {lib(args.pdk, TIMING_CORNER)}.\n"
              f"Run tools/fetch_pdk.sh or pass --pdk.", file=sys.stderr)
        return 1

    BUILD.mkdir(parents=True, exist_ok=True)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    entries = {}
    for top in args.tops:
        print(f"{top}", flush=True)
        entry = measure(top, args.pdk, args.tiles, args.neg_fraction, args.seed)
        entries[top] = entry
        print(f"  -> {entry['cell_area_um2']:,.0f} um2, "
              f"{entry['fmax_mhz']:.1f} MHz, "
              f"{entry['power']['total']['total_w'] * 1e3:.2f} mW, "
              f"annotation {entry['activity_annotation']['coverage']:.1%}", flush=True)

    payload = {
        "source": "tools/pdk_ppa.py",
        "pdk": str(args.pdk),
        "timing_corner": TIMING_CORNER,
        "power_corner": POWER_CORNER,
        "note": (
            "Real IHP SG13G2 numbers, post-synthesis. Area is standard cell area, not "
            "die area: no routing, filler, tap, power grid or pad frame. Timing uses "
            "placement-estimated parasitics from set_wire_rc plus estimate_parasitics, "
            "not parasitics extracted from routing, so post-route Fmax is lower. Power "
            "is annotated from a VCD of the same netlist under an identical operand "
            "stream across candidates, and the annotation coverage is reported rather "
            "than assumed. The gate level simulation is zero-delay because the PDK "
            "specify blocks must be stripped for Icarus, so glitch power is not "
            "captured and deep combinational designs are flattered."
        ),
        "candidates": entries,
    }
    (args.out_dir / "summary.json").write_text(json.dumps(payload, indent=2) + "\n")

    with (args.out_dir / "summary.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["top", "cell_area_um2", "critical_path_ns", "fmax_mhz",
                         "internal_w", "switching_w", "leakage_w", "total_w",
                         "annotation_coverage"])
        for top, e in entries.items():
            total = e["power"]["total"]
            writer.writerow([top, f"{e['cell_area_um2']:.3f}",
                             e["critical_path_ns"], e["fmax_mhz"],
                             f"{total['internal_w']:.9f}",
                             f"{total['switching_w']:.9f}",
                             f"{total['leakage_w']:.9f}",
                             f"{total['total_w']:.9f}",
                             e["activity_annotation"]["coverage"]])
    print(f"\nwrote {args.out_dir / 'summary.json'} and summary.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
