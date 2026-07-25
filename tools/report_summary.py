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
    parser.add_argument("--activity", action="store_true")
    parser.add_argument("--chip", action="store_true")
    args = parser.parse_args()
    show_all = not (args.ppa or args.activity or args.chip)

    synth = load(RESULTS / "synth" / "generic" / "summary.json")
    perf = load(RESULTS / "perf" / "cycle_counts.json")
    gate = load(RESULTS / "activity" / "gate_summary.json")

    if args.ppa or show_all:
        print("## PPA per candidate\n")
        print(ppa_table(synth, perf, gate))
    if args.activity or show_all:
        print("## Switching activity against operand sign mix\n")
        print(activity_table(gate))
    if args.chip or show_all:
        print("## Whole chip\n")
        print(chip_table(synth))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
