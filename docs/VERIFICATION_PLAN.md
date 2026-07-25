# Verification plan

Two entry points, one suite.

- **`tb_engine_harness`** puts all five candidates on the same clock with the same
  operands, so one stimulus stream exercises every candidate and the outputs can be
  compared both against NumPy and against each other in the same pass.
- **`gemm_bench_chip`** is the whole chip, driven only through its eleven pins. No
  chip-level test reaches into the hierarchy except the clock gating tests, which
  cannot observe what they need to from outside.

Backend: Icarus Verilog 12.0 through cocotb 2.0.1. Verilator would be faster, but
cocotb 2.0 requires Verilator 5.036 and the pinned toolchain here is 5.020, so
Verilator is used for linting instead. Setting `SIM=verilator` in `tb/Makefile` is
all that changes when a newer Verilator is available.

```bash
make sim            # everything
make sim-quick      # reduced sweep, what CI runs
make -C tb MODULE=test_spi_protocol TOPLEVEL=gemm_bench_chip   # one file
```

`SEED` is pinned by default so a failure reproduces. `CASES` scales the randomised
counts.

---

## The reference is independent

`tb/gemm_model.py` computes the expected answer with NumPy and an explicit INT64
accumulator, then wraps to INT32. It does not mirror RTL structure: no tiles, no
partial products, no carry-save. A shared misunderstanding between the RTL and the
model cannot make a test pass, because the model does not know how the RTL works.

Cross-candidate equivalence is checked separately, in `test_engine_equiv.py`, for the
opposite reason: a common-mode error in the model would pass every comparison against
it. Both checks together are what make the pair meaningful.

---

## 1. Per-candidate bit-exactness

`tb/test_engine_exact.py`, on `tb_engine_harness`.

| Test | What it establishes |
|---|---|
| `test_corner_operands` | 11 hand-picked INT8 corner pairs: all `-128`, all `127`, zeros, `-128 x -1`, `127 x -1`, identity, and a rank-deficient pair where every row of A and every column of B is identical |
| `test_random_bit_exact` | randomised tiles across five operand distributions: uniform, all negative, all positive, an even sign mix, and sparse |
| `test_output_stationary_accumulation` | several launches without a clear must accumulate, at depths 2, 3, `GRID_K` and `2*GRID_K` |
| `test_clear_is_absolute` | `acc_clear` zeroes the bank whatever was in it |
| `test_accumulator_headroom` | the worst case accumulation, `GRID_K` launches of `-128 x -128`, held exactly in INT32; also asserts the arithmetic bound so a geometry change that would overflow fails here |
| `test_mac_tick_and_latency` | every candidate reports exactly `TILE_M*TILE_N*TILE_K` MACs per launch, at the latency `gemm_model.ENGINE_LATENCY` claims |

That last one is load-bearing. The cycle-count check in test 6 uses those latencies,
so measuring them rather than assuming them is what keeps the analytic check from
being circular.

Every assertion names the candidate, the element and both values, because "some
element differed" is not a useful failure on a 4x4 tile across five candidates.

---

## 2. Candidate equivalence

`tb/test_engine_equiv.py`, on `tb_engine_harness`.

| Test | What it establishes |
|---|---|
| `test_all_candidates_agree_random` | all `ENGINE_COUNT * (ENGINE_COUNT-1) / 2` = 10 candidate pairs agree bit-exactly on randomised operands |
| `test_all_candidates_agree_corners` | the same on the INT8 corner cases, where hand-written arithmetic is most likely to diverge |
| `test_all_candidates_agree_exhaustive_scalar` | strided sweep over the INT8 multiplier operand space with the rest of the tile zeroed. Not exhaustive over the engine, but exhaustive over the multiplier, which is where Booth recoding, Wallace reduction and sign-magnitude conversion actually differ. `GEMM_EXHAUSTIVE_STRIDE=1` runs all 65536 pairs |

---

## 3. SPI protocol

`tb/test_spi_protocol.py`, on `gemm_bench_chip`, through the pins only.

