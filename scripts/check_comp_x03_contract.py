#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKS = {
    "volume": ("src/arc_cli/volume.py", "arc.split-manifest/v1"),
    "cli-split": ("src/arc_cli/cli.py", 'sub.add_parser("split"'),
    "cli-join": ("src/arc_cli/cli.py", 'sub.add_parser("join"'),
    "schema": ("src/arc_cli/schemas/split-manifest-v1.schema.json", "arc.split-manifest/v1"),
    "tests": ("tests/test_comp_x03_volume.py", "test_split_size_manifest_and_join_round_trip"),
    "audit": ("docs/ARC-COMP-X03-AUDIT.md", "Status: **IMPLEMENTED; COMP-G1 qualification pending.**"),
    "roadmap": ("docs/ARC-NEXT-ROADMAP.md", "COMP-X03 — exact split + join volume protocol"),
    "wrapper": ("docs/WRAPPER.md", "comp-x03"),
    "workflow": (".devtool.toml", "comp_x03"),
    "aliases": ("src/arc_cli/command_docs.py", '"arcsplit", "arc-split"'),
}
for label, (rel, marker) in CHECKS.items():
    path = ROOT / rel
    if not path.is_file():
        raise SystemExit(f"COMP-X03 contract missing {label}: {rel}")
    text = path.read_text(encoding="utf-8")
    if marker not in text:
        raise SystemExit(f"COMP-X03 contract missing marker for {label}: {marker!r}")
print("COMP-X03 contract: PASS")
