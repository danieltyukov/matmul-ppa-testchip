# Copyright 2026 Daniel Tyukov
# SPDX-License-Identifier: Apache-2.0
#
# Filler insertion, final timing and power reports, and stream out.
#
# The power report here is the only place in this repository that produces an
# actual power number, and it needs both the PDK and a switching activity file.
# tools/vcd_activity.py provides the activity proxy that stands in for it when the
# PDK is unavailable; the two are not interchangeable and must not be conflated.

source [file join [file dirname [info script]] common.tcl]
read_pdk
load_stage route
read_constraints

filler_placement {sg13g2_fill_1 sg13g2_fill_2 sg13g2_fill_4 sg13g2_fill_8}
check_placement

set_propagated_clock [all_clocks]
estimate_parasitics -global_routing

report_design_area
report_checks -path_delay min_max -format full_clock_expanded \
  -fields {slew cap input nets fanout}
report_worst_slack -max
report_worst_slack -min
report_tns
report_clock_skew
report_check_types -max_slew -max_capacitance -max_fanout -violators

# Power. Without an activity file this reports default toggle rates, which is not a
# measurement of anything; feed it a VCD from the same workload the activity proxy
# uses so the two are comparable.
if {[info exists ::env(POWER_VCD)] && $::env(POWER_VCD) ne ""} {
  read_power_activities -vcd $::env(POWER_VCD)
  puts "finish: power reported from $::env(POWER_VCD)"
} else {
  puts "finish: no POWER_VCD set, power below uses default toggle rates and is not"
  puts "        a measurement. Set POWER_VCD to a dump of the benchmark workload."
}
report_power

write_verilog [file join $OUT ${TOP}_final.v]
write_def     [file join $OUT ${TOP}_final.def]
write_db      [file join $OUT ${TOP}_final.odb]
write_gds     [file join $OUT ${TOP}.gds]

save_stage final
