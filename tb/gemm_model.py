# Copyright 2026 Daniel Tyukov
# SPDX-License-Identifier: Apache-2.0
"""Reference model and shared helpers for the matmul PPA test chip testbenches.

The golden reference is NumPy with an explicit INT32 accumulator. Nothing in this
file mirrors RTL structure: it computes what the answer should be, independently,
so that a shared misunderstanding cannot make a test pass.
"""

from __future__ import annotations

import numpy as np

# ---------------------------------------------------------------------------
# Configuration. Must match rtl/pkg/gemm_pkg.sv. test_config.py asserts that it
# does by reading the geometry back out of the chip over SPI.
# ---------------------------------------------------------------------------
OPERAND_W = 8
ACC_W = 32
ACC_BYTES = ACC_W // 8

MAT_M = 32
MAT_N = 32
MAT_K = 32

TILE_M = 4
TILE_N = 4
TILE_K = 4

GRID_M = MAT_M // TILE_M
GRID_N = MAT_N // TILE_N
GRID_K = MAT_K // TILE_K

ENGINE_COUNT = 5

ENG_INFER = 0
ENG_WALLACE = 1
ENG_BOOTH4 = 2
ENG_SIGNMAG = 3
ENG_BITSERIAL = 4

# The candidate with the longest launch-to-valid latency. Tests that drive every
# candidate at once wait on this one, because when it is valid all of them are.
ENG_SLOWEST = ENG_BITSERIAL

ENGINE_NAMES = {
    0: "infer",
    1: "wallace",
    2: "booth4",
    3: "signmag",
    4: "bitserial",
}

# Launch-to-valid latency of each candidate, in core clock cycles. These are
# design properties stated in each engine's header comment; test_perf_counters.py
# measures them at the engine harness level before using them, so the analytic
# cycle count below is never checked against an unverified assumption.
ENGINE_LATENCY = {
    0: 1,
    1: 1,
    2: 1,
    3: 1,
    4: OPERAND_W,
}

# ---------------------------------------------------------------------------
# SPI opcodes. Must match rtl/pkg/gemm_pkg.sv.
# ---------------------------------------------------------------------------
OP_NOP = 0x00
OP_WR_A = 0x01
OP_WR_B = 0x02
OP_WR_REF = 0x03
OP_WR_ENGINE = 0x08
OP_WR_TRIG = 0x09
OP_SOFT_RST = 0x0F
OP_RD_ID = 0x81
OP_RD_STATUS = 0x82
OP_RD_PERF = 0x83
OP_RD_C = 0x84
OP_RD_A = 0x85
OP_RD_B = 0x86
OP_RD_CFG = 0x87
OP_RD_REF = 0x88

SOFT_RST_KEY = 0x5A
CHIP_ID = 0x4D500102

TRIG_RUN = 1 << 0
TRIG_CLR_C = 1 << 1
TRIG_VERIFY = 1 << 2
TRIG_CLR_PERF = 1 << 3
TRIG_CLR_STICKY = 1 << 4

ST_BUSY = 1 << 0
ST_DONE = 1 << 1
ST_VFY_BUSY = 1 << 2
ST_VFY_DONE = 1 << 3
ST_MISMATCH = 1 << 4
ST_CMD_ERR = 1 << 5
ST_FRAME_ERR = 1 << 6

A_BYTES = MAT_M * MAT_K
B_BYTES = MAT_K * MAT_N
C_BYTES = MAT_M * MAT_N * ACC_BYTES


