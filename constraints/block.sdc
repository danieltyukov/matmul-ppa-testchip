# Copyright 2026 Daniel Tyukov
# SPDX-License-Identifier: Apache-2.0
#
# Block level constraints for one candidate engine, used by LibreLane for both
# place and route and signoff timing (PNR_SDC_FILE and SIGNOFF_SDC_FILE).
#
# Without this file LibreLane falls back to its generic SDC, warns that it is doing
# so, and constrains a design it knows nothing about: no false path on the reset, no
# fanout limit, and IO delays taken from a default. A timing number produced that way
# is not worth quoting, which is why this file exists.
#
# Every candidate in rtl/engines has the identical port list, documented in
# docs/ADDING_A_CANDIDATE.md, so one constraint file covers all of them.

set clk_port [lindex $::env(CLOCK_PORT) 0]
set clk_name core_clk
set period   $::env(CLOCK_PERIOD)

create_clock -name $clk_name -period $period [get_ports $clk_port]

# Jitter plus the clock tree skew that is not built yet at the point placement reads
# this file. Signoff reads it again with a real tree, where the uncertainty is margin.
set_clock_uncertainty 0.30 [get_clocks $clk_name]
set_clock_transition  0.20 [get_clocks $clk_name]
set_propagated_clock  [all_clocks]

# rst_ni is asserted asynchronously and released synchronously by the reset bridge one
# level up (rtl/lib/reset_bridge.sv), so it has no setup requirement here.
set_false_path -from [get_ports rst_ni]

# The operands and the control strobes are driven by the sequencer's tile registers in
# this same clock domain, so they arrive a little way into the cycle rather than at the
# edge, and the result is captured by registers one level up. One nanosecond each side
# covers the launching flip-flop's clock-to-Q and the wire to the block boundary at this
# process.
#
# Fixed nanoseconds, deliberately not a fraction of the period. A budget that scales
# with the constraint makes `Fmax = 1 / (period - slack)` wrong, because the slack then
# moves with the period for two reasons at once. With a fixed budget the arithmetic is
# exact, which is what lets results/pnr quote a frequency from a single routed run.
set io_budget 1.0
set data_inputs [get_ports {a_tile_i b_tile_i acc_clear_i launch_i}]
set_input_delay $io_budget -clock $clk_name $data_inputs
set_output_delay $io_budget -clock $clk_name [get_ports {c_tile_o ready_o valid_o \
                                                         mac_tick_o}]

# Drive and load. sg13g2_buf_4 is the library's mid-strength buffer, and 0.03 pF is a
# short on-die net into a register bank rather than a pad.
set_driving_cell -lib_cell sg13g2_buf_4 -pin X $data_inputs
set_load 0.03 [all_outputs]

# launch_i reaches all TILE_M*TILE_N accumulator registers, which is 512 flip-flops at
# the committed geometry. Without a fanout limit the resizer leaves it as one net and
# the detailed router spends longer in its pin query than it spends routing. This was
# not a theory: it is what stalled the first place and route attempt in this
# repository. A limit makes the resizer build the buffer tree the net needs anyway.
set_max_fanout 20 [current_design]
set_max_transition 1.5 [current_design]
