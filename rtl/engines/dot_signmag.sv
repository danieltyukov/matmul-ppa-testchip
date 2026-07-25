// Copyright 2026 Daniel Tyukov
// SPDX-License-Identifier: Apache-2.0
//
// Dot product unit with a sign-magnitude datapath.
//
// The hypothesis this candidate exists to test: two's complement operands make
// small negative values look like wide runs of ones (-1 is all ones, -2 is all
// ones but the bottom bit), so a stream of operands that crosses zero flips many
// high-order bits. Sign-magnitude keeps the magnitude bits quiet and confines
// the polarity change to a single sign bit, which should reduce switching
// activity in the multiplier array and therefore dynamic power.
//
// The cost is two converters per operand element, a conditional negation per
// product, and unsigned partial products that are one bit wider than they would
// otherwise need to be, because |-2**(W-1)| = 2**(W-1) does not fit in W-1 bits.
//
// Structurally this is dot_wallace with the signed partial product scheme
// replaced by unsigned partial products plus sign handling. That is deliberate:
// holding the reduction tree identical is what makes the activity comparison
// against dot_wallace a controlled experiment rather than an anecdote.

module dot_signmag #(
  parameter int unsigned TILE_K    = gemm_pkg::TILE_K,
  parameter int unsigned OPERAND_W = gemm_pkg::OPERAND_W,
  parameter int unsigned PROD_W    = gemm_pkg::PROD_W,
  parameter int unsigned DOT_W     = gemm_pkg::DOT_W
) (
  input  logic [TILE_K*OPERAND_W-1:0] a_row_i,
  input  logic [TILE_K*OPERAND_W-1:0] b_col_i,
  output logic [DOT_W-1:0]            dot_o
);

  logic [TILE_K*DOT_W-1:0] prod_sext;

  for (genvar k = 0; k < TILE_K; k++) begin : gen_mul
    logic [OPERAND_W-1:0]         a_elem;
    logic [OPERAND_W-1:0]         b_elem;
    logic                         a_sign;
    logic                         b_sign;
    logic [OPERAND_W-1:0]         a_mag;
    logic [OPERAND_W-1:0]         b_mag;
    logic [PROD_W-1:0]            a_mag_wide;
    logic [OPERAND_W*PROD_W-1:0]  pp;
    logic [PROD_W-1:0]            pp_sum;
    logic [PROD_W-1:0]            pp_carry;
    logic [PROD_W-1:0]            mag_product;
    logic                         prod_sign;
    logic [PROD_W-1:0]            product;

    assign a_elem = a_row_i[k*OPERAND_W +: OPERAND_W];
    assign b_elem = b_col_i[k*OPERAND_W +: OPERAND_W];

    // Two's complement to sign-magnitude. Negating the whole OPERAND_W wide word
    // rather than just the low bits is what makes |-2**(W-1)| come out right.
    assign a_sign = a_elem[OPERAND_W-1];
    assign b_sign = b_elem[OPERAND_W-1];
    assign a_mag  = a_sign ? (~a_elem + {{(OPERAND_W-1){1'b0}}, 1'b1}) : a_elem;
    assign b_mag  = b_sign ? (~b_elem + {{(OPERAND_W-1){1'b0}}, 1'b1}) : b_elem;

    assign a_mag_wide = {{(PROD_W-OPERAND_W){1'b0}}, a_mag};

    // Unsigned partial products: no complemented term, no correction addend.
    for (genvar j = 0; j < OPERAND_W; j++) begin : gen_pp
      assign pp[j*PROD_W +: PROD_W] =
          b_mag[j] ? (a_mag_wide << j) : {PROD_W{1'b0}};
    end

    csa_reduce #(
      .N_IN  (OPERAND_W),
      .WIDTH (PROD_W)
    ) u_pp_tree (
      .addends_i (pp),
      .sum_o     (pp_sum),
      .carry_o   (pp_carry)
    );

    assign mag_product = pp_sum + pp_carry;

    // Apply the product sign once, at the output of the magnitude array.
    assign prod_sign = a_sign ^ b_sign;
    assign product   = prod_sign ? (~mag_product + {{(PROD_W-1){1'b0}}, 1'b1})
                                 : mag_product;

    assign prod_sext[k*DOT_W +: DOT_W] =
        {{(DOT_W-PROD_W){product[PROD_W-1]}}, product};
  end

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
