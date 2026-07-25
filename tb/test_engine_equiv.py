# Copyright 2026 Daniel Tyukov
# SPDX-License-Identifier: Apache-2.0
"""Cross-candidate equivalence.

test_engine_exact.py checks every candidate against NumPy. This file checks the
candidates against each other, which catches a different class of bug: a shared
misunderstanding between the RTL and the Python reference would pass there and
fail here only if the candidates disagree, and a common-mode error in the
reference would pass both. Running both is what makes the pair meaningful, and
this file is also the one that fails loudly if someone adds a candidate that is
subtly not equivalent.
"""

from __future__ import annotations

import itertools
import os

import cocotb
import numpy as np

import gemm_model as gm
from engine_harness import EngineHarness

EQUIV_LAUNCHES = int(os.environ.get("GEMM_CASES") or 2000)


def _pairwise_check(harness: EngineHarness, label: str) -> int:
    """Assert every pair of candidates holds the same accumulator contents."""
    banks = {e: harness.read_acc(e) for e in range(gm.ENGINE_COUNT)}
    comparisons = 0
    for lhs, rhs in itertools.combinations(range(gm.ENGINE_COUNT), 2):
        comparisons += 1
        if not np.array_equal(banks[lhs], banks[rhs]):
            bad = np.argwhere(banks[lhs] != banks[rhs])
            r, c = bad[0]
            raise AssertionError(
                f"{label}: candidate {lhs} ({gm.ENGINE_NAMES[lhs]}) and "
                f"candidate {rhs} ({gm.ENGINE_NAMES[rhs]}) disagree at "
                f"element ({r},{c}): {banks[lhs][r, c]} vs {banks[rhs][r, c]}"
            )
    return comparisons


@cocotb.test()
async def test_all_candidates_agree_random(dut):
    """Randomised operands: every candidate must produce identical accumulators."""
    harness = EngineHarness(dut)
    await harness.start()
    rng = np.random.default_rng(int(os.environ.get("GEMM_SEED", 20260725)) + 99)

    launches = 0
    comparisons = 0
    for _ in range(EQUIV_LAUNCHES):
        a = gm.random_int8(rng, (gm.TILE_M, gm.TILE_K))
        b = gm.random_int8(rng, (gm.TILE_K, gm.TILE_N))
        await harness.clear()
        await harness.launch(a, b)
        comparisons += _pairwise_check(harness, "random equivalence")
        launches += 1

    pairs = gm.ENGINE_COUNT * (gm.ENGINE_COUNT - 1) // 2
    dut._log.info(
        f"equivalence launches: {launches}, candidate pair comparisons: {comparisons} "
        f"({pairs} pairs per launch)"
    )
    assert comparisons == launches * pairs


@cocotb.test()
async def test_all_candidates_agree_corners(dut):
    """The INT8 corner cases, where hand-written arithmetic is most likely to differ."""
    harness = EngineHarness(dut)
    await harness.start()

    comparisons = 0
    for name, a, b in gm.corner_tiles():
        await harness.clear()
        await harness.launch(a, b)
        comparisons += _pairwise_check(harness, f"corner {name}")
    dut._log.info(f"corner equivalence comparisons: {comparisons}")
    assert comparisons > 0


@cocotb.test()
async def test_all_candidates_agree_exhaustive_scalar(dut):
    """Exhaustive over one full operand pair, with the rest of the tile zeroed.

    With TILE_K MACs per dot product this is not an exhaustive test of the whole
    engine, but it is exhaustive over the multiplier: all 65536 INT8 by INT8
    products pass through element (0,0) of every candidate. That is the part of
    the datapath where Booth recoding, Wallace reduction and sign-magnitude
    conversion actually differ from each other.
    """
    harness = EngineHarness(dut)
    await harness.start()

    # 65536 launches at roughly a thousand simulated nanoseconds each is too slow
    # for routine runs, so stride the space unless asked for the full sweep.
    stride = int(os.environ.get("GEMM_EXHAUSTIVE_STRIDE") or 8)
    values = list(range(-128, 128))
    products = 0

    for av in values[::stride]:
        for bv in values:
            a = np.zeros((gm.TILE_M, gm.TILE_K), dtype=np.int8)
            b = np.zeros((gm.TILE_K, gm.TILE_N), dtype=np.int8)
            a[0, 0] = av
            b[0, 0] = bv
            await harness.clear()
            await harness.launch(a, b)
            expected = gm.matmul_ref(a, b)
            for engine in range(gm.ENGINE_COUNT):
                got = harness.read_acc(engine)
                assert got[0, 0] == expected[0, 0], (
                    f"candidate {engine} ({gm.ENGINE_NAMES[engine]}) computed "
                    f"{av} * {bv} = {got[0, 0]}, expected {expected[0, 0]}"
                )
            products += 1

    dot_products = len(values) * len(values[::stride])
    dut._log.info(
        f"multiplier operand pairs checked on all candidates: {products} "
        f"of {dot_products} in the strided space (stride {stride}, "
        f"set GEMM_EXHAUSTIVE_STRIDE=1 for all 65536)"
    )
    assert products == dot_products
