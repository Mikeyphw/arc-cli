#!/usr/bin/env python3
"""Fail closed when the ARC-R09B cumulative gate contract drifts."""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def fail(message: str) -> None:
    print(f"r09b gate contract: FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    data = tomllib.loads((ROOT / ".devtool.toml").read_text(encoding="utf-8"))
    profiles = data.get("test_profiles", {})
    profile = profiles.get("r09b_gate", {})
    expected_tests = {
        "tests/test_r09b_gate.py",
        "tests/test_r09b_remote_capability_publication.py",
        "tests/test_r04_remote_transport.py",
        "tests/test_r05_remote_native.py",
        "tests/test_r08_machine_capability_verification.py",
        "tests/test_r09a_provenance_diff.py",
        "tests/test_r07_execution_recovery_resume.py",
        "tests/test_convert_command.py",
        "tests/test_cli_integration.py",
        "tests/test_safety.py",
        "tests/test_info_command.py",
        "tests/test_command_aliases.py",
        "tests/test_completion.py",
        "tests/test_manpages.py",
        "tests/test_distribution_contract.py",
    }
    actual_profile = set(profile.get("pytest_tests", []))
    missing_profile = sorted(expected_tests - actual_profile)
    if missing_profile:
        fail("r09b_gate profile missing: " + ", ".join(missing_profile))
    if profile.get("pytest_include_defaults") is not False:
        fail("r09b_gate profile must be explicit (pytest_include_defaults=false)")

    declared = {row.get("id"): row for row in data.get("test", []) if isinstance(row, dict)}
    gate_test = declared.get("arc-r09b-gate")
    if not gate_test:
        fail("missing first-class arc-r09b-gate test")
    command = list(gate_test.get("command", []))
    for test_path in sorted(expected_tests):
        if test_path not in command:
            fail(f"arc-r09b-gate test command missing {test_path}")

    target = data.get("targets", {}).get("arc", {})
    jobs = target.get("jobs", {})
    gate_job = jobs.get("r09b-gate-contract", {})
    if list(gate_job.get("command", [])) != ["python3", "scripts/check_r09b_gate_contract.py"]:
        fail("r09b-gate-contract job must execute the canonical checker")

    workflow = target.get("workflows", {}).get("r09b_gate", [])
    refs = {row.get("ref") for row in workflow if isinstance(row, dict)}
    for required in (
        "job:machine-contract",
        "job:provenance-contract",
        "job:remote-contract",
        "job:command-docs-contract",
        "job:completion-contract",
        "job:devtool-contract",
        "job:r09b-gate-contract",
        "test:arc-r09b-gate",
    ):
        if required not in refs:
            fail(f"r09b_gate workflow missing {required}")

    wrapper = data.get("wrapper", {}).get("commands", {})
    if wrapper.get("r09b-gate", {}).get("workflow") != "r09b_gate":
        fail("wrapper r09b-gate command must use workflow='r09b_gate'")

    gate_doc = ROOT / "docs" / "ARC-R09B-GATE.md"
    if not gate_doc.is_file():
        fail("docs/ARC-R09B-GATE.md is missing")
    text = gate_doc.read_text(encoding="utf-8")
    for token in (
        "Status: QUALIFIED",
        "arc.remote-capability/v1",
        "single SSH probe",
        "provider-dependent",
        "zero-network",
        "provider generation",
        "remote re-read",
    ):
        if token not in text:
            fail(f"gate ledger missing token: {token}")

    wrapper_doc = (ROOT / "docs" / "WRAPPER.md").read_text(encoding="utf-8")
    if "./devtoolw r09b-gate" not in wrapper_doc:
        fail("wrapper documentation does not expose ./devtoolw r09b-gate")

    roadmap = (ROOT / "docs" / "ARC-NEXT-ROADMAP.md").read_text(encoding="utf-8")
    if "R09B gate: QUALIFIED" not in roadmap:
        fail("roadmap does not record R09B gate qualification")
    if "## R10 — Destructive-operation policy and interactive execution UX" not in roadmap:
        fail("R10 boundary disappeared from roadmap")

    print("r09b gate contract: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
