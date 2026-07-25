// Copyright 2026 Daniel Tyukov
// SPDX-License-Identifier: Apache-2.0
//
// Multiply-accumulate operation counter.
//
// The engine interface reports mac_tick as a count of MACs retired in the current
// cycle rather than a single pulse. That keeps the counter honest for candidates
// that retire a partial tile per cycle, and it makes the expected total purely a
// property of the workload: MAT_M * MAT_N * MAT_K, independent of which candidate
// ran or how many cycles it took. Cycles divided by MACs is then a real
// throughput number.
//
// Saturates rather than wrapping.

module mac_meter #(
  parameter int unsigned WIDTH  = 32,
  parameter int unsigned TICK_W = 7
) (
  input  logic              clk_i,
  input  logic              rst_ni,
  input  logic              clear_i,
  input  logic [TICK_W-1:0] tick_i,
  output logic [WIDTH-1:0]  count_o
);

  logic [WIDTH-1:0] count_q;
  logic [WIDTH-1:0] next_count;
  logic             would_overflow;

  assign next_count     = count_q + {{(WIDTH-TICK_W){1'b0}}, tick_i};
  assign would_overflow = (next_count < count_q);

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni)             count_q <= '0;
    else if (clear_i)        count_q <= '0;
    else if (would_overflow) count_q <= {WIDTH{1'b1}};
    else                     count_q <= next_count;
  end

  assign count_o = count_q;

endmodule
