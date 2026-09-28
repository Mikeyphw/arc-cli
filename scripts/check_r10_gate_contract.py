#!/usr/bin/env python3
"""Fail closed when the ARC-R10 cumulative gate contract drifts."""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def fail(message: str) -> None:
    print(f"r10 gate contract: FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    data = tomllib.loads((ROOT / ".devtool.toml").read_text(encoding="utf-8"))
    expected_tests = {
        "tests/test_r10_gate.py",
        "tests/test_r10c_mutation_policy_convergence.py",
        "tests/test_r10_mutation_policy_progress.py",
        "tests/test_r10a_backend_command_truth.py",
        "tests/test_r10b_machine_batch.py",
        "tests/test_r07_execution_recovery_resume.py",
        "tests/test_r09b_remote_capability_publication.py",
        "tests/test_r05_remote_native.py",
        "tests/test_r04_execution_plan.py",
        "tests/test_r04_remote_transport.py",
        "tests/test_r08_machine_capability_verification.py",
        "tests/test_convert_command.py",
        "tests/test_cli_integration.py",
        "tests/test_safety.py",
        "tests/test_create_ui_shortcuts.py",
        "tests/test_command_aliases.py",
        "tests/test_completion.py",
        "tests/test_manpages.py",
        "tests/test_distribution_contract.py",
    }

    profile = data.get("test_profiles", {}).get("r10_gate", {})
    actual_profile = set(profile.get("pytest_tests", []))
    missing = sorted(expected_tests - actual_profile)
    if missing:
        fail("r10_gate profile missing: " + ", ".join(missing))
    if profile.get("pytest_include_defaults") is not False:
        fail("r10_gate profile must be explicit (pytest_include_defaults=false)")

    declared = {row.get("id"): row for row in data.get("test", []) if isinstance(row, dict)}
    gate_test = declared.get("arc-r10-gate")
    if not gate_test:
        fail("missing first-class arc-r10-gate test")
    command = list(gate_test.get("command", []))
    for test_path in sorted(expected_tests):
        if test_path not in command:
            fail(f"arc-r10-gate test command missing {test_path}")

    target = data.get("targets", {}).get("arc", {})
    jobs = target.get("jobs", {})
    gate_job = jobs.get("r10-gate-contract", {})
    if list(gate_job.get("command", [])) != ["python3", "scripts/check_r10_gate_contract.py"]:
        fail("r10-gate-contract job must execute the canonical checker")

    workflow = target.get("workflows", {}).get("r10_gate", [])
    refs = {row.get("ref") for row in workflow if isinstance(row, dict)}
    for required in (
        "job:machine-contract",
        "job:remote-contract",
        "job:batch-contract",
        "job:command-docs-contract",
        "job:completion-contract",
        "job:devtool-contract",
        "job:r10-gate-contract",
        "test:arc-r10-gate",
    ):
        if required not in refs:
            fail(f"r10_gate workflow missing {required}")

    wrapper = data.get("wrapper", {}).get("commands", {})
    if wrapper.get("r10-gate", {}).get("workflow") != "r10_gate":
        fail("wrapper r10-gate command must use workflow='r10_gate'")
    for surface in ("test", "validate"):
        choices = wrapper.get(surface, {}).get("options", {}).get("profile", {}).get("choices", [])
        if "r10_gate" not in choices:
            fail(f"wrapper {surface} profile choices missing r10_gate")

    gate_doc = ROOT / "docs" / "ARC-R10-GATE.md"
    if not gate_doc.is_file():
        fail("docs/ARC-R10-GATE.md is missing")
    text = gate_doc.read_text(encoding="utf-8")
    for token in (
        "Status: QUALIFIED",
        "fail|replace|rename|skip-identical",
        "skip-identical",
        "backup-existing",
        "Command",
        "arc.batch-input/v1",
        "single clean JSON record",
        "240 tests",
        "R11 configuration provenance",
    ):
        if token not in text:
            fail(f"gate ledger missing token: {token}")

    wrapper_doc = (ROOT / "docs" / "WRAPPER.md").read_text(encoding="utf-8")
    if "./devtoolw r10-gate" not in wrapper_doc:
        fail("wrapper documentation does not expose ./devtoolw r10-gate")

    roadmap = (ROOT / "docs" / "ARC-NEXT-ROADMAP.md").read_text(encoding="utf-8")
    if "R10 gate: QUALIFIED" not in roadmap:
        fail("roadmap does not record R10 gate qualification")
    if "## R11 — Configuration provenance and explainability" not in roadmap:
        fail("R11 boundary disappeared from roadmap")

    print("r10 gate contract: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
