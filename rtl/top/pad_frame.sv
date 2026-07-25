// Copyright 2026 Daniel Tyukov
// SPDX-License-Identifier: Apache-2.0
//
// Pad frame.
//
// The default body is plain wires plus an explicit tristate on MISO, which is
// what Verilator, Icarus and Yosys need to see. Define GEMM_PAD_MACRO to bind IHP
// SG13G2 IO cells (sg13g2_IOPadIn, sg13g2_IOPadOut16mA, sg13g2_IOPadInOut16mA and
// the supply pads); the port list here is the contract that binding satisfies.
//
// Pin budget: 4 SPI, 1 clock, 1 reset, 1 test mode, 4 status = 11 signal pads,
// plus core and IO supply pairs. That fits a small QFN, which is the point of
// choosing SPI over a parallel host bus.

module pad_frame (
  // Chip side (bonded pads).
  input  logic pad_clk_i,
  input  logic pad_rst_ni,
  input  logic pad_test_mode_i,
  input  logic pad_spi_sck_i,
  input  logic pad_spi_cs_ni,
  input  logic pad_spi_mosi_i,
  inout  wire  pad_spi_miso_io,
  output logic pad_stat_busy_o,
  output logic pad_stat_done_o,
  output logic pad_stat_vfy_done_o,
  output logic pad_stat_mismatch_o,

  // Core side.
  output logic core_clk_o,
  output logic core_rst_no,
  output logic core_test_mode_o,
  output logic core_spi_sck_o,
  output logic core_spi_cs_no,
  output logic core_spi_mosi_o,
  input  logic core_spi_miso_i,
  input  logic core_spi_miso_oe_i,
  input  logic core_stat_busy_i,
  input  logic core_stat_done_i,
  input  logic core_stat_vfy_done_i,
  input  logic core_stat_mismatch_i
);

`ifdef GEMM_PAD_MACRO
  gemm_pad_macro_bind u_pads (
    .pad_clk_i, .pad_rst_ni, .pad_test_mode_i,
    .pad_spi_sck_i, .pad_spi_cs_ni, .pad_spi_mosi_i, .pad_spi_miso_io,
    .pad_stat_busy_o, .pad_stat_done_o, .pad_stat_vfy_done_o, .pad_stat_mismatch_o,
    .core_clk_o, .core_rst_no, .core_test_mode_o,
    .core_spi_sck_o, .core_spi_cs_no, .core_spi_mosi_o,
    .core_spi_miso_i, .core_spi_miso_oe_i,
    .core_stat_busy_i, .core_stat_done_i,
    .core_stat_vfy_done_i, .core_stat_mismatch_i
  );
`else
  assign core_clk_o       = pad_clk_i;
  assign core_rst_no      = pad_rst_ni;
  assign core_test_mode_o = pad_test_mode_i;
  assign core_spi_sck_o   = pad_spi_sck_i;
  assign core_spi_cs_no   = pad_spi_cs_ni;
  assign core_spi_mosi_o  = pad_spi_mosi_i;

  // MISO is only driven while the controller has selected the chip, so several
  // targets can share one bus.
  assign pad_spi_miso_io = core_spi_miso_oe_i ? core_spi_miso_i : 1'bz;

  assign pad_stat_busy_o     = core_stat_busy_i;
  assign pad_stat_done_o     = core_stat_done_i;
  assign pad_stat_vfy_done_o = core_stat_vfy_done_i;
  assign pad_stat_mismatch_o = core_stat_mismatch_i;
`endif

endmodule
