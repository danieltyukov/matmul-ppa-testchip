#!/usr/bin/env python3
# Copyright 2026 Daniel Tyukov
# SPDX-License-Identifier: Apache-2.0
"""Print the committed measurements as markdown tables.

The README quotes numbers, and a number typed into prose goes stale the first time
the design changes. This prints the tables straight out of results/ so the README can
be checked against, or regenerated from, the data that produced it.

    python3 tools/report_summary.py            # everything
    python3 tools/report_summary.py --ppa      # just the PPA table
"""

from __future__ import annotations

import argparse
import json
import pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent
RESULTS = REPO / "results"
ORDER = ["infer", "wallace", "booth4", "signmag", "bitserial"]


def load(path: pathlib.Path):
    return json.loads(path.read_text()) if path.exists() else None


def ppa_table(synth, perf, gate) -> str:
    if not (synth and perf and gate):
        return "(PPA table needs results from make synth, make sim and make power)\n"
    tops = synth["tops"]
    point = min(gate["points"],
                key=lambda p: abs(p["operand_stats"]["measured_neg_fraction"] - 0.5))
    neg = point["operand_stats"]["measured_neg_fraction"]
    lines = [
        f"Switching activity column measured at {neg:.1%} negative operands, "
        f"{point['tiles']} tile launches.",
        "",
        "| Candidate | Cells | Gate equivalents | Logic depth | Cycles | MACs/cycle "
        "| Transitions/tile |",
        "|---|---|---|---|---|---|---|",
    ]
    for name in ORDER:
        top = tops.get(f"engine_{name}")
        cand = perf["candidates"].get(name)
        act = point["per_candidate"].get(name)
        if not top:
            continue
        lines.append(
            f"| `engine_{name}` | {top['total_cells']:,} | "
            f"{top['gate_equivalents']:,.0f} | {top['logic_depth']} | "
            f"{cand['cycles']:,} | {cand['macs_per_cycle']:.2f} | "
            f"{act / point['tiles']:,.0f} |"
        )
    return "\n".join(lines) + "\n"


def activity_table(gate) -> str:
    if not gate:
        return "(activity table needs results from make power)\n"
    points = sorted(gate["points"],
                    key=lambda p: p["operand_stats"]["measured_neg_fraction"])
    names = [n for n in ORDER if n in points[0]["per_candidate"]]
    header = "| Negative operands | " + " | ".join(names) + " | signmag vs wallace |"
    lines = [header, "|" + "---|" * (len(names) + 2)]
    for p in points:
        per = p["per_candidate"]
        row = [f"{p['operand_stats']['measured_neg_fraction']:.1%}"]
        for n in names:
            row.append(f"{per[n] / p['tiles']:,.0f}")
        if "wallace" in per and "signmag" in per:
            delta = (per["signmag"] - per["wallace"]) / per["wallace"] * 100
            row.append(f"{delta:+.1f}%")
        else:
            row.append("")
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines) + "\n"


def real_ppa_table(pdk, pnr, routed_power) -> str:
    """The headline table: real area, real frequency, real power, per candidate.

    Area and frequency are post-route. Power is post-route too where
    tools/verify_routed.py has measured it on the netlist the GDS was streamed from,
    and falls back to the post-synthesis measurement otherwise. The two are not
    interchangeable, so the fallback is marked in the cell rather than silently mixed
    into a column labelled post-route.
    """
    if not (pdk and pnr):
        return "(needs results from tools/pdk_ppa.py and tools/run_pnr.py)\n"
    cands, routed = pdk["candidates"], pnr["candidates"]
    measured = (routed_power or {}).get("candidates", {})
    clock = next(iter(cands.values()))["power_clock_ns"]
    lines = [
        f"Area and Fmax are post-route, at the {pnr['signoff_corner']} corner. Power is "
        f"at {1000.0 / clock:.0f} MHz, annotated from a gate level VCD at an even "
        "operand sign mix; a dagger marks power still measured on the synthesis "
        "netlist rather than the routed one.",
        "",
        "| Candidate | Cell area | Die area | Post-route Fmax | Power | Energy/tile |",
        "|---|---|---|---|---|---|",
    ]
    for name in ORDER:
        top = f"engine_{name}"
        cell = cands.get(top)
        die = routed.get(top)
        if not (cell and die):
            continue
        post = measured.get(top)
        if post:
            watts, energy, mark = post["power"]["total"]["total_w"], \
                post["energy_per_tile_pj"], ""
        else:
            watts, energy, mark = cell["power"]["total"]["total_w"], \
                cell["energy_per_tile_pj"], " †"
        lines.append(
            f"| `{top}` | {die['design__instance__area__stdcell']:,.0f} um2 | "
            f"{die['design__die__area'] / 1e6:.3f} mm2 | "
            f"{die['fmax_mhz']:.1f} MHz | "
            f"{watts * 1e3:.2f} mW{mark} | "
            f"{energy:,.0f} pJ{mark} |"
        )
    return "\n".join(lines) + "\n"


