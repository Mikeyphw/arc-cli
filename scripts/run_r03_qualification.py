#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arc_cli.qualification import FAIL, run_qualification


def main() -> int:
    p = argparse.ArgumentParser(description="Run ARC-R03 backend/safety/resilience qualification matrix")
    p.add_argument("--output-dir", type=Path, default=Path(".devtool/evidence/arc-r03"))
    p.add_argument("--skip-large-manifest", action="store_true")
    args = p.parse_args()
    out = args.output_dir
    result = run_qualification(out / "qualification.json", include_large_manifest=not args.skip_large_manifest)
    out.mkdir(parents=True, exist_ok=True)
    split = {
        "capability-matrix.json": {"schema_version": 1, "capability_matrix": result["capability_matrix"], "backend_operation_matrix": result["backend_operation_matrix"]},
        "backend-matrix.json": {"schema_version": 1, "backend_matrix": result["backend_matrix"], "mutation_matrix": result["mutation_matrix"]},
        "safety-matrix.json": {"schema_version": 1, "safety_matrix": result["safety_matrix"]},
        "resilience-matrix.json": {"schema_version": 1, "resilience_matrix": result["resilience_matrix"]},
        "summary.json": {"schema_version": 1, "summary": result["summary"]},
    }
    for name, data in split.items():
        (out / name).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], sort_keys=True))
    if result["summary"].get("failures"):
        for failure in result["summary"]["failures"]:
            print(
                "R03 FAIL: " + json.dumps(failure, ensure_ascii=False, sort_keys=True),
                file=sys.stderr,
            )
    return 1 if result["summary"]["status"] == FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
