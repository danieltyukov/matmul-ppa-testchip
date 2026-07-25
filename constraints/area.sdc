# Copyright 2026 Daniel Tyukov
# SPDX-License-Identifier: Apache-2.0
#
# Area and utilisation targets for the OpenROAD floorplan.
#
# These are the numbers flow/openroad/floorplan.tcl reads. They are a starting
# point derived from the committed synthesis results, not a measured die size:
# nothing here has been through place and route. See docs/PPA_METHODOLOGY.md.
#
# Sizing argument, all from results/synth/sg13g2/summary.json when the PDK liberty
# is available:
#
#   The five candidates plus shared logic dominate the standard cell area. At 55
#   percent core utilisation, which is a reasonable target for a design with this
#   much local interconnect in a multiplier array, the core area is the cell area
#   divided by 0.55. The die adds a pad frame ring.
#
# Override any of these from the environment when driving the flow.

set CORE_UTILISATION   0.55
set CORE_ASPECT_RATIO  1.0

# Channel between the core area and the pad frame, in micrometres. Wide enough for
# the power ring and the pad signal routing.
set CORE_MARGIN_UM     60.0

# Pad frame ring width, in micrometres. IHP SG13G2 IO cells are roughly 80 um tall
# for the 16 mA drivers this design would use.
set PAD_RING_UM        90.0

# Placement density for global placement. Lower than utilisation on purpose: the
# multiplier arrays route densely and a tight placement fails to route.
set PLACE_DENSITY      0.50

# Metal layers available for signal routing on SG13G2: Metal1 through Metal5, with
# TopMetal1 and TopMetal2 reserved for power.
set ROUTE_LAYER_MIN    Metal2
set ROUTE_LAYER_MAX    Metal5
