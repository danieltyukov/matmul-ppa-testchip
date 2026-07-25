#!/usr/bin/env python3
# Copyright 2026 Daniel Tyukov
# SPDX-License-Identifier: Apache-2.0
"""Place and route every candidate with LibreLane, and harvest the routed numbers.

Synthesis area is not die area, and a synthesis timing estimate is not a routed
timing result. This is the script that produces the real ones: it drives LibreLane's
Classic flow (Yosys, OpenROAD, KLayout DRC and LVS, antenna, slew and capacitance
signoff) on each candidate at an identical clock constraint, then reads
`final/metrics.json` and writes the numbers this repository quotes.

Every candidate is routed at the same `--period`, so die area, instance area and power
are comparable between them. Post-route slack is reported at all three PDK corners,
and the slow corner is the one Fmax is quoted from, because that is the corner a
tapeout closes at.

    tools/run_pnr.py                        # every candidate, sequentially
    tools/run_pnr.py --tops engine_booth4   # one of them
    tools/run_pnr.py --configs-only         # regenerate the committed configs
    tools/run_pnr.py --harvest-only         # re-read metrics from finished runs

Process hygiene: each LibreLane invocation is started in its own process group and its
pid is written to build/pnr/<top>.pid. Cleanup signals that process group and nothing
else, so a stray `pkill -f openroad` can never take out someone else's run.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO / "flow" / "librelane"
BUILD = REPO / "build" / "pnr"
RESULTS = REPO / "results" / "pnr"
RUN_TAG = "pnr"

# The chip's declared target, from constraints/clocks.sdc. Routing every candidate at
# one constraint is what makes their area and power comparable; a per-candidate
# constraint would report each design's own best frequency and an area measured under a
# different amount of optimisation pressure.
DEFAULT_PERIOD_NS = 20.0

# A candidate is arithmetic plus one register bank, so it packs harder than a chip with
# macros and a pad ring. 40 percent leaves room for the operand and accumulator buses,
# which are what actually congest.
DEFAULT_UTIL = 40

# The PDK corner names LibreLane defines for ihp-sg13g2. Signoff STA reports slack at
# all three; Fmax is quoted from the slow one.
CORNERS = ["nom_slow_1p08V_125C", "nom_typ_1p20V_25C", "nom_fast_1p32V_m40C"]
SIGNOFF_CORNER = "nom_slow_1p08V_125C"

# Source files per candidate. Deliberately minimal rather than the whole filelist: a
# forker reading the config should see exactly what the block is made of.
COMMON = ["rtl/pkg/gemm_pkg.sv", "rtl/engines/acc_bank.sv"]
TREE = ["rtl/engines/csa_reduce.sv"]

SOURCES = {
    "engine_infer": COMMON + ["rtl/engines/dot_infer.sv", "rtl/engines/engine_infer.sv"],
    "engine_wallace": COMMON + TREE + ["rtl/engines/dot_wallace.sv",
                                       "rtl/engines/engine_wallace.sv"],
    "engine_booth4": COMMON + TREE + ["rtl/engines/dot_booth4.sv",
                                      "rtl/engines/engine_booth4.sv"],
    "engine_signmag": COMMON + TREE + ["rtl/engines/dot_signmag.sv",
                                       "rtl/engines/engine_signmag.sv"],
    "engine_bitserial": COMMON + ["rtl/engines/engine_bitserial.sv"],
    # The shared block: all five candidates, their clock gates and their operand
    # isolation. This is the candidates in full-chip context rather than standalone.
    "engine_array": COMMON + TREE + [
        "rtl/lib/clock_gate.sv",
        "rtl/engines/dot_infer.sv", "rtl/engines/dot_wallace.sv",
        "rtl/engines/dot_booth4.sv", "rtl/engines/dot_signmag.sv",
        "rtl/engines/engine_infer.sv", "rtl/engines/engine_wallace.sv",
        "rtl/engines/engine_booth4.sv", "rtl/engines/engine_signmag.sv",
        "rtl/engines/engine_bitserial.sv", "rtl/seq/engine_array.sv",
    ],
}

CANDIDATES = ["engine_infer", "engine_wallace", "engine_booth4", "engine_signmag",
              "engine_bitserial"]

# Metrics worth committing. LibreLane writes 191 keys; these are the ones the README and
# the charts use, and keeping the list explicit means a LibreLane upgrade that renames
# one shows up as an empty column rather than passing silently.
#
# design__instance__area includes the fill cells, which exist to satisfy density rules
# and are not the design. design__instance__area__stdcell is the design's own cells, and
# that is the number to compare against synthesis.
HEADLINE = [
    "design__die__area", "design__core__area",
    "design__instance__area", "design__instance__area__stdcell",
    "design__instance__area__class:fill_cell",
    "design__instance__area__class:timing_repair_buffer",
    "design__instance__utilization",
    "design__instance__count", "design__instance__count__stdcell",
    "design__instance__count__class:sequential_cell",
    "route__wirelength", "route__vias", "route__net",
    "power__total", "power__internal__total", "power__switching__total",
    "power__leakage__total",
    "magic__drc_error__count", "klayout__drc_error__count",
    "design__lvs_error__count", "route__drc_errors",
    "route__antenna_violation__count",
    "design__max_fanout_violation__count", "design__max_slew_violation__count",
    "design__max_cap_violation__count",
]


def config_for(top: str, period: float, util: int, threads: int) -> dict:
    """The LibreLane configuration for one candidate."""
    rel = "dir::" + "../" * 3
    return {
        "DESIGN_NAME": top,
        "PDK": "ihp-sg13g2",
        "VERILOG_FILES": [rel + f for f in SOURCES[top]],
        "CLOCK_PORT": "clk_i",
        "CLOCK_PERIOD": period,
        # Real constraints for both place and route and signoff. Without these
        # LibreLane warns that it is falling back to a generic SDC, and the timing
        # numbers that come out of that are not worth quoting.
        "PNR_SDC_FILE": rel + "constraints/block.sdc",
        "SIGNOFF_SDC_FILE": rel + "constraints/block.sdc",
        "FP_SIZING": "relative",
        "FP_CORE_UTIL": util,
        "PL_TARGET_DENSITY_PCT": util + 5,
        # The repository lints with Verilator -Wall and zero warnings in `make lint`,
        # which is stricter than this step and already gates every push. Running a
        # second linter inside place and route would only add a different waiver list.
        "RUN_LINTER": False,
        # KLayout DRC and XOR are the two stages that dominate wall time if they run
        # single-threaded. The Makefile computes the default from nproc; this is the
        # value that reached the container.
        "KLAYOUT_DRC_THREADS": threads,
        "KLAYOUT_XOR_THREADS": threads,
        "DRT_THREADS": threads,
    }


def write_configs(tops: list[str], period: float, util: int, threads: int) -> None:
    for top in tops:
        path = CONFIG_DIR / top / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config_for(top, period, util, threads),
                                   indent=2) + "\n")
        print(f"wrote {path.relative_to(REPO)}")


def run_one(top: str, timeout_s: int) -> bool:
    """Run LibreLane on one candidate. Returns True if the flow finished."""
    config = CONFIG_DIR / top / "config.json"
    log = BUILD / f"{top}.log"
    pid_file = BUILD / f"{top}.pid"
    BUILD.mkdir(parents=True, exist_ok=True)

    cmd = ["librelane", "--run-tag", RUN_TAG, "--overwrite", "--condensed",
           "--hide-progress-bar", str(config.relative_to(REPO))]
    print(f"{top}: {' '.join(cmd)}", flush=True)
    start = time.time()
    with log.open("w") as handle:
        # Its own process group, so cleanup can signal exactly this run and nothing
        # else on a machine that has other flows on it.
        proc = subprocess.Popen(cmd, cwd=REPO, stdout=handle,
                                stderr=subprocess.STDOUT, start_new_session=True)
        pid_file.write_text(f"{proc.pid}\n")
        try:
            code = proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
            print(f"{top}: timed out after {timeout_s} s, see {log}", flush=True)
            return False
        except KeyboardInterrupt:
            os.killpg(proc.pid, signal.SIGTERM)
            raise
    pid_file.unlink(missing_ok=True)
    minutes = (time.time() - start) / 60.0
    if code != 0:
        print(f"{top}: LibreLane exited {code} after {minutes:.0f} min, see {log}",
              flush=True)
        return False
    print(f"{top}: finished in {minutes:.0f} min", flush=True)
    return True


def final_dir(top: str) -> pathlib.Path:
    return CONFIG_DIR / top / "runs" / RUN_TAG / "final"


def harvest(top: str) -> dict | None:
    """Read one finished run's metrics, and keep its GDS where the renderer looks."""
    metrics_path = final_dir(top) / "metrics.json"
    if not metrics_path.exists():
        return None
    metrics = json.loads(metrics_path.read_text())

    gds = next(iter(sorted((final_dir(top) / "gds").glob("*.gds"))), None)
    if gds is not None:
        BUILD.mkdir(parents=True, exist_ok=True)
        shutil.copy2(gds, BUILD / f"{top}.gds")

    slack = {c: metrics.get(f"timing__setup__ws__corner:{c}") for c in CORNERS}
    hold = {c: metrics.get(f"timing__hold__ws__corner:{c}") for c in CORNERS}
    r2r = {c: metrics.get(f"timing__setup_r2r__ws__corner:{c}") for c in CORNERS}
    # LibreLane does not put the constraint in metrics.json, so it comes from the
    # configuration the run was driven with, which is committed next to it.
    period = json.loads((CONFIG_DIR / top / "config.json").read_text())["CLOCK_PERIOD"]

    def fmax(ws: float | None) -> float | None:
        # Exact because constraints/block.sdc uses a fixed IO budget rather than a
        # fraction of the period: nothing in the constraint moves when the period does.
        return round(1000.0 / (period - ws), 2) if ws is not None else None

    entry = {
        "top": top,
        "clock_period_ns": period,
        "setup_slack_ns": slack,
        "hold_slack_ns": hold,
        "reg_to_reg_slack_ns": r2r,
        "signoff_corner": SIGNOFF_CORNER,
        "fmax_mhz": fmax(slack.get(SIGNOFF_CORNER)),
        "fmax_mhz_by_corner": {c: fmax(slack[c]) for c in CORNERS},
        "gds": str((BUILD / f"{top}.gds").relative_to(REPO)) if gds else None,
    }
    for key in HEADLINE:
        entry[key] = metrics.get(key)
    return entry


