#!/usr/bin/env python3
# Copyright 2026 Daniel Tyukov
# SPDX-License-Identifier: Apache-2.0
"""Check that every relative link and image in the markdown resolves.

A README full of broken links is the most visible kind of rot, and it is the kind
nobody notices until someone else clicks. This walks every markdown file, extracts
every relative target, and reports the ones that do not exist.

Anchors within a file are checked too: a link to `docs/FOO.md#some-heading` fails if
that heading is not in that file. Heading slugs follow GitHub's rules closely enough
for the headings this repository uses.

External links are not fetched: that would make the check depend on the network and on
other people's uptime.

A target that exists on disk but is not committed is reported as a problem rather than
passed. CI checks out the commit, so an uncommitted figure resolves locally and 404s for
everyone else, which is exactly the rot this check exists to catch. Outside a git work
tree the check falls back to plain existence.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
HEADING_RE = re.compile(r"^#{1,6}\s+(.*?)\s*$", re.M)


def tracked_files() -> set[pathlib.Path] | None:
    """Every path git knows about, or None outside a work tree."""
    try:
        out = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z"],
                             capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return {REPO / p.decode() for p in out.split(b"\0") if p}


def slugify(heading: str) -> str:
    """GitHub's heading slug: lowercase, punctuation dropped, spaces to hyphens."""
    text = heading.strip().lower()
    text = re.sub(r"[`*_]", "", text)
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"\s+", "-", text)


def headings(path: pathlib.Path) -> set[str]:
    return {slugify(h) for h in HEADING_RE.findall(path.read_text())}


def main() -> int:
    problems: list[str] = []
    tracked = tracked_files()
    files = sorted(REPO.rglob("*.md"))
    files = [f for f in files if ".venv" not in f.parts and "build" not in f.parts]
    # Markdown that git ignores is generated output, and the run directories under
    # flow/librelane hold several of them. Checking those reported a different file
    # count locally than in CI, which made the two runs hard to compare.
    if tracked is not None:
        files = [f for f in files if f in tracked]
    checked = 0

    for path in files:
        rel = path.relative_to(REPO)
        for target in LINK_RE.findall(path.read_text()):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            checked += 1
            anchor = ""
            if "#" in target:
                target, anchor = target.split("#", 1)

            if target:
                resolved = (path.parent / target).resolve()
                if not resolved.exists():
                    problems.append(f"{rel}: missing target {target}")
                    continue
                if tracked is not None and resolved.is_file() and resolved not in tracked:
                    problems.append(f"{rel}: {target} exists but is not committed")
                    continue
            else:
                resolved = path

            if anchor and resolved.suffix == ".md":
                if anchor not in headings(resolved):
                    problems.append(
                        f"{rel}: {target or resolved.name} has no heading #{anchor}"
                    )

    for problem in problems:
        print(problem)
    print(f"\nchecked {checked} relative links in {len(files)} markdown files, "
          f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
