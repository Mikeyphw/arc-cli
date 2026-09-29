#!/usr/bin/env python3
"""Fail closed when the ARC-R12 final campaign qualification gate drifts."""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_TESTS = {
    "tests/test_r12_gate.py",
    "tests/test_r12a_benchmark_corpus_truth.py",
    "tests/test_r12_advisory_benchmark_diagnostics.py",
    "tests/test_r11_gate.py",
    "tests/test_r11b_doctor_environment_isolation.py",
    "tests/test_r11a_config_diagnostic_convergence.py",
    "tests/test_r11_config_provenance.py",
    "tests/test_r10_gate.py",
    "tests/test_r10c_mutation_policy_convergence.py",
    "tests/test_r10_mutation_policy_progress.py",
    "tests/test_r10b_machine_batch.py",
    "tests/test_r10a_backend_command_truth.py",
    "tests/test_r09b_gate.py",
    "tests/test_r09b_remote_capability_publication.py",
    "tests/test_r09a_provenance_diff.py",
    "tests/test_r08_machine_capability_verification.py",
    "tests/test_r07_execution_recovery_resume.py",
    "tests/test_r06_runtime_install_truth.py",
    "tests/test_r05_remote_native.py",
    "tests/test_r04_execution_plan.py",
    "tests/test_r04_remote_transport.py",
    "tests/test_convert_command.py",
    "tests/test_cli_integration.py",
    "tests/test_safety.py",
    "tests/test_info_command.py",
    "tests/test_create_ui_shortcuts.py",
    "tests/test_command_aliases.py",
    "tests/test_completion.py",
    "tests/test_manpages.py",
    "tests/test_distribution_contract.py",
}


def fail(message: str) -> None:
    print(f"r12 gate contract: FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    cfg = tomllib.loads((ROOT / ".devtool.toml").read_text(encoding="utf-8"))
    profile = cfg.get("test_profiles", {}).get("r12_gate", {})
    actual = set(profile.get("pytest_tests", []))
    if actual != EXPECTED_TESTS:
        missing = sorted(EXPECTED_TESTS - actual)
        extra = sorted(actual - EXPECTED_TESTS)
        fail(f"r12_gate profile drift; missing={missing}, extra={extra}")
    if profile.get("pytest_include_defaults") is not False:
        fail("r12_gate profile must be explicit (pytest_include_defaults=false)")

    declared = {row.get("id"): row for row in cfg.get("test", []) if isinstance(row, dict)}
    for test_id in (
        "arc-r09b-gate",
        "arc-r10-gate",
        "arc-r11-gate",
        "arc-r12-advisory-benchmark-diagnostics",
        "arc-r12a-benchmark-corpus-truth",
        "arc-r12-gate",
    ):
        if test_id not in declared:
            fail(f"missing first-class {test_id} test")
    command = list(declared["arc-r12-gate"].get("command", []))
    for path in sorted(EXPECTED_TESTS):
        if path not in command:
            fail(f"arc-r12-gate test command missing {path}")

    target = cfg.get("targets", {}).get("arc", {})
    jobs = target.get("jobs", {})
    if list(jobs.get("r12-gate-contract", {}).get("command", [])) != ["python3", "scripts/check_r12_gate_contract.py"]:
        fail("r12-gate-contract job must execute the canonical checker")
    workflow = target.get("workflows", {}).get("r12_gate", [])
    refs = {step.get("ref") for step in workflow if isinstance(step, dict)}
    required_refs = {
        "job:machine-contract", "job:config-contract", "job:remote-contract",
        "job:r10-gate-contract", "job:r11-gate-contract", "job:r12-contract",
        "job:r12a-contract", "job:r12-gate-contract", "job:command-docs-contract",
        "job:completion-contract", "job:devtool-contract", "test:arc-r12-gate",
    }
    if not required_refs <= refs:
        fail("r12_gate workflow missing required cumulative refs: " + ", ".join(sorted(required_refs - refs)))

    wrapper = cfg.get("wrapper", {}).get("commands", {}).get("r12-gate", {})
    if wrapper.get("workflow") != "r12_gate":
        fail("wrapper r12-gate command must execute r12_gate workflow")

    gate_doc = (ROOT / "docs/ARC-R12-GATE.md").read_text(encoding="utf-8").lower()
    for phrase in (
        "recommendation remains non-prescriptive",
        "manifest integrity",
        "no live remote/network probe",
        "benchmark corpus truth",
        "final campaign qualification",
        "separate content seal",
    ):
        if phrase not in gate_doc:
            fail(f"gate ledger missing promise: {phrase}")

    roadmap = (ROOT / "docs/ARC-NEXT-ROADMAP.md").read_text(encoding="utf-8").lower()
    if "r12 gate: qualified" not in roadmap:
        fail("roadmap must record R12 gate qualification")
    if "separate final campaign content seal" not in roadmap:
        fail("roadmap must preserve separate final seal boundary")

    print("r12 gate contract: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
