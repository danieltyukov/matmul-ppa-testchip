# Contributing

The most useful contribution to this repository is a new candidate
microarchitecture. That path is documented end to end in
[docs/ADDING_A_CANDIDATE.md](docs/ADDING_A_CANDIDATE.md) and is designed to touch
five files.

## Getting set up

```bash
git clone https://github.com/danieltyukov/matmul-ppa-testchip.git
cd matmul-ppa-testchip
make venv
make check-tools     # reports what is present and what is missing
```

Needed: Verilator 5.020 or newer, Icarus Verilog 12.0 or newer, Yosys 0.33 or newer,
Python 3.12. Optional: KLayout for GDS rendering, OpenROAD and the IHP SG13G2 PDK
for place and route.

## Before opening a pull request

```bash
make lint     # must be zero warnings, not zero errors
make sim      # the full suite
make synth    # per candidate and for the chip
make power    # switching activity, if you changed a datapath
make images   # if you changed anything the figures are drawn from
```

If your change affects measurement, commit the regenerated `results/` alongside it.
The numbers in the README come from those files, and a change that moves them
without updating them makes the documentation wrong.

## What gets a change rejected

- **A lint warning.** `-Wall` clean is not negotiable. Fix the cause; do not waive it.
- **A candidate that is not bit-exact.** Approximate arithmetic is interesting but
  needs a different harness. The equivalence tests will reject it, correctly.
- **A measurement without a stated methodology.** If you add a metric, add the
  paragraph in `docs/PPA_METHODOLOGY.md` that says what it does and does not mean.
- **A figure that is not generated from committed data.** Every image in `docs/img/`
  is produced by a script in `tools/` from a file in `results/`. A hand-drawn or
  hand-edited image cannot be regenerated and will go stale.
- **A number presented as more than it is.** Yosys cell area is not PDK area. PDK
  cell area is not die area. A transition count is not power. A synthesis estimate is
  not a layout. This repository is careful about that distinction and a change that
  blurs it will not be merged.

## Code style

### RTL

- SystemVerilog, synthesisable subset, `always_ff` and `always_comb`.
- Flat vectors, not packed multi-dimensional arrays, because Yosys 0.33 rejects
  them. See the portability table in
  [docs/ADDING_A_CANDIDATE.md](docs/ADDING_A_CANDIDATE.md#portability-notes-learned-the-hard-way).
- `_i` and `_o` port suffixes, `_q` for registers, `_d` for next-state.
- One module per file, named after the file.
- Comments explain why, not what. A comment that restates the code is noise; a
  comment that records why a construct is written awkwardly saves the next person an
  afternoon.

### Python

- Standard library plus NumPy, matplotlib and cocotb. No other dependencies.
- Type hints on function signatures.
- Tools fail loudly with an actionable message when their input is missing. Never
  invent placeholder data.

### Commit messages

Conventional Commit prefixes: `feat:`, `fix:`, `test:`, `docs:`, `chore:`, `ci:`.
Say what changed and why, and if a change was forced by a tool limitation, say which
tool.

## Reporting a problem

A useful bug report includes the tool versions from `make check-tools`, the exact
command, and the failing assertion. Every assertion in the suite is written to name
the candidate, the element and both values, so pasting it usually localises the
problem immediately.

## Licence

Apache-2.0. By contributing you agree your contribution is licensed under it.
