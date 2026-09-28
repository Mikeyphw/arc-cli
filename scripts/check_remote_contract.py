#!/usr/bin/env python3
"""Deterministic ARC-R09B transport capability/publication contract."""
from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arc_cli.machine import load_schema, schema_names
from arc_cli.remote import REMOTE_CAPABILITY_SCHEMA


def fail(message: str) -> None:
    raise SystemExit(f"remote contract: FAIL: {message}")


def main() -> int:
    if "remote-capability-v1" not in schema_names():
        fail("remote capability schema is missing from the public registry")
    schema = load_schema("remote-capability-v1")
    if schema.get("$id") != "https://arc-cli.local/schema/remote-capability-v1.schema.json":
        fail("remote capability JSON Schema identity drift")
    if schema.get("properties", {}).get("schema", {}).get("const") != REMOTE_CAPABILITY_SCHEMA:
        fail("runtime/schema remote capability identity drift")
    json.dumps(schema, sort_keys=True)

    config = tomllib.loads((ROOT / ".devtool.toml").read_text(encoding="utf-8"))
    target = config["targets"]["arc"]
    jobs = target["jobs"]
    if list(jobs.get("machine-contract", {}).get("command", []) or []) != ["python3", "scripts/check_machine_contract_r09b.py"]:
        fail("machine-contract job must use the R09B-compatible checker")
    if list(jobs.get("devtool-contract", {}).get("command", []) or []) != ["python3", "scripts/check_devtool_contract_r09b.py"]:
        fail("devtool-contract job must use the R09B-compatible checker")
    workflow = target["workflows"].get("r09b", [])
    refs = {step.get("ref") for step in workflow if isinstance(step, dict)}
    for ref in ("job:machine-contract", "job:remote-contract", "job:devtool-contract", "test:arc-r09b-remote-capability-publication"):
        if ref not in refs:
            fail(f"r09b workflow missing {ref}")

    remote_man = (ROOT / "src" / "arc_cli" / "man" / "arc-remote.7").read_text(encoding="utf-8").replace("\\-", "-")
    for token in ("provider-dependent", "probe provenance", "same-parent"):
        if token not in remote_man:
            fail(f"arc-remote(7) missing R09B truth token: {token}")
    print("remote contract: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
