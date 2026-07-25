# Copyright 2026 Daniel Tyukov
# SPDX-License-Identifier: Apache-2.0
"""Tiling correctness.

The full matrix product is only right if three things are right at once: the tile
address arithmetic in the sequencer, the output-stationary accumulation over the K
tiles, and the write-back mapping from accumulator bank to result store. A plain
random matrix test can hide a transposition or an off-by-one tile because the
errors average out visually, so these tests use operands designed so that a wrong
tile index produces a provably different answer.
"""

from __future__ import annotations

import os

import cocotb
import numpy as np

import gemm_model as gm
from chip_env import bring_up


async def _run_and_compare(dut, spi, a, b, label):
    expected = gm.matmul_ref(a, b)
    await spi.load_a(a)
    await spi.load_b(b)
    await spi.load_reference(expected)
    await spi.run_gemm()
    status = await spi.verify()
    if status & gm.ST_MISMATCH:
        perf = await spi.read_perf()
        idx = perf["first_mismatch"]
        raise AssertionError(
            f"{label}: on-chip comparator found {perf['mismatch_count']} wrong "
            f"elements, first at row {idx // gm.MAT_N} column {idx % gm.MAT_N}"
        )
    return expected


@cocotb.test()
async def test_tile_index_is_not_transposed(dut):
    """Operands that make every tile position unique catch a swapped tile index.

    A[m][k] = m * 4 + k modulo the INT8 range makes the value of an element depend
    on both of its coordinates, so fetching tile (kt, mt) instead of (mt, kt)
    cannot accidentally give the right answer.
    """
    spi = await bring_up(dut)

    m_idx = np.arange(gm.MAT_M).reshape(-1, 1)
    k_idx = np.arange(gm.MAT_K).reshape(1, -1)
    n_idx = np.arange(gm.MAT_N).reshape(1, -1)

    a = (((m_idx * 4 + k_idx) % 255) - 127).astype(np.int8)
    b = (((k_idx.reshape(-1, 1) * 5 + n_idx) % 255) - 127).astype(np.int8)

    await spi.select_engine(gm.ENG_WALLACE)
    await _run_and_compare(dut, spi, a, b, "coordinate dependent operands")

    # A transposed A would give a different product, so confirm that the test
    # actually discriminates rather than passing on a symmetric matrix.
    if gm.MAT_M == gm.MAT_K:
        assert not np.array_equal(gm.matmul_ref(a, b), gm.matmul_ref(a.T.copy(), b)), (
            "the stimulus is symmetric enough that a transposed A would also pass"
        )
    dut._log.info("tile index arithmetic verified with coordinate dependent operands")


