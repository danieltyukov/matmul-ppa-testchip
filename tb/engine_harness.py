# Copyright 2026 Daniel Tyukov
# SPDX-License-Identifier: Apache-2.0
"""Shared driver for tb_engine_harness.

Every candidate sits on the same clock with the same operands, so one stimulus
stream exercises all of them and the outputs can be compared against each other
and against NumPy in the same pass.
"""

from __future__ import annotations

import cocotb
import numpy as np
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge, Timer

import gemm_model as gm


CLK_PERIOD_NS = 10

# cocotb resumes from RisingEdge before non-blocking assignments have settled, so
# every step nudges the simulation a little past the edge before sampling. Staying
# in a writable region (rather than using ReadOnly) keeps drive and sample in the
# same helper.
SETTLE_NS = 1

# Number of random tile launches per candidate in the main sweep. Each launch
# exercises all ENGINE_COUNT candidates at once.
RANDOM_LAUNCHES = int(os.environ.get("GEMM_CASES") or 2000)


class EngineHarness:
    """Thin wrapper around tb_engine_harness."""

    def __init__(self, dut):
        self.dut = dut
        self.launches = 0

    async def step(self, n: int = 1):
        for _ in range(n):
            await RisingEdge(self.dut.clk_i)
        await Timer(SETTLE_NS, unit="ns")

    async def start(self):
        cocotb.start_soon(Clock(self.dut.clk_i, CLK_PERIOD_NS, unit="ns").start())
        self.dut.rst_ni.value = 0
        self.dut.acc_clear_i.value = 0
        self.dut.launch_i.value = 0
        self.dut.a_tile_i.value = 0
        self.dut.b_tile_i.value = 0
        await ClockCycles(self.dut.clk_i, 5)
        self.dut.rst_ni.value = 1
        await self.step(3)

    async def clear(self):
        await self.wait_ready()
        self.dut.acc_clear_i.value = 1
        await self.step()
        self.dut.acc_clear_i.value = 0
        await self.step()

    async def wait_ready(self, timeout: int = 64):
        for _ in range(timeout):
            if int(self.dut.ready_o.value) == (1 << gm.ENGINE_COUNT) - 1:
                return
            await self.step()
        raise TimeoutError("not every candidate became ready")

    async def launch(self, a: np.ndarray, b: np.ndarray) -> dict:
        """Present one operand tile pair and wait until the slowest candidate retires it.

        Returns the MAC tick each candidate reported on its own valid cycle, and
        the launch-to-valid latency it took, so callers can check both without a
        second stimulus pass.
        """
        await self.wait_ready()
        self.dut.a_tile_i.value = gm.pack_tile(a)
        self.dut.b_tile_i.value = gm.pack_tile(b)
        self.dut.launch_i.value = 1
        await self.step()
        self.dut.launch_i.value = 0

        ticks: dict[int, int] = {}
        latency: dict[int, int] = {}
        # Once the slowest candidate is valid, every accumulator holds its final
        # value for this launch.
        for cycle in range(1, 4 * gm.OPERAND_W + 9):
            valid = int(self.dut.valid_o.value)
            for engine in range(gm.ENGINE_COUNT):
                if (valid >> engine) & 1 and engine not in ticks:
                    ticks[engine] = self.read_mac_tick(engine)
                    latency[engine] = cycle
            if gm.ENG_SLOWEST in ticks:
                break
            await self.step()
        else:
            raise TimeoutError("the slowest candidate never asserted valid")
        self.launches += 1
        return {"ticks": ticks, "latency": latency}

    def read_acc(self, engine: int) -> np.ndarray:
        """One candidate's accumulator bank, as a TILE_M x TILE_N INT32 matrix."""
        c_tile_w = gm.TILE_M * gm.TILE_N * gm.ACC_W
        allbits = int(self.dut.c_tile_o.value)
        word = (allbits >> (engine * c_tile_w)) & ((1 << c_tile_w) - 1)
        return gm.unpack_tile(word, gm.TILE_M, gm.TILE_N, gm.ACC_W)

    def read_mac_tick(self, engine: int) -> int:
        tick_w = (gm.TILE_M * gm.TILE_N * gm.TILE_K).bit_length()
        allbits = int(self.dut.mac_tick_o.value)
        return (allbits >> (engine * tick_w)) & ((1 << tick_w) - 1)


def check_against(harness: "EngineHarness", expected: np.ndarray, label: str,
                  engines=None):
    """Assert the named candidates hold `expected`, with a precise failure message."""
    for engine in (range(gm.ENGINE_COUNT) if engines is None else engines):
        got = harness.read_acc(engine)
        if not np.array_equal(got, expected):
            bad = np.argwhere(got != expected)
            r, c = bad[0]
            raise AssertionError(
                f"{label}: candidate {engine} ({gm.ENGINE_NAMES[engine]}) "
                f"element ({r},{c}) is {got[r, c]}, expected {expected[r, c]} "
                f"({len(bad)} of {expected.size} elements differ)"
            )
