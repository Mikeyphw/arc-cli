#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arc_cli.machine import load_schema


def fail(message: str) -> None:
    raise SystemExit(f"r12a contract: FAIL: {message}")


def main() -> int:
    advisory = (ROOT / "src/arc_cli/advisory.py").read_text(encoding="utf-8")
    for needle in (
        "normalize_link_target",
        "normalize_member_name",
        "cwd=source.parent",
        "source.name or",
        "benchmark corpus must be a regular file or directory, not a symlink",
    ):
        if needle not in advisory:
            fail(f"benchmark/recommendation truth marker missing: {needle}")

    schema = load_schema("benchmark-v1")
    corpus = schema.get("properties", {}).get("corpus", {})
    required = set(corpus.get("required", []))
    if {"directories", "symlinks"} - required:
        fail("benchmark corpus schema must require directory/symlink counts")
    if corpus.get("properties", {}).get("files", {}).get("minimum") != 0:
        fail("benchmark schema must allow an empty-directory corpus with zero regular files")

    config = tomllib.loads((ROOT / ".devtool.toml").read_text(encoding="utf-8"))
    if "r12a" not in config.get("test_profiles", {}):
        fail("missing r12a test profile")
    tests = config.get("test", [])
    if not any(isinstance(item, dict) and item.get("id") == "arc-r12a-benchmark-corpus-truth" for item in tests):
        fail("missing first-class R12A test")
    jobs = config.get("targets", {}).get("arc", {}).get("jobs", {})
    if list(jobs.get("r12a-contract", {}).get("command", []) or []) != ["python3", "scripts/check_r12a_contract.py"]:
        fail("missing canonical r12a-contract job")
    workflow = config.get("targets", {}).get("arc", {}).get("workflows", {}).get("r12a", [])
    refs = {step.get("ref") for step in workflow if isinstance(step, dict)}
    required_refs = {
        "job:machine-contract", "job:r12-contract", "job:r12a-contract",
        "job:command-docs-contract", "job:completion-contract", "job:devtool-contract",
        "test:arc-r12a-benchmark-corpus-truth",
    }
    if not required_refs <= refs:
        fail("r12a workflow missing required first-class refs")
    wrapper = config.get("wrapper", {}).get("commands", {}).get("r12a", {})
    if wrapper.get("workflow") != "r12a":
        fail("wrapper r12a command must execute the r12a workflow")

    roadmap = (ROOT / "docs/ARC-NEXT-ROADMAP.md").read_text(encoding="utf-8").lower()
    for phrase in ("r12a", "empty directories", "symlink targets", "portable corpus root"):
        if phrase not in roadmap:
            fail(f"roadmap missing R12A promise: {phrase}")
    json.dumps(schema, sort_keys=True)
    print("r12a contract: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
