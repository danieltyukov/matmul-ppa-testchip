// Copyright 2026 Daniel Tyukov
// SPDX-License-Identifier: Apache-2.0
//
// Candidate 2: radix-4 Booth multiplier MAC.
//
// Same reduction tree as candidate 1, but the multiplier is Booth recoded first
// so there are OPERAND_W/2 partial products instead of OPERAND_W. Fewer addends
// to reduce, more multiplexing per addend. Single cycle.
//
// Implements the candidate engine interface documented in
// docs/ADDING_A_CANDIDATE.md. Every candidate in this directory has this exact
// port list.

module engine_booth4 #(
  parameter int unsigned TILE_M     = gemm_pkg::TILE_M,
  parameter int unsigned TILE_N     = gemm_pkg::TILE_N,
  parameter int unsigned TILE_K     = gemm_pkg::TILE_K,
  parameter int unsigned OPERAND_W  = gemm_pkg::OPERAND_W,
  parameter int unsigned ACC_W      = gemm_pkg::ACC_W,
  parameter int unsigned PROD_W     = gemm_pkg::PROD_W,
  parameter int unsigned DOT_W      = gemm_pkg::DOT_W,
  parameter int unsigned MAC_TICK_W = gemm_pkg::MAC_TICK_W
) (
  input  logic                                clk_i,
  input  logic                                rst_ni,
  input  logic                                acc_clear_i,
  input  logic                                launch_i,
  input  logic [TILE_M*TILE_K*OPERAND_W-1:0]  a_tile_i,
  input  logic [TILE_K*TILE_N*OPERAND_W-1:0]  b_tile_i,
  output logic [TILE_M*TILE_N*ACC_W-1:0]      c_tile_o,
  output logic                                ready_o,
  output logic                                valid_o,
  output logic [MAC_TICK_W-1:0]               mac_tick_o
);

  localparam int unsigned N_ELEM   = TILE_M * TILE_N;
  localparam int unsigned OP_SLICE = TILE_K * OPERAND_W;

  // MACs retired on the cycle valid_o is high, held in a 32 bit constant and
  // sliced at use so no tool has to guess about width truncation.
  localparam logic [31:0] MACS_PER_TILE = TILE_M * TILE_N * TILE_K;

  logic [N_ELEM*DOT_W-1:0] dots;

  for (genvar m = 0; m < TILE_M; m++) begin : gen_row
    for (genvar n = 0; n < TILE_N; n++) begin : gen_col
      logic [OP_SLICE-1:0] a_row;
      logic [OP_SLICE-1:0] b_col;

      // Row m of A is contiguous in the flattened tile.
      assign a_row = a_tile_i[m*OP_SLICE +: OP_SLICE];

      // Column n of B is strided by TILE_N.
      for (genvar k = 0; k < TILE_K; k++) begin : gen_gather
        assign b_col[k*OPERAND_W +: OPERAND_W] =
            b_tile_i[((k*TILE_N) + n)*OPERAND_W +: OPERAND_W];
      end

      dot_booth4 #(
        .TILE_K    (TILE_K),
        .OPERAND_W (OPERAND_W),
        .PROD_W    (PROD_W),
        .DOT_W     (DOT_W)
      ) u_dot (
        .a_row_i (a_row),
        .b_col_i (b_col),
        .dot_o   (dots[((m*TILE_N) + n)*DOT_W +: DOT_W])
      );
    end
  end

  acc_bank #(
    .N_ELEM (N_ELEM),
    .DOT_W  (DOT_W),
    .ACC_W  (ACC_W)
  ) u_acc (
    .clk_i    (clk_i),
    .rst_ni   (rst_ni),
    .clear_i  (acc_clear_i),
    .add_en_i (launch_i),
    .dots_i   (dots),
    .acc_o    (c_tile_o)
  );

  // Single cycle engine: always able to take a launch, answers one cycle later.
  logic valid_q;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) valid_q <= 1'b0;
    else         valid_q <= launch_i;
  end

  assign ready_o    = 1'b1;
  assign valid_o    = valid_q;
  assign mac_tick_o = valid_q ? MACS_PER_TILE[MAC_TICK_W-1:0]
                              : {MAC_TICK_W{1'b0}};

endmodule
