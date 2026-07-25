# Copyright 2026 Daniel Tyukov
# SPDX-License-Identifier: Apache-2.0
"""cocotb SPI controller model for the matmul PPA test chip.

Mode 0, MSB first, full duplex. The chip oversamples its SPI pins in the core
clock domain and needs f_spi <= f_core/8, so the driver measures its bit period in
core clock cycles: CYCLES_PER_HALF core cycles per SPI half period.

Every method here talks to the chip only through the four SPI pins. Nothing pokes
internal signals, so the same sequences would work against real silicon driven by
an FPGA or a Raspberry Pi.
"""

from __future__ import annotations

import cocotb
from cocotb.triggers import ClockCycles

import gemm_model as gm

# Core clock cycles per SPI half period. Four is f_spi = f_core/8, the maximum the
# target documents, so the default test configuration is also the tightest timing
# case for the readback prefetch. test_spi_protocol also runs a relaxed case.
CYCLES_PER_HALF = 4


class SpiController:
    """Drives the chip's SPI pins from cocotb."""

    def __init__(self, dut, cycles_per_half: int = CYCLES_PER_HALF):
        self.dut = dut
        self.half = cycles_per_half
        self.frames = 0
        self.bytes_out = 0
        # Event log for the timing diagram generator: (core cycle, event, value).
        self.trace: list[tuple[int, str, int]] = []
        self.trace_enabled = False
        self.cycle = 0

    # -- low level ---------------------------------------------------------
    async def _tick(self, n: int = 1):
        await ClockCycles(self.dut.pad_clk_i, n)
        self.cycle += n

    def _log(self, event: str, value: int = 0):
        if self.trace_enabled:
            self.trace.append((self.cycle, event, value))

    async def idle(self, cycles: int = CYCLES_PER_HALF):
        self.dut.pad_spi_cs_ni.value = 1
        self.dut.pad_spi_sck_i.value = 0
        self.dut.pad_spi_mosi_i.value = 0
        await self._tick(cycles)

    async def select(self):
        self.dut.pad_spi_sck_i.value = 0
        self.dut.pad_spi_cs_ni.value = 0
        self._log("cs_assert")
        await self._tick(self.half)

    async def deselect(self):
        self.dut.pad_spi_sck_i.value = 0
        await self._tick(self.half)
        self.dut.pad_spi_cs_ni.value = 1
        self._log("cs_release")
        await self._tick(self.half)
        self.frames += 1

    async def xfer_byte(self, value: int) -> int:
        """Shift one byte out and one byte in, MSB first."""
        received = 0
        for bit in range(7, -1, -1):
            self.dut.pad_spi_mosi_i.value = (value >> bit) & 1
            await self._tick(self.half)
            # Mode 0: the controller samples MISO on the rising edge.
            self.dut.pad_spi_sck_i.value = 1
            miso = int(self.dut.pad_spi_miso_io.value)
            received = (received << 1) | (miso & 1)
            await self._tick(self.half)
            self.dut.pad_spi_sck_i.value = 0
        self.bytes_out += 1
        self._log("byte", value)
        return received

    async def xfer_bits(self, value: int, n_bits: int):
        """Shift out fewer than eight bits, to build a deliberately broken frame."""
        for bit in range(n_bits - 1, -1, -1):
            self.dut.pad_spi_mosi_i.value = (value >> bit) & 1
            await self._tick(self.half)
            self.dut.pad_spi_sck_i.value = 1
            await self._tick(self.half)
            self.dut.pad_spi_sck_i.value = 0

    async def frame(self, payload: bytes) -> bytes:
        """One complete frame: assert chip select, shift payload, release."""
        await self.select()
        received = bytearray()
        for byte in payload:
            received.append(await self.xfer_byte(byte))
        await self.deselect()
        return bytes(received)

    # -- protocol ----------------------------------------------------------
    async def write_memory(self, opcode: int, addr: int, data: bytes):
        await self.frame(bytes([opcode, (addr >> 8) & 0xFF, addr & 0xFF]) + data)

    async def read_memory(self, opcode: int, addr: int, length: int) -> bytes:
        got = await self.frame(
            bytes([opcode, (addr >> 8) & 0xFF, addr & 0xFF]) + bytes(length)
        )
        # The first three returned bytes overlap the opcode and address phases; the
        # payload starts once the address has landed and the first byte has been
        # prefetched.
        return got[3:]

    async def write_reg(self, opcode: int, value: int):
        await self.frame(bytes([opcode, value & 0xFF]))

    async def read_reg(self, opcode: int, length: int) -> bytes:
        got = await self.frame(bytes([opcode]) + bytes(length))
        return got[1:]

    async def read_status(self) -> int:
        return (await self.read_reg(gm.OP_RD_STATUS, 1))[0]

    async def read_id(self) -> int:
        return int.from_bytes(await self.read_reg(gm.OP_RD_ID, 4), "big")

    async def read_cfg(self) -> dict:
        raw = await self.read_reg(gm.OP_RD_CFG, 10)
        keys = [
            "mat_m", "mat_n", "mat_k",
            "tile_m", "tile_n", "tile_k",
            "operand_w", "acc_w",
            "engine_count", "engine_sel",
        ]
        return dict(zip(keys, raw))

    async def read_perf(self) -> dict:
        raw = await self.read_reg(gm.OP_RD_PERF, 12)
        return {
            "cycles": int.from_bytes(raw[0:4], "little"),
            "macs": int.from_bytes(raw[4:8], "little"),
            "mismatch_count": int.from_bytes(raw[8:10], "little"),
            "first_mismatch": int.from_bytes(raw[10:12], "little"),
        }

    async def select_engine(self, engine: int):
        await self.write_reg(gm.OP_WR_ENGINE, engine)

    async def trigger(self, bits: int):
        await self.write_reg(gm.OP_WR_TRIG, bits)

    async def soft_reset(self):
        await self.write_reg(gm.OP_SOFT_RST, gm.SOFT_RST_KEY)

    async def load_a(self, matrix):
        await self.write_memory(gm.OP_WR_A, 0, gm.matrix_to_bytes_int8(matrix))

    async def load_b(self, matrix):
        await self.write_memory(gm.OP_WR_B, 0, gm.matrix_to_bytes_int8(matrix))

    async def load_reference(self, matrix):
        await self.write_memory(gm.OP_WR_REF, 0, gm.matrix_to_bytes_int32(matrix))

    async def read_result(self):
        data = await self.read_memory(gm.OP_RD_C, 0, gm.C_BYTES)
        return gm.bytes_to_matrix_int32(data, gm.MAT_M, gm.MAT_N)

    async def wait_idle(self, timeout_frames: int = 200) -> int:
        """Poll the status byte until neither the sequencer nor the checker is busy."""
        for _ in range(timeout_frames):
            status = await self.read_status()
            if not (status & (gm.ST_BUSY | gm.ST_VFY_BUSY)):
                return status
        raise TimeoutError("chip stayed busy for too many status polls")

    async def run_gemm(self, engine: int | None = None) -> int:
        if engine is not None:
            await self.select_engine(engine)
        await self.trigger(gm.TRIG_RUN)
        return await self.wait_idle()

    async def verify(self) -> int:
        await self.trigger(gm.TRIG_VERIFY)
        return await self.wait_idle()


async def reset_chip(dut, spi: SpiController, cycles: int = 12):
    """Hold the reset pin low, then release it with the SPI bus idle."""
    dut.pad_rst_ni.value = 0
    dut.pad_test_mode_i.value = 0
    dut.pad_spi_cs_ni.value = 1
    dut.pad_spi_sck_i.value = 0
    dut.pad_spi_mosi_i.value = 0
    await ClockCycles(dut.pad_clk_i, cycles)
    dut.pad_rst_ni.value = 1
    await ClockCycles(dut.pad_clk_i, cycles)
    spi.cycle += 2 * cycles
