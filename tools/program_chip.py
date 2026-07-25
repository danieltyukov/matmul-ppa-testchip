#!/usr/bin/env python3
# Copyright 2026 Daniel Tyukov
# SPDX-License-Identifier: Apache-2.0
"""Host driver for the packaged chip, over a real SPI bus.

The same command sequences the cocotb tests use, driven from Linux spidev instead of
a simulator. Written for a Raspberry Pi or any board that exposes /dev/spidev*, and
it is the piece that turns simulation results into silicon results.

This has never been run against silicon, because nothing has been fabricated. It has
been checked against the protocol the tests exercise, and the frame construction is
shared with the testbench through tb/gemm_model.py, so a protocol change cannot make
these two disagree silently.

    # discover the chip and its geometry
    tools/program_chip.py info

    # run one candidate on a random workload and check it on chip
    tools/program_chip.py bench --engine 1

    # sweep every candidate and print a PPA table from the chip's own counters
    tools/program_chip.py sweep --clock-hz 50e6

    # dump the result matrix
    tools/program_chip.py read-c --out result.npy

Wiring, from docs/ARCHITECTURE.md:

    pad_spi_sck_i   <- SCLK      pad_clk_i        <- board oscillator
    pad_spi_cs_ni   <- CE0       pad_rst_ni       <- a GPIO, active low
    pad_spi_mosi_i  <- MOSI      pad_test_mode_i  <- tie low
    pad_spi_miso_io -> MISO      pad_stat_*       -> GPIOs, optional

Keep the SPI clock at or below f_core/8. At the 50 MHz core target that is 6.25 MHz.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import time

import numpy as np

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tb"))

import gemm_model as gm  # noqa: E402


class SpiLink:
    """Thin wrapper over spidev, with the same frame layout as the testbench driver."""

    def __init__(self, bus: int, device: int, speed_hz: int):
        try:
            import spidev
        except ImportError:  # pragma: no cover - depends on the host
            raise SystemExit(
                "program_chip: spidev is not installed. On a Raspberry Pi:\n"
                "  sudo apt-get install python3-spidev\n"
                "and enable SPI with raspi-config."
            )
        self.spi = spidev.SpiDev()
        self.spi.open(bus, device)
        self.spi.mode = 0             # CPOL = 0, CPHA = 0, matching spi_target.sv
        self.spi.bits_per_word = 8
        self.spi.lsbfirst = False     # MSB first
        self.spi.max_speed_hz = speed_hz
        # Chip select must fall and rise around each frame, which spidev does per
        # xfer2 call. cshigh stays False: the chip's select is active low.
        self.spi.cshigh = False

    def frame(self, payload: bytes) -> bytes:
        return bytes(self.spi.xfer2(list(payload)))

    def close(self):
        self.spi.close()

    # -- protocol, mirroring tb/spi_driver.py ------------------------------
    def write_memory(self, opcode: int, addr: int, data: bytes):
        # Long transfers are chunked so one frame is not larger than the kernel's
        # SPI buffer. The chip auto-increments its address, so a chunked write and a
        # single write land in exactly the same place.
        chunk = 2048
        offset = 0
        while offset < len(data):
            piece = data[offset:offset + chunk]
            here = addr + offset
            self.frame(bytes([opcode, (here >> 8) & 0xFF, here & 0xFF]) + piece)
            offset += len(piece)

    def read_memory(self, opcode: int, addr: int, length: int) -> bytes:
        out = bytearray()
        chunk = 2048
        offset = 0
        while offset < length:
            want = min(chunk, length - offset)
            here = addr + offset
            got = self.frame(
                bytes([opcode, (here >> 8) & 0xFF, here & 0xFF]) + bytes(want)
            )
            out += got[3:]           # three bytes cover the opcode and address phases
            offset += want
        return bytes(out)

    def write_reg(self, opcode: int, value: int):
        self.frame(bytes([opcode, value & 0xFF]))

    def read_reg(self, opcode: int, length: int) -> bytes:
        return self.frame(bytes([opcode]) + bytes(length))[1:]

    def status(self) -> int:
        return self.read_reg(gm.OP_RD_STATUS, 1)[0]

    def identify(self) -> int:
        return int.from_bytes(self.read_reg(gm.OP_RD_ID, 4), "big")

    def config(self) -> dict:
        raw = self.read_reg(gm.OP_RD_CFG, gm.CFG_BYTES if hasattr(gm, "CFG_BYTES") else 10)
        keys = ["mat_m", "mat_n", "mat_k", "tile_m", "tile_n", "tile_k",
                "operand_w", "acc_w", "engine_count", "engine_sel"]
        return dict(zip(keys, raw))

    def perf(self) -> dict:
        raw = self.read_reg(gm.OP_RD_PERF, 12)
        return {
            "cycles": int.from_bytes(raw[0:4], "little"),
            "macs": int.from_bytes(raw[4:8], "little"),
            "mismatch_count": int.from_bytes(raw[8:10], "little"),
            "first_mismatch": int.from_bytes(raw[10:12], "little"),
        }

    def wait_idle(self, timeout_s: float = 10.0) -> int:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            status = self.status()
            if not (status & (gm.ST_BUSY | gm.ST_VFY_BUSY)):
                return status
            time.sleep(0.001)
        raise TimeoutError(
            "the chip stayed busy. Check the core clock is running and that the SPI "
            "clock is at or below f_core/8."
        )


def load_workload(link: SpiLink, cfg: dict, seed: int):
    """Generate a random workload sized from the chip's own geometry report."""
    rng = np.random.default_rng(seed)
    a = rng.integers(-128, 128, size=(cfg["mat_m"], cfg["mat_k"]),
                     dtype=np.int64).astype(np.int8)
    b = rng.integers(-128, 128, size=(cfg["mat_k"], cfg["mat_n"]),
                     dtype=np.int64).astype(np.int8)
    expected = gm.matmul_ref(a, b)

    link.write_memory(gm.OP_WR_A, 0, gm.matrix_to_bytes_int8(a))
    link.write_memory(gm.OP_WR_B, 0, gm.matrix_to_bytes_int8(b))
    link.write_memory(gm.OP_WR_REF, 0, gm.matrix_to_bytes_int32(expected))
    return a, b, expected


