#!/usr/bin/env python3
"""Dependency-free ARC-R01 promise checks.

Devtool owns executable pytest validation.  This script guards the R01 source
contract itself so artifact qualification can explain exactly which semantic
pieces are expected to exist without bootstrapping another project runtime.
"""
from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def text(rel: str) -> str:
    path = ROOT / rel
    if not path.is_file():
        raise SystemExit(f"ARC-R01 contract: missing {rel}")
    value = path.read_text(encoding="utf-8")
    ast.parse(value, filename=str(path)) if path.suffix == ".py" else None
    return value


def main() -> int:
    cli = text("src/arc_cli/cli.py")
    backends = text("src/arc_cli/backends.py")
    filtering = text("src/arc_cli/filtering.py")
    formats = text("src/arc_cli/formats.py")
    tests = text("tests/test_r01_semantics.py")

    checks = {
        "shared member filtering": "def _select_archive_members" in cli and "def filter_members" in filtering,
        "empty selection guard": "selection_requested and not selected_members" in cli,
        "stream conflict model": "def _stream_output_name" in cli and "fmt.is_stream and not args.stdout" in cli,
        "dry-run manifest suppression": "dry_run: bool = False" in backends and 'Path("<arc-manifest>")' in backends,
        "list native passthrough": 'meta["forward_output"] = True' in cli,
        "add/update separation": 'if operation == "add":' in cli and "use update for replacement semantics" in cli,
        "stream/tar ambiguity": "return tf.next() is not None" in formats and "def compressed_kind" in formats,
        "password classification": "PasswordError" in backends and "password_markers" in cli,
        "regression suite": "test_dry_run_rename_existing_is_zero_write" in tests and "test_add_conflicts_but_update_replaces" in tests,
    }
    failed = [name for name, ok in checks.items() if not ok]
    for name, ok in checks.items():
        print(f"ARC-R01 contract: {'PASS' if ok else 'FAIL'}: {name}")
    if failed:
        print("ARC-R01 contract failures: " + ", ".join(failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
