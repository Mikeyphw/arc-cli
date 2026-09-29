#!/usr/bin/env python3
"""Run the ARC R01-R12 cumulative behavior gate and emit gate evidence.

The repository wrapper invokes this through the final_gate EXO workflow. Each
roadmap phase is executed in a fresh pytest process so fake transports, monkey
patches, and process-global caches cannot leak between campaign phases. The
final_seal workflow adds Devtool wrapper sealing, content sealing, and the final
SEALED verdict after this gate passes.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import sysconfig
import time
import venv
import xml.etree.ElementTree as ET

PYTEST_ARGS = ["--maxfail=1", "-ra", "-q"]
REQUIREMENTS = ["pytest>=8", "rich>=13.9", "setuptools>=75", "wheel"]
TOOLS = [
    "tar", "bsdtar", "zip", "unzip", "7z", "7zz", "rar", "unrar",
    "gzip", "pigz", "bzip2", "pbzip2", "xz", "pixz", "zstd", "pzstd",
    "ssh", "rclone", "fzf", "rg", "yazi",
]
SUITES: list[tuple[str, list[str]]] = [
    ("core", [
        "tests/test_cli_integration.py",
        "tests/test_completion.py",
        "tests/test_filtering.py",
        "tests/test_formats.py",
        "tests/test_safety.py",
        "tests/test_command_aliases.py",
        "tests/test_create_ui_shortcuts.py",
        "tests/test_info_command.py",
        "tests/test_convert_command.py",
        "tests/test_manpages.py",
        "tests/test_distribution_contract.py",
    ]),
    ("r01", ["tests/test_r01_semantics.py"]),
    ("r02", ["tests/test_r02_interaction.py"]),
    ("r03", ["tests/test_r03_qualification.py"]),
    ("r04-plan", ["tests/test_r04_execution_plan.py"]),
    ("r04-transport-1", [
        'tests/test_r04_remote_transport.py::test_remote_parser_supports_ssh_alias_and_rclone_native',
        'tests/test_r04_remote_transport.py::test_configured_rclone_parser_does_not_probe',
        'tests/test_r04_remote_transport.py::test_native_rclone_dry_run_discovers_local_config_without_subprocess',
        'tests/test_r04_remote_transport.py::test_existing_local_colon_path_wins_over_rclone',
        'tests/test_r04_remote_transport.py::test_rclone_completion_cache_and_mutation_invalidation',
        'tests/test_r04_remote_transport.py::test_double_star_remote_completion_forces_refresh',
        'tests/test_r04_remote_transport.py::test_cache_cli_reports_and_clears_entries',
        'tests/test_r04_remote_transport.py::test_remote_completion_refresh_cli',
        'tests/test_r04_remote_transport.py::test_remote_transfer_dry_run_performs_no_io',
        'tests/test_r04_remote_transport.py::test_remote_create_dispatch_stages_then_uploads',
        'tests/test_r04_remote_transport.py::test_remote_create_dry_run_does_not_probe_or_upload',
        'tests/test_r04_remote_transport.py::test_ssh_completion_uses_configured_alias_and_cache',
    ]),
    ("r04-transport-2", [
        'tests/test_r04_remote_transport.py::test_parser_has_remote_native_options',
        'tests/test_r04_remote_transport.py::test_rclone_upload_is_temp_then_moveto',
        'tests/test_r04_remote_transport.py::test_remote_capability_cache_is_generation_scoped',
        'tests/test_r04_remote_transport.py::test_remote_execution_requires_remote_arc',
        'tests/test_r04_remote_transport.py::test_remote_execution_delegates_to_remote_arc_when_available',
        'tests/test_r04_remote_transport.py::test_rclone_remote_file_and_directory_inputs_stage_for_create',
        'tests/test_r04_remote_transport.py::test_remote_input_dry_run_records_without_network',
        'tests/test_r04_remote_transport.py::test_generated_zsh_fzf_descends_single_value_remote_directories',
        'tests/test_r04_remote_transport.py::test_remote_execution_dry_run_does_not_probe_network',
        'tests/test_r04_remote_transport.py::test_rclone_failed_upload_cleans_temporary_object',
        'tests/test_r04_remote_transport.py::test_configured_rclone_alias_is_preserved_in_completion',
        'tests/test_r04_remote_transport.py::test_default_rclone_config_change_invalidates_provider_generation',
    ]),
    ("r04-transport-3", [
        'tests/test_r04_remote_transport.py::test_completion_cache_state_uses_configured_ttl_and_generation',
        'tests/test_r04_remote_transport.py::test_remote_capabilities_require_rclone_binary',
        'tests/test_r04_remote_transport.py::test_ssh_capability_probe_reports_machine_safe_listing_features',
        'tests/test_r04_remote_transport.py::test_rclone_tar_create_streams_directly_and_finalizes_atomically',
        'tests/test_r04_remote_transport.py::test_remote_add_extension_changes_actual_remote_target',
        'tests/test_r04_remote_transport.py::test_remote_gzip_extract_streams_without_archive_staging',
        'tests/test_r04_remote_transport.py::test_ssh_atomic_upload_quotes_hostile_remote_path',
        'tests/test_r04_remote_transport.py::test_remote_completion_preserves_newline_filename_via_nul_transport',
        'tests/test_r04_remote_transport.py::test_stage_remote_read_cleans_partial_file_on_transport_failure',
        'tests/test_r04_remote_transport.py::test_rclone_upload_interrupt_path_deletes_temporary_object',
        'tests/test_r04_remote_transport.py::test_streaming_create_failure_cleans_rclone_temp_object',
        'tests/test_r04_remote_transport.py::test_ssh_atomic_sink_contains_interrupt_cleanup_trap',
    ]),
    ("r05", ["tests/test_r05_remote_native.py"]),
    ("r06", ["tests/test_r06_runtime_install_truth.py"]),
    ("r07", ["tests/test_r07_execution_recovery_resume.py"]),
    ("r08", ["tests/test_r08_machine_capability_verification.py"]),
    ("r09", [
        "tests/test_r09a_provenance_diff.py",
        "tests/test_r09b_remote_capability_publication.py",
        "tests/test_r09b_gate.py",
    ]),
    ("r10", [
        "tests/test_r10_mutation_policy_progress.py",
        "tests/test_r10a_backend_command_truth.py",
        "tests/test_r10b_machine_batch.py",
        "tests/test_r10c_mutation_policy_convergence.py",
        "tests/test_r10_gate.py",
    ]),
    ("r11", [
        "tests/test_r11_config_provenance.py",
        "tests/test_r11a_config_diagnostic_convergence.py",
        "tests/test_r11b_doctor_environment_isolation.py",
        "tests/test_r11_gate.py",
    ]),
    ("r12", [
        "tests/test_r12_advisory_benchmark_diagnostics.py",
        "tests/test_r12a_benchmark_corpus_truth.py",
        "tests/test_r12_gate.py",
    ]),
    ("final-contract", ["tests/test_final_gate.py"]),
]


def _can_run(python: str) -> bool:
    probe = subprocess.run(
        [python, "-c", "import pytest, rich, setuptools, wheel"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return probe.returncode == 0


def _cache_python() -> Path:
    cache_root = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))).expanduser()
    tag = f"{sys.implementation.name}-{sys.version_info.major}.{sys.version_info.minor}-{sysconfig.get_platform()}"
    req_hash = hashlib.sha256("\n".join(REQUIREMENTS).encode()).hexdigest()[:12]
    env_dir = cache_root / "arc-cli" / "test-envs" / f"final-gate-{tag}-{req_hash}"
    python = env_dir / "bin" / "python"
    if not python.exists():
        env_dir.parent.mkdir(parents=True, exist_ok=True)
        venv.EnvBuilder(with_pip=True, system_site_packages=True).create(env_dir)
    if not _can_run(str(python)):
        subprocess.run(
            [str(python), "-m", "pip", "install", "--disable-pip-version-check", "--prefer-binary", *REQUIREMENTS],
            check=True,
        )
    return python


def _junit_counts(path: Path) -> dict[str, int]:
    root = ET.parse(path).getroot()
    nodes = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    if root.tag != "testsuite" and nodes and nodes[0] is root:
        nodes = nodes[1:]
    return {
        "tests": sum(int(node.attrib.get("tests", 0)) for node in nodes),
        "failures": sum(int(node.attrib.get("failures", 0)) for node in nodes),
        "errors": sum(int(node.attrib.get("errors", 0)) for node in nodes),
        "skipped": sum(int(node.attrib.get("skipped", 0)) for node in nodes),
    }


def _add_counts(total: dict[str, int], part: dict[str, int]) -> None:
    for key in total:
        total[key] += int(part.get(key, 0))


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    evidence = root / ".devtool" / "evidence" / "arc-final-gate"
    evidence.mkdir(parents=True, exist_ok=True)
    python = sys.executable if _can_run(sys.executable) else str(_cache_python())
    env = os.environ.copy()
    src = str(root / "src")
    env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["ARC_GATE_EVIDENCE_DIR"] = str(evidence)

    started = time.time()
    counts = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    suite_results: list[dict[str, object]] = []
    overall_rc = 0

    for name, tests in SUITES:
        junit = evidence / f"pytest-{name}.xml"
        command = [python, "-m", "pytest", *tests, *PYTEST_ARGS, f"--junitxml={junit}"]
        print(f"[ARC gate] {name}: {' '.join(tests)}", flush=True)
        proc = subprocess.run(command, cwd=root, env=env, check=False)
        part = _junit_counts(junit) if junit.exists() else {"tests": 0, "failures": 0, "errors": 1, "skipped": 0}
        _add_counts(counts, part)
        suite_results.append({"name": name, "tests": tests, "returncode": proc.returncode, "counts": part})
        if proc.returncode != 0:
            overall_rc = proc.returncode or 1
            break

    from arc_final_seal import CAMPAIGN_BASE_COMMIT_EXPECTED_PREFIX, QUALIFIED_GATE_COMMIT_EXPECTED_PREFIX, ROADMAP, SEAL_ID
    environment = {
        "schema_version": 2,
        "python": sys.version,
        "gate_python": python,
        "platform": platform.platform(),
        "tools": {name: shutil.which(name) for name in TOOLS},
    }
    (evidence / "environment.json").write_text(json.dumps(environment, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    status = "GATE_PASS" if overall_rc == 0 else "GATE_FAIL"
    verdict = {
        "schema_version": 2,
        "status": status,
        "gate": SEAL_ID,
        "pytest": counts,
        "suites": suite_results,
        "duration_seconds": round(time.time() - started, 3),
        "campaign_base_commit_expected_prefix": CAMPAIGN_BASE_COMMIT_EXPECTED_PREFIX,
        "qualified_gate_commit_expected_prefix": QUALIFIED_GATE_COMMIT_EXPECTED_PREFIX,
        "roadmap": ROADMAP,
    }
    (evidence / "gate-verdict.json").write_text(json.dumps(verdict, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (evidence / "gate-summary.json").write_text(json.dumps(verdict, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(verdict, sort_keys=True))
    return overall_rc


if __name__ == "__main__":
    raise SystemExit(main())
