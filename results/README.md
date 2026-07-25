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

`make report` prints the tables as markdown, which is how the README numbers were
checked.

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

## trace/

| File | Contents |
|---|---|
| `spi_frame.json` | one SPI frame sampled on every core clock edge, from `tb/test_spi_protocol.py::test_capture_timing_trace` |

`docs/img/spi_frame_timing.svg` is drawn from this, so the timing diagram cannot
describe timing the RTL does not have.

## What is not here

- **No layout, no GDS, no routed netlist.** OpenROAD and the IHP SG13G2 physical views
  are not installed in the environment this repository was developed in.
- **No power in watts.** That needs the PDK, a placed and routed netlist and an
  activity file. `flow/openroad/finish.tcl` is where it would come from.
- **No silicon measurements.** Nothing has been fabricated.
