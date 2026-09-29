#!/usr/bin/env python3
"""Run Devtool's wrapper seal while isolating applicator-template drift.

ARC's repository wrapper contract is sealed against command discovery, resolution,
EXO provenance, completion, docs and doctor behavior.  Devtool's launcher
*template bytes* are owned by the applying Devtool installation and can advance
independently while an ARC campaign is in flight.  A final ARC content seal must
therefore not fail merely because existing executable marker-bearing launchers
are older than the currently installed Devtool template.

Only the exact `drifted` launcher-template condition is tolerated. Missing,
non-executable, unmarked launchers or any other wrapper-seal error remain fatal.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / ".devtool" / "evidence" / "arc-final-gate"
OUT = EVIDENCE / "wrapper-seal.json"
TOLERATED_ERROR_KEYS = {
    ("launchers", "current"),
    ("doctor", "project:wrapper-launchers"),
}


def _key(check: dict[str, Any]) -> tuple[str, str]:
    return str(check.get("section", "")), str(check.get("name", ""))


def _launcher_drift_is_safe(checks: list[dict[str, Any]]) -> bool:
    launcher = next((item for item in checks if _key(item) == ("launchers", "current")), None)
    if not isinstance(launcher, dict) or launcher.get("status") != "error":
        return False
    data = launcher.get("data") or {}
    status = data.get("launchers") or {}
    files = status.get("files") or []
    if not files:
        return False
    return all(
        bool(item.get("exists"))
        and bool(item.get("executable"))
        and bool(item.get("marker_present"))
        and item.get("reason") == "drifted"
        for item in files
        if isinstance(item, dict)
    ) and len([item for item in files if isinstance(item, dict)]) == len(files)


def classify_wrapper_payload(payload: dict[str, Any], *, process_exit_code: int, stderr: str = "") -> dict[str, Any]:
    data = payload.get("data") or {}
    checks = [item for item in (data.get("checks") or []) if isinstance(item, dict)]
    safe_drift = _launcher_drift_is_safe(checks)
    tolerated: list[dict[str, Any]] = []
    unexpected: list[dict[str, Any]] = []

    for item in checks:
        status = str(item.get("status", ""))
        if status in {"ok", "skipped"}:
            continue
        key = _key(item)
        if status == "error" and safe_drift and key in TOLERATED_ERROR_KEYS and "drifted" in str(item.get("detail", "")):
            tolerated.append({"section": key[0], "name": key[1], "detail": item.get("detail", "")})
            continue
        unexpected.append({
            "section": key[0],
            "name": key[1],
            "status": status,
            "detail": item.get("detail", ""),
        })

    required = {
        ("execution-bridge", "generic-contract"),
        ("execution-origin", "generic-contract"),
        ("discovery", "commands"),
        ("completion", "commands"),
        ("docs", "wrapper-guide"),
    }
    check_map = {_key(item): item for item in checks}
    missing_or_bad_required = [
        {"section": section, "name": name, "status": (check_map.get((section, name)) or {}).get("status", "missing")}
        for section, name in sorted(required)
        if (check_map.get((section, name)) or {}).get("status") != "ok"
    ]
    unexpected.extend(missing_or_bad_required)

    contract_ok = bool(checks) and not unexpected
    status = "PASS" if contract_ok else "FAIL"
    contract_status = "PASS_WITH_APPLICATOR_TEMPLATE_DRIFT" if contract_ok and tolerated else ("PASS" if contract_ok else "FAIL")
    return {
        "schema_version": 2,
        "status": status,
        "contract_status": contract_status,
        "process_exit_code": process_exit_code,
        "devtool_wrapper_status": data.get("status"),
        "checks": len(checks),
        "tolerated_template_drift": tolerated,
        "unexpected_checks": unexpected,
        "payload": payload,
        "stderr": stderr,
    }


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
    result = classify_wrapper_payload(payload, process_exit_code=proc.returncode, stderr=proc.stderr)
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary = {k: result[k] for k in ("schema_version", "status", "contract_status", "process_exit_code", "devtool_wrapper_status", "checks")}
    summary["tolerated_template_drift"] = result["tolerated_template_drift"]
    if result["status"] != "PASS":
        print(json.dumps(result, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
