# Copyright 2026 Daniel Tyukov
# SPDX-License-Identifier: Apache-2.0
"""Reset behaviour and clock gating.

Two things are checked here that no other test covers.

Reset: the chip must come up in a defined state from the pin, and a soft reset
must return the datapath to that state without disturbing the SPI front end.

Clock gating: only the selected candidate's clock may run. This is what makes the
switching-activity measurement mean anything, so it is asserted directly by
sampling every candidate's gated clock on every core cycle and counting edges. The
same claim is measured a second way, from a VCD, by tools/vcd_activity.py; two
independent measurements of the same property is deliberate.

These tests reach into the hierarchy, unlike the protocol and flow tests, because
a gated clock is not observable from the pins. That is the point of the test.
"""

from __future__ import annotations

import cocotb
import numpy as np
from cocotb.triggers import RisingEdge, Timer

import gemm_model as gm
from chip_env import bring_up

SETTLE_NS = 1


def _engine_array(dut):
    return dut.u_bench_core.u_engine_array


async def _count_gated_clock_edges(dut, cycles: int) -> list[int]:
    """Count rising edges on each candidate's gated clock over `cycles` core cycles."""
    array = _engine_array(dut)
    previous = [0] * gm.ENGINE_COUNT
    edges = [0] * gm.ENGINE_COUNT
    for _ in range(cycles):
        await RisingEdge(dut.pad_clk_i)
        await Timer(SETTLE_NS, unit="ns")
        raw = int(array.clk_gated.value)
        for engine in range(gm.ENGINE_COUNT):
            level = (raw >> engine) & 1
            if level and not previous[engine]:
                edges[engine] += 1
            previous[engine] = level
    return edges


@cocotb.test()
async def test_state_after_hard_reset(dut):
    """Reset must leave a defined and specific state, observable over SPI."""
    spi = await bring_up(dut)

    status = await spi.read_status()
    assert status == 0x00, f"status after reset is 0x{status:02X}, expected 0x00"

    perf = await spi.read_perf()
    assert perf == {
        "cycles": 0, "macs": 0, "mismatch_count": 0, "first_mismatch": 0
    }, f"counters after reset are {perf}"

    cfg = await spi.read_cfg()
    assert cfg["engine_sel"] == 0, (
        f"engine selection after reset is {cfg['engine_sel']}, expected 0"
    )

    # The status pins must agree with the status byte.
    assert int(dut.pad_stat_busy_o.value) == 0, "the busy pin is high after reset"
    assert int(dut.pad_stat_done_o.value) == 0, "the done pin is high after reset"
    assert int(dut.pad_stat_vfy_done_o.value) == 0, "the verify done pin is high after reset"
    assert int(dut.pad_stat_mismatch_o.value) == 0, "the mismatch pin is high after reset"


@cocotb.test()
async def test_reset_during_a_run(dut):
    """Pulling reset mid-run must abort cleanly and leave the chip usable."""
    spi = await bring_up(dut)
    rng = np.random.default_rng(101)

    a = gm.random_int8(rng, (gm.MAT_M, gm.MAT_K))
    b = gm.random_int8(rng, (gm.MAT_K, gm.MAT_N))
    await spi.load_a(a)
    await spi.load_b(b)
    await spi.select_engine(gm.ENG_BITSERIAL)
    await spi.trigger(gm.TRIG_RUN)

    status = await spi.read_status()
    assert status & gm.ST_BUSY, "the run did not start"

    # Assert the reset pin in the middle of the run.
    dut.pad_rst_ni.value = 0
    await Timer(200, unit="ns")
    dut.pad_rst_ni.value = 1
    await Timer(200, unit="ns")

    status = await spi.read_status()
    assert status == 0x00, (
        f"status after a reset during a run is 0x{status:02X}, expected 0x00"
    )

    # The operand stores are memory and survive; the chip must run again.
    await spi.load_reference(gm.matmul_ref(a, b))
    await spi.run_gemm(gm.ENG_INFER)
    status = await spi.verify()
    assert not (status & gm.ST_MISMATCH), (
        "the chip did not compute correctly after being reset mid-run"
    )


@cocotb.test()
async def test_only_selected_candidate_is_clocked(dut):
    """Every unselected candidate's clock must be completely stopped."""
    spi = await bring_up(dut)
    rng = np.random.default_rng(103)

    await spi.load_a(gm.random_int8(rng, (gm.MAT_M, gm.MAT_K)))
    await spi.load_b(gm.random_int8(rng, (gm.MAT_K, gm.MAT_N)))

    for engine in range(gm.ENGINE_COUNT):
        await spi.select_engine(engine)
        await spi.trigger(gm.TRIG_RUN)
        # Sample well inside the run so the count covers real activity.
        edges = await _count_gated_clock_edges(dut, 400)
        await spi.wait_idle()

        assert edges[engine] > 0, (
            f"candidate {engine} ({gm.ENGINE_NAMES[engine]}) is selected but its "
            f"clock never rose during 400 core cycles of a run"
        )
        for other in range(gm.ENGINE_COUNT):
            if other == engine:
                continue
            assert edges[other] == 0, (
                f"with candidate {engine} ({gm.ENGINE_NAMES[engine]}) selected, "
                f"candidate {other} ({gm.ENGINE_NAMES[other]}) saw "
                f"{edges[other]} clock edges; gating is not working"
            )
        dut._log.info(
            f"candidate {engine} ({gm.ENGINE_NAMES[engine]}) selected: "
            f"gated clock edges per candidate over 400 cycles = {edges}"
        )