def timing_table(pdk, pnr) -> str:
    """Where the frequency comes from, and what limits it at each stage."""
    if not (pdk and pnr):
        return "(needs results from tools/pdk_ppa.py and tools/run_pnr.py)\n"
    cands, routed = pdk["candidates"], pnr["candidates"]
    lines = [
        "| Candidate | Netlist worst path | Limited by | Datapath path | Routed Fmax "
        "(slow) | Routed Fmax (typical) |",
        "|---|---|---|---|---|---|",
    ]
    for name in ORDER:
        top = f"engine_{name}"
        cell = cands.get(top)
        die = routed.get(top)
        if not cell:
            continue
        # A candidate that has not been routed has no routed frequency. Printing 0.0
        # MHz for it would be a fabricated number that reads like a measured one.
        by_corner = die["fmax_mhz_by_corner"] if die else {}
        slow = f"{die['fmax_mhz']:.1f} MHz" if die else "not routed"
        typical = by_corner.get("nom_typ_1p20V_25C")
        lines.append(
            f"| `{top}` | {cell['critical_path_ns']:.2f} ns | "
            f"`{cell['limiting_path']['from']}` | "
            f"{cell['datapath_path_ns']:.2f} ns | "
            f"{slow} | "
            f"{f'{typical:.1f} MHz' if typical is not None else 'not routed'} |"
        )
    return "\n".join(lines) + "\n"


def sign_power_table(sweep, gate) -> str:
    """The sign-magnitude hypothesis in watts, against the same test in transitions."""
    if not sweep:
        return "(needs results from tools/pdk_ppa.py --sweep)\n"
    points = {(p["top"], p["neg_fraction"]): p for p in sweep["points"]}
    fractions = sorted({p["neg_fraction"] for p in sweep["points"]})
    lines = [
        "| Negative operands | wallace | signmag | total power | switching power "
        "| transition count |",
        "|---|---|---|---|---|---|",
    ]
    for f in fractions:
        w = points.get(("engine_wallace", f))
        s = points.get(("engine_signmag", f))
        if not (w and s):
            continue
        proxy = ""
        if gate:
            point = min(gate["points"],
                        key=lambda p: abs(
                            p["operand_stats"]["measured_neg_fraction"] - f))
            per = point["per_candidate"]
            proxy = f"{100 * (per['signmag'] / per['wallace'] - 1):+.1f}%"
        lines.append(
            f"| {f:.0%} | {w['total_w'] * 1e3:.3f} mW | {s['total_w'] * 1e3:.3f} mW | "
            f"{100 * (s['total_w'] / w['total_w'] - 1):+.1f}% | "
            f"{100 * (s['switching_w'] / w['switching_w'] - 1):+.1f}% | {proxy} |"
        )
    return "\n".join(lines) + "\n"


def chip_table(synth) -> str:
    if not synth:
        return "(chip table needs results from make synth)\n"
    tops = synth["tops"]
    lines = ["| Scope | Cells | Flip-flops | Memory bits |", "|---|---|---|---|"]
    for name in ("engine_array", "bench_core", "gemm_bench_chip"):
        top = tops.get(name)
        if not top:
            continue
        lines.append(
            f"| `{name}` | {top['total_cells']:,} | {top['flip_flops']:,} | "
            f"{top.get('memory_bits', 0):,} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--ppa", action="store_true")
    parser.add_argument("--real", action="store_true")
    parser.add_argument("--timing", action="store_true")
    parser.add_argument("--activity", action="store_true")
    parser.add_argument("--chip", action="store_true")
    args = parser.parse_args()
    show_all = not (args.ppa or args.real or args.timing or args.activity or args.chip)

    synth = load(RESULTS / "synth" / "generic" / "summary.json")
    perf = load(RESULTS / "perf" / "cycle_counts.json")
    gate = load(RESULTS / "activity" / "gate_summary.json")
    pdk = load(RESULTS / "pdk" / "summary.json")
    pnr = load(RESULTS / "pnr" / "summary.json")
    sweep = load(RESULTS / "pdk" / "sign_sweep.json")
    routed_power = load(RESULTS / "pnr" / "routed_power.json")

    if args.real or show_all:
        print("## Real PPA per candidate\n")
        print(real_ppa_table(pdk, pnr, routed_power))
    if args.timing or show_all:
        print("## Where the frequency comes from\n")
        print(timing_table(pdk, pnr))
    if args.ppa or show_all:
        print("## Technology-independent PPA per candidate\n")
        print(ppa_table(synth, perf, gate))
    if args.activity or show_all:
        print("## Switching activity against operand sign mix\n")
        print(activity_table(gate))
        print("## Measured power against operand sign mix\n")
        print(sign_power_table(sweep, gate))
    if args.chip or show_all:
        print("## Whole chip\n")
        print(chip_table(synth))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
