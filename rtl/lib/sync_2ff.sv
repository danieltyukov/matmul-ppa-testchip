// Copyright 2026 Daniel Tyukov
// SPDX-License-Identifier: Apache-2.0
//
// Two flop synchroniser for a slow asynchronous input. Used on the SPI pins,
// which are oversampled in the core clock domain rather than clocked directly.

module sync_2ff #(
  parameter int unsigned WIDTH = 1,
  parameter logic        RESET_VALUE = 1'b0
) (
  input  logic             clk_i,
  input  logic             rst_ni,
  input  logic [WIDTH-1:0] d_i,
  output logic [WIDTH-1:0] q_o
);

  logic [WIDTH-1:0] stage0_q;
  logic [WIDTH-1:0] stage1_q;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      stage0_q <= {WIDTH{RESET_VALUE}};
      stage1_q <= {WIDTH{RESET_VALUE}};
    end else begin
      stage0_q <= d_i;
      stage1_q <= stage0_q;
    end
  end

  assign q_o = stage1_q;

endmodule
