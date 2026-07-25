# Copyright 2026 Daniel Tyukov
# SPDX-License-Identifier: Apache-2.0
#
# Clock tree synthesis. One clock, plus the gated branches the integrated clock
# gates in engine_array produce, so the tree has five gated leaves under a common
# root and CTS has to balance across the gates.

source [file join [file dirname [info script]] common.tcl]
read_pdk
load_stage place
read_constraints

set_wire_rc -clock -layer Metal5
set_wire_rc -signal -layer Metal3

clock_tree_synthesis \
  -root_buf sg13g2_buf_8 \
  -buf_list {sg13g2_buf_2 sg13g2_buf_4 sg13g2_buf_8 sg13g2_buf_16} \
  -sink_clustering_enable \
  -balance_levels

set_propagated_clock [all_clocks]
estimate_parasitics -placement
repair_clock_nets
detailed_placement

repair_timing -setup
repair_timing -hold -hold_margin 0.05
detailed_placement
check_placement

save_stage cts
report_stage cts
