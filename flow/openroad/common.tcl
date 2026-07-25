# Copyright 2026 Daniel Tyukov
# SPDX-License-Identifier: Apache-2.0
#
# Shared setup for every OpenROAD step: read the PDK views, set the units and the
# route layers, and provide load/save helpers so each step is independently
# restartable from the previous step's ODB.

proc env_or_die {name} {
  if {![info exists ::env($name)] || $::env($name) eq ""} {
    puts stderr "openroad: $name is not set; see flow/Makefile"
    exit 1
  }
  return $::env($name)
}

proc env_or {name default} {
  if {[info exists ::env($name)] && $::env($name) ne ""} { return $::env($name) }
  return $default
}

set REPO      [file normalize [file join [file dirname [info script]] .. ..]]
set OUT       [env_or FLOW_OUT [file join $REPO flow out]]
set TOP       [env_or TOP gemm_bench_chip]
set LIB       [env_or_die SG13G2_LIB]
set TECH_LEF  [env_or_die SG13G2_TECH_LEF]
set CELL_LEF  [env_or_die SG13G2_CELL_LEF]
set IO_LEF    [env_or SG13G2_IO_LEF ""]
set SRAM_LEF  [env_or SG13G2_SRAM_LEF ""]

file mkdir $OUT

# Area and utilisation targets.
source [file join $REPO constraints area.sdc]

proc read_pdk {} {
  global TECH_LEF CELL_LEF IO_LEF SRAM_LEF LIB
  read_lef $TECH_LEF
  read_lef $CELL_LEF
  if {$IO_LEF ne ""}   { read_lef $IO_LEF }
  if {$SRAM_LEF ne ""} { read_lef $SRAM_LEF }
  read_liberty $LIB
}

proc read_constraints {} {
  global REPO
  read_sdc [file join $REPO constraints clocks.sdc]
  read_sdc [file join $REPO constraints io.sdc]
}

proc load_stage {name} {
  global OUT TOP
  read_db [file join $OUT ${TOP}_${name}.odb]
}

proc save_stage {name} {
  global OUT TOP
  write_db [file join $OUT ${TOP}_${name}.odb]
  write_def [file join $OUT ${TOP}_${name}.def]
}

proc report_stage {name} {
  global OUT TOP
  set path [file join $OUT ${TOP}_${name}_report.txt]
  set fh [open $path w]
  puts $fh "stage: $name"
  close $fh
  report_design_area
  report_worst_slack -max
  report_worst_slack -min
  report_tns
  report_check_types -max_slew -max_capacitance -max_fanout -violators
}
