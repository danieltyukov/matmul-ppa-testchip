# Copyright 2026 Daniel Tyukov
# SPDX-License-Identifier: Apache-2.0
#
# Yosys synthesis for one module, in either of two modes.
#
#   generic   map to unit-delay generic gates and report cell counts, gate
#             equivalents and logic depth. Needs no PDK, so it runs anywhere
#             including CI, and is what the committed generic numbers come from.
#
#   sg13g2    map to the IHP SG13G2 standard cell library and report real cell
#             area in square micrometres plus a static timing estimate. Needs
#             SG13G2_LIB to point at sg13g2_stdcell_typ_1p20V_25C.lib; fetch it
#             with tools/fetch_pdk.sh.
#
# Environment:
#   TOP          module to synthesise (required)
#   MODE         generic | sg13g2      (default generic)
#   SG13G2_LIB   path to the liberty file, required for MODE=sg13g2
#   OUT_DIR      where reports and netlists go (default build/synth)
#   FILELIST     source list (default rtl/filelist.f)
#   WRITE_NETLIST  1 to emit a gate level Verilog netlist (default 0)
#   ABC_CLOCK_PS clock period in picoseconds for the sg13g2 timing target
#                (default 20000, that is 50 MHz)

yosys -import

# Yosys 0.33's Tcl mode has no getenv command, so read the environment directly.
proc envdef {name default} {
  if {[info exists ::env($name)]} {
    set value [string trim $::env($name)]
    if {$value ne ""} { return $value }
  }
  return $default
}

set top      [envdef TOP ""]
set mode     [envdef MODE generic]
set out_dir  [envdef OUT_DIR build/synth]
set filelist [envdef FILELIST rtl/filelist.f]
set write_netlist [envdef WRITE_NETLIST 0]
set abc_clock_ps  [envdef ABC_CLOCK_PS 20000]

if {$top eq ""} {
  log -stderr "synth.tcl: set TOP to the module to synthesise"
  exit 1
}

file mkdir $out_dir

# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------
set fh [open $filelist r]
set sources {}
while {[gets $fh line] >= 0} {
  set line [string trim $line]
  if {$line eq "" || [string index $line 0] eq "#"} { continue }
  lappend sources $line
}
close $fh

# The engine harness is verification-only, but the activity flow synthesises
# individual engines, so extra sources can be appended through EXTRA_SOURCES.
foreach extra [split [envdef EXTRA_SOURCES ""] " "] {
  if {$extra ne ""} { lappend sources $extra }
}

foreach src $sources {
  read_verilog -sv $src
}

hierarchy -check -top $top

# ---------------------------------------------------------------------------
# Generic synthesis
# ---------------------------------------------------------------------------
# -flatten gives one flat module, which makes the cell counts directly
# comparable between candidates and makes the gate level netlist self contained
# for the activity measurement.
synth -top $top -flatten

opt -full
opt_clean -purge

# ---------------------------------------------------------------------------
# No inferred latches, and no unexpected blackboxes.
#
# The behavioural SRAM is the one intentional technology boundary in the design.
# In this generic build it is inferred as $mem_v2 (or as registers when the tool
# decides that is cheaper), never as a blackbox, so a blackbox here means
# something failed to elaborate.
# ---------------------------------------------------------------------------
select -assert-none {t:$_DLATCH_*} {t:$_DLATCHSR_*} {t:$dlatch} {t:$dlatchsr} {t:$sr}
log "check: no inferred latches"

# Multiple drivers, undriven inputs and similar structural problems.
check -assert

if {$mode eq "sg13g2"} {
  set lib [envdef SG13G2_LIB ""]
  if {$lib eq "" || ![file exists $lib]} {
    log -stderr "synth.tcl: MODE=sg13g2 needs SG13G2_LIB to point at the liberty file"
    log -stderr "           run tools/fetch_pdk.sh to download it"
    exit 1
  }

  # Memories have to become registers before standard cell mapping, because this
  # build has no SRAM macros to bind. That is honest but expensive in area, and
  # the report says so.
  memory_map
  opt -full

  dfflibmap -liberty $lib
  abc -liberty $lib -D $abc_clock_ps
  setundef -zero
  splitnets -ports
  opt_clean -purge

  tee -o $out_dir/${top}_sg13g2_stat.txt stat -liberty $lib
  tee -o $out_dir/${top}_sg13g2_ltp.txt ltp -noff
  write_json $out_dir/${top}_sg13g2.json
  if {$write_netlist == 1} {
    write_verilog -noattr $out_dir/${top}_sg13g2_netlist.v
  }
} else {
  # Unit-cost generic gates. No liberty, so no area in micrometres: the report is
  # cell counts and logic depth, plus gate equivalents computed by
  # tools/synth_collect.py from static CMOS transistor counts.
  memory_map
  opt -full
  abc -g AND,NAND,OR,NOR,XOR,XNOR,ANDNOT,ORNOT,MUX,NMUX,AOI3,OAI3,AOI4,OAI4
  setundef -zero
  opt_clean -purge

  tee -o $out_dir/${top}_generic_stat.txt stat
  tee -o $out_dir/${top}_generic_ltp.txt ltp -noff
  write_json $out_dir/${top}_generic.json
  if {$write_netlist == 1} {
    write_verilog -noattr $out_dir/${top}_generic_netlist.v
  }
}

log "synth.tcl: finished $top in mode $mode, reports in $out_dir"