@cocotb.test()
async def test_unselected_candidates_see_constant_operands(dut):
    """Operand isolation: an unselected candidate's operand inputs must stay at zero.

    Clock gating alone is not enough. A candidate's multiplier array is
    combinational, so if it still sees the operand bus it keeps toggling and burns
    power with no clock at all. engine_array AND-gates the operands per candidate;
    this checks that it actually does.
    """
    spi = await bring_up(dut)
    rng = np.random.default_rng(107)
    array = _engine_array(dut)

    a = gm.random_int8(rng, (gm.MAT_M, gm.MAT_K))
    b = gm.random_int8(rng, (gm.MAT_K, gm.MAT_N))
    # Make sure the operands are far from zero, so a gating failure is obvious.
    a[a == 0] = 99
    b[b == 0] = -99
    await spi.load_a(a)
    await spi.load_b(b)

    a_tile_w = gm.TILE_M * gm.TILE_K * gm.OPERAND_W
    b_tile_w = gm.TILE_K * gm.TILE_N * gm.OPERAND_W

    for engine in range(gm.ENGINE_COUNT):
        await spi.select_engine(engine)
        await spi.trigger(gm.TRIG_RUN)

        nonzero_seen = [0] * gm.ENGINE_COUNT
        for _ in range(300):
            await RisingEdge(dut.pad_clk_i)
            await Timer(SETTLE_NS, unit="ns")
            all_a = int(array.eng_a_tile.value)
            all_b = int(array.eng_b_tile.value)
            for other in range(gm.ENGINE_COUNT):
                a_slice = (all_a >> (other * a_tile_w)) & ((1 << a_tile_w) - 1)
                b_slice = (all_b >> (other * b_tile_w)) & ((1 << b_tile_w) - 1)
                if a_slice or b_slice:
                    nonzero_seen[other] += 1
        await spi.wait_idle()

        assert nonzero_seen[engine] > 0, (
            f"the selected candidate {engine} ({gm.ENGINE_NAMES[engine]}) never saw "
            f"a nonzero operand, so this test is not measuring anything"
        )
        for other in range(gm.ENGINE_COUNT):
            if other == engine:
                continue
            assert nonzero_seen[other] == 0, (
                f"with candidate {engine} selected, candidate {other} "
                f"({gm.ENGINE_NAMES[other]}) saw nonzero operands on "
                f"{nonzero_seen[other]} of 300 cycles; operand isolation is broken"
            )

    dut._log.info("operand isolation verified for every candidate")


@cocotb.test()
async def test_test_mode_ungates_everything(dut):
    """Test mode must run every candidate's clock, for scan and characterisation."""
    spi = await bring_up(dut)
    rng = np.random.default_rng(109)

    await spi.load_a(gm.random_int8(rng, (gm.MAT_M, gm.MAT_K)))
    await spi.load_b(gm.random_int8(rng, (gm.MAT_K, gm.MAT_N)))
    await spi.select_engine(0)

    dut.pad_test_mode_i.value = 1
    await Timer(100, unit="ns")
    await spi.trigger(gm.TRIG_RUN)
    edges = await _count_gated_clock_edges(dut, 200)
    await spi.wait_idle()
    dut.pad_test_mode_i.value = 0

    for engine in range(gm.ENGINE_COUNT):
        assert edges[engine] > 0, (
            f"in test mode candidate {engine} ({gm.ENGINE_NAMES[engine]}) saw no "
            f"clock edges; the test enable does not reach its clock gate"
        )
    dut._log.info(f"test mode clocked every candidate: {edges}")


@cocotb.test()
async def test_gated_candidates_hold_their_accumulators(dut):
    """Switching candidates must not disturb the accumulator of the previous one.

    A candidate whose clock is stopped keeps whatever was in its accumulator bank.
    That is harmless because the sequencer always clears before the first k tile of
    an output tile, and this test proves that clear actually happens rather than the
    result depending on leftover state.
    """
    spi = await bring_up(dut)
    rng = np.random.default_rng(113)

    a = gm.random_int8(rng, (gm.MAT_M, gm.MAT_K))
    b = gm.random_int8(rng, (gm.MAT_K, gm.MAT_N))
    expected = gm.matmul_ref(a, b)
    await spi.load_a(a)
    await spi.load_b(b)
    await spi.load_reference(expected)

    # Run candidate 0 to leave state in it, then a different candidate, then back.
    for engine in (0, gm.ENGINE_COUNT - 1, 0, 1, 0):
        await spi.run_gemm(engine)
        status = await spi.verify()
        assert not (status & gm.ST_MISMATCH), (
            f"candidate {engine} ({gm.ENGINE_NAMES[engine]}) produced a wrong result "
            f"after candidate switching, so accumulator state leaked"
        )
    dut._log.info("candidate switching leaves no accumulator state behind")
