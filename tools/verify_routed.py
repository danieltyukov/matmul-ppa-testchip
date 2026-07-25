#!/usr/bin/env python3
# Copyright 2026 Daniel Tyukov
# SPDX-License-Identifier: Apache-2.0
"""Simulate the routed netlist, and measure its power with the routing's own parasitics.

Place and route rewrites the design: it inserts buffers, resizes cells, adds antenna
diodes and hold buffers, and hands the result to a router. LVS says the routed layout
matches that netlist. Nothing in the flow says the routed netlist still computes a
matrix product. This does.

For each candidate that has been routed:

  1. Compile LibreLane's final netlist against the PDK's own Verilog cell models and run
     the shared operand stimulus through it, checking every output element against the
     reference. That is post-route functional verification, and it is the strongest
     functional statement in this repository because it runs on the netlist the GDS was
     made from.
  2. Annotate the VCD from that run onto the same netlist in OpenROAD, with the
     parasitics extracted from the routing, and report power. This is the best power
     number here: real cells, real wire capacitance, real switching activity, and the
     annotation coverage reported rather than assumed.

The remaining gap is glitch power. The gate level simulation is zero-delay because the
PDK's specify blocks have to be stripped for Icarus to parse them, so a carry chain that
settles through three intermediate values in a real cycle is counted once here.

    tools/verify_routed.py                       # every routed candidate
    tools/verify_routed.py --tops engine_booth4
"""

from __future__ import annotations

import argparse
import csv
import json
import pathlib
import shutil
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "tb"))

import gemm_model as gm  # noqa: E402
import pdk_ppa as pp  # noqa: E402

CONFIG_DIR = REPO / "flow" / "librelane"
BUILD = REPO / "build" / "routed"
RESULTS = REPO / "results" / "pnr"
RUN_TAG = "pnr"


def routed_netlist(top: str) -> pathlib.Path:
    return CONFIG_DIR / top / "runs" / RUN_TAG / "final" / "nl" / f"{top}.nl.v"


def routed_spef(top: str) -> pathlib.Path | None:
    spef = CONFIG_DIR / top / "runs" / RUN_TAG / "final" / "spef"
    return next(iter(sorted(spef.rglob("*.spef"))), None)


def power_script(pdk: pathlib.Path, netlist: pathlib.Path, spef: pathlib.Path,
                 top: str, vcd: pathlib.Path, period: float) -> str:
    stdcell = pdk / "libs.ref/sg13g2_stdcell"
    return "\n".join([
        f"read_lef {stdcell}/lef/sg13g2_tech.lef",
        f"read_lef {stdcell}/lef/sg13g2_stdcell.lef",
        f"read_liberty {pp.lib(pdk, pp.POWER_CORNER)}",
        f"read_verilog {netlist}",
        f"link_design {top}",
        # The parasitics the router produced, not an estimate from placement.
        f"read_spef {spef}",
        f"create_clock -name clk -period {period} [get_ports clk_i]",
        "set_propagated_clock [all_clocks]",
        f"read_vcd -scope tb_activity_gate/u_dut {vcd}",
        "report_activity_annotation",
        "report_power -digits 8",
    ]) + "\n"


