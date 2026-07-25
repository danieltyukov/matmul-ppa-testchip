# Copyright 2026 Daniel Tyukov
# SPDX-License-Identifier: Apache-2.0
"""SPI control plane protocol tests.

Covers every opcode, unknown opcodes, truncated frames, back-to-back frames,
readback correctness, address range violations and commands that arrive while the
core is busy. Everything is driven through the four SPI pins; nothing here reaches
into the design.
"""

from __future__ import annotations

import os

import cocotb
import numpy as np

import gemm_model as gm
from chip_env import bring_up


@cocotb.test()
async def test_identity_and_geometry(dut):
    """OP_RD_ID and OP_RD_CFG must report this build's identity and geometry."""
    spi = await bring_up(dut)

    ident = await spi.read_id()
    assert ident == gm.CHIP_ID, f"identity 0x{ident:08X}, expected 0x{gm.CHIP_ID:08X}"

    cfg = await spi.read_cfg()
    expected = {
        "mat_m": gm.MAT_M, "mat_n": gm.MAT_N, "mat_k": gm.MAT_K,
        "tile_m": gm.TILE_M, "tile_n": gm.TILE_N, "tile_k": gm.TILE_K,
        "operand_w": gm.OPERAND_W, "acc_w": gm.ACC_W,
        "engine_count": gm.ENGINE_COUNT, "engine_sel": 0,
    }
    assert cfg == expected, f"geometry readback {cfg}, expected {expected}"
    dut._log.info(f"geometry discovered over SPI: {cfg}")


@cocotb.test()
async def test_status_after_reset(dut):
    """Every status bit must be clear after reset."""
    spi = await bring_up(dut)
    status = await spi.read_status()
    assert status == 0x00, f"status after reset is 0x{status:02X}, expected 0x00"


@cocotb.test()
async def test_engine_selection_readback(dut):
    """Every valid engine index must be selectable and read back through OP_RD_CFG."""
    spi = await bring_up(dut)
    for engine in range(gm.ENGINE_COUNT):
        await spi.select_engine(engine)
        cfg = await spi.read_cfg()
        assert cfg["engine_sel"] == engine, (
            f"selected engine {engine}, chip reports {cfg['engine_sel']}"
        )
        status = await spi.read_status()
        assert not (status & gm.ST_CMD_ERR), (
            f"selecting engine {engine} set the command error flag"
        )


@cocotb.test()
async def test_engine_selection_out_of_range(dut):
    """An engine index the build does not have must be refused and flagged."""
    spi = await bring_up(dut)
    await spi.select_engine(gm.ENGINE_COUNT - 1)
    await spi.select_engine(gm.ENGINE_COUNT + 3)

    status = await spi.read_status()
    assert status & gm.ST_CMD_ERR, "an out of range engine index did not set command error"

    cfg = await spi.read_cfg()
    assert cfg["engine_sel"] == gm.ENGINE_COUNT - 1, (
        "a refused engine selection changed the active engine"
    )

    await spi.trigger(gm.TRIG_CLR_STICKY)
    status = await spi.read_status()
    assert not (status & gm.ST_CMD_ERR), "the sticky clear trigger did not clear command error"


@cocotb.test()
async def test_unknown_opcodes(dut):
    """Unknown opcodes must set the command error flag and change nothing else."""
    spi = await bring_up(dut)
    await spi.select_engine(2)

    unknown = [0x04, 0x07, 0x0A, 0x10, 0x55, 0x7F, 0x80, 0x89, 0xC0, 0xFE, 0xFF]
    for opcode in unknown:
        await spi.trigger(gm.TRIG_CLR_STICKY)
        await spi.frame(bytes([opcode, 0x11, 0x22, 0x33]))
        status = await spi.read_status()
        assert status & gm.ST_CMD_ERR, (
            f"opcode 0x{opcode:02X} is not implemented but did not set command error"
        )
        assert not (status & (gm.ST_BUSY | gm.ST_VFY_BUSY)), (
            f"opcode 0x{opcode:02X} started something"
        )

    cfg = await spi.read_cfg()
    assert cfg["engine_sel"] == 2, "an unknown opcode changed the selected engine"
    dut._log.info(f"unknown opcodes rejected: {len(unknown)}")


