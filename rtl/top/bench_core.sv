// Copyright 2026 Daniel Tyukov
// SPDX-License-Identifier: Apache-2.0
//
// Everything inside the pad ring.
//
// Structure:
//
//   pins -> spi_target -> frame_router -> matrix stores (A, B, C, reference)
//                              |          gemm_sequencer -> engine_array
//                              |          result_checker
//                              +--------- cycle_meter, mac_meter
//
// Reset domains: the whole chip runs on one clock, and the external reset pin is
// brought in through reset_bridge (async assert, sync release). A soft reset from
// the host resets the datapath (sequencer, checker, meters, engines) but not the
// SPI front end, so the frame carrying the soft reset command can finish cleanly
// and the host stays in sync.
//
// SRAM port arbitration: the sequencer and the checker both reach the result
// store, and they are mutually exclusive because frame_router refuses to start
// one while the other is running and records the refusal as a command error.

module bench_core #(
  parameter int unsigned MAT_M        = gemm_pkg::MAT_M,
  parameter int unsigned MAT_N        = gemm_pkg::MAT_N,
  parameter int unsigned MAT_K        = gemm_pkg::MAT_K,
  parameter int unsigned TILE_M       = gemm_pkg::TILE_M,
  parameter int unsigned TILE_N       = gemm_pkg::TILE_N,
  parameter int unsigned TILE_K       = gemm_pkg::TILE_K,
  parameter int unsigned OPERAND_W    = gemm_pkg::OPERAND_W,
  parameter int unsigned ACC_W        = gemm_pkg::ACC_W,
  parameter int unsigned ENGINE_COUNT = gemm_pkg::ENGINE_COUNT
) (
  input  logic       clk_i,
  input  logic       ext_rst_ni,
  input  logic       test_mode_i,

  input  logic       spi_sck_i,
  input  logic       spi_cs_ni,
  input  logic       spi_mosi_i,
  output logic       spi_miso_o,
  output logic       spi_miso_oe_o,

  output logic       stat_busy_o,
  output logic       stat_done_o,
  output logic       stat_vfy_done_o,
  output logic       stat_mismatch_o
);

  // ---------------------------------------------------------------------------
  // Elaboration-time configuration checks. Wrapped for Yosys, which defines the
  // YOSYS macro and does not run initial blocks.
  // ---------------------------------------------------------------------------
