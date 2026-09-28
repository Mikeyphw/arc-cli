#!/usr/bin/env python3
"""Fail closed when the ARC-R11 cumulative configuration gate drifts."""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def fail(message: str) -> None:
    print(f"r11 gate contract: FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    data = tomllib.loads((ROOT / ".devtool.toml").read_text(encoding="utf-8"))
    expected_tests = {
        "tests/test_r11_gate.py",
        "tests/test_r11b_doctor_environment_isolation.py",
        "tests/test_r11a_config_diagnostic_convergence.py",
        "tests/test_r11_config_provenance.py",
        "tests/test_r10_gate.py",
        "tests/test_r10c_mutation_policy_convergence.py",
        "tests/test_r10b_machine_batch.py",
        "tests/test_r10a_backend_command_truth.py",
        "tests/test_r08_machine_capability_verification.py",
        "tests/test_r06_runtime_install_truth.py",
        "tests/test_cli_integration.py",
        "tests/test_completion.py",
        "tests/test_manpages.py",
        "tests/test_distribution_contract.py",
    }

    profile = data.get("test_profiles", {}).get("r11_gate", {})
    actual_profile = set(profile.get("pytest_tests", []))
    missing = sorted(expected_tests - actual_profile)
    if missing:
        fail("r11_gate profile missing: " + ", ".join(missing))
    if actual_profile - expected_tests:
        fail("r11_gate profile has unexpected paths: " + ", ".join(sorted(actual_profile - expected_tests)))
    if profile.get("pytest_include_defaults") is not False:
        fail("r11_gate profile must be explicit (pytest_include_defaults=false)")

    declared = {row.get("id"): row for row in data.get("test", []) if isinstance(row, dict)}
    for test_id in (
        "arc-r11-config-provenance",
        "arc-r11a-config-diagnostic-convergence",
        "arc-r11b-doctor-environment-isolation",
        "arc-r11-gate",
    ):
        if test_id not in declared:
            fail(f"missing first-class {test_id} test")
    gate_test = declared["arc-r11-gate"]
    command = list(gate_test.get("command", []))
    for test_path in sorted(expected_tests):
        if test_path not in command:
            fail(f"arc-r11-gate test command missing {test_path}")

    target = data.get("targets", {}).get("arc", {})
    jobs = target.get("jobs", {})
    gate_job = jobs.get("r11-gate-contract", {})
    if list(gate_job.get("command", [])) != ["python3", "scripts/check_r11_gate_contract.py"]:
        fail("r11-gate-contract job must execute the canonical checker")

    workflow = target.get("workflows", {}).get("r11_gate", [])
    refs = {row.get("ref") for row in workflow if isinstance(row, dict)}
    for required in (
        "job:machine-contract",
        "job:config-contract",
        "job:batch-contract",
        "job:r10-gate-contract",
        "job:command-docs-contract",
        "job:completion-contract",
        "job:devtool-contract",
        "job:r11-gate-contract",
        "test:arc-r11-gate",
    ):
        if required not in refs:
            fail(f"r11_gate workflow missing {required}")

    wrapper = data.get("wrapper", {}).get("commands", {})
    if wrapper.get("r11-gate", {}).get("workflow") != "r11_gate":
        fail("wrapper r11-gate command must use workflow='r11_gate'")
    for surface in ("test", "validate"):
        choices = wrapper.get(surface, {}).get("options", {}).get("profile", {}).get("choices", [])
        if "r11_gate" not in choices:
            fail(f"wrapper {surface} profile choices missing r11_gate")

    gate_doc = ROOT / "docs" / "ARC-R11-GATE.md"
    if not gate_doc.is_file():
        fail("docs/ARC-R11-GATE.md is missing")
    text = gate_doc.read_text(encoding="utf-8")
    for token in (
        "Status: QUALIFIED",
        "built-in → config → environment → profile → CLI",
        "arc.config-inspection/v1",
        "runtime-vs-explain parity",
        "invalid supported environment",
        "doctor environment isolation",
        "134 tests",
        "R12 advisory intelligence",
    ):
        if token not in text:
            fail(f"gate ledger missing token: {token}")

    wrapper_doc = (ROOT / "docs" / "WRAPPER.md").read_text(encoding="utf-8")
    if "./devtoolw r11-gate" not in wrapper_doc:
        fail("wrapper documentation does not expose ./devtoolw r11-gate")

    audit = (ROOT / "docs" / "ARC-R11-AUDIT.md").read_text(encoding="utf-8")
    if "R11 gate result: QUALIFIED" not in audit:
        fail("R11 audit does not record gate qualification")

    roadmap = (ROOT / "docs" / "ARC-NEXT-ROADMAP.md").read_text(encoding="utf-8")
    if "R11 gate: QUALIFIED" not in roadmap:
        fail("roadmap does not record R11 gate qualification")
    if "## R12 — Advisory intelligence, benchmarking, and support evidence" not in roadmap:
        fail("R12 boundary disappeared from roadmap")

    print("r11 gate contract: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
