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
  3. Static timing analysis, twice: the worst path in the whole netlist, and the worst
     path through the arithmetic on its own. Both are needed, because on a raw Yosys
     netlist they are different things. Yosys does no load-aware buffering, so
     `acc_clear_i` and `launch_i` reach 512 accumulator flip-flops through one gate and
     that unbuffered net is the worst path in every single-cycle candidate, at within
     15 ps of the same delay. Quoting it as the candidate's Fmax would report the same
     number for four different multipliers, which is exactly what happened before this
     was split. The operand-to-accumulator path is what distinguishes the
     microarchitectures, and the routed netlist in results/pnr is what fixes the
     control net, because place and route inserts the buffer tree.

     The whole-netlist number is measured closed loop: slack at a starting period, then
     the period that would give zero slack, re-run there and confirmed. Reporting
     `1 / (period - slack)` from a single run assumes the critical path delay is
     independent of the constraint, which is not exactly true, so the second run is the
     check.
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

# Power scales with frequency, so the SDC clock for the power run has to match the clock
# the VCD was written at or the annotation is inconsistent. Both come from this one
# constant: it is passed to the bench as +half_ns and to OpenROAD as the clock period.
# 20 ns is the chip's declared target in constraints/clocks.sdc and the period every
# candidate is placed and routed at, so every power number in this repository is quoted
# at one frequency the design is actually built for.
BENCH_CLOCK_NS = 20.0

# Starting point for the Fmax search. Generous enough that the first run has positive
# slack for the fast candidates and a large negative slack for the slow ones; either
# way the second iteration lands close.
FMAX_SEED_NS = 40.0

_SLACK_RE = re.compile(r"worst slack max\s+(-?[\d.]+)")
# One reported path: its endpoints and its slack. Used to name the net that limits the
# netlist, so a reader can see for themselves that it is a control fanout and not the
# multiplier.
_PATH_RE = re.compile(
    r"Startpoint: (\S+).*?Endpoint: (\S+).*?(-?[\d.]+)\s+slack \((?:MET|VIOLATED)\)",
    re.S)
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


# The netlist does not depend on the stimulus, so the sign sweep synthesises each
# candidate once and reuses it. Keyed by top, cleared by deleting build/pdk.
_NETLISTS: dict[str, pathlib.Path] = {}


def synthesise(top: str, pdk: pathlib.Path) -> pathlib.Path:
    """Map the candidate onto SG13G2 cells at the timing corner."""
    if top in _NETLISTS:
        return _NETLISTS[top]
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
    _NETLISTS[top] = netlist
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
             latency: int, clock_ns: float) -> pathlib.Path:
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
         f"+tiles={tiles}", "+clear_every=8", f"+latency={latency}", f"+name={top}",
         f"+half_ns={clock_ns / 2.0}"],
        cwd=REPO, capture_output=True, text=True,
    )
    (BUILD / f"{top}_sim.log").write_text(result.stdout + result.stderr)
    if result.returncode != 0 or "errors=0" not in result.stdout:
        raise RuntimeError(
            f"the SG13G2 netlist for {top} does not match the reference:\n"
            f"{result.stdout[-800:]}"
        )
    return vcd


_TIMESCALE_RE = re.compile(r"\$timescale\s+(\d+)\s*([munpf]?s)")
_UNIT_NS = {"s": 1e9, "ms": 1e6, "us": 1e3, "ns": 1.0, "ps": 1e-3, "fs": 1e-6}


def vcd_span_ns(vcd: pathlib.Path) -> float:
    """How long the dump covers, in nanoseconds.

    Needed to turn average power into energy per tile. Watts are per second and the
    candidates take different numbers of cycles per tile, so power alone compares a
    bit-serial engine against a single-cycle one on different terms. Energy per tile is
    the same question for both.
    """
    with vcd.open("rb") as handle:
        head = handle.read(4096).decode("ascii", "replace")
        handle.seek(max(0, handle.seek(0, os.SEEK_END) - 8192))
        tail = handle.read().decode("ascii", "replace")
    match = _TIMESCALE_RE.search(head)
    unit = _UNIT_NS[match.group(2)] * int(match.group(1)) if match else 1.0
    stamps = re.findall(r"^#(\d+)", tail, re.M)
    return float(stamps[-1]) * unit if stamps else 0.0


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
        # Every port is constrained. Operands, control and results all cross to the
        # sequencer's registers in this same clock domain, so they are timed against the
        # same clock with no external delay: the whole period belongs to the candidate.
        #
        # This matters more than it looks. An unconstrained input has no arrival time,
        # so every path through it is invisible to the analysis: leaving a_tile_i and
        # b_tile_i out means the multiplier array is never timed at all, and the report
        # is then about the control logic. `remove_from_collection` does not exist in
        # this OpenROAD build, so the ports are listed rather than derived.
        "set_input_delay 0.0 -clock clk [get_ports {rst_ni acc_clear_i launch_i}]",
        "set_input_delay 0.0 -clock clk [get_ports a_tile_i]",
        "set_input_delay 0.0 -clock clk [get_ports b_tile_i]",
        "set_output_delay 0.0 -clock clk [get_ports {ready_o valid_o mac_tick_o}]",
        "set_output_delay 0.0 -clock clk [get_ports c_tile_o]",
        "report_worst_slack -max -digits 4",
        # The worst path with its endpoints, so the report names what limits the
        # netlist instead of only how much.
        "report_checks -path_delay max -digits 4 -group_path_count 1 -fields {}",
        # The arithmetic on its own: operand ports to whatever register they reach.
        "puts {=== datapath ===}",
        "report_checks -from [get_ports {a_tile_i b_tile_i}] -path_delay max "
        "-digits 4 -group_path_count 1 -endpoint_path_count 1 -fields {}",
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


