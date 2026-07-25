# PPA methodology

This chip exists to answer one question: for INT8 matrix multiply on a 130 nm
process, which multiplier microarchitecture wins, and by how much? That means every
number in this repository has to be defensible. This document says exactly how each
one is produced, and, more importantly, what each one does not mean.

The short version:

| Metric | What is measured | Status |
|---|---|---|
| Area | Yosys cell counts and gate equivalents (generic gates); cell area in um2 from the IHP SG13G2 liberty when the PDK is present | measured from synthesis, not from layout |
| Performance | cycles and MAC count read out of the chip's own counters over SPI | measured in simulation, matched against a closed-form model |
| Power | bit transitions per net, weighted by Hamming distance, on gate level netlists | a proxy, not power |
| Layout | not produced | OpenROAD and the SG13G2 physical views are not installed here |

---

## What has and has not been run

Run, and committed:

- Verilator 5.020 `--lint-only -Wall` on the whole chip: zero warnings.
- Icarus Verilog 12.0 through cocotb 2.0.1 for the whole test suite.
- Yosys 0.33 generic synthesis for every candidate, `engine_array`, `bench_core`
  and `gemm_bench_chip`. Reports in `results/synth/generic/`.
- Yosys 0.33 gate level netlists for every candidate, simulated and checked against
  a reference before their activity is counted.
- The switching-activity sweep, at RTL and at gate level.

Partly run:

- **Place and route, on one candidate, up to detailed routing.** OpenROAD 26Q3 and the
  IHP SG13G2 standard cell views became available late in development, so
  `flow/openroad/block_flow.tcl` was written and run on `engine_booth4`. What completed:

  | Step | Result |
  |---|---|
  | Floorplan | 878 um square die, 726,259 um2 core, 45.2 percent utilisation, 30,389 instances |
  | Pin placement | 781 pins placed, IO net half-perimeter wire length 377,063 um |
  | Power grid | ring on TopMetal1 and TopMetal2, Metal1 followpin rails |
  | Global and detailed placement | legal, zero displacement in legalisation |
  | Clock tree synthesis | complete |
  | Setup and hold repair | **no setup violations, no hold violations** at the 20 ns target |
  | Detailed routing | **did not complete** |

  Detailed routing stalls in its pin query. The cause is identifiable: `launch_i` reaches
  all 512 accumulator registers as a single net, which OpenROAD warns about (DRT-0120),
  and the block SDC had no fanout limit so the resizer never built a buffer tree for it.
  `set_max_fanout` is now in the script, but the corrected run has not been completed
  here, so **there is no GDS and no routed area number in this repository.**

  Five real bugs in the flow were found by running it rather than by reading it: a
  missing `make_tracks`, an unnamed voltage domain, two deprecated routing arguments, and
  logic constants arriving as literals rather than tie cells. All five are fixed. Treat
  the chip-level sequence in `flow/openroad/floorplan.tcl` and its siblings as still
  untested: it has never been run, and it has a pad ring and SRAM macros that the block
  flow does not exercise.

Not run:
- **Real power analysis.** That needs a placed and routed netlist, which does not exist,
  and a switching activity file. `flow/openroad/finish.tcl` calls `report_power` and takes
  a VCD through `POWER_VCD`, and that is the only place in this repository that
  would produce a power number in watts.
- **Silicon.** Nothing has been fabricated.

Nowhere in this repository is a synthesis estimate presented as a layout result, or
a transition count presented as power. If you find such a place, it is a bug.

---

## Area

### Generic mode: `make synth`

`flow/yosys/synth.tcl` in `generic` mode runs `synth -top <module> -flatten`, then
`memory_map`, then maps to a small fixed gate set with
`abc -fast -g AND,NAND,OR,NOR,XOR,XNOR,ANDNOT,ORNOT,MUX`.

Three numbers come out:

**Cell count.** Total mapped cells, split into combinational cells and flip-flops.
Directly comparable between candidates because they all go through the identical
script, including `-fast`.

**Gate equivalents.** `tools/synth_collect.py` weights each cell by its static CMOS
transistor count and divides by four, so a two-input NAND is 1.0 GE. The weights
are in the `TRANSISTORS` and `FF_TRANSISTORS` tables in that file and are properties
of static CMOS rather than of any process: a NAND2 is four transistors, a XOR2 is
twelve, a plain D flip-flop is about twenty-four. This makes a XOR-heavy design
cost more than an AND-heavy one of the same cell count, which cell counting alone
misses.

Gate equivalents are **not area.** They ignore drive strength, cell height, routing
and the fact that a real library's XOR2 is not exactly three times its NAND2.

**Logic depth.** Yosys `ltp -noff` reports the longest topological path through the
mapped netlist, in gate levels. Fewer levels means a shorter critical path and a
higher achievable clock, but this is a **count of gates, not a delay**: no cell
timing is involved, and a path of forty fast cells can be quicker than a path of
thirty slow ones.

