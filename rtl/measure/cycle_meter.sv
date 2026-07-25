// Copyright 2026 Daniel Tyukov
// SPDX-License-Identifier: Apache-2.0
//
// Cycle counter. Counts core clock cycles while the sequencer is not idle, so a
// readback after a run is the exact cost of that run and nothing else. Saturates
// instead of wrapping, because a wrapped counter reads like a fast run.
//
// A run trigger clears the counter, so the host does not have to remember to.

module cycle_meter #(
  parameter int unsigned WIDTH = 32
) (
  input  logic             clk_i,
  input  logic             rst_ni,
  input  logic             clear_i,
  input  logic             enable_i,
  output logic [WIDTH-1:0] count_o
);

  logic [WIDTH-1:0] count_q;
  logic             saturated;

  assign saturated = (count_q == {WIDTH{1'b1}});

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni)                            count_q <= '0;
    else if (clear_i)                       count_q <= '0;
    else if (enable_i && !saturated)        count_q <= count_q + 1'b1;
  end

  assign count_o = count_q;

endmodule
