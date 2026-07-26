# PPA methodology

This chip exists to answer one question: for INT8 matrix multiply on a 130 nm
process, which multiplier microarchitecture wins, and by how much? That means every
number in this repository has to be defensible. This document says exactly how each
one is produced, and, more importantly, what each one does not mean.

The short version:

| Metric | What is measured | Status |
|---|---|---|
| Area | Yosys cell counts and gate equivalents (generic gates); cell area in um2 from the IHP SG13G2 liberty; die area from the routed layout | synthesis and layout, both committed, labelled apart |
| Performance | cycles and MAC count read out of the chip's own counters over SPI; maximum frequency from signoff timing on the routed netlist at three PDK corners | measured in simulation and after routing |
| Power | bit transitions per net weighted by Hamming distance (proxy), and watts from OpenROAD with switching activity annotated from a gate level VCD | both, reported side by side, and they do not agree by the same margin |
| Layout | routed GDS per candidate, DRC and LVS clean | produced by LibreLane on the IHP SG13G2 PDK |

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
- **Real IHP SG13G2 synthesis, timing and power per candidate**, by
  `tools/pdk_ppa.py`: cell area in um2, path delay at the slow corner, and power in
  watts with switching activity annotated from a gate level VCD of the same netlist.
  Results in `results/pdk/`.
- **Place and route to a signed-off GDS per candidate**, by `tools/run_pnr.py` driving
  LibreLane on the IHP SG13G2 PDK: floorplan, power grid, placement, clock tree,
  routing, parasitic extraction, signoff timing at three corners, Magic and KLayout DRC,
  LVS against the netlist, and antenna, slew and capacitance checks. Metrics in
  `results/pnr/`, renders in `docs/img/layout_*.png`.

- **Real SG13G2 area for `engine_array`**, the five candidates in full-chip context with
  their clock gates and operand isolation: 2,233,332 um2 against 1,665,153 um2 for the
  five candidates standalone, so the logic that makes the measurement valid costs 34
  percent on top of the arithmetic. That is the integration cost, and it is charged to
  shared logic rather than to any candidate.

- **Determinism of the place and route flow**, checked rather than assumed.
  `engine_bitserial` was routed a second time from the identical committed configuration
  under a separate run tag, on a machine running three other flows concurrently. All 191
  keys in `final/metrics.json` matched, including worst slack at every corner, area,
  wirelength, power, and the DRC, LVS and antenna counts. Differences between candidates
  in `results/pnr/` are therefore differences between designs.

Not run:

- **`engine_array` through place and route.** It has a LibreLane configuration in
  `flow/librelane/engine_array/`, committed and reproducible with
  `tools/run_pnr.py --tops engine_array`, and it has a real synthesis area. It has no
  routed result. The flow was run and did not finish inside the time budget available
  here: it synthesises, floorplans, places, gets a clock tree and reaches detailed
  routing, where it presents OpenROAD with **1,167,004 routing guides against 288,273 for
  the largest candidate**, roughly four times the work. Magic DRC, which is
  single-threaded and has no thread control to tune, then scales the same way.

  So the integration cost quoted above is a synthesis number. **There is no die area, no
  routed frequency and no routed power for `engine_array`**, and the 34 percent overhead
  should be read as a cell-area ratio at the typical corner rather than as a routed
  result. Every routed number in this repository is a single candidate.
- **The chip-level flow.** `flow/openroad/floorplan.tcl` and its siblings place and route
  `gemm_bench_chip` with a pad ring and SRAM macros. That sequence has never completed:
  the candidates are what has been routed, and every routed number in this repository is
  a candidate.
- **A delay-annotated gate level simulation.** The PDK's specify blocks have to be
  stripped for Icarus to parse the cell models, so the gate level simulation is
  zero-delay and no glitch is ever counted. This is the largest known bias in both the
  proxy and the annotated power number, and it flatters deep combinational designs.
- **Silicon.** Nothing has been fabricated.

The earlier hand-written OpenROAD block flow, `flow/openroad/block_flow.tcl`, still
works and is still committed. It reached clock tree synthesis and timing closure on
`engine_booth4` with no setup and no hold violations, and stalled in detailed routing
because `launch_i` reached all 512 accumulator registers as one unbuffered net. Five real
bugs in that flow were found by running it rather than by reading it: a missing
`make_tracks`, an unnamed voltage domain, two deprecated routing arguments, and logic
constants arriving as literals rather than tie cells. The fanout limit that fixes the
stall is now in the script, and it is also why `constraints/block.sdc` sets one. The
LibreLane flow is what produced the committed GDS.

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

### Routed area: `make pnr`