@cocotb.test()
async def test_nop_is_silent(dut):
    """OP_NOP must be accepted and must not set any error flag."""
    spi = await bring_up(dut)
    await spi.frame(bytes([gm.OP_NOP]))
    await spi.frame(bytes([gm.OP_NOP, 0x00, 0x00, 0x00]))
    status = await spi.read_status()
    assert status == 0x00, f"NOP left status at 0x{status:02X}"


@cocotb.test()
async def test_truncated_frames(dut):
    """Frames that end before the opcode's required bytes must set the frame error flag."""
    spi = await bring_up(dut)

    cases = [
        ("write A, no address", bytes([gm.OP_WR_A])),
        ("write A, half address", bytes([gm.OP_WR_A, 0x00])),
        ("read C, no address", bytes([gm.OP_RD_C])),
        ("read C, half address", bytes([gm.OP_RD_C, 0x00])),
        ("select engine, no value", bytes([gm.OP_WR_ENGINE])),
        ("trigger, no value", bytes([gm.OP_WR_TRIG])),
        ("soft reset, no key", bytes([gm.OP_SOFT_RST])),
    ]
    for label, payload in cases:
        await spi.trigger(gm.TRIG_CLR_STICKY)
        await spi.frame(payload)
        status = await spi.read_status()
        assert status & gm.ST_FRAME_ERR, f"truncated frame ({label}) did not set frame error"

    # A frame that carries the full address but no data is complete, not truncated:
    # it is how a host sets up an address and stops.
    for label, payload in [
        ("write A, address only", bytes([gm.OP_WR_A, 0x00, 0x00])),
        ("read C, address only", bytes([gm.OP_RD_C, 0x00, 0x00])),
    ]:
        await spi.trigger(gm.TRIG_CLR_STICKY)
        await spi.frame(payload)
        status = await spi.read_status()
        assert not (status & gm.ST_FRAME_ERR), (
            f"a zero length but complete frame ({label}) was reported as truncated"
        )

    dut._log.info(f"truncated frame cases checked: {len(cases)}")


@cocotb.test()
async def test_partial_byte_frame(dut):
    """A frame that ends mid-byte must not be mistaken for a complete byte."""
    spi = await bring_up(dut)
    await spi.trigger(gm.TRIG_CLR_STICKY)

    # Clock out the opcode, then only five bits of the address high byte.
    await spi.select()
    await spi.xfer_byte(gm.OP_WR_ENGINE)
    await spi.xfer_bits(0b10110, 5)
    await spi.deselect()

    status = await spi.read_status()
    assert status & gm.ST_FRAME_ERR, "a frame cut off mid-byte did not set frame error"

    # The chip must still be usable afterwards.
    await spi.trigger(gm.TRIG_CLR_STICKY)
    await spi.select_engine(1)
    cfg = await spi.read_cfg()
    assert cfg["engine_sel"] == 1, "the chip did not recover from a mid-byte frame end"


@cocotb.test()
async def test_memory_write_read_round_trip(dut):
    """Operand and reference stores must read back exactly what was written."""
    spi = await bring_up(dut)
    rng = np.random.default_rng(int(os.environ.get("GEMM_SEED", 20260725)) + 5)

    a = gm.random_int8(rng, (gm.MAT_M, gm.MAT_K))
    b = gm.random_int8(rng, (gm.MAT_K, gm.MAT_N))
    ref = gm.matmul_ref(a, b)

    await spi.load_a(a)
    await spi.load_b(b)
    await spi.load_reference(ref)

    got_a = await spi.read_memory(gm.OP_RD_A, 0, gm.A_BYTES)
    assert got_a == gm.matrix_to_bytes_int8(a), "operand store A did not read back"

    got_b = await spi.read_memory(gm.OP_RD_B, 0, gm.B_BYTES)
    assert got_b == gm.matrix_to_bytes_int8(b), "operand store B did not read back"

    got_ref = await spi.read_memory(gm.OP_RD_REF, 0, gm.C_BYTES)
    assert got_ref == gm.matrix_to_bytes_int32(ref), "reference store did not read back"

    status = await spi.read_status()
    assert status == 0x00, f"a clean round trip left status at 0x{status:02X}"
    dut._log.info(
        f"round tripped {gm.A_BYTES + gm.B_BYTES + gm.C_BYTES} bytes through the stores"
    )


