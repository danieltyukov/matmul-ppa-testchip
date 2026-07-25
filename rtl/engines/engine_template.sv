// Copyright 2026 Daniel Tyukov
// SPDX-License-Identifier: Apache-2.0
//
// TEMPLATE, not part of the chip.
//
// Copy this file to rtl/engines/engine_<yourname>.sv, rename the module, replace the
// arithmetic, and follow docs/ADDING_A_CANDIDATE.md to register it. It is left out of
// rtl/filelist.f on purpose, so it does not appear in any measurement, but it is
// linted by `make lint-template` and it is a working single cycle candidate as
// written: what it computes is correct, it is just the same inferred multiply as
// candidate 0, so measuring it would tell you nothing.
//
// Everything below the "your arithmetic goes here" marker is the part you replace.
// Everything above it is the contract, and changing it will make the harness, the
// sequencer or the tests reject your candidate.

module engine_template #(
  parameter int unsigned TILE_M     = gemm_pkg::TILE_M,
  parameter int unsigned TILE_N     = gemm_pkg::TILE_N,
  parameter int unsigned TILE_K     = gemm_pkg::TILE_K,
  parameter int unsigned OPERAND_W  = gemm_pkg::OPERAND_W,
  parameter int unsigned ACC_W      = gemm_pkg::ACC_W,
  parameter int unsigned DOT_W      = gemm_pkg::DOT_W,
  parameter int unsigned MAC_TICK_W = gemm_pkg::MAC_TICK_W
) (
  input  logic                                clk_i,        // gated by engine_array
  input  logic                                rst_ni,       // async assert, sync release
  input  logic                                acc_clear_i,  // one cycle pulse
  input  logic                                launch_i,     // one cycle pulse
  input  logic [TILE_M*TILE_K*OPERAND_W-1:0]  a_tile_i,     // stable until valid_o
  input  logic [TILE_K*TILE_N*OPERAND_W-1:0]  b_tile_i,     // stable until valid_o
  output logic [TILE_M*TILE_N*ACC_W-1:0]      c_tile_o,     // held until next launch
  output logic                                ready_o,      // launch_i is accepted
  output logic                                valid_o,      // one cycle pulse
  output logic [MAC_TICK_W-1:0]               mac_tick_o    // MACs retired this cycle
);

  localparam int unsigned N_ELEM   = TILE_M * TILE_N;
  localparam int unsigned OP_SLICE = TILE_K * OPERAND_W;

  localparam logic [31:0] MACS_PER_TILE = TILE_M * TILE_N * TILE_K;

  logic [N_ELEM*DOT_W-1:0] dots;

  // ---------------------------------------------------------------------------
  // Operand unpacking. Element (r, c) of an R x C tile lives at bit offset
  // ((r * C) + c) * ELEM_W, with element (0, 0) in the least significant bits. Row m
  // of A is contiguous; column n of B is strided by TILE_N.
  //
  // Flat vectors rather than packed multi-dimensional arrays, because Yosys 0.33 does
  // not parse packed multi-dimensional declarations. See the portability table in
  // docs/ADDING_A_CANDIDATE.md before reaching for a nicer looking type.
  // ---------------------------------------------------------------------------
  for (genvar m = 0; m < TILE_M; m++) begin : gen_row
    for (genvar n = 0; n < TILE_N; n++) begin : gen_col
      logic [OP_SLICE-1:0] a_row;
      logic [OP_SLICE-1:0] b_col;

      assign a_row = a_tile_i[m*OP_SLICE +: OP_SLICE];

      for (genvar k = 0; k < TILE_K; k++) begin : gen_gather
        assign b_col[k*OPERAND_W +: OPERAND_W] =
            b_tile_i[((k*TILE_N) + n)*OPERAND_W +: OPERAND_W];
      end

      // -----------------------------------------------------------------------
      // YOUR ARITHMETIC GOES HERE.
      //
      // Compute sum over k of signed(a_row[k]) * signed(b_col[k]) into a DOT_W wide
      // two's complement value. It must be bit-exact: the equivalence tests compare
      // your candidate against the other four and against NumPy, and an approximate
      // multiplier will fail them, correctly.
      //
      // rtl/engines/csa_reduce.sv is a parameterised Wallace 3:2 reduction tree you
      // can reuse. dot_wallace.sv, dot_booth4.sv and dot_signmag.sv are three worked
      // examples of using it.
      // -----------------------------------------------------------------------
      logic signed [DOT_W-1:0] dot;
      integer k_acc;

      always_comb begin
        dot = '0;
        for (k_acc = 0; k_acc < TILE_K; k_acc = k_acc + 1) begin
          dot = dot + $signed(a_row[k_acc*OPERAND_W +: OPERAND_W])
                    * $signed(b_col[k_acc*OPERAND_W +: OPERAND_W]);
        end
      end

      assign dots[((m*TILE_N) + n)*DOT_W +: DOT_W] = dot;
    end
  end

  // ---------------------------------------------------------------------------
  // Shared accumulator bank. Using it rather than writing your own keeps the
  // accumulators out of the comparison, so the measured difference between your
  // candidate and the others is your arithmetic and nothing else.
  // ---------------------------------------------------------------------------
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

  // ---------------------------------------------------------------------------
  // Handshake. This is the single cycle form: always ready, valid one cycle after
  // launch. For a multi-cycle candidate, drop ready_o while busy and raise valid_o on
  // the cycle the accumulator absorbs the tile; engine_bitserial.sv is the worked
  // example, and gemm_sequencer never assumes a latency either way.
  //
  // Whatever latency you end up with, put it in gemm_model.ENGINE_LATENCY.
  // test_engine_exact measures it and fails if the two disagree.
  // ---------------------------------------------------------------------------
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
