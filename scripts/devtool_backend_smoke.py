#!/usr/bin/env python3
"""Small deterministic native-backend smoke gate with structured evidence."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
def evidence_path() -> Path | None:
    raw = os.environ.get("ARC_DEVTOOL_SMOKE_EVIDENCE", "").strip()
    if raw == "-":
        return None
    if raw:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = ROOT / path
        return path.resolve()
    return ROOT / ".devtool" / "evidence" / "backend-smoke.json"


def run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.run(
        [sys.executable, "-m", "arc_cli", *args],
        cwd=cwd or ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    cases: list[dict[str, object]] = []
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="arc-devtool-smoke-") as tmp:
        base = Path(tmp)
        src = base / "src"
        src.mkdir()
        (src / "hello.txt").write_text("arc devtool smoke\n", encoding="utf-8")
        expected = digest(src / "hello.txt")

        candidates = [
            ("tar.gz", "tar.gz", ("tar", "gzip")),
            ("zip", "zip", ("zip", "unzip")),
            ("tar.zst", "tar.zst", ("tar", "zstd")),
        ]
        for suffix, fmt, required_binaries in candidates:
            missing = [binary for binary in required_binaries if shutil.which(binary) is None]
            if missing:
                cases.append({"format": fmt, "status": "skipped", "reason": f"missing: {', '.join(missing)}"})
                continue
            archive = base / f"smoke.{suffix}"
            restored = base / f"out-{fmt.replace('.', '-')}"
            create = run("create", str(archive), "src", cwd=base)
            if create.returncode != 0:
                failures.append(f"{fmt}: create failed: {create.stderr.strip()}")
                cases.append({"format": fmt, "status": "failed", "phase": "create"})
                continue
            identify = run("identify", str(archive), "--json", cwd=base)
            listing = run("list", str(archive), "--json", cwd=base)
            extract = run("extract", str(archive), "-o", str(restored), cwd=base)
            restored_file = restored / "src" / "hello.txt"
            ok = (
                identify.returncode == 0
                and listing.returncode == 0
                and extract.returncode == 0
                and restored_file.is_file()
                and digest(restored_file) == expected
            )
            cases.append({"format": fmt, "status": "passed" if ok else "failed"})
            if not ok:
                failures.append(
                    f"{fmt}: identify={identify.returncode} list={listing.returncode} "
                    f"extract={extract.returncode} restored={restored_file.is_file()}"
                )

    payload = {
        "schema_version": 1,
        "kind": "arc-devtool-backend-smoke",
        "ok": not failures,
        "cases": cases,
        "failures": failures,
    }
    evidence = evidence_path()
    if evidence is not None:
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
