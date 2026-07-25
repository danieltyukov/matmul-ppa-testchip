// Copyright 2026 Daniel Tyukov
// SPDX-License-Identifier: Apache-2.0
//
// One matrix held in a single-port SRAM, with two views onto it.
//
//  - The core port is word wide. Words are cut so that one word is exactly the
//    slice of a matrix row that a tile fetch needs, which is why a tile fetch
//    costs TILE_M (or TILE_K) reads and not one magic wide access.
//  - The host port is byte wide and byte addressed, and the byte address is the
//    plain row-major element index of the matrix. Word and lane fall out of that
//    by division and remainder, both powers of two in every supported
//    configuration.
//
// The SRAM has one port, so the two views are arbitrated with fixed priority to
// the core. The host side is expected not to compete: frame_router.sv rejects
// host memory access while the core is busy and reports it in the status byte.
// Nothing here silently drops a host access, because the arbitration decision
// lives one level up where it can be reported.
//
// Read latency is one cycle on both views.

module matrix_store #(
  parameter int unsigned WORDS  = 256,
  parameter int unsigned WORD_W = 32
) (
  input  logic                                    clk_i,
  input  logic                                    rst_ni,

  // Core word port.
  input  logic                                    core_req_i,
  input  logic                                    core_we_i,
  input  logic [$clog2(WORDS)-1:0]                core_addr_i,
  input  logic [WORD_W-1:0]                       core_wdata_i,
  output logic [WORD_W-1:0]                       core_rdata_o,

  // Host byte port.
  input  logic                                    host_req_i,
  input  logic                                    host_we_i,
  input  logic [$clog2(WORDS*(WORD_W/8))-1:0]     host_baddr_i,
  input  logic [7:0]                              host_wdata_i,
  output logic [7:0]                              host_rdata_o
);

  localparam int unsigned LANES    = WORD_W / 8;
  localparam int unsigned LANE_W   = (LANES > 1) ? $clog2(LANES) : 1;
  localparam int unsigned WADDR_W  = $clog2(WORDS);

  logic [WADDR_W-1:0]  sram_addr;
  logic [WORD_W-1:0]   sram_wdata;
  logic [LANES-1:0]    sram_wstrb;
  logic [WORD_W-1:0]   sram_rdata;
  logic                sram_req;
  logic                sram_we;

  logic [WADDR_W-1:0]  host_waddr;
  logic [LANE_W-1:0]   host_lane;
  logic [LANE_W-1:0]   host_lane_q;

  // Byte address split. LANES is a power of two in every configuration this
  // design supports, so these are wire slices, not dividers.
  if (LANES > 1) begin : gen_lane_split
    assign host_lane  = host_baddr_i[LANE_W-1:0];
    assign host_waddr = host_baddr_i[LANE_W+WADDR_W-1:LANE_W];
  end else begin : gen_no_lane
    assign host_lane  = '0;
    assign host_waddr = host_baddr_i[WADDR_W-1:0];
  end

  // Fixed priority: the core wins.
  assign sram_req = core_req_i || host_req_i;
  assign sram_we  = core_req_i ? core_we_i : host_we_i;

  always_comb begin
    if (core_req_i) begin
      sram_addr  = core_addr_i;
      sram_wdata = core_wdata_i;
      sram_wstrb = {LANES{1'b1}};
    end else begin
      sram_addr  = host_waddr;
      // Replicate the host byte across the word; the strobe picks the lane.
      sram_wdata = {LANES{host_wdata_i}};
      sram_wstrb = '0;
      sram_wstrb[host_lane] = 1'b1;
    end
  end

  sram_1rw #(
    .WORDS  (WORDS),
    .WORD_W (WORD_W)
  ) u_sram (
    .clk_i   (clk_i),
    .rst_ni  (rst_ni),
    .req_i   (sram_req),
    .we_i    (sram_we),
    .addr_i  (sram_addr),
    .wdata_i (sram_wdata),
    .wstrb_i (sram_wstrb),
    .rdata_o (sram_rdata)
  );

  // The lane has to be remembered for one cycle so that the returning word can
  // be narrowed to the byte the host asked for.
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) host_lane_q <= '0;
    else if (host_req_i && !core_req_i && !host_we_i) host_lane_q <= host_lane;
  end

  assign core_rdata_o = sram_rdata;
  assign host_rdata_o = sram_rdata[{host_lane_q, 3'b000} +: 8];

endmodule
