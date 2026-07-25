# Copyright 2026 Daniel Tyukov
# SPDX-License-Identifier: Apache-2.0
#
# Floorplan: die and core area from the synthesised cell area and the utilisation
# target in constraints/area.sdc, then IO placement, power rings and tap cells.

source [file join [file dirname [info script]] common.tcl]

read_pdk
read_verilog [file join $OUT ${TOP}_sg13g2_netlist.v]
link_design $TOP
read_constraints

# Core size from the standard cell area actually needed, not a guessed number.
set cell_area [expr {[rsz::design_area] * 1e12}]   ;# square micrometres
set core_area [expr {$cell_area / $CORE_UTILISATION}]
set core_side [expr {sqrt($core_area * $CORE_ASPECT_RATIO)}]
set core_w    $core_side
set core_h    [expr {$core_area / $core_side}]

set die_w [expr {$core_w + 2 * ($CORE_MARGIN_UM + $PAD_RING_UM)}]
set die_h [expr {$core_h + 2 * ($CORE_MARGIN_UM + $PAD_RING_UM)}]
set core_x [expr {$CORE_MARGIN_UM + $PAD_RING_UM}]
set core_y $core_x

puts [format "floorplan: cell area %.1f um2, core %.1f x %.1f um, die %.1f x %.1f um" \
      $cell_area $core_w $core_h $die_w $die_h]

initialize_floorplan \
  -die_area  [list 0 0 $die_w $die_h] \
  -core_area [list $core_x $core_y [expr {$core_x + $core_w}] [expr {$core_y + $core_h}]] \
  -site CoreSite

# Tap cells keep the wells tied down; SG13G2 wants them every 37 um or so.
insert_tapcells -distance 37 -master sg13g2_fill_1

# IO placement. With a real IO LEF the pads go in the ring; without one the ports
# are placed on the die edge so routing still has somewhere to terminate.
if {$IO_LEF ne ""} {
  place_pads -row IO_NORTH {pad_clk_i pad_rst_ni pad_test_mode_i}
  place_pads -row IO_WEST  {pad_spi_sck_i pad_spi_cs_ni pad_spi_mosi_i pad_spi_miso_io}
  place_pads -row IO_SOUTH {pad_stat_busy_o pad_stat_done_o pad_stat_vfy_done_o \
                            pad_stat_mismatch_o}
  place_io_fill -row IO_NORTH -row IO_EAST -row IO_SOUTH -row IO_WEST
  connect_by_abutment
} else {
  puts "floorplan: no IO LEF, placing bare ports on the die edge"
  place_pins -hor_layers Metal3 -ver_layers Metal4
}

# Power: a ring around the core and straps on the top metals, which on SG13G2 are
# TopMetal1 and TopMetal2.
add_global_connection -net VDD -pin_pattern {^VDD$} -power
add_global_connection -net VSS -pin_pattern {^VSS$} -ground
global_connect

define_pdn_grid -name core_grid -voltage_domains CORE
add_pdn_ring   -grid core_grid -layers {TopMetal1 TopMetal2} \
               -widths {4.0 4.0} -spacings {2.0 2.0} -core_offsets {4.0 4.0}
add_pdn_stripe -grid core_grid -layer Metal1 -width 0.44 -followpins
add_pdn_stripe -grid core_grid -layer TopMetal1 -width 3.0 -pitch 60 -offset 20
add_pdn_stripe -grid core_grid -layer TopMetal2 -width 3.0 -pitch 60 -offset 20
add_pdn_connect -grid core_grid -layers {Metal1 TopMetal1}
add_pdn_connect -grid core_grid -layers {TopMetal1 TopMetal2}
pdngen

save_stage floorplan
report_stage floorplan
