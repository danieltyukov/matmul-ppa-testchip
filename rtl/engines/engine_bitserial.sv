// Copyright 2026 Daniel Tyukov
// SPDX-License-Identifier: Apache-2.0
//
// Candidate 4: bit-serial MAC array, the extreme area point.
//
// There is no multiplier here at all. Operand B is consumed one bit plane at a
// time, most significant first, and the dot product is built up by Horner's
// method. For each output element (m, n):
//
//   P_j = sum_k A[m][k] * B[k][n][j]        (a signed sum of selected A values)
//   r   = -P_{W-1}                          (first step, the sign bit plane)
//   r   = 2*r + P_j        for j = W-2 .. 0
//
// which reproduces sum_k A[m][k] * B[k][n] exactly because
// B = -b[W-1]*2**(W-1) + sum_{j<W-1} b[j]*2**j. The doubling is a hard wired
// shift, so the only arithmetic per cycle is one narrow signed adder tree plus
// one wider add, at the cost of OPERAND_W cycles per tile launch instead of one.
//
// This candidate is the reason the engine interface carries ready_o and valid_o
// rather than a fixed latency: it takes OPERAND_W cycles and the sequencer has to
// cope with that without knowing which engine is selected.
//
// Implements the candidate engine interface documented in
// docs/ADDING_A_CANDIDATE.md. Every candidate in this directory has this exact
// port list.

module engine_bitserial #(
  parameter int unsigned TILE_M     = gemm_pkg::TILE_M,
  parameter int unsigned TILE_N     = gemm_pkg::TILE_N,
  parameter int unsigned TILE_K     = gemm_pkg::TILE_K,
  parameter int unsigned OPERAND_W  = gemm_pkg::OPERAND_W,
  parameter int unsigned ACC_W      = gemm_pkg::ACC_W,
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
  localparam int unsigned STEP_W   = (OPERAND_W > 1) ? $clog2(OPERAND_W) : 1;

  localparam logic [31:0] MACS_PER_TILE = TILE_M * TILE_N * TILE_K;

  // ---------------------------------------------------------------------------
  // Bit plane sequencer.
  //
  // The first bit plane is consumed in the launch cycle itself, so the total
  // latency from launch to valid_o is exactly OPERAND_W cycles. step_eff is the
  // multiplier bit index being consumed this cycle: it walks from the sign bit
  // down to bit 0.
  // ---------------------------------------------------------------------------
  localparam logic [31:0] STEP_TOP_FULL = OPERAND_W - 1;
  localparam logic [STEP_W-1:0] STEP_TOP = STEP_TOP_FULL[STEP_W-1:0];

  logic              busy_q;
  logic [STEP_W-1:0] step_q;
  logic [STEP_W-1:0] step_eff;
  logic              active;
  logic              first_step;
  logic              last_step;
  logic              valid_q;

  assign ready_o    = !busy_q;
  assign active     = busy_q || launch_i;
  assign step_eff   = busy_q ? step_q : STEP_TOP;
  assign first_step = active && !busy_q;
  assign last_step  = active && (step_eff == {STEP_W{1'b0}});

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      busy_q  <= 1'b0;
      step_q  <= '0;
      valid_q <= 1'b0;
    end else begin
      valid_q <= last_step;
      if (active) begin
        if (last_step) begin
          busy_q <= 1'b0;
        end else begin
          busy_q <= 1'b1;
          step_q <= step_eff - 1'b1;
        end
      end
    end
  end

  assign valid_o = valid_q;

  // ---------------------------------------------------------------------------
  // One Horner register per output element.
  // ---------------------------------------------------------------------------
  logic [N_ELEM*DOT_W-1:0] horner_next;

  for (genvar m = 0; m < TILE_M; m++) begin : gen_row
    for (genvar n = 0; n < TILE_N; n++) begin : gen_col
      localparam int unsigned E = (m * TILE_N) + n;

      // Partial sum width: TILE_K signed operands, so OPERAND_W + clog2(TILE_K)
      // bits are enough to hold sum_k A[m][k] * bit.
      localparam int unsigned PSUM_W =
          OPERAND_W + ((TILE_K > 1) ? $clog2(TILE_K) : 0) + 1;

      logic [OP_SLICE-1:0]      a_row;
      logic [TILE_K-1:0]        b_bits;
      logic signed [PSUM_W-1:0] psum;
      logic signed [DOT_W-1:0]  horner_q;
      logic signed [DOT_W-1:0]  psum_sext;
      logic signed [DOT_W-1:0]  next_val;
      integer                   k;

      assign a_row = a_tile_i[m*OP_SLICE +: OP_SLICE];

      // The bit plane currently selected out of column n of B.
      for (genvar kk = 0; kk < TILE_K; kk++) begin : gen_bitsel
        assign b_bits[kk] =
            b_tile_i[(((kk*TILE_N) + n)*OPERAND_W) + {{(32-STEP_W){1'b0}}, step_eff}];
      end

      // P_j: signed sum of the A elements whose multiplier bit is set.
      always_comb begin
        psum = '0;
        for (k = 0; k < TILE_K; k = k + 1) begin
          if (b_bits[k]) begin
            psum = psum + PSUM_W'($signed(a_row[k*OPERAND_W +: OPERAND_W]));
          end
        end
      end

      assign psum_sext = {{(DOT_W-PSUM_W){psum[PSUM_W-1]}}, psum};

      // First step negates (that is the sign bit plane), later steps double and
      // add. Doubling is a wired shift, not an adder.
      assign next_val = first_step ? -psum_sext
                                   : ((horner_q <<< 1) + psum_sext);

      always_ff @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni)      horner_q <= '0;
        else if (active)  horner_q <= next_val;
      end

      assign horner_next[E*DOT_W +: DOT_W] = next_val;
    end
  end

  // On the last bit plane the freshly computed Horner value goes straight into
  // the accumulator, so no extra cycle is spent parking it in horner_q.
  acc_bank #(
    .N_ELEM (N_ELEM),
    .DOT_W  (DOT_W),
    .ACC_W  (ACC_W)
  ) u_acc (
    .clk_i    (clk_i),
    .rst_ni   (rst_ni),
    .clear_i  (acc_clear_i),
    .add_en_i (last_step),
    .dots_i   (horner_next),
    .acc_o    (c_tile_o)
  );

  assign mac_tick_o = valid_q ? MACS_PER_TILE[MAC_TICK_W-1:0]
                              : {MAC_TICK_W{1'b0}};

endmodule
