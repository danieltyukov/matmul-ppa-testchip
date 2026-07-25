# Copyright 2026 Daniel Tyukov
# SPDX-License-Identifier: Apache-2.0
"""Performance counter tests.

The cycle count is checked against a closed-form expression derived from the
sequencer's state machine, not against a previously recorded number. The only
input to that expression that is not a design parameter is the engine's
launch-to-valid latency, and test_engine_exact.test_mac_tick_and_latency measures
that at the engine level, so nothing here rests on an unverified assumption.

The MAC count expectation is stronger still: MAT_M * MAT_N * MAT_K regardless of
candidate or cycle count, which is what makes MACs per cycle a fair throughput
number across candidates with different latencies.

These are also the numbers that feed the committed performance results, so
tools/collect_perf.py runs the same sequences.
"""

from __future__ import annotations

import json
import os
import pathlib

import cocotb
import numpy as np

import gemm_model as gm
from chip_env import bring_up


@cocotb.test()
async def test_counters_zero_after_reset(dut):
    """Both counters must read zero before anything runs."""
    spi = await bring_up(dut)
    perf = await spi.read_perf()
    assert perf["cycles"] == 0, f"cycle counter is {perf['cycles']} after reset"
    assert perf["macs"] == 0, f"MAC counter is {perf['macs']} after reset"
    assert perf["mismatch_count"] == 0
    assert perf["first_mismatch"] == 0


@cocotb.test()
async def test_cycle_and_mac_counts_match_analysis(dut):
    """Measured cycles and MACs must equal the analytic expectation, exactly."""
    spi = await bring_up(dut)
    rng = np.random.default_rng(int(os.environ.get("GEMM_SEED", 20260725)) + 71)

    a = gm.random_int8(rng, (gm.MAT_M, gm.MAT_K))
    b = gm.random_int8(rng, (gm.MAT_K, gm.MAT_N))
    await spi.load_a(a)
    await spi.load_b(b)

    measured = {}
    for engine in range(gm.ENGINE_COUNT):
        await spi.run_gemm(engine)
        perf = await spi.read_perf()

        want_cycles = gm.expected_run_cycles(engine)
        want_macs = gm.expected_mac_count()

        assert perf["cycles"] == want_cycles, (
            f"candidate {engine} ({gm.ENGINE_NAMES[engine]}) took "
            f"{perf['cycles']} cycles, the sequencer model predicts {want_cycles} "
            f"(latency {gm.ENGINE_LATENCY[engine]}, "
            f"fetch {max(gm.TILE_M, gm.TILE_K)}, grid "
            f"{gm.GRID_M}x{gm.GRID_N}x{gm.GRID_K})"
        )
        assert perf["macs"] == want_macs, (
            f"candidate {engine} ({gm.ENGINE_NAMES[engine]}) retired "
            f"{perf['macs']} MACs, expected {want_macs}"
        )
        measured[engine] = perf
        dut._log.info(
            f"candidate {engine} ({gm.ENGINE_NAMES[engine]}): "
            f"{perf['cycles']} cycles, {perf['macs']} MACs, "
            f"{perf['macs'] / perf['cycles']:.3f} MACs/cycle"
        )

    # The bit-serial candidate must be measurably slower, otherwise the whole
    # multi-cycle path is not being exercised.
    assert measured[gm.ENG_BITSERIAL]["cycles"] > measured[gm.ENG_INFER]["cycles"], (
        "the bit-serial candidate did not take more cycles than the single cycle one"
    )

    _write_perf_results(measured)


@cocotb.test()
async def test_counters_are_deterministic(dut):
    """The same workload must cost the same number of cycles every time."""
    spi = await bring_up(dut)
    rng = np.random.default_rng(73)

    a = gm.random_int8(rng, (gm.MAT_M, gm.MAT_K))
    b = gm.random_int8(rng, (gm.MAT_K, gm.MAT_N))
    await spi.load_a(a)
    await spi.load_b(b)
    await spi.select_engine(gm.ENG_WALLACE)

    counts = []
    for _ in range(3):
        await spi.run_gemm()
        counts.append((await spi.read_perf())["cycles"])
    assert len(set(counts)) == 1, f"repeated identical runs cost {counts} cycles"

    # Different operand data must not change the cycle count either: the sequencer
    # is data independent, which is what makes the cycle count a clean performance
    # measure rather than a workload artefact.
    a2 = np.full((gm.MAT_M, gm.MAT_K), -128, dtype=np.int8)
    b2 = np.zeros((gm.MAT_K, gm.MAT_N), dtype=np.int8)
    await spi.load_a(a2)
    await spi.load_b(b2)
    await spi.run_gemm()
    data_independent = (await spi.read_perf())["cycles"]
    assert data_independent == counts[0], (
        f"the cycle count changed with operand data: {counts[0]} vs {data_independent}"
    )
    dut._log.info(f"cycle count is data independent at {counts[0]} cycles")


