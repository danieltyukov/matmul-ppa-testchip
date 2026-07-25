# Architecture

![Block diagram](img/architecture.svg)

The chip is a benchmark harness in silicon. It holds two INT8 matrices, computes
their product with one of several interchangeable multiplier microarchitectures, and
reports how long that took, how many operations it performed and whether the answer
was right. Everything else follows from wanting that comparison to be fair.

Target process: IHP SG13G2, 130 nm, open-source. Flow: Yosys plus OpenROAD.

---

## Geometry

All of it is in `rtl/pkg/gemm_pkg.sv`, which is the only place any of these numbers
appear.

| Parameter | Default | Meaning |
|---|---|---|
| `MAT_M`, `MAT_N`, `MAT_K` | 32, 32, 32 | full matrix dimensions. A is M x K, B is K x N, C is M x N |
| `TILE_M`, `TILE_N`, `TILE_K` | 4, 4, 4 | what one engine invocation computes |
| `GRID_M`, `GRID_N`, `GRID_K` | 8, 8, 8 | tile grid, derived |
| `OPERAND_W` | 8 | INT8 two's complement operands |
| `ACC_W` | 32 | INT32 accumulators |
| `ENGINE_COUNT` | 5 | candidates on the die |

Per run: 512 tile operations, 64 MACs each, 32768 MACs total.

`bench_core.sv` checks at elaboration that the matrices tile evenly, that `ACC_W`
can hold a product, that `TILE_K`, `TILE_N` and `GRID_N` are powers of two (the host
byte address maps to a word and a lane by slicing, which needs that), and that
`ENGINE_COUNT` is at least one. Wrong combinations fail to elaborate rather than
producing wrong silicon.

`ACC_W = 32` is not arbitrary. The largest magnitude the chip can produce for one
output element is `MAT_K * 128 * 128 = 524288`, comfortably inside INT32, and
`test_engine_exact.test_accumulator_headroom` asserts that rather than assuming it.

---

## Data layout, stated once

Matrices are row major. A flattened `R x C` tile packs element `(r, c)` at bit
offset `((r * C) + c) * ELEM_W`, element `(0, 0)` in the least significant bits.

The host byte address of a matrix element is its plain row-major index, times four
for the INT32 matrices, little endian within an accumulator. That is not a
coincidence, it is a design constraint: it means a host can `memcpy` a row-major
array straight into the chip.

Everything in the repository follows this: `tb/gemm_model.py` implements it, the
protocol tests assert it and the figures are drawn from it.

---

## Host interface

### `spi_target.sv`

SPI Mode 0 (CPOL=0, CPHA=0), MSB first, 8-bit bytes, chip select active low. A frame
is everything between the falling and rising edge of chip select, with no length
field: the frame is as long as the controller keeps chip select low.

The design decision worth explaining is that **the SPI pins are oversampled in the
core clock domain rather than used as a clock.** Every flop in the chip runs on one
clock, which removes an entire class of clock domain crossing bugs, makes the design
time as a single-clock block, and means the SDC has one `create_clock` and three
false paths.

The price is a frequency ratio requirement: `f_spi <= f_core / 8`, which puts at
least four core cycles in an SPI half period. Two are needed for the synchroniser
and edge detection, three are needed by the command router to fetch the byte that
answers the byte just received, so four is the floor.
`test_spi_protocol.test_spi_clock_ratio_sweep` runs at exactly that ratio and at
three slower ones.

MISO is loaded on the first falling edge of each byte and shifted on the following
seven. The controller samples on rising edges, so every bit is stable for half an
SPI period before it is sampled, and the byte going out during byte n is the answer
to byte n-1: one byte of command latency, which is what any SPI controller expects.

![SPI frame timing](img/spi_frame_timing.svg)

### `frame_router.sv`

Turns the byte stream into memory accesses, control pulses and readback bytes. Frame
layouts and the full opcode table are in [MEMORY_MAP.md](MEMORY_MAP.md).

Four behaviours are worth calling out, because they are what makes the protocol
usable from a real lab setup rather than only from a cooperative testbench:

**Auto-incrementing addresses.** A whole operand matrix is one frame; a partial
update is a shorter frame at an offset. There is no per-byte address overhead.

