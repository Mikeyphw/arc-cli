#!/usr/bin/env python3
"""Verify that arc-cli uses the host Termux Ruff executable, not a venv module."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

ruff = shutil.which("ruff")
if not ruff:
    print("system ruff: FAIL: 'ruff' not found on PATH", file=sys.stderr)
    print("Install the Termux package with: pkg install ruff", file=sys.stderr)
    raise SystemExit(1)

try:
    proc = subprocess.run([ruff, "--version"], text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, timeout=20, check=False)
except OSError as exc:
    print(f"system ruff: FAIL: cannot execute {ruff}: {exc}", file=sys.stderr)
    raise SystemExit(1)

if proc.returncode:
    print(proc.stdout, end="", file=sys.stderr)
    print(f"system ruff: FAIL: {ruff} --version exited {proc.returncode}", file=sys.stderr)
    raise SystemExit(proc.returncode)

prefix = os.environ.get("PREFIX", "")
if prefix:
    try:
        Path(ruff).resolve().relative_to(Path(prefix).resolve())
    except ValueError:
        print(f"system ruff: warning: resolved outside PREFIX ({ruff})", file=sys.stderr)

version = proc.stdout.strip() or "ruff (version unavailable)"
print(f"system ruff: pass: {ruff} ({version})")
