# Copyright 2026 Daniel Tyukov
# SPDX-License-Identifier: Apache-2.0
"""Full matrix end-to-end flow through the SPI control plane.

Load operands, trigger, wait for done, read the result back, and check the whole
MAT_M x MAT_N output matrix against NumPy. Then check that the on-chip comparator
agrees with that verdict, and that it correctly reports a mismatch when the
reference matrix is deliberately corrupted.

Reading 4 KB back over SPI is the slow part, so the tests that sweep across
candidates use the on-chip comparator and only the primary test does the full
readback. That is also how silicon bring-up would work.
"""

from __future__ import annotations

import os

import cocotb
import numpy as np

import gemm_model as gm
from chip_env import bring_up


@cocotb.test()
async def test_full_matrix_readback(dut):
    """The complete flow with a full 4 KB result readback, checked against NumPy."""
    spi = await bring_up(dut)
    rng = np.random.default_rng(int(os.environ.get("GEMM_SEED", 20260725)) + 41)

    a = gm.random_int8(rng, (gm.MAT_M, gm.MAT_K))
    b = gm.random_int8(rng, (gm.MAT_K, gm.MAT_N))
    expected = gm.matmul_ref(a, b)

    await spi.load_a(a)
    await spi.load_b(b)
    await spi.select_engine(gm.ENG_WALLACE)
    status = await spi.run_gemm()

    assert status & gm.ST_DONE, f"the run did not report done, status 0x{status:02X}"
    assert not (status & (gm.ST_CMD_ERR | gm.ST_FRAME_ERR)), (
        f"a clean run raised an error flag, status 0x{status:02X}"
    )

    result = await spi.read_result()
    if not np.array_equal(result, expected):
        bad = np.argwhere(result != expected)
        r, c = bad[0]
        raise AssertionError(
            f"result element ({r},{c}) is {result[r, c]}, expected {expected[r, c]}; "
            f"{len(bad)} of {expected.size} elements differ"
        )
    dut._log.info(
        f"full {gm.MAT_M}x{gm.MAT_N}x{gm.MAT_K} product verified element by element "
        f"over SPI ({expected.size} INT32 elements)"
    )


@cocotb.test()
async def test_on_chip_comparator_agrees(dut):
    """The on-chip comparator must pass when the reference is correct."""
    spi = await bring_up(dut)
    rng = np.random.default_rng(int(os.environ.get("GEMM_SEED", 20260725)) + 42)

    a = gm.random_int8(rng, (gm.MAT_M, gm.MAT_K))
    b = gm.random_int8(rng, (gm.MAT_K, gm.MAT_N))
    expected = gm.matmul_ref(a, b)

    await spi.load_a(a)
    await spi.load_b(b)
    await spi.load_reference(expected)
    await spi.run_gemm(gm.ENG_INFER)
    status = await spi.verify()

    assert status & gm.ST_VFY_DONE, f"verify did not finish, status 0x{status:02X}"
    assert not (status & gm.ST_MISMATCH), (
        f"the comparator reported a mismatch against a correct reference, "
        f"status 0x{status:02X}"
    )
    perf = await spi.read_perf()
    assert perf["mismatch_count"] == 0, (
        f"the comparator counted {perf['mismatch_count']} mismatches against a "
        f"correct reference"
    )


@cocotb.test()
async def test_on_chip_comparator_detects_corruption(dut):
    """A deliberately corrupted reference must be caught, counted and localised."""
    spi = await bring_up(dut)
    rng = np.random.default_rng(int(os.environ.get("GEMM_SEED", 20260725)) + 43)

    a = gm.random_int8(rng, (gm.MAT_M, gm.MAT_K))
    b = gm.random_int8(rng, (gm.MAT_K, gm.MAT_N))
    expected = gm.matmul_ref(a, b)

    await spi.load_a(a)
    await spi.load_b(b)
    await spi.run_gemm(gm.ENG_BOOTH4)

    # Corrupt a known set of elements, including the very first and the very last,
    # so both the count and the first-mismatch index are pinned down.
    corrupt_indices = sorted({0, 1, 17, 400, 511, gm.MAT_M * gm.MAT_N - 1})
    bad_ref = expected.copy().reshape(-1)
    for idx in corrupt_indices:
        bad_ref[idx] = bad_ref[idx] + 1
    bad_ref = bad_ref.reshape(gm.MAT_M, gm.MAT_N)

    await spi.load_reference(bad_ref)
    status = await spi.verify()

    assert status & gm.ST_MISMATCH, (
        f"the comparator missed {len(corrupt_indices)} corrupted elements, "
        f"status 0x{status:02X}"
    )
    perf = await spi.read_perf()
    assert perf["mismatch_count"] == len(corrupt_indices), (
        f"the comparator counted {perf['mismatch_count']} mismatches, "
        f"expected {len(corrupt_indices)}"
    )
    assert perf["first_mismatch"] == corrupt_indices[0], (
        f"the first mismatch index is {perf['first_mismatch']}, "
        f"expected {corrupt_indices[0]}"
    )
    dut._log.info(
        f"comparator found all {len(corrupt_indices)} corrupted elements and "
        f"reported the first at index {perf['first_mismatch']}"
    )

    # Restoring the reference and re-verifying must clear the verdict, which proves
    # the mismatch flag is not stuck.
    await spi.load_reference(expected)
    status = await spi.verify()
    assert not (status & gm.ST_MISMATCH), (
        "the mismatch flag did not clear when the reference was restored"
    )


