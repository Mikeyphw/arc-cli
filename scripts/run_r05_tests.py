#!/usr/bin/env python3
"""Run the ARC-R05 pytest slice with the smallest possible bootstrap.

This is intentionally a single tests-only validation entrypoint. It prefers the
host Python when pytest/rich are already importable. Otherwise it creates a
small reusable cache venv containing only the test/runtime dependencies needed
for the R05 modules. The project itself is not installed; PYTHONPATH points at
``src`` so validation does not trigger Devtool's Python restore/build/SBOM
lifecycle.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
import venv

TESTS = [
    "tests/test_r05_remote_native.py",
]
PYTEST_ARGS = ["--maxfail=1"]
REQUIREMENTS = ["pytest>=8", "rich>=13.9"]


def _can_run(python: str) -> bool:
    probe = subprocess.run(
        [python, "-c", "import pytest, rich"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return probe.returncode == 0


def _cache_python() -> Path:
    cache_root = Path(
        os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))
    ).expanduser()
    tag = f"{sys.implementation.name}-{sys.version_info.major}.{sys.version_info.minor}-{sysconfig.get_platform()}"
    req_hash = hashlib.sha256("\n".join(REQUIREMENTS).encode()).hexdigest()[:12]
    env_dir = cache_root / "arc-cli" / "test-envs" / f"r05-{tag}-{req_hash}"
    python = env_dir / "bin" / "python"
    if not python.exists():
        env_dir.parent.mkdir(parents=True, exist_ok=True)
        venv.EnvBuilder(with_pip=True, system_site_packages=True).create(env_dir)
    if not _can_run(str(python)):
        subprocess.run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--prefer-binary",
                *REQUIREMENTS,
            ],
            check=True,
        )
    return python


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    python = sys.executable if _can_run(sys.executable) else str(_cache_python())
    env = os.environ.copy()
    src = str(root / "src")
    env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    command = [python, "-m", "pytest", *TESTS, *PYTEST_ARGS]
    return subprocess.call(command, cwd=root, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
