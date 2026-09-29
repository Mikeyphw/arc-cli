#!/usr/bin/env python3
"""Fail closed when ARC R01-R12 final campaign seal wiring drifts."""
from __future__ import annotations
import ast
import json
from pathlib import Path
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]

def fail(msg: str) -> None:
    print(f"final campaign seal contract: FAIL: {msg}", file=sys.stderr)
    raise SystemExit(1)

seal = json.loads((ROOT / "release" / "ARC-FINAL-SEAL.json").read_text(encoding="utf-8"))
if seal.get("schema_version") != 7 or seal.get("seal_id") != "ARC-R01-R12-FINAL":
    fail("static release seal must be schema-7 ARC-R01-R12-FINAL")
if seal.get("qualified_gate_commit_expected_prefix") != "95981f7" or seal.get("qualified_gate_tests") != 378:
    fail("qualified R12 Gate identity drifted")

# Every current test file must appear exactly once in run_final_gate_tests.SUITES.
mod = ast.parse((ROOT / "scripts" / "run_final_gate_tests.py").read_text(encoding="utf-8"))
suites = None
for node in mod.body:
    if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None) == "SUITES":
        suites = ast.literal_eval(node.value)
        break
if suites is None:
    fail("run_final_gate_tests.py has no literal SUITES declaration")
covered = [name for _, names in suites for name in names]
covered_files = [name.split("::", 1)[0] for name in covered]
actual = sorted(str(p.relative_to(ROOT)).replace("\\", "/") for p in (ROOT / "tests").glob("test_*.py"))
if sorted(set(covered_files)) != actual:
    fail(f"final gate file coverage drifted: files={len(set(covered_files))} actual={len(actual)}")
for path in actual:
    count = covered_files.count(path)
    if path == "tests/test_r04_remote_transport.py":
        continue
    if count != 1:
        fail(f"final gate file {path} must appear exactly once, got {count}")
r04_selectors = [name.split("::", 1)[1] for name in covered if name.startswith("tests/test_r04_remote_transport.py::")]
r04_mod = ast.parse((ROOT / "tests" / "test_r04_remote_transport.py").read_text(encoding="utf-8"))
r04_actual = sorted(node.name for node in r04_mod.body if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"))
if sorted(r04_selectors) != r04_actual or len(r04_selectors) != len(set(r04_selectors)):
    fail(f"R04 split selectors drifted: selectors={len(r04_selectors)} actual={len(r04_actual)}")

# Seal scope must include newer package contracts.
sys.path.insert(0, str(ROOT / "scripts"))
from arc_final_seal import authoritative_paths  # noqa: E402
paths = set(authoritative_paths(ROOT))
for required in (
    "MANIFEST.in",
    "src/arc_cli/schemas/machine-v1.schema.json",
    "src/arc_cli/schemas/benchmark-v1.schema.json",
    "src/arc_cli/man/arc.1",
    "src/arc_cli/man/arc-config.5",
    "src/arc_cli/man/arc-remote.7",
):
    if required not in paths:
        fail(f"authoritative seal scope missing {required}")

cfg = tomllib.loads((ROOT / ".devtool.toml").read_text(encoding="utf-8"))
jobs = cfg["targets"]["arc"]["jobs"]
workflows = cfg["targets"]["arc"]["workflows"]
commands = cfg["wrapper"]["commands"]
if jobs.get("final-gate", {}).get("command") != ["python3", "scripts/run_final_gate_tests.py"]:
    fail("final-gate job command drifted")
if jobs.get("final-seal-integrity", {}).get("command") != ["python3", "scripts/verify_final_campaign_seal.py", "--write-evidence"]:
    fail("final-seal-integrity job missing or drifted")
if "final_gate" not in workflows or "final_seal" not in workflows:
    fail("final gate/seal workflows missing")
refs = [step.get("ref") for step in workflows["final_seal"] if isinstance(step, dict)]
for ref in ("job:final-gate", "job:wrapper-seal", "job:content-seal", "job:final-seal-verdict", "job:final-seal-integrity"):
    if ref not in refs:
        fail(f"final_seal missing {ref}")
if commands.get("gate", {}).get("workflow") != "final_gate" or commands.get("seal", {}).get("workflow") != "final_seal":
    fail("wrapper gate/seal workflow wiring drifted")
print("final campaign seal contract: pass")
