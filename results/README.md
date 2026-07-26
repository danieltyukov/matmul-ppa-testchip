# results

Every measurement the README quotes and every figure in `docs/img/` comes from a file
in here. Nothing in this directory is hand-edited; each file names the tool that wrote
it, and each carries a note saying what the numbers do and do not mean.

Regenerate the whole set with:

```bash
make sim      # perf/ and trace/
make synth    # synth/generic/
make power    # activity/
make images   # docs/img/, from the above
```

With the IHP PDK and LibreLane installed, the real-process measurements as well:

```bash
make synth-pdk  # synth/sg13g2/, cell area in um2
make pdk-ppa    # pdk/, path delay and watts with real switching activity
make pnr        # pnr/, place and route to a signed-off GDS
make verify-routed # simulate the routed netlist, measure its power
make layout     # docs/img/layout_*.png, rendered from that GDS
```

`make report` prints the tables as markdown, which is how the README numbers were
checked.

## What is measured for what

Not every scope carries every measurement, and the gaps are deliberate rather than
pending. This is the map.

| Scope | Generic synth | SG13G2 synth | PDK timing and power | Place and route | Routed power |
|---|---|---|---|---|---|
| `engine_infer` | yes | yes | yes | yes | yes |
| `engine_wallace` | yes | yes | yes | yes | yes |
| `engine_booth4` | yes | yes | yes | yes | yes |
| `engine_signmag` | yes | yes | yes | yes | yes |
| `engine_bitserial` | yes | yes | yes | yes | yes |
| `engine_array` | yes | no | no | no | no |
| `bench_core` | yes | no | no | no | no |
| `gemm_bench_chip` | yes | no | no | no | no |

All five candidates are routed at the identical constraint and all five carry a power
number measured on their own routed netlist. No number appears in this repository before
its flow has run.

The three integration scopes stop at generic synthesis for one reason: this build binds
no SRAM macros, so `memory_map` turns 74 kbit of matrix store into flip-flops. Their
SG13G2 area would be the area of a design nobody would build, and routing them would
measure that same design more expensively. The candidates contain no memory, so they are
unaffected and they are what every physical number here describes.

`engine_array` is the one scope where that argument does not apply, since it holds no
memory either. It has a LibreLane configuration in `flow/librelane/engine_array/` and it
has not been routed: at 232,071 cells it is an order of magnitude more flow time than a
single candidate, and it answers a different question from the one this chip is for.

## synth/

Yosys reports. Produced by `tools/synth_collect.py`, which drives
`flow/yosys/synth.tcl`.

| File | Contents |
|---|---|
| `summary.csv` | one row per module per mode: cells, flip-flops, gate equivalents, logic depth, cell area |
| `generic/summary.json` | the same for the generic gate mode, with a per-cell histogram |
| `generic/<top>_generic_stat.txt` | the raw Yosys `stat` output, kept as evidence |
| `generic/<top>_generic_ltp.txt` | the `ltp` headline; the full path listing is 64 MB for the chip and stays in `build/` |
| `sg13g2/summary.json` | real cell area in square micrometres from the IHP SG13G2 liberty |
| `sg13g2/<top>_sg13g2_stat.txt` | the raw report, including the per-cell histogram of PDK cells |

Generic mode covers all five candidates plus `engine_array`, `bench_core` and
`gemm_bench_chip`. SG13G2 mode covers the candidates only: the memory bearing tops
would map 74 kbit of storage to flip-flops, which measures a design nobody would build.

## perf/

| File | Contents |
|---|---|
| `cycle_counts.json` | cycles and MAC count per candidate for one full 32x32x32 run, read out of the chip's own counters over SPI |

Written by `tb/test_perf_counters.py`, which also asserts every number against a
closed-form model of the sequencer.

## activity/

Switching-activity proxy. Produced by `tools/activity_sweep.py`, which uses
`tools/vcd_activity.py` to count bit transitions in VCD dumps.

