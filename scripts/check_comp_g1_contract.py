#!/usr/bin/env python3
"""Fail closed when the COMP-G1 cumulative composition gate drifts."""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_TESTS = {
    "tests/test_comp_g1_gate.py",
    "tests/test_comp_x02_merge.py",
    "tests/test_comp_x03_volume.py",
    "tests/test_comp_temp_cleanup.py",
    "tests/test_comp_r03_machine_schema_registry.py",
    "tests/test_r03_qualification.py",
    "tests/test_r04_execution_plan.py",
    "tests/test_r04_remote_transport.py",
    "tests/test_r07_execution_recovery_resume.py",
    "tests/test_r08_machine_capability_verification.py",
    "tests/test_r10a_backend_command_truth.py",
    "tests/test_r10b_machine_batch.py",
    "tests/test_formats.py",
    "tests/test_final_gate.py",
    "tests/test_r06_runtime_install_truth.py",
    "tests/test_cli_integration.py",
    "tests/test_safety.py",
    "tests/test_info_command.py",
    "tests/test_command_aliases.py",
    "tests/test_completion.py",
    "tests/test_manpages.py",
    "tests/test_distribution_contract.py",
}


def fail(message: str) -> None:
    print(f"comp g1 contract: FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    cfg = tomllib.loads((ROOT / ".devtool.toml").read_text(encoding="utf-8"))
    profiles = cfg.get("test_profiles", {})
    profile = profiles.get("comp_g1", {})
    actual = set(profile.get("pytest_tests", []))
    if actual != EXPECTED_TESTS:
        fail(f"comp_g1 profile drift; missing={sorted(EXPECTED_TESTS-actual)}, extra={sorted(actual-EXPECTED_TESTS)}")
    if profile.get("pytest_include_defaults") is not False:
        fail("comp_g1 profile must be explicit (pytest_include_defaults=false)")
    if "comp_r03" not in profiles:
        fail("COMP-G1 must preserve the COMP-R03 focused profile")

    declared = {row.get("id"): row for row in cfg.get("test", []) if isinstance(row, dict)}
    for test_id in (
        "arc-comp-x01-version-composition-foundation",
        "arc-comp-x02-logical-merge",
        "arc-comp-x03-exact-volume-protocol",
        "arc-comp-r01-temp-lifecycle",
        "arc-comp-r02-mixed-stream-repack",
        "arc-comp-r03-machine-schema-registry",
        "arc-comp-g1-gate",
    ):
        if test_id not in declared:
            fail(f"missing first-class {test_id} test")
    command = list(declared["arc-comp-g1-gate"].get("command", []))
    for path in sorted(EXPECTED_TESTS):
        if path not in command:
            fail(f"arc-comp-g1-gate command missing {path}")

    target = cfg.get("targets", {}).get("arc", {})
    jobs = target.get("jobs", {})
    if list(jobs.get("comp-g1-contract", {}).get("command", [])) != ["python3", "scripts/check_comp_g1_contract.py"]:
        fail("comp-g1-contract job must execute the canonical checker")
    if "comp-r03-contract" not in jobs:
        fail("COMP-G1 must preserve the COMP-R03 contract job")

    workflow = target.get("workflows", {}).get("comp_g1", [])
    refs = {step.get("ref") for step in workflow if isinstance(step, dict)}
    required_refs = {
        "job:source-hygiene",
        "job:comp-x01-contract",
        "job:comp-x02-contract",
        "job:comp-x03-contract",
        "job:comp-r01-contract",
        "job:comp-r02-contract",
        "job:comp-r03-contract",
        "job:machine-contract",
        "job:remote-contract",
        "job:alias-contract",
        "job:command-docs-contract",
        "job:completion-contract",
        "job:devtool-contract",
        "job:comp-g1-contract",
        "test:arc-comp-g1-gate",
    }
    if not required_refs <= refs:
        fail("comp_g1 workflow missing cumulative refs: " + ", ".join(sorted(required_refs - refs)))

    wrappers = cfg.get("wrapper", {}).get("commands", {})
    wrapper = wrappers.get("comp-g1", {})
    if wrapper.get("workflow") != "comp_g1":
        fail("wrapper comp-g1 command must execute comp_g1 workflow")
    if wrappers.get("comp-r03", {}).get("workflow") != "comp_r03":
        fail("COMP-G1 must preserve the COMP-R03 wrapper command")

    gate_tests = (ROOT / "tests/test_comp_g1_gate.py").read_text(encoding="utf-8")
    for name in (
        "test_gate_zip_logical_merge_and_safe_gzip_concat",
        "test_gate_hostile_merge_member_is_rejected_without_escape",
        "test_gate_mixed_format_resolution_fails_closed_then_explicit_format_works",
        "test_gate_split_join_strict_manifest_order_and_cleanup",
        "test_gate_success_paths_leave_no_composition_temp_state",
        "test_gate_merge_split_join_keyboard_interrupts_leave_no_temp_state",
        "test_gate_real_sigint_cleans_transaction_owned_temp_and_preserves_journal",
        "test_gate_backend_manifest_materialization_cleans_on_interrupt",
        "test_gate_machine_explain_and_batch_surfaces",
        "test_gate_7z_logical_merge_when_backend_available",
    ):
        if name not in gate_tests:
            fail(f"gate regression missing: {name}")

    gate_doc = (ROOT / "docs/ARC-COMP-G1-GATE.md").read_text(encoding="utf-8").lower()
    for phrase in (
        "qualification-only",
        "version/package identity",
        "safe stream concat",
        "mixed-stream logical repack",
        "exact split/join",
        "temporary-file hygiene",
        "success, ordinary errors, and ctrl+c/sigint",
        "remote staging",
        "backend manifest",
        "wheel + sdist",
        "comp-r03",
        "no implementation work",
    ):
        if phrase not in gate_doc:
            fail(f"gate ledger missing promise: {phrase}")

    roadmap = (ROOT / "docs/ARC-NEXT-ROADMAP.md").read_text(encoding="utf-8").lower()
    if "comp-g1 gate: qualified" not in roadmap:
        fail("roadmap must record COMP-G1 qualification")
    if "roadmap refresh required after composition closure" not in roadmap:
        fail("roadmap must require a refresh before naming the next major scope")
    for remediation in ("comp-r01", "comp-r02", "comp-r03"):
        if remediation not in roadmap:
            fail(f"roadmap lost {remediation} ownership")

    print("comp g1 contract: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
