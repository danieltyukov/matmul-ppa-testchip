# Copyright 2026 Daniel Tyukov
# SPDX-License-Identifier: Apache-2.0
#
# Clock definitions.
#
# The chip is single clock by construction: the SPI pins are oversampled in the
# core domain rather than used as a clock, so there is exactly one clock to
# constrain and no clock domain crossing to except.
#
# 50 MHz is a deliberately conservative target for 130 nm with a 64 MAC
# combinational array in the critical path. The measured logic depth of the
# candidates (results/synth/generic/summary.csv) is 51 to 57 gate levels, which at
# a rough 40 ps per stage is around 2.3 ns, so 20 ns leaves large margin for
# routing, the SRAM access and process corners. Raise it once a real timing run
# says what the design can do.

set CLK_PORT   pad_clk_i
set CLK_NAME   core_clk
set CLK_PERIOD 20.0

create_clock -name $CLK_NAME -period $CLK_PERIOD [get_ports $CLK_PORT]

# Uncertainty covers jitter plus the clock tree skew that CTS has not built yet.
set_clock_uncertainty 0.30 [get_clocks $CLK_NAME]
set_clock_transition  0.20 [get_clocks $CLK_NAME]

# The SPI pins are asynchronous to the core clock. They go through a two flop
# synchroniser (rtl/lib/sync_2ff.sv), so timing them as ordinary inputs is not
# meaningful; what matters is that they are stable for at least two core cycles,
# which the f_spi <= f_core/8 rule guarantees. Marking them false paths keeps the
# tool from optimising a path that has no setup requirement.
set_false_path -from [get_ports {pad_spi_sck_i pad_spi_cs_ni pad_spi_mosi_i}]

# The external reset is asynchronously asserted and synchronously released by
# rtl/lib/reset_bridge.sv, so it has no setup requirement either.
set_false_path -from [get_ports pad_rst_ni]

# Test mode is a static configuration pin, strapped for the whole run.
set_false_path -from [get_ports pad_test_mode_i]

# The integrated clock gates in engine_array produce gated versions of the core
# clock. They must be treated as the same clock, not as new ones.
set_propagated_clock [get_clocks $CLK_NAME]
