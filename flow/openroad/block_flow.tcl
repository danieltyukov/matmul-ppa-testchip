# Copyright 2026 Daniel Tyukov
# SPDX-License-Identifier: Apache-2.0
#
# Place and route for one candidate engine, as a standalone block.
#
# Separate from the chip-level sequence because a candidate has no pads, no memory
# macros and no test mode strap: it is a lump of combinational arithmetic with one
# register bank, which is exactly the thing worth pushing through a real flow to see
# whether the synthesis area estimate holds up.
#
# The whole sequence runs in one OpenROAD invocation rather than five, because there is
# no reason to round-trip a 30 000 cell block through ODB four times.
#
# Environment:
#   TOP              engine to route (required)
#   SG13G2_LIB       liberty
#   SG13G2_TECH_LEF  technology LEF
#   SG13G2_CELL_LEF  standard cell LEF
#   NETLIST          gate level netlist from make synth-pdk
#   FLOW_OUT         output directory

proc env_or_die {name} {
  if {![info exists ::env($name)] || $::env($name) eq ""} {
    puts stderr "block_flow: $name is not set"
    exit 1
  }
  return $::env($name)
}

proc env_or {name default} {
  if {[info exists ::env($name)] && $::env($name) ne ""} { return $::env($name) }
  return $default
}

set REPO     [file normalize [file join [file dirname [info script]] .. ..]]
set TOP      [env_or_die TOP]
set OUT      [env_or FLOW_OUT [file join $REPO flow out]]
set LIB      [env_or_die SG13G2_LIB]
set TECH_LEF [env_or_die SG13G2_TECH_LEF]
set CELL_LEF [env_or_die SG13G2_CELL_LEF]
set NETLIST  [env_or NETLIST [file join $REPO build synth ${TOP}_sg13g2_netlist.v]]

# A candidate is combinational arithmetic plus one register bank, so it can be packed
# harder than a chip with memory macros and a pad frame.
set UTILISATION [env_or BLOCK_UTILISATION 0.45]
set DENSITY     [env_or BLOCK_DENSITY 0.42]
set CLOCK_NS    [env_or BLOCK_CLOCK_NS 20.0]

file mkdir $OUT

read_lef $TECH_LEF
read_lef $CELL_LEF
read_liberty $LIB
read_verilog $NETLIST
link_design $TOP

# One clock, and the operand inputs are driven by the sequencer's tile registers, so
# they arrive shortly after the clock edge rather than asynchronously.
create_clock -name core_clk -period $CLOCK_NS [get_ports clk_i]
set_input_transition 0.30 [all_inputs]
set_load 0.03 [all_outputs]
set_false_path -from [get_ports rst_ni]

# ---------------------------------------------------------------------------
# Floorplan, sized from the cell area the netlist actually needs.
# ---------------------------------------------------------------------------
set cell_area [expr {[rsz::design_area] * 1e12}]
set core_area [expr {$cell_area / $UTILISATION}]
set side      [expr {sqrt($core_area)}]
set margin    12.0
set die       [expr {$side + 2 * $margin}]

puts [format "block_flow: %s cell area %.1f um2, core %.1f um square, die %.1f um square" \
      $TOP $cell_area $side $die]

initialize_floorplan \
  -die_area  [list 0 0 $die $die] \
  -core_area [list $margin $margin [expr {$margin + $side}] [expr {$margin + $side}]] \
  -site CoreSite

# SG13G2 has no dedicated tap cell: the standard cells carry their own well ties, so
# there is no insert_tapcells step here. That is a property of the library, not an
# omission.

place_pins -hor_layers Metal3 -ver_layers Metal4

# ---------------------------------------------------------------------------
# Power. A ring on the top metals plus followpin rails on Metal1.
# ---------------------------------------------------------------------------
add_global_connection -net VDD -pin_pattern {^VDD$} -power
add_global_connection -net VSS -pin_pattern {^VSS$} -ground
global_connect

define_pdn_grid -name block_grid -voltage_domains CORE
add_pdn_ring   -grid block_grid -layers {TopMetal1 TopMetal2} \
               -widths {3.0 3.0} -spacings {2.0 2.0} -core_offsets {3.0 3.0}
add_pdn_stripe -grid block_grid -layer Metal1 -width 0.44 -followpins
add_pdn_stripe -grid block_grid -layer TopMetal1 -width 2.0 -pitch 40 -offset 15
add_pdn_stripe -grid block_grid -layer TopMetal2 -width 2.0 -pitch 40 -offset 15
add_pdn_connect -grid block_grid -layers {Metal1 TopMetal1}
add_pdn_connect -grid block_grid -layers {TopMetal1 TopMetal2}
pdngen

write_def [file join $OUT ${TOP}_floorplan.def]

# ---------------------------------------------------------------------------
# Placement
# ---------------------------------------------------------------------------
set_wire_rc -signal -layer Metal3
set_wire_rc -clock  -layer Metal5

global_placement -density $DENSITY -pad_left 1 -pad_right 1

estimate_parasitics -placement
repair_design
detailed_placement
optimize_mirroring
check_placement -verbose

write_def [file join $OUT ${TOP}_place.def]

# ---------------------------------------------------------------------------
# Clock tree
# ---------------------------------------------------------------------------
clock_tree_synthesis \
  -root_buf sg13g2_buf_8 \
  -buf_list {sg13g2_buf_2 sg13g2_buf_4 sg13g2_buf_8 sg13g2_buf_16} \
  -sink_clustering_enable

set_propagated_clock [all_clocks]
estimate_parasitics -placement
repair_clock_nets
detailed_placement

repair_timing -setup
repair_timing -hold -hold_margin 0.05
detailed_placement
check_placement

write_def [file join $OUT ${TOP}_cts.def]

# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------
global_route -congestion_iterations 30

estimate_parasitics -global_routing
repair_timing -setup
repair_timing -hold -hold_margin 0.05

detailed_route \
  -output_drc [file join $OUT ${TOP}_route_drc.rpt] \
  -bottom_routing_layer Metal2 \
  -top_routing_layer Metal5 \
  -verbose 0

# ---------------------------------------------------------------------------
# Finish
# ---------------------------------------------------------------------------
filler_placement {sg13g2_fill_1 sg13g2_fill_2 sg13g2_fill_4 sg13g2_fill_8}
check_placement

set_propagated_clock [all_clocks]
estimate_parasitics -global_routing

puts "=== $TOP post-route reports ==="
report_design_area
report_worst_slack -max
report_worst_slack -min
report_tns
report_clock_skew
report_power

write_verilog [file join $OUT ${TOP}_final.v]
write_def     [file join $OUT ${TOP}_final.def]
write_db      [file join $OUT ${TOP}_final.odb]
write_gds     [file join $OUT ${TOP}.gds]

puts "block_flow: $TOP finished, output in $OUT"
