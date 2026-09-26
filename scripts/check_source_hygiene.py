#!/usr/bin/env python3
"""Dependency-free source hygiene checks for arc-cli.

This deliberately does not import arc_cli or any third-party package so the
Devtool target-local job can run with the Termux host Python before/alongside
project environment restoration.
"""
from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOTS = (ROOT / "src", ROOT / "tests", ROOT / "scripts")
BACKUP_SUFFIXES = ("~", ".bak", ".orig", ".rej")


def relative(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def main() -> int:
    failures: list[str] = []
    checked = 0

    for base in SCAN_ROOTS:
        if not base.exists():
            continue
        for path in sorted(p for p in base.rglob("*") if p.is_file()):
            name = path.name
            if name.endswith(BACKUP_SUFFIXES):
                failures.append(f"backup/reject artifact present: {relative(path)}")
                continue
            if path.suffix != ".py":
                continue

            checked += 1
            try:
                raw = path.read_bytes()
            except OSError as exc:
                failures.append(f"cannot read {relative(path)}: {exc}")
                continue

            if b"\x00" in raw:
                failures.append(f"NUL byte in Python source: {relative(path)}")
                continue

            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                failures.append(f"non-UTF-8 Python source {relative(path)}: {exc}")
                continue

            try:
                ast.parse(text, filename=str(path))
            except SyntaxError as exc:
                failures.append(
                    f"syntax error in {relative(path)}:{exc.lineno or 0}: {exc.msg}"
                )

    if failures:
        for failure in failures:
            print(f"source hygiene: FAIL: {failure}", file=sys.stderr)
        return 1

    print(f"source hygiene: pass ({checked} Python files parsed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
