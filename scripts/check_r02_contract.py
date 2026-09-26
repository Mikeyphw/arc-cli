#!/usr/bin/env python3
"""Small deterministic contract check for ARC-R02 surfaces."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arc_cli.completion import COMMAND_OPTIONS, completion_mode, zsh_completion  # noqa: E402
from arc_cli.progress import ProgressReporter  # noqa: E402

errors: list[str] = []

if "--level" not in COMMAND_OPTIONS["create"] or "--level" in COMMAND_OPTIONS["extract"]:
    errors.append("completion option grammar is not command-specific")
if completion_mode(["create", "out.zip", ""]) != "multi":
    errors.append("create input completion is not multi-select")
if completion_mode(["create", "out.zip", "--backend", ""]) != "single":
    errors.append("backend completion is not single-select")

zsh = zsh_completion()
for token in ("__complete0", "__complete-mode", "--read0", "--print0"):
    if token not in zsh:
        errors.append(f"generated zsh completion missing {token}")
if "fzf --multi" in zsh:
    errors.append("generated completion contains unconditional fzf --multi")

if ProgressReporter("x", 0, 0, False).kind != "indeterminate":
    errors.append("unknown progress totals are not indeterminate")

committed = (ROOT / "completions" / "_arc").read_text(encoding="utf-8")
if committed != zsh:
    errors.append("committed completions/_arc differs from generator")

readme = (ROOT / "README.md").read_text(encoding="utf-8")
for phrase in ("[profiles.backup]", "--password-env", "--null", "--read0"):
    if phrase not in readme and phrase not in zsh:
        errors.append(f"R02 documentation/contract marker missing: {phrase}")

if errors:
    for error in errors:
        print(f"ARC-R02 contract: FAIL: {error}", file=sys.stderr)
    raise SystemExit(1)
print("ARC-R02 contract: pass")