### SG13G2 mode: `make synth-pdk`

With `SG13G2_LIB` pointing at the IHP liberty file, the same script maps to real
standard cells with `dfflibmap` and `abc -fast -liberty`, and `stat -liberty`
reports cell area in square micrometres straight from the library.

This is real PDK area, and it is still not a die size. It excludes routing, filler,
tap cells, the power grid, the pad frame and every other thing place and route adds.
Expect a real die to be substantially larger.

One important caveat for the memory-bearing tops: this build binds **no SRAM
macros**, so `memory_map` turns all four matrix stores into flip-flops. `store C`
and `store REF` are 4 KiB each, so `bench_core` and `gemm_bench_chip` are dominated
by 74 kbit of flip-flops that a macro-backed implementation would replace with four
compiled SRAM cuts at a fraction of the area. The per-candidate numbers are
unaffected: the candidates contain no memory.

---

## Performance

`make sim` runs `tb/test_perf_counters.py`, which loads operands over SPI, triggers
a run, waits for done and reads `OP_RD_PERF`. Those are the chip's own counters, not
a simulator timestamp.

The cycle count is then checked against a closed-form model of the sequencer:

```
FETCH_LEN  = max(TILE_M, TILE_K)
per k tile = (FETCH_LEN + 1) + 1 + L        fetch, launch, wait
per o tile = 1 + GRID_K * (per k tile) + TILE_M
total      = GRID_M * GRID_N * (per o tile)
```

`L` is the candidate's launch-to-valid latency. That is the only term that is not a
design parameter, and it is not assumed: `test_engine_exact.test_mac_tick_and_latency`
measures it at the engine harness level and fails if
`gemm_model.ENGINE_LATENCY` disagrees. So the analytic check is not circular.

The MAC count expectation is stronger: `MAT_M * MAT_N * MAT_K` regardless of
candidate, latency or cycle count. That is what makes MACs per cycle a fair
throughput comparison between candidates that take different numbers of cycles.

The cycle count is also asserted to be **data independent**: the same workload costs
the same cycles every time, and different operand data costs the same cycles. That
matters because a data-dependent cycle count would make performance a property of
the benchmark rather than of the design.

---

## Power: the switching-activity proxy

This is the part that needs the most care, because it is the part most easily
overstated.

### What is measured

`tools/vcd_activity.py` parses a VCD and counts, for every net, the number of bit
transitions over an observation window. A scalar net going 0 to 1 is one transition.
A vector net changing from `0x0F` to `0x11` is **three** transitions, because three
of its bits flipped: the Hamming distance between the two values.

Totals are then aggregated per module scope, so activity can be attributed to a
candidate, to a submodule inside it, or to the whole design.

### Why Hamming distance

Dynamic power in CMOS is approximately

```
P_dyn = sum over nodes of  alpha * C_load * V^2 * f
```

where `alpha` is the switching probability of the node. Counting bit transitions
estimates the sum of `alpha` across the nodes the netlist exposes, with every node
weighted equally. Counting a 32-bit bus change as one event instead would
under-weight wide datapaths by up to a factor of the bus width, which is exactly
the effect this chip exists to measure. Hamming distance is the right primitive.

### Why gate level, not RTL, for the headline number

The candidates are deliberately described at different levels of abstraction.
`engine_infer` is a behavioural `*` and `+`, so at RTL it has almost no internal
nets: the simulator evaluates one multiply operation and produces one result. Its
RTL transition count is therefore artificially low and **not comparable** with the
structural candidates, which expose every partial product and every carry-save
adder output as a net.

The fix is to measure after synthesis. `tools/activity_sweep.py --level gate`
synthesises each candidate to a flat generic gate netlist, simulates that netlist
with the identical operand stream, and counts transitions on it. Post-synthesis
every candidate is the same kind of object, so the comparison is like for like.
This is the headline measurement and the one the README quotes.

The RTL sweep is kept as a secondary measurement because it gives a per-module
breakdown that a flattened netlist cannot, and that breakdown is genuinely useful
for understanding where activity lives inside a candidate. It is reported as
internal proportions, never as a cross-candidate comparison.

### Why Icarus and not Verilator

Icarus's VCD contains every net in the design. Verilator optimises nets away before
dumping, so a per-module breakdown taken from a Verilator dump would partly reflect
the simulator's optimiser rather than the RTL. For an activity measurement that is
disqualifying.

### What the proxy does not tell you

- **Every net is weighted equally.** Real capacitance varies by more than an order
  of magnitude between a short local wire and a long spine. A design that moves
  activity from long nets to short ones would look unchanged here and be
  substantially cheaper in silicon.
