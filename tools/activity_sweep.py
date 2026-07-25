#!/usr/bin/env python3
# Copyright 2026 Daniel Tyukov
# SPDX-License-Identifier: Apache-2.0
"""Switching-activity sweep across candidates and operand statistics.

This is the experiment the sign-magnitude candidate exists to settle. Every
candidate sees the same operand stream on the same clock in the same simulation,
so the only variable is the arithmetic. The stream is then regenerated with a
different fraction of negative operands and the whole thing repeated, which is what
turns a single number into a curve.

Why the fraction of negative operands is the right sweep variable
----------------------------------------------------------------
In two's complement, -1 is all ones and 0 is all zeros, so an operand stream that
crosses zero flips every high-order bit. In sign-magnitude the same crossing flips
one sign bit and leaves the magnitude bits nearly alone. If the sign-magnitude
hypothesis holds, the two's complement candidates' activity should rise with the
rate of sign changes while the sign-magnitude candidate's stays flatter. If it does
not hold, this sweep is what shows that.

Outputs, all committed:
  results/activity/engines_neg<pct>.json   full per-scope report per sweep point
  results/activity/summary.json            per candidate totals for every point
  results/activity/summary.csv             the same, for spreadsheets and plots

Run with:
    make power
"""

from __future__ import annotations

import argparse
import csv
import json
import pathlib
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tb"))

import gemm_model as gm  # noqa: E402  (needs the sys.path line above)
from vcd_activity import parse_vcd, report_to_dict  # noqa: E402

RTL_BENCH_TOP = "tb_activity_engines"
GATE_BENCH_TOP = "tb_activity_gate"
BENCH_TOP = RTL_BENCH_TOP
HARNESS_SCOPE = f"{BENCH_TOP}.u_harness"
ENGINE_INSTANCES = {
    gm.ENG_INFER: "u_eng0",
    gm.ENG_WALLACE: "u_eng1",
    gm.ENG_BOOTH4: "u_eng2",
    gm.ENG_SIGNMAG: "u_eng3",
    gm.ENG_BITSERIAL: "u_eng4",
}

# Reset and the first accumulator clear happen inside this many VCD time units.
# The bench uses a 10 ns clock and settles within 14 cycles, so 200 ps units at
# 1 ns/1 ps timescale is well clear of it. Transitions at or before this time are
# not counted, so no candidate is charged for coming out of reset.
SETTLE_TIME = 200_000  # in VCD time units (1 ps), that is 200 ns

DEFAULT_NEG_FRACTIONS = [0.0, 0.125, 0.25, 0.375, 0.5, 0.625, 0.75, 0.875, 1.0]
DEFAULT_TILES = 192


def rtl_sources() -> list[pathlib.Path]:
    listing = (REPO / "rtl" / "filelist.f").read_text().splitlines()
    files = [line.strip() for line in listing
             if line.strip() and not line.strip().startswith("#")]
    return [REPO / f for f in files]


def write_stimulus(path_a: pathlib.Path, path_b: pathlib.Path, tiles: int,
                   neg_fraction: float, seed: int) -> dict:
    """Write one hex byte per line for `tiles` operand tile pairs.

    Returns the realised operand statistics, which are reported alongside the
    activity so the sweep axis is the measured fraction rather than the requested
    one.
    """
    rng = np.random.default_rng(seed)
    a = gm.random_int8_biased(rng, (tiles, gm.TILE_M * gm.TILE_K), neg_fraction)
    b = gm.random_int8_biased(rng, (tiles, gm.TILE_K * gm.TILE_N), neg_fraction)

    for path, data in ((path_a, a), (path_b, b)):
        with path.open("w") as handle:
            for value in data.reshape(-1):
                handle.write(f"{gm.to_unsigned(int(value), 8):02x}\n")

    everything = np.concatenate([a.reshape(-1), b.reshape(-1)])
    return {
        "requested_neg_fraction": neg_fraction,
        "measured_neg_fraction": float(np.mean(everything < 0)),
        "operand_bytes": int(everything.size),
        "mean_abs": float(np.mean(np.abs(everything.astype(np.int64)))),
    }