| File | Contents |
|---|---|
| `gate_summary.json` | the headline measurement: per-candidate transitions on synthesised gate netlists, swept over the fraction of negative operands |
| `gate_summary.csv` | the same, flat |
| `summary.json` | the RTL sweep, finer in the sweep variable and with a per-module breakdown |
| `summary.csv` | the same, flat |
| `engines_neg<n>.json` | full per-scope report for one RTL sweep point, including the 25 busiest nets |

Gate level is the comparable measurement, because the candidates are described at
different levels of abstraction and only post-synthesis netlists compare like with
like. The reasoning is in
[docs/PPA_METHODOLOGY.md](../docs/PPA_METHODOLOGY.md#why-gate-level-not-rtl-for-the-headline-number).

**These are transition counts, not power.** Read
[what the proxy does not tell you](../docs/PPA_METHODOLOGY.md#what-the-proxy-does-not-tell-you)
before quoting them.

## pdk/

Real IHP SG13G2 measurements on the synthesised netlist, from `tools/pdk_ppa.py`.

| File | Contents |
|---|---|
| `summary.json` | per candidate: cell area in um2, the worst path in the netlist and what limits it, the operand-to-accumulator path, power in watts with the switching activity annotated from a gate level VCD, and energy per tile |
| `summary.csv` | the same, flat |
| `sign_sweep.json` | power in watts against the fraction of negative operands, for `engine_wallace` and `engine_signmag`: the sign-magnitude hypothesis in a physical unit |
| `sign_sweep.csv` | the same, flat |

The power numbers are annotated at 100 percent coverage, which
`report_activity_annotation` reports and this file records rather than assuming. They
are quoted at the 20 ns clock the candidates are placed and routed at, and dynamic power
scales with frequency.

`critical_path_ns` in this file is **not** the design's maximum frequency. It is the
worst path in an unbuffered Yosys netlist, which is the control fanout rather than the
arithmetic, and `limiting_path` names it. The routed frequency is in `pnr/`. The
reasoning is in
[docs/PPA_METHODOLOGY.md](../docs/PPA_METHODOLOGY.md#timing-and-a-trap-worth-documenting).

## pnr/

Post-route measurements from LibreLane's own `final/metrics.json`, harvested by
`tools/run_pnr.py`.

| File | Contents |
|---|---|
| `summary.json` | per candidate: die area, standard cell area with and without the fill, instance counts, routed wirelength and vias, setup and hold slack at all three PDK corners, maximum frequency per corner, DRC and LVS counts |
| `summary.csv` | the same, flat |
| `routed_power.json` | per candidate: post-route power split into sequential, combinational and clock, energy per tile, annotation coverage, and whether the routed netlist still computes the right answer. Written by `tools/verify_routed.py` |
| `routed_power.csv` | the same, flat |

Every candidate is routed at the identical 20 ns constraint and 40 percent target
utilisation, so the area columns compare like with like. The GDS itself is not
committed: it is tens of megabytes per candidate and reproducible with `make pnr`. The
renders in `docs/img/` are what is committed.

**Two power numbers live here and they are not the same number.** `power__total` in
`summary.json` is what OpenROAD reports during the flow at its default switching
activity, with nothing annotated from a workload, and it runs about five times the
measured figure. The measured one is in `routed_power.json`: LibreLane's final netlist
simulated against the reference model, with power annotated from that run's VCD and the
parasitics extracted from the routing. Quote the second, and check its `functional` and
`coverage` fields before you do.

## trace/

| File | Contents |
|---|---|
| `spi_frame.json` | one SPI frame sampled on every core clock edge, from `tb/test_spi_protocol.py::test_capture_timing_trace` |

`docs/img/spi_frame_timing.svg` is drawn from this, so the timing diagram cannot
describe timing the RTL does not have.

## What is not here

- **No GDS.** Each routed candidate is tens of megabytes and `make pnr` reproduces it.
  What is committed is the metrics it produced and the renders.
- **No chip-level layout.** `gemm_bench_chip` has a pad ring and SRAM macros, and that
  flow has never completed. Every routed number here is a candidate.
- **No silicon measurements.** Nothing has been fabricated.
