// Copyright 2026 Daniel Tyukov
// SPDX-License-Identifier: Apache-2.0
//
// Carry-save addend reduction tree (Wallace style 3:2 compression).
//
// Takes N_IN addends of WIDTH bits packed into one flat vector and reduces them
// to a redundant sum/carry pair. Each layer takes groups of three addends and
// compresses them to two with a row of full adders; leftovers pass through
// untouched. Repeating that until two addends remain is exactly a Wallace tree,
// and the critical path grows as log_1.5(N_IN) full adder delays rather than
// linearly in N_IN.
//
// The caller adds sum_o and carry_o with one carry propagating adder.
//
// Addends are packed LSB first: addend i occupies bits [i*WIDTH +: WIDTH].
// Reduction is done modulo 2**WIDTH, so callers must sign extend their addends
// to WIDTH before handing them over.

module csa_reduce #(
  parameter int unsigned N_IN  = 9,
  parameter int unsigned WIDTH = 16
) (
  input  logic [N_IN*WIDTH-1:0] addends_i,
  output logic [WIDTH-1:0]      sum_o,
  output logic [WIDTH-1:0]      carry_o
);

  localparam int unsigned LAYERS = gemm_pkg::csa_layers(N_IN);

  if (N_IN < 2) begin : gen_degenerate
    assign sum_o   = addends_i[WIDTH-1:0];
    assign carry_o = '0;
  end else if (LAYERS == 0) begin : gen_passthrough
    // Already two addends, nothing to compress.
    assign sum_o   = addends_i[0*WIDTH +: WIDTH];
    assign carry_o = addends_i[1*WIDTH +: WIDTH];
  end else begin : gen_tree
    // layer_vec[l] holds the addends entering layer l, sized to the widest
    // layer (the input) so one array can carry every stage. Deliberately an
    // unpacked array of flat vectors: Yosys 0.33 does not parse packed
    // multi-dimensional declarations, and Verilator treats unpacked elements as
    // independent signals instead of one circular blob.
    logic [N_IN*WIDTH-1:0] layer_vec [LAYERS+1] /*verilator split_var*/;

    assign layer_vec[0] = addends_i;

    for (genvar l = 0; l < LAYERS; l++) begin : gen_layer
      localparam int unsigned N_CUR  = gemm_pkg::csa_width_at(N_IN, l);
      localparam int unsigned GROUPS = N_CUR / 3;
      localparam int unsigned REST   = N_CUR % 3;

      // Each group of three addends becomes a sum word and a carry word.
      for (genvar g = 0; g < GROUPS; g++) begin : gen_group
        logic [WIDTH-1:0] a, b, c, s, cy;
        logic [WIDTH-2:0] maj;

        assign a = layer_vec[l][(3*g + 0)*WIDTH +: WIDTH];
        assign b = layer_vec[l][(3*g + 1)*WIDTH +: WIDTH];
        assign c = layer_vec[l][(3*g + 2)*WIDTH +: WIDTH];

        // Full adder row: the sum bit stays in place, the carry bit moves up one
        // position. No majority gate is built for the top bit because its carry
        // would fall outside WIDTH, which is the correct truncation for a
        // modulo 2**WIDTH accumulation.
        assign s   = a ^ b ^ c;
        assign maj = (a[WIDTH-2:0] & b[WIDTH-2:0])
                   | (a[WIDTH-2:0] & c[WIDTH-2:0])
                   | (b[WIDTH-2:0] & c[WIDTH-2:0]);
        assign cy  = {maj, 1'b0};

        assign layer_vec[l+1][(2*g + 0)*WIDTH +: WIDTH] = s;
        assign layer_vec[l+1][(2*g + 1)*WIDTH +: WIDTH] = cy;
      end

      // Addends that did not fill a group of three move to the next layer.
      for (genvar r = 0; r < REST; r++) begin : gen_rest
        assign layer_vec[l+1][(2*GROUPS + r)*WIDTH +: WIDTH] =
            layer_vec[l][(3*GROUPS + r)*WIDTH +: WIDTH];
      end

      // Unused upper slots of this layer are tied off so nothing floats.
      localparam int unsigned N_NEXT = 2*GROUPS + REST;
      if (N_NEXT < N_IN) begin : gen_tieoff
        assign layer_vec[l+1][N_IN*WIDTH-1 : N_NEXT*WIDTH] = '0;
      end
    end

    assign sum_o   = layer_vec[LAYERS][0*WIDTH +: WIDTH];
    assign carry_o = layer_vec[LAYERS][1*WIDTH +: WIDTH];
  end

endmodule
