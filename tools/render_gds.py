#!/usr/bin/env python3
# Copyright 2026 Daniel Tyukov
# SPDX-License-Identifier: Apache-2.0
"""Render routed GDS to PNG with KLayout, and build the per-candidate contact sheets.

Only useful after a real place and route run: `tools/run_pnr.py` leaves one GDS per
candidate in build/pnr/. If a GDS is missing this script says so rather than producing
a picture that looks like a layout but is not one.

Three things come out:

  layout_<top>.png              one candidate's whole die
  layout_contact_sheet.png      every candidate's die at one scale, so the sizes are
                                directly comparable on the page
  layout_zoom_contact_sheet.png the same physical window of silicon in each candidate,
                                which is where the microarchitectures look different

The contact sheets are the point. Five functionally identical engines, laid out by the
same flow with the same constraint, look nothing like each other: a Wallace tree is a
dense uniform mat, a Booth radix-4 engine is visibly smaller, and the bit-serial engine
is a fraction of the area with a wide sparse channel through the middle.

KLayout batch mode takes no positional arguments, only `-rd name=value` pairs, which
arrive in the script as globals. That is why the render script is generated and driven
rather than being a committed .py file with an argument parser.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
BUILD = REPO / "build" / "render"
PNR = REPO / "build" / "pnr"
IMG = REPO / "docs" / "img"
RESULTS = REPO / "results" / "pnr"

PDK = pathlib.Path.home() / ".local/share/pdk/IHP-Open-PDK/ihp-sg13g2"
LAYER_PROPS = PDK / "libs.tech/klayout/tech/sg13g2.lyp"

ORDER = ["engine_infer", "engine_wallace", "engine_booth4", "engine_signmag",
         "engine_bitserial", "engine_array"]

LABELS = {
    "engine_infer": "infer (synthesiser's choice)",
    "engine_wallace": "wallace (3:2 CSA tree)",
    "engine_booth4": "booth4 (radix-4 Booth)",
    "engine_signmag": "signmag (sign-magnitude)",
    "engine_bitserial": "bitserial (8 cycles/tile)",
    "engine_array": "engine_array (all five plus gating)",
}

# The window for the zoom crop, in micrometres. Large enough to hold a few hundred
# cells and their local routing, small enough that individual cells are visible.
ZOOM_UM = 60.0

RENDER = """
import pya

lv = pya.LayoutView()
lv.load_layout(gds, 0)
lv.max_hier()
try:
    if lyp:
        lv.load_layer_props(lyp)
except NameError:
    pass

cv = lv.active_cellview()
box = cv.layout().top_cell().dbbox()

if mode == "box":
    lv.zoom_box(pya.DBox(float(x0), float(y0), float(x1), float(y1)))
else:
    lv.zoom_fit()

