#!/usr/bin/env python3
"""Reject machine-specific paths and private IPv4 addresses in tracked text files."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_SUFFIXES = {".sog", ".spz", ".glb", ".png", ".jpg"}
IPV4_OCTET = r"(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)"
PATTERNS = {
    "macOS home path": re.compile("/" + "Users/"),
    "Linux home path": re.compile("/" + "home/"),
    "private IPv4 address": re.compile(
        rf"(?<![\d.])(?:10\.{IPV4_OCTET}|172\.(?:1[6-9]|2\d|3[01])|192\.168)"
        rf"\.{IPV4_OCTET}\.{IPV4_OCTET}(?![\d.])"
    ),
}


def tracked_files(root: Path = ROOT) -> list[Path]:
    """Return existing tracked files, preserving unusual names via NUL delimiters."""
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
    )
    return [root / Path(name.decode("utf-8")) for name in result.stdout.split(b"\0") if name]


def eligible(path: Path, root: Path = ROOT) -> bool:
    relative = path.relative_to(root)
    return relative.parts[:1] != ("scenes",) and relative.suffix.lower() not in SKIP_SUFFIXES


def text_content(path: Path) -> str | None:
    data = path.read_bytes()
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def findings(root: Path = ROOT) -> list[tuple[Path, int, str]]:
    problems = []
    for path in tracked_files(root):
        if not path.is_file() or not eligible(path, root):
            continue
        source = text_content(path)
        if source is None:
            continue
        for line_number, line in enumerate(source.splitlines(), 1):
            for label, pattern in PATTERNS.items():
                if pattern.search(line):
                    problems.append((path.relative_to(root), line_number, label))
    return problems


def main(argv=None) -> int:
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    try:
        problems = findings()
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"path hygiene scan failed: {exc}", file=sys.stderr)
        return 2
    for path, line_number, label in problems:
        print(f"{path}:{line_number}: forbidden {label}")
    if problems:
        print(f"Path hygiene: FAIL ({len(problems)} finding(s))")
        return 1
    print("Path hygiene: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