@cocotb.test()
async def test_counter_clear_triggers(dut):
    """Both the explicit clear and a new run must zero the counters."""
    spi = await bring_up(dut)
    rng = np.random.default_rng(79)

    await spi.load_a(gm.random_int8(rng, (gm.MAT_M, gm.MAT_K)))
    await spi.load_b(gm.random_int8(rng, (gm.MAT_K, gm.MAT_N)))
    await spi.run_gemm(gm.ENG_INFER)

    perf = await spi.read_perf()
    assert perf["cycles"] > 0 and perf["macs"] > 0

    await spi.trigger(gm.TRIG_CLR_PERF)
    perf = await spi.read_perf()
    assert perf["cycles"] == 0 and perf["macs"] == 0, (
        f"the explicit counter clear left {perf}"
    )

    # A run trigger clears the counters itself, so a readback after two runs
    # reflects the second run only rather than their sum.
    await spi.run_gemm()
    first = (await spi.read_perf())["cycles"]
    await spi.run_gemm()
    second = (await spi.read_perf())["cycles"]
    assert second == first, (
        f"the counters accumulated across runs: {first} then {second}"
    )


@cocotb.test()
async def test_verify_does_not_disturb_counters(dut):
    """Running the comparator must not change the GEMM cycle or MAC counts.

    The cycle counter is gated on the sequencer being busy, not on the chip being
    busy, so a verify pass in between must leave the measurement of the run intact.
    """
    spi = await bring_up(dut)
    rng = np.random.default_rng(83)

    a = gm.random_int8(rng, (gm.MAT_M, gm.MAT_K))
    b = gm.random_int8(rng, (gm.MAT_K, gm.MAT_N))
    await spi.load_a(a)
    await spi.load_b(b)
    await spi.load_reference(gm.matmul_ref(a, b))

    await spi.run_gemm(gm.ENG_BOOTH4)
    before = await spi.read_perf()
    await spi.verify()
    after = await spi.read_perf()

    assert after["cycles"] == before["cycles"], (
        f"a verify pass changed the cycle count from {before['cycles']} to "
        f"{after['cycles']}"
    )
    assert after["macs"] == before["macs"], (
        f"a verify pass changed the MAC count from {before['macs']} to {after['macs']}"
    )


def _write_perf_results(measured: dict):
    """Commit the measured counts so the charts and the README come from real data."""
    out_dir = pathlib.Path(
        os.environ.get("GEMM_RESULTS_DIR")
        or pathlib.Path(__file__).resolve().parent.parent / "results" / "perf"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": "tb/test_perf_counters.py::test_cycle_and_mac_counts_match_analysis",
        "note": (
            "Cycle and MAC counts read out of the chip's performance counters over "
            "SPI after a full MAT_M x MAT_N x MAT_K run, one entry per candidate. "
            "Simulated with Icarus Verilog on the behavioural SRAM model."
        ),
        "geometry": {
            "mat_m": gm.MAT_M, "mat_n": gm.MAT_N, "mat_k": gm.MAT_K,
            "tile_m": gm.TILE_M, "tile_n": gm.TILE_N, "tile_k": gm.TILE_K,
        },
        "candidates": {
            gm.ENGINE_NAMES[e]: {
                "index": e,
                "cycles": measured[e]["cycles"],
                "macs": measured[e]["macs"],
                "macs_per_cycle": measured[e]["macs"] / measured[e]["cycles"],
                "launch_to_valid_latency": gm.ENGINE_LATENCY[e],
            }
            for e in sorted(measured)
        },
    }
    (out_dir / "cycle_counts.json").write_text(json.dumps(payload, indent=2) + "\n")
