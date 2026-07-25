// Copyright 2026 Daniel Tyukov
// SPDX-License-Identifier: Apache-2.0
//
// Command frame router: turns the SPI byte stream into memory accesses, control
// pulses and readback bytes.
//
// Frame layout, address big endian on the wire:
//
//   memory opcode     [opcode][addr_hi][addr_lo][data ...]
//   register write    [opcode][value]
//   register read     [opcode][dummy ...]
//
// Memory frames auto-increment the byte address, so a whole operand matrix is one
// frame and a partial update is a shorter frame at an offset. There is no length
// field: a frame is as long as the controller keeps chip select low. That makes
// truncation a first class case rather than an accident, and it is reported in
// the status byte.
//
// Readback is prefetched. When the address has been received (memory reads) or
// the opcode decoded (register reads), the byte that goes out next is fetched and
// parked. Each completed byte triggers the fetch of the one after it, so a fetch
// always has a full SPI byte period to complete and never gates the SPI clock.
//
// Host access to the matrix stores is refused while the core is busy, and the
// refusal is recorded as a command error rather than silently corrupting a run.
// Status, performance counter, identification and geometry reads stay available
// at all times so a controller can poll progress.

module frame_router #(
  parameter int unsigned HOST_ADDR_W = gemm_pkg::HOST_ADDR_W,
  // Byte address widths of the four stores. Outputs are narrowed to these so the
  // stores get exactly the bits they can use, and the upper bits of the protocol
  // address are spent on range checking instead of being discarded.
  parameter int unsigned A_BADDR_W   = $clog2(gemm_pkg::A_BYTES),
  parameter int unsigned B_BADDR_W   = $clog2(gemm_pkg::B_BYTES),
  parameter int unsigned C_BADDR_W   = $clog2(gemm_pkg::C_BYTES)
) (
  input  logic                    clk_i,
  input  logic                    rst_ni,

  // Byte stream from spi_target.
  input  logic                    frame_start_i,
  input  logic                    frame_end_i,
  input  logic                    rx_valid_i,
  input  logic [7:0]              rx_byte_i,
  output logic [7:0]              tx_byte_o,

  // Matrix store host ports.
  output logic                    a_req_o,
  output logic                    a_we_o,
  output logic [A_BADDR_W-1:0]  a_baddr_o,
  output logic [7:0]              a_wdata_o,
  input  logic [7:0]              a_rdata_i,

  output logic                    b_req_o,
  output logic                    b_we_o,
  output logic [B_BADDR_W-1:0]  b_baddr_o,
  output logic [7:0]              b_wdata_o,
  input  logic [7:0]              b_rdata_i,

  output logic                    c_req_o,
  output logic [C_BADDR_W-1:0]  c_baddr_o,
  input  logic [7:0]              c_rdata_i,

  output logic                    ref_req_o,
  output logic                    ref_we_o,
  output logic [C_BADDR_W-1:0]  ref_baddr_o,
  output logic [7:0]              ref_wdata_o,
  input  logic [7:0]              ref_rdata_i,

  // Control outputs. All single cycle pulses except engine_sel_o, which is held.
  output logic                    run_o,
  output logic                    clear_c_o,
  output logic                    verify_o,
  output logic                    clear_perf_o,
  output logic                    clear_sticky_o,
  output logic                    soft_rst_o,
  output logic [gemm_pkg::ENGINE_SEL_W-1:0] engine_sel_o,

  // Status and measurement inputs.
  input  logic                    core_busy_i,
  input  logic                    core_done_i,
  input  logic                    vfy_busy_i,
  input  logic                    vfy_done_i,
  input  logic                    mismatch_i,
  input  logic [31:0]             cycle_count_i,
  input  logic [31:0]             mac_count_i,
  input  logic [15:0]             mismatch_count_i,
  input  logic [15:0]             first_mismatch_i
);

  // ---------------------------------------------------------------------------
  // Geometry constants, kept 32 bits wide and sliced at the point of use so no
  // tool has to guess about truncation.
  // ---------------------------------------------------------------------------
  localparam logic [31:0] CFG_MAT_M    = gemm_pkg::MAT_M;
  localparam logic [31:0] CFG_MAT_N    = gemm_pkg::MAT_N;
  localparam logic [31:0] CFG_MAT_K    = gemm_pkg::MAT_K;
  localparam logic [31:0] CFG_TILE_M   = gemm_pkg::TILE_M;
  localparam logic [31:0] CFG_TILE_N   = gemm_pkg::TILE_N;
  localparam logic [31:0] CFG_TILE_K   = gemm_pkg::TILE_K;
  localparam logic [31:0] CFG_OP_W     = gemm_pkg::OPERAND_W;
  localparam logic [31:0] CFG_ACC_W    = gemm_pkg::ACC_W;
  localparam logic [31:0] CFG_ENGINES  = gemm_pkg::ENGINE_COUNT;
  localparam int unsigned SEL_W        = gemm_pkg::ENGINE_SEL_W;

  // ---------------------------------------------------------------------------
  // Frame state
  // ---------------------------------------------------------------------------
  localparam logic [2:0] ST_IDLE   = 3'd0;  // no frame in progress
  localparam logic [2:0] ST_OPCODE = 3'd1;  // waiting for the opcode byte
  localparam logic [2:0] ST_ADDRHI = 3'd2;
  localparam logic [2:0] ST_ADDRLO = 3'd3;
  localparam logic [2:0] ST_MEM    = 3'd4;  // streaming memory data bytes
  localparam logic [2:0] ST_REGVAL = 3'd5;  // waiting for a register write value
  localparam logic [2:0] ST_REGRD  = 3'd6;  // streaming register readback bytes
  localparam logic [2:0] ST_DRAIN  = 3'd7;  // opcode done or refused, ignore rest

  logic [2:0]             state_q;
  logic [2:0]             state_d;
  logic [7:0]             opcode_q;
  logic [HOST_ADDR_W-1:0] addr_q;
  logic [3:0]             reg_idx_q;

  // ---------------------------------------------------------------------------
  // Opcode classification
  // ---------------------------------------------------------------------------
  function automatic logic is_mem_wr(input logic [7:0] op);
    is_mem_wr = (op == gemm_pkg::OP_WR_A) || (op == gemm_pkg::OP_WR_B)
             || (op == gemm_pkg::OP_WR_REF);
  endfunction

  function automatic logic is_mem_rd(input logic [7:0] op);
    is_mem_rd = (op == gemm_pkg::OP_RD_A) || (op == gemm_pkg::OP_RD_B)
             || (op == gemm_pkg::OP_RD_C) || (op == gemm_pkg::OP_RD_REF);
  endfunction

  function automatic logic is_reg_wr(input logic [7:0] op);
    is_reg_wr = (op == gemm_pkg::OP_WR_ENGINE) || (op == gemm_pkg::OP_WR_TRIG)
             || (op == gemm_pkg::OP_SOFT_RST);
  endfunction

  function automatic logic is_reg_rd(input logic [7:0] op);
    is_reg_rd = (op == gemm_pkg::OP_RD_ID) || (op == gemm_pkg::OP_RD_STATUS)
             || (op == gemm_pkg::OP_RD_PERF) || (op == gemm_pkg::OP_RD_CFG);
  endfunction

  logic op_mem_wr;
  logic op_mem_rd;
  logic op_reg_wr;
  logic op_reg_rd;
  logic op_known;
  logic op_needs_idle;
  logic busy_any;

  assign op_mem_wr = is_mem_wr(rx_byte_i);
  assign op_mem_rd = is_mem_rd(rx_byte_i);
  assign op_reg_wr = is_reg_wr(rx_byte_i);
  assign op_reg_rd = is_reg_rd(rx_byte_i);
  assign op_known  = op_mem_wr || op_mem_rd || op_reg_wr || op_reg_rd
                  || (rx_byte_i == gemm_pkg::OP_NOP);

  // Anything that touches a matrix store needs the core port free. A trigger byte
  // is accepted at any time; the trigger decode decides which bits are legal.
  assign op_needs_idle = op_mem_wr || op_mem_rd;
  assign busy_any      = core_busy_i || vfy_busy_i;

  logic accept_op;
  logic reject_op;

  assign accept_op = (state_q == ST_OPCODE) && rx_valid_i && op_known
                  && !(op_needs_idle && busy_any);
  assign reject_op = (state_q == ST_OPCODE) && rx_valid_i
                  && (!op_known || (op_needs_idle && busy_any));

  // ---------------------------------------------------------------------------
  // Next state
  // ---------------------------------------------------------------------------
  always_comb begin
    state_d = state_q;
    case (state_q)
      ST_IDLE: begin
        if (frame_start_i) state_d = ST_OPCODE;
      end
      ST_OPCODE: begin
        if (rx_valid_i) begin
          if (reject_op)                   state_d = ST_DRAIN;
          else if (op_mem_wr || op_mem_rd) state_d = ST_ADDRHI;
          else if (op_reg_wr)              state_d = ST_REGVAL;
          else if (op_reg_rd)              state_d = ST_REGRD;
          else                             state_d = ST_DRAIN;  // NOP
        end
      end
      ST_ADDRHI: if (rx_valid_i) state_d = ST_ADDRLO;
      ST_ADDRLO: if (rx_valid_i) state_d = ST_MEM;
      ST_REGVAL: if (rx_valid_i) state_d = ST_DRAIN;
      default: ;  // ST_MEM, ST_REGRD and ST_DRAIN run until the frame ends
    endcase

    if (frame_end_i) state_d = ST_IDLE;
  end

  // A frame is truncated if it ends while the router is still waiting for bytes
  // the opcode requires: either half of an address, or a register write value.
  logic truncated;
  assign truncated = frame_end_i && ((state_q == ST_ADDRHI)
                                  || (state_q == ST_ADDRLO)
                                  || (state_q == ST_REGVAL));

  // ---------------------------------------------------------------------------
  // Store access
  // ---------------------------------------------------------------------------
  logic mem_wr_stb;
  logic mem_rd_stb;
  logic addr_lands;
  logic sel_a;
  logic sel_b;
  logic sel_c;
  logic sel_ref;

  assign addr_lands = (state_q == ST_ADDRLO) && rx_valid_i;
  assign mem_wr_stb = (state_q == ST_MEM) && rx_valid_i && is_mem_wr(opcode_q);
  assign mem_rd_stb = is_mem_rd(opcode_q)
                   && (addr_lands || ((state_q == ST_MEM) && rx_valid_i));

  assign sel_a   = (opcode_q == gemm_pkg::OP_WR_A)   || (opcode_q == gemm_pkg::OP_RD_A);
  assign sel_b   = (opcode_q == gemm_pkg::OP_WR_B)   || (opcode_q == gemm_pkg::OP_RD_B);
  assign sel_ref = (opcode_q == gemm_pkg::OP_WR_REF) || (opcode_q == gemm_pkg::OP_RD_REF);
  assign sel_c   = (opcode_q == gemm_pkg::OP_RD_C);

  // Reads run one byte ahead of the byte currently on the wire: the read issued
  // when the address lands fetches the first byte, and every completed byte
  // fetches its successor.
  logic [HOST_ADDR_W-1:0] mem_addr;
  logic [HOST_ADDR_W-9:0] addr_hi;

  assign addr_hi = addr_q[HOST_ADDR_W-1:8];

  always_comb begin
    if (addr_lands)      mem_addr = {addr_hi, rx_byte_i};
    else if (mem_wr_stb) mem_addr = addr_q;
    else                 mem_addr = addr_q + 1'b1;
  end

  // Address range check. The limit is the size of the store the opcode targets.
  // A write or an initial read address outside it is refused and recorded; a
  // prefetch that walks one byte past the end is simply suppressed, because that
  // is the normal way a full length read finishes and is not an error.
  logic [31:0] addr_limit;
  logic        addr_ok;
  logic        addr_err;

  always_comb begin
    if      (sel_a)   addr_limit = gemm_pkg::A_BYTES;
    else if (sel_b)   addr_limit = gemm_pkg::B_BYTES;
    else if (sel_c)   addr_limit = gemm_pkg::C_BYTES;
    else if (sel_ref) addr_limit = gemm_pkg::C_BYTES;
    else              addr_limit = 32'd0;
  end

  assign addr_ok  = (32'(mem_addr) < addr_limit);
  assign addr_err = (mem_wr_stb || (mem_rd_stb && addr_lands)) && !addr_ok;

  assign a_req_o   = (mem_wr_stb || mem_rd_stb) && sel_a && addr_ok;
  assign a_we_o    = mem_wr_stb && sel_a;
  assign a_baddr_o = mem_addr[A_BADDR_W-1:0];
  assign a_wdata_o = rx_byte_i;

  assign b_req_o   = (mem_wr_stb || mem_rd_stb) && sel_b && addr_ok;
  assign b_we_o    = mem_wr_stb && sel_b;
  assign b_baddr_o = mem_addr[B_BADDR_W-1:0];
  assign b_wdata_o = rx_byte_i;

  assign ref_req_o   = (mem_wr_stb || mem_rd_stb) && sel_ref && addr_ok;
  assign ref_we_o    = mem_wr_stb && sel_ref;
  assign ref_baddr_o = mem_addr[C_BADDR_W-1:0];
  assign ref_wdata_o = rx_byte_i;

  assign c_req_o   = mem_rd_stb && sel_c && addr_ok;
  assign c_baddr_o = mem_addr[C_BADDR_W-1:0];

  // ---------------------------------------------------------------------------
  // Sequential frame bookkeeping
  // ---------------------------------------------------------------------------
  logic advance_rd;
  assign advance_rd = (state_q == ST_REGRD) && rx_valid_i;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      state_q   <= ST_IDLE;
      opcode_q  <= gemm_pkg::OP_NOP;
      addr_q    <= '0;
      reg_idx_q <= '0;
    end else begin
      state_q <= state_d;

      if (accept_op) begin
        opcode_q <= rx_byte_i;
        // Byte 0 of a register read is parked immediately, so the index of the
        // byte to fetch next starts at one.
        reg_idx_q <= 4'd1;
      end

      if ((state_q == ST_ADDRHI) && rx_valid_i) begin
        addr_q[HOST_ADDR_W-1:8] <= rx_byte_i[HOST_ADDR_W-9:0];
      end
      if (addr_lands) begin
        addr_q[7:0] <= rx_byte_i;
      end
      if ((state_q == ST_MEM) && rx_valid_i) begin
        addr_q <= addr_q + 1'b1;
      end
      if (advance_rd) begin
        reg_idx_q <= reg_idx_q + 4'd1;
      end
    end
  end

  // ---------------------------------------------------------------------------
  // Readback byte assembly
  // ---------------------------------------------------------------------------
  logic [7:0] status_byte;

  always_comb begin
    status_byte                        = 8'h00;
    status_byte[gemm_pkg::ST_BUSY]     = core_busy_i;
    status_byte[gemm_pkg::ST_DONE]     = core_done_i;
    status_byte[gemm_pkg::ST_VFY_BUSY] = vfy_busy_i;
    status_byte[gemm_pkg::ST_VFY_DONE] = vfy_done_i;
    status_byte[gemm_pkg::ST_MISMATCH] = mismatch_i;
    status_byte[gemm_pkg::ST_CMD_ERR]  = cmd_err_o;
    status_byte[gemm_pkg::ST_FRAME_ERR]= frame_err_o;
    // ST_RST_ACK reads back zero: a completed soft reset is observable through
    // every other flag returning to its reset value.
    status_byte[gemm_pkg::ST_RST_ACK]  = 1'b0;
  end

  // One lookup for every register readback byte, indexed by opcode and byte
  // position. Geometry discovery through OP_RD_CFG is what lets host tooling
  // survive a change to MAT_* or TILE_* without being rebuilt.
  function automatic logic [7:0] reg_byte(input logic [7:0] op,
                                          input logic [3:0] idx);
    logic [7:0] res;
    begin
      res = 8'h00;
      case (op)
        gemm_pkg::OP_RD_ID: begin
          case (idx)
            4'd0: res = gemm_pkg::CHIP_ID[31:24];
            4'd1: res = gemm_pkg::CHIP_ID[23:16];
            4'd2: res = gemm_pkg::CHIP_ID[15:8];
            4'd3: res = gemm_pkg::CHIP_ID[7:0];
            default: res = 8'h00;   // past ID_BYTES
          endcase
        end
        gemm_pkg::OP_RD_STATUS: res = status_byte;
        gemm_pkg::OP_RD_PERF: begin
          // Indices at or above PERF_BYTES fall through to the zero default.
          case (idx)
            4'd0:  res = cycle_count_i[7:0];
            4'd1:  res = cycle_count_i[15:8];
            4'd2:  res = cycle_count_i[23:16];
            4'd3:  res = cycle_count_i[31:24];
            4'd4:  res = mac_count_i[7:0];
            4'd5:  res = mac_count_i[15:8];
            4'd6:  res = mac_count_i[23:16];
            4'd7:  res = mac_count_i[31:24];
            4'd8:  res = mismatch_count_i[7:0];
            4'd9:  res = mismatch_count_i[15:8];
            4'd10: res = first_mismatch_i[7:0];
            4'd11: res = first_mismatch_i[15:8];
            default: res = 8'h00;
          endcase
        end
        gemm_pkg::OP_RD_CFG: begin
          // Indices at or above CFG_BYTES fall through to the zero default.
          case (idx)
            4'd0: res = CFG_MAT_M[7:0];
            4'd1: res = CFG_MAT_N[7:0];
            4'd2: res = CFG_MAT_K[7:0];
            4'd3: res = CFG_TILE_M[7:0];
            4'd4: res = CFG_TILE_N[7:0];
            4'd5: res = CFG_TILE_K[7:0];
            4'd6: res = CFG_OP_W[7:0];
            4'd7: res = CFG_ACC_W[7:0];
            4'd8: res = CFG_ENGINES[7:0];
            4'd9: res = {{(8-SEL_W){1'b0}}, engine_sel_o};
            default: res = 8'h00;
          endcase
        end
        default: res = 8'h00;
      endcase
      // Anything past the declared payload length reads back zero.
      if ((op == gemm_pkg::OP_RD_ID)   && (32'(idx) >= gemm_pkg::ID_BYTES))   res = 8'h00;
      if ((op == gemm_pkg::OP_RD_PERF) && (32'(idx) >= gemm_pkg::PERF_BYTES)) res = 8'h00;
      if ((op == gemm_pkg::OP_RD_CFG)  && (32'(idx) >= gemm_pkg::CFG_BYTES))  res = 8'h00;
      reg_byte = res;
    end
  endfunction

  // Effective opcode and index for the byte being fetched this cycle. On the
  // cycle a register read opcode is accepted, opcode_q has not been updated yet.
  logic [7:0] rd_op_eff;
  logic [3:0] rd_idx_eff;

  assign rd_op_eff  = accept_op ? rx_byte_i : opcode_q;
  assign rd_idx_eff = accept_op ? 4'd0      : reg_idx_q;

  logic       mem_capture_q;
  logic [7:0] tx_byte_q;
  logic [7:0] store_rdata;

  // Store reads take one cycle inside the SRAM, so the request strobe is delayed
  // by one to know when the byte is on the store's host output.
  // A suppressed read must not capture: no access was issued, so the store's output
  // holds nothing meaningful. That covers both an out-of-range address and the
  // prefetch that runs one byte past the end of a store at the close of a full
  // length read. The previously parked byte is left in place instead, so an out of
  // range read returns the last valid byte rather than an undefined one.
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) mem_capture_q <= 1'b0;
    else         mem_capture_q <= mem_rd_stb && addr_ok;
  end

  always_comb begin
    if      (sel_a)   store_rdata = a_rdata_i;
    else if (sel_b)   store_rdata = b_rdata_i;
    else if (sel_c)   store_rdata = c_rdata_i;
    else if (sel_ref) store_rdata = ref_rdata_i;
    else              store_rdata = 8'h00;
  end

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni)                            tx_byte_q <= 8'h00;
    else if (frame_start_i)                 tx_byte_q <= 8'h00;
    else if (mem_capture_q)                 tx_byte_q <= store_rdata;
    else if (accept_op && op_reg_rd)        tx_byte_q <= reg_byte(rd_op_eff, rd_idx_eff);
    else if (advance_rd)                    tx_byte_q <= reg_byte(rd_op_eff, rd_idx_eff);
  end

  assign tx_byte_o = tx_byte_q;

  // ---------------------------------------------------------------------------
  // Triggers, engine selection and soft reset
  // ---------------------------------------------------------------------------
  logic       reg_val_stb;
  logic [7:0] reg_val;
  logic       trig_stb;

  assign reg_val_stb = (state_q == ST_REGVAL) && rx_valid_i;
  assign reg_val     = rx_byte_i;
  assign trig_stb    = reg_val_stb && (opcode_q == gemm_pkg::OP_WR_TRIG);

  // Starting a run or a verify while one is already in flight is refused and
  // reported. Counter and flag clears are harmless and always allowed.
  assign run_o          = trig_stb && reg_val[gemm_pkg::TRIG_RUN]    && !busy_any;
  assign verify_o       = trig_stb && reg_val[gemm_pkg::TRIG_VERIFY] && !busy_any;
  assign clear_c_o      = trig_stb && reg_val[gemm_pkg::TRIG_CLR_C]  && !busy_any;
  assign clear_perf_o   = trig_stb && reg_val[gemm_pkg::TRIG_CLR_PERF];
  assign clear_sticky_o = trig_stb && reg_val[gemm_pkg::TRIG_CLR_STICKY];
  assign soft_rst_o     = reg_val_stb && (opcode_q == gemm_pkg::OP_SOFT_RST)
                       && (reg_val == gemm_pkg::SOFT_RST_KEY);

  logic engine_stb;
  logic bad_engine;

  assign engine_stb = reg_val_stb && (opcode_q == gemm_pkg::OP_WR_ENGINE)
                   && (reg_val < CFG_ENGINES[7:0]);
  assign bad_engine = reg_val_stb && (opcode_q == gemm_pkg::OP_WR_ENGINE)
                   && (reg_val >= CFG_ENGINES[7:0]);

  logic [SEL_W-1:0] engine_sel_q;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni)         engine_sel_q <= '0;
    else if (soft_rst_o) engine_sel_q <= '0;
    else if (engine_stb) engine_sel_q <= reg_val[SEL_W-1:0];
  end

  assign engine_sel_o = engine_sel_q;

  // ---------------------------------------------------------------------------
  // Sticky error flags
  // ---------------------------------------------------------------------------
  // Sticky error flags. They are not brought out as ports: the only way to see
  // them is the status byte, which is where a controller looks anyway.
  logic cmd_err_q;
  logic frame_err_q;
  logic cmd_err_o;
  logic frame_err_o;
  logic bad_trigger;

  assign bad_trigger = trig_stb && busy_any
                    && (reg_val[gemm_pkg::TRIG_RUN]
                     || reg_val[gemm_pkg::TRIG_VERIFY]
                     || reg_val[gemm_pkg::TRIG_CLR_C]);

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      cmd_err_q   <= 1'b0;
      frame_err_q <= 1'b0;
    end else if (soft_rst_o || clear_sticky_o) begin
      cmd_err_q   <= 1'b0;
      frame_err_q <= 1'b0;
    end else begin
      if (reject_op || bad_engine || bad_trigger || addr_err) cmd_err_q <= 1'b1;
      if (truncated)                              frame_err_q <= 1'b1;
    end
  end

  assign cmd_err_o   = cmd_err_q;
  assign frame_err_o = frame_err_q;

endmodule