@cocotb.test()
async def test_every_candidate_end_to_end(dut):
    """Every candidate must produce the same correct full matrix result.

    Checked with the on-chip comparator, which is both faster than a 4 KB readback
    and a test of the comparator itself against five independent datapaths.
    """
    spi = await bring_up(dut)
    rng = np.random.default_rng(int(os.environ.get("GEMM_SEED", 20260725)) + 44)

    a = gm.random_int8(rng, (gm.MAT_M, gm.MAT_K))
    b = gm.random_int8(rng, (gm.MAT_K, gm.MAT_N))
    expected = gm.matmul_ref(a, b)

    await spi.load_a(a)
    await spi.load_b(b)
    await spi.load_reference(expected)

    for engine in range(gm.ENGINE_COUNT):
        # Zero the result store first, so a candidate that computes nothing at all
        # cannot pass on the previous candidate's leftovers.
        await spi.trigger(gm.TRIG_CLR_C)
        await spi.wait_idle()
        await spi.trigger(gm.TRIG_VERIFY)
        status = await spi.wait_idle()
        assert status & gm.ST_MISMATCH, (
            "clearing the result store did not make the comparator fail, so the "
            "candidate sweep below would not prove anything"
        )
        await spi.trigger(gm.TRIG_CLR_STICKY)

        await spi.run_gemm(engine)
        status = await spi.verify()
        assert not (status & gm.ST_MISMATCH), (
            f"candidate {engine} ({gm.ENGINE_NAMES[engine]}) produced a wrong full "
            f"matrix result, status 0x{status:02X}"
        )
        perf = await spi.read_perf()
        assert perf["macs"] == gm.expected_mac_count(), (
            f"candidate {engine} ({gm.ENGINE_NAMES[engine]}) retired "
            f"{perf['macs']} MACs, expected {gm.expected_mac_count()}"
        )
        dut._log.info(
            f"candidate {engine} ({gm.ENGINE_NAMES[engine]}): full matrix correct, "
            f"{perf['cycles']} cycles, {perf['macs']} MACs"
        )


@cocotb.test()
async def test_extreme_operand_matrices(dut):
    """Full matrix runs on the operand extremes, checked on chip."""
    spi = await bring_up(dut)

    cases = [
        ("all_zero",
         np.zeros((gm.MAT_M, gm.MAT_K), dtype=np.int8),
         np.zeros((gm.MAT_K, gm.MAT_N), dtype=np.int8)),
        ("all_min",
         np.full((gm.MAT_M, gm.MAT_K), -128, dtype=np.int8),
         np.full((gm.MAT_K, gm.MAT_N), -128, dtype=np.int8)),
        ("all_max",
         np.full((gm.MAT_M, gm.MAT_K), 127, dtype=np.int8),
         np.full((gm.MAT_K, gm.MAT_N), 127, dtype=np.int8)),
        ("min_times_max",
         np.full((gm.MAT_M, gm.MAT_K), -128, dtype=np.int8),
         np.full((gm.MAT_K, gm.MAT_N), 127, dtype=np.int8)),
        ("identity",
         np.eye(gm.MAT_M, gm.MAT_K, dtype=np.int8),
         np.full((gm.MAT_K, gm.MAT_N), -128, dtype=np.int8)),
    ]

    for name, a, b in cases:
        expected = gm.matmul_ref(a, b)
        await spi.load_a(a)
        await spi.load_b(b)
        await spi.load_reference(expected)
        await spi.run_gemm(gm.ENG_SIGNMAG)
        status = await spi.verify()
        assert not (status & gm.ST_MISMATCH), (
            f"extreme case {name} failed on chip, status 0x{status:02X}"
        )

    # The all-min case is the largest magnitude the chip can produce, so confirm
    # the model agrees it is inside INT32 rather than assuming it.
    worst = gm.MAT_K * 128 * 128
    assert worst < (1 << 31), "the all-min case overflows INT32"
    dut._log.info(
        f"extreme operand matrices verified on chip: {len(cases)}; "
        f"largest output magnitude {worst}"
    )


@cocotb.test()
async def test_result_store_clear(dut):
    """The clear trigger must zero the whole result store."""
    spi = await bring_up(dut)
    rng = np.random.default_rng(51)

    a = gm.random_int8(rng, (gm.MAT_M, gm.MAT_K))
    b = gm.random_int8(rng, (gm.MAT_K, gm.MAT_N))
    await spi.load_a(a)
    await spi.load_b(b)
    await spi.run_gemm(gm.ENG_INFER)

    await spi.trigger(gm.TRIG_CLR_C)
    await spi.wait_idle()

    result = await spi.read_result()
    assert np.count_nonzero(result) == 0, (
        f"{np.count_nonzero(result)} result elements survived the clear trigger"
    )