@cocotb.test()
async def test_single_nonzero_tile_positions(dut):
    """One nonzero A tile at a time: the output must appear only where it should.

    This isolates the tile addressing completely. With A zero except for tile
    (mt, kt), the product is nonzero only in rows mt*TILE_M .. +TILE_M-1, and only
    the k tile kt of B can contribute. Any address error moves the nonzero block.
    """
    spi = await bring_up(dut)
    rng = np.random.default_rng(int(os.environ.get("GEMM_SEED", 20260725)) + 61)

    b = gm.random_int8(rng, (gm.MAT_K, gm.MAT_N))
    await spi.load_b(b)
    await spi.select_engine(gm.ENG_BOOTH4)

    # Sweep the corners and the diagonal of the tile grid rather than all
    # GRID_M*GRID_K positions, which would take far longer than it is worth.
    positions = {(0, 0), (0, gm.GRID_K - 1), (gm.GRID_M - 1, 0),
                 (gm.GRID_M - 1, gm.GRID_K - 1), (gm.GRID_M // 2, gm.GRID_K // 2),
                 (1, gm.GRID_K - 2)}

    for mt, kt in sorted(positions):
        a = np.zeros((gm.MAT_M, gm.MAT_K), dtype=np.int8)
        block = gm.random_int8(rng, (gm.TILE_M, gm.TILE_K))
        a[mt * gm.TILE_M:(mt + 1) * gm.TILE_M, kt * gm.TILE_K:(kt + 1) * gm.TILE_K] = block

        expected = await _run_and_compare(dut, spi, a, b, f"single tile ({mt},{kt})")

        nonzero_rows = set(np.flatnonzero(np.any(expected != 0, axis=1)).tolist())
        allowed_rows = set(range(mt * gm.TILE_M, (mt + 1) * gm.TILE_M))
        assert nonzero_rows <= allowed_rows, (
            f"tile ({mt},{kt}) produced output outside rows {sorted(allowed_rows)}: "
            f"{sorted(nonzero_rows - allowed_rows)}"
        )

    dut._log.info(f"single nonzero tile positions verified: {len(positions)}")


@cocotb.test()
async def test_k_tile_accumulation_is_complete(dut):
    """Every K tile must contribute, and exactly once.

    Building A so that k tile kt contributes a distinct power of two to a known
    output element makes the result a signature of which K tiles were accumulated:
    because the weights are +-2**kt, every subset of K tiles produces a different
    sum, so a skipped tile, a doubled tile or a swapped pair of tiles all give a
    wrong and diagnosable value rather than one that might coincidentally match.

    The top weight is negative because +2**(GRID_K-1) does not fit in INT8 for
    GRID_K = 8. Negating it preserves the uniqueness argument, since the binary
    representation of a signed sum of distinct +-2**kt terms is still unique.
    """
    spi = await bring_up(dut)

    assert gm.GRID_K <= 8, (
        f"GRID_K={gm.GRID_K} needs a weight of {1 << (gm.GRID_K - 1)}, which does not "
        f"fit in INT8 with either sign; this test needs GRID_K <= 8"
    )

    # A row 0 carries 1 in the first column of every k tile, zero elsewhere.
    # B column 0 carries +-2**kt in the first row of k tile kt.
    a = np.zeros((gm.MAT_M, gm.MAT_K), dtype=np.int8)
    b = np.zeros((gm.MAT_K, gm.MAT_N), dtype=np.int8)
    weights = []
    for kt in range(gm.GRID_K):
        a[0, kt * gm.TILE_K] = 1
        magnitude = 1 << kt
        weight = magnitude if magnitude <= 127 else -magnitude
        weights.append(weight)
        b[kt * gm.TILE_K, 0] = weight

    expected_element = sum(weights)
    expected = gm.matmul_ref(a, b)
    assert int(expected[0, 0]) == expected_element, (
        f"the model says element (0,0) is {expected[0, 0]}, the weight sum says "
        f"{expected_element}"
    )

    for engine in range(gm.ENGINE_COUNT):
        await spi.load_a(a)
        await spi.load_b(b)
        await spi.load_reference(expected)
        await spi.run_gemm(engine)
        status = await spi.verify()
        assert not (status & gm.ST_MISMATCH), (
            f"candidate {engine} ({gm.ENGINE_NAMES[engine]}) did not accumulate all "
            f"{gm.GRID_K} K tiles; expected element (0,0) = {expected_element} "
            f"from K tile weights {weights}"
        )

    dut._log.info(
        f"all {gm.GRID_K} K tiles verified to contribute exactly once, on all "
        f"{gm.ENGINE_COUNT} candidates"
    )


@cocotb.test()
async def test_boundary_tiles(dut):
    """The first and last tile of the grid must be handled like any other.

    Off-by-one errors in a tile loop show up at the edges, so this puts distinctive
    values in the corner tiles of both operands and checks the corner elements of
    the result explicitly rather than trusting the aggregate comparison.
    """
    spi = await bring_up(dut)
    rng = np.random.default_rng(int(os.environ.get("GEMM_SEED", 20260725)) + 62)

    a = gm.random_int8(rng, (gm.MAT_M, gm.MAT_K))
    b = gm.random_int8(rng, (gm.MAT_K, gm.MAT_N))
    # Make the extreme rows and columns unmistakable.
    a[0, :] = 1
    a[-1, :] = -1
    b[:, 0] = 1
    b[:, -1] = -1

    await spi.select_engine(gm.ENG_BITSERIAL)
    expected = await _run_and_compare(dut, spi, a, b, "boundary tiles")

    result = await spi.read_result()
    corners = [(0, 0), (0, gm.MAT_N - 1), (gm.MAT_M - 1, 0), (gm.MAT_M - 1, gm.MAT_N - 1)]
    for r, c in corners:
        assert result[r, c] == expected[r, c], (
            f"corner element ({r},{c}) is {result[r, c]}, expected {expected[r, c]}"
        )
    # First and last row of A were set to +1 and -1, so their row sums must be
    # exact negatives of each other. This is independent of the reference model.
    assert np.array_equal(result[0, :], -result[gm.MAT_M - 1, :]), (
        "the first and last output rows are not negatives of each other, so the "
        "row indexing at the grid boundary is wrong"
    )
    dut._log.info("boundary tile handling verified at all four result corners")


@cocotb.test()
async def test_repeated_runs_are_idempotent(dut):
    """Running the same workload twice must give the same answer.

    Catches state that leaks from one run into the next: a stale accumulator, an
    uncleared tile register, or a loop counter that does not return to zero.
    """
    spi = await bring_up(dut)
    rng = np.random.default_rng(int(os.environ.get("GEMM_SEED", 20260725)) + 63)

    a = gm.random_int8(rng, (gm.MAT_M, gm.MAT_K))
    b = gm.random_int8(rng, (gm.MAT_K, gm.MAT_N))
    expected = gm.matmul_ref(a, b)

    await spi.load_a(a)
    await spi.load_b(b)
    await spi.load_reference(expected)

    for run in range(4):
        engine = run % gm.ENGINE_COUNT
        await spi.run_gemm(engine)
        status = await spi.verify()
        assert not (status & gm.ST_MISMATCH), (
            f"run {run} with candidate {engine} ({gm.ENGINE_NAMES[engine]}) "
            f"produced a different answer, so state leaked between runs"
        )
    dut._log.info("four consecutive runs across different candidates all agreed")
