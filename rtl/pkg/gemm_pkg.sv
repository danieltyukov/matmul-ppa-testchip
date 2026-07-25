// Copyright 2026 Daniel Tyukov
// SPDX-License-Identifier: Apache-2.0
//
// Central parameter package for the matmul PPA test chip.
//
// Everything that sizes the design lives here. Changing MAT_* or TILE_* is the
// supported way to retarget the chip; the elaboration-time checks in
// bench_core.sv reject combinations that do not tile evenly.
//
// Data layout convention used everywhere in this repo:
//   A is MAT_M x MAT_K, B is MAT_K x MAT_N, C = A * B is MAT_M x MAT_N.
//   Matrices are row major. A flattened tile vector packs element (r, c) of an
//   R x C tile at bit offset ((r * C) + c) * ELEM_W, element (0, 0) in the LSBs.

package gemm_pkg;

  // ---------------------------------------------------------------------------
  // Arithmetic
  // ---------------------------------------------------------------------------
  parameter int unsigned OPERAND_W = 8;   // INT8 two's complement operands
  parameter int unsigned ACC_W     = 32;  // INT32 accumulators

  // ---------------------------------------------------------------------------
  // Full matrix geometry (what the on-chip SRAMs hold)
  // ---------------------------------------------------------------------------
  parameter int unsigned MAT_M = 32;
  parameter int unsigned MAT_N = 32;
  parameter int unsigned MAT_K = 32;

  // ---------------------------------------------------------------------------
  // Tile geometry (what one engine invocation computes)
  // ---------------------------------------------------------------------------
  parameter int unsigned TILE_M = 4;
  parameter int unsigned TILE_N = 4;
  parameter int unsigned TILE_K = 4;

  // ---------------------------------------------------------------------------
  // Derived tile grid
  // ---------------------------------------------------------------------------
  parameter int unsigned GRID_M = MAT_M / TILE_M;
  parameter int unsigned GRID_N = MAT_N / TILE_N;
  parameter int unsigned GRID_K = MAT_K / TILE_K;

  parameter int unsigned GRID_M_W = (GRID_M > 1) ? $clog2(GRID_M) : 1;
  parameter int unsigned GRID_N_W = (GRID_N > 1) ? $clog2(GRID_N) : 1;
  parameter int unsigned GRID_K_W = (GRID_K > 1) ? $clog2(GRID_K) : 1;

  // ---------------------------------------------------------------------------
  // Flattened tile vector widths
  // ---------------------------------------------------------------------------
  parameter int unsigned A_TILE_W = TILE_M * TILE_K * OPERAND_W;
  parameter int unsigned B_TILE_W = TILE_K * TILE_N * OPERAND_W;
  parameter int unsigned C_TILE_W = TILE_M * TILE_N * ACC_W;

  // ---------------------------------------------------------------------------
  // Engine roster. Adding a candidate means bumping ENGINE_COUNT and adding an
  // instance in engine_array.sv. See docs/ADDING_A_CANDIDATE.md.
  // ---------------------------------------------------------------------------
  parameter int unsigned ENGINE_COUNT = 5;
  parameter int unsigned ENGINE_SEL_W = $clog2(ENGINE_COUNT);

  parameter int unsigned ENG_INFER     = 0;
  parameter int unsigned ENG_WALLACE   = 1;
  parameter int unsigned ENG_BOOTH4    = 2;
  parameter int unsigned ENG_SIGNMAG   = 3;
  parameter int unsigned ENG_BITSERIAL = 4;

  // Width of one operand-by-operand product, and of one TILE_K-long dot product.
  // A dot product of TILE_K INT8 products spans
  // [-TILE_K * 2**(2*OPERAND_W-2), TILE_K * (2**(2*OPERAND_W-2) - ...)], so
  // 2*OPERAND_W + clog2(TILE_K) bits of two's complement is exactly enough.
  parameter int unsigned PROD_W = 2 * OPERAND_W;
  parameter int unsigned DOT_W  = PROD_W + ((TILE_K > 1) ? $clog2(TILE_K) : 0);

  // MACs retired per tile launch, and the width of the per-cycle mac_tick bus.
  parameter int unsigned MACS_PER_TILE = TILE_M * TILE_N * TILE_K;
  parameter int unsigned MAC_TICK_W    = $clog2(MACS_PER_TILE + 1);

  // ---------------------------------------------------------------------------
  // SRAM geometry
  //
  // Operand stores are word organised so that one read delivers exactly the
  // slice of a matrix row that a tile fetch needs. A tile fetch is therefore
  // TILE_M (for A) or TILE_K (for B) single-port reads, not a magic wide port.
  // ---------------------------------------------------------------------------
  parameter int unsigned A_WORD_W  = TILE_K * OPERAND_W;      // one A row slice
  parameter int unsigned A_WORDS   = MAT_M * GRID_K;
  parameter int unsigned B_WORD_W  = TILE_N * OPERAND_W;      // one B row slice
  parameter int unsigned B_WORDS   = MAT_K * GRID_N;
  parameter int unsigned C_WORD_W  = TILE_N * ACC_W;          // one C row slice
  parameter int unsigned C_WORDS   = MAT_M * GRID_N;

  parameter int unsigned A_ADDR_W = $clog2(A_WORDS);
  parameter int unsigned B_ADDR_W = $clog2(B_WORDS);
  parameter int unsigned C_ADDR_W = $clog2(C_WORDS);

  // Byte counts as seen from the host address space.
  parameter int unsigned A_BYTES = MAT_M * MAT_K;
  parameter int unsigned B_BYTES = MAT_K * MAT_N;
  parameter int unsigned C_BYTES = MAT_M * MAT_N * (ACC_W / 8);

  // ---------------------------------------------------------------------------
  // Host (SPI) interface
  // ---------------------------------------------------------------------------
  parameter int unsigned HOST_ADDR_W = 16;   // two address bytes per frame
  parameter int unsigned PERF_BYTES  = 12;   // cycles, macs, mismatches, first
  parameter int unsigned CFG_BYTES   = 10;   // geometry discovery payload
  parameter int unsigned ID_BYTES    = 4;

  // Chip identification returned by OP_RD_ID: ASCII "MP" then major.minor.
  parameter logic [31:0] CHIP_ID = 32'h4D50_0102;

  // Opcodes. Bit 7 selects direction: 0 = host writes, 1 = chip returns data.
  parameter logic [7:0] OP_NOP       = 8'h00;
  parameter logic [7:0] OP_WR_A      = 8'h01;
  parameter logic [7:0] OP_WR_B      = 8'h02;
  parameter logic [7:0] OP_WR_REF    = 8'h03;
  parameter logic [7:0] OP_WR_ENGINE = 8'h08;
  parameter logic [7:0] OP_WR_TRIG   = 8'h09;
  parameter logic [7:0] OP_SOFT_RST  = 8'h0F;
  parameter logic [7:0] OP_RD_ID     = 8'h81;
  parameter logic [7:0] OP_RD_STATUS = 8'h82;
  parameter logic [7:0] OP_RD_PERF   = 8'h83;
  parameter logic [7:0] OP_RD_C      = 8'h84;
  parameter logic [7:0] OP_RD_A      = 8'h85;
  parameter logic [7:0] OP_RD_B      = 8'h86;
  parameter logic [7:0] OP_RD_CFG    = 8'h87;
  parameter logic [7:0] OP_RD_REF    = 8'h88;

  // Key byte that OP_SOFT_RST must carry, so a stray frame cannot reset the chip.
  parameter logic [7:0] SOFT_RST_KEY = 8'h5A;

  // OP_WR_TRIG payload bits.
  parameter int unsigned TRIG_RUN        = 0;  // start the tiled GEMM
  parameter int unsigned TRIG_CLR_C      = 1;  // zero the result store
  parameter int unsigned TRIG_VERIFY     = 2;  // start the on-chip comparator
  parameter int unsigned TRIG_CLR_PERF   = 3;  // zero cycle and MAC counters
  parameter int unsigned TRIG_CLR_STICKY = 4;  // clear sticky status flags

  // Status byte bits.
  parameter int unsigned ST_BUSY      = 0;
  parameter int unsigned ST_DONE      = 1;
  parameter int unsigned ST_VFY_BUSY  = 2;
  parameter int unsigned ST_VFY_DONE  = 3;
  parameter int unsigned ST_MISMATCH  = 4;
  parameter int unsigned ST_CMD_ERR   = 5;
  parameter int unsigned ST_FRAME_ERR = 6;
  parameter int unsigned ST_RST_ACK   = 7;

  // ---------------------------------------------------------------------------
  // Helper functions (elaboration-time only)
  //
  // Written in Verilog-2001 function style with `integer` types and bounded for
  // loops. Yosys 0.33 rejects `int unsigned` in function signatures and cannot
  // constant-fold unbounded while loops, and these functions have to elaborate
  // in Verilator, Icarus and Yosys alike.
  // ---------------------------------------------------------------------------

  // Upper bound on 3:2 compression layers, enough for any practical addend count.
  parameter int unsigned CSA_LAYER_LIMIT = 64;

  // Number of addends left after `layer` rounds of 3:2 carry-save compression.
  function automatic integer csa_width_at(input integer n_in, input integer layer);
    integer n;
    integer l;
    begin
      n = n_in;
      for (l = 0; l < layer; l = l + 1) begin
        if (n > 2) n = 2 * (n / 3) + (n % 3);
      end
      csa_width_at = n;
    end
  endfunction

  // How many 3:2 layers it takes to get from n_in down to two addends.
  function automatic integer csa_layers(input integer n_in);
    integer n;
    integer l;
    integer cnt;
    begin
      n = n_in;
      cnt = 0;
      for (l = 0; l < CSA_LAYER_LIMIT; l = l + 1) begin
        if (n > 2) begin
          n = 2 * (n / 3) + (n % 3);
          cnt = cnt + 1;
        end
      end
      csa_layers = cnt;
    end
  endfunction

  function automatic integer max_of(input integer a, input integer b);
    begin
      max_of = (a > b) ? a : b;
    end
  endfunction

endpackage
