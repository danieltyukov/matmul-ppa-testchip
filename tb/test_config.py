# Copyright 2026 Daniel Tyukov
# SPDX-License-Identifier: Apache-2.0
"""Configuration consistency between the RTL, the model and the tools.

Three places encode the chip's geometry: rtl/pkg/gemm_pkg.sv, tb/gemm_model.py, and
the host tooling that sizes transfers. If they disagree, every other test in the
suite is testing the wrong thing while appearing to pass. This file closes that gap
by reading the geometry back out of the chip over SPI and comparing it against the
model, and by checking the arithmetic bounds the model assumes.
"""

from __future__ import annotations

import cocotb

import gemm_model as gm
from chip_env import bring_up


@cocotb.test()
async def test_model_matches_chip(dut):
    """OP_RD_CFG must report exactly what gemm_model.py believes."""
    spi = await bring_up(dut)
    cfg = await spi.read_cfg()

    expected = {
        "mat_m": gm.MAT_M, "mat_n": gm.MAT_N, "mat_k": gm.MAT_K,
        "tile_m": gm.TILE_M, "tile_n": gm.TILE_N, "tile_k": gm.TILE_K,
        "operand_w": gm.OPERAND_W, "acc_w": gm.ACC_W,
        "engine_count": gm.ENGINE_COUNT, "engine_sel": 0,
    }
    mismatches = {k: (v, cfg[k]) for k, v in expected.items() if cfg[k] != v}
    assert not mismatches, (
        f"gemm_model.py and rtl/pkg/gemm_pkg.sv disagree "
        f"(model, chip): {mismatches}"
    )


@cocotb.test()
async def test_model_invariants(dut):
    """The assumptions the model and the tools make about the geometry."""
    spi = await bring_up(dut)
    await spi.read_status()   # keep the chip in the loop so this is not a unit test

    assert gm.MAT_M % gm.TILE_M == 0, "MAT_M must tile evenly"
    assert gm.MAT_N % gm.TILE_N == 0, "MAT_N must tile evenly"
    assert gm.MAT_K % gm.TILE_K == 0, "MAT_K must tile evenly"

    for name, value in (("TILE_K", gm.TILE_K), ("TILE_N", gm.TILE_N),
                        ("GRID_N", gm.GRID_N)):
        assert value & (value - 1) == 0, (
            f"{name}={value} must be a power of two: the host byte address maps to a "
            f"word and a lane by slicing, which needs that"
        )

    assert len(gm.ENGINE_NAMES) == gm.ENGINE_COUNT, (
        f"ENGINE_NAMES has {len(gm.ENGINE_NAMES)} entries for "
        f"{gm.ENGINE_COUNT} candidates"
    )
    assert len(gm.ENGINE_LATENCY) == gm.ENGINE_COUNT, (
        f"ENGINE_LATENCY has {len(gm.ENGINE_LATENCY)} entries for "
        f"{gm.ENGINE_COUNT} candidates"
    )
    assert gm.ENGINE_LATENCY[gm.ENG_SLOWEST] == max(gm.ENGINE_LATENCY.values()), (
        "ENG_SLOWEST does not point at the candidate with the longest latency, so "
        "the tests that drive every candidate at once would sample too early"
    )

    worst = gm.MAT_K * (1 << (gm.OPERAND_W - 1)) ** 2
    assert worst < (1 << (gm.ACC_W - 1)), (
        f"the largest possible output element, {worst}, does not fit in "
        f"INT{gm.ACC_W}; ACC_W must grow"
    )

    assert gm.A_BYTES == gm.MAT_M * gm.MAT_K
    assert gm.C_BYTES == gm.MAT_M * gm.MAT_N * (gm.ACC_W // 8)

    dut._log.info(
        f"geometry consistent: {gm.MAT_M}x{gm.MAT_N}x{gm.MAT_K}, tile "
        f"{gm.TILE_M}x{gm.TILE_N}x{gm.TILE_K}, grid "
        f"{gm.GRID_M}x{gm.GRID_N}x{gm.GRID_K}, {gm.ENGINE_COUNT} candidates, "
        f"worst case element {worst} inside INT{gm.ACC_W}"
    )
