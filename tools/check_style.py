#!/usr/bin/env python3
# Copyright 2026 Daniel Tyukov
# SPDX-License-Identifier: Apache-2.0
"""Enforce the line length in .editorconfig across the sources.

A style rule nobody checks is a suggestion. This is the check, and `make style` runs
it. 96 columns rather than 80: the comments in this repository carry a lot of
reasoning, and 80 was cramping it into telegraphese.

Trailing whitespace and missing final newlines are checked too, for the same reason.
"""

from __future__ import annotations

import pathlib
import sys

LIMIT = 96
REPO = pathlib.Path(__file__).resolve().parent.parent
TARGETS = [
    ("rtl", ("*.sv",)),
    ("tb", ("*.sv", "*.py")),
    ("tools", ("*.py",)),
    ("flow", ("*.tcl",)),
    ("constraints", ("*.sdc",)),
]


def main() -> int:
    problems: list[str] = []
    checked = 0
    for root, patterns in TARGETS:
        for pattern in patterns:
            for path in sorted((REPO / root).rglob(pattern)):
                checked += 1
                text = path.read_text()
                rel = path.relative_to(REPO)
                if text and not text.endswith("\n"):
                    problems.append(f"{rel}: no final newline")
                for n, line in enumerate(text.splitlines(), 1):
                    if len(line) > LIMIT:
                        problems.append(f"{rel}:{n}: {len(line)} columns, limit {LIMIT}")
                    if line != line.rstrip():
                        problems.append(f"{rel}:{n}: trailing whitespace")

    for problem in problems:
        print(problem)
    print(f"\nchecked {checked} files, {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
