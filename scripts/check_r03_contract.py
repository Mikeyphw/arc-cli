#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arc_cli.backends import backend_capabilities, backend_inventory
from arc_cli.cli import parser
from arc_cli.completion import COMMAND_OPTIONS
from arc_cli.qualification import (
    FAIL,
    PASS,
    SKIP_BACKEND,
    SKIP_CAPABILITY,
)

errors: list[str] = []

args = parser().parse_args(["create", "out.zip", "src", "--no-fallback"])
if not args.no_fallback:
    errors.append("--no-fallback parser contract missing")
if "--no-fallback" not in COMMAND_OPTIONS["create"]:
    errors.append("--no-fallback completion contract missing")

if "threads" not in backend_capabilities("7z") or "threads" in backend_capabilities("zip"):
    errors.append("thread capability registry is inconsistent")
if "password" not in backend_capabilities("unzip"):
    errors.append("password capability registry missing unzip")
if "remove" in backend_capabilities("bsdtar"):
    errors.append("bsdtar must not advertise normalized tar remove")

inventory = backend_inventory({})
if not inventory or not all("candidates" in row for row in inventory):
    errors.append("backend candidate inventory missing")

for marker in (PASS, FAIL, SKIP_BACKEND, SKIP_CAPABILITY):
    if not marker:
        errors.append("qualification status marker missing")

for path in (
    ROOT / "src" / "arc_cli" / "qualification.py",
    ROOT / "scripts" / "run_r03_qualification.py",
    ROOT / "docs" / "ARC-R03-AUDIT.md",
):
    if not path.is_file():
        errors.append(f"R03 artifact missing: {path.relative_to(ROOT)}")

readme = (ROOT / "README.md").read_text(encoding="utf-8")
for phrase in ("--no-fallback", "capability-matrix.json", "ARC-R04"):
    if phrase not in readme:
        errors.append(f"R03 README marker missing: {phrase}")

if errors:
    for error in errors:
        print(f"ARC-R03 contract: FAIL: {error}", file=sys.stderr)
    raise SystemExit(1)
print("ARC-R03 contract: pass")
