// Copyright 2026 Daniel Tyukov
// SPDX-License-Identifier: Apache-2.0
//
// Asynchronous assert, synchronous de-assert reset bridge. The external reset
// pin is asynchronous to the core clock; every sequential element in the core
// uses the output of this module so that reset release is clean.
//
// STAGES flops are held in reset by the async input and shift in a 1 once the
// pin releases, so de-assertion is aligned to the core clock.

module reset_bridge #(
  parameter int unsigned STAGES = 3
) (
  input  logic clk_i,
  input  logic ext_rst_ni,
  output logic rst_no
);

  logic [STAGES-1:0] chain_q;

  always_ff @(posedge clk_i or negedge ext_rst_ni) begin
    if (!ext_rst_ni) chain_q <= '0;
    else             chain_q <= {chain_q[STAGES-2:0], 1'b1};
  end

  assign rst_no = chain_q[STAGES-1];

endmodule
