#!/usr/bin/env python3
# Copyright 2026 Daniel Tyukov
# SPDX-License-Identifier: Apache-2.0
"""Render a GDS to docs/img/layout.png with KLayout.

Only useful after a real place and route run. If flow/out/gemm_bench_chip.gds does
not exist, nothing has been laid out and this script says so rather than producing
a picture that looks like a layout but is not one.

The estimate figure that stands in when there is no layout is
docs/img/floorplan_estimate.png, produced by tools/plot_floorplan.py and labelled
as an estimate on its face.
"""

from __future__ import annotations

import argparse
import pathlib
import shutil
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_GDS = REPO / "flow" / "out" / "gemm_bench_chip.gds"
OUT = REPO / "docs" / "img" / "layout.png"

# KLayout has no one-line "render this GDS" command, so drive it with a short script.
KLAYOUT_SCRIPT = """
gds = ARGV[0]
png = ARGV[1]
width = ARGV[2].to_i

layout = RBA::Layout.new
layout.read(gds)
top = layout.top_cell

view = RBA::LayoutView.new
view.load_layout(gds, 0)
view.max_hier_levels = 20
view.zoom_fit
view.save_image(png, width, (width * top.bbox.height.to_f / top.bbox.width).to_i)
puts "rendered #{gds} -> #{png}"
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("gds", nargs="?", type=pathlib.Path, default=DEFAULT_GDS)
    parser.add_argument("--out", type=pathlib.Path, default=OUT)
    parser.add_argument("--width", type=int, default=1600)
    args = parser.parse_args(argv)

    if not args.gds.exists():
        print(
            f"render_gds: {args.gds} does not exist.\n"
            f"No place and route has been run, so there is no layout to render. "
            f"Run `make flow`\nwith the IHP PDK and OpenROAD installed first. The "
            f"area estimate figure that stands in\nfor a layout is "
            f"docs/img/floorplan_estimate.png, and it is labelled as an estimate.",
            file=sys.stderr,
        )
        return 1

    if shutil.which("klayout") is None:
        print("render_gds: klayout is required", file=sys.stderr)
        return 1

    script = args.gds.parent / "render.rb"
    script.write_text(KLAYOUT_SCRIPT)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["klayout", "-z", "-r", str(script), str(args.gds), str(args.out),
         str(args.width)],
        capture_output=True, text=True,
    )
    print(result.stdout.strip() or result.stderr.strip())
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
