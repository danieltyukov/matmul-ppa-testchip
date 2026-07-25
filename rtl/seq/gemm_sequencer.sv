// Copyright 2026 Daniel Tyukov
// SPDX-License-Identifier: Apache-2.0
//
// Output-stationary tiled GEMM sequencer.
//
// One trigger runs the whole MAT_M x MAT_N x MAT_K product without further host
// involvement:
//
//   for mt in 0 .. GRID_M-1:
//     for nt in 0 .. GRID_N-1:
//       clear the accumulator bank
//       for kt in 0 .. GRID_K-1:
//         fetch A tile (mt, kt) and B tile (kt, nt) from the operand stores
//         launch the selected engine, wait for it to retire the tile
//       write the accumulator bank out to the result store
//
// The accumulator for one output tile stays resident across the whole kt loop,
// which is what output stationary means: each A tile is fetched GRID_N times and
// each B tile GRID_M times, but no partial sum ever leaves the chip's registers.
//
// Cycle cost, which the performance counter test checks against measurement:
//
//   FETCH_LEN  = max(TILE_M, TILE_K)
//   per k tile = (FETCH_LEN + 1) + 1 + L          fetch, launch, wait
//   per o tile = 1 + GRID_K * (per k tile) + TILE_M
//   total      = GRID_M * GRID_N * (per o tile)
//
// where L is the engine's launch-to-valid latency (1 for the single cycle
// engines, OPERAND_W for the bit-serial one). The sequencer never assumes L: it
// waits on ready_i and valid_i, which is exactly what lets a candidate be
// multi-cycle without the rest of the chip knowing.

