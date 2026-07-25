// Copyright 2026 Daniel Tyukov
// SPDX-License-Identifier: Apache-2.0
//
// Verification harness that instantiates every candidate engine side by side on
// the same clock, driven by the same operands and the same launch pulse.
//
// This is what makes the bit-exactness and cross-candidate equivalence tests
// cheap: one stimulus stream, ENGINE_COUNT answers to compare. No clock gating
// here on purpose, because the point is to run all candidates at once. Gating is
// verified separately at the chip level.
//
// launch_i must only be asserted when every bit of ready_o is high, since the
// slowest candidate sets the pace.

module tb_engine_harness #(
  parameter int unsigned TILE_M       = gemm_pkg::TILE_M,
  parameter int unsigned TILE_N       = gemm_pkg::TILE_N,
  parameter int unsigned TILE_K       = gemm_pkg::TILE_K,
  parameter int unsigned OPERAND_W    = gemm_pkg::OPERAND_W,
  parameter int unsigned ACC_W        = gemm_pkg::ACC_W,
  parameter int unsigned ENGINE_COUNT = gemm_pkg::ENGINE_COUNT,
  parameter int unsigned MAC_TICK_W   = gemm_pkg::MAC_TICK_W
) (
  input  logic                                clk_i,
  input  logic                                rst_ni,
  input  logic                                acc_clear_i,
  input  logic                                launch_i,
  input  logic [TILE_M*TILE_K*OPERAND_W-1:0]  a_tile_i,
  input  logic [TILE_K*TILE_N*OPERAND_W-1:0]  b_tile_i,
  output logic [ENGINE_COUNT-1:0]             ready_o,
  output logic [ENGINE_COUNT-1:0]             valid_o,
  output logic [ENGINE_COUNT*TILE_M*TILE_N*ACC_W-1:0] c_tile_o,
  output logic [ENGINE_COUNT*MAC_TICK_W-1:0]  mac_tick_o
);

  localparam int unsigned C_TILE_W = TILE_M * TILE_N * ACC_W;

  engine_infer #(
    .TILE_M (TILE_M), .TILE_N (TILE_N), .TILE_K (TILE_K),
    .OPERAND_W (OPERAND_W), .ACC_W (ACC_W)
  ) u_eng0 (
    .clk_i (clk_i), .rst_ni (rst_ni),
    .acc_clear_i (acc_clear_i), .launch_i (launch_i),
    .a_tile_i (a_tile_i), .b_tile_i (b_tile_i),
    .c_tile_o (c_tile_o[gemm_pkg::ENG_INFER*C_TILE_W +: C_TILE_W]),
    .ready_o (ready_o[gemm_pkg::ENG_INFER]),
    .valid_o (valid_o[gemm_pkg::ENG_INFER]),
    .mac_tick_o (mac_tick_o[gemm_pkg::ENG_INFER*MAC_TICK_W +: MAC_TICK_W])
  );

  engine_wallace #(
    .TILE_M (TILE_M), .TILE_N (TILE_N), .TILE_K (TILE_K),
    .OPERAND_W (OPERAND_W), .ACC_W (ACC_W)
  ) u_eng1 (
    .clk_i (clk_i), .rst_ni (rst_ni),
    .acc_clear_i (acc_clear_i), .launch_i (launch_i),
    .a_tile_i (a_tile_i), .b_tile_i (b_tile_i),
    .c_tile_o (c_tile_o[gemm_pkg::ENG_WALLACE*C_TILE_W +: C_TILE_W]),
    .ready_o (ready_o[gemm_pkg::ENG_WALLACE]),
    .valid_o (valid_o[gemm_pkg::ENG_WALLACE]),
    .mac_tick_o (mac_tick_o[gemm_pkg::ENG_WALLACE*MAC_TICK_W +: MAC_TICK_W])
  );

  engine_booth4 #(
    .TILE_M (TILE_M), .TILE_N (TILE_N), .TILE_K (TILE_K),
    .OPERAND_W (OPERAND_W), .ACC_W (ACC_W)
  ) u_eng2 (
    .clk_i (clk_i), .rst_ni (rst_ni),
    .acc_clear_i (acc_clear_i), .launch_i (launch_i),
    .a_tile_i (a_tile_i), .b_tile_i (b_tile_i),
    .c_tile_o (c_tile_o[gemm_pkg::ENG_BOOTH4*C_TILE_W +: C_TILE_W]),
    .ready_o (ready_o[gemm_pkg::ENG_BOOTH4]),
    .valid_o (valid_o[gemm_pkg::ENG_BOOTH4]),
    .mac_tick_o (mac_tick_o[gemm_pkg::ENG_BOOTH4*MAC_TICK_W +: MAC_TICK_W])
  );

  engine_signmag #(
    .TILE_M (TILE_M), .TILE_N (TILE_N), .TILE_K (TILE_K),
    .OPERAND_W (OPERAND_W), .ACC_W (ACC_W)
  ) u_eng3 (
    .clk_i (clk_i), .rst_ni (rst_ni),
    .acc_clear_i (acc_clear_i), .launch_i (launch_i),
    .a_tile_i (a_tile_i), .b_tile_i (b_tile_i),
    .c_tile_o (c_tile_o[gemm_pkg::ENG_SIGNMAG*C_TILE_W +: C_TILE_W]),
    .ready_o (ready_o[gemm_pkg::ENG_SIGNMAG]),
    .valid_o (valid_o[gemm_pkg::ENG_SIGNMAG]),
    .mac_tick_o (mac_tick_o[gemm_pkg::ENG_SIGNMAG*MAC_TICK_W +: MAC_TICK_W])
  );

  engine_bitserial #(
    .TILE_M (TILE_M), .TILE_N (TILE_N), .TILE_K (TILE_K),
    .OPERAND_W (OPERAND_W), .ACC_W (ACC_W)
  ) u_eng4 (
    .clk_i (clk_i), .rst_ni (rst_ni),
    .acc_clear_i (acc_clear_i), .launch_i (launch_i),
    .a_tile_i (a_tile_i), .b_tile_i (b_tile_i),
    .c_tile_o (c_tile_o[gemm_pkg::ENG_BITSERIAL*C_TILE_W +: C_TILE_W]),
    .ready_o (ready_o[gemm_pkg::ENG_BITSERIAL]),
    .valid_o (valid_o[gemm_pkg::ENG_BITSERIAL]),
    .mac_tick_o (mac_tick_o[gemm_pkg::ENG_BITSERIAL*MAC_TICK_W +: MAC_TICK_W])
  );

endmodule