- **Glitching is invisible.** A combinational multiplier array glitches heavily as
  carries settle, and glitch power can be a large fraction of the dynamic power of
  a Wallace tree. The gate level simulation here has zero cell delays, so every net
  settles instantly and no glitch is ever counted. This systematically favours deep
  combinational designs and is the single largest known bias in the measurement.
  Closing it needs a delay-annotated gate level simulation with SDF, which needs
  the PDK.
- **Clock tree power is not counted.** The clock tree does not exist until CTS.
- **Memory array power is not counted.** The behavioural SRAM model has no internal
  nets that correspond to a real bit cell array, sense amplifier or decoder.
- **Leakage is not modelled at all.** At 130 nm leakage is a smaller share than at
  modern nodes, but it is not zero, and it correlates with area rather than with
  activity, so a large low-activity design can lose on total power.
- **It is not watts.** There is no capacitance, no voltage and no frequency in the
  number. It is a count.

Read the numbers as a **relative ranking of designs under an identical workload**.
That is what they are good for, and for choosing between candidate
microarchitectures it is the question that matters.

### Determinism

The parser is deterministic: the same VCD always produces identical output, because
scopes are emitted in sorted order and nothing depends on dictionary iteration order
or wall-clock time. `tools/activity_sweep.py` asserts this by parsing every dump
twice and comparing.

The observation window excludes reset. Transitions at or before `SETTLE_TIME` set a
net's initial value without counting, so no candidate is charged for coming out of
reset.

### Making the comparison controlled

Two design choices make the sign-magnitude comparison a controlled experiment
rather than an anecdote:

1. **`engine_signmag` and `engine_wallace` share the same reduction tree.** They
   both use `csa_reduce` with the same layer structure and the same final adder.
   The only difference is that one generates signed partial products with the
   negate-the-last-product trick and the other converts operands to sign-magnitude,
   multiplies unsigned magnitudes and applies the sign once at the array output. So
   the gap between them is the encoding, not the tree.

2. **The sign-magnitude conversion is inside the engine, not in shared logic.** A
   tempting alternative is to convert operands once before the candidate mux and
   hand sign-magnitude operands to the candidates that want them. That would make
   the sign-magnitude candidate look better by moving its converters out of its own
   subtree and by quieting its input wires. Charging the conversion to the candidate
   that needs it is the honest accounting, and it keeps the engine interface
   identical for all candidates, which is what makes this repository a template.

### Sweeping operand statistics

Activity depends on the workload, so a single number is not an answer. The sweep
varies the fraction of operands that are negative from 0 to 1.

That is the right axis for this hypothesis. In two's complement, `-1` is all ones
and `0` is all zeros, so an operand stream that crosses zero flips every high-order
bit. In sign-magnitude the same crossing flips one sign bit and leaves the magnitude
bits alone. If the sign-magnitude hypothesis holds, the two's complement candidates'
activity should rise with the rate of sign changes while the sign-magnitude
candidate stays flatter. `docs/img/activity_vs_signs.png` is that plot, and it is
drawn with a relative panel against `engine_wallace` so the encoding effect is
isolated from everything else.

The finding is reported in the README exactly as measured, including where it does
not support the hypothesis.

---

## Clock gating, and why it matters to the measurement

A test chip with five candidates on it has a problem the production accelerator it
models does not: four fifths of the arithmetic is idle at any moment, and if the
idle parts keep switching, the measurement is meaningless.

`engine_array` handles this three ways:

1. **Clock gating.** One integrated clock gate per candidate. Only the selected
   candidate's clock runs.
2. **Operand isolation.** Operand tiles are AND-gated per candidate, so an
   unselected candidate sees constant zero. This is not optional: a combinational
   multiplier array has no clock to gate, so gating alone would leave it toggling
   on every operand change.
3. **Control isolation.** `launch` and `clear` are gated too.

Both 1 and 2 are asserted directly by `tb/test_reset_gating.py`, which counts edges
on every candidate's gated clock and non-zero cycles on every candidate's operand
inputs across a full run, for each selection in turn.

The cost is real and is charged to shared logic rather than to any candidate:
`TILE_M*TILE_K + TILE_K*TILE_N` operand bytes times `ENGINE_COUNT` AND gates, plus
five clock gates. `results/synth/generic/summary.json` reports `engine_array`
separately from the candidates so the overhead is visible. A production design with
one datapath would not pay it.

---

## Reproducing everything

```bash
make venv
make lint
make sim      # populates results/perf/ and results/trace/
make synth    # populates results/synth/generic/
make power    # populates results/activity/
make images   # regenerates every figure in docs/img/ from the above
```

With the PDK:

```bash
tools/fetch_pdk.sh
source pdk/env.sh
make synth-pdk    # real cell area in um2
make flow         # place and route, untested here
```

Everything in `results/` is committed, so the charts in the README can be checked
against the data that produced them without running anything.