def parse_paths(text: str) -> list[dict]:
    """Every reported path, as start, end and slack."""
    return [{"from": f, "to": t, "slack_ns": float(s)}
            for f, t, s in _PATH_RE.findall(text)]


def worst_path(text: str) -> dict | None:
    paths = parse_paths(text)
    return min(paths, key=lambda p: p["slack_ns"]) if paths else None


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
    vcd = simulate(top, netlist, models, a_hex, b_hex, tiles, latency, BENCH_CLOCK_NS)

    # Fmax, closed loop.
    print(f"  {top}: timing at {TIMING_CORNER}", flush=True)
    text = openroad(sta_script(pdk, TIMING_CORNER, netlist, top, FMAX_SEED_NS, None),
                    BUILD / f"{top}_sta_seed.log")
    slack = parse_slack(text)
    if slack is None:
        raise RuntimeError(f"no slack reported for {top}; see {BUILD}/{top}_sta_seed.log")
    critical_ns = FMAX_SEED_NS - slack

    # Split the report at the marker: before it is every path group, after it is the
    # operand-to-register path on its own.
    head, _, tail = text.partition("=== datapath ===")
    limiter = worst_path(head)
    datapath = worst_path(tail)

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

    datapath_ns = (FMAX_SEED_NS - datapath["slack_ns"]) if datapath else None

    # Energy, so a candidate that takes eight cycles per tile is charged for all eight.
    span_ns = vcd_span_ns(vcd)
    total_w = power["total"]["total_w"]
    energy_pj = total_w * span_ns * 1e3 / tiles if tiles else None

    return {
        "top": top,
        "timing_corner": TIMING_CORNER,
        "power_corner": POWER_CORNER,
        "cell_area_um2": area,
        "critical_path_ns": round(critical_ns, 4),
        "fmax_mhz": round(1000.0 / critical_ns, 2),
        "fmax_check_slack_ns": slack2,
        "limiting_path": limiter,
        "datapath_path_ns": round(datapath_ns, 4) if datapath_ns else None,
        "datapath_fmax_mhz": round(1000.0 / datapath_ns, 2) if datapath_ns else None,
        "datapath_path": datapath,
        "seed_period_ns": FMAX_SEED_NS,
        "seed_slack_ns": slack,
        "power_clock_ns": BENCH_CLOCK_NS,
        "vcd_span_ns": round(span_ns, 3),
        "cycles_per_tile": round(span_ns / BENCH_CLOCK_NS / tiles, 3) if tiles else None,
        "energy_per_tile_pj": round(energy_pj, 3) if energy_pj else None,
        "power": {k: v for k, v in power.items() if k != "annotation"},
        "activity_annotation": {
            "annotated_pins": annotated,
            "unannotated_pins": unannotated,
            "coverage": round(coverage, 6),
        },
        "stimulus": {"tiles": tiles, "neg_fraction": neg_fraction, "seed": seed},
    }


