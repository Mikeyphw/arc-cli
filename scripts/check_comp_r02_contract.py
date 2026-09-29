#!/usr/bin/env python3
"""Fail closed when COMP-R02 mixed-stream merge remediation drifts."""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def fail(message: str) -> None:
    print(f"comp r02 contract: FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    cli = (ROOT / "src/arc_cli/cli.py").read_text(encoding="utf-8")
    for phrase in (
        "stream merge output can represent only stream inputs",
        "concatenating the *decompressed* source bytes",
        'create_inputs = [logical_stream.name]',
    ):
        if phrase not in cli:
            fail(f"missing mixed-stream remediation authority: {phrase}")

    tests = (ROOT / "tests/test_comp_x02_merge.py").read_text(encoding="utf-8")
    for test_name in (
        "test_merge_mixed_stream_formats_repack_to_one_logical_stream",
        "test_merge_container_to_stream_fails_closed_as_ambiguous",
    ):
        if test_name not in tests:
            fail(f"missing regression: {test_name}")

    cfg = tomllib.loads((ROOT / ".devtool.toml").read_text(encoding="utf-8"))
    profile = cfg.get("test_profiles", {}).get("comp_r02", {})
    required_tests = {"tests/test_comp_x02_merge.py", "tests/test_comp_temp_cleanup.py"}
    if not required_tests <= set(profile.get("pytest_tests", [])):
        fail("comp_r02 test profile does not own X02 + cleanup regressions")
    if profile.get("pytest_include_defaults") is not False:
        fail("comp_r02 test profile must be explicit")

    declared = {row.get("id"): row for row in cfg.get("test", []) if isinstance(row, dict)}
    if "arc-comp-r02-mixed-stream-repack" not in declared:
        fail("missing first-class comp-r02 test")
    target = cfg.get("targets", {}).get("arc", {})
    job = target.get("jobs", {}).get("comp-r02-contract", {})
    if list(job.get("command", [])) != ["python3", "scripts/check_comp_r02_contract.py"]:
        fail("comp-r02-contract job must execute canonical checker")
    refs = {step.get("ref") for step in target.get("workflows", {}).get("comp_r02", []) if isinstance(step, dict)}
    if not {"job:comp-r02-contract", "test:arc-comp-r02-mixed-stream-repack"} <= refs:
        fail("comp_r02 workflow missing contract/test ownership")
    wrapper = cfg.get("wrapper", {}).get("commands", {}).get("comp-r02", {})
    if wrapper.get("workflow") != "comp_r02":
        fail("wrapper comp-r02 command must execute comp_r02 workflow")

    roadmap = (ROOT / "docs/ARC-NEXT-ROADMAP.md").read_text(encoding="utf-8").lower()
    if "comp-r02" not in roadmap or "mixed-stream" not in roadmap:
        fail("roadmap does not record COMP-R02")
    print("comp r02 contract: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
