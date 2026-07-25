// Copyright 2026 Daniel Tyukov
// SPDX-License-Identifier: Apache-2.0
//
// On-chip result comparator.
//
// Walks the result store and the reference store in lockstep, one word from each
// per cycle, and compares them element by element. Reports how many output
// elements differ and the row-major index of the first one.
//
// This exists so that silicon bring-up does not depend on reading 4 KB back out
// over SPI at every step: load a reference matrix once, trigger a run, trigger a
// verify, and read one status bit. It also means a mismatch is localised on chip,
// which matters when the failure only shows up at a corner voltage.
//
// The two stores are separate SRAMs, so both reads happen in the same cycle and
// the walk costs C_WORDS + 2 cycles.

module result_checker #(
  parameter int unsigned MAT_N   = gemm_pkg::MAT_N,
  parameter int unsigned TILE_N  = gemm_pkg::TILE_N,
  parameter int unsigned GRID_N  = gemm_pkg::GRID_N,
  parameter int unsigned ACC_W   = gemm_pkg::ACC_W,
  parameter int unsigned C_WORDS = gemm_pkg::C_WORDS
) (
  input  logic clk_i,
  input  logic rst_ni,

  input  logic start_i,
  input  logic clear_sticky_i,   // drop done and the mismatch flags
  output logic busy_o,
  output logic done_o,

  // Result store core read port.
  output logic                         c_req_o,
  output logic [$clog2(C_WORDS)-1:0]   c_addr_o,
  input  logic [TILE_N*ACC_W-1:0]      c_rdata_i,

  // Reference store core read port.
  output logic                         ref_req_o,
  output logic [$clog2(C_WORDS)-1:0]   ref_addr_o,
  input  logic [TILE_N*ACC_W-1:0]      ref_rdata_i,

  output logic        mismatch_o,           // sticky, any element differed
  output logic [15:0] mismatch_count_o,     // saturating element count
  output logic [15:0] first_mismatch_o      // row-major index of the first one
);

  localparam int unsigned ADDR_W = $clog2(C_WORDS);
  localparam int unsigned NTW    = (GRID_N > 1) ? $clog2(GRID_N) : 1;

  localparam logic [1:0] S_IDLE = 2'd0;
  localparam logic [1:0] S_WALK = 2'd1;  // issuing reads
  localparam logic [1:0] S_TAIL = 2'd2;  // draining the last read

  logic [1:0]        state_q;
  logic [ADDR_W-1:0] addr_q;
  logic [ADDR_W-1:0] cmp_addr_q;   // address whose data is on the bus now
  logic              cmp_valid_q;
  logic              done_q;
  logic              mismatch_q;
  logic [15:0]       count_q;
  logic [15:0]       first_q;
  logic              first_seen_q;

  logic last_word;
  assign last_word = (32'(addr_q) == C_WORDS - 1);

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      state_q     <= S_IDLE;
      addr_q      <= '0;
      cmp_addr_q  <= '0;
      cmp_valid_q <= 1'b0;
      done_q      <= 1'b0;
    end else begin
      cmp_valid_q <= (state_q == S_WALK);
      cmp_addr_q  <= addr_q;

      if (clear_sticky_i) done_q <= 1'b0;

      case (state_q)
        S_IDLE: begin
          if (start_i) begin
            state_q <= S_WALK;
            addr_q  <= '0;
            done_q  <= 1'b0;
          end
        end
        S_WALK: begin
          if (last_word) state_q <= S_TAIL;
          else           addr_q  <= addr_q + 1'b1;
        end
        S_TAIL: begin
          state_q <= S_IDLE;
          done_q  <= 1'b1;
        end
        default: state_q <= S_IDLE;
      endcase
    end
  end

  assign c_req_o    = (state_q == S_WALK);
  assign c_addr_o   = addr_q;
  assign ref_req_o  = (state_q == S_WALK);
  assign ref_addr_o = addr_q;

  assign busy_o = (state_q != S_IDLE);
  assign done_o = done_q;

  // ---------------------------------------------------------------------------
  // Element comparison
  //
  // Word cmp_addr_q holds row (cmp_addr_q / GRID_N), n tile (cmp_addr_q % GRID_N).
  // GRID_N is a power of two in every supported configuration, so both are wire
  // slices. Lane j of the word is output column nt*TILE_N + j.
  // ---------------------------------------------------------------------------
  logic [TILE_N-1:0] lane_diff;
  logic [31:0]       row_index;
  logic [31:0]       ntile_index;

  for (genvar j = 0; j < TILE_N; j++) begin : gen_lane_cmp
    assign lane_diff[j] = cmp_valid_q
                       && (c_rdata_i[j*ACC_W +: ACC_W] != ref_rdata_i[j*ACC_W +: ACC_W]);
  end

  if (GRID_N > 1) begin : gen_row_split
    assign ntile_index = 32'(cmp_addr_q[NTW-1:0]);
    assign row_index   = 32'(cmp_addr_q[ADDR_W-1:NTW]);
  end else begin : gen_row_single
    assign ntile_index = 32'd0;
    assign row_index   = 32'(cmp_addr_q);
  end

  // Index of the lowest numbered differing element in this word. The result store
  // holds MAT_M*MAT_N elements, which fits the 16 bit status field for every
  // configuration this chip supports, so the index is computed 16 bits wide.
  logic [15:0] word_first_index;
  logic        word_any_diff;
  integer      j_scan;
  logic        found;

  always_comb begin
    word_first_index = 16'd0;
    word_any_diff    = 1'b0;
    found            = 1'b0;
    for (j_scan = 0; j_scan < TILE_N; j_scan = j_scan + 1) begin
      if (lane_diff[j_scan] && !found) begin
        found            = 1'b1;
        word_any_diff    = 1'b1;
        word_first_index = 16'((row_index * MAT_N) + (ntile_index * TILE_N)
                               + 32'(j_scan));
      end
    end
  end

  // Number of differing elements in this word, added to the running total.
  logic [31:0] word_diff_count;
  integer      j_cnt;

  always_comb begin
    word_diff_count = 32'd0;
    for (j_cnt = 0; j_cnt < TILE_N; j_cnt = j_cnt + 1) begin
      if (lane_diff[j_cnt]) word_diff_count = word_diff_count + 32'd1;
    end
  end

  logic [31:0] next_count;
  assign next_count = 32'(count_q) + word_diff_count;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      mismatch_q   <= 1'b0;
      count_q      <= '0;
      first_q      <= '0;
      first_seen_q <= 1'b0;
    end else if (start_i || clear_sticky_i) begin
      mismatch_q   <= 1'b0;
      count_q      <= '0;
      first_q      <= '0;
      first_seen_q <= 1'b0;
    end else if (word_any_diff) begin
      mismatch_q <= 1'b1;
      count_q    <= (next_count > 32'hFFFF) ? 16'hFFFF : next_count[15:0];
      if (!first_seen_q) begin
        first_seen_q <= 1'b1;
        first_q      <= word_first_index;
      end
    end
  end

  assign mismatch_o       = mismatch_q;
  assign mismatch_count_o = count_q;
  assign first_mismatch_o = first_q;

endmodule