`tools/run_pnr.py` runs LibreLane's Classic flow on each candidate and reads
`final/metrics.json`. Three area numbers come out of it and they mean different things:

- `design__instance__area__stdcell` is the design's own cells after routing. It is
  larger than the synthesis number because place and route inserts the buffering the
  netlist needs and the resizer upsizes cells to meet timing. The difference is not
  overhead in the pejorative sense: a synthesis netlist with a 512-way unbuffered net is
  not a design that can be built.
- `design__instance__area` adds the fill cells, which exist to satisfy the density
  rules and are not the design. Never compare this against synthesis.
- `design__die__area` is the die. It includes the routing, the power grid, the fill and
  the margin around the core at the configured 40 percent target utilisation.

Every candidate is routed at the same 20 ns constraint and the same target utilisation,
so these are comparable across candidates. Closing each candidate at its own best period
instead would measure each one under a different amount of optimisation pressure, which
is exactly the confound this chip exists to remove.

### Routed power: two numbers that are not the same number

`results/pnr/summary.json` and `results/pnr/routed_power.json` both report watts after
routing, and they answer different questions. Quoting one for the other is the easiest
mistake to make here.

- `power__total` in `summary.json` is what OpenROAD reports during the flow, at its
  **default switching activity**. Nothing is annotated from a workload. It is a bound
  the flow produces for free, it is roughly five times the measured figure, and it
  should be read as an upper bound rather than as this design's power.
- `results/pnr/routed_power.json` is the measured one. `tools/verify_routed.py`
  simulates LibreLane's final netlist, the one the GDS was streamed from, against the
  reference model, and annotates power from the VCD of that run with the parasitics
  extracted from the routing. Real cells, real wire capacitance, real workload, and the
  clock tree included because by then it exists.

That second number carries a `functional` field, and it is not decoration. A power
figure taken from a netlist that computes the wrong answer is worthless, so the same
run checks every output element of every tile against NumPy before its activity is
counted. It also reports annotation coverage; anything below 1.0 means some pins fell
back to a default and the number is partly synthetic.

The zero-delay caveat survives routing. Icarus needs the PDK's specify blocks stripped
to parse the cell models, so the post-route simulation is still zero-delay and still
counts no glitch. This remains the largest known bias in every power number here.

---

## Timing, and a trap worth documenting

The first version of the timing measurement in this repository reported 32.9 MHz for
`engine_infer`, `engine_wallace`, `engine_booth4` and `engine_signmag`. All four. Within
15 picoseconds of each other. Four different multiplier microarchitectures cannot have
the same critical path, and the reason they appeared to is worth writing down, because
the mistake is easy to make and the number looks plausible.

Two things were wrong.

**Unconstrained inputs are invisible.** The SDC constrained `rst_ni`, `acc_clear_i` and
`launch_i` but not `a_tile_i` or `b_tile_i`. An input port with no arrival time has no
setup requirement, so every path through the multiplier array was excluded from the
analysis. What was being timed was the control logic.

**A Yosys netlist has no buffering.** `acc_clear_i` reaches all `TILE_M*TILE_N`
accumulator flip-flops, which is 512 of them, through one gate. Yosys does no
load-aware buffering, so that gate drives 512 loads and takes 21 ns to do it. That path
is identical in every candidate because the accumulator bank is shared, which is why the
four numbers agreed.

So `tools/pdk_ppa.py` now constrains every port and reports two numbers per candidate:

- `critical_path_ns`, the worst path in the netlist, with `limiting_path` naming its
  endpoints so a reader can see for themselves what limits it.
- `datapath_path_ns`, the worst path from the operand ports to whatever register they
  reach. That is the arithmetic, and it is what differs between the candidates.

Neither is the design's frequency. **The routed number is**, because place and route
buffers the control net properly: `results/pnr/summary.json` reports
`1/(period - worst setup slack)` from signoff timing on the routed netlist with
parasitics extracted from the routing, at all three PDK corners, and the slow corner is
the one to quote.

One detail makes that arithmetic exact rather than approximate.
`constraints/block.sdc` sets the IO budget to a fixed 1 ns instead of a fraction of the
clock period. With a proportional budget the slack moves for two reasons when the period
moves, and `1/(period - slack)` is then wrong.

That is not a hypothetical. An early trial of `engine_bitserial` ran with an SDC whose
budget was `period * 0.2`, so 4 ns at a 20 ns period, and reported 67.1 MHz where the
committed constraint reports 77.3. The design lost 3 ns of external budget it did not need
to lose, and the reported figure was computed with the wrong arithmetic on top of that.
The temptation is to write a 15 percent gap like that down as tool variance; the
determinism check above rules that out, and the constraint accounts for it exactly.

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