def sweep(args) -> int:
    """Power against operand sign mix, in watts, for the candidates given.

    The switching-activity proxy in results/activity says sign-magnitude encoding cuts
    transitions. This is the same experiment with the same operand streams, measured as
    power on real cells: same netlists, same stimulus, one point per sign mix. Whether
    the two agree is itself worth knowing, because a transition count weights every net
    equally and real power does not.
    """
    points = []
    for fraction in args.sweep:
        for top in args.tops:
            print(f"{top} at {fraction:.0%} negative operands", flush=True)
            entry = measure(top, args.pdk, args.tiles, fraction, args.seed)
            total = entry["power"]["total"]
            points.append({
                "top": top,
                "neg_fraction": fraction,
                "total_w": total["total_w"],
                "internal_w": total["internal_w"],
                "switching_w": total["switching_w"],
                "leakage_w": total["leakage_w"],
                "cycles_per_tile": entry["cycles_per_tile"],
                "energy_per_tile_pj": entry["energy_per_tile_pj"],
                "coverage": entry["activity_annotation"]["coverage"],
            })
            print(f"  -> {total['total_w'] * 1e3:.3f} mW "
                  f"(switching {total['switching_w'] * 1e3:.3f} mW)", flush=True)

    payload = {
        "source": "tools/pdk_ppa.py --sweep",
        "pdk": str(args.pdk),
        "power_corner": POWER_CORNER,
        "clock_ns": BENCH_CLOCK_NS,
        "stimulus": {"tiles": args.tiles, "seed": args.seed},
        "note": (
            "Power in watts against the fraction of negative operands, annotated from a "
            "VCD of each candidate's own SG13G2 netlist under the identical operand "
            "stream. This is the same experiment as the transition-count sweep in "
            "results/activity, measured in a physical unit. Dynamic power scales with "
            "frequency and these are quoted at clock_ns. Glitch power is not captured, "
            "because the gate level simulation is zero-delay."
        ),
        "points": points,
    }
    (args.out_dir / "sign_sweep.json").write_text(json.dumps(payload, indent=2) + "\n")

    with (args.out_dir / "sign_sweep.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["top", "neg_fraction", "internal_w", "switching_w",
                         "leakage_w", "total_w", "energy_per_tile_pj", "coverage"])
        for p in points:
            writer.writerow([p["top"], p["neg_fraction"], f"{p['internal_w']:.9f}",
                             f"{p['switching_w']:.9f}", f"{p['leakage_w']:.9f}",
                             f"{p['total_w']:.9f}", p["energy_per_tile_pj"],
                             p["coverage"]])
    print(f"\nwrote {args.out_dir / 'sign_sweep.json'} and sign_sweep.csv")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--pdk", type=pathlib.Path, default=PDK_DEFAULT)
    parser.add_argument("--tops", nargs="+", default=ENGINE_TOPS)
    parser.add_argument("--tiles", type=int, default=48)
    parser.add_argument("--neg-fraction", type=float, default=0.5)
    parser.add_argument("--seed", type=int, default=20260725)
    parser.add_argument("--out-dir", type=pathlib.Path, default=RESULTS)
    parser.add_argument(
        "--sweep", nargs="+", type=float, default=None,
        help="measure at these negative-operand fractions and write sign_sweep.json. "
             "This is the sign-magnitude hypothesis measured in watts rather than in "
             "transition counts.")
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

    if args.sweep is not None:
        return sweep(args)

    entries = {}
    for top in args.tops:
        print(f"{top}", flush=True)
        entry = measure(top, args.pdk, args.tiles, args.neg_fraction, args.seed)
        entries[top] = entry
        print(f"  -> {entry['cell_area_um2']:,.0f} um2, "
              f"netlist {entry['fmax_mhz']:.1f} MHz "
              f"(limited by {entry['limiting_path']['from']} -> "
              f"{entry['limiting_path']['to']}), "
              f"datapath {entry['datapath_fmax_mhz']:.1f} MHz, "
              f"{entry['power']['total']['total_w'] * 1e3:.2f} mW at "
              f"{1000.0 / BENCH_CLOCK_NS:.0f} MHz, "
              f"{entry['energy_per_tile_pj']:.1f} pJ/tile, "
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
            "not parasitics extracted from routing. critical_path_ns and fmax_mhz are "
            "the worst path in the netlist, and limiting_path names it: on a Yosys "
            "netlist that path is the unbuffered acc_clear_i or launch_i fanout to 512 "
            "accumulator flip-flops, not the arithmetic, which is why it is nearly "
            "identical across the single-cycle candidates. datapath_path_ns is the "
            "operand-to-accumulator path, which is the number that distinguishes the "
            "microarchitectures. Place and route buffers the control net, so the "
            "routed frequencies in results/pnr/summary.json are the ones to quote. "
            "Power is annotated from a VCD of the same netlist under an identical "
            "operand stream across candidates, at power_clock_ns, and the annotation "
            "coverage is reported rather than assumed. Dynamic power scales with "
            "frequency. The gate level simulation is zero-delay because the PDK "
            "specify blocks must be stripped for Icarus, so glitch power is not "
            "captured and deep combinational designs are flattered."
        ),
        "candidates": entries,
    }
    (args.out_dir / "summary.json").write_text(json.dumps(payload, indent=2) + "\n")

    with (args.out_dir / "summary.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["top", "cell_area_um2", "critical_path_ns", "fmax_mhz",
                         "datapath_path_ns", "datapath_fmax_mhz",
                         "internal_w", "switching_w", "leakage_w", "total_w",
                         "cycles_per_tile", "energy_per_tile_pj",
                         "annotation_coverage"])
        for top, e in entries.items():
            total = e["power"]["total"]
            writer.writerow([top, f"{e['cell_area_um2']:.3f}",
                             e["critical_path_ns"], e["fmax_mhz"],
                             e["datapath_path_ns"], e["datapath_fmax_mhz"],
                             f"{total['internal_w']:.9f}",
                             f"{total['switching_w']:.9f}",
                             f"{total['leakage_w']:.9f}",
                             f"{total['total_w']:.9f}",
                             e["cycles_per_tile"], e["energy_per_tile_pj"],
                             e["activity_annotation"]["coverage"]])
    print(f"\nwrote {args.out_dir / 'summary.json'} and summary.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