def build_bench(build_dir: pathlib.Path) -> pathlib.Path:
    """Compile the activity bench with Icarus.

    Icarus is used rather than Verilator because its VCD contains every net in the
    design. Verilator optimises nets away before dumping, which would make the
    per-module breakdown depend on the simulator's optimiser rather than on the
    RTL. See docs/PPA_METHODOLOGY.md.
    """
    binary = build_dir / "activity_engines.vvp"
    sources = rtl_sources() + [
        REPO / "tb" / "tb_engine_harness.sv",
        REPO / "tb" / "tb_activity_engines.sv",
    ]
    cmd = ["iverilog", "-g2012", "-s", BENCH_TOP, "-o", str(binary)]
    cmd += [str(s) for s in sources]
    subprocess.run(cmd, check=True)
    return binary


def run_bench(binary: pathlib.Path, a_hex: pathlib.Path, b_hex: pathlib.Path,
              vcd: pathlib.Path, tiles: int, clear_every: int) -> str:
    result = subprocess.run(
        [
            "vvp", str(binary),
            f"+a_hex={a_hex}", f"+b_hex={b_hex}", f"+vcd={vcd}",
            f"+tiles={tiles}", f"+clear_every={clear_every}",
        ],
        check=True, capture_output=True, text=True,
    )
    return result.stdout.strip()


def measure(vcd: pathlib.Path) -> dict:
    """Per-candidate inclusive transition totals, plus the whole-design total."""
    report = parse_vcd(vcd, start_time=SETTLE_TIME, scope_filter=BENCH_TOP)
    per_candidate = {}
    for engine, instance in ENGINE_INSTANCES.items():
        prefix = f"{HARNESS_SCOPE}.{instance}"
        per_candidate[gm.ENGINE_NAMES[engine]] = report.subtree_total(prefix)
    return {
        "report": report,
        "per_candidate": per_candidate,
        "total": report.total_transitions,
        "nets": len(report.nets),
    }


def candidate_module_breakdown(report, engine: int) -> dict[str, int]:
    """Transitions inside one candidate, split by its immediate child instances."""
    prefix = f"{HARNESS_SCOPE}.{ENGINE_INSTANCES[engine]}"
    out: dict[str, int] = {}
    for net in report.nets.values():
        if net.scope != prefix and not net.scope.startswith(prefix + "."):
            continue
        remainder = net.scope[len(prefix):].lstrip(".")
        child = remainder.split(".")[0] if remainder else "(engine top)"
        out[child] = out.get(child, 0) + net.transitions
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


# ---------------------------------------------------------------------------
# Gate level sweep
#
# The candidates are described at deliberately different levels of abstraction:
# engine_infer is a behavioural `*` and `+` while the others are structural. At
# RTL that makes their net counts incomparable, because engine_infer has almost no
# internal nets to count. After synthesis every candidate is the same kind of
# object, a flat netlist of generic gates, so gate level transition counts are the
# measurement that compares like with like. This is the headline number.
# ---------------------------------------------------------------------------
SIMCELLS_CANDIDATES = [
    pathlib.Path("/usr/share/yosys/simcells.v"),
    pathlib.Path("/usr/local/share/yosys/simcells.v"),
]


def find_simcells() -> pathlib.Path:
    """Locate Yosys's simulation models for its generic gate primitives."""
    from_env = os.environ.get("YOSYS_SIMCELLS")
    if from_env:
        return pathlib.Path(from_env)
    for candidate in SIMCELLS_CANDIDATES:
        if candidate.exists():
            return candidate
    datadir = subprocess.run(["yosys-config", "--datdir"], capture_output=True,
                             text=True)
    if datadir.returncode == 0:
        guess = pathlib.Path(datadir.stdout.strip()) / "simcells.v"
        if guess.exists():
            return guess
    raise FileNotFoundError(
        "could not find yosys simcells.v; set YOSYS_SIMCELLS to its path"
    )


