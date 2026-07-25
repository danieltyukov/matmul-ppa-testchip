// Copyright 2026 Daniel Tyukov
// SPDX-License-Identifier: Apache-2.0
//
// Candidate engine array: all ENGINE_COUNT candidates, one runtime selected.
//
// This is the block that makes the PPA measurement meaningful, and it does three
// things beyond a plain output mux:
//
//  1. Clock gating. Every candidate gets its own integrated clock gate, and only
//     the selected candidate's clock runs. Without this, the unselected
//     candidates' accumulator banks and pipeline registers would clock on every
//     cycle and swamp the measurement.
//  2. Operand isolation. The operand tiles are AND-gated per candidate, so an
//     unselected candidate sees a constant zero on its inputs and its
//     combinational logic does not toggle either. Clock gating alone does not
//     achieve this: combinational arrays have no clock to gate.
//  3. Control isolation. launch and clear are gated too, so a stopped candidate
//     cannot be told to do anything.
//
// The cost of 2 and 3 is real: TILE_M*TILE_K + TILE_K*TILE_N operand bytes times
// ENGINE_COUNT AND gates, charged to the shared logic rather than to any
// candidate. It is reported separately in the synthesis results, because a
// production accelerator with one datapath would not pay it.
//
// Adding a candidate: bump gemm_pkg::ENGINE_COUNT and add one instance below
// following the pattern. See docs/ADDING_A_CANDIDATE.md.

