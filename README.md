# matmul-ppa-testchip

An open-source ASIC test chip that measures the power, performance and area of
competing INT8 matrix-multiply microarchitectures: on the same die, under the same
workload, with the same measurement, through the same flow, to a routed GDS.

[![lint](https://github.com/danieltyukov/matmul-ppa-testchip/actions/workflows/lint.yml/badge.svg)](https://github.com/danieltyukov/matmul-ppa-testchip/actions/workflows/lint.yml)
[![sim](https://github.com/danieltyukov/matmul-ppa-testchip/actions/workflows/sim.yml/badge.svg)](https://github.com/danieltyukov/matmul-ppa-testchip/actions/workflows/sim.yml)
[![synth](https://github.com/danieltyukov/matmul-ppa-testchip/actions/workflows/synth.yml/badge.svg)](https://github.com/danieltyukov/matmul-ppa-testchip/actions/workflows/synth.yml)
[![licence: Apache-2.0](https://img.shields.io/badge/licence-Apache--2.0-blue)](LICENSE)

Target: IHP SG13G2 130 nm open-source PDK. Flow: Yosys, OpenROAD and LibreLane, to a
DRC and LVS clean GDS. RTL: synthesisable SystemVerilog. Verification: cocotb with real
assertions, run again on the routed netlist.

---

## The question

Anyone building an INT8 accelerator has to pick a multiplier. The textbooks offer
Wallace trees, Booth recoding, bit-serial arrays, and the option of writing `*` and
letting the synthesiser decide. The literature offers numbers from different processes,
tile sizes, accumulator widths and workloads, which makes them close to incomparable.

This chip settles it for one process and one tile size. Five candidate
microarchitectures sit on the same die behind an identical interface, selectable at
runtime. The chip runs the same 32x32x32 INT8 GEMM through each of them, reports
cycles, MAC count and switching activity, and checks every result against a golden
matrix with an on-chip comparator. Each candidate is then placed and routed on its own
at an identical clock constraint, so the area and frequency numbers are physical rather
than estimated.

There is a second, narrower question underneath it, and it is the reason candidate 3
exists: **does sign-magnitude operand encoding actually reduce power?** The answer is in
[the sign-magnitude result](#the-sign-magnitude-result), and it is a qualified no as
often as it is a yes.

![Architecture](docs/img/architecture.svg)

## The candidates

| # | Module | Approach | Latency |
|---|---|---|---|
| 0 | `engine_infer` | `*` and `+`, so Yosys and ABC choose the structure. The control point. | 1 cycle |
| 1 | `engine_wallace` | Explicit signed partial products, 3:2 carry-save reduction tree | 1 cycle |
| 2 | `engine_booth4` | Radix-4 modified Booth recoding, then the same tree | 1 cycle |
| 3 | `engine_signmag` | Sign-magnitude datapath: unsigned magnitude array, sign applied once | 1 cycle |
| 4 | `engine_bitserial` | No multiplier. Horner's method over 8 bit planes | 8 cycles |

Two more scopes are measured alongside them, because a candidate in isolation is not the
whole story:

| Scope | What it is | Why it is measured |
|---|---|---|
| `engine_array` | all five candidates, their clock gates and their operand isolation | the candidates in full-chip context, and the price of making the measurement valid |
| `bench_core` | the whole benchmark core: sequencer, stores, meters, SPI | where the memory cost shows up |

Every candidate is hand-written and builds from this repository with nothing but
Verilator, Icarus and Yosys. There is no external generator, no private tool, and no
committed netlist you cannot regenerate.

### What makes this a controlled experiment

Comparing microarchitectures is easy to do badly. Four things here exist specifically to
stop the comparison from being an anecdote:

1. **Identical interface.** Every candidate implements the same port list, so nothing
   about the surrounding design changes when the selection changes.
2. **Identical workload.** The same operand stream, from the same seed, reaches every
   candidate. The sweeps vary one property of the data at a time.
3. **Identical constraint through place and route.** Every candidate routes at the same
   20 ns period and the same 40 percent target utilisation. Closing each at its own best
   period would measure each under a different amount of optimisation pressure, which is
   exactly the confound worth removing.
4. **The costs land on whoever incurs them.** The sign-magnitude converters live inside
   `engine_signmag`, not in shared logic. Moving them out would have flattered the
   candidate this chip was built to test.

`engine_signmag` and `engine_wallace` go further and share the same `csa_reduce` tree and
the same final adder. The only difference between them is the operand encoding, so the
gap between them is the encoding and nothing else.

---

## Results

All measured, all committed under `results/`, all charts regenerated from those files by
`make images`. `make report` prints the tables below straight from the same JSON, which
is how they were checked. Read [docs/PPA_METHODOLOGY.md](docs/PPA_METHODOLOGY.md) for
exactly what each number does and does not mean.

### Post-route PPA per candidate

This is the headline table, and every column in it is physical. Area and frequency come
from LibreLane's own `final/metrics.json` after routing, not from synthesis. Power is
measured on the netlist the GDS was streamed from, with parasitics extracted from the
routing.

| Candidate | Routed cell area | Die area | Post-route Fmax | Power | Energy/tile | Cycles |
|---|---|---|---|---|---|---|
| `engine_infer` | 443,933 um2 | 0.939 mm2 | 81.4 MHz | 8.73 mW | 446 pJ | 3,904 |
| `engine_wallace` | still routing | still routing | still routing | still routing | still routing | 3,904 |
| `engine_booth4` | **383,415 um2** | **0.796 mm2** | **83.0 MHz** | **8.21 mW** | **419 pJ** | 3,904 |
| `engine_signmag` | 394,146 um2 | 0.819 mm2 | 69.2 MHz | 8.41 mW | 429 pJ | 3,904 |
| `engine_bitserial` | 185,463 um2 | 0.356 mm2 | 77.3 MHz | 6.51 mW | 1,245 pJ | 7,488 |

Every routed candidate is clean: 0 Magic DRC errors, 0 KLayout DRC errors, 0 LVS errors
and 0 routing DRC violations, with positive hold slack at every corner. A handful of
antenna violations survive (1 on bit-serial, 9 on infer, 21 on Booth), and they are
reported rather than swept up. `engine_wallace` is still in place and route as this is
written; its row appears when its flow finishes, and nothing is estimated in the
meantime.

**Booth recoding wins the routed comparison outright.** Among the four single-cycle
candidates it is simultaneously the smallest die, the fastest at signoff and the lowest
energy per tile. That is unusual: area, speed and energy normally trade against each
other, and halving the partial product count improves all three at once.

**Sign-magnitude does not come out ahead once the design is routed.** `engine_signmag`
costs 2.4 percent more energy per tile than Booth and is 17 percent slower at signoff,
69.2 MHz against 83.0 MHz. The encoding's extra logic depth shows up as frequency, which
a synthesis-stage power sweep cannot see. The controlled comparison against
`engine_wallace`, which shares its reduction tree, is the one that isolates the encoding,
and it needs the Wallace route to finish.

`engine_bitserial` draws the lowest power of anything here, 6.51 mW, and that is exactly
the trap described [below](#does-the-cheap-proxy-predict-real-power): it spends 7,488
cycles to everyone else's 3,904, so it uses **three times the energy** for the same
work. Its die is 2.2 times smaller, which is the reason to build it.

Synthesis cell area to routed cell area grows by 13 to 24 percent for three of the four,
and by only 1.7 percent for `engine_signmag`. The die is then roughly twice the routed
cell area again at 40 percent target utilisation. Compounding those, a die is 2.1 to 2.4
times the synthesis cell area, which is why synthesis area should never be quoted as a
die size.

<details>
<summary>Per-candidate route detail</summary>

| Candidate | Instances | Utilisation | Wirelength | Synthesis to routed cell growth | Antenna |
|---|---|---|---|---|---|
| `engine_infer` | 90,326 | 49.6% | 1,284 mm | +13.1% | 9 |
| `engine_booth4` | 75,990 | 50.6% | 1,120 mm | +16.8% | 21 |
| `engine_signmag` | 78,552 | 50.6% | 1,396 mm | +1.7% | 2 |
| `engine_bitserial` | 30,628 | 56.1% | 414 mm | +23.7% | 1 |

`engine_signmag` routes the most wire of any candidate despite not being the largest,
which is the operand conversion fanning out across the array.

</details>

#### One caveat about repeatability

`engine_bitserial` was routed twice from the identical configuration during this work.
The two runs gave **67.1 MHz and 77.3 MHz**, a 15 percent spread, from cell areas that
differed by 0.04 percent. Multi-threaded detailed routing and timing repair are not
bit-reproducible, and the slack that falls out of them moves more than the area does.

The table quotes the second run, because that is the run whose GDS and netlist are on
disk and whose power was measured. Treat single-run Fmax differences of a few percent
between candidates as noise; the 14 MHz gap between Booth and sign-magnitude is larger
than the spread observed here, and the area numbers are solid either way.

Fmax is `1/(period - worst setup slack)` from signoff STA at `nom_slow_1p08V_125C`, the
corner a tapeout closes at. That arithmetic is exact rather than approximate because
`constraints/block.sdc` fixes the IO budget at 1 ns instead of taking a fraction of the
clock period, so nothing in the constraint moves when the period does.

![Post-route area](docs/img/pnr_area.png)

![Post-route Fmax](docs/img/pnr_fmax.png)

Three area numbers come out of routing and they mean different things. Confusing them is
the easiest way to overstate a result:

- **Routed cell area** (`design__instance__area__stdcell`) is the design's own cells after
  routing. It is larger than the synthesis figure because place and route inserts the
  buffering the netlist actually needs and the resizer upsizes cells to meet timing. That
  growth is not waste: a synthesis netlist with a 512-way unbuffered net is not a design
  that can be built.
- **Instance area** adds the fill cells that exist only to satisfy density rules. Never
  compare this against synthesis.
- **Die area** is the die, including routing, the power grid, the fill and the margin
  around the core at the configured utilisation.

### What the same function looks like in silicon

Functionally equivalent engines, the same flow, the same 20 ns constraint, rendered at
one scale so the size difference is a difference on the page rather than a number in a
table. `engine_wallace` joins them when its route finishes.

![Layout contact sheet](docs/img/layout_contact_sheet.png)

The bit-serial engine is the obvious one: a third of the die of anything else, because it
has no multiplier array at all. Less obvious is `engine_signmag`, whose placed core is
visibly rounder and less uniform at its edges than Booth's rectangular mat. That is the
operand conversion logic, which does not tile as regularly as a reduction tree and is the
same logic that makes it route the most wire of any candidate.

The zoom sheet takes the same physical window of silicon from each candidate at the same
magnification, which is where the microarchitecture is actually visible rather than just
the die size:

![Layout zoom contact sheet](docs/img/layout_zoom_contact_sheet.png)

![engine_bitserial routed](docs/img/layout_engine_bitserial.png)

These are rendered from the routed GDS by `tools/render_gds.py` driving KLayout with the
PDK's own layer properties. If a GDS is missing the script says so rather than drawing
something that looks like a layout and is not one.

### Technology-independent PPA

Yosys generic synthesis, before any PDK is involved. Useful because the ranking it
produces is not a property of one library's cost model.

| Candidate | Cells | Gate equivalents | Logic depth | Cycles | MACs/cycle | Transitions/tile |
|---|---|---|---|---|---|---|
| `engine_infer` | 39,482 | 79,683 | 51 | 3,904 | 8.39 | 13,251 |
| `engine_wallace` | 46,526 | 89,590 | 53 | 3,904 | 8.39 | 14,630 |
| `engine_booth4` | 34,169 | 71,648 | 52 | 3,904 | 8.39 | 12,698 |
| `engine_signmag` | 42,361 | 82,184 | 59 | 3,904 | 8.39 | 12,118 |
| `engine_bitserial` | 11,760 | 27,768 | 59 | 7,488 | 4.38 | 25,561 |

Gate equivalents weight each cell by its static CMOS transistor count, so a XOR-heavy
design costs more than an AND-heavy one of the same cell count. That is technology
independent and it is still not area. Logic depth is a count of gate levels, not a
delay.

### Real PDK cell area at synthesis

Mapped to the IHP SG13G2 standard cell library, so this is square micrometres rather than
a gate count. It is the number to compare against the routed cell area above, and the gap
between the two is what place and route adds.

| Candidate | Cells | Cell area | Relative | Area per MAC |
|---|---|---|---|---|
| `engine_infer` | 35,917 | 392,529 um2 | 1.20x | 6,133 um2 |
| `engine_wallace` | 39,795 | 406,835 um2 | 1.24x | 6,357 um2 |
| `engine_booth4` | 30,389 | **328,163 um2** | **1.00x** | **5,128 um2** |
| `engine_signmag` | 38,390 | 387,504 um2 | 1.18x | 6,055 um2 |
| `engine_bitserial` | 13,143 | 149,875 um2 | 0.46x | 2,342 um2 |

![SG13G2 cell area](docs/img/ppa_area_sg13g2.png)

**Booth recoding wins on area.** `engine_booth4` is 19 percent smaller than
`engine_wallace` and 16 percent smaller than `engine_infer`, which is roughly what halving
the partial product count should buy. `engine_bitserial` is 2.2 times smaller than
anything else and pays for it with 1.92 times the cycles.

The Wallace result is more interesting than a single ratio suggests, and it is worth
being careful about which cost model is talking. Counting generic cells,
`engine_wallace` is 18 percent larger than the inferred baseline, which reads as a clear
loss for hand-writing a reduction tree. Measured in real SG13G2 area it is only **4
percent larger**. The gap between those two figures is the point: generic cell counting
treats every cell as equally expensive, and a real library maps a carry-save tree onto
cells that are cheaper per cell than the count implies.

So the honest version of the claim is narrower than "do not hand-write a Wallace tree".
ABC's own multiplier mapping is at least as good as an explicit 3:2 tree handed to it,
and on real area the two are within a few percent. What is not close is Booth: halving
the partial products is worth about a fifth of the area against either of them, and that
holds in both cost models.

![PPA area](docs/img/ppa_area.png)

![Logic depth](docs/img/ppa_depth.png)

![Cycles and throughput](docs/img/ppa_cycles.png)

Cycles are identical for the four single-cycle candidates, and exactly as the sequencer
model predicts:

```
per k tile = (max(TILE_M, TILE_K) + 1) + 1 + L = 5 + 1 + L
per o tile = 1 + GRID_K * (per k tile) + TILE_M
total      = GRID_M * GRID_N * (per o tile)
           = 64 * (1 + 8 * 7 + 4)  = 3904   for L = 1
           = 64 * (1 + 8 * 14 + 4) = 7488   for L = 8
```

`test_perf_counters` asserts measured against predicted, and the latency `L` it uses is
itself measured at the engine harness level rather than assumed, so the check is not
circular.

### Where the frequency comes from

Three different frequencies appear in this repository and only one of them is the
design's. This table is here so they cannot be confused.

| Candidate | Netlist worst path | Limited by | Datapath path | Routed Fmax (slow) | Routed Fmax (typical) |
|---|---|---|---|---|---|
| `engine_infer` | 30.38 ns | `acc_clear_i` | 10.06 ns | 81.4 MHz | 120.7 MHz |
| `engine_wallace` | 30.38 ns | `acc_clear_i` | 10.46 ns | still routing | still routing |
| `engine_booth4` | 30.39 ns | `acc_clear_i` | 11.85 ns | 83.0 MHz | 123.6 MHz |
| `engine_signmag` | 30.39 ns | `acc_clear_i` | 12.08 ns | 69.2 MHz | 104.2 MHz |
| `engine_bitserial` | 66.24 ns | `_25522_` | 12.24 ns | 77.3 MHz | 120.6 MHz |

The synthesis column ranks the candidates almost identically and is wrong about all of
them. It puts every single-cycle candidate within 0.01 ns of 30.39 ns and `engine_infer`
ahead of `engine_signmag` on the datapath path by 2 ns; after routing they differ by 12
MHz in the opposite proportion. The synthesis number is measuring a control net that
place and route buffers away.

The first version of this measurement reported 32.9 MHz for four different multiplier
microarchitectures, within 15 picoseconds of each other. Four different multipliers
cannot have the same critical path. Two things were wrong, and both are worth knowing
about because the wrong number looks entirely plausible:

**Unconstrained inputs are invisible.** The SDC constrained the control ports but not
`a_tile_i` or `b_tile_i`. An input with no arrival time has no setup requirement, so
every path through the multiplier array was excluded from the analysis. What was being
timed was the control logic.

**A Yosys netlist has no buffering.** `acc_clear_i` reaches all 512 accumulator
flip-flops through one gate, which then takes 21 ns to drive them. That path is identical
in every candidate because the accumulator bank is shared, which is exactly why the four
numbers agreed. It is still the limiting path in the synthesis column above, and it is
why that column is not a frequency.

Place and route fixes this properly by buffering the net, which is why the routed number
is the design's and the synthesis number is not.

### The sign-magnitude result

Candidate 3 exists to test one claim: that converting operands from two's complement to
sign-magnitude before the multiplier array reduces switching activity, because in two's
complement a value crossing zero flips every high-order bit while in sign-magnitude it
flips one sign bit and leaves the magnitude alone.

The chip can now answer that in watts rather than in transition counts. Both engines are
synthesised to real SG13G2 cells, simulated under the identical operand stream, and their
power annotated from the VCD of that run at full coverage.

![Power against operand sign mix](docs/img/power_vs_signs_real.png)

| Negative operands | wallace | signmag | Total power | Switching power | Transition count (proxy) |
|---|---|---|---|---|---|
| 0% | 3.908 mW | 4.072 mW | **+4.2%** | +6.8% | +7.9% |
| 25% | 4.618 mW | 4.437 mW | **-3.9%** | -6.1% | -8.6% |
| 50% | 4.966 mW | 4.554 mW | **-8.3%** | -13.1% | -17.2% |
| 75% | 4.958 mW | 4.440 mW | **-10.4%** | -16.2% | -22.2% |
| 100% | 4.717 mW | 4.078 mW | **-13.5%** | -21.5% | -28.6% |

**The hypothesis is confirmed in direction and substantially overstated in size, and it
is false at the one operating point most likely to matter.**

Taking those in order.

The mechanism is real. Sign-magnitude saves switching power, the saving grows
monotonically with the fraction of negative operands, and at an all-negative stream it
reaches 21.5 percent of switching power. The underlying effect is visible directly:
`engine_wallace` power rises 27 percent from an all-positive stream to its peak, which is
the two's complement sign extension cost, while `engine_signmag` rises only 12 percent.
That divergence is precisely what the candidate was built to detect.

The size is smaller than an activity-only study would claim. Switching power is not total
power. The converters and the wider magnitude datapath add internal and leakage power
that no transition count sees, so the 21.5 percent saving on switching power becomes 13.5
percent on total power, and the proxy's 28.6 percent becomes 13.5 percent. **A study that
stopped at transition counts would have overstated the benefit by a factor of two.**

And it loses where it probably matters most. At an all-positive operand stream
sign-magnitude **costs 4.2 percent**. The conversion hardware is pure overhead when no
sign ever changes. The crossover is near 13 percent negatives by linear interpolation
between the first two rows, so the encoding is a win for signed weights and activations
that straddle zero, and a loss for a post-ReLU unsigned activation stream. Post-ReLU
unsigned activations are extremely common in exactly the inference workloads an INT8
accelerator is built for.

**And routing charges it again, in frequency.** Everything above is measured on the
synthesis netlist, where the comparison against `engine_wallace` is properly controlled.
After place and route `engine_signmag` closes at 69.2 MHz against 83.0 MHz for
`engine_booth4`, and uses slightly more energy per tile, 429 pJ against 419 pJ. The
conversion logic adds depth that a power sweep at fixed frequency cannot charge it for,
and it routes the most wire of any candidate. A 13 percent power saving bought with a 17
percent frequency loss is not a saving at constant throughput.

The honest summary: sign-magnitude encoding is worth about 13 percent of total power on
signed data at a fixed clock, nothing like the 29 percent a transition count suggests, it
is a net loss on unsigned data, and after routing it is slower and no cheaper in energy
than plain Booth recoding. Whether to use it is a question about your operand statistics
and your timing slack, not a question with one answer. None of that distinction survives
a single-number benchmark, which is why the sweep exists and why the sweep is not the
last word either.

![Activity against operand sign mix](docs/img/activity_vs_signs.png)

The transition-count sweep behind the last column, kept because it gives a per-module
breakdown that a power number does not:

| Negative operands | infer | wallace | booth4 | signmag | bitserial | signmag vs wallace |
|---|---|---|---|---|---|---|
| 0.0% | 8,772 | 9,816 | 10,861 | 10,594 | 18,683 | +7.9% |
| 24.7% | 11,858 | 12,912 | 12,301 | 11,808 | 23,736 | -8.6% |
| 50.1% | 13,251 | 14,630 | 12,698 | 12,118 | 25,561 | -17.2% |
| 73.8% | 13,712 | 15,210 | 12,459 | 11,827 | 25,365 | -22.2% |
| 100.0% | 13,570 | 14,846 | 11,164 | 10,607 | 23,424 | -28.6% |

![Activity totals](docs/img/activity_totals.png)

![Per-module activity](docs/img/activity_modules.png)

### Does the cheap proxy predict real power?

Counting bit transitions in a VCD is nearly free and needs no PDK. Annotated power needs
a liberty file, a gate level netlist and a signoff tool. If the free measurement predicts
the expensive one, most of this analysis is reproducible by anyone. So it is worth
checking directly rather than assuming.

![Proxy against measured power](docs/img/power_proxy_vs_real.png)

**For ranking candidates by energy per tile, the proxy is excellent.** Across all five
candidates the rank correlation between transitions per tile and measured energy per tile
is exactly 1.00, and the linear correlation is 0.99. Every candidate lands in the right
order. If the question is "which of these should I build", the free measurement answers
it.

**For predicting how much better, the proxy is poor, and it errs in both directions.**
Relative to `engine_wallace`:

| Candidate | Proxy says | Measured energy says | Proxy error |
|---|---|---|---|
| `engine_infer` | 0.91x | 0.97x | overstates the saving |
| `engine_booth4` | 0.87x | 0.94x | overstates the saving |
| `engine_signmag` | 0.83x | 0.92x | overstates the saving by about 2x |
| `engine_bitserial` | 1.75x | 3.17x | understates the cost by about 1.8x |

The two failure modes have the same root cause. The proxy weights every net equally and
sees no cell internals, so it misses the internal power that dominates in a
register-heavy design and it cannot see that a wide flat tree and a narrow deep one drive
very different capacitances. For the encodings it therefore flatters the quiet candidate;
for the bit-serial candidate, which spends eight cycles and a great deal of sequential
activity per tile, it badly understates the cost.

**And for average power the proxy is worthless.** Its rank correlation with average power
across the five candidates is 0.00. That is not a defect in the proxy so much as a
warning about the question: `engine_bitserial` spreads the same work over eight cycles,
so it draws the *lowest* average power of any candidate while consuming over three times
the energy per tile. Average power rewards being slow. Energy per tile is the comparison
that means something, and it is the one this repository quotes.

The practical guidance, for anyone who cannot run place and route: use the transition
count to rank designs of similar structure, do not quote its percentages, and never use
it to compare designs with different cycle counts.

### The Pareto view

![Die area against measured energy](docs/img/ppa_pareto_real.png)

Both axes are physical and both are post-route: routed die area against energy per tile
launch measured on the routed netlist. Energy rather than power, for the reason above.

Reading it: `engine_booth4` and `engine_bitserial` are the two candidates on the
frontier. Booth is the best single-cycle design on every axis, and bit-serial buys a
2.2x smaller die for 3x the energy and 1.92x the cycles, which is the right trade only
when area is the binding constraint. `engine_infer` and `engine_signmag` are both
dominated by Booth: bigger, slower and no cheaper.

### Whole chip

| Scope | Cells | Flip-flops |
|---|---|---|
| `engine_array` (all five candidates plus gating and isolation) | 232,071 | 2,841 |
| `bench_core` | 412,685 | 85,577 |
| `gemm_bench_chip` | 412,687 | 85,577 |

`engine_array` is 232,071 cells against 174,298 for the five candidates on their own. The
58,000 cell difference is the clock gating and operand isolation that makes the
measurement valid, charged to shared logic rather than to any candidate. A production
accelerator with one datapath would not pay it.

The flip-flop count jumps at `bench_core` because Yosys maps the four matrix stores
(74 kbit in total) to flip-flops: this build binds no SRAM macros. A macro-backed build
replaces those with four compiled SRAM cuts at a fraction of the area.
`rtl/lib/sram_1rw.sv` is the binding point.

![Area estimate](docs/img/floorplan_estimate.png)

**That figure is an area estimate, not a layout.** Block sizes are proportional to
synthesised cell counts and the positions are arbitrary. The real layouts are the contact
sheets above. The whole-chip flow, with its pad ring and SRAM macros, has never been run:
every routed number here is a candidate, not the chip.

---

## Verification

Everything below genuinely ran, on Icarus Verilog 12.0 through cocotb 2.0.1.

| Suite | Result | Scale |
|---|---|---|
| `test_engine_exact` | 6/6 pass | 2,000 randomised tile launches across five operand distributions = 10,000 candidate evaluations and 160,000 element comparisons, plus 11 INT8 corner cases |
| `test_engine_equiv` | 3/3 pass | 20,000 candidate pair comparisons (10 pairs x 2,000 launches), 110 corner comparisons, 8,192 multiplier operand pairs |
| `test_config` | 2/2 pass | geometry read back out of the chip and compared against the Python model |
| `test_spi_protocol` | 16/16 pass | every opcode, 11 unknown opcodes, 7 truncated frames, a mid-byte frame, offset writes, address range violations, 48 back-to-back frames, commands during a run, soft reset, four SPI clock ratios |
| `test_end_to_end` | 6/6 pass | full 4 KiB result readback checked element by element, on-chip comparator agreement, 6 deliberately corrupted reference elements all localised, all five candidates through the whole chip |
| `test_perf_counters` | 5/5 pass | measured cycles equal the analytic model exactly for every candidate, MAC count exactly 32,768, cycle count data independent |
| `test_tiling` | 5/5 pass | coordinate-dependent operands, 6 single-tile positions, all 8 K tiles proved to contribute exactly once on every candidate, boundary tiles, repeated-run idempotence |
| `test_reset_gating` | 6/6 pass | reset state, reset mid-run, per-candidate clock gating, operand isolation, test mode, candidate switching |
| Verilator lint | 0 warnings | `-Wall` on the chip with no waivers of any kind, and on the engine harness with `UNUSEDPARAM` waived because that verification-only top uses none of the host-interface constants |
| Yosys synthesis | pass | 8 tops, no inferred latches, `check -assert` clean, no blackboxes |
| Gate level equivalence | pass | all five synthesised netlists checked against a reference before their activity is counted |
| Post-route functional | pass | LibreLane's final netlist, the one the GDS was streamed from, simulated against the reference model with the PDK's own cell models |

Total: 49 cocotb tests, all passing.

That last row matters more than its size suggests. A power number taken from a netlist
that computes the wrong answer is worthless, so `tools/verify_routed.py` checks every
output element of every tile against NumPy on the routed netlist *before* its activity is
counted, and reports its annotation coverage so a partly-synthetic number cannot pass as
a measured one.

```bash
make lint          # Verilator -Wall, must be zero warnings
make sim           # the full suite
make sim-quick     # reduced sweep, what CI runs
make verify-routed # simulate the routed netlist and measure its power
```

The reference model is NumPy with an explicit INT64 accumulator wrapped to INT32. It does
not mirror RTL structure, so a shared misunderstanding cannot make a test pass.
Cross-candidate equivalence is checked separately, because a common-mode error in the
model would pass every comparison against it. The full plan is in
[docs/VERIFICATION_PLAN.md](docs/VERIFICATION_PLAN.md).

---

## Control plane

SPI Mode 0, MSB first, chip select active low, `f_spi <= f_core/8`. The pins are
oversampled in the core clock domain rather than used as a clock, so the whole chip is
single-clock and there is no clock domain crossing to verify.

![SPI frame timing](docs/img/spi_frame_timing.svg)

That waveform is a real captured frame: `test_capture_timing_trace` samples the pins on
every core clock edge and writes `results/trace/spi_frame.json`, and the figure is drawn
from that file.

![Memory map](docs/img/memory_map.svg)

Fifteen opcodes cover loading operands and a golden reference, selecting a candidate,
clearing, triggering, verifying, reading the result, reading the performance counters and
the status byte, geometry discovery, and a keyed soft reset. Errors are reported rather
than swallowed: unknown opcodes, out-of-range addresses, truncated frames and commands
issued while busy all set sticky status bits. Full table in
[docs/MEMORY_MAP.md](docs/MEMORY_MAP.md).

`OP_RD_CFG` returns the build's geometry, so host tooling sizes its transfers from the
chip rather than from a compile-time constant. A fork that changes `MAT_*` or `TILE_*`
does not need to change its host software.

---

## Dataflow

![Output-stationary dataflow](docs/img/dataflow_output_stationary.svg)

One trigger runs the whole product. The accumulator for each output tile stays resident
while all `GRID_K` K-tiles stream through it, so no partial sum ever leaves the
accumulator registers.

The operand SRAM words are cut so that one word is exactly the slice of a matrix row a
tile fetch needs, which makes a tile fetch `TILE_M` (or `TILE_K`) single-port reads rather
than one implausibly wide access. Five of the seven cycles per K-tile are operand fetch,
and that cost is real: a design that pretends a single-port SRAM has a wide port measures
a memory system that cannot be built.
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) has the rest.

---

## Adding your own candidate

This repository is a harness, and dropping a sixth microarchitecture into it is meant to
be mechanical. Five files change and nothing else in the design needs to know your
candidate exists.

```bash
cp rtl/engines/engine_template.sv rtl/engines/engine_mine.sv
sed -i 's/engine_template/engine_mine/' rtl/engines/engine_mine.sv
make lint-template          # the skeleton is kept lint clean on purpose
```

`rtl/engines/engine_template.sv` is a complete, lint-clean, working single-cycle candidate
with the arithmetic factored out behind a marked block. It is deliberately absent from
`rtl/filelist.f`, so it never reaches a measurement until you add it. As written it
computes the right answer with an inferred multiply, which means it will pass every
correctness test and tell you nothing: the point is to start from a known-good baseline
rather than from a blank file.

Once it is in the filelist, every measurement in this README applies to it automatically:
the test suite will check it against the reference model and against every other
candidate, the sweeps will include it, and `tools/run_pnr.py` will route it at the same
constraint as everything else. The full contract, including the exact port list and the
five files, is in [docs/ADDING_A_CANDIDATE.md](docs/ADDING_A_CANDIDATE.md).

---

## Running everything

```bash
git clone https://github.com/danieltyukov/matmul-ppa-testchip.git
cd matmul-ppa-testchip
make venv           # .venv with cocotb, numpy, matplotlib
make check-tools    # reports what is present and what is missing
make lint sim       # lint and the full test suite
make synth power    # area and the switching-activity proxy
make images         # regenerate every figure from results/
```

Needed, and verified working: Verilator 5.020 (lint), Icarus Verilog 12.0 (simulation),
Yosys 0.33 (synthesis), Python 3.12.

Verilator would be the faster cocotb backend, but cocotb 2.0 requires Verilator 5.036 and
the toolchain here is 5.020, so Icarus runs the tests and Verilator does lint. Setting
`SIM=verilator` in `tb/Makefile` is the only change needed once a newer Verilator is
available.

### With the IHP PDK

```bash
tools/fetch_pdk.sh        # sparse clone of the views the flow needs
source pdk/env.sh
make synth-pdk            # real cell area in square micrometres
make pdk-ppa              # path delay and watts with real switching activity
make pnr                  # place and route every candidate to a signed-off GDS
make verify-routed        # simulate the routed netlist, measure its power
make layout               # render the GDS and build the contact sheets
```

`tools/fetch_pdk.sh` does not vendor the PDK. It is a large Apache-2.0 third-party
artefact with its own release cadence, and a committed copy would go stale.

`make pnr` is the expensive one: a full LibreLane Classic flow per candidate, including
Magic and KLayout DRC, LVS, antenna, slew and capacitance signoff. Set `PNR_THREADS` to
suit the machine. The Makefile defaults it from `nproc`, because LibreLane leaves
`KLAYOUT_DRC_THREADS` and `KLAYOUT_XOR_THREADS` unset and those two stages dominate wall
time when they run single-threaded.

### What needs the PDK

| Target | Needs the PDK | Status here |
|---|---|---|
| `make lint` | no | run, zero warnings |
| `make sim` | no | run, 49 tests pass |
| `make synth` | no | run, reports committed |
| `make power` | no | run, results committed |
| `make images` | no | run, figures committed |
| `make synth-pdk` | yes | run, results committed under `results/synth/sg13g2/` |
| `make pdk-ppa` | yes | run, results committed under `results/pdk/` |
| `make pnr` | yes, plus LibreLane | run, results committed under `results/pnr/` |
| `make verify-routed` | yes, plus LibreLane | run, results committed |
| `make layout` | yes, plus KLayout | run, renders committed |
| `make flow` (whole chip) | yes, plus OpenROAD | **not run** |

---

## Repository layout

```
rtl/
  pkg/        gemm_pkg.sv, the single source of truth for every dimension
  lib/        synchroniser, reset bridge, clock gate, SRAM technology wrapper
  host/       spi_target, frame_router
  mem/        matrix_store: word and byte views on one single-port SRAM
  engines/    the five candidates, plus the shared CSA tree and accumulator bank
  seq/        gemm_sequencer, engine_array
  measure/    cycle_meter, mac_meter, result_checker
  top/        bench_core, pad_frame, gemm_bench_chip
tb/           cocotb suite, the NumPy reference model, the SPI driver, SV benches
tools/        synthesis collection, VCD activity proxy, figure generators, the
              LibreLane driver, the routed-netlist verifier, the GDS renderer,
              and program_chip.py: the host driver for packaged silicon
flow/         Yosys script, the PDK-gated OpenROAD sequence, LibreLane configs
constraints/  clocks, IO and area SDC
results/      every measurement the README and the figures are built from
docs/         architecture, memory map, PPA methodology, verification plan,
              and the guide for adding your own candidate
```

## Bringing up silicon

`tools/program_chip.py` drives the packaged chip from Linux spidev with the same command
sequences the tests use, sharing its frame construction with the testbench through
`tb/gemm_model.py` so a protocol change cannot make the two disagree silently.

```bash
tools/program_chip.py info                    # identify the chip, report its geometry
tools/program_chip.py bench --engine 1        # one candidate, checked on chip
tools/program_chip.py sweep --clock-hz 50e6   # every candidate, PPA table
tools/program_chip.py read-c --out result.npy
```

It has never been run against silicon, because nothing has been fabricated.

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): the design, and why each decision was taken
- [docs/MEMORY_MAP.md](docs/MEMORY_MAP.md): the SPI command set, register maps and worked sequences
- [docs/PPA_METHODOLOGY.md](docs/PPA_METHODOLOGY.md): how every number is produced and what it does not mean
- [docs/VERIFICATION_PLAN.md](docs/VERIFICATION_PLAN.md): every test and what it establishes
- [docs/ADDING_A_CANDIDATE.md](docs/ADDING_A_CANDIDATE.md): drop in your own microarchitecture
- [CONTRIBUTING.md](CONTRIBUTING.md)

## Honesty notes

Collected in one place, because a benchmark repository that overstates its numbers is
worse than no benchmark at all.

- **Nothing has been fabricated.** The layouts are routed GDS from LibreLane, signed off
  against DRC and LVS. No mask has been made and no silicon exists.
- **The whole-chip flow has never been run.** Every routed number here is a candidate
  block. `gemm_bench_chip`, with its pad ring and SRAM macros, has not been through place
  and route at all.
- **Synthesis area and routed area are different numbers** and are labelled apart
  everywhere. Yosys generic cell counts are not PDK area; PDK cell area is not die area.
- **Gate equivalents are not area, and logic depth is not delay.** Both are counts.
- **The synthesis frequency column is not a frequency.** It is limited by an unbuffered
  512-load net that place and route removes, and it is included only to show that.
- **A transition count is not power.** Where both exist they are reported side by side,
  and they disagree by about a factor of two on the headline comparison. The proxy's
  biases, glitch power chief among them, are listed in the methodology document.
- **The gate level simulation is zero-delay,** at synthesis and after routing, because
  Icarus needs the PDK's specify blocks stripped. No glitch power is counted anywhere in
  this repository, which systematically flatters deep combinational designs.
- **`power__total` in `results/pnr/summary.json` is not measured power.** It is
  OpenROAD's estimate at its default switching activity, roughly five times the annotated
  figure. The measured number is in `results/pnr/routed_power.json`.
- **The sign-magnitude result is a qualified one.** At a fixed clock it is a 13.5 percent
  total-power win on all-negative operands, a 4.2 percent loss on all-positive ones, and
  roughly half what a transition-count study would have claimed. After routing the
  candidate is also 17 percent slower than Booth and no cheaper in energy.
- **Place and route is not bit-reproducible.** The same candidate routed twice from the
  identical configuration gave Fmax figures 15 percent apart. Area was stable to 0.04
  percent. Small frequency differences between candidates are not significant.
- **`engine_wallace` has not finished routing.** Its row is marked as such rather than
  filled with a synthesis estimate, and the controlled post-route comparison against
  `engine_signmag` is therefore not yet available.

## Licence

Apache-2.0. Copyright 2026 Daniel Tyukov. See [LICENSE](LICENSE).
