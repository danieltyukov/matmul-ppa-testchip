# Copyright 2026 Daniel Tyukov
# SPDX-License-Identifier: Apache-2.0
"""Chip level test environment.

Brings up the clock, releases reset and hands back an SpiController. Everything a
chip level test does goes through the SPI pins, so the same sequences would work
against packaged silicon.
"""

from __future__ import annotations

import cocotb
from cocotb.clock import Clock

import gemm_model as gm
from spi_driver import SpiController, reset_chip

CLK_PERIOD_NS = 10


async def bring_up(dut, cycles_per_half: int | None = None) -> SpiController:
    cocotb.start_soon(Clock(dut.pad_clk_i, CLK_PERIOD_NS, unit="ns").start())
    spi = SpiController(dut) if cycles_per_half is None else SpiController(dut, cycles_per_half)
    await reset_chip(dut, spi)
    return spi


async def assert_identity(dut, spi: SpiController):
    """Check the chip answers with the identification word this build expects."""
    got = await spi.read_id()
    assert got == gm.CHIP_ID, f"identification word is 0x{got:08X}, expected 0x{gm.CHIP_ID:08X}"


async def load_operands(spi: SpiController, a, b):
    await spi.load_a(a)
    await spi.load_b(b)
