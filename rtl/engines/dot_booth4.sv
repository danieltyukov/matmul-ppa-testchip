// Copyright 2026 Daniel Tyukov
// SPDX-License-Identifier: Apache-2.0
//
// Dot product unit using radix-4 modified Booth recoding, then the same Wallace
// reduction as dot_wallace. Booth recoding halves the number of partial products
// (OPERAND_W/2 instead of OPERAND_W) at the cost of a recoder and a 3:1 select
// per digit, which is the classic area-versus-multiplexing trade this chip is
// built to measure.
//
// The multiplier is split into overlapping triplets (b[2i+1], b[2i], b[2i-1])
// with b[-1] = 0, each mapping to a digit in {-2,-1,0,+1,+2}:
//
//   000 ->  0     100 -> -2
//   001 -> +1     101 -> -1
//   010 -> +1     110 -> -1
//   011 -> +2     111 ->  0
//
// sum_i digit_i * 4**i reproduces the signed value of the multiplier exactly, so
// no separate sign correction on the multiplier side is needed. Negative digits
// are formed as bitwise complement plus one, and the ones are collected into a
// single extra addend with each bit sitting at its digit's weight.

module dot_booth4 #(
  parameter int unsigned TILE_K    = gemm_pkg::TILE_K,
  parameter int unsigned OPERAND_W = gemm_pkg::OPERAND_W,
  parameter int unsigned PROD_W    = gemm_pkg::PROD_W,
  parameter int unsigned DOT_W     = gemm_pkg::DOT_W
) (
  input  logic [TILE_K*OPERAND_W-1:0] a_row_i,
  input  logic [TILE_K*OPERAND_W-1:0] b_col_i,
  output logic [DOT_W-1:0]            dot_o
);

  localparam int unsigned DIGITS   = (OPERAND_W + 1) / 2;
  localparam int unsigned PP_COUNT = DIGITS + 1;  // digits plus the +1 collector

  logic [TILE_K*DOT_W-1:0] prod_sext;

  for (genvar k = 0; k < TILE_K; k++) begin : gen_mul
    logic [OPERAND_W-1:0]       a_elem;
    logic [OPERAND_W-1:0]       b_elem;
    logic [PROD_W-1:0]          a_wide;
    logic [PROD_W-1:0]          a_twice;
    logic [PP_COUNT*PROD_W-1:0] pp;
    logic [PROD_W-1:0]          corr;
    logic [PROD_W-1:0]          pp_sum;
    logic [PROD_W-1:0]          pp_carry;
    logic [PROD_W-1:0]          product;
    logic [DIGITS-1:0]          digit_neg;

    assign a_elem  = a_row_i[k*OPERAND_W +: OPERAND_W];
    assign b_elem  = b_col_i[k*OPERAND_W +: OPERAND_W];
    assign a_wide  = {{(PROD_W-OPERAND_W){a_elem[OPERAND_W-1]}}, a_elem};
    assign a_twice = a_wide << 1;

    for (genvar d = 0; d < DIGITS; d++) begin : gen_digit
      logic [2:0]        triplet;
      logic              sel_two;
      logic              sel_one;
      logic              is_neg;
      logic [PROD_W-1:0] magnitude;
      logic [PROD_W-1:0] signed_pp;

      // b[-1] is a hard zero; the top triplet reads the multiplier sign bit.
      assign triplet[0] = (d == 0) ? 1'b0 : b_elem[2*d-1];
      assign triplet[1] = b_elem[2*d];
      assign triplet[2] = (2*d + 1 < OPERAND_W) ? b_elem[2*d+1]
                                                : b_elem[OPERAND_W-1];

      // +-2 when the triplet is 011 or 100, +-1 when it is 001/010/101/110.
      assign sel_two = (triplet == 3'b011) || (triplet == 3'b100);
      assign sel_one = (triplet == 3'b001) || (triplet == 3'b010)
                    || (triplet == 3'b101) || (triplet == 3'b110);
      assign is_neg  = triplet[2] && (triplet != 3'b111);

      assign magnitude = sel_two ? a_twice
                       : sel_one ? a_wide
                                 : {PROD_W{1'b0}};

      assign signed_pp = is_neg ? ~magnitude : magnitude;

      assign pp[d*PROD_W +: PROD_W] = signed_pp << (2*d);
      assign digit_neg[d] = is_neg;
    end

    // One correction bit per negated digit, each at that digit's weight.
    always_comb begin
      corr = '0;
      for (int unsigned d = 0; d < DIGITS; d++) begin
        corr[2*d] = digit_neg[d];
      end
    end

    assign pp[DIGITS*PROD_W +: PROD_W] = corr;

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