def synth_engine_netlist(engine: int, netlist_dir: pathlib.Path) -> pathlib.Path:
    """Synthesise one candidate to a flat generic gate netlist, with caching."""
    module = f"engine_{gm.ENGINE_NAMES[engine]}"
    netlist = netlist_dir / f"{module}_generic_netlist.v"
    if netlist.exists():
        return netlist
    netlist_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update({
        "TOP": module,
        "MODE": "generic",
        "OUT_DIR": str(netlist_dir),
        "FILELIST": str(REPO / "rtl" / "filelist.f"),
        "WRITE_NETLIST": "1",
    })
    log = netlist_dir / f"{module}_generic_yosys.log"
    with log.open("w") as handle:
        result = subprocess.run(
            ["yosys", "-c", str(REPO / "flow" / "yosys" / "synth.tcl")],
            cwd=REPO, env=env, stdout=handle, stderr=subprocess.STDOUT,
        )
    if result.returncode != 0:
        tail = "\n".join(log.read_text().splitlines()[-20:])
        raise RuntimeError(f"yosys failed for {module}:\n{tail}")
    return netlist


def build_gate_bench(engine: int, netlist: pathlib.Path,
                     build_dir: pathlib.Path) -> pathlib.Path:
    module = f"engine_{gm.ENGINE_NAMES[engine]}"
    binary = build_dir / f"gate_{module}.vvp"
    cmd = [
        "iverilog", "-g2012", "-s", GATE_BENCH_TOP,
        f"-DENGINE_MODULE={module}",
        "-o", str(binary),
        str(netlist), str(find_simcells()),
        str(REPO / "tb" / "tb_activity_gate.sv"),
    ]
    subprocess.run(cmd, check=True)
    return binary


def run_gate_bench(binary: pathlib.Path, a_hex: pathlib.Path, b_hex: pathlib.Path,
                   vcd: pathlib.Path, tiles: int, clear_every: int,
                   latency: int) -> str:
    result = subprocess.run(
        [
            "vvp", str(binary),
            f"+a_hex={a_hex}", f"+b_hex={b_hex}", f"+vcd={vcd}",
            f"+tiles={tiles}", f"+clear_every={clear_every}", f"+latency={latency}",
        ],
        check=True, capture_output=True, text=True,
    )
    return result.stdout.strip()


