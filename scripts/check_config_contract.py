#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arc_cli.command_docs import COMMAND_DOCS
from arc_cli.completion import COMMAND_OPTIONS
from arc_cli.config import CONFIG_INSPECTION_SCHEMA, configuration_keys
from arc_cli.machine import load_schema, schema_names
from arc_cli.manual import PAGES


def fail(message: str) -> None:
    raise SystemExit(f"config contract: FAIL: {message}")


def main() -> int:
    if "config" not in COMMAND_DOCS:
        fail("config command missing from command identity registry")
    if "config" not in COMMAND_OPTIONS:
        fail("config command missing from completion grammar")
    required_options = {"--effective", "--profile", "--cli", "--json"}
    if not required_options <= set(COMMAND_OPTIONS["config"]):
        fail("config completion options are incomplete")
    if ("arc-config", 1) not in PAGES or ("arc-config", 5) not in PAGES:
        fail("config command/reference manual pages must both exist")
    if "config-inspection-v1" not in schema_names():
        fail("config-inspection-v1 missing from schema registry")
    schema = load_schema("config-inspection-v1")
    if schema.get("$id") != CONFIG_INSPECTION_SCHEMA:
        fail("config inspection schema id drift")
    json.dumps(schema, sort_keys=True)
    required_keys = {"ui.progress", "create.level", "create.threads", "ui.show_native", "ui.native_command_style", "remote.execution", "command.backend", "backends.zstd"}
    if not required_keys <= set(configuration_keys()):
        fail("effective configuration key registry is incomplete")

    devtool = tomllib.loads((ROOT / ".devtool.toml").read_text(encoding="utf-8"))
    tests = devtool.get("test", [])
    if not any(isinstance(row, dict) and row.get("id") == "arc-r11-config-provenance" for row in tests):
        fail("first-class arc-r11-config-provenance test missing")
    target = devtool.get("targets", {}).get("arc", {})
    jobs = target.get("jobs", {})
    workflows = target.get("workflows", {})
    if list(jobs.get("config-contract", {}).get("command", []) or []) != ["python3", "scripts/check_config_contract.py"]:
        fail("config-contract job does not own this checker")
    refs = {str(step.get("ref", "")) for step in workflows.get("r11", []) if isinstance(step, dict)}
    for ref in ("job:machine-contract", "job:config-contract", "job:command-docs-contract", "job:completion-contract", "job:devtool-contract", "test:arc-r11-config-provenance"):
        if ref not in refs:
            fail(f"r11 workflow missing {ref}")
    wrapper = devtool.get("wrapper", {}).get("commands", {})
    if wrapper.get("r11", {}).get("workflow") != "r11":
        fail("wrapper r11 command missing")

    audit = (ROOT / "docs" / "ARC-R11-AUDIT.md").read_text(encoding="utf-8")
    for phrase in ("built-in → config → environment → profile → CLI", "Invalid config truth", "Doctor parity", "Shared runtime authority"):
        if phrase not in audit:
            fail(f"R11 audit missing promise marker: {phrase}")
    roadmap = (ROOT / "docs" / "ARC-NEXT-ROADMAP.md").read_text(encoding="utf-8")
    if "Separate R11 gate" not in roadmap or "keep R11 standalone" not in roadmap:
        fail("R11 roadmap boundary/gate markers missing")

    print("config contract: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
