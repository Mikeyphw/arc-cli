#!/usr/bin/env python3
"""Combine ARC gate, wrapper seal, and post-gate content candidate."""
from __future__ import annotations

import json
from pathlib import Path
import platform
import shutil
import sys
import time

from arc_final_seal import CAMPAIGN_BASE_COMMIT_EXPECTED_PREFIX, QUALIFIED_GATE_COMMIT_EXPECTED_PREFIX, ROADMAP, SEAL_ID

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / ".devtool" / "evidence" / "arc-final-gate"
TOOLS = ["tar", "bsdtar", "zip", "unzip", "7z", "7zz", "rar", "unrar", "gzip", "pigz", "bzip2", "xz", "zstd", "ssh", "rclone", "fzf", "rg", "yazi"]

def load(name: str) -> dict:
    path = EVIDENCE / name
    if not path.is_file():
        return {"status": "MISSING", "path": str(path)}
    return json.loads(path.read_text(encoding="utf-8"))

def main() -> int:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    gate = load("gate-verdict.json")
    wrapper = load("wrapper-seal.json")
    content = load("content-seal.json")
    checks = {
        "gate": gate.get("status") == "GATE_PASS" and gate.get("gate") == SEAL_ID,
        "wrapper_seal": wrapper.get("status") == "PASS",
        "content_candidate": content.get("status") == "PASS" and content.get("seal_id") == SEAL_ID,
        "qualified_gate_prefix": gate.get("qualified_gate_commit_expected_prefix") == QUALIFIED_GATE_COMMIT_EXPECTED_PREFIX and content.get("qualified_gate_commit_expected_prefix") == QUALIFIED_GATE_COMMIT_EXPECTED_PREFIX,
    }
    status = "SEALED" if all(checks.values()) else "FAILED"
    verdict = {
        "schema_version": 4,
        "status": status,
        "gate": SEAL_ID,
        "seal_id": SEAL_ID,
        "content_root_sha256": content.get("candidate_root_sha256"),
        "sealed_file_count": content.get("sealed_file_count"),
        "campaign_base_commit_expected_prefix": CAMPAIGN_BASE_COMMIT_EXPECTED_PREFIX,
        "qualified_gate_commit_expected_prefix": QUALIFIED_GATE_COMMIT_EXPECTED_PREFIX,
        "roadmap": ROADMAP,
        "checks": checks,
        "gate_summary": gate,
        "wrapper_summary": {k: wrapper.get(k) for k in ("status", "exit_code", "wrapper_status", "checks")},
        "content_summary": {k: content.get(k) for k in ("status", "candidate_root_sha256", "sealed_file_count", "seal_phase")},
        "host": {"python": sys.version, "platform": platform.platform(), "tools": {name: shutil.which(name) for name in TOOLS}},
        "validated_at_epoch": int(time.time()),
    }
    for name in ("final-seal-verdict.json", "summary.json"):
        (EVIDENCE / name).write_text(json.dumps(verdict, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": status, "seal_id": SEAL_ID, "content_root_sha256": content.get("candidate_root_sha256"), "checks": checks}, sort_keys=True))
    return 0 if status == "SEALED" else 1

if __name__ == "__main__":
    raise SystemExit(main())
