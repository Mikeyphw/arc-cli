#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arc_cli.batch import BATCH_SCHEMA_ID, MAX_OPERATIONS
from arc_cli.cli import parser
from arc_cli.command_docs import COMMAND_DOCS
from arc_cli.completion import COMMAND_OPTIONS
from arc_cli.machine import load_schema, schema_names


def fail(message: str) -> None:
    raise SystemExit(f"batch contract: FAIL: {message}")


def main() -> int:
    if "batch-input-v1" not in schema_names():
        fail("batch-input-v1 missing from public schema registry")
    schema = load_schema("batch-input-v1")
    if schema.get("$id") != BATCH_SCHEMA_ID or schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        fail("batch input schema identity/dialect drift")
    operations = schema.get("properties", {}).get("operations", {})
    if operations.get("maxItems") != MAX_OPERATIONS:
        fail("runtime/schema operation limit drift")
    argv_items = operations.get("items", {}).get("properties", {}).get("argv", {}).get("items", {})
    if argv_items.get("pattern") != "^[^\\u0000]*$":
        fail("schema must reject NUL argv tokens")
    if "batch" not in COMMAND_DOCS or "batch" not in COMMAND_OPTIONS:
        fail("batch missing from command/completion registries")
    parsed = parser().parse_args(["batch", "jobs.json", "--validate-only", "--json=v1"])
    if parsed.command != "batch" or parsed.input != "jobs.json" or not parsed.validate_only or parsed.json != "v1":
        fail("batch parser contract drift")

    cfg = tomllib.loads((ROOT / ".devtool.toml").read_text(encoding="utf-8"))
    target = cfg.get("targets", {}).get("arc", {})
    jobs = target.get("jobs", {})
    if list(jobs.get("batch-contract", {}).get("command", []) or []) != ["python3", "scripts/check_batch_contract.py"]:
        fail("batch-contract Devtool job missing")
    workflow = target.get("workflows", {}).get("r10b", [])
    refs = {node.get("ref") for node in workflow if isinstance(node, dict)}
    if "job:batch-contract" not in refs or "test:arc-r10b-machine-batch" not in refs:
        fail("r10b workflow does not own batch contract + test")

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    man1 = project["tool"]["setuptools"]["data-files"]["share/man/man1"]
    if "src/arc_cli/man/arc-batch.1" not in man1:
        fail("arc-batch.1 missing from installed manpage data")
    json.dumps(schema, sort_keys=True)
    print("batch contract: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