module gemm_sequencer #(
  parameter int unsigned TILE_M    = gemm_pkg::TILE_M,
  parameter int unsigned TILE_N    = gemm_pkg::TILE_N,
  parameter int unsigned TILE_K    = gemm_pkg::TILE_K,
  parameter int unsigned GRID_M    = gemm_pkg::GRID_M,
  parameter int unsigned GRID_N    = gemm_pkg::GRID_N,
  parameter int unsigned GRID_K    = gemm_pkg::GRID_K,
  parameter int unsigned OPERAND_W = gemm_pkg::OPERAND_W,
  parameter int unsigned ACC_W     = gemm_pkg::ACC_W,
  parameter int unsigned A_WORDS   = gemm_pkg::A_WORDS,
  parameter int unsigned B_WORDS   = gemm_pkg::B_WORDS,
  parameter int unsigned C_WORDS   = gemm_pkg::C_WORDS
) (
  input  logic clk_i,
  input  logic rst_ni,

  // Triggers, single cycle pulses.
  input  logic run_i,
  input  logic clear_c_i,
  input  logic clear_sticky_i,   // drop the done flag without starting anything

  output logic busy_o,
  output logic done_o,

  // Operand store core read ports.
  output logic                         a_req_o,
  output logic [$clog2(A_WORDS)-1:0]   a_addr_o,
  input  logic [TILE_K*OPERAND_W-1:0]  a_rdata_i,

  output logic                         b_req_o,
  output logic [$clog2(B_WORDS)-1:0]   b_addr_o,
  input  logic [TILE_N*OPERAND_W-1:0]  b_rdata_i,

  // Result store core write port.
  output logic                         c_req_o,
  output logic                         c_we_o,
  output logic [$clog2(C_WORDS)-1:0]   c_addr_o,
  output logic [TILE_N*ACC_W-1:0]      c_wdata_o,

  // Engine interface.
  output logic                               eng_clear_o,
  output logic                               eng_launch_o,
  output logic [TILE_M*TILE_K*OPERAND_W-1:0] a_tile_o,
  output logic [TILE_K*TILE_N*OPERAND_W-1:0] b_tile_o,
  input  logic                               eng_ready_i,
  input  logic                               eng_valid_i,
  input  logic [TILE_M*TILE_N*ACC_W-1:0]     c_tile_i
);

  localparam int unsigned A_WORD_W = TILE_K * OPERAND_W;
  localparam int unsigned B_WORD_W = TILE_N * OPERAND_W;
  localparam int unsigned C_WORD_W = TILE_N * ACC_W;
  localparam int unsigned A_ADDR_W = $clog2(A_WORDS);
  localparam int unsigned B_ADDR_W = $clog2(B_WORDS);
  localparam int unsigned C_ADDR_W = $clog2(C_WORDS);

  localparam int unsigned FETCH_LEN = gemm_pkg::max_of(TILE_M, TILE_K);
  localparam int unsigned FCNT_W    = $clog2(FETCH_LEN + 2);
  localparam int unsigned WCNT_W    = (TILE_M > 1) ? $clog2(TILE_M) : 1;
  localparam int unsigned MTW       = (GRID_M > 1) ? $clog2(GRID_M) : 1;
  localparam int unsigned NTW       = (GRID_N > 1) ? $clog2(GRID_N) : 1;
  localparam int unsigned KTW       = (GRID_K > 1) ? $clog2(GRID_K) : 1;

  localparam logic [2:0] S_IDLE   = 3'd0;
  localparam logic [2:0] S_INIT   = 3'd1;  // clear the accumulator bank
  localparam logic [2:0] S_FETCH  = 3'd2;  // read the two operand tiles
  localparam logic [2:0] S_LAUNCH = 3'd3;  // hand the tile to the engine
  localparam logic [2:0] S_WAIT   = 3'd4;  // engine is retiring the tile
  localparam logic [2:0] S_WB     = 3'd5;  // write the output tile out
  localparam logic [2:0] S_CLR    = 3'd6;  // zero the whole result store

  logic [2:0]        state_q;
  logic [2:0]        state_d;
  logic [MTW-1:0]    mt_q;
  logic [NTW-1:0]    nt_q;
  logic [KTW-1:0]    kt_q;
  logic [FCNT_W-1:0] fcnt_q;
  logic [WCNT_W-1:0] wcnt_q;
  logic [C_ADDR_W:0] clr_q;
  logic              done_q;

  logic [TILE_M*A_WORD_W-1:0] a_tile_q;
  logic [TILE_K*B_WORD_W-1:0] b_tile_q;

  logic last_kt;
  logic last_nt;
  logic last_mt;
  logic fetch_done;
  logic wb_done;
  logic clr_done;

  assign last_kt    = (32'(kt_q)   == GRID_K - 1);
  assign last_nt    = (32'(nt_q)   == GRID_N - 1);
  assign last_mt    = (32'(mt_q)   == GRID_M - 1);
  assign fetch_done = (32'(fcnt_q) == FETCH_LEN);
  assign wb_done    = (32'(wcnt_q) == TILE_M - 1);
  assign clr_done   = (32'(clr_q)  == C_WORDS - 1);

  // ---------------------------------------------------------------------------
  // Next state
  // ---------------------------------------------------------------------------
  always_comb begin
    state_d = state_q;
    case (state_q)
      S_IDLE:   if (run_i)          state_d = S_INIT;
                else if (clear_c_i) state_d = S_CLR;
      S_INIT:                       state_d = S_FETCH;
      S_FETCH:  if (fetch_done)     state_d = S_LAUNCH;
      S_LAUNCH: if (eng_ready_i)    state_d = S_WAIT;
      S_WAIT:   if (eng_valid_i)    state_d = last_kt ? S_WB : S_FETCH;
      S_WB:     if (wb_done)        state_d = (last_mt && last_nt) ? S_IDLE : S_INIT;
      S_CLR:    if (clr_done)       state_d = S_IDLE;
      default:                      state_d = S_IDLE;
    endcase
  end

  // ---------------------------------------------------------------------------
  // Loop counters
  // ---------------------------------------------------------------------------
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      state_q <= S_IDLE;
      mt_q    <= '0;
      nt_q    <= '0;
      kt_q    <= '0;
      fcnt_q  <= '0;
      wcnt_q  <= '0;
      clr_q   <= '0;
      done_q  <= 1'b0;
    end else begin
      state_q <= state_d;

      if (clear_sticky_i) done_q <= 1'b0;

      case (state_q)
        S_IDLE: begin
          if (run_i || clear_c_i) begin
            mt_q   <= '0;
            nt_q   <= '0;
            kt_q   <= '0;
            wcnt_q <= '0;
            clr_q  <= '0;
            done_q <= 1'b0;
          end
        end

        S_INIT: fcnt_q <= '0;

        S_FETCH: fcnt_q <= fetch_done ? fcnt_q : (fcnt_q + 1'b1);

        S_WAIT: begin
          if (eng_valid_i) begin
            if (last_kt) begin
              kt_q   <= '0;
              wcnt_q <= '0;
            end else begin
              kt_q   <= kt_q + 1'b1;
              fcnt_q <= '0;
            end
          end
        end

        S_WB: begin
          if (wb_done) begin
            wcnt_q <= '0;
            if (last_nt) begin
              nt_q <= '0;
              if (last_mt) done_q <= 1'b1;
              else         mt_q   <= mt_q + 1'b1;
            end else begin
              nt_q <= nt_q + 1'b1;
            end
          end else begin
            wcnt_q <= wcnt_q + 1'b1;
          end
        end

        S_CLR: begin
          if (clr_done) done_q <= 1'b1;
          else          clr_q  <= clr_q + 1'b1;
        end

        default: ;
      endcase
    end
  end

  // A trigger cannot arrive on two consecutive core cycles, because the SPI byte
  // that carries it takes at least eight SPI clocks and f_spi <= f_core/8.
  assign busy_o = (state_q != S_IDLE);
  assign done_o = done_q;

  // ---------------------------------------------------------------------------
  // Operand fetch
  //
  // One A word is one TILE_K wide slice of an A row, so row (mt*TILE_M + i) with
  // k tile kt lives at word (mt*TILE_M + i) * GRID_K + kt. B is the same shape
  // with GRID_N. Requests go out on cycles 0 .. FETCH_LEN-1 of S_FETCH and the
  // answers land one cycle later, so the state lasts FETCH_LEN + 1 cycles.
  // ---------------------------------------------------------------------------
  logic              in_fetch;
  logic [FCNT_W-1:0] cap_idx;

  assign in_fetch = (state_q == S_FETCH);
  assign cap_idx  = fcnt_q - 1'b1;

  assign a_req_o = in_fetch && (32'(fcnt_q) < TILE_M);
  assign b_req_o = in_fetch && (32'(fcnt_q) < TILE_K);

  assign a_addr_o =
      A_ADDR_W'(((32'(mt_q) * TILE_M + 32'(fcnt_q)) * GRID_K) + 32'(kt_q));
  assign b_addr_o =
      B_ADDR_W'(((32'(kt_q) * TILE_K + 32'(fcnt_q)) * GRID_N) + 32'(nt_q));

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      a_tile_q <= '0;
      b_tile_q <= '0;
    end else if (in_fetch && (fcnt_q != '0)) begin
      if (32'(cap_idx) < TILE_M) begin
        a_tile_q[32'(cap_idx) * A_WORD_W +: A_WORD_W] <= a_rdata_i;
      end
      if (32'(cap_idx) < TILE_K) begin
        b_tile_q[32'(cap_idx) * B_WORD_W +: B_WORD_W] <= b_rdata_i;
      end
    end
  end

  assign a_tile_o = a_tile_q;
  assign b_tile_o = b_tile_q;

  // ---------------------------------------------------------------------------
  // Engine handshake
  // ---------------------------------------------------------------------------
  assign eng_clear_o  = (state_q == S_INIT);
  assign eng_launch_o = (state_q == S_LAUNCH) && eng_ready_i;

  // ---------------------------------------------------------------------------
  // Result write back, and result store clearing
  // ---------------------------------------------------------------------------
  logic [C_ADDR_W-1:0] wb_addr;

  assign wb_addr =
      C_ADDR_W'(((32'(mt_q) * TILE_M + 32'(wcnt_q)) * GRID_N) + 32'(nt_q));

  assign c_req_o  = (state_q == S_WB) || (state_q == S_CLR);
  assign c_we_o   = (state_q == S_WB) || (state_q == S_CLR);
  assign c_addr_o = (state_q == S_CLR) ? clr_q[C_ADDR_W-1:0] : wb_addr;

  always_comb begin
    if (state_q == S_CLR) c_wdata_o = '0;
    else                  c_wdata_o = c_tile_i[32'(wcnt_q) * C_WORD_W +: C_WORD_W];
  end

endmodule
