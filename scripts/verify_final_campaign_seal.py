#!/usr/bin/env python3
"""Verify ARC R01-R12 final campaign seal evidence before/after commit."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from arc_final_seal import ROOT, SEAL_ID, QUALIFIED_GATE_COMMIT_EXPECTED_PREFIX, qualified_gate_ancestry, verify_manifest

EVIDENCE = ROOT / ".devtool" / "evidence" / "arc-final-gate"

def load(name: str) -> dict:
    path = EVIDENCE / name
    if not path.is_file():
        return {"status": "MISSING", "path": str(path)}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"status": "INVALID", "path": str(path), "error": str(exc)}

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-evidence", action="store_true")
    args = ap.parse_args()
    candidate_path = EVIDENCE / "candidate-seal.json"
    content_ok, content_verify = verify_manifest(candidate_path, ROOT)
    ancestry_ok, ancestry = qualified_gate_ancestry(ROOT)
    gate = load("gate-verdict.json")
    wrapper = load("wrapper-seal.json")
    content = load("content-seal.json")
    verdict = load("final-seal-verdict.json")
    candidate = load("candidate-seal.json")
    root_hash = candidate.get("root_sha256")
    checks = {
        "candidate_content": content_ok,
        "qualified_gate_ancestry": ancestry_ok is True,
        "gate": gate.get("status") == "GATE_PASS" and gate.get("gate") == SEAL_ID,
        "wrapper": wrapper.get("status") == "PASS",
        "content": content.get("status") == "PASS" and content.get("seal_id") == SEAL_ID,
        "verdict": verdict.get("status") == "SEALED" and verdict.get("seal_id") == SEAL_ID,
        "qualified_gate_prefix": verdict.get("qualified_gate_commit_expected_prefix") == QUALIFIED_GATE_COMMIT_EXPECTED_PREFIX,
        "root_consistency": bool(root_hash) and root_hash == content.get("candidate_root_sha256") == verdict.get("content_root_sha256"),
    }
    ok = all(checks.values())
    result = {
        "schema_version": 1,
        "status": "PASS" if ok else "FAIL",
        "seal_id": SEAL_ID,
        "qualified_gate_commit_expected_prefix": QUALIFIED_GATE_COMMIT_EXPECTED_PREFIX,
        "content_root_sha256": root_hash,
        "checks": checks,
        "qualified_gate_ancestry": ancestry,
        "content_verification": content_verify,
    }
    if args.write_evidence:
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        (EVIDENCE / "final-integrity.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    return 0 if ok else 1

if __name__ == "__main__":
    raise SystemExit(main())