@cocotb.test()
async def test_partial_and_offset_writes(dut):
    """Auto-incrementing addresses must allow short writes at an offset."""
    spi = await bring_up(dut)
    rng = np.random.default_rng(3)

    a = gm.random_int8(rng, (gm.MAT_M, gm.MAT_K))
    await spi.load_a(a)

    # Rewrite one row, then one single byte, then check the whole matrix.
    row = 7
    new_row = gm.random_int8(rng, (gm.MAT_K,))
    await spi.write_memory(gm.OP_WR_A, row * gm.MAT_K, bytes(
        gm.to_unsigned(int(v), 8) for v in new_row))
    a[row, :] = new_row

    a[0, 3] = -128
    await spi.write_memory(gm.OP_WR_A, 3, bytes([0x80]))

    got = await spi.read_memory(gm.OP_RD_A, 0, gm.A_BYTES)
    assert got == gm.matrix_to_bytes_int8(a), "offset writes did not land where expected"

    # A read that starts at an offset must return the tail of the matrix.
    offset = 100
    tail = await spi.read_memory(gm.OP_RD_A, offset, gm.A_BYTES - offset)
    assert tail == gm.matrix_to_bytes_int8(a)[offset:], "an offset read returned the wrong bytes"


@cocotb.test()
async def test_address_out_of_range(dut):
    """An address past the end of a store must be refused and flagged."""
    spi = await bring_up(dut)

    await spi.trigger(gm.TRIG_CLR_STICKY)
    await spi.write_memory(gm.OP_WR_A, gm.A_BYTES, bytes([0xAA]))
    status = await spi.read_status()
    assert status & gm.ST_CMD_ERR, "a write past the end of store A was not flagged"

    await spi.trigger(gm.TRIG_CLR_STICKY)
    await spi.read_memory(gm.OP_RD_C, gm.C_BYTES + 16, 4)
    status = await spi.read_status()
    assert status & gm.ST_CMD_ERR, "a read past the end of the result store was not flagged"

    # A full length read that ends exactly at the last byte is legal, and the
    # prefetch that runs one byte past the end must not be reported as an error.
    await spi.trigger(gm.TRIG_CLR_STICKY)
    await spi.read_memory(gm.OP_RD_A, 0, gm.A_BYTES)
    status = await spi.read_status()
    assert not (status & gm.ST_CMD_ERR), (
        "a full length read was wrongly flagged, so the end of buffer prefetch is not handled"
    )


@cocotb.test()
async def test_back_to_back_frames(dut):
    """Many frames with minimal idle time between them must all be interpreted."""
    spi = await bring_up(dut)

    frames = 0
    for i in range(24):
        engine = i % gm.ENGINE_COUNT
        # No extra idle: deselect and select again straight away.
        await spi.frame(bytes([gm.OP_WR_ENGINE, engine]))
        cfg = await spi.read_cfg()
        assert cfg["engine_sel"] == engine, (
            f"back-to-back frame {i} selected {engine} but the chip reports "
            f"{cfg['engine_sel']}"
        )
        frames += 2

    status = await spi.read_status()
    assert status == 0x00, f"back-to-back frames left status at 0x{status:02X}"
    dut._log.info(f"back-to-back frames exchanged: {frames}")