| Test | What it establishes |
|---|---|
| `test_identity_and_geometry` | `OP_RD_ID` and `OP_RD_CFG` report this build |
| `test_status_after_reset` | status is `0x00` |
| `test_engine_selection_readback` | every valid index selects and reads back |
| `test_engine_selection_out_of_range` | an index the build does not have is refused, flagged, and does not change the active engine |
| `test_unknown_opcodes` | 11 unimplemented opcodes each set command error and start nothing |
| `test_nop_is_silent` | `OP_NOP` sets no flag |
| `test_truncated_frames` | 7 frames that end before their required bytes each set frame error; 2 that are short but complete do not |
| `test_partial_byte_frame` | a frame cut off mid-byte sets frame error and the chip recovers |
| `test_memory_write_read_round_trip` | 6144 bytes through A, B and REF read back exactly |
| `test_partial_and_offset_writes` | a one-row write, a one-byte write and an offset read all land where they should |
| `test_address_out_of_range` | writes and reads past the end of a store are flagged; a full-length read is not |
| `test_back_to_back_frames` | 48 frames with no idle between them all interpreted |
| `test_command_while_busy` | an operand write during a run is refused and flagged, a second run trigger is refused, status and counters stay readable, and the run's result is still correct afterwards |
| `test_soft_reset` | the wrong key does nothing; the right key clears the datapath and the counters, leaves the operand stores intact, and the chip computes correctly again with no hard reset |
| `test_spi_clock_ratio_sweep` | the protocol works at `f_core/8` (the documented maximum and the tightest case for the readback prefetch) and at three slower ratios |
| `test_capture_timing_trace` | records a real frame cycle by cycle into `results/trace/spi_frame.json`, which is what `docs/img/spi_frame_timing.svg` is drawn from |

---

## 4. End-to-end flow

`tb/test_end_to_end.py`, on `gemm_bench_chip`.

| Test | What it establishes |
|---|---|
| `test_full_matrix_readback` | load, trigger, wait, read 4 KiB back, and check all 1024 INT32 elements against NumPy |
| `test_on_chip_comparator_agrees` | the comparator passes against a correct reference and reports zero mismatches |
| `test_on_chip_comparator_detects_corruption` | 6 deliberately corrupted reference elements, including the first and the last, are all found, the count is exact, the first-mismatch index is exact, and restoring the reference clears the verdict |
| `test_every_candidate_end_to_end` | every candidate produces the same correct full-matrix result and retires exactly 32768 MACs. Store C is zeroed before each candidate, and the test asserts that the zeroing really does make the comparator fail, so a candidate cannot pass on leftovers |
| `test_extreme_operand_matrices` | 5 full-matrix extremes: all zero, all `-128`, all `127`, `-128 x 127`, identity |
| `test_result_store_clear` | the clear trigger zeroes all 1024 elements |

The candidate sweep uses the on-chip comparator rather than a 4 KiB readback. That
is both much faster and a test of the comparator against five independent datapaths.

---

## 5. Tiling correctness

`tb/test_tiling.py`, on `gemm_bench_chip`. A plain random matrix hides a
transposition or an off-by-one tile because the errors average out, so these tests
use operands designed so a wrong tile index gives a provably different answer.

| Test | What it establishes |
|---|---|
| `test_tile_index_is_not_transposed` | operands whose value depends on both coordinates, plus an assertion that the stimulus is not symmetric enough for a transposed A to pass |
| `test_single_nonzero_tile_positions` | 6 grid positions, one nonzero A tile at a time; the output must be nonzero only in the rows that tile can reach |
| `test_k_tile_accumulation_is_complete` | A and B built so that k tile `kt` contributes `2^kt` to one output element, making the result a bit mask of which K tiles were accumulated. A skipped tile clears a bit, a doubled one overflows into the next. Checked on every candidate |
| `test_boundary_tiles` | the corner elements checked explicitly, plus a model-independent check that the first and last output rows are exact negatives of each other |
| `test_repeated_runs_are_idempotent` | four consecutive runs across different candidates all agree, so no state leaks between runs |

---

## 6. Performance counters

`tb/test_perf_counters.py`, on `gemm_bench_chip`.

| Test | What it establishes |
|---|---|
| `test_counters_zero_after_reset` | both counters read zero |
| `test_cycle_and_mac_counts_match_analysis` | measured cycles equal the closed-form sequencer model exactly, for every candidate, and the MAC count equals `MAT_M*MAT_N*MAT_K`. Also asserts the bit-serial candidate is measurably slower, so the multi-cycle path is really exercised. Writes `results/perf/cycle_counts.json` |
| `test_counters_are_deterministic` | three identical runs cost identical cycles, and different operand data costs the same cycles: the sequencer is data independent, which is what makes the cycle count a performance measure rather than a benchmark artefact |
| `test_counter_clear_triggers` | the explicit clear zeroes both, and a run trigger clears them itself so counts do not accumulate across runs |
| `test_verify_does_not_disturb_counters` | a verify pass between run and readback leaves the run's measurement intact |

