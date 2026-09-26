#!/usr/bin/env python3
"""Run the ARC R01-R05 cumulative behavior gate and emit gate evidence.

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
    ]),
    ("r01", ["tests/test_r01_semantics.py"]),
    ("r02", ["tests/test_r02_interaction.py"]),
    ("r03", ["tests/test_r03_qualification.py"]),
    ("r04", ["tests/test_r04_execution_plan.py", "tests/test_r04_remote_transport.py"]),
    ("r05", ["tests/test_r05_remote_native.py"]),
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

    from arc_final_seal import BASE_COMMIT_EXPECTED_PREFIX, ROADMAP
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
        "gate": "ARC-R01-R05-FINAL",
        "pytest": counts,
        "suites": suite_results,
        "duration_seconds": round(time.time() - started, 3),
        "base_commit_expected_prefix": BASE_COMMIT_EXPECTED_PREFIX,
        "roadmap": ROADMAP,
    }
    (evidence / "gate-verdict.json").write_text(json.dumps(verdict, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (evidence / "gate-summary.json").write_text(json.dumps(verdict, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(verdict, sort_keys=True))
    return overall_rc


if __name__ == "__main__":
    raise SystemExit(main())
