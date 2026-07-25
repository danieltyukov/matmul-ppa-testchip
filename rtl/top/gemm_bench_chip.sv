// Copyright 2026 Daniel Tyukov
// SPDX-License-Identifier: Apache-2.0
//
// Chip top level: pad frame plus core, nothing else.
//
// Keeping the top level this thin is deliberate. Everything the flow needs to
// treat specially (IO cells, tristates) lives in pad_frame, and everything that
// simulates and synthesises as ordinary logic lives in bench_core. Verification
// drives this module, so the pad frame is in the loop for every test.

module gemm_bench_chip (
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
  output logic pad_stat_mismatch_o
);

  logic core_clk;
  logic core_rst_n;
  logic core_test_mode;
  logic core_spi_sck;
  logic core_spi_cs_n;
  logic core_spi_mosi;
  logic core_spi_miso;
  logic core_spi_miso_oe;
  logic core_stat_busy;
  logic core_stat_done;
  logic core_stat_vfy_done;
  logic core_stat_mismatch;

  pad_frame u_pad_frame (
    .pad_clk_i            (pad_clk_i),
    .pad_rst_ni           (pad_rst_ni),
    .pad_test_mode_i      (pad_test_mode_i),
    .pad_spi_sck_i        (pad_spi_sck_i),
    .pad_spi_cs_ni        (pad_spi_cs_ni),
    .pad_spi_mosi_i       (pad_spi_mosi_i),
    .pad_spi_miso_io      (pad_spi_miso_io),
    .pad_stat_busy_o      (pad_stat_busy_o),
    .pad_stat_done_o      (pad_stat_done_o),
    .pad_stat_vfy_done_o  (pad_stat_vfy_done_o),
    .pad_stat_mismatch_o  (pad_stat_mismatch_o),
    .core_clk_o           (core_clk),
    .core_rst_no          (core_rst_n),
    .core_test_mode_o     (core_test_mode),
    .core_spi_sck_o       (core_spi_sck),
    .core_spi_cs_no       (core_spi_cs_n),
    .core_spi_mosi_o      (core_spi_mosi),
    .core_spi_miso_i      (core_spi_miso),
    .core_spi_miso_oe_i   (core_spi_miso_oe),
    .core_stat_busy_i     (core_stat_busy),
    .core_stat_done_i     (core_stat_done),
    .core_stat_vfy_done_i (core_stat_vfy_done),
    .core_stat_mismatch_i (core_stat_mismatch)
  );

  bench_core u_bench_core (
    .clk_i           (core_clk),
    .ext_rst_ni      (core_rst_n),
    .test_mode_i     (core_test_mode),
    .spi_sck_i       (core_spi_sck),
    .spi_cs_ni       (core_spi_cs_n),
    .spi_mosi_i      (core_spi_mosi),
    .spi_miso_o      (core_spi_miso),
    .spi_miso_oe_o   (core_spi_miso_oe),
    .stat_busy_o     (core_stat_busy),
    .stat_done_o     (core_stat_done),
    .stat_vfy_done_o (core_stat_vfy_done),
    .stat_mismatch_o (core_stat_mismatch)
  );

endmodule