def run_once(link: SpiLink, engine: int) -> dict:
    link.write_reg(gm.OP_WR_ENGINE, engine)
    link.write_reg(gm.OP_WR_TRIG, gm.TRIG_RUN)
    link.wait_idle()
    link.write_reg(gm.OP_WR_TRIG, gm.TRIG_VERIFY)
    status = link.wait_idle()
    perf = link.perf()
    perf["mismatch"] = bool(status & gm.ST_MISMATCH)
    perf["status"] = status
    return perf


def cmd_info(link: SpiLink, args) -> int:
    ident = link.identify()
    print(f"identification  0x{ident:08X}", end="")
    if ident == gm.CHIP_ID:
        print("  (expected)")
    else:
        print(f"  UNEXPECTED, this build expects 0x{gm.CHIP_ID:08X}")
        print("  Check wiring, the core clock, and that the SPI clock is <= f_core/8.")
        return 1
    cfg = link.config()
    print(f"geometry        {cfg['mat_m']}x{cfg['mat_n']}x{cfg['mat_k']}, "
          f"tile {cfg['tile_m']}x{cfg['tile_n']}x{cfg['tile_k']}, "
          f"INT{cfg['operand_w']} operands, INT{cfg['acc_w']} accumulators")
    print(f"candidates      {cfg['engine_count']}, currently selected "
          f"{cfg['engine_sel']}")
    status = link.status()
    print(f"status          0x{status:02X}")
    print(f"counters        {link.perf()}")
    return 0


