# Memory map and SPI command set

![Memory map and command set](img/memory_map.svg)

Everything the host can do to this chip is in this document. The constants live in
`rtl/pkg/gemm_pkg.sv`, are mirrored in `tb/gemm_model.py`, and
`tb/test_spi_protocol.py` asserts them against the chip, so all three cannot drift
apart without a test failing.

---

## Physical layer

| Property | Value |
|---|---|
| Mode | SPI Mode 0: CPOL = 0, CPHA = 0 |
| Bit order | MSB first |
| Word size | 8 bits |
| Chip select | active low; a frame is the interval it is asserted |
| Maximum clock | `f_spi <= f_core / 8` |
| Recommended clock | `f_core / 16` |
| MISO | driven only while chip select is asserted, so several targets can share a bus |

The chip oversamples its SPI pins in the core clock domain rather than clocking on
them, which is where the frequency ratio limit comes from. See
[ARCHITECTURE.md](ARCHITECTURE.md#host-interface).

There is no length field in the protocol. A frame is as long as the controller keeps
chip select low, which makes truncation a first-class case rather than an accident:
it is detected and reported.

---

## Frame layouts

Addresses are big endian on the wire. INT32 values in the stores are little endian.

```
memory opcode     [opcode][addr hi][addr lo][data 0][data 1] ...
register write    [opcode][value]
register read     [opcode][dummy][dummy] ...
```

Memory frames auto-increment the byte address, so a whole matrix is one frame and a
partial update is a shorter frame at an offset.

### What comes back on MISO

The chip answers the byte *before* the one currently on the wire: one byte of
command latency, which is what any SPI controller expects.

| Frame | MISO byte 0 | byte 1 | byte 2 | byte 3 | ... |
|---|---|---|---|---|---|
| register read | `0x00` | payload[0] | payload[1] | payload[2] | ... |
| memory read | `0x00` | `0x00` | `0x00` | data[0] | data[1] ... |

Byte 0 is always `0x00` because at that point the chip has not decoded an opcode
yet. For memory reads two more filler bytes cover the address phase.

`tb/spi_driver.py` implements exactly this: `read_reg` drops one byte, `read_memory`
drops three.

---

## Opcodes

Bit 7 selects direction: 0 means the host writes, 1 means the chip answers.

| Opcode | Name | Payload | Effect |
|---|---|---|---|
| `0x00` | `OP_NOP` | none | accepted, does nothing, sets no flag |
| `0x01` | `OP_WR_A` | addr + up to 1024 B | write operand store A |
| `0x02` | `OP_WR_B` | addr + up to 1024 B | write operand store B |
| `0x03` | `OP_WR_REF` | addr + up to 4096 B | write the reference store |
| `0x08` | `OP_WR_ENGINE` | 1 B | select candidate 0 to `ENGINE_COUNT-1` |
| `0x09` | `OP_WR_TRIG` | 1 B | trigger bits, see below |
| `0x0F` | `OP_SOFT_RST` | 1 B = `0x5A` | soft reset the datapath |
| `0x81` | `OP_RD_ID` | 4 B out | `0x4D500102`, ASCII "MP" then 1.2 |
| `0x82` | `OP_RD_STATUS` | 1 B out | status byte, see below |
| `0x83` | `OP_RD_PERF` | 12 B out | performance counters, see below |
| `0x84` | `OP_RD_C` | addr + up to 4096 B out | read the result store |
| `0x85` | `OP_RD_A` | addr + up to 1024 B out | read operand store A back |
| `0x86` | `OP_RD_B` | addr + up to 1024 B out | read operand store B back |
| `0x87` | `OP_RD_CFG` | 10 B out | geometry discovery, see below |
| `0x88` | `OP_RD_REF` | addr + up to 4096 B out | read the reference store back |

Any other opcode sets the sticky command error bit; the rest of the frame is
ignored. `test_spi_protocol.test_unknown_opcodes` checks eleven of them.

---

## Address spaces

The byte address of a matrix element is its plain row-major index.

| Store | Opcodes | Bytes | Element at byte address |
|---|---|---|---|
| A | `OP_WR_A`, `OP_RD_A` | 0 .. 1023 | `A[m][k]` at `m*32 + k` |
| B | `OP_WR_B`, `OP_RD_B` | 0 .. 1023 | `B[k][n]` at `k*32 + n` |
| C | `OP_RD_C` | 0 .. 4095 | `C[m][n]` at `(m*32 + n)*4`, little endian |
| REF | `OP_WR_REF`, `OP_RD_REF` | 0 .. 4095 | same as C |

Store C is read-only from the host: it is written by the sequencer.

An address at or past the end of the target store is refused and sets the command
error bit. The one prefetch that runs a single byte past the end at the close of a
full-length read is suppressed silently, because that is how a legal full-length
read finishes; `test_spi_protocol.test_address_out_of_range` checks both halves.

---

## `OP_WR_TRIG` bits

| Bit | Name | Effect | Refused while busy |
|---|---|---|---|
| 0 | `TRIG_RUN` | start the tiled GEMM; also clears both counters | yes |
| 1 | `TRIG_CLR_C` | zero the whole result store | yes |
| 2 | `TRIG_VERIFY` | start the on-chip comparator | yes |
| 3 | `TRIG_CLR_PERF` | zero the cycle and MAC counters | no |
| 4 | `TRIG_CLR_STICKY` | clear done, verify done, mismatch, command error, frame error | no |

Bits can be combined in one byte. A refused bit sets the command error flag and the
operation in flight is not disturbed.

`TRIG_RUN` clearing the counters is deliberate: a readback after two runs then
reflects the second run rather than their sum, which is what a benchmark wants.
`TRIG_CLR_PERF` remains for explicit control.

---

## `OP_RD_STATUS`

| Bit | Name | Meaning | Sticky |
|---|---|---|---|
| 0 | `ST_BUSY` | sequencer or checker running | no |
| 1 | `ST_DONE` | last run completed | until a new run or `TRIG_CLR_STICKY` |
| 2 | `ST_VFY_BUSY` | comparator running | no |
| 3 | `ST_VFY_DONE` | last verify completed | until a new verify or `TRIG_CLR_STICKY` |
| 4 | `ST_MISMATCH` | comparator found at least one difference | until a new verify or `TRIG_CLR_STICKY` |
| 5 | `ST_CMD_ERR` | unknown opcode, out-of-range address, bad engine index, or a trigger refused while busy | yes |
| 6 | `ST_FRAME_ERR` | a frame ended while required bytes were still outstanding | yes |
| 7 | reserved | reads 0 | |

`0x00` after reset. `test_reset_gating.test_state_after_hard_reset` asserts that,
and also that the four status pads agree with the byte.

---

## `OP_RD_PERF`, 12 bytes

| Bytes | Field | Notes |
|---|---|---|
| 0 .. 3 | cycle count | core cycles the sequencer was busy, little endian, saturating |
| 4 .. 7 | MAC count | MACs retired, little endian, saturating |
| 8 .. 9 | mismatch count | output elements that differed, saturating at 65535 |
| 10 .. 11 | first mismatch | row-major index of the first differing element |

Both counters are readable while the chip is busy, so a controller can watch a run
progress.

For the default geometry the MAC count after a complete run is always 32768,
regardless of candidate. That is what makes cycles per MAC a fair comparison.

---

## `OP_RD_CFG`, 10 bytes

| Byte | Field | Default |
|---|---|---|
| 0 | `MAT_M` | 32 |
| 1 | `MAT_N` | 32 |
| 2 | `MAT_K` | 32 |
| 3 | `TILE_M` | 4 |
| 4 | `TILE_N` | 4 |
| 5 | `TILE_K` | 4 |
| 6 | `OPERAND_W` | 8 |
| 7 | `ACC_W` | 32 |
| 8 | `ENGINE_COUNT` | 5 |
| 9 | currently selected engine | 0 after reset |

This exists so host tooling can size its transfers from the chip rather than from a
build-time constant. A fork that changes `MAT_*` or `TILE_*` does not need to change
its host software, which is the difference between a template and a one-off.

---

## Typical sequences

### Run a benchmark and check the answer on chip

```
OP_WR_A     addr 0x0000, 1024 bytes
OP_WR_B     addr 0x0000, 1024 bytes
OP_WR_REF   addr 0x0000, 4096 bytes    (the expected product)
OP_WR_ENGINE 0x01                      (candidate 1)
OP_WR_TRIG  0x01                       (TRIG_RUN)
   poll OP_RD_STATUS until ST_BUSY clears
OP_WR_TRIG  0x04                       (TRIG_VERIFY)
   poll OP_RD_STATUS until ST_VFY_BUSY clears
OP_RD_STATUS                           (ST_MISMATCH must be clear)
OP_RD_PERF                             (cycles and MACs for this candidate)
```

### Sweep every candidate on one workload

Load A, B and REF once, then for each candidate: `TRIG_CLR_C`, `OP_WR_ENGINE`,
`TRIG_RUN`, `TRIG_VERIFY`, `OP_RD_PERF`. Clearing C first means a candidate that
computes nothing cannot pass on the previous candidate's result.
`tb/test_end_to_end.py::test_every_candidate_end_to_end` does exactly this, and
asserts that the clear really does make the comparator fail, so the sweep proves
something.

### Read the whole result out

```
OP_RD_C  addr 0x0000, 4096 dummy bytes
```

Slower than the on-chip comparator by three orders of magnitude at any realistic SPI
clock, which is why the comparator exists.
