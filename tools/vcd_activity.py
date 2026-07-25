#!/usr/bin/env python3
# Copyright 2026 Daniel Tyukov
# SPDX-License-Identifier: Apache-2.0
"""Switching-activity extraction from a VCD, used as a technology-independent
dynamic-power proxy.

What this measures
------------------
For every net in the dump, the number of bit transitions over the observation
window. A scalar net that goes 0 -> 1 counts one transition. A vector net that
changes from 0x0F to 0x11 counts the Hamming distance between the two values,
which is three, because three of its bits flipped. Transitions are then summed per
module scope so the result can be attributed to a candidate, a submodule or the
whole design.

Why Hamming distance and not "one change per net"
-------------------------------------------------
Dynamic power in CMOS is roughly

    P_dyn = alpha * C_load * V**2 * f

summed over nodes, where alpha is the switching probability of that node. The
number of bit transitions is a direct estimate of the sum of alpha over the nodes
that the RTL makes visible, with every node weighted equally. Counting a 32-bit
bus change as one event instead of as its Hamming distance would under-weight wide
datapaths by up to a factor of the bus width, which is exactly the effect this
chip exists to measure.

What this does NOT measure
--------------------------
It is a proxy, not power.

  - Every node is weighted equally. Real capacitance varies by more than an order
    of magnitude between a short local wire and a clock spine.
  - It counts RTL nets. Post-synthesis a net may vanish, be duplicated across
    fanout buffers, or be replaced by cells with entirely different internal
    activity. Glitching, which can be a large share of the dynamic power of a
    combinational multiplier array, is only partly visible at RTL and not at all
    in a cycle-based dump.
  - Leakage is not modelled at all.
  - It says nothing about clock tree or memory array power.

Read the numbers as a relative ranking of designs under an identical workload,
which is what it is good for, and see docs/PPA_METHODOLOGY.md for the full
argument. Anything stated as measured power in this repo would be a lie.

Determinism
-----------
The same VCD always produces byte-identical output: scopes are reported in sorted
order and nothing depends on dictionary iteration order or wall-clock time.
tools/activity_sweep.py asserts that by running the parse twice.

Usage
-----
    vcd_activity.py dump.vcd --json out.json [--start-time N] [--scope PREFIX]
    vcd_activity.py dump.vcd --top-nets 20
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from dataclasses import dataclass, field


@dataclass
class Net:
    """One VCD variable: its full hierarchical name and its transition count."""

    scope: str
    name: str
    width: int
    transitions: int = 0
    value: str | None = None

    @property
    def full_name(self) -> str:
        return f"{self.scope}.{self.name}" if self.scope else self.name


@dataclass
class ActivityReport:
    nets: dict[str, Net] = field(default_factory=dict)
    total_transitions: int = 0
    first_time: int | None = None
    last_time: int | None = None
    timescale: str = ""
    start_time: int = 0
    value_changes: int = 0

    def per_scope(self) -> dict[str, int]:
        """Transitions attributed to each scope, exclusive of child scopes."""
        out: dict[str, int] = {}
        for net in self.nets.values():
            out[net.scope] = out.get(net.scope, 0) + net.transitions
        return dict(sorted(out.items()))

    def per_scope_inclusive(self) -> dict[str, int]:
        """Transitions attributed to each scope, including everything below it."""
        exclusive = self.per_scope()
        out: dict[str, int] = {}
        for scope, count in exclusive.items():
            parts = scope.split(".") if scope else []
            for depth in range(len(parts) + 1):
                prefix = ".".join(parts[:depth])
                out[prefix] = out.get(prefix, 0) + count
        return dict(sorted(out.items()))

    def subtree_total(self, prefix: str) -> int:
        """Transitions in one scope and everything under it."""
        return sum(
            net.transitions
            for net in self.nets.values()
            if net.scope == prefix or net.scope.startswith(prefix + ".")
        )

    def top_nets(self, count: int) -> list[tuple[str, int, int]]:
        ranked = sorted(
            ((n.full_name, n.transitions, n.width) for n in self.nets.values()),
            key=lambda item: (-item[1], item[0]),
        )
        return ranked[:count]


_VAR_RE = re.compile(
    r"\$var\s+(?P<kind>\S+)\s+(?P<width>\d+)\s+(?P<ident>\S+)\s+(?P<name>.+?)\s*\$end"
)
_SCOPE_RE = re.compile(r"\$scope\s+(?P<kind>\S+)\s+(?P<name>\S+)\s*\$end")
_TIMESCALE_RE = re.compile(r"\$timescale\s+(?P<ts>.+?)\s*\$end", re.S)


_CLEAN = {"0", "1"}


def _hamming(old: str, new: str, width: int) -> int:
    """Bit transitions between two VCD binary strings, padded to `width`.

    VCD omits leading zeros in vector values, so both sides are left-padded. An x or z
    on either side of a bit position counts as one transition if the two characters
    differ, which treats entering and leaving an unknown state as activity. That
    matches what happens in silicon: a node driven to an indeterminate level has moved.

    The common case, two fully defined values, is done with an integer XOR and a
    population count. That is roughly an order of magnitude faster than comparing
    characters, and this function runs once per value change on every net in a dump
    that can hold tens of millions of them.
    """
    if old == new:
        return 0
    if _CLEAN.issuperset(old) and _CLEAN.issuperset(new):
        return (int(old, 2) ^ int(new, 2)).bit_count()
    old_p = old.rjust(width, old[0] if old and old[0] in "xzXZ" else "0")
    new_p = new.rjust(width, new[0] if new and new[0] in "xzXZ" else "0")
    return sum(1 for a, b in zip(old_p, new_p) if a != b)


def parse_vcd(
    path: pathlib.Path,
    start_time: int = 0,
    scope_filter: str | None = None,
) -> ActivityReport:
    """Count bit transitions per net in a VCD.

    start_time skips a settling or reset window: value changes at or before it set
    the initial value of a net without counting as transitions. scope_filter keeps
    only nets whose scope starts with the given dotted prefix, which makes a
    focused parse of one candidate much faster than parsing everything.
    """
    report = ActivityReport(start_time=start_time)
    ident_to_nets: dict[str, list[Net]] = {}
    scope_stack: list[str] = []
    in_header = True
    time_now = 0
    counting = start_time <= 0

    with path.open("r", errors="replace") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue

            if in_header:
                if line.startswith("$scope"):
                    match = _SCOPE_RE.match(line)
                    if match:
                        scope_stack.append(match.group("name"))
                    continue
                if line.startswith("$upscope"):
                    if scope_stack:
                        scope_stack.pop()
                    continue
                if line.startswith("$var"):
                    match = _VAR_RE.match(line)
                    if match:
                        scope = ".".join(scope_stack)
                        name = match.group("name").strip()
                        if scope_filter is None or scope.startswith(scope_filter):
                            net = Net(
                                scope=scope,
                                name=name,
                                width=int(match.group("width")),
                            )
                            key = net.full_name
                            # A net can be dumped under several identifiers (an
                            # aliased port, for example). Keep one entry per name
                            # and let every identifier drive it.
                            if key not in report.nets:
                                report.nets[key] = net
                            ident_to_nets.setdefault(match.group("ident"), []).append(
                                report.nets[key]
                            )
                    continue
                if line.startswith("$timescale"):
                    match = _TIMESCALE_RE.match(line)
                    if match:
                        report.timescale = " ".join(match.group("ts").split())
                    continue
                if line.startswith("$enddefinitions"):
                    in_header = False
                    continue
                continue

            if line.startswith("#"):
                try:
                    time_now = int(line[1:])
                except ValueError:
                    continue
                if report.first_time is None:
                    report.first_time = time_now
                report.last_time = time_now
                counting = time_now > start_time
                continue

            if line[0] in "01xXzZ" and len(line) >= 2:
                # Scalar change: one character of value then the identifier.
                value = line[0].lower()
                ident = line[1:].strip()
                _apply(ident_to_nets, ident, value, counting, report)
                continue

            if line[0] in "bB":
                parts = line.split()
                if len(parts) >= 2:
                    _apply(ident_to_nets, parts[1], parts[0][1:].lower(), counting, report)
                continue

            if line[0] in "rR":
                # Real valued nets have no bit level meaning; count any change as one.
                parts = line.split()
                if len(parts) >= 2:
                    _apply(ident_to_nets, parts[1], parts[0][1:], counting, report,
                           real=True)
                continue

    report.total_transitions = sum(net.transitions for net in report.nets.values())
    return report


def _apply(
    ident_to_nets: dict[str, list[Net]],
    ident: str,
    value: str,
    counting: bool,
    report: ActivityReport,
    real: bool = False,
) -> None:
    nets = ident_to_nets.get(ident)
    if not nets:
        return
    report.value_changes += 1
    for net in nets:
        if net.value is not None and counting:
            if real:
                net.transitions += 0 if net.value == value else 1
            else:
                net.transitions += _hamming(net.value, value, net.width)
        net.value = value


def report_to_dict(report: ActivityReport, source: str) -> dict:
    return {
        "source": source,
        "measure": "bit transitions (Hamming distance per value change)",
        "caveat": (
            "This is a technology-independent switching-activity proxy for dynamic "
            "power, not a power measurement. Every net is weighted equally, RTL "
            "nets are not post-synthesis nets, glitching is only partly visible, "
            "and leakage is not modelled. See docs/PPA_METHODOLOGY.md."
        ),
        "timescale": report.timescale,
        "window": {
            "start_time": report.start_time,
            "first_time": report.first_time,
            "last_time": report.last_time,
        },
        "nets": len(report.nets),
        "value_changes": report.value_changes,
        "total_transitions": report.total_transitions,
        "per_scope_exclusive": report.per_scope(),
        "per_scope_inclusive": report.per_scope_inclusive(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Count bit transitions per net and per module scope in a VCD",
    )
    parser.add_argument("vcd", type=pathlib.Path)
    parser.add_argument("--json", type=pathlib.Path,
                        help="write the full report as JSON")
    parser.add_argument("--start-time", type=int, default=0,
                        help="ignore transitions at or before this VCD time, to skip reset")
    parser.add_argument("--scope", default=None,
                        help="only parse nets whose scope starts with this dotted prefix")
    parser.add_argument("--top-nets", type=int, default=0,
                        help="print the N busiest nets")
    parser.add_argument("--subtree", action="append", default=[],
                        help="print the inclusive total for this scope prefix; repeatable")
    args = parser.parse_args(argv)

    if not args.vcd.exists():
        print(f"vcd_activity: {args.vcd} does not exist", file=sys.stderr)
        return 1

    report = parse_vcd(args.vcd, start_time=args.start_time, scope_filter=args.scope)

    print(f"file                 {args.vcd}")
    print(f"timescale            {report.timescale}")
    print(f"time window          {report.first_time} .. {report.last_time} "
          f"(counting after {report.start_time})")
    print(f"nets                 {len(report.nets)}")
    print(f"value changes        {report.value_changes}")
    print(f"total transitions    {report.total_transitions}")

    for prefix in args.subtree:
        print(f"subtree {prefix:<28} {report.subtree_total(prefix)}")

    if args.top_nets:
        print()
        print(f"{'net':<70} {'width':>5} {'transitions':>12}")
        for name, transitions, width in report.top_nets(args.top_nets):
            print(f"{name:<70} {width:>5} {transitions:>12}")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(report_to_dict(report, str(args.vcd)), indent=2) + "\n"
        )
        print(f"\nwrote {args.json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