def cmd_bench(link: SpiLink, args) -> int:
    cfg = link.config()
    load_workload(link, cfg, args.seed)
    perf = run_once(link, args.engine)
    name = gm.ENGINE_NAMES.get(args.engine, str(args.engine))
    verdict = "MISMATCH" if perf["mismatch"] else "correct"
    print(f"candidate {args.engine} ({name}): {verdict}, {perf['cycles']} cycles, "
          f"{perf['macs']} MACs")
    if perf["mismatch"]:
        idx = perf["first_mismatch"]
        print(f"  {perf['mismatch_count']} elements differ, first at row "
              f"{idx // cfg['mat_n']} column {idx % cfg['mat_n']}")
        return 1
    return 0


def cmd_sweep(link: SpiLink, args) -> int:
    cfg = link.config()
    load_workload(link, cfg, args.seed)
    print(f"{'cand':<4} {'name':<12} {'result':<10} {'cycles':>8} {'MACs':>9} "
          f"{'MACs/cycle':>11} {'time':>10}")
    failures = 0
    for engine in range(cfg["engine_count"]):
        # Zero the result store first, so a candidate that computes nothing cannot
        # pass on the previous candidate's result.
        link.write_reg(gm.OP_WR_TRIG, gm.TRIG_CLR_C)
        link.wait_idle()
        perf = run_once(link, engine)
        name = gm.ENGINE_NAMES.get(engine, str(engine))
        verdict = "MISMATCH" if perf["mismatch"] else "correct"
        failures += 1 if perf["mismatch"] else 0
        rate = perf["macs"] / perf["cycles"] if perf["cycles"] else 0.0
        wall = (f"{perf['cycles'] / args.clock_hz * 1e6:.1f} us"
                if args.clock_hz else "-")
        print(f"{engine:<4} {name:<12} {verdict:<10} {perf['cycles']:>8} "
              f"{perf['macs']:>9} {rate:>11.2f} {wall:>10}")
    if failures:
        print(f"\n{failures} candidate(s) produced a wrong result")
    return 1 if failures else 0


def cmd_read_c(link: SpiLink, args) -> int:
    cfg = link.config()
    nbytes = cfg["mat_m"] * cfg["mat_n"] * (cfg["acc_w"] // 8)
    data = link.read_memory(gm.OP_RD_C, 0, nbytes)
    matrix = gm.bytes_to_matrix_int32(data, cfg["mat_m"], cfg["mat_n"])
    if args.out:
        np.save(args.out, matrix)
        print(f"wrote {args.out}")
    else:
        print(matrix)
    return 0


def cmd_reset(link: SpiLink, args) -> int:
    link.write_reg(gm.OP_SOFT_RST, gm.SOFT_RST_KEY)
    status = link.status()
    print(f"soft reset issued, status now 0x{status:02X}")
    return 0 if status == 0x00 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="This driver has never been run against silicon: nothing has been "
               "fabricated.",
    )
    parser.add_argument("--bus", type=int, default=0)
    parser.add_argument("--device", type=int, default=0)
    parser.add_argument("--speed-hz", type=float, default=1e6,
                        help="SPI clock; must be <= f_core/8")
    parser.add_argument("--clock-hz", type=float, default=0.0,
                        help="core clock, used only to convert cycles to time")
    parser.add_argument("--seed", type=int, default=20260725)

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("info", help="identify the chip and report its geometry")
    bench = sub.add_parser("bench", help="run one candidate and check it on chip")
    bench.add_argument("--engine", type=int, default=0)
    sub.add_parser("sweep", help="run every candidate and print a PPA table")
    read_c = sub.add_parser("read-c", help="read the result matrix back")
    read_c.add_argument("--out", type=pathlib.Path)
    sub.add_parser("reset", help="issue a keyed soft reset")

    args = parser.parse_args(argv)

    link = SpiLink(args.bus, args.device, int(args.speed_hz))
    try:
        handler = {
            "info": cmd_info,
            "bench": cmd_bench,
            "sweep": cmd_sweep,
            "read-c": cmd_read_c,
            "reset": cmd_reset,
        }[args.command]
        return handler(link, args)
    finally:
        link.close()


if __name__ == "__main__":
    raise SystemExit(main())
