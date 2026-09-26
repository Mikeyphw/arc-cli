#!/usr/bin/env python3
"""Run Devtool's wrapper final seal and preserve its machine evidence."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / ".devtool" / "evidence" / "arc-final-gate"
OUT = EVIDENCE / "wrapper-seal.json"


def main() -> int:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["devtool", "--repo", str(ROOT), "wrapper", "seal", "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        env=os.environ.copy(),
    )
    try:
        payload = json.loads(proc.stdout) if proc.stdout.strip() else {}
    except json.JSONDecodeError:
        payload = {"ok": False, "status": "invalid-json", "stdout": proc.stdout}
    result = {
        "schema_version": 1,
        "status": "PASS" if proc.returncode == 0 and payload.get("ok") is True else "FAIL",
        "exit_code": proc.returncode,
        "wrapper_status": (payload.get("data") or {}).get("status"),
        "checks": len((payload.get("data") or {}).get("checks") or []),
        "payload": payload,
        "stderr": proc.stderr,
    }
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if result["status"] != "PASS":
        print(json.dumps(result, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps({k: result[k] for k in ("schema_version", "status", "exit_code", "wrapper_status", "checks")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