def write_summary(entries: dict) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": "tools/run_pnr.py",
        "flow": "LibreLane Classic, ihp-sg13g2",
        "signoff_corner": SIGNOFF_CORNER,
        "note": (
            "Post-route numbers from LibreLane's own metrics.json, not synthesis "
            "estimates. design__die__area is the die the candidate needs at the "
            "configured utilisation, and design__instance__area__stdcell is the "
            "design's own cells inside it, excluding the fill cells that exist only to "
            "meet density rules. fmax_mhz is 1/(period - worst setup slack) at the slow "
            "corner, from signoff STA on the routed netlist with parasitics extracted "
            "from the routing. Every candidate is routed at the identical clock "
            "constraint, so the area and power columns are comparable; closing each "
            "candidate at its own best period instead would report a higher frequency "
            "for each and an area measured under different optimisation pressure. "
            "power__total is OpenROAD's estimate at its default switching activity "
            "rather than annotated from a VCD, so treat it as an upper bound and read "
            "results/pdk/summary.json for power measured from real activity."
        ),
        "candidates": entries,
    }
    (RESULTS / "summary.json").write_text(json.dumps(payload, indent=2) + "\n")

    columns = ["top", "clock_period_ns", "fmax_mhz", "design__die__area",
               "design__instance__area__stdcell", "design__instance__utilization",
               "design__instance__count__stdcell", "route__wirelength", "power__total",
               "magic__drc_error__count", "klayout__drc_error__count",
               "design__lvs_error__count"]
    with (RESULTS / "summary.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns + [f"setup_slack_{c}" for c in CORNERS])
        for top, e in entries.items():
            writer.writerow([e.get(c) for c in columns]
                            + [e["setup_slack_ns"].get(c) for c in CORNERS])
    print(f"wrote {(RESULTS / 'summary.json').relative_to(REPO)} and summary.csv")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--tops", nargs="+", default=CANDIDATES)
    parser.add_argument("--period", type=float, default=DEFAULT_PERIOD_NS)
    parser.add_argument("--util", type=int, default=DEFAULT_UTIL)
    parser.add_argument("--threads", type=int,
                        default=int(os.environ.get("PNR_THREADS", "4")))
    parser.add_argument("--timeout-min", type=int, default=300)
    parser.add_argument("--configs-only", action="store_true")
    parser.add_argument("--harvest-only", action="store_true")
    args = parser.parse_args(argv)

    unknown = [t for t in args.tops if t not in SOURCES]
    if unknown:
        print(f"run_pnr: no source list for {', '.join(unknown)}", file=sys.stderr)
        return 1

    if not args.harvest_only:
        write_configs(args.tops, args.period, args.util, args.threads)
    if args.configs_only:
        return 0

    if not args.harvest_only:
        if shutil.which("librelane") is None:
            print("run_pnr: librelane is required", file=sys.stderr)
            return 1
        for top in args.tops:
            run_one(top, args.timeout_min * 60)

    entries, missing = {}, []
    for top in args.tops:
        entry = harvest(top)
        if entry is None:
            missing.append(top)
            continue
        entries[top] = entry
        print(f"  {top}: die {entry['design__die__area']} um2, "
              f"cells {entry['design__instance__area__stdcell']} um2, "
              f"{entry['fmax_mhz']} MHz at {SIGNOFF_CORNER}, "
              f"DRC {entry['magic__drc_error__count']}/"
              f"{entry['klayout__drc_error__count']}, "
              f"LVS {entry['design__lvs_error__count']}")
    if entries:
        write_summary(entries)
    if missing:
        print(f"no metrics for: {', '.join(missing)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
