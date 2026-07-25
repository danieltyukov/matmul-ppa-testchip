// Copyright 2026 Daniel Tyukov
// SPDX-License-Identifier: Apache-2.0
//
// Switching-activity bench for the candidate engines.
//
// Every candidate runs on the same clock with the same operand stream, so one VCD
// contains all of them under identical stimulus. That is what makes the comparison
// controlled: there is no run-to-run variation, no scheduling difference and no
// separate compile to account for.
//
// The operand stream comes from plain text hex files written by
// tools/gen_activity_stimulus.py, so the workload is reproducible and the sweep
// over operand statistics is driven from Python rather than hard coded here.
//
// Plusargs:
//   +a_hex=<path>     one hex byte per line, TILE_M*TILE_K bytes per tile
//   +b_hex=<path>     one hex byte per line, TILE_K*TILE_N bytes per tile
//   +tiles=<n>        how many tiles to stream
//   +vcd=<path>       where to write the dump
//   +clear_every=<n>  clear the accumulators every n tiles (0 disables)

`timescale 1ns/1ps

module tb_activity_engines;

  localparam int unsigned TILE_M = gemm_pkg::TILE_M;
  localparam int unsigned TILE_N = gemm_pkg::TILE_N;
  localparam int unsigned TILE_K = gemm_pkg::TILE_K;
  localparam int unsigned OPW    = gemm_pkg::OPERAND_W;
  localparam int unsigned NE     = gemm_pkg::ENGINE_COUNT;
  localparam int unsigned A_ELEMS = TILE_M * TILE_K;
  localparam int unsigned B_ELEMS = TILE_K * TILE_N;
  localparam int unsigned MAX_TILES = 4096;

  logic clk = 1'b0;
  logic rst_n = 1'b0;
  logic acc_clear = 1'b0;
  logic launch = 1'b0;

  logic [A_ELEMS*OPW-1:0] a_tile;
  logic [B_ELEMS*OPW-1:0] b_tile;
  logic [NE-1:0]          ready;
  logic [NE-1:0]          valid;
  logic [NE*TILE_M*TILE_N*gemm_pkg::ACC_W-1:0] c_tile;
  logic [NE*gemm_pkg::MAC_TICK_W-1:0]         mac_tick;

  tb_engine_harness u_harness (
    .clk_i       (clk),
    .rst_ni      (rst_n),
    .acc_clear_i (acc_clear),
    .launch_i    (launch),
    .a_tile_i    (a_tile),
    .b_tile_i    (b_tile),
    .ready_o     (ready),
    .valid_o     (valid),
    .c_tile_o    (c_tile),
    .mac_tick_o  (mac_tick)
  );

  always #5 clk = ~clk;

  // Operand memories, one byte per entry.
  logic [7:0] a_mem [0:MAX_TILES*A_ELEMS-1];
  logic [7:0] b_mem [0:MAX_TILES*B_ELEMS-1];

  string a_path;
  string b_path;
  string vcd_path;
  int    n_tiles;
  int    clear_every;

  initial begin : p_run
    int t;
    int e;
    int launches;

    if (!$value$plusargs("a_hex=%s", a_path))   a_path = "a.hex";
    if (!$value$plusargs("b_hex=%s", b_path))   b_path = "b.hex";
    if (!$value$plusargs("vcd=%s", vcd_path))   vcd_path = "activity_engines.vcd";
    if (!$value$plusargs("tiles=%d", n_tiles))  n_tiles = 256;
    if (!$value$plusargs("clear_every=%d", clear_every)) clear_every = 8;

    if (n_tiles > MAX_TILES) begin
      $fatal(1, "tb_activity_engines: tiles=%0d exceeds MAX_TILES=%0d",
             n_tiles, MAX_TILES);
    end

    $readmemh(a_path, a_mem);
    $readmemh(b_path, b_mem);

    // A missing or short stimulus file must not pass silently. $readmemh leaves the
    // array at x, the reference is then computed from x, and comparing x against x is
    // false in SystemVerilog, so every check would pass and the equivalence this bench
    // exists to perform would be vacuous. That happened during development, so it is
    // now an error.
    for (e = 0; e < n_tiles * A_ELEMS; e++) begin
      if (^a_mem[e] === 1'bx) begin
        $fatal(1, "%s: a_mem[%0d] is x after reading %s. The stimulus file is missing or shorter than %0d bytes.",
               "tb_activity_engines", e, a_path, n_tiles * A_ELEMS);
      end
    end
    for (e = 0; e < n_tiles * B_ELEMS; e++) begin
      if (^b_mem[e] === 1'bx) begin
        $fatal(1, "%s: b_mem[%0d] is x after reading %s. The stimulus file is missing or shorter than %0d bytes.",
               "tb_activity_engines", e, b_path, n_tiles * B_ELEMS);
      end
    end

    $dumpfile(vcd_path);
    $dumpvars(0, tb_activity_engines);

    // Reset, then settle. tools/activity_sweep.py passes the same settle time to
    // the parser as its start-time, so reset activity is never counted.
    repeat (8) @(posedge clk);
    rst_n = 1'b1;
    repeat (4) @(posedge clk);

    acc_clear <= 1'b1;
    @(posedge clk);
    acc_clear <= 1'b0;
    @(posedge clk);

    launches = 0;
    for (t = 0; t < n_tiles; t++) begin
      if (clear_every > 0 && t != 0 && (t % clear_every) == 0) begin
        while (ready !== {NE{1'b1}}) @(posedge clk);
        acc_clear <= 1'b1;
        @(posedge clk);
        acc_clear <= 1'b0;
      end

      for (e = 0; e < A_ELEMS; e++) begin
        a_tile[e*OPW +: OPW] = a_mem[(t*A_ELEMS) + e];
      end
      for (e = 0; e < B_ELEMS; e++) begin
        b_tile[e*OPW +: OPW] = b_mem[(t*B_ELEMS) + e];
      end

      while (ready !== {NE{1'b1}}) @(posedge clk);
      launch <= 1'b1;
      @(posedge clk);
      launch <= 1'b0;
      // Wait for the slowest candidate, so every candidate has fully retired the
      // tile before the next operand pair appears.
      while (!valid[gemm_pkg::ENG_BITSERIAL]) @(posedge clk);
      launches++;
    end

    repeat (8) @(posedge clk);
    $display("tb_activity_engines: %0d tile launches, %0d candidates, vcd=%s",
             launches, NE, vcd_path);
    $finish;
  end

endmodule
