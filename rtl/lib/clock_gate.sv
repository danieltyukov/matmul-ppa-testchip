// Copyright 2026 Daniel Tyukov
// SPDX-License-Identifier: Apache-2.0
//
// Integrated clock gate. The behavioural body is a negative level sensitive
// latch on the enable followed by an AND, which is the structure every standard
// cell ICG implements. Define GEMM_ICG_MACRO to bind a real PDK cell instead;
// the port list below is the contract that binding must satisfy.
//
// This is the only place in the design where a clock is combinationally
// modified. Every engine gets one instance so that the engines that are not
// selected see a stopped clock and contribute no switching activity.

module clock_gate (
  input  logic clk_i,
  input  logic enable_i,
  input  logic test_enable_i,  // scan or characterisation bypass, ties enable high
  output logic clk_o
);

`ifdef GEMM_ICG_MACRO
  sg13g2_slgcp_1 u_icg (
    .CLK  (clk_i),
    .GATE (~(enable_i | test_enable_i)),
    .SCE  (1'b0),
    .GCLK (clk_o)
  );
`else
  logic enable_latched;

  // Latch the enable while the clock is low so that clk_o never truncates or
  // stretches a pulse.
  always_latch begin
    if (!clk_i) enable_latched = enable_i | test_enable_i;
  end

  assign clk_o = clk_i & enable_latched;
`endif

endmodule
