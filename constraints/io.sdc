# Copyright 2026 Daniel Tyukov
# SPDX-License-Identifier: Apache-2.0
#
# IO characteristics.
#
# The status pins are observed by a slow logic analyser or an FPGA, and MISO is
# sampled by an SPI controller running at f_core/8 or slower, so the output delay
# budgets here are generous on purpose. Nothing on this chip drives a fast bus.

# Drive strength seen at the chip inputs, as an equivalent slew.
set_input_transition 0.50 [all_inputs]

# Load presented by the board on each output. 0.05 pF is a short trace into a
# high-impedance receiver.
set_load 0.05 [all_outputs]

# MISO must be stable well before the controller's next rising edge. At
# f_spi = f_core/8 that is four core cycles, so an output delay of one quarter of
# the core period is already ten times more margin than needed.
set_output_delay 5.0 -clock core_clk [get_ports pad_spi_miso_io]

# Status pins are polled, not sampled synchronously.
set_output_delay 5.0 -clock core_clk [get_ports {pad_stat_busy_o pad_stat_done_o \
                                                 pad_stat_vfy_done_o \
                                                 pad_stat_mismatch_o}]

# Maximum fanout and transition, so synthesis inserts buffers rather than building
# one enormous driver.
set_max_fanout 12 [current_design]
set_max_transition 1.5 [current_design]
