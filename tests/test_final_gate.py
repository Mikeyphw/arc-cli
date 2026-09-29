from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tomllib
import zipfile

import pytest

from arc_cli import __version__
from arc_cli.completion import zsh_completion

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = Path(os.environ.get("ARC_GATE_EVIDENCE_DIR", ROOT / ".devtool" / "evidence" / "arc-final-gate"))


def _seal_api():
    scripts = str(ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import arc_final_seal
    return arc_final_seal


def test_final_seal_generator_verifier_roundtrip(tmp_path: Path):
    seal_api = _seal_api()
    manifest_path = tmp_path / "ARC-FINAL-SEAL.json"
    manifest = seal_api.write_manifest(manifest_path, ROOT, seal_source="test-roundtrip")
    ok, result = seal_api.verify_manifest(manifest_path, ROOT)
    assert ok, result
    assert manifest["schema_version"] == 5
    assert manifest["status"] == "SEALED_CONTENT"
    assert manifest["seal_id"] == "ARC-R01-R12-FINAL"
    assert manifest["campaign_base_commit_expected_prefix"] == "a7028c1"
    assert manifest["qualified_gate_commit_expected_prefix"] == "95981f7"
    assert manifest["sealed_file_count"] == len(manifest["sealed_files"])
    assert manifest["sealed_file_count"] >= 69
    assert len(manifest["root_sha256"]) == 64


def test_release_seal_contract_is_static_and_points_to_live_evidence():
    seal = json.loads((ROOT / "release" / "ARC-FINAL-SEAL.json").read_text(encoding="utf-8"))
    assert seal["schema_version"] == 7
    assert seal["status"] == "SEAL_CONTRACT"
    assert seal["seal_id"] == "ARC-R01-R12-FINAL"
    assert seal["campaign_base_commit_expected_prefix"] == "a7028c1"
    assert seal["qualified_gate_commit_expected_prefix"] == "95981f7"
    assert seal["qualified_gate"] == "ARC-R12-GATE"
    assert seal["qualified_gate_tests"] == 378
    assert seal["validation_entrypoint"] == "./devtoolw seal"
    assert seal["live_ledger"] == ".devtool/evidence/arc-final-gate/candidate-seal.json"
    assert seal["live_verdict"] == ".devtool/evidence/arc-final-gate/final-seal-verdict.json"


def test_final_seal_is_descended_from_qualified_r12_gate():
    if not (ROOT / ".git").exists():
        pytest.skip("Git metadata is not present in the exported local audit snapshot")
    exists = subprocess.run(
        ["git", "cat-file", "-e", "95981f7^{commit}"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if exists.returncode != 0:
        pytest.skip("R12 gate object is not present in this reconstructed audit repository")
    proc = subprocess.run(
        ["git", "merge-base", "--is-ancestor", "95981f7", "HEAD"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_final_seal_ignores_unrelated_worktree_detritus(tmp_path: Path):
    seal_api = _seal_api()
    manifest_path = tmp_path / "seal.json"
    seal_api.write_manifest(manifest_path, ROOT, seal_source="detritus-test")
    junk = ROOT / ".arc-final-seal-runtime-junk"
    junk.write_text("runtime-only\n", encoding="utf-8")
    try:
        assert junk.name not in seal_api.authoritative_paths(ROOT)
        ok, result = seal_api.verify_manifest(manifest_path, ROOT)
        assert ok, result
    finally:
        junk.unlink(missing_ok=True)


def test_final_seal_detects_authoritative_mutation(tmp_path: Path):
    seal_api = _seal_api()
    manifest_path = tmp_path / "seal.json"
    seal_api.write_manifest(manifest_path, ROOT, seal_source="mutation-test")
    target = ROOT / "README.md"
    original = target.read_bytes()
    try:
        target.write_bytes(original + b"\nARC seal mutation probe\n")
        ok, result = seal_api.verify_manifest(manifest_path, ROOT)
        assert not ok
        assert "README.md" in result["changed"]
    finally:
        target.write_bytes(original)


def test_final_seal_detects_new_authoritative_file(tmp_path: Path):
    seal_api = _seal_api()
    manifest_path = tmp_path / "seal.json"
    seal_api.write_manifest(manifest_path, ROOT, seal_source="coverage-test")
    probe = ROOT / "tests" / "_arc_final_seal_untracked_probe.py"
    probe.write_text("PROBE = True\n", encoding="utf-8")
    try:
        ok, result = seal_api.verify_manifest(manifest_path, ROOT)
        assert not ok
        assert "tests/_arc_final_seal_untracked_probe.py" in result["unsealed_authoritative"]
    finally:
        probe.unlink(missing_ok=True)


def test_final_seal_scope_covers_packaged_schemas_manpages_and_manifest():
    seal_api = _seal_api()
    paths = set(seal_api.authoritative_paths(ROOT))
    assert "MANIFEST.in" in paths
    assert "src/arc_cli/schemas/machine-v1.schema.json" in paths
    assert "src/arc_cli/schemas/benchmark-v1.schema.json" in paths
    assert "src/arc_cli/man/arc.1" in paths
    assert "src/arc_cli/man/arc-config.5" in paths
    assert "src/arc_cli/man/arc-remote.7" in paths


def test_version_and_project_metadata_are_coherent():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["name"] == "arc-cli"
    assert data["project"]["version"] == __version__
    proc = subprocess.run(
        [sys.executable, "-m", "arc_cli", "--version"],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == f"arc {__version__}"


def test_committed_completion_matches_generator():
    assert (ROOT / "completions" / "_arc").read_text(encoding="utf-8") == zsh_completion()


def test_final_gate_devtool_and_wrapper_contract():
    cfg = tomllib.loads((ROOT / ".devtool.toml").read_text(encoding="utf-8"))
    profiles = cfg["test_profiles"]
    assert profiles["final_gate"]["pytest_tests"] == ["tests"]
    assert profiles["final_gate"]["pytest_include_defaults"] is False
    jobs = cfg["targets"]["arc"]["jobs"]
    assert jobs["final-gate"]["command"] == ["python3", "scripts/run_final_gate_tests.py"]
    assert jobs["wrapper-seal"]["command"] == ["python3", "scripts/run_wrapper_seal.py"]
    assert jobs["content-seal"]["command"] == ["python3", "scripts/run_content_seal.py"]
    assert jobs["final-seal-verdict"]["command"] == ["python3", "scripts/write_final_seal_verdict.py"]
    assert jobs["final-seal-contract"]["command"] == ["python3", "scripts/check_final_campaign_seal_contract.py"]
    assert jobs["final-seal-integrity"]["command"] == ["python3", "scripts/verify_final_campaign_seal.py", "--write-evidence"]
    assert (ROOT / "scripts" / "arc_final_seal.py").is_file()
    assert (ROOT / "scripts" / "run_content_seal.py").is_file()
    workflows = cfg["targets"]["arc"]["workflows"]
    assert "final_gate" in workflows and "final_seal" in workflows
    final_refs = {step["ref"] for step in workflows["final_seal"]}
    assert "job:final-seal-integrity" in final_refs
    assert "job:final-seal-contract" in final_refs
    commands = cfg["wrapper"]["commands"]
    assert commands["gate"]["workflow"] == "final_gate"
    assert commands["seal"]["workflow"] == "final_seal"


def test_wrapper_docs_expose_authoritative_gate_and_seal_entrypoints():
    doc = (ROOT / "docs" / "WRAPPER.md").read_text(encoding="utf-8")
    final_doc = (ROOT / "docs" / "ARC-FINAL-GATE-SEAL.md").read_text(encoding="utf-8")
    for command in ("./devtoolw gate", "./devtoolw seal"):
        assert command in doc
        assert command in final_doc


def test_campaign_promise_ledgers_and_gate_docs_are_present_and_final_doc_is_current():
    for revision in range(1, 6):
        assert (ROOT / "docs" / f"ARC-R0{revision}-AUDIT.md").is_file()
    for doc in (
        "ARC-R09B-GATE.md", "ARC-R10-GATE.md", "ARC-R11-GATE.md", "ARC-R12-GATE.md",
        "ARC-R10A-AUDIT.md", "ARC-R10B-AUDIT.md", "ARC-R10C-AUDIT.md",
        "ARC-R11-AUDIT.md",
        "ARC-R12-AUDIT.md", "ARC-R12A-AUDIT.md",
    ):
        assert (ROOT / "docs" / doc).is_file(), doc
    final_doc = (ROOT / "docs" / "ARC-FINAL-GATE-SEAL.md").read_text(encoding="utf-8")
    for gate in range(1, 19):
        assert f"G{gate:02d}" in final_doc
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "ARC-R04 is reserved for the later campaign gate/seal" not in readme
    assert "ARC final gate and content seal" in readme
    assert "ARC-R01 through ARC-R12" in readme


def test_full_r03_runtime_qualification_and_evidence():
    out = EVIDENCE / "r03"
    out.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [sys.executable, "scripts/run_r03_qualification.py", "--output-dir", str(out)],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))["summary"]
    assert summary["status"] == "PASS"
    assert summary["fail"] == 0
    assert summary["pass"] > 0
    assert summary["operation_matrix_pass"] > 0


def test_package_builds_wheel_and_sdist_with_expected_metadata(tmp_path: Path):
    out = tmp_path / "dist"
    out.mkdir()
    code = r"""
from setuptools.build_meta import build_sdist, build_wheel
import sys
out = sys.argv[1]
print(build_wheel(out))
print(build_sdist(out))
"""
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code, str(out)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
    finally:
        shutil.rmtree(ROOT / "build", ignore_errors=True)
        shutil.rmtree(ROOT / "src" / "arc_cli.egg-info", ignore_errors=True)
    wheels = list(out.glob("*.whl"))
    sdists = list(out.glob("*.tar.gz"))
    assert len(wheels) == 1
    assert len(sdists) == 1

    with zipfile.ZipFile(wheels[0]) as zf:
        names = set(zf.namelist())
        metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
        entry_name = next(name for name in names if name.endswith(".dist-info/entry_points.txt"))
        metadata = zf.read(metadata_name).decode("utf-8")
        entry_points = zf.read(entry_name).decode("utf-8")
        assert "src/" not in "\n".join(names)
        assert "arc_cli/cli.py" in names
        assert f"Version: {__version__}" in metadata
        assert re.search(r"(?m)^Requires-Dist: rich", metadata)
        assert "arc = arc_cli.cli:main" in entry_points

    with tarfile.open(sdists[0], "r:gz") as tf:
        names = tf.getnames()
        assert any(name.endswith("/pyproject.toml") for name in names)
        assert any(name.endswith("/src/arc_cli/cli.py") for name in names)

    evidence = EVIDENCE / "package"
    evidence.mkdir(parents=True, exist_ok=True)
    summary = {
        "schema_version": 1,
        "status": "PASS",
        "wheel": wheels[0].name,
        "wheel_sha256": __import__("hashlib").sha256(wheels[0].read_bytes()).hexdigest(),
        "sdist": sdists[0].name,
        "sdist_sha256": __import__("hashlib").sha256(sdists[0].read_bytes()).hexdigest(),
    }
    (evidence / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_cli_discovery_surfaces_are_operational():
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    for args in (["formats", "--json"], ["backends", "--json"]):
        proc = subprocess.run(
            [sys.executable, "-m", "arc_cli", *args],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        assert json.loads(proc.stdout)


def test_wrapper_seal_tolerates_only_safe_applicator_template_drift():
    import importlib.util
    spec = importlib.util.spec_from_file_location("arc_run_wrapper_seal", ROOT / "scripts" / "run_wrapper_seal.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    checks = [
        {"section": "execution-bridge", "name": "generic-contract", "status": "ok"},
        {"section": "execution-origin", "name": "generic-contract", "status": "ok"},
        {"section": "discovery", "name": "commands", "status": "ok"},
        {"section": "completion", "name": "commands", "status": "ok"},
        {"section": "docs", "name": "wrapper-guide", "status": "ok"},
        {"section": "launchers", "name": "current", "status": "error", "detail": "devtoolw:drifted, devtoolw.cmd:drifted",
         "data": {"launchers": {"files": [
             {"exists": True, "executable": True, "marker_present": True, "reason": "drifted"},
             {"exists": True, "executable": True, "marker_present": True, "reason": "drifted"},
         ]}}},
        {"section": "doctor", "name": "project:wrapper-launchers", "status": "error", "detail": "devtoolw:drifted, devtoolw.cmd:drifted"},
    ]
    result = mod.classify_wrapper_payload({"data": {"status": "error", "checks": checks}}, process_exit_code=1)
    assert result["status"] == "PASS"
    assert result["contract_status"] == "PASS_WITH_APPLICATOR_TEMPLATE_DRIFT"
    assert len(result["tolerated_template_drift"]) == 2
    assert result["unexpected_checks"] == []


def test_wrapper_seal_never_tolerates_missing_or_unmarked_launcher():
    import importlib.util
    spec = importlib.util.spec_from_file_location("arc_run_wrapper_seal_bad", ROOT / "scripts" / "run_wrapper_seal.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    checks = [
        {"section": "execution-bridge", "name": "generic-contract", "status": "ok"},
        {"section": "execution-origin", "name": "generic-contract", "status": "ok"},
        {"section": "discovery", "name": "commands", "status": "ok"},
        {"section": "completion", "name": "commands", "status": "ok"},
        {"section": "docs", "name": "wrapper-guide", "status": "ok"},
        {"section": "launchers", "name": "current", "status": "error", "detail": "devtoolw:missing",
         "data": {"launchers": {"files": [
             {"exists": False, "executable": False, "marker_present": False, "reason": "missing"},
         ]}}},
    ]
    result = mod.classify_wrapper_payload({"data": {"status": "error", "checks": checks}}, process_exit_code=1)
    assert result["status"] == "FAIL"
    assert result["unexpected_checks"]
