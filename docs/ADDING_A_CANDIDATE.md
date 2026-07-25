# Adding a candidate

This repository is a harness for comparing INT8 matrix-multiply microarchitectures
against each other on the same silicon, under the same workload, with the same
measurement. Adding your own microarchitecture is meant to be a small, mechanical
change. This document is the contract.

Five files change. Nothing else in the design needs to know your candidate exists.

---

## 0. Start from the skeleton

`rtl/engines/engine_template.sv` is a complete, lint-clean, working single-cycle
candidate with the arithmetic factored out behind a marked block. Copy it, rename the
module, and replace what is below the marker.

```bash
cp rtl/engines/engine_template.sv rtl/engines/engine_mine.sv
sed -i 's/engine_template/engine_mine/' rtl/engines/engine_mine.sv
make lint-template          # the skeleton is kept lint clean on purpose
```

It is deliberately absent from `rtl/filelist.f`, so it never reaches a measurement.
As written it computes the right answer with an inferred multiply, which means it will
pass every correctness test and tell you nothing: the point is that it starts from a
known-good baseline rather than from a blank file.

## 1. The interface

Every candidate implements this exact port list. Copy it verbatim.

```systemverilog
module engine_<yourname> #(
  parameter int unsigned TILE_M     = gemm_pkg::TILE_M,
  parameter int unsigned TILE_N     = gemm_pkg::TILE_N,
  parameter int unsigned TILE_K     = gemm_pkg::TILE_K,
  parameter int unsigned OPERAND_W  = gemm_pkg::OPERAND_W,
  parameter int unsigned ACC_W      = gemm_pkg::ACC_W,
  parameter int unsigned DOT_W      = gemm_pkg::DOT_W,
  parameter int unsigned MAC_TICK_W = gemm_pkg::MAC_TICK_W
) (
  input  logic                                clk_i,
  input  logic                                rst_ni,
  input  logic                                acc_clear_i,
  input  logic                                launch_i,
  input  logic [TILE_M*TILE_K*OPERAND_W-1:0]  a_tile_i,
  input  logic [TILE_K*TILE_N*OPERAND_W-1:0]  b_tile_i,
  output logic [TILE_M*TILE_N*ACC_W-1:0]      c_tile_o,
  output logic                                ready_o,
  output logic                                valid_o,
  output logic [MAC_TICK_W-1:0]               mac_tick_o
);
```

### Port semantics

| Port | Meaning |
|---|---|
| `clk_i` | Gated clock from `engine_array`. It stops entirely when your candidate is not selected. Never use any other clock. |
| `rst_ni` | Active-low reset, asynchronously asserted and synchronously released upstream. A soft reset from the host also arrives here. |
| `acc_clear_i` | One-cycle pulse. Zero the accumulator bank on the next edge. Takes priority over `launch_i` in the same cycle. |
| `launch_i` | One-cycle pulse, only asserted when `ready_o` is high. Start absorbing the tile product presented on `a_tile_i` and `b_tile_i` into the accumulator. |
| `a_tile_i` | `TILE_M x TILE_K` INT8 operands, two's complement. Held stable from the launch cycle until you assert `valid_o`. |
| `b_tile_i` | `TILE_K x TILE_N` INT8 operands, same guarantee. |
| `c_tile_o` | `TILE_M x TILE_N` accumulators, INT`ACC_W`. Must hold its value from `valid_o` until the next `launch_i` or `acc_clear_i`. |
| `ready_o` | High when a `launch_i` would be accepted. A single-cycle candidate ties this to 1. |
| `valid_o` | One-cycle pulse on the cycle the accumulator bank has absorbed the launched tile. |
| `mac_tick_o` | Number of MACs retired this cycle. Report `TILE_M*TILE_N*TILE_K` on the `valid_o` cycle and zero otherwise, unless your candidate genuinely retires partial tiles. |

### Timing contract

```
cycle 0        ready_o = 1, launch_i asserted by the sequencer
cycle 0 .. L-1 your candidate does its work; ready_o may go low
cycle L        valid_o = 1, c_tile_o holds the accumulated result
```