# ---------------------------------------------------------------------------
# Reference arithmetic
# ---------------------------------------------------------------------------
def matmul_ref(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """INT8 x INT8 -> INT32 matrix product, accumulated in int64 then wrapped.

    Accumulating in int64 and wrapping at the end makes the wrap explicit rather
    than relying on NumPy overflow behaviour. For the supported geometries the
    exact sum always fits in INT32, so no wrapping actually happens; the test
    that checks that is test_engine_exact.test_accumulator_headroom.
    """
    assert a.dtype == np.int8 and b.dtype == np.int8
    acc = a.astype(np.int64) @ b.astype(np.int64)
    return wrap_int32(acc)


def wrap_int32(x: np.ndarray) -> np.ndarray:
    return ((x + (1 << 31)) % (1 << 32) - (1 << 31)).astype(np.int64)


def to_signed(value: int, width: int) -> int:
    value &= (1 << width) - 1
    return value - (1 << width) if value >> (width - 1) else value


def to_unsigned(value: int, width: int) -> int:
    return value & ((1 << width) - 1)


# ---------------------------------------------------------------------------
# Tile packing
#
# A flattened R x C tile packs element (r, c) at bit offset ((r*C)+c)*ELEM_W with
# element (0, 0) in the least significant bits. That is the one layout rule the
# whole repo follows.
# ---------------------------------------------------------------------------
def pack_tile(tile: np.ndarray, elem_w: int = OPERAND_W) -> int:
    rows, cols = tile.shape
    word = 0
    for r in range(rows):
        for c in range(cols):
            word |= to_unsigned(int(tile[r, c]), elem_w) << (((r * cols) + c) * elem_w)
    return word


def unpack_tile(word: int, rows: int, cols: int, elem_w: int) -> np.ndarray:
    out = np.zeros((rows, cols), dtype=np.int64)
    for r in range(rows):
        for c in range(cols):
            raw = (word >> (((r * cols) + c) * elem_w)) & ((1 << elem_w) - 1)
            out[r, c] = to_signed(raw, elem_w)
    return out


# ---------------------------------------------------------------------------
# Host byte streams. The host byte address of a matrix element is its plain
# row-major index (times ACC_BYTES for INT32 matrices), little endian within an
# accumulator.
# ---------------------------------------------------------------------------
def matrix_to_bytes_int8(m: np.ndarray) -> bytes:
    return bytes(to_unsigned(int(v), 8) for v in m.reshape(-1))


def matrix_to_bytes_int32(m: np.ndarray) -> bytes:
    out = bytearray()
    for v in m.reshape(-1):
        out += to_unsigned(int(v), 32).to_bytes(ACC_BYTES, "little")
    return bytes(out)


def bytes_to_matrix_int32(data: bytes, rows: int, cols: int) -> np.ndarray:
    assert len(data) == rows * cols * ACC_BYTES, (
        f"expected {rows * cols * ACC_BYTES} bytes, got {len(data)}"
    )
    flat = [
        to_signed(int.from_bytes(data[i : i + ACC_BYTES], "little"), 32)
        for i in range(0, len(data), ACC_BYTES)
    ]
    return np.array(flat, dtype=np.int64).reshape(rows, cols)


# ---------------------------------------------------------------------------
# Analytic cycle cost of one full GEMM run, straight out of the sequencer's
# documented state machine. FETCH_LEN + 1 cycles of fetch, one launch cycle and
# L cycles of waiting per k tile; one accumulator clear and TILE_M write-back
# cycles per output tile.
# ---------------------------------------------------------------------------
def expected_run_cycles(engine: int) -> int:
    fetch_len = max(TILE_M, TILE_K)
    latency = ENGINE_LATENCY[engine]
    per_k_tile = (fetch_len + 1) + 1 + latency
    per_out_tile = 1 + GRID_K * per_k_tile + TILE_M
    return GRID_M * GRID_N * per_out_tile


def expected_mac_count() -> int:
    return MAT_M * MAT_N * MAT_K


# ---------------------------------------------------------------------------
# Stimulus generators
# ---------------------------------------------------------------------------
def random_int8(rng: np.random.Generator, shape) -> np.ndarray:
    return rng.integers(-128, 128, size=shape, dtype=np.int64).astype(np.int8)


def random_int8_biased(rng: np.random.Generator, shape, neg_fraction: float) -> np.ndarray:
    """INT8 matrix in which each element is negative with probability neg_fraction.

    Used by the switching-activity sweep: the sign-magnitude hypothesis predicts
    that activity in a two's complement datapath grows with the rate of sign
    changes, while a sign-magnitude datapath is much flatter.
    """
    magnitude = rng.integers(0, 128, size=shape, dtype=np.int64)
    negative = rng.random(size=shape) < neg_fraction
    values = np.where(negative, -magnitude, magnitude)
    # -128 is only reachable through the negative branch, so fold it in explicitly
    # rather than leaving the most awkward INT8 value untested.
    values = np.where(negative & (magnitude == 0), -128, values)
    return values.astype(np.int8)


def corner_tiles():
    """Operand pairs that exercise the awkward corners of INT8 arithmetic."""
    lo = np.full((TILE_M, TILE_K), -128, dtype=np.int8)
    hi = np.full((TILE_M, TILE_K), 127, dtype=np.int8)
    zero = np.zeros((TILE_M, TILE_K), dtype=np.int8)
    ones = np.full((TILE_M, TILE_K), 1, dtype=np.int8)
    neg_one = np.full((TILE_M, TILE_K), -1, dtype=np.int8)
    identity = np.eye(TILE_M, TILE_K, dtype=np.int8)
    # A rank-deficient pair: every row of A identical, every column of B identical.
    rank1_a = np.tile(np.arange(-2, TILE_K - 2, dtype=np.int8), (TILE_M, 1))
    rank1_b = np.tile(np.arange(-2, TILE_N - 2, dtype=np.int8).reshape(-1, 1), (1, TILE_N))
    return [
        ("zero_zero", zero, zero),
        ("min_min", lo, lo),
        ("min_max", lo, hi),
        ("max_max", hi, hi),
        ("min_one", lo, ones),
        ("min_neg_one", lo, neg_one),
        ("max_neg_one", hi, neg_one),
        ("identity_max", identity, hi),
        ("max_identity", hi, np.eye(TILE_K, TILE_N, dtype=np.int8)),
        ("rank_deficient", rank1_a, rank1_b),
        ("zero_min", zero, lo),
    ]
