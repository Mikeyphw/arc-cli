#!/usr/bin/env python3
"""Deterministic non-test contract for ARC-R09A provenance/diff surfaces."""
from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arc_cli.command_docs import COMMAND_DOCS, console_script_mapping
from arc_cli.completion import COMMAND_OPTIONS
from arc_cli.machine import load_schema, schema_names
from arc_cli.provenance import DIFF_SCHEMA, FINGERPRINT_NORMALIZATION, FINGERPRINT_SCHEMA, normalize_member_name


def fail(message: str) -> None:
    raise SystemExit(f"provenance contract: FAIL: {message}")


def main() -> int:
    required_schemas = {"logical-fingerprint-v1", "archive-diff-v1"}
    if not required_schemas <= set(schema_names()):
        fail("provenance schemas are missing from the public registry")
    fp = load_schema("logical-fingerprint-v1")
    diff = load_schema("archive-diff-v1")
    if fp.get("$id") != FINGERPRINT_SCHEMA:
        fail("logical fingerprint schema identity drift")
    if diff.get("$id") != DIFF_SCHEMA:
        fail("archive diff schema identity drift")
    if FINGERPRINT_NORMALIZATION != "arc-logical-members-v1":
        fail("logical normalization identity drift")
    if normalize_member_name("./folder\\caf\u0065\u0301.txt") != "folder/caf\u00e9.txt":
        fail("path normalization contract drift")
    json.dumps(fp, sort_keys=True)
    json.dumps(diff, sort_keys=True)

    doc = COMMAND_DOCS.get("diff")
    if doc is None or set(doc.aliases) != {"arcdiff", "arc-diff"}:
        fail("diff command/alias registry drift")
    scripts = console_script_mapping()
    for executable in ("arc", "arcdiff", "arc-diff"):
        if scripts.get(executable) != "arc_cli.cli:main":
            fail(f"console script missing: {executable}")

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    packaged = project["project"]["scripts"]
    for executable in ("arcdiff", "arc-diff"):
        if packaged.get(executable) != "arc_cli.cli:main":
            fail(f"pyproject console script missing: {executable}")
    man_files = project["tool"]["setuptools"]["data-files"]["share/man/man1"]
    if "src/arc_cli/man/arc-diff.1" not in man_files:
        fail("arc-diff(1) is not installed as package data")

    reference = (ROOT / "docs" / "COMMAND_REFERENCE.md").read_text(encoding="utf-8")
    if "--prove-equivalent" not in COMMAND_OPTIONS.get("convert", set()):
        fail("convert completion is missing --prove-equivalent")

    for token in ("arc diff", "logical", "metadata", "byte", "--prove-equivalent"):
        if token not in reference:
            fail(f"generated command reference missing {token!r}")

    print("provenance contract: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