`L` is your latency, and the design never assumes what it is: `gemm_sequencer`
waits on `ready_o` and `valid_o`. `engine_bitserial` takes `OPERAND_W` cycles and
the other four take one, and none of the surrounding logic changes between them.

### Flattening rule

Tile vectors are flat, not packed multi-dimensional arrays, because Yosys 0.33 does
not parse packed multi-dimensional declarations. Element `(r, c)` of an `R x C` tile
lives at bit offset `((r * C) + c) * ELEM_W`, with element `(0, 0)` in the least
significant bits. So:

```systemverilog
// A[m][k]
a_tile_i[((m * TILE_K) + k) * OPERAND_W +: OPERAND_W]
// B[k][n]
b_tile_i[((k * TILE_N) + n) * OPERAND_W +: OPERAND_W]
// C[m][n]
c_tile_o[((m * TILE_N) + n) * ACC_W +: ACC_W]
```

### What your candidate must compute

```
for every launch:
  C[m][n] += sum over k of A[m][k] * B[k][n]
```

Bit-exact, in two's complement, wrapping at `ACC_W` bits. Not approximately, not
saturating, not rounded. Every candidate must produce the same bits, because the
whole point is comparing implementations of the same function. If your idea is an
approximate multiplier, that is a legitimate and interesting thing to measure but
it needs a different harness: the equivalence tests here will fail it, correctly.

### What you may reuse

- `acc_bank.sv`: the shared accumulator bank. Using it keeps the accumulators out
  of the comparison so the measured difference is your arithmetic and nothing else.
  All five existing candidates use it.
- `csa_reduce.sv`: a parameterised Wallace 3:2 reduction tree over `N_IN` addends.

---

## 2. Register it

Two edits.

**`rtl/pkg/gemm_pkg.sv`**: bump the count and add an index.

```systemverilog
  parameter int unsigned ENGINE_COUNT = 6;   // was 5
  ...
  parameter int unsigned ENG_YOURNAME = 5;
```

`ENGINE_SEL_W` is derived, so the SPI command and the status readback widen on
their own.

**`rtl/seq/engine_array.sv`**: add one instance, copying the pattern of the others.

```systemverilog
  engine_yourname #(
    .TILE_M (TILE_M), .TILE_N (TILE_N), .TILE_K (TILE_K),
    .OPERAND_W (OPERAND_W), .ACC_W (ACC_W)
  ) u_engine_yourname (
    .clk_i       (clk_gated[gemm_pkg::ENG_YOURNAME]),
    .rst_ni      (rst_ni),
    .acc_clear_i (eng_clear[gemm_pkg::ENG_YOURNAME]),
    .launch_i    (eng_launch[gemm_pkg::ENG_YOURNAME]),
    .a_tile_i    (eng_a_tile[gemm_pkg::ENG_YOURNAME*A_TILE_W +: A_TILE_W]),
    .b_tile_i    (eng_b_tile[gemm_pkg::ENG_YOURNAME*B_TILE_W +: B_TILE_W]),
    .c_tile_o    (eng_c_tile[gemm_pkg::ENG_YOURNAME*C_TILE_W +: C_TILE_W]),
    .ready_o     (eng_ready[gemm_pkg::ENG_YOURNAME]),
    .valid_o     (eng_valid[gemm_pkg::ENG_YOURNAME]),
    .mac_tick_o  (eng_mac_tick[gemm_pkg::ENG_YOURNAME*MAC_TICK_W +: MAC_TICK_W])
  );
```

The clock gate, the operand isolation and the output mux are all generated from
`ENGINE_COUNT`, so there is nothing else to wire.

**`rtl/filelist.f`**: add your source files, before `rtl/seq/engine_array.sv`.

---

## 3. Tell the testbench and the tools

**`tb/tb_engine_harness.sv`**: add one instance so the equivalence and
bit-exactness tests cover your candidate. Same pattern as the others.

**`tb/gemm_model.py`**: three entries.

