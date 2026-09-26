#!/usr/bin/env python3
"""Verify an ARC schema-5 content seal against the current authoritative tree."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from arc_final_seal import ROOT, verify_manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=ROOT / ".devtool" / "evidence" / "arc-final-gate" / "candidate-seal.json")
    parser.add_argument("--write-evidence", action="store_true")
    args = parser.parse_args()
    path = args.manifest if args.manifest.is_absolute() else ROOT / args.manifest
    ok, result = verify_manifest(path, ROOT)
    if args.write_evidence:
        evidence = ROOT / ".devtool" / "evidence" / "arc-final-gate"
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence / "content-seal.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