**Prefetched readback.** When the address lands (memory reads) or the opcode is
decoded (register reads), the byte that goes out next is fetched and parked. Each
completed byte triggers the fetch of the one after it, so a fetch always has a full
SPI byte period and never gates the SPI clock.

**Errors are reported, not swallowed.** An unknown opcode, an out-of-range address,
an engine index the build does not have, or a run trigger while the chip is busy all
set a sticky command error bit. A frame that ends while the router is still waiting
for an address half or a register value sets a sticky frame error bit. Both are
visible in the status byte and clearable with a trigger. Silence would be worse: a
lab bring-up needs to know the difference between a chip that computed the wrong
answer and a chip that never received the command.

**Store access is refused while the core is busy.** The stores are single-port and
the sequencer owns them during a run, so a host write would have to be dropped or
would corrupt the run. It is refused and reported instead. Status, performance
counter, identification and geometry reads stay available at all times, so a
controller can poll progress.

---

## Storage

Four matrices, each in its own single-port SRAM behind `rtl/lib/sram_1rw.sv`.

| Store | Contents | Size | Words |
|---|---|---|---|
| A | 32 x 32 INT8 | 1 KiB | 256 x 32 bit |
| B | 32 x 32 INT8 | 1 KiB | 256 x 32 bit |
| C | 32 x 32 INT32 | 4 KiB | 256 x 128 bit |
| REF | golden C | 4 KiB | 256 x 128 bit |

Words are cut so that **one word is exactly the slice of a matrix row that a tile
fetch needs**: an A word is `TILE_K` operand bytes of one A row, a B word is
`TILE_N` bytes of one B row, a C word is `TILE_N` accumulators of one C row.

That choice is deliberate and it is the main place this design differs from a
textbook one. A tile fetch is therefore `TILE_M` reads from A and `TILE_K` reads
from B, not one magic wide port. Real SRAM has one port and a one-cycle read, and a
design that pretends otherwise measures a memory system that cannot be built. The
cost shows up honestly in the cycle count: five of the seven cycles per k tile are
operand fetch.

`matrix_store.sv` puts two views on each SRAM: a word-wide core port and a
byte-wide, byte-addressed host port with byte enables. Arbitration is fixed priority
to the core, and the host side is expected not to compete because `frame_router`
refuses host access during a run. Nothing is silently dropped: the arbitration
decision lives one level up where it can be reported.

`sram_1rw.sv` is the one intentional technology boundary. The default body is a
synthesisable behavioural array, so the whole chip simulates and synthesises with
nothing but Verilator, Icarus and Yosys. `GEMM_SRAM_MACRO` swaps in PDK macros; the
port list is the contract that binding satisfies. IHP SG13G2 ships
`RM_IHPSG13_1P_*` single-port cuts with bit write enables, which map onto `wstrb_i`
directly.

---

## Sequencer

![Output-stationary dataflow](img/dataflow_output_stationary.svg)

`gemm_sequencer.sv` runs the whole product from one trigger:

```
for mt in 0 .. GRID_M-1:
  for nt in 0 .. GRID_N-1:
    clear the accumulator bank
    for kt in 0 .. GRID_K-1:
      fetch A tile (mt, kt) and B tile (kt, nt)
      launch the selected engine, wait for valid
    write the accumulator bank to store C
```

**Output stationary** means the accumulator for one output tile stays resident
across the whole `kt` loop. Each A tile is fetched `GRID_N` times and each B tile
`GRID_M` times, but no partial sum ever leaves the accumulator registers. The
alternative, streaming partial sums back to memory, would triple the C traffic and
add a read-modify-write to the critical path.

State machine, and the cycle cost that follows from it:

| State | Cycles | What happens |
|---|---|---|
| `S_INIT` | 1 | assert `acc_clear` |
| `S_FETCH` | `max(TILE_M, TILE_K) + 1` | issue A and B reads in parallel, capture one cycle later |
| `S_LAUNCH` | 1 | assert `launch` when the engine is ready |
| `S_WAIT` | `L` | wait for `valid` |
| `S_WB` | `TILE_M` | write the output tile to store C |

```
per k tile = (FETCH_LEN + 1) + 1 + L
per o tile = 1 + GRID_K * (per k tile) + TILE_M
total      = GRID_M * GRID_N * (per o tile)
```

