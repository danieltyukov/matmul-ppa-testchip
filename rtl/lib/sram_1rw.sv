// Copyright 2026 Daniel Tyukov
// SPDX-License-Identifier: Apache-2.0
//
// Single port synchronous SRAM technology wrapper.
//
// This is the one intentional technology boundary in the design. The default
// body is a synthesisable behavioural array, which lets the whole chip simulate
// and synthesise with nothing but Verilator, Icarus and Yosys. Define
// GEMM_SRAM_MACRO to swap in PDK macros; the port list here is the contract.
//
// Read latency is one cycle. A read issued in cycle n returns data in cycle
// n + 1. Read and write share the address port, so a write blocks a read in the
// same cycle. Everything upstream of this module is written to respect that.
//
// Byte enables are per WORD_W/8 lane. WORD_W must be a multiple of 8.

module sram_1rw #(
  parameter int unsigned WORDS  = 256,
  parameter int unsigned WORD_W = 32,
  // Set for the macro-backed build so the wrapper knows its instance name.
  parameter              MACRO_NAME = "behavioural"
) (
  input  logic                      clk_i,
  input  logic                      rst_ni,
  input  logic                      req_i,
  input  logic                      we_i,
  input  logic [$clog2(WORDS)-1:0]  addr_i,
  input  logic [WORD_W-1:0]         wdata_i,
  input  logic [(WORD_W/8)-1:0]     wstrb_i,
  output logic [WORD_W-1:0]         rdata_o
);

  localparam int unsigned LANES = WORD_W / 8;

`ifndef YOSYS
  initial begin : p_check_word_width
    if (WORD_W % 8 != 0) begin
      $fatal(1, "sram_1rw: WORD_W (%0d) must be a multiple of 8", WORD_W);
    end
  end
`endif

`ifdef GEMM_SRAM_MACRO
  // Bind PDK macros here. IHP SG13G2 ships RM_IHPSG13_1P_* single port cuts
  // with bit write enables; a real binding stitches together as many cuts as
  // WORDS x WORD_W needs and maps wstrb_i onto the bit write mask.
  gemm_sram_macro_bind #(
    .WORDS  (WORDS),
    .WORD_W (WORD_W)
  ) u_macro (
    .clk_i, .rst_ni, .req_i, .we_i, .addr_i, .wdata_i, .wstrb_i, .rdata_o
  );
`else
  logic [WORD_W-1:0] mem [WORDS];
  logic [WORD_W-1:0] rdata_q;

  always_ff @(posedge clk_i) begin
    if (req_i) begin
      if (we_i) begin
        for (int unsigned l = 0; l < LANES; l++) begin
          if (wstrb_i[l]) mem[addr_i][l*8 +: 8] <= wdata_i[l*8 +: 8];
        end
      end else begin
        rdata_q <= mem[addr_i];
      end
    end
  end

  assign rdata_o = rdata_q;

  // rst_ni is part of the wrapper contract because PDK macros often need it for
  // retention or built-in self test control. The behavioural body does not.
  logic unused_rst;
  assign unused_rst = rst_ni;
`endif

endmodule