---

## 7. Reset and clock gating

`tb/test_reset_gating.py`, on `gemm_bench_chip`. These reach into the hierarchy,
because a gated clock is not observable from the pins. That is the point of the test.

| Test | What it establishes |
|---|---|
| `test_state_after_hard_reset` | status, both counters, the engine selection and all four status pads are in their defined reset state |
| `test_reset_during_a_run` | reset asserted mid-run aborts cleanly, and the chip computes correctly afterwards |
| `test_only_selected_candidate_is_clocked` | for each selection in turn, 400 core cycles of a run are sampled and the selected candidate's gated clock must have risen while every other candidate's must not have risen once |
| `test_unselected_candidates_see_constant_operands` | 300 cycles sampled per selection; an unselected candidate's operand inputs must be zero on every one of them. Clock gating alone does not achieve this, because a combinational array has no clock to gate |
| `test_test_mode_ungates_everything` | the test mode pin runs every candidate's clock, for scan and characterisation |
| `test_gated_candidates_hold_their_accumulators` | switching candidates back and forth still gives correct results, so the sequencer's clear is doing its job rather than the answer depending on leftover state |

The same claim is measured a second way, from a VCD, by `tools/vcd_activity.py`.
Two independent measurements of the property the whole power comparison rests on is
deliberate.

---

## 8. Switching activity

`tools/activity_sweep.py`, driven by `make power`. Not a cocotb test, because it
sweeps a workload parameter and post-processes dumps, but it carries assertions:

- The gate level bench (`tb/tb_activity_gate.sv`) checks every result against a
  reference computed in the bench. A netlist that does not match its RTL cannot
  contribute activity numbers, and this doubles as post-synthesis functional
  verification of all five candidates.
- Every dump is parsed twice and the results must be identical, which asserts that
  `tools/vcd_activity.py` is deterministic.
- The realised operand statistics are measured and reported per sweep point, so the
  x-axis of the plots is the measured fraction of negative operands rather than the
  requested one.

---

## 9. Lint

`make lint` runs Verilator `--lint-only -Wall` and requires **zero warnings**. Not
zero errors, zero warnings.

On `gemm_bench_chip` there are no waivers of any kind. Everything Verilator flagged
during development was fixed rather than suppressed, and several of those fixes found
real problems: an unused upper address range that became address range checking, a
truncated constant, and an unnecessary majority gate on the top bit of every
carry-save adder row.

On `tb_engine_harness`, `UNUSEDPARAM` is waived. That top is a verification harness
that instantiates the five candidates and nothing else, so every host-interface
constant in `gemm_pkg` really is unused in that elaboration. It is the only waiver in
the repository, it applies to one warning class on one verification-only top, and the
reason is recorded next to it in the Makefile.

## 10. Synthesis

`make synth` runs Yosys per candidate and for `engine_array`, `bench_core` and
`gemm_bench_chip`. Two structural assertions are inside the script itself, so a
regression fails the build rather than being noticed later in a report:

- `select -assert-none {t:$_DLATCH_*} {t:$_DLATCHSR_*} {t:$dlatch} {t:$dlatchsr} {t:$sr}`
  no inferred latches anywhere.
- `check -assert` no multiple drivers, no undriven inputs, no combinational loops.

The one intentional latch in the design is inside `clock_gate.sv`, which is an
integrated clock gate and is supposed to be a latch. It maps to a real ICG cell in a
PDK build, and in the generic build it is the only place a latch is expected, which
is why the assertion runs after `synth` has already resolved it.

There are no blackboxes. The behavioural SRAM is inferred as memory and then mapped,
never left as a blackbox, so a blackbox in a report would mean something failed to
elaborate.

Reports are committed under `results/synth/`, for both the generic gate mode and the
IHP SG13G2 standard cell mode.

## 11. Silicon, when there is any

`tools/program_chip.py` runs the same sequences over Linux spidev. It shares its frame
construction with the testbench through `tb/gemm_model.py`, so a protocol change cannot
make the host driver and the tests disagree without a test failing. Nothing has been
fabricated, so it has never been run against a chip.
