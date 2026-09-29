#!/usr/bin/env python3
"""Fail closed when COMP-R03 machine-schema registry convergence drifts."""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def fail(message: str) -> None:
    print(f"comp r03 contract: FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    checker = (ROOT / "scripts/check_machine_contract_r09b.py").read_text(encoding="utf-8")
    if "split-manifest-v1" not in checker:
        fail("active machine checker does not own split-manifest-v1")
    machine_tests = (ROOT / "tests/test_r08_machine_capability_verification.py").read_text(encoding="utf-8")
    if "split-manifest-v1" not in machine_tests:
        fail("active machine registry regression does not own split-manifest-v1")

    tests = (ROOT / "tests/test_comp_r03_machine_schema_registry.py").read_text(encoding="utf-8")
    for name in (
        "test_split_manifest_is_part_of_public_machine_schema_registry",
        "test_active_machine_contract_accepts_split_manifest_registry",
    ):
        if name not in tests:
            fail(f"missing regression: {name}")

    cfg = tomllib.loads((ROOT / ".devtool.toml").read_text(encoding="utf-8"))
    profile = cfg.get("test_profiles", {}).get("comp_r03", {})
    required = {
        "tests/test_comp_r03_machine_schema_registry.py",
        "tests/test_comp_x03_volume.py",
        "tests/test_r08_machine_capability_verification.py",
        "tests/test_distribution_contract.py",
    }
    if not required <= set(profile.get("pytest_tests", [])):
        fail("comp_r03 profile does not own machine/schema/package regressions")
    if profile.get("pytest_include_defaults") is not False:
        fail("comp_r03 profile must be explicit")

    declared = {row.get("id"): row for row in cfg.get("test", []) if isinstance(row, dict)}
    if "arc-comp-r03-machine-schema-registry" not in declared:
        fail("missing first-class comp-r03 test")
    target = cfg.get("targets", {}).get("arc", {})
    job = target.get("jobs", {}).get("comp-r03-contract", {})
    if list(job.get("command", [])) != ["python3", "scripts/check_comp_r03_contract.py"]:
        fail("comp-r03-contract job must execute canonical checker")
    if list(target.get("jobs", {}).get("machine-contract", {}).get("command", [])) != ["python3", "scripts/check_machine_contract_r09b.py"]:
        fail("active machine-contract authority drifted from R09B checker")
    refs = {step.get("ref") for step in target.get("workflows", {}).get("comp_r03", []) if isinstance(step, dict)}
    if not {"job:comp-r03-contract", "job:machine-contract", "test:arc-comp-r03-machine-schema-registry"} <= refs:
        fail("comp_r03 workflow missing contract/machine/test ownership")
    wrapper = cfg.get("wrapper", {}).get("commands", {}).get("comp-r03", {})
    if wrapper.get("workflow") != "comp_r03":
        fail("wrapper comp-r03 command must execute comp_r03 workflow")

    roadmap = (ROOT / "docs/ARC-NEXT-ROADMAP.md").read_text(encoding="utf-8").lower()
    if "comp-r03" not in roadmap or "machine-schema registry" not in roadmap:
        fail("roadmap does not record COMP-R03")
    print("comp r03 contract: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