The sequencer never assumes `L`. It waits on `ready_o` and `valid_o`, which is
exactly what lets `engine_bitserial` take eight cycles per tile while the others
take one, with nothing else in the design changing.

A separate `S_CLR` state zeroes the whole result store on request, which the tests
use to prove that a candidate really computed the result rather than passing on the
previous candidate's leftovers.

---

## Candidate engines

`engine_array.sv` holds all five candidates and selects one at runtime. It does
three things beyond an output mux, all of which exist to make the PPA measurement
mean something. They are described in
[PPA_METHODOLOGY.md](PPA_METHODOLOGY.md#clock-gating-and-why-it-matters-to-the-measurement).

The candidates, and what each one is testing:

| # | Module | Approach | Latency |
|---|---|---|---|
| 0 | `engine_infer` | `*` and `+`, so Yosys and ABC choose. The control point: every other candidate has to beat what the synthesiser produces on its own. | 1 |
| 1 | `engine_wallace` | Explicit signed partial products reduced by a 3:2 carry-save tree, then one carry-propagating adder per product. Nothing left to multiplier inference. | 1 |
| 2 | `engine_booth4` | Radix-4 modified Booth recoding, so `OPERAND_W/2` partial products instead of `OPERAND_W`, then the same tree. Fewer addends, more multiplexing per addend. | 1 |
| 3 | `engine_signmag` | Operands converted to sign-magnitude inside the engine, multiplied as unsigned magnitudes, sign applied once at the array output. Same tree as candidate 1, so the difference is the encoding and nothing else. | 1 |
| 4 | `engine_bitserial` | No multiplier at all: operand B consumed one bit plane at a time, most significant first, Horner's method. The extreme area point, and the reason the interface carries `ready`/`valid`. | 8 |

Shared building blocks:

- `csa_reduce.sv`: a parameterised Wallace 3:2 reduction tree over N addends of W
  bits. Each layer takes groups of three and compresses them to two with a row of
  full adders; leftovers pass through. Critical path grows as `log_1.5(N)` full
  adder delays rather than linearly.
- `acc_bank.sv`: the output-stationary accumulator bank, one `ACC_W` register per
  output tile element. Common to every candidate on purpose, so the measured
  difference between candidates comes from their arithmetic rather than from
  different accumulator implementations.

### Signed multiplication, for the record

`engine_wallace` uses the negate-the-last-partial-product form. With multiplier
`B = -b[W-1]*2^(W-1) + sum_{j<W-1} b[j]*2^j`:

```
A*B = sum_{j=0}^{W-2} (b[j] ? A : 0) << j  -  (b[W-1] ? A : 0) << (W-1)
```

The subtraction becomes an addition of the bitwise complement plus one, and that one
is folded into a single extra addend. So one `OPERAND_W` wide signed multiply is
`OPERAND_W + 1` addends.

`engine_booth4` splits the multiplier into overlapping triplets
`(b[2i+1], b[2i], b[2i-1])` with `b[-1] = 0`, each mapping to a digit in
`{-2,-1,0,+1,+2}`. `sum_i digit_i * 4^i` reproduces the signed multiplier exactly,
so no separate sign correction is needed on the multiplier side. Negative digits
become complement plus one, and the ones are collected into a single extra addend
with each bit at its digit's weight.

`engine_signmag` converts by negating the whole `OPERAND_W` wide word rather than
just the magnitude bits, which is what makes `|-2^(W-1)| = 2^(W-1)` come out right.
That is why the magnitude is `OPERAND_W` bits wide rather than `OPERAND_W - 1`, and
it is a real cost of the encoding.

`engine_bitserial` uses Horner's method over the bit planes:

```
P_j = sum_k A[m][k] * B[k][n][j]
r   = -P_{W-1}                 first step, the sign bit plane
r   = 2*r + P_j                for j = W-2 .. 0
```

The doubling is a wired shift, so the only arithmetic per cycle is one narrow signed
adder tree plus one wider add.

---

## Measurement

`cycle_meter.sv` counts core cycles while the sequencer is busy, so a readback after
a run is the cost of that run and nothing else. A run trigger clears it. It saturates
rather than wrapping, because a wrapped counter reads like a fast run.

`mac_meter.sv` sums `mac_tick` every cycle. The engine interface reports a *count*
of MACs retired this cycle rather than a single pulse, which keeps the counter honest
for a candidate that retires a partial tile per cycle and makes the expected total
purely a property of the workload.

`result_checker.sv` walks store C against store REF, one word from each per cycle,
and reports how many output elements differ and the row-major index of the first one.
This exists so silicon bring-up does not need to read 4 KiB back over SPI at every
step: load a reference once, run, verify, read one status bit. It also localises a
mismatch on chip, which matters when a failure only appears at a corner voltage.

The two stores are separate SRAMs, so both reads happen in the same cycle and the
walk costs `C_WORDS + 2` cycles.

---

## Reset

The external reset pin is asynchronous. `reset_bridge.sv` asserts asynchronously and
releases synchronously, and every sequential element in the core uses its output.

A soft reset from the host resets the **datapath** (sequencer, checker, meters,
engines, engine selection and the sticky flags) but not the SPI front end, so the
frame carrying the soft reset command can finish cleanly and the host stays in sync.
The one-cycle pulse is stretched to four so every datapath flop sees it. The operand
stores are memory, not state, and survive: `test_spi_protocol.test_soft_reset`
asserts both halves of that.

`OP_SOFT_RST` carries a key byte (`0x5A`), so a stray frame cannot reset the chip.

---

## Pins

| Pad | Direction | Function |
|---|---|---|
| `pad_clk_i` | in | core clock |
| `pad_rst_ni` | in | asynchronous reset, active low |
| `pad_test_mode_i` | in | ungate every candidate's clock, for scan and characterisation |
| `pad_spi_sck_i` | in | SPI clock, Mode 0, `<= f_core/8` |
| `pad_spi_cs_ni` | in | chip select, active low |
| `pad_spi_mosi_i` | in | host to chip |
| `pad_spi_miso_io` | inout | chip to host, driven only while selected |
| `pad_stat_busy_o` | out | sequencer or checker running |
| `pad_stat_done_o` | out | last run completed |
| `pad_stat_vfy_done_o` | out | last verify completed |
| `pad_stat_mismatch_o` | out | comparator found a difference |

Eleven signal pads plus supplies. That fits a small QFN, which is the point of
choosing SPI over a parallel host bus: a test chip that needs a 100-pin package to
bring up is a test chip that does not get brought up.

`pad_frame.sv` is plain wires plus an explicit tristate on MISO by default.
`GEMM_PAD_MACRO` binds IHP SG13G2 IO cells; the port list is the contract.

---

## Module index

| Module | File | Role |
|---|---|---|
| `gemm_bench_chip` | `rtl/top/gemm_bench_chip.sv` | chip top: pad frame plus core |
| `pad_frame` | `rtl/top/pad_frame.sv` | IO, with a PDK macro hook |
| `bench_core` | `rtl/top/bench_core.sv` | everything inside the pad ring, plus elaboration checks |
| `spi_target` | `rtl/host/spi_target.sv` | oversampled SPI Mode 0 target |
| `frame_router` | `rtl/host/frame_router.sv` | frame decode, readback, error reporting |
| `matrix_store` | `rtl/mem/matrix_store.sv` | word and byte views on one SRAM |
| `sram_1rw` | `rtl/lib/sram_1rw.sv` | SRAM technology wrapper |
| `gemm_sequencer` | `rtl/seq/gemm_sequencer.sv` | output-stationary tile loops |
| `engine_array` | `rtl/seq/engine_array.sv` | candidate select, clock gating, isolation |
| `engine_*` | `rtl/engines/engine_*.sv` | the five candidates |
| `csa_reduce` | `rtl/engines/csa_reduce.sv` | Wallace 3:2 reduction tree |
| `acc_bank` | `rtl/engines/acc_bank.sv` | shared accumulators |
| `cycle_meter` | `rtl/measure/cycle_meter.sv` | cycle counter |
| `mac_meter` | `rtl/measure/mac_meter.sv` | MAC counter |
| `result_checker` | `rtl/measure/result_checker.sv` | on-chip comparator |
| `reset_bridge` | `rtl/lib/reset_bridge.sv` | async assert, sync release |
| `sync_2ff` | `rtl/lib/sync_2ff.sv` | two flop synchroniser |
| `clock_gate` | `rtl/lib/clock_gate.sv` | ICG, with a PDK macro hook |
