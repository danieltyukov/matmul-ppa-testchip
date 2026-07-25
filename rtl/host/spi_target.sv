// Copyright 2026 Daniel Tyukov
// SPDX-License-Identifier: Apache-2.0
//
// SPI target (subordinate), Mode 0, MSB first, 8 bit bytes.
//
//   CPOL = 0: the SPI clock idles low.
//   CPHA = 0: the controller drives MOSI before the rising edge and samples
//             MISO on the rising edge.
//   Chip select is active low. A frame is everything between the falling and
//   rising edge of chip select. There is no byte count in the protocol: frame
//   length is whatever the controller clocks out.
//
// Clock domain strategy: the SPI pins are oversampled in the core clock domain
// rather than used as a clock. Every flop in the chip therefore runs on one
// clock, which removes an entire class of CDC bugs and makes the whole design
// synthesise and time as a single-clock block. The price is a frequency ratio
// requirement:
//
//   f_spi <= f_core / 8
//
// Eight core cycles per SPI half period leaves ample margin over the four that
// edge detection strictly needs, and the byte-boundary handshake below gives the
// command router a whole SPI byte period to produce the next outgoing byte.
//
// MISO timing: the outgoing shift register is loaded on the SPI clock rising
// edge that completes the previous byte, and shifted on falling edges. The
// controller samples on rising edges, so every MISO bit is stable for close to a
// full SPI period before it is sampled. The first byte of a frame reads back
// 0x00, because at that point the chip has not yet seen an opcode.

module spi_target (
  input  logic       clk_i,
  input  logic       rst_ni,

  // Pins, already through the pad ring, asynchronous to clk_i.
  input  logic       spi_sck_i,
  input  logic       spi_cs_ni,
  input  logic       spi_mosi_i,
  output logic       spi_miso_o,
  output logic       spi_miso_oe_o,   // drive MISO only while selected

  // Byte stream towards the command router, all in the core clock domain.
  output logic       frame_start_o,   // one cycle pulse, chip select asserted
  output logic       frame_end_o,     // one cycle pulse, chip select released
  output logic       rx_valid_o,      // one cycle pulse, rx_byte_o is complete
  output logic [7:0] rx_byte_o,
  input  logic [7:0] tx_byte_i        // next byte out, sampled at byte boundaries
);

  // ---------------------------------------------------------------------------
  // Pin synchronisation and edge detection
  // ---------------------------------------------------------------------------
  logic sck_s;
  logic cs_n_s;
  logic mosi_s;
  logic sck_q;
  logic cs_active_q;
  logic cs_active;
  logic sck_rise;
  logic sck_fall;

  sync_2ff #(.WIDTH(1), .RESET_VALUE(1'b0)) u_sync_sck  (
    .clk_i (clk_i), .rst_ni (rst_ni), .d_i (spi_sck_i),  .q_o (sck_s));
  sync_2ff #(.WIDTH(1), .RESET_VALUE(1'b1)) u_sync_cs   (
    .clk_i (clk_i), .rst_ni (rst_ni), .d_i (spi_cs_ni),  .q_o (cs_n_s));
  sync_2ff #(.WIDTH(1), .RESET_VALUE(1'b0)) u_sync_mosi (
    .clk_i (clk_i), .rst_ni (rst_ni), .d_i (spi_mosi_i), .q_o (mosi_s));

  assign cs_active = !cs_n_s;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      sck_q       <= 1'b0;
      cs_active_q <= 1'b0;
    end else begin
      sck_q       <= sck_s;
      cs_active_q <= cs_active;
    end
  end

  // Clock edges only count while the chip is selected.
  assign sck_rise = cs_active &&  sck_s && !sck_q;
  assign sck_fall = cs_active && !sck_s &&  sck_q;

  assign frame_start_o =  cs_active && !cs_active_q;
  assign frame_end_o   = !cs_active &&  cs_active_q;

  // ---------------------------------------------------------------------------
  // Shift registers
  // ---------------------------------------------------------------------------
  logic [2:0] bit_cnt_q;
  logic [6:0] rx_shift_q;   // bit 7 goes straight into rx_byte_q
  logic [7:0] tx_shift_q;
  logic [7:0] rx_byte_q;
  logic       rx_valid_q;
  logic       byte_done;

  assign byte_done = sck_rise && (bit_cnt_q == 3'd7);

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      bit_cnt_q  <= '0;
      rx_shift_q <= '0;
      tx_shift_q <= '0;
      rx_byte_q  <= '0;
      rx_valid_q <= 1'b0;
    end else begin
      rx_valid_q <= byte_done;

      if (frame_start_o || frame_end_o) begin
        // A frame boundary always realigns the bit counter. The first byte out
        // of a new frame is a placeholder, because no opcode has been decoded
        // yet at that point.
        bit_cnt_q <= '0;
        if (frame_start_o) tx_shift_q <= 8'h00;
      end else if (sck_rise) begin
        rx_shift_q <= {rx_shift_q[5:0], mosi_s};
        bit_cnt_q  <= (bit_cnt_q == 3'd7) ? 3'd0 : (bit_cnt_q + 3'd1);
        if (bit_cnt_q == 3'd7) begin
          rx_byte_q  <= {rx_shift_q, mosi_s};
          tx_shift_q <= tx_byte_i;
        end
      end else if (sck_fall && (bit_cnt_q != 3'd0)) begin
        // Skipped when bit_cnt_q is zero so the falling edge right after a byte
        // boundary does not shift away the byte that was just loaded.
        tx_shift_q <= {tx_shift_q[6:0], 1'b0};
      end
    end
  end

  assign rx_valid_o    = rx_valid_q;
  assign rx_byte_o     = rx_byte_q;
  assign spi_miso_o    = tx_shift_q[7];
  assign spi_miso_oe_o = cs_active;

endmodule
