#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arc_cli.command_docs import COMMAND_DOCS
from arc_cli.machine import load_schema, schema_names

REQUIRED_SCHEMAS = {"format-recommendation-v1", "benchmark-v1", "diagnostics-bundle-v1"}


def fail(message: str) -> None:
    raise SystemExit(f"r12 contract: FAIL: {message}")


def main() -> int:
    if not REQUIRED_SCHEMAS <= set(schema_names()):
        fail("R12 schema registry incomplete")
    loaded = {}
    for name in REQUIRED_SCHEMAS:
        schema = load_schema(name)
        loaded[name] = schema
        if schema.get("$id") != "arc." + name.replace("-v1", "/v1"):
            fail(f"{name}: schema identity drift")
        json.dumps(schema, sort_keys=True)
    recommendation_required = set(loaded["format-recommendation-v1"].get("properties", {}).get("candidates", {}).get("items", {}).get("properties", {}).get("tradeoffs", {}).get("required", []))
    if {"metadata_preservation", "streaming_create", "encryption_write", "multipart", "random_access_container", "single_stream"} - recommendation_required:
        fail("format recommendation schema does not require promised tradeoff evidence")
    benchmark_defs = loaded["benchmark-v1"].get("$defs", {})
    iteration_required = set(benchmark_defs.get("iteration", {}).get("required", []))
    if {"encoded_to_input_ratio", "compression_ratio", "space_savings_fraction", "encode", "decode", "verified_roundtrip"} - iteration_required:
        fail("benchmark schema does not require promised measurement/ratio evidence")
    diagnostic_props = loaded["diagnostics-bundle-v1"].get("properties", {})
    if diagnostic_props.get("archive_contents_included", {}).get("const") is not False or diagnostic_props.get("network_probe_performed", {}).get("const") is not False:
        fail("diagnostics schema must fail closed on archive-content/network claims")
    for command in ("benchmark", "diagnostics", "formats"):
        if command not in COMMAND_DOCS:
            fail(f"missing command metadata: {command}")
    advisory = (ROOT / "src/arc_cli/advisory.py").read_text(encoding="utf-8")
    for needle in (
        "selection_policy", "selection\": None", "host_specific", "verified_roundtrip",
        "archive_contents_included", "network_probe_performed", "redact_payload",
    ):
        if needle not in advisory:
            fail(f"advisory implementation missing contract marker: {needle}")
    if "remote_capabilities(" in advisory:
        fail("diagnostics/recommendation module must not perform live remote capability probes")
    config = tomllib.loads((ROOT / ".devtool.toml").read_text(encoding="utf-8"))
    profiles = config.get("test_profiles", {})
    if "r12" not in profiles:
        fail("missing r12 test profile")
    tests = config.get("test", [])
    if not any(isinstance(item, dict) and item.get("id") == "arc-r12-advisory-benchmark-diagnostics" for item in tests):
        fail("missing first-class R12 test")
    jobs = config.get("targets", {}).get("arc", {}).get("jobs", {})
    if list(jobs.get("r12-contract", {}).get("command", []) or []) != ["python3", "scripts/check_r12_contract.py"]:
        fail("missing canonical r12-contract job")
    workflow = config.get("targets", {}).get("arc", {}).get("workflows", {}).get("r12", [])
    refs = {step.get("ref") for step in workflow if isinstance(step, dict)}
    required_refs = {"job:machine-contract", "job:r12-contract", "job:command-docs-contract", "job:completion-contract", "job:devtool-contract", "test:arc-r12-advisory-benchmark-diagnostics"}
    if not required_refs <= refs:
        fail("r12 workflow is missing required first-class refs")
    wrapper = config.get("wrapper", {}).get("commands", {}).get("r12", {})
    if wrapper.get("workflow") != "r12":
        fail("wrapper r12 command must execute the r12 workflow")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for page in ("arc-benchmark.1", "arc-diagnostics.1"):
        if page not in pyproject:
            fail(f"package data missing {page}")
    roadmap = (ROOT / "docs/ARC-NEXT-ROADMAP.md").read_text(encoding="utf-8")
    for promise in ("formats recommend", "host-specific", "diagnostics bundle", "no network", "archive contents"):
        if promise.lower() not in roadmap.lower():
            fail(f"roadmap missing R12 promise: {promise}")
    print("r12 contract: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
