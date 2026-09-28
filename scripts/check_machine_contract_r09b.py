#!/usr/bin/env python3
"""Deterministic non-test contract for Arc's public R08 machine surfaces."""
from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arc_cli.capabilities import BACKEND_PROFILES, VerificationLevel, verification_rank
from arc_cli.machine import load_schema, schema_names

EXPECTED = {"machine-v1", "backend-capability-v1", "verification-evidence-v1", "logical-fingerprint-v1", "archive-diff-v1", "remote-capability-v1", "batch-input-v1", "config-inspection-v1"}


def main() -> int:
    names = set(schema_names())
    if names != EXPECTED:
        raise SystemExit(f"machine schema registry drift: {sorted(names)} != {sorted(EXPECTED)}")
    for name in sorted(names):
        schema = load_schema(name)
        if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
            raise SystemExit(f"{name}: unsupported JSON Schema dialect")
        if schema.get("type") != "object" or not schema.get("required"):
            raise SystemExit(f"{name}: incomplete schema contract")
        json.dumps(schema, sort_keys=True)

    for binary, profile in BACKEND_PROFILES.items():
        payload = profile.to_dict()
        if payload["binary"] != binary or payload["schema_version"] != 1:
            raise SystemExit(f"{binary}: capability profile identity drift")
        levels = list(profile.verification_levels)
        if VerificationLevel.NONE not in levels:
            raise SystemExit(f"{binary}: verification profile must include none")
        if verification_rank(profile.maximum_verification) != max(verification_rank(level) for level in levels):
            raise SystemExit(f"{binary}: verification maximum drift")

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_data = project["tool"]["setuptools"]["package-data"]["arc_cli"]
    if "schemas/*.json" not in package_data:
        raise SystemExit("pyproject package-data does not ship Arc machine schemas")
    manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    if "recursive-include src/arc_cli/schemas *.json" not in manifest:
        raise SystemExit("sdist manifest does not ship Arc machine schemas")
    print("machine contract: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
