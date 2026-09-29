#!/usr/bin/env python3
"""Generate and verify ARC's exact post-gate content ledger in validation evidence."""
from __future__ import annotations

import json

from arc_final_seal import ROOT, SEAL_ID, QUALIFIED_GATE_COMMIT_EXPECTED_PREFIX, build_manifest, qualified_gate_ancestry, verify_manifest


def main() -> int:
    evidence = ROOT / ".devtool" / "evidence" / "arc-final-gate"
    evidence.mkdir(parents=True, exist_ok=True)
    candidate_path = evidence / "candidate-seal.json"
    candidate = build_manifest(ROOT, seal_source="validated-gate-worktree")
    candidate_path.write_text(json.dumps(candidate, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    ok, verification = verify_manifest(candidate_path, ROOT)
    ancestry_ok, ancestry = qualified_gate_ancestry(ROOT)
    ok = ok and ancestry_ok is not False
    result = {
        "schema_version": 3,
        "status": "PASS" if ok else "FAIL",
        "seal_phase": "post-gate-live-content",
        "seal_id": SEAL_ID,
        "qualified_gate_commit_expected_prefix": QUALIFIED_GATE_COMMIT_EXPECTED_PREFIX,
        "source_head_at_seal": candidate.get("source_head_at_seal"),
        "candidate_manifest": str(candidate_path.relative_to(ROOT)),
        "candidate_root_sha256": candidate["root_sha256"],
        "sealed_file_count": candidate["sealed_file_count"],
        "verification": verification,
        "qualified_gate_ancestry": ancestry,
    }
    (evidence / "content-seal.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
