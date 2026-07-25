# Copyright 2026 Daniel Tyukov
# SPDX-License-Identifier: Apache-2.0
#
# Global and detailed routing.

source [file join [file dirname [info script]] common.tcl]
read_pdk
load_stage cts
read_constraints

set_thermal_derate -cell 0.0

global_route \
  -guide_file [file join $OUT ${TOP}_route.guide] \
  -congestion_iterations 40 \
  -verbose

estimate_parasitics -global_routing
repair_timing -setup -skip_pin_swap
repair_timing -hold -hold_margin 0.05

detailed_route \
  -output_drc [file join $OUT ${TOP}_route_drc.rpt] \
  -output_maze [file join $OUT ${TOP}_route_maze.log] \
  -bottom_routing_layer $ROUTE_LAYER_MIN \
  -top_routing_layer $ROUTE_LAYER_MAX \
  -verbose 1

save_stage route
report_stage route
