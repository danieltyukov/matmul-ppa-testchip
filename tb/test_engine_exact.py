# Copyright 2026 Daniel Tyukov
# SPDX-License-Identifier: Apache-2.0
"""Per-candidate bit-exactness against a NumPy INT32 reference.

Runs on tb_engine_harness, which puts every candidate on the same clock with the
same operands, so one stimulus stream checks all of them. Every assertion compares
against NumPy, never against another candidate: cross-candidate agreement is
checked separately in test_engine_equiv.py, and the two would both have to be
wrong in the same way for a bug to slip through.
"""

from __future__ import annotations

import os

import cocotb
import numpy as np

import gemm_model as gm
from engine_harness import EngineHarness, check_against

# Number of random tile launches in the main sweep. Each launch exercises every
# candidate at once, so the candidate evaluation count is this times ENGINE_COUNT.
RANDOM_LAUNCHES = int(os.environ.get("GEMM_CASES") or 2000)

@cocotb.test()
async def test_corner_operands(dut):
    """Hand-picked INT8 corners: -128, 127, zero, identity, rank deficient."""
    harness = EngineHarness(dut)
    await harness.start()

    checked = 0
    for name, a, b in gm.corner_tiles():
        await harness.clear()
        expected = gm.matmul_ref(a, b)
        await harness.launch(a, b)
        check_against(harness, expected, f"corner {name}")
        checked += 1

    dut._log.info(f"corner operand cases checked on all candidates: {checked}")
    assert checked == len(gm.corner_tiles())