def gate_sweep(args) -> int:
    work = pathlib.Path(tempfile.mkdtemp(prefix="gemm-activity-gate-"))
    netlist_dir = REPO / "build" / "synth"
    print(f"work directory {work}")
    args.out_dir.mkdir(parents=True, exist_ok=True)

    binaries = {}
    cell_counts = {}
    for engine in sorted(ENGINE_INSTANCES):
        netlist = synth_engine_netlist(engine, netlist_dir)
        binaries[engine] = build_gate_bench(engine, netlist, work)
        # Cell count straight from the report next to the netlist, so the activity
        # summary carries the area it should be compared against.
        stat = netlist_dir / f"engine_{gm.ENGINE_NAMES[engine]}_generic_stat.txt"
        cells = None
        if stat.exists():
            for line in stat.read_text().splitlines():
                if "Number of cells:" in line:
                    cells = int(line.split(":")[1].strip())
        cell_counts[gm.ENGINE_NAMES[engine]] = cells
        print(f"  {gm.ENGINE_NAMES[engine]:<12} netlist ready ({cells} cells)")

    points = []
    for neg_fraction in args.neg_fractions:
        tag = f"neg{int(round(neg_fraction * 1000)):04d}"
        a_hex = work / f"a_{tag}.hex"
        b_hex = work / f"b_{tag}.hex"
        stats = write_stimulus(a_hex, b_hex, args.tiles, neg_fraction, args.seed)

        per_candidate = {}
        per_candidate_nets = {}
        for engine in sorted(ENGINE_INSTANCES):
            name = gm.ENGINE_NAMES[engine]
            vcd = work / f"gate_{name}_{tag}.vcd"
            run_gate_bench(binaries[engine], a_hex, b_hex, vcd, args.tiles,
                           args.clear_every, gm.ENGINE_LATENCY[engine])
            report = parse_vcd(vcd, start_time=SETTLE_TIME,
                               scope_filter=GATE_BENCH_TOP)
            dut_prefix = f"{GATE_BENCH_TOP}.u_dut"
            per_candidate[name] = report.subtree_total(dut_prefix)
            per_candidate_nets[name] = sum(
                1 for net in report.nets.values()
                if net.scope == dut_prefix or net.scope.startswith(dut_prefix + ".")
            )
            if args.check_determinism:
                again = parse_vcd(vcd, start_time=SETTLE_TIME,
                                  scope_filter=GATE_BENCH_TOP)
                assert again.subtree_total(dut_prefix) == per_candidate[name], (
                    "vcd_activity is not deterministic on the gate level dump"
                )
            if not args.keep_vcd:
                vcd.unlink(missing_ok=True)

        points.append({
            "tag": tag,
            "operand_stats": stats,
            "tiles": args.tiles,
            "clear_every": args.clear_every,
            "per_candidate": per_candidate,
            "per_candidate_nets": per_candidate_nets,
        })
        ranked = sorted(per_candidate.items(), key=lambda kv: kv[1])
        print(f"  neg={stats['measured_neg_fraction']:.3f}  "
              + "  ".join(f"{n}={c}" for n, c in ranked))

    summary = {
        "source": "tools/activity_sweep.py --level gate",
        "bench": GATE_BENCH_TOP,
        "simulator": "Icarus Verilog on Yosys generic gate netlists",
        "measure": "bit transitions (Hamming distance per value change)",
        "why_gate_level": (
            "The candidates are described at different levels of abstraction, so "
            "RTL net counts are not comparable between them: engine_infer is a "
            "behavioural multiply with almost no internal nets. After synthesis "
            "every candidate is a flat netlist of the same generic gates, which "
            "makes the transition counts comparable."
        ),
        "caveat": (
            "A switching-activity proxy for dynamic power, not a power "
            "measurement. Every net is weighted equally regardless of its real "
            "capacitance, and gate delays are zero in this simulation so glitch "
            "power is not captured. See docs/PPA_METHODOLOGY.md."
        ),
        "netlist_cells": cell_counts,
        "settle_time_vcd_units": SETTLE_TIME,
        "tiles_per_point": args.tiles,
        "seed": args.seed,
        "points": points,
    }
    (args.out_dir / "gate_summary.json").write_text(json.dumps(summary, indent=2) + "\n")

    with (args.out_dir / "gate_summary.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["measured_neg_fraction", "candidate", "transitions",
                         "transitions_per_tile", "netlist_cells"])
        for point in points:
            for name, count in sorted(point["per_candidate"].items()):
                writer.writerow([
                    f"{point['operand_stats']['measured_neg_fraction']:.6f}",
                    name, count, f"{count / point['tiles']:.3f}",
                    cell_counts.get(name, ""),
                ])

    print(f"\nwrote {args.out_dir / 'gate_summary.json'} and gate_summary.csv")
    if args.keep_vcd:
        print(f"dumps kept in {work}")
    else:
        shutil.rmtree(work, ignore_errors=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--tiles", type=int, default=DEFAULT_TILES,
                        help="operand tile pairs per sweep point")
    parser.add_argument("--clear-every", type=int, default=8,
                        help="clear the accumulators every n tiles, mirroring GRID_K")
    parser.add_argument("--seed", type=int, default=20260725)
    parser.add_argument("--neg-fractions", type=float, nargs="+",
                        default=DEFAULT_NEG_FRACTIONS)
    parser.add_argument("--out-dir", type=pathlib.Path,
                        default=REPO / "results" / "activity")
    parser.add_argument("--keep-vcd", action="store_true",
                        help="keep the dumps, which are large and not committed")
    parser.add_argument("--check-determinism", action="store_true", default=True,
                        help="parse each dump twice and require identical results")
    parser.add_argument("--level", choices=["rtl", "gate", "both"], default="both",
                        help="rtl is fast and gives a per-module breakdown; gate is "
                             "the comparable headline measurement")
    args = parser.parse_args(argv)

    if shutil.which("iverilog") is None or shutil.which("vvp") is None:
        print("activity_sweep: iverilog and vvp are required", file=sys.stderr)
        return 1

    args.out_dir.mkdir(parents=True, exist_ok=True)

    if args.level in ("gate", "both"):
        print("=== gate level sweep (comparable across all candidates) ===")
        rc = gate_sweep(args)
        if rc != 0:
            return rc
        if args.level == "gate":
            return 0
        print()

    print("=== RTL sweep (per-module breakdown, structural candidates only) ===")
    work = pathlib.Path(tempfile.mkdtemp(prefix="gemm-activity-"))
    print(f"work directory {work}")

    binary = build_bench(work)
    summary_points = []

    for neg_fraction in args.neg_fractions:
        tag = f"neg{int(round(neg_fraction * 1000)):04d}"
        a_hex = work / f"a_{tag}.hex"
        b_hex = work / f"b_{tag}.hex"
        vcd = work / f"engines_{tag}.vcd"

        stats = write_stimulus(a_hex, b_hex, args.tiles, neg_fraction, args.seed)
        bench_line = run_bench(binary, a_hex, b_hex, vcd, args.tiles, args.clear_every)
        result = measure(vcd)

        if args.check_determinism:
            again = measure(vcd)
            assert again["per_candidate"] == result["per_candidate"], (
                "vcd_activity is not deterministic: two parses of the same dump "
                f"disagreed ({result['per_candidate']} vs {again['per_candidate']})"
            )
            assert again["total"] == result["total"]

        point = {
            "tag": tag,
            "operand_stats": stats,
            "tiles": args.tiles,
            "clear_every": args.clear_every,
            "bench_report": bench_line,
            "nets_observed": result["nets"],
            "total_transitions": result["total"],
            "per_candidate": result["per_candidate"],
        }
        summary_points.append(point)

        detail = report_to_dict(result["report"], f"{BENCH_TOP} {tag}")
        detail["operand_stats"] = stats
        detail["per_candidate_inclusive"] = result["per_candidate"]
        detail["per_candidate_module_breakdown"] = {
            gm.ENGINE_NAMES[e]: candidate_module_breakdown(result["report"], e)
            for e in sorted(ENGINE_INSTANCES)
        }
        detail["top_nets"] = [
            {"net": name, "width": width, "transitions": count}
            for name, count, width in result["report"].top_nets(25)
        ]
        (args.out_dir / f"engines_{tag}.json").write_text(
            json.dumps(detail, indent=2) + "\n"
        )

        ranked = sorted(result["per_candidate"].items(), key=lambda kv: kv[1])
        print(f"  neg={stats['measured_neg_fraction']:.3f}  "
              + "  ".join(f"{name}={count}" for name, count in ranked))

        if not args.keep_vcd:
            vcd.unlink(missing_ok=True)

    summary = {
        "source": "tools/activity_sweep.py",
        "bench": BENCH_TOP,
        "simulator": "Icarus Verilog (complete net set in the VCD)",
        "measure": "bit transitions (Hamming distance per value change)",
        "caveat": (
            "A switching-activity proxy for dynamic power, not a power measurement. "
            "See docs/PPA_METHODOLOGY.md for what it does and does not tell you."
        ),
        "settle_time_vcd_units": SETTLE_TIME,
        "tiles_per_point": args.tiles,
        "seed": args.seed,
        "points": summary_points,
    }
    (args.out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")

    with (args.out_dir / "summary.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["measured_neg_fraction", "candidate", "transitions",
                         "transitions_per_tile"])
        for point in summary_points:
            for name, count in sorted(point["per_candidate"].items()):
                writer.writerow([
                    f"{point['operand_stats']['measured_neg_fraction']:.6f}",
                    name, count, f"{count / point['tiles']:.3f}",
                ])

    print(f"\nwrote {args.out_dir / 'summary.json'} and summary.csv")
    if not args.keep_vcd:
        shutil.rmtree(work, ignore_errors=True)
    else:
        print(f"dumps kept in {work}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