def measure(top: str, pdk: pathlib.Path, tiles: int, neg_fraction: float,
            seed: int) -> dict:
    netlist = routed_netlist(top)
    spef = routed_spef(top)
    if spef is None:
        raise RuntimeError(f"no SPEF for {top}; the run did not reach extraction")

    print(f"  {top}: simulating the routed netlist", flush=True)
    models = pp.strip_models(pdk)
    a_hex, b_hex = pp.write_stimulus(tiles, neg_fraction, seed)
    latency = gm.ENGINE_LATENCY[pp.ENGINE_TOPS.index(top)]
    # pdk_ppa.simulate fails loudly if any output element disagrees with the reference,
    # so reaching the next line is the functional result.
    vcd = pp.simulate(f"{top}_routed", netlist, models, a_hex, b_hex, tiles, latency,
                      pp.BENCH_CLOCK_NS, module=top)

    print(f"  {top}: power with the routed parasitics", flush=True)
    text = pp.openroad(
        power_script(pdk, netlist, spef, top, vcd, pp.BENCH_CLOCK_NS),
        BUILD / f"{top}_routed_power.log")
    power = pp.parse_power(text)
    annotated = power["annotation"]["annotated_pins"]
    unannotated = power["annotation"]["unannotated_pins"]
    total = power["total"]["total_w"]
    span_ns = pp.vcd_span_ns(vcd)

    return {
        "top": top,
        "netlist": str(netlist.relative_to(REPO)),
        "spef": str(spef.relative_to(REPO)),
        "functional": "pass",
        "power_corner": pp.POWER_CORNER,
        "clock_ns": pp.BENCH_CLOCK_NS,
        "power": {k: v for k, v in power.items() if k != "annotation"},
        "energy_per_tile_pj": round(total * span_ns * 1e3 / tiles, 3),
        "activity_annotation": {
            "annotated_pins": annotated,
            "unannotated_pins": unannotated,
            "coverage": round(annotated / max(annotated + unannotated, 1), 6),
        },
        "stimulus": {"tiles": tiles, "neg_fraction": neg_fraction, "seed": seed},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--pdk", type=pathlib.Path, default=pp.PDK_DEFAULT)
    parser.add_argument("--tops", nargs="+", default=pp.ENGINE_TOPS)
    parser.add_argument("--tiles", type=int, default=48)
    parser.add_argument("--neg-fraction", type=float, default=0.5)
    parser.add_argument("--seed", type=int, default=20260725)
    args = parser.parse_args(argv)

    for tool in ("iverilog", "vvp", "openroad"):
        if shutil.which(tool) is None:
            print(f"verify_routed: {tool} is required", file=sys.stderr)
            return 1

    BUILD.mkdir(parents=True, exist_ok=True)
    pp.BUILD.mkdir(parents=True, exist_ok=True)
    RESULTS.mkdir(parents=True, exist_ok=True)

    tops = [t for t in args.tops if routed_netlist(t).exists()]
    missing = [t for t in args.tops if t not in tops]
    if not tops:
        print("verify_routed: no routed netlist under flow/librelane/*/runs. "
              "Run `make pnr` first.", file=sys.stderr)
        return 1
    if missing:
        print(f"verify_routed: not routed yet, skipping: {', '.join(missing)}")

    entries = {}
    for top in tops:
        print(top, flush=True)
        entry = measure(top, args.pdk, args.tiles, args.neg_fraction, args.seed)
        entries[top] = entry
        print(f"  -> functional pass, "
              f"{entry['power']['total']['total_w'] * 1e3:.2f} mW at "
              f"{1000.0 / pp.BENCH_CLOCK_NS:.0f} MHz, "
              f"{entry['energy_per_tile_pj']:,.0f} pJ/tile, "
              f"annotation {entry['activity_annotation']['coverage']:.1%}", flush=True)

    payload = {
        "source": "tools/verify_routed.py",
        "power_corner": pp.POWER_CORNER,
        "clock_ns": pp.BENCH_CLOCK_NS,
        "note": (
            "Post-route functional verification and post-route power. The netlist is "
            "LibreLane's final netlist, the one the GDS was streamed from. 'functional: "
            "pass' means every output element of every tile matched the NumPy reference "
            "model, on the routed netlist, simulated against the PDK's own cell models. "
            "Power is annotated from the VCD of that run with the parasitics extracted "
            "from the routing, so it is measured on real cells with real wire "
            "capacitance and real switching activity. The simulation is zero-delay, "
            "because the PDK specify blocks have to be stripped for Icarus, so glitch "
            "power is still not counted."
        ),
        "candidates": entries,
    }
    (RESULTS / "routed_power.json").write_text(json.dumps(payload, indent=2) + "\n")

    with (RESULTS / "routed_power.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["top", "functional", "internal_w", "switching_w", "leakage_w",
                         "total_w", "energy_per_tile_pj", "annotation_coverage"])
        for top, e in entries.items():
            t = e["power"]["total"]
            writer.writerow([top, e["functional"], f"{t['internal_w']:.9f}",
                             f"{t['switching_w']:.9f}", f"{t['leakage_w']:.9f}",
                             f"{t['total_w']:.9f}", e["energy_per_tile_pj"],
                             e["activity_annotation"]["coverage"]])
    print(f"\nwrote {(RESULTS / 'routed_power.json').relative_to(REPO)} and "
          f"routed_power.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
