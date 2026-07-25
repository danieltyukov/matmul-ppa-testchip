// Copyright 2026 Daniel Tyukov
// SPDX-License-Identifier: Apache-2.0
//
// Dot product unit built from explicit signed partial products and a Wallace
// (3:2 carry-save) reduction tree. Nothing here is left to the synthesiser's
// multiplier inference.
//
// Signed multiplication uses the negate-the-last-partial-product form. With
// multiplier B = -b[MSB]*2**(W-1) + sum_{j<W-1} b[j]*2**j, the product is
//
//   A*B = sum_{j=0}^{W-2} (b[j] ? A : 0) << j   -   (b[W-1] ? A : 0) << (W-1)
//
// The subtraction is turned into an addition of the bitwise complement plus a
// one, and that one is folded into a single extra addend. So one OPERAND_W wide
// signed multiply becomes OPERAND_W + 1 addends: OPERAND_W - 1 positive partial
// products, one complemented partial product, and one correction term.
//
// Each product is resolved to PROD_W bits with a carry propagating adder, then
// the TILE_K products go through a second Wallace tree and a final adder.

module dot_wallace #(
  parameter int unsigned TILE_K    = gemm_pkg::TILE_K,
  parameter int unsigned OPERAND_W = gemm_pkg::OPERAND_W,
  parameter int unsigned PROD_W    = gemm_pkg::PROD_W,
  parameter int unsigned DOT_W     = gemm_pkg::DOT_W
) (
  input  logic [TILE_K*OPERAND_W-1:0] a_row_i,
  input  logic [TILE_K*OPERAND_W-1:0] b_col_i,
  output logic [DOT_W-1:0]            dot_o
);

  // OPERAND_W - 1 shifted partial products, one complemented top product, and
  // one correction addend carrying the +1 of that complement.
  localparam int unsigned PP_COUNT = OPERAND_W + 1;

  logic [TILE_K*DOT_W-1:0] prod_sext;

  for (genvar k = 0; k < TILE_K; k++) begin : gen_mul
    logic [OPERAND_W-1:0] a_elem;
    logic [OPERAND_W-1:0] b_elem;
    logic [PROD_W-1:0]    a_wide;
    logic [PP_COUNT*PROD_W-1:0] pp;
    logic [PROD_W-1:0]    pp_sum;
    logic [PROD_W-1:0]    pp_carry;
    logic [PROD_W-1:0]    product;

    assign a_elem = a_row_i[k*OPERAND_W +: OPERAND_W];
    assign b_elem = b_col_i[k*OPERAND_W +: OPERAND_W];

    // Multiplicand sign extended to the full product width.
    assign a_wide = {{(PROD_W-OPERAND_W){a_elem[OPERAND_W-1]}}, a_elem};

    // Positive partial products for multiplier bits 0 .. OPERAND_W-2.
    for (genvar j = 0; j < OPERAND_W-1; j++) begin : gen_pp
      assign pp[j*PROD_W +: PROD_W] = b_elem[j] ? (a_wide << j) : {PROD_W{1'b0}};
    end

    // Complement of the top partial product, weighted 2**(OPERAND_W-1).
    assign pp[(OPERAND_W-1)*PROD_W +: PROD_W] =
        b_elem[OPERAND_W-1] ? ~(a_wide << (OPERAND_W-1)) : {PROD_W{1'b0}};

    // The +1 that completes the two's complement negation above.
    assign pp[OPERAND_W*PROD_W +: PROD_W] =
        {{(PROD_W-1){1'b0}}, b_elem[OPERAND_W-1]};

    csa_reduce #(
      .N_IN  (PP_COUNT),
      .WIDTH (PROD_W)
    ) u_pp_tree (
      .addends_i (pp),
      .sum_o     (pp_sum),
      .carry_o   (pp_carry)
    );

    assign product = pp_sum + pp_carry;

    assign prod_sext[k*DOT_W +: DOT_W] =
        {{(DOT_W-PROD_W){product[PROD_W-1]}}, product};
  end

  // Second reduction stage: sum the TILE_K signed products.
  if (TILE_K == 1) begin : gen_single_k
    assign dot_o = prod_sext[0 +: DOT_W];
  end else begin : gen_multi_k
    logic [DOT_W-1:0] dot_sum;
    logic [DOT_W-1:0] dot_carry;

    csa_reduce #(
      .N_IN  (TILE_K),
      .WIDTH (DOT_W)
    ) u_dot_tree (
      .addends_i (prod_sext),
      .sum_o     (dot_sum),
      .carry_o   (dot_carry)
    );

    assign dot_o = dot_sum + dot_carry;
  end

endmodule