@cocotb.test()
async def test_random_bit_exact(dut):
    """Randomised INT8 tiles through every candidate, bit-exact against NumPy."""
    harness = EngineHarness(dut)
    await harness.start()
    rng = np.random.default_rng(int(os.environ.get("GEMM_SEED", 20260725)))

    # A mix of operand distributions: uniform, all negative, all positive and
    # sign-heavy, because the sign-magnitude candidate has different corner cases
    # from the two's complement ones.
    distributions = [
        ("uniform", lambda: (gm.random_int8(rng, (gm.TILE_M, gm.TILE_K)),
                             gm.random_int8(rng, (gm.TILE_K, gm.TILE_N)))),
        ("all_negative", lambda: (gm.random_int8_biased(rng, (gm.TILE_M, gm.TILE_K), 1.0),
                                  gm.random_int8_biased(rng, (gm.TILE_K, gm.TILE_N), 1.0))),
        ("all_positive", lambda: (gm.random_int8_biased(rng, (gm.TILE_M, gm.TILE_K), 0.0),
                                  gm.random_int8_biased(rng, (gm.TILE_K, gm.TILE_N), 0.0))),
        ("mixed_sign", lambda: (gm.random_int8_biased(rng, (gm.TILE_M, gm.TILE_K), 0.5),
                                gm.random_int8_biased(rng, (gm.TILE_K, gm.TILE_N), 0.5))),
        ("sparse", lambda: (gm.random_int8(rng, (gm.TILE_M, gm.TILE_K))
                            * (rng.random((gm.TILE_M, gm.TILE_K)) < 0.25),
                            gm.random_int8(rng, (gm.TILE_K, gm.TILE_N)))),
    ]

    launches = 0
    per_dist = max(1, RANDOM_LAUNCHES // len(distributions))
    for name, gen in distributions:
        await harness.clear()
        for _ in range(per_dist):
            a, b = gen()
            a = a.astype(np.int8)
            b = b.astype(np.int8)
            expected = gm.matmul_ref(a, b)
            await harness.clear()
            await harness.launch(a, b)
            check_against(harness, expected, f"random {name}")
            launches += 1

    dut._log.info(
        f"randomised tile launches: {launches} "
        f"({launches * gm.ENGINE_COUNT} candidate evaluations, "
        f"{launches * gm.ENGINE_COUNT * gm.TILE_M * gm.TILE_N} element comparisons)"
    )
    assert launches == per_dist * len(distributions)


@cocotb.test()
async def test_output_stationary_accumulation(dut):
    """Several launches without a clear must accumulate, which is the whole point."""
    harness = EngineHarness(dut)
    await harness.start()
    rng = np.random.default_rng(int(os.environ.get("GEMM_SEED", 20260725)) + 1)

    sequences = 0
    for depth in (2, 3, gm.GRID_K, 2 * gm.GRID_K):
        await harness.clear()
        expected = np.zeros((gm.TILE_M, gm.TILE_N), dtype=np.int64)
        for _ in range(depth):
            a = gm.random_int8(rng, (gm.TILE_M, gm.TILE_K))
            b = gm.random_int8(rng, (gm.TILE_K, gm.TILE_N))
            expected = gm.wrap_int32(expected + gm.matmul_ref(a, b))
            await harness.launch(a, b)
            check_against(harness, expected, f"accumulate depth {depth}")
        sequences += 1

    dut._log.info(f"accumulation sequences checked: {sequences}")
    assert sequences == 4


@cocotb.test()
async def test_clear_is_absolute(dut):
    """acc_clear must zero the bank regardless of what was in it."""
    harness = EngineHarness(dut)
    await harness.start()
    rng = np.random.default_rng(7)

    for _ in range(8):
        a = gm.random_int8(rng, (gm.TILE_M, gm.TILE_K))
        b = gm.random_int8(rng, (gm.TILE_K, gm.TILE_N))
        await harness.launch(a, b)
        await harness.clear()
        zero = np.zeros((gm.TILE_M, gm.TILE_N), dtype=np.int64)
        check_against(harness, zero, "after clear")


@cocotb.test()
async def test_accumulator_headroom(dut):
    """The worst case accumulation must not overflow INT32.

    GRID_K launches of the most extreme tile product is the largest magnitude the
    chip can ever produce for one output element. If that fits, nothing else can
    overflow, and the reference model's wrap never fires.
    """
    harness = EngineHarness(dut)
    await harness.start()

    worst = gm.TILE_K * 128 * 128 * gm.GRID_K
    assert worst < (1 << 31), (
        f"worst case accumulation {worst} does not fit in INT32; "
        f"ACC_W must grow for TILE_K={gm.TILE_K}, GRID_K={gm.GRID_K}"
    )

    a = np.full((gm.TILE_M, gm.TILE_K), -128, dtype=np.int8)
    b = np.full((gm.TILE_K, gm.TILE_N), -128, dtype=np.int8)
    await harness.clear()
    expected = np.zeros((gm.TILE_M, gm.TILE_N), dtype=np.int64)
    for _ in range(gm.GRID_K):
        expected = expected + gm.matmul_ref(a, b)
        await harness.launch(a, b)
        check_against(harness, expected, "worst case accumulation")

    assert int(expected[0, 0]) == worst, f"expected {worst}, model says {expected[0, 0]}"
    dut._log.info(f"worst case accumulated value {worst} held exactly in INT32")


@cocotb.test()
async def test_mac_tick_and_latency(dut):
    """Each candidate reports TILE_M*TILE_N*TILE_K MACs, at its documented latency.

    The latency check is what makes the analytic cycle count in
    test_perf_counters.py legitimate: gemm_model.ENGINE_LATENCY is measured here
    rather than assumed there.
    """
    harness = EngineHarness(dut)
    await harness.start()
    rng = np.random.default_rng(11)
    expected_tick = gm.TILE_M * gm.TILE_N * gm.TILE_K

    await harness.clear()
    for _ in range(16):
        a = gm.random_int8(rng, (gm.TILE_M, gm.TILE_K))
        b = gm.random_int8(rng, (gm.TILE_K, gm.TILE_N))
        info = await harness.launch(a, b)
        for engine in range(gm.ENGINE_COUNT):
            assert engine in info["ticks"], (
                f"candidate {engine} ({gm.ENGINE_NAMES[engine]}) never asserted valid"
            )
            assert info["ticks"][engine] == expected_tick, (
                f"candidate {engine} ({gm.ENGINE_NAMES[engine]}) reported "
                f"{info['ticks'][engine]} MACs, expected {expected_tick}"
            )
            assert info["latency"][engine] == gm.ENGINE_LATENCY[engine], (
                f"candidate {engine} ({gm.ENGINE_NAMES[engine]}) took "
                f"{info['latency'][engine]} cycles from launch to valid, "
                f"gemm_model.ENGINE_LATENCY says {gm.ENGINE_LATENCY[engine]}"
            )

    dut._log.info(
        "measured launch-to-valid latency per candidate: "
        + ", ".join(f"{gm.ENGINE_NAMES[e]}={gm.ENGINE_LATENCY[e]}"
                    for e in range(gm.ENGINE_COUNT))
    )
