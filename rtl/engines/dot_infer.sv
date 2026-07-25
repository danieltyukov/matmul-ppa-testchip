// Copyright 2026 Daniel Tyukov
// SPDX-License-Identifier: Apache-2.0
//
// Dot product unit, candidate 0 style: written with `*` and `+` and nothing
// else, so the synthesiser picks the multiplier and adder architecture. This is
// the control point of the whole experiment. Every other candidate has to beat
// what Yosys plus ABC produce from this description.

module dot_infer #(
  parameter int unsigned TILE_K    = gemm_pkg::TILE_K,
  parameter int unsigned OPERAND_W = gemm_pkg::OPERAND_W,
  parameter int unsigned DOT_W     = gemm_pkg::DOT_W
) (
  input  logic [TILE_K*OPERAND_W-1:0] a_row_i,  // A[m][0..TILE_K-1]
  input  logic [TILE_K*OPERAND_W-1:0] b_col_i,  // B[0..TILE_K-1][n]
  output logic [DOT_W-1:0]            dot_o
);

  logic signed [DOT_W-1:0] acc;
  integer k;

  always_comb begin
    acc = '0;
    for (k = 0; k < TILE_K; k = k + 1) begin
      acc = acc + $signed(a_row_i[k*OPERAND_W +: OPERAND_W])
                * $signed(b_col_i[k*OPERAND_W +: OPERAND_W]);
    end
  end

  assign dot_o = acc;

endmodule
