#!/usr/bin/env python3
"""Verify the committed Zsh completion matches the generator exactly."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arc_cli.completion import zsh_completion  # noqa: E402

committed = (ROOT / "completions" / "_arc").read_text(encoding="utf-8")
generated = zsh_completion()
if committed != generated:
    print("completions/_arc is stale; regenerate with: arc completion zsh > completions/_arc", file=sys.stderr)
    raise SystemExit(1)
print("completion contract: pass")