```python
ENGINE_COUNT = 6

ENG_YOURNAME = 5

ENGINE_NAMES = {..., 5: "yourname"}

# Your launch-to-valid latency. test_engine_exact.test_mac_tick_and_latency
# measures this and fails if the number here is wrong, so it cannot silently rot.
ENGINE_LATENCY = {..., 5: 1}

# If yours is the slowest candidate, point ENG_SLOWEST at it: the tests that drive
# every candidate at once wait on that one.
ENG_SLOWEST = ENG_BITSERIAL
```

**`tools/synth_collect.py`**: add `"engine_yourname"` to `ENGINE_TOPS`.

**`tools/activity_sweep.py`**: add `gm.ENG_YOURNAME: "u_eng5"` to
`ENGINE_INSTANCES`, matching the instance name you used in the harness.

**`tools/plot_ppa.py` and `tools/plot_activity.py`**: add your name to `ORDER`, a
colour to `COLOURS`, and a label to `LABELS`.

---

## 4. Run the measurement

```bash
make lint          # Verilator -Wall, zero warnings expected
make sim           # bit-exactness, equivalence and the full chip flow
make synth         # cells, gate equivalents and logic depth, per candidate
make power         # switching activity at gate level and RTL
make images        # every chart regenerated from the new data
```

`make sim` is where a wrong candidate fails, and it fails specifically:

- `test_engine_exact` compares your candidate against NumPy on corner operands,
  five randomised operand distributions, accumulation depth up to `2*GRID_K`, and
  the INT32 headroom case. It also measures your latency and checks it against
  `ENGINE_LATENCY`.
- `test_engine_equiv` compares your candidate against every other candidate
  pairwise, and sweeps the multiplier operand space.
- `test_end_to_end` runs your candidate through the whole chip over SPI and checks
  the full `MAT_M x MAT_N` result with the on-chip comparator.
- `test_perf_counters` checks that your candidate's cycle count matches the
  analytic formula given your measured latency, and that it retires exactly
  `MAT_M*MAT_N*MAT_K` MACs.
- `test_reset_gating` checks that your candidate's clock actually stops when it is
  not selected, and that its operand inputs are isolated.

---

## 5. Checklist

- [ ] `engine_<name>.sv` compiles with `verilator --lint-only -Wall` with zero warnings
- [ ] No packed multi-dimensional declarations (Yosys 0.33 rejects them)
- [ ] No `int unsigned` in function signatures (same reason)
- [ ] Registered in `gemm_pkg.sv`, `engine_array.sv`, `filelist.f`
- [ ] Instantiated in `tb_engine_harness.sv`
- [ ] `gemm_model.py` updated, including `ENGINE_LATENCY`
- [ ] `synth_collect.py`, `activity_sweep.py`, both plot scripts updated
- [ ] `make lint sim synth power images` all pass
- [ ] `results/` regenerated and committed, so your numbers are in the repository

---

## Portability notes learned the hard way

The toolchain here is Verilator 5.020 for lint, Icarus 12.0 for simulation and
Yosys 0.33 for synthesis. Yosys is the strictest of the three, and these are the
constructs that cost time:

| Construct | Problem | Use instead |
|---|---|---|
| `logic [A-1:0][B-1:0] x;` | Yosys 0.33 syntax error | `logic [A*B-1:0] x;` with part-selects, or an unpacked array `logic [B-1:0] x [A];` |
| `function automatic int unsigned f(...)` | Yosys 0.33 syntax error | `function automatic integer f(input integer ...)` with a Verilog-2001 body |
| `while` in a constant function | Yosys cannot fold it | a bounded `for` loop |
| `logic [N-1:0] arr [M];` feeding itself across generate iterations | Verilator `UNOPTFLAT` | add `/*verilator split_var*/` to the declaration |
| `WIDTH'(expr)` on a `localparam` | Verilator `WIDTHTRUNC` | a 32-bit `localparam` sliced at use |
| `initial` blocks with `$fatal` | Yosys does not run them | wrap in `` `ifndef YOSYS `` |
| `.port` implicit connections | uncertain Yosys support | `.port(signal)` |

`rtl/engines/csa_reduce.sv` and `rtl/pkg/gemm_pkg.sv` carry comments explaining
each of these where they bite.
