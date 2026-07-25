// Copyright 2026 Daniel Tyukov
// SPDX-License-Identifier: Apache-2.0
//
// Output-stationary accumulator bank, shared by every candidate engine.
//
// One ACC_W wide register per output tile element. The tile stays resident here
// while K tiles stream past, which is what makes the dataflow output stationary.
// Keeping this block common to all candidates means the measured differences
// between candidates come from their arithmetic, not from different accumulator
// implementations.
//
// clear_i wins over add_en_i, so a clear and a launch in the same cycle leaves
// the bank zeroed.

module acc_bank #(
  parameter int unsigned N_ELEM = 16,
  parameter int unsigned DOT_W  = 18,
  parameter int unsigned ACC_W  = 32
) (
  input  logic                    clk_i,
  input  logic                    rst_ni,
  input  logic                    clear_i,
  input  logic                    add_en_i,
  input  logic [N_ELEM*DOT_W-1:0] dots_i,
  output logic [N_ELEM*ACC_W-1:0] acc_o
);

  for (genvar e = 0; e < N_ELEM; e++) begin : gen_elem
    logic [ACC_W-1:0] acc_q;
    logic [ACC_W-1:0] dot_sext;

    assign dot_sext = {{(ACC_W-DOT_W){dots_i[e*DOT_W + DOT_W - 1]}},
                       dots_i[e*DOT_W +: DOT_W]};

    always_ff @(posedge clk_i or negedge rst_ni) begin
      if (!rst_ni)         acc_q <= '0;
      else if (clear_i)    acc_q <= '0;
      else if (add_en_i)   acc_q <= acc_q + dot_sext;
    end

    assign acc_o[e*ACC_W +: ACC_W] = acc_q;
  end

endmodule
