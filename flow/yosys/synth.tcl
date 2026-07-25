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

# Flatten for the leaf modules, so cell counts are directly comparable between
# candidates and the gate level netlist is self contained for the activity
# measurement. Keep hierarchy for the aggregate tops: flattening five 40 000 cell
# candidates into one module makes ABC's mapping time explode for no benefit, since
# the per-candidate numbers are already measured on their own.
set flatten       [envdef FLATTEN 1]

# How many latch cells this build is expected to contain. The only intentional latch
# in the design is inside clock_gate.sv, which is an integrated clock gate and is
# supposed to be one. With hierarchy preserved that is one latch cell in one module
# however many times the module is instantiated; flattened, it is one per instance.
# Any other latch is an inferred latch and a bug.
set expect_latches [envdef EXPECT_LATCHES 0]

# Whether to turn inferred memories into flip-flops.
#
# 0 (the default) leaves them as memory cells, which the statistics report
# separately as memories and memory bits. That is the honest picture: the four
# matrix stores are SRAM in any real build, and a report that counts 74 kbit of
# storage as flip-flops overstates the chip's logic by an order of magnitude.
#
# 1 maps them to flip-flops, which the standard cell flow has to do because this
# build binds no SRAM macros. It is also very slow on the memory bearing tops.
set map_memory [envdef MAP_MEMORY 0]

# Where the machine readable netlist JSON goes. It is a build artefact rather than a
# report: for the whole chip it is 60 MB of internal net names, which is not evidence
# of anything a reader can check. Keep it out of the committed results by default.
set json_dir [envdef JSON_DIR $out_dir]

if {$top eq ""} {
  log -stderr "synth.tcl: set TOP to the module to synthesise"
  exit 1
}

file mkdir $out_dir
file mkdir $json_dir

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
# Synthesis
# ---------------------------------------------------------------------------
if {$flatten == 1} {
  synth -top $top -flatten
} else {
  synth -top $top
}

opt -full
opt_clean -purge

# ---------------------------------------------------------------------------
# Structural checks.
#
# Latches: see EXPECT_LATCHES above. Everything else must be edge triggered.
#
# Blackboxes: the behavioural SRAM is the one intentional technology boundary in
# the design, and in this build it is inferred as memory and then mapped, never
# left as a blackbox, so 'check -assert' finding one would mean something failed
# to elaborate.
# ---------------------------------------------------------------------------
select -assert-count $expect_latches {t:$_DLATCH_*} {t:$_DLATCHSR_*} \
    {t:$dlatch} {t:$dlatchsr} {t:$sr}
log "check: exactly $expect_latches latch(es), all of them integrated clock gates"

# Multiple drivers, undriven inputs and similar structural problems.
check -assert

if {$mode eq "sg13g2"} {
  set lib [envdef SG13G2_LIB ""]
  if {$lib eq "" || ![file exists $lib]} {
    log -stderr "synth.tcl: MODE=sg13g2 needs SG13G2_LIB to point at the liberty file"
    log -stderr "           run tools/fetch_pdk.sh to download it"
    exit 1
  }

  if {$map_memory == 1} {
    # No SRAM macros are bound in this build, so memories become flip-flops. That
    # is honest but expensive: see the note in results/synth/sg13g2/summary.json.
    memory_map
    opt -full
  }

  dfflibmap -liberty $lib
  # -fast bounds ABC's effort. Without it the candidate built from inferred
  # multipliers takes tens of minutes on its own, and the whole point of this flow
  # is that every candidate goes through an identical script.
  abc -fast -liberty $lib -D $abc_clock_ps
  setundef -zero
  splitnets -ports
  opt_clean -purge

  tee -o $out_dir/${top}_sg13g2_stat.txt stat -liberty $lib -top $top
  tee -o $out_dir/${top}_sg13g2_ltp.txt ltp -noff
  write_json $json_dir/${top}_sg13g2.json
  if {$write_netlist == 1} {
    write_verilog -noattr $out_dir/${top}_sg13g2_netlist.v
  }
} else {
  # Unit-cost generic gates. No liberty, so no area in micrometres: the report is
  # cell counts and logic depth, plus gate equivalents computed by
  # tools/synth_collect.py from static CMOS transistor counts.
  if {$map_memory == 1} {
    memory_map
    opt -full
  }
  # A deliberately small gate set: fewer cell types map faster and keep the gate
  # equivalent model in tools/synth_collect.py simple enough to state exactly.
  # -fast bounds ABC's effort so every candidate is treated alike.
  abc -fast -g AND,NAND,OR,NOR,XOR,XNOR,ANDNOT,ORNOT,MUX
  setundef -zero
  opt_clean -purge

  tee -o $out_dir/${top}_generic_stat.txt stat -top $top
  tee -o $out_dir/${top}_generic_ltp.txt ltp -noff
  write_json $json_dir/${top}_generic.json
  if {$write_netlist == 1} {
    write_verilog -noattr $out_dir/${top}_generic_netlist.v
  }
}

log "synth.tcl: finished $top in mode $mode, reports in $out_dir"