`ifndef YOSYS
  initial begin : p_config_checks
    if (MAT_M % TILE_M != 0)
      $fatal(1, "bench_core: MAT_M (%0d) must be a multiple of TILE_M (%0d)",
             MAT_M, TILE_M);
    if (MAT_N % TILE_N != 0)
      $fatal(1, "bench_core: MAT_N (%0d) must be a multiple of TILE_N (%0d)",
             MAT_N, TILE_N);
    if (MAT_K % TILE_K != 0)
      $fatal(1, "bench_core: MAT_K (%0d) must be a multiple of TILE_K (%0d)",
             MAT_K, TILE_K);
    if (ACC_W % 8 != 0)
      $fatal(1, "bench_core: ACC_W (%0d) must be a multiple of 8", ACC_W);
    if (ACC_W < 2*OPERAND_W + 1)
      $fatal(1, "bench_core: ACC_W (%0d) cannot hold a product of two %0d bit operands",
             ACC_W, OPERAND_W);
    if (2 ** $clog2(TILE_K) != TILE_K)
      $fatal(1, "bench_core: TILE_K (%0d) must be a power of two so the host byte address maps to a word and a lane by slicing",
             TILE_K);
    if (2 ** $clog2(TILE_N) != TILE_N)
      $fatal(1, "bench_core: TILE_N (%0d) must be a power of two", TILE_N);
    if (2 ** $clog2(gemm_pkg::GRID_N) != gemm_pkg::GRID_N)
      $fatal(1, "bench_core: MAT_N/TILE_N (%0d) must be a power of two",
             gemm_pkg::GRID_N);
    if (ENGINE_COUNT < 1)
      $fatal(1, "bench_core: ENGINE_COUNT must be at least 1");
  end
`endif

  localparam int unsigned GRID_M   = MAT_M / TILE_M;
  localparam int unsigned GRID_N   = MAT_N / TILE_N;
  localparam int unsigned GRID_K   = MAT_K / TILE_K;
  localparam int unsigned A_WORD_W = TILE_K * OPERAND_W;
  localparam int unsigned B_WORD_W = TILE_N * OPERAND_W;
  localparam int unsigned C_WORD_W = TILE_N * ACC_W;
  localparam int unsigned A_WORDS  = MAT_M * GRID_K;
  localparam int unsigned B_WORDS  = MAT_K * GRID_N;
  localparam int unsigned C_WORDS  = MAT_M * GRID_N;
  localparam int unsigned A_ADDR_W = $clog2(A_WORDS);
  localparam int unsigned B_ADDR_W = $clog2(B_WORDS);
  localparam int unsigned C_ADDR_W = $clog2(C_WORDS);
  localparam int unsigned HOST_ADDR_W = gemm_pkg::HOST_ADDR_W;
  localparam int unsigned MAC_TICK_W  = gemm_pkg::MAC_TICK_W;
  localparam int unsigned SEL_W       = gemm_pkg::ENGINE_SEL_W;
  localparam int unsigned A_TILE_W    = TILE_M * TILE_K * OPERAND_W;
  localparam int unsigned B_TILE_W    = TILE_K * TILE_N * OPERAND_W;
  localparam int unsigned C_TILE_W    = TILE_M * TILE_N * ACC_W;

  // ---------------------------------------------------------------------------
  // Reset
  // ---------------------------------------------------------------------------
  logic rst_n;        // synchronised chip reset
  logic dp_rst_n;     // datapath reset, additionally driven by the soft reset
  logic soft_rst;
  logic [2:0] soft_rst_cnt_q;

  reset_bridge #(.STAGES(3)) u_reset_bridge (
    .clk_i      (clk_i),
    .ext_rst_ni (ext_rst_ni),
    .rst_no     (rst_n)
  );

  // Stretch the one cycle soft reset pulse so every datapath flop sees it.
  always_ff @(posedge clk_i or negedge rst_n) begin
    if (!rst_n)        soft_rst_cnt_q <= '0;
    else if (soft_rst) soft_rst_cnt_q <= 3'd4;
    else if (soft_rst_cnt_q != '0) soft_rst_cnt_q <= soft_rst_cnt_q - 1'b1;
  end

  assign dp_rst_n = rst_n && (soft_rst_cnt_q == '0) && !soft_rst;

  // ---------------------------------------------------------------------------
  // Host front end
  // ---------------------------------------------------------------------------
  logic       frame_start;
  logic       frame_end;
  logic       rx_valid;
  logic [7:0] rx_byte;
  logic [7:0] tx_byte;

  spi_target u_spi_target (
    .clk_i         (clk_i),
    .rst_ni        (rst_n),
    .spi_sck_i     (spi_sck_i),
    .spi_cs_ni     (spi_cs_ni),
    .spi_mosi_i    (spi_mosi_i),
    .spi_miso_o    (spi_miso_o),
    .spi_miso_oe_o (spi_miso_oe_o),
    .frame_start_o (frame_start),
    .frame_end_o   (frame_end),
    .rx_valid_o    (rx_valid),
    .rx_byte_o     (rx_byte),
    .tx_byte_i     (tx_byte)
  );

  localparam int unsigned A_BADDR_W = $clog2(A_WORDS*(A_WORD_W/8));
  localparam int unsigned B_BADDR_W = $clog2(B_WORDS*(B_WORD_W/8));
  localparam int unsigned C_BADDR_W = $clog2(C_WORDS*(C_WORD_W/8));

  logic                   a_host_req, a_host_we;
  logic [A_BADDR_W-1:0]   a_host_baddr;
  logic [7:0]             a_host_wdata, a_host_rdata;
  logic                   b_host_req, b_host_we;
  logic [B_BADDR_W-1:0]   b_host_baddr;
  logic [7:0]             b_host_wdata, b_host_rdata;
  logic                   c_host_req;
  logic [C_BADDR_W-1:0]   c_host_baddr;
  logic [7:0]             c_host_rdata;
  logic                   ref_host_req, ref_host_we;
  logic [C_BADDR_W-1:0]   ref_host_baddr;
  logic [7:0]             ref_host_wdata, ref_host_rdata;

  logic             trig_run, trig_clear_c, trig_verify;
  logic             trig_clear_perf, trig_clear_sticky;
  logic [SEL_W-1:0] engine_sel;

  logic        seq_busy, seq_done;
  logic        vfy_busy, vfy_done;
  logic        mismatch;
  logic [15:0] mismatch_count, first_mismatch;
  logic [31:0] cycle_count, mac_count;

  frame_router #(
    .HOST_ADDR_W (HOST_ADDR_W),
    .A_BADDR_W   (A_BADDR_W),
    .B_BADDR_W   (B_BADDR_W),
    .C_BADDR_W   (C_BADDR_W)
  ) u_frame_router (
    .clk_i            (clk_i),
    .rst_ni           (rst_n),
    .frame_start_i    (frame_start),
    .frame_end_i      (frame_end),
    .rx_valid_i       (rx_valid),
    .rx_byte_i        (rx_byte),
    .tx_byte_o        (tx_byte),
    .a_req_o          (a_host_req),
    .a_we_o           (a_host_we),
    .a_baddr_o        (a_host_baddr),
    .a_wdata_o        (a_host_wdata),
    .a_rdata_i        (a_host_rdata),
    .b_req_o          (b_host_req),
    .b_we_o           (b_host_we),
    .b_baddr_o        (b_host_baddr),
    .b_wdata_o        (b_host_wdata),
    .b_rdata_i        (b_host_rdata),
    .c_req_o          (c_host_req),
    .c_baddr_o        (c_host_baddr),
    .c_rdata_i        (c_host_rdata),
    .ref_req_o        (ref_host_req),
    .ref_we_o         (ref_host_we),
    .ref_baddr_o      (ref_host_baddr),
    .ref_wdata_o      (ref_host_wdata),
    .ref_rdata_i      (ref_host_rdata),
    .run_o            (trig_run),
    .clear_c_o        (trig_clear_c),
    .verify_o         (trig_verify),
    .clear_perf_o     (trig_clear_perf),
    .clear_sticky_o   (trig_clear_sticky),
    .soft_rst_o       (soft_rst),
    .engine_sel_o     (engine_sel),
    .core_busy_i      (seq_busy),
    .core_done_i      (seq_done),
    .vfy_busy_i       (vfy_busy),
    .vfy_done_i       (vfy_done),
    .mismatch_i       (mismatch),
    .cycle_count_i    (cycle_count),
    .mac_count_i      (mac_count),
    .mismatch_count_i (mismatch_count),
    .first_mismatch_i (first_mismatch)
  );

  // ---------------------------------------------------------------------------
  // Matrix stores
  // ---------------------------------------------------------------------------
  logic                  seq_a_req;
  logic [A_ADDR_W-1:0]   seq_a_addr;
  logic [A_WORD_W-1:0]   seq_a_rdata;
  logic                  seq_b_req;
  logic [B_ADDR_W-1:0]   seq_b_addr;
  logic [B_WORD_W-1:0]   seq_b_rdata;
  logic                  seq_c_req, seq_c_we;
  logic [C_ADDR_W-1:0]   seq_c_addr;
  logic [C_WORD_W-1:0]   seq_c_wdata;

  logic                  chk_c_req;
  logic [C_ADDR_W-1:0]   chk_c_addr;
  logic                  chk_ref_req;
  logic [C_ADDR_W-1:0]   chk_ref_addr;
  logic [C_WORD_W-1:0]   c_core_rdata;
  logic [C_WORD_W-1:0]   ref_core_rdata;

  matrix_store #(
    .WORDS  (A_WORDS),
    .WORD_W (A_WORD_W)
  ) u_store_a (
    .clk_i        (clk_i),
    .rst_ni       (rst_n),
    .core_req_i   (seq_a_req),
    .core_we_i    (1'b0),
    .core_addr_i  (seq_a_addr),
    .core_wdata_i ({A_WORD_W{1'b0}}),
    .core_rdata_o (seq_a_rdata),
    .host_req_i   (a_host_req),
    .host_we_i    (a_host_we),
    .host_baddr_i (a_host_baddr),
    .host_wdata_i (a_host_wdata),
    .host_rdata_o (a_host_rdata)
  );

  matrix_store #(
    .WORDS  (B_WORDS),
    .WORD_W (B_WORD_W)
  ) u_store_b (
    .clk_i        (clk_i),
    .rst_ni       (rst_n),
    .core_req_i   (seq_b_req),
    .core_we_i    (1'b0),
    .core_addr_i  (seq_b_addr),
    .core_wdata_i ({B_WORD_W{1'b0}}),
    .core_rdata_o (seq_b_rdata),
    .host_req_i   (b_host_req),
    .host_we_i    (b_host_we),
    .host_baddr_i (b_host_baddr),
    .host_wdata_i (b_host_wdata),
    .host_rdata_o (b_host_rdata)
  );

  // The sequencer writes the result store and the checker reads it. They never
  // overlap, so a plain priority mux is enough.
  logic                c_core_req, c_core_we;
  logic [C_ADDR_W-1:0] c_core_addr;

  assign c_core_req  = seq_c_req || chk_c_req;
  assign c_core_we   = seq_c_req && seq_c_we;
  assign c_core_addr = seq_c_req ? seq_c_addr : chk_c_addr;

  matrix_store #(
    .WORDS  (C_WORDS),
    .WORD_W (C_WORD_W)
  ) u_store_c (
    .clk_i        (clk_i),
    .rst_ni       (rst_n),
    .core_req_i   (c_core_req),
    .core_we_i    (c_core_we),
    .core_addr_i  (c_core_addr),
    .core_wdata_i (seq_c_wdata),
    .core_rdata_o (c_core_rdata),
    .host_req_i   (c_host_req),
    .host_we_i    (1'b0),
    .host_baddr_i (c_host_baddr),
    .host_wdata_i (8'h00),
    .host_rdata_o (c_host_rdata)
  );

  matrix_store #(
    .WORDS  (C_WORDS),
    .WORD_W (C_WORD_W)
  ) u_store_ref (
    .clk_i        (clk_i),
    .rst_ni       (rst_n),
    .core_req_i   (chk_ref_req),
    .core_we_i    (1'b0),
    .core_addr_i  (chk_ref_addr),
    .core_wdata_i ({C_WORD_W{1'b0}}),
    .core_rdata_o (ref_core_rdata),
    .host_req_i   (ref_host_req),
    .host_we_i    (ref_host_we),
    .host_baddr_i (ref_host_baddr),
    .host_wdata_i (ref_host_wdata),
    .host_rdata_o (ref_host_rdata)
  );

  // ---------------------------------------------------------------------------
  // Sequencer and engines
  // ---------------------------------------------------------------------------
  logic                eng_clear, eng_launch, eng_ready, eng_valid;
  logic [A_TILE_W-1:0] a_tile;
  logic [B_TILE_W-1:0] b_tile;
  logic [C_TILE_W-1:0] c_tile;
  logic [MAC_TICK_W-1:0] mac_tick;

  gemm_sequencer #(
    .TILE_M (TILE_M), .TILE_N (TILE_N), .TILE_K (TILE_K),
    .GRID_M (GRID_M), .GRID_N (GRID_N), .GRID_K (GRID_K),
    .OPERAND_W (OPERAND_W), .ACC_W (ACC_W),
    .A_WORDS (A_WORDS), .B_WORDS (B_WORDS), .C_WORDS (C_WORDS)
  ) u_sequencer (
    .clk_i        (clk_i),
    .rst_ni       (dp_rst_n),
    .run_i          (trig_run),
    .clear_c_i      (trig_clear_c),
    .clear_sticky_i (trig_clear_sticky),
    .busy_o       (seq_busy),
    .done_o       (seq_done),
    .a_req_o      (seq_a_req),
    .a_addr_o     (seq_a_addr),
    .a_rdata_i    (seq_a_rdata),
    .b_req_o      (seq_b_req),
    .b_addr_o     (seq_b_addr),
    .b_rdata_i    (seq_b_rdata),
    .c_req_o      (seq_c_req),
    .c_we_o       (seq_c_we),
    .c_addr_o     (seq_c_addr),
    .c_wdata_o    (seq_c_wdata),
    .eng_clear_o  (eng_clear),
    .eng_launch_o (eng_launch),
    .a_tile_o     (a_tile),
    .b_tile_o     (b_tile),
    .eng_ready_i  (eng_ready),
    .eng_valid_i  (eng_valid),
    .c_tile_i     (c_tile)
  );

  engine_array #(
    .TILE_M (TILE_M), .TILE_N (TILE_N), .TILE_K (TILE_K),
    .OPERAND_W (OPERAND_W), .ACC_W (ACC_W),
    .ENGINE_COUNT (ENGINE_COUNT)
  ) u_engine_array (
    .clk_i        (clk_i),
    .rst_ni       (dp_rst_n),
    .test_mode_i  (test_mode_i),
    .engine_sel_i (engine_sel),
    .clear_i      (eng_clear),
    .launch_i     (eng_launch),
    .a_tile_i     (a_tile),
    .b_tile_i     (b_tile),
    .ready_o      (eng_ready),
    .valid_o      (eng_valid),
    .mac_tick_o   (mac_tick),
    .c_tile_o     (c_tile)
  );

  // ---------------------------------------------------------------------------
  // Measurement
  // ---------------------------------------------------------------------------
  result_checker #(
    .MAT_N (MAT_N), .TILE_N (TILE_N), .GRID_N (GRID_N),
    .ACC_W (ACC_W), .C_WORDS (C_WORDS)
  ) u_checker (
    .clk_i            (clk_i),
    .rst_ni           (dp_rst_n),
    .start_i          (trig_verify),
    .clear_sticky_i   (trig_clear_sticky),
    .busy_o           (vfy_busy),
    .done_o           (vfy_done),
    .c_req_o          (chk_c_req),
    .c_addr_o         (chk_c_addr),
    .c_rdata_i        (c_core_rdata),
    .ref_req_o        (chk_ref_req),
    .ref_addr_o       (chk_ref_addr),
    .ref_rdata_i      (ref_core_rdata),
    .mismatch_o       (mismatch),
    .mismatch_count_o (mismatch_count),
    .first_mismatch_o (first_mismatch)
  );

  cycle_meter #(.WIDTH (32)) u_cycle_meter (
    .clk_i    (clk_i),
    .rst_ni   (dp_rst_n),
    .clear_i  (trig_run || trig_clear_perf),
    .enable_i (seq_busy),
    .count_o  (cycle_count)
  );

  mac_meter #(.WIDTH (32), .TICK_W (MAC_TICK_W)) u_mac_meter (
    .clk_i   (clk_i),
    .rst_ni  (dp_rst_n),
    .clear_i (trig_run || trig_clear_perf),
    .tick_i  (mac_tick),
    .count_o (mac_count)
  );

  // ---------------------------------------------------------------------------
  // Status pins
  // ---------------------------------------------------------------------------
  assign stat_busy_o     = seq_busy || vfy_busy;
  assign stat_done_o     = seq_done;
  assign stat_vfy_done_o = vfy_done;
  assign stat_mismatch_o = mismatch;

endmodule