module engine_array #(
  parameter int unsigned TILE_M       = gemm_pkg::TILE_M,
  parameter int unsigned TILE_N       = gemm_pkg::TILE_N,
  parameter int unsigned TILE_K       = gemm_pkg::TILE_K,
  parameter int unsigned OPERAND_W    = gemm_pkg::OPERAND_W,
  parameter int unsigned ACC_W        = gemm_pkg::ACC_W,
  parameter int unsigned ENGINE_COUNT = gemm_pkg::ENGINE_COUNT,
  parameter int unsigned MAC_TICK_W   = gemm_pkg::MAC_TICK_W
) (
  input  logic                               clk_i,
  input  logic                               rst_ni,
  input  logic                               test_mode_i,   // ungate every clock

  input  logic [$clog2(ENGINE_COUNT)-1:0]    engine_sel_i,
  input  logic                               clear_i,
  input  logic                               launch_i,
  input  logic [TILE_M*TILE_K*OPERAND_W-1:0] a_tile_i,
  input  logic [TILE_K*TILE_N*OPERAND_W-1:0] b_tile_i,

  output logic                               ready_o,
  output logic                               valid_o,
  output logic [MAC_TICK_W-1:0]              mac_tick_o,
  output logic [TILE_M*TILE_N*ACC_W-1:0]     c_tile_o
);

  localparam int unsigned A_TILE_W = TILE_M * TILE_K * OPERAND_W;
  localparam int unsigned B_TILE_W = TILE_K * TILE_N * OPERAND_W;
  localparam int unsigned C_TILE_W = TILE_M * TILE_N * ACC_W;

  logic [ENGINE_COUNT-1:0]              sel_onehot;
  logic [ENGINE_COUNT-1:0]              clk_gated;
  logic [ENGINE_COUNT-1:0]              eng_ready;
  logic [ENGINE_COUNT-1:0]              eng_valid;
  logic [ENGINE_COUNT*MAC_TICK_W-1:0]   eng_mac_tick;
  logic [ENGINE_COUNT*C_TILE_W-1:0]     eng_c_tile;
  logic [ENGINE_COUNT*A_TILE_W-1:0]     eng_a_tile;
  logic [ENGINE_COUNT*B_TILE_W-1:0]     eng_b_tile;
  logic [ENGINE_COUNT-1:0]              eng_launch;
  logic [ENGINE_COUNT-1:0]              eng_clear;

  for (genvar e = 0; e < ENGINE_COUNT; e++) begin : gen_iso
    assign sel_onehot[e] = (32'(engine_sel_i) == e) || test_mode_i;

    clock_gate u_icg (
      .clk_i         (clk_i),
      .enable_i      (sel_onehot[e]),
      .test_enable_i (test_mode_i),
      .clk_o         (clk_gated[e])
    );

    // Operand and control isolation. An unselected candidate sees constants, so
    // its combinational arrays hold still and contribute no switching activity.
    assign eng_a_tile[e*A_TILE_W +: A_TILE_W] =
        sel_onehot[e] ? a_tile_i : {A_TILE_W{1'b0}};
    assign eng_b_tile[e*B_TILE_W +: B_TILE_W] =
        sel_onehot[e] ? b_tile_i : {B_TILE_W{1'b0}};
    assign eng_launch[e] = sel_onehot[e] && launch_i;
    assign eng_clear[e]  = sel_onehot[e] && clear_i;
  end

  // ---------------------------------------------------------------------------
  // Candidate instances. One per gemm_pkg engine index.
  // ---------------------------------------------------------------------------
  engine_infer #(
    .TILE_M (TILE_M), .TILE_N (TILE_N), .TILE_K (TILE_K),
    .OPERAND_W (OPERAND_W), .ACC_W (ACC_W)
  ) u_engine_infer (
    .clk_i       (clk_gated[gemm_pkg::ENG_INFER]),
    .rst_ni      (rst_ni),
    .acc_clear_i (eng_clear[gemm_pkg::ENG_INFER]),
    .launch_i    (eng_launch[gemm_pkg::ENG_INFER]),
    .a_tile_i    (eng_a_tile[gemm_pkg::ENG_INFER*A_TILE_W +: A_TILE_W]),
    .b_tile_i    (eng_b_tile[gemm_pkg::ENG_INFER*B_TILE_W +: B_TILE_W]),
    .c_tile_o    (eng_c_tile[gemm_pkg::ENG_INFER*C_TILE_W +: C_TILE_W]),
    .ready_o     (eng_ready[gemm_pkg::ENG_INFER]),
    .valid_o     (eng_valid[gemm_pkg::ENG_INFER]),
    .mac_tick_o  (eng_mac_tick[gemm_pkg::ENG_INFER*MAC_TICK_W +: MAC_TICK_W])
  );

  engine_wallace #(
    .TILE_M (TILE_M), .TILE_N (TILE_N), .TILE_K (TILE_K),
    .OPERAND_W (OPERAND_W), .ACC_W (ACC_W)
  ) u_engine_wallace (
    .clk_i       (clk_gated[gemm_pkg::ENG_WALLACE]),
    .rst_ni      (rst_ni),
    .acc_clear_i (eng_clear[gemm_pkg::ENG_WALLACE]),
    .launch_i    (eng_launch[gemm_pkg::ENG_WALLACE]),
    .a_tile_i    (eng_a_tile[gemm_pkg::ENG_WALLACE*A_TILE_W +: A_TILE_W]),
    .b_tile_i    (eng_b_tile[gemm_pkg::ENG_WALLACE*B_TILE_W +: B_TILE_W]),
    .c_tile_o    (eng_c_tile[gemm_pkg::ENG_WALLACE*C_TILE_W +: C_TILE_W]),
    .ready_o     (eng_ready[gemm_pkg::ENG_WALLACE]),
    .valid_o     (eng_valid[gemm_pkg::ENG_WALLACE]),
    .mac_tick_o  (eng_mac_tick[gemm_pkg::ENG_WALLACE*MAC_TICK_W +: MAC_TICK_W])
  );

  engine_booth4 #(
    .TILE_M (TILE_M), .TILE_N (TILE_N), .TILE_K (TILE_K),
    .OPERAND_W (OPERAND_W), .ACC_W (ACC_W)
  ) u_engine_booth4 (
    .clk_i       (clk_gated[gemm_pkg::ENG_BOOTH4]),
    .rst_ni      (rst_ni),
    .acc_clear_i (eng_clear[gemm_pkg::ENG_BOOTH4]),
    .launch_i    (eng_launch[gemm_pkg::ENG_BOOTH4]),
    .a_tile_i    (eng_a_tile[gemm_pkg::ENG_BOOTH4*A_TILE_W +: A_TILE_W]),
    .b_tile_i    (eng_b_tile[gemm_pkg::ENG_BOOTH4*B_TILE_W +: B_TILE_W]),
    .c_tile_o    (eng_c_tile[gemm_pkg::ENG_BOOTH4*C_TILE_W +: C_TILE_W]),
    .ready_o     (eng_ready[gemm_pkg::ENG_BOOTH4]),
    .valid_o     (eng_valid[gemm_pkg::ENG_BOOTH4]),
    .mac_tick_o  (eng_mac_tick[gemm_pkg::ENG_BOOTH4*MAC_TICK_W +: MAC_TICK_W])
  );

  engine_signmag #(
    .TILE_M (TILE_M), .TILE_N (TILE_N), .TILE_K (TILE_K),
    .OPERAND_W (OPERAND_W), .ACC_W (ACC_W)
  ) u_engine_signmag (
    .clk_i       (clk_gated[gemm_pkg::ENG_SIGNMAG]),
    .rst_ni      (rst_ni),
    .acc_clear_i (eng_clear[gemm_pkg::ENG_SIGNMAG]),
    .launch_i    (eng_launch[gemm_pkg::ENG_SIGNMAG]),
    .a_tile_i    (eng_a_tile[gemm_pkg::ENG_SIGNMAG*A_TILE_W +: A_TILE_W]),
    .b_tile_i    (eng_b_tile[gemm_pkg::ENG_SIGNMAG*B_TILE_W +: B_TILE_W]),
    .c_tile_o    (eng_c_tile[gemm_pkg::ENG_SIGNMAG*C_TILE_W +: C_TILE_W]),
    .ready_o     (eng_ready[gemm_pkg::ENG_SIGNMAG]),
    .valid_o     (eng_valid[gemm_pkg::ENG_SIGNMAG]),
    .mac_tick_o  (eng_mac_tick[gemm_pkg::ENG_SIGNMAG*MAC_TICK_W +: MAC_TICK_W])
  );

  engine_bitserial #(
    .TILE_M (TILE_M), .TILE_N (TILE_N), .TILE_K (TILE_K),
    .OPERAND_W (OPERAND_W), .ACC_W (ACC_W)
  ) u_engine_bitserial (
    .clk_i       (clk_gated[gemm_pkg::ENG_BITSERIAL]),
    .rst_ni      (rst_ni),
    .acc_clear_i (eng_clear[gemm_pkg::ENG_BITSERIAL]),
    .launch_i    (eng_launch[gemm_pkg::ENG_BITSERIAL]),
    .a_tile_i    (eng_a_tile[gemm_pkg::ENG_BITSERIAL*A_TILE_W +: A_TILE_W]),
    .b_tile_i    (eng_b_tile[gemm_pkg::ENG_BITSERIAL*B_TILE_W +: B_TILE_W]),
    .c_tile_o    (eng_c_tile[gemm_pkg::ENG_BITSERIAL*C_TILE_W +: C_TILE_W]),
    .ready_o     (eng_ready[gemm_pkg::ENG_BITSERIAL]),
    .valid_o     (eng_valid[gemm_pkg::ENG_BITSERIAL]),
    .mac_tick_o  (eng_mac_tick[gemm_pkg::ENG_BITSERIAL*MAC_TICK_W +: MAC_TICK_W])
  );

  // ---------------------------------------------------------------------------
  // Output selection
  // ---------------------------------------------------------------------------
  integer sel;

  always_comb begin
    ready_o    = 1'b0;
    valid_o    = 1'b0;
    mac_tick_o = '0;
    c_tile_o   = '0;
    for (sel = 0; sel < ENGINE_COUNT; sel = sel + 1) begin
      if (32'(engine_sel_i) == sel) begin
        ready_o    = eng_ready[sel];
        valid_o    = eng_valid[sel];
        mac_tick_o = eng_mac_tick[sel*MAC_TICK_W +: MAC_TICK_W];
        c_tile_o   = eng_c_tile[sel*C_TILE_W +: C_TILE_W];
      end
    end
  end

endmodule
