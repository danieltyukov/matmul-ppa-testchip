// Copyright 2026 Daniel Tyukov
// SPDX-License-Identifier: Apache-2.0
//
// Gate level switching-activity bench for one candidate.
//
// Why gate level rather than RTL for the headline measurement: the candidates are
// deliberately described at different levels of abstraction. engine_infer is a
// behavioural `*` and `+`, so at RTL it has almost no internal nets and its RTL
// transition count is meaninglessly low. After synthesis every candidate is the
// same kind of object, a flat netlist of gates, and counting transitions on those
// nets compares like with like.
//
// One candidate per simulation, driven from the same stimulus files as every
// other candidate, so the comparison stays controlled without needing five
// netlists in one elaboration (which would collide on internal names).
//
// The instantiated module name comes from the ENGINE_MODULE macro, and the
// netlist is passed on the iverilog command line by tools/activity_sweep.py.
//
// This bench also checks results against a reference computed here, so a gate
// level netlist that does not match its RTL cannot quietly contribute activity
// numbers.
//
// Plusargs:
//   +a_hex=<path>     one hex byte per line, TILE_M*TILE_K bytes per tile
//   +b_hex=<path>     one hex byte per line, TILE_K*TILE_N bytes per tile
//   +tiles=<n>        how many tiles to stream
//   +vcd=<path>       where to write the dump
//   +clear_every=<n>  clear the accumulator every n tiles (0 disables)
//   +latency=<n>      launch-to-valid latency of this candidate, in cycles

`timescale 1ns/1ps

`ifndef ENGINE_MODULE
  `define ENGINE_MODULE engine_infer
`endif

module tb_activity_gate;

  // The gate level netlist carries no parameters, so the geometry is fixed here
  // to the values the netlist was synthesised with.
  localparam int unsigned TILE_M = 4;
  localparam int unsigned TILE_N = 4;
  localparam int unsigned TILE_K = 4;
  localparam int unsigned OPW    = 8;
  localparam int unsigned ACCW   = 32;
  localparam int unsigned MAC_TICK_W = 7;

  localparam int unsigned A_ELEMS = TILE_M * TILE_K;
  localparam int unsigned B_ELEMS = TILE_K * TILE_N;
  localparam int unsigned C_ELEMS = TILE_M * TILE_N;
  localparam int unsigned MAX_TILES = 4096;

  logic clk = 1'b0;
  logic rst_n = 1'b0;
  logic acc_clear = 1'b0;
  logic launch = 1'b0;

  logic [A_ELEMS*OPW-1:0]  a_tile;
  logic [B_ELEMS*OPW-1:0]  b_tile;
  logic [C_ELEMS*ACCW-1:0] c_tile;
  logic                    ready;
  logic                    valid;
  logic [MAC_TICK_W-1:0]   mac_tick;

  `ENGINE_MODULE u_dut (
    .clk_i       (clk),
    .rst_ni      (rst_n),
    .acc_clear_i (acc_clear),
    .launch_i    (launch),
    .a_tile_i    (a_tile),
    .b_tile_i    (b_tile),
    .c_tile_o    (c_tile),
    .ready_o     (ready),
    .valid_o     (valid),
    .mac_tick_o  (mac_tick)
  );

  always #5 clk = ~clk;

  logic [7:0] a_mem [0:MAX_TILES*A_ELEMS-1];
  logic [7:0] b_mem [0:MAX_TILES*B_ELEMS-1];

  string a_path;
  string b_path;
  string vcd_path;
  string dut_name;
  int    n_tiles;
  int    clear_every;
  int    latency;

  // Reference accumulators, kept alongside the design.
  longint expected [0:255];

  initial begin : p_run
    int t;
    int e;
    int m;
    int n;
    int k;
    int errors;
    longint prod;
    longint got;

    if (!$value$plusargs("a_hex=%s", a_path))   a_path = "a.hex";
    if (!$value$plusargs("b_hex=%s", b_path))   b_path = "b.hex";
    if (!$value$plusargs("vcd=%s", vcd_path))   vcd_path = "activity_gate.vcd";
    if (!$value$plusargs("tiles=%d", n_tiles))  n_tiles = 64;
    if (!$value$plusargs("clear_every=%d", clear_every)) clear_every = 8;
    if (!$value$plusargs("latency=%d", latency)) latency = 1;
    // The instantiated module comes from a macro, and stringifying a macro is not
    // portable, so the name is passed in for the report line instead.
    if (!$value$plusargs("name=%s", dut_name)) dut_name = "unknown";

    if (n_tiles > MAX_TILES) begin
      $fatal(1, "tb_activity_gate: tiles=%0d exceeds MAX_TILES=%0d",
             n_tiles, MAX_TILES);
    end

    $readmemh(a_path, a_mem);
    $readmemh(b_path, b_mem);

    $dumpfile(vcd_path);
    $dumpvars(0, tb_activity_gate);

    errors = 0;
    for (e = 0; e < C_ELEMS; e++) expected[e] = 0;

    repeat (8) @(posedge clk);
    rst_n = 1'b1;
    repeat (4) @(posedge clk);

    acc_clear <= 1'b1;
    @(posedge clk);
    acc_clear <= 1'b0;
    @(posedge clk);

    for (t = 0; t < n_tiles; t++) begin
      if (clear_every > 0 && t != 0 && (t % clear_every) == 0) begin
        while (!ready) @(posedge clk);
        acc_clear <= 1'b1;
        @(posedge clk);
        acc_clear <= 1'b0;
        for (e = 0; e < C_ELEMS; e++) expected[e] = 0;
      end

      for (e = 0; e < A_ELEMS; e++) a_tile[e*OPW +: OPW] = a_mem[(t*A_ELEMS) + e];
      for (e = 0; e < B_ELEMS; e++) b_tile[e*OPW +: OPW] = b_mem[(t*B_ELEMS) + e];

      for (m = 0; m < TILE_M; m++) begin
        for (n = 0; n < TILE_N; n++) begin
          prod = 0;
          for (k = 0; k < TILE_K; k++) begin
            prod = prod
                 + longint'($signed(a_tile[(((m*TILE_K) + k)*OPW) +: OPW]))
                 * longint'($signed(b_tile[(((k*TILE_N) + n)*OPW) +: OPW]));
          end
          expected[(m*TILE_N) + n] = expected[(m*TILE_N) + n] + prod;
        end
      end

      while (!ready) @(posedge clk);
      launch <= 1'b1;
      @(posedge clk);
      launch <= 1'b0;
      while (!valid) @(posedge clk);

      for (e = 0; e < C_ELEMS; e++) begin
        got = longint'($signed(c_tile[e*ACCW +: ACCW]));
        if (got != expected[e]) begin
          if (errors < 5) begin
            $display("tb_activity_gate: element %0d is %0d, expected %0d (tile %0d)",
                     e, got, expected[e], t);
          end
          errors++;
        end
      end
    end

    repeat (8) @(posedge clk);
    $display("tb_activity_gate: module=%s tiles=%0d latency=%0d errors=%0d vcd=%s",
             dut_name, n_tiles, latency, errors, vcd_path);
    if (errors != 0) begin
      $fatal(1, "tb_activity_gate: the gate level netlist does not match the reference");
    end
    $finish;
  end

endmodule