lv.save_image(out, int(w), int(h))
print("BBOX %.4f %.4f %.4f %.4f" % (box.left, box.bottom, box.right, box.top))
"""


def klayout(gds: pathlib.Path, out: pathlib.Path, width: int, height: int,
            box: tuple[float, float, float, float] | None = None) -> tuple:
    """Render one image. Returns the layout bounding box in micrometres."""
    BUILD.mkdir(parents=True, exist_ok=True)
    script = BUILD / "render.py"
    script.write_text(RENDER)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["klayout", "-b", "-rm", str(script),
           "-rd", f"gds={gds}", "-rd", f"out={out}",
           "-rd", f"w={width}", "-rd", f"h={height}",
           "-rd", f"mode={'box' if box else 'fit'}"]
    if LAYER_PROPS.exists():
        cmd += ["-rd", f"lyp={LAYER_PROPS}"]
    if box:
        cmd += ["-rd", f"x0={box[0]}", "-rd", f"y0={box[1]}",
                "-rd", f"x1={box[2]}", "-rd", f"y1={box[3]}"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not out.exists():
        raise RuntimeError(f"klayout failed on {gds}:\n{result.stdout}\n{result.stderr}")
    line = next((ln for ln in result.stdout.splitlines() if ln.startswith("BBOX")), None)
    if line is None:
        raise RuntimeError(f"klayout printed no bounding box for {gds}")
    return tuple(float(v) for v in line.split()[1:])


def gds_for(top: str) -> pathlib.Path:
    return PNR / f"{top}.gds"


def available(tops: list[str]) -> list[str]:
    return [t for t in tops if gds_for(t).exists()]


def metrics() -> dict:
    path = RESULTS / "summary.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text()).get("candidates", {})


def render_single(top: str, px_per_um: float) -> dict:
    """One candidate: whole die, and a zoom crop from the middle of the core."""
    gds = gds_for(top)
    x0, y0, x1, y1 = klayout(gds, IMG / f"layout_{top}.png", 2000, 2000)
    span_x, span_y = x1 - x0, y1 - y0

    die = BUILD / f"{top}_die.png"
    klayout(gds, die, max(int(span_x * px_per_um), 8), max(int(span_y * px_per_um), 8))

    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    half = ZOOM_UM / 2.0
    zoom = BUILD / f"{top}_zoom.png"
    klayout(gds, zoom, 1100, 1100,
            box=(cx - half, cy - half, cx + half, cy + half))
    print(f"  {top}: {span_x:.0f} x {span_y:.0f} um die")
    return {"top": top, "die": die, "zoom": zoom,
            "span_x": span_x, "span_y": span_y}


def contact_sheet(cards: list[dict], out: pathlib.Path, kind: str,
                  info: dict) -> None:
    """Compose the per-candidate renders into one figure at a single scale."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.image as mpimg
    import matplotlib.pyplot as plt

    columns = min(len(cards), 3)
    rows = (len(cards) + columns - 1) // columns
    slot = max(max(c["span_x"], c["span_y"]) for c in cards)

    # One figure inch per 150 um of silicon, plus room for the captions.
    scale = 3.4 / slot
    cell_w, cell_h = 3.4, 3.4 + 0.62
    fig = plt.figure(figsize=(columns * cell_w, rows * cell_h + 0.5))

    for index, card in enumerate(cards):
        row, column = divmod(index, columns)
        if kind == "die":
            w, h = card["span_x"] * scale, card["span_y"] * scale
            image = card["die"]
        else:
            w = h = ZOOM_UM * (3.4 / ZOOM_UM)
            image = card["zoom"]
        left = (column * cell_w + (cell_w - w) / 2.0) / (columns * cell_w)
        bottom = 1.0 - ((row + 1) * cell_h - (cell_h - 0.62 - h) / 2.0) / (
            rows * cell_h + 0.5)
        ax = fig.add_axes([left, bottom, w / (columns * cell_w),
                           h / (rows * cell_h + 0.5)])
        ax.imshow(mpimg.imread(str(image)))
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_color("#8a949c")
            spine.set_linewidth(0.7)

        entry = info.get(card["top"], {})
        title = LABELS.get(card["top"], card["top"])
        if kind == "die":
            detail = (f"{card['span_x']:.0f} x {card['span_y']:.0f} um"
                      f"  |  {entry.get('design__instance__count', '?')} instances")
            if entry.get("fmax_mhz"):
                detail += f"  |  {entry['fmax_mhz']:.0f} MHz"
        else:
            detail = f"{ZOOM_UM:.0f} x {ZOOM_UM:.0f} um window, core centre"
        ax.set_title(f"{title}\n{detail}", fontsize=9.5, pad=5)

    if kind == "die":
        caption = (
            "Routed GDS from LibreLane on IHP SG13G2, every candidate at the same scale "
            "and the same 20 ns constraint. Die area is what the\ncandidate needs at 40 "
            "percent core utilisation, including routing, filler and the power grid, "
            "which standard cell area excludes."
        )
    else:
        caption = (
            f"The same {ZOOM_UM:.0f} micrometre square of silicon in each candidate, at "
            f"the same magnification, taken from the middle of the core.\nSame function, "
            f"same flow, same constraint: what differs is the microarchitecture."
        )
    fig.text(0.012, 0.008, caption, fontsize=9, color="#5a6672", va="bottom")
    fig.savefig(out, dpi=150, facecolor="white")
    plt.close(fig)
    print(f"wrote {out.relative_to(REPO)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--tops", nargs="+", default=ORDER)
    parser.add_argument("--px-per-um", type=float, default=2.2)
    args = parser.parse_args(argv)

    if shutil.which("klayout") is None:
        print("render_gds: klayout is required", file=sys.stderr)
        return 1

    tops = available(args.tops)
    if not tops:
        print(
            "render_gds: no routed GDS under build/pnr.\n"
            "Run `tools/run_pnr.py` with the IHP PDK and LibreLane installed first. "
            "Nothing here\nwill draw a layout that has not been produced.",
            file=sys.stderr,
        )
        return 1
    missing = [t for t in args.tops if t not in tops]
    if missing:
        print(f"render_gds: no GDS for {', '.join(missing)}, skipping those")

    info = metrics()
    cards = [render_single(top, args.px_per_um) for top in tops]
    contact_sheet(cards, IMG / "layout_contact_sheet.png", "die", info)
    contact_sheet(cards, IMG / "layout_zoom_contact_sheet.png", "zoom", info)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
