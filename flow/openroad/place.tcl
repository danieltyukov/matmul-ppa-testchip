# Copyright 2026 Daniel Tyukov
# SPDX-License-Identifier: Apache-2.0
#
# Global and detailed placement, with a repair pass in between.

source [file join [file dirname [info script]] common.tcl]
read_pdk
load_stage floorplan
read_constraints

# Buffer the ports so placement is not fighting an unbuffered top level net.
set_dont_use {sg13g2_dlygate4sd1_1 sg13g2_dlygate4sd2_1 sg13g2_dlygate4sd3_1}
buffer_ports

global_placement -density $PLACE_DENSITY -pad_left 2 -pad_right 2

estimate_parasitics -placement
repair_design
repair_tie_fanout -separation 10 sg13g2_tielo_1/L
repair_tie_fanout -separation 10 sg13g2_tiehi_1/H

detailed_placement
improve_placement
optimize_mirroring
check_placement -verbose

save_stage place
report_stage place