@cocotb.test()
async def test_command_while_busy(dut):
    """Store access must be refused while the core runs, status reads must not be."""
    spi = await bring_up(dut)
    rng = np.random.default_rng(17)

    a = gm.random_int8(rng, (gm.MAT_M, gm.MAT_K))
    b = gm.random_int8(rng, (gm.MAT_K, gm.MAT_N))
    await spi.load_a(a)
    await spi.load_b(b)
    await spi.select_engine(gm.ENG_BITSERIAL)   # the slowest, so the window is wide

    await spi.trigger(gm.TRIG_RUN)

    status = await spi.read_status()
    assert status & gm.ST_BUSY, "the chip did not report busy right after a run trigger"

    # An operand write while busy must be refused and must not corrupt the run.
    await spi.write_memory(gm.OP_WR_A, 0, bytes([0x7F] * 16))
    status = await spi.read_status()
    assert status & gm.ST_CMD_ERR, "an operand write during a run was not refused"
    assert status & gm.ST_BUSY, "a refused command disturbed the run"

    # A second run trigger while busy must be refused too.
    await spi.trigger(gm.TRIG_RUN)
    status = await spi.read_status()
    assert status & gm.ST_BUSY, "a refused second run trigger disturbed the first"

    # Performance counters and geometry stay readable throughout.
    perf = await spi.read_perf()
    assert perf["cycles"] > 0, "the cycle counter did not advance during a run"
    cfg = await spi.read_cfg()
    assert cfg["engine_count"] == gm.ENGINE_COUNT

    await spi.wait_idle()
    status = await spi.read_status()
    assert status & gm.ST_DONE, "the run did not finish"

    await spi.trigger(gm.TRIG_CLR_STICKY)
    result = await spi.read_result()
    expected = gm.matmul_ref(a, b)
    assert np.array_equal(result, expected), (
        "the refused write during the run corrupted the result after all"
    )
    dut._log.info("commands during a run were refused without disturbing it")


@cocotb.test()
async def test_soft_reset(dut):
    """A keyed soft reset must clear the datapath but leave the SPI front end usable."""
    spi = await bring_up(dut)
    rng = np.random.default_rng(23)

    a = gm.random_int8(rng, (gm.MAT_M, gm.MAT_K))
    b = gm.random_int8(rng, (gm.MAT_K, gm.MAT_N))
    await spi.load_a(a)
    await spi.load_b(b)
    await spi.select_engine(3)
    await spi.run_gemm()

    status = await spi.read_status()
    assert status & gm.ST_DONE

    # The wrong key must do nothing.
    await spi.write_reg(gm.OP_SOFT_RST, 0x00)
    status = await spi.read_status()
    assert status & gm.ST_DONE, "a soft reset with the wrong key took effect anyway"
    cfg = await spi.read_cfg()
    assert cfg["engine_sel"] == 3, "a soft reset with the wrong key cleared engine selection"

    await spi.soft_reset()
    status = await spi.read_status()
    assert status == 0x00, f"status after a soft reset is 0x{status:02X}, expected 0x00"
    perf = await spi.read_perf()
    assert perf["cycles"] == 0 and perf["macs"] == 0, (
        f"the counters survived a soft reset: {perf}"
    )
    cfg = await spi.read_cfg()
    assert cfg["engine_sel"] == 0, "a soft reset did not return engine selection to zero"

    # The operand stores are memory, not state, so they must be untouched, and the
    # chip must run again without another hard reset.
    got_a = await spi.read_memory(gm.OP_RD_A, 0, gm.A_BYTES)
    assert got_a == gm.matrix_to_bytes_int8(a), "a soft reset disturbed the operand store"

    await spi.run_gemm()
    result = await spi.read_result()
    assert np.array_equal(result, gm.matmul_ref(a, b)), (
        "the chip did not compute correctly after a soft reset"
    )


@cocotb.test()
async def test_spi_clock_ratio_sweep(dut):
    """The protocol must work across the supported core-to-SPI clock ratio range.

    Every other test runs at four core cycles per SPI half period, which is
    f_spi = f_core/8, the documented maximum and therefore the tightest case for the
    readback prefetch. This one also checks slower SPI clocks, where the risk is the
    opposite: a byte boundary handshake that only works when it is rushed.
    """
    for cycles_per_half in (4, 6, 8, 16):
        spi = await bring_up(dut, cycles_per_half=cycles_per_half)

        ident = await spi.read_id()
        assert ident == gm.CHIP_ID, (
            f"at {cycles_per_half} core cycles per SPI half period the identity "
            f"read back 0x{ident:08X}"
        )

        payload = bytes(range(32))
        await spi.write_memory(gm.OP_WR_A, 0, payload)
        got = await spi.read_memory(gm.OP_RD_A, 0, len(payload))
        assert got == payload, (
            f"at {cycles_per_half} core cycles per SPI half period the readback was "
            f"wrong: {got.hex()} vs {payload.hex()}"
        )
        dut._log.info(
            f"protocol verified at f_spi = f_core/{2 * cycles_per_half}"
        )
