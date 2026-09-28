#!/usr/bin/env python3
"""Fail closed when the checked-in Devtool repository contract drifts."""
from __future__ import annotations

from pathlib import Path
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / ".devtool.toml"


def fail(message: str) -> None:
    print(f"devtool contract: FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


data = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
project = data.get("project", {})
python_cfg = data.get("python", {})
workspace = data.get("workspace", {})
target = data.get("targets", {}).get("arc", {})
jobs = target.get("jobs", {})
workflows = target.get("workflows", {})
wrapper = data.get("wrapper", {})
commands = wrapper.get("commands", {})

if project.get("runner") != "python":
    fail("project.runner must remain python")
if project.get("python") != "python3":
    fail("project.python must remain host python3")
if workspace.get("default_target") != "arc":
    fail("workspace.default_target must remain arc")
if target.get("execution_environment") != "native-termux":
    fail("targets.arc.execution_environment must remain native-termux (host Python policy)")

# Devtool's built-in Python lint runner executes tools as modules from the managed
# venv. Ruff is provided by Termux as a host executable, so the Python runner must
# not try to import/install it. EXO command jobs below own Ruff execution.
if list(python_cfg.get("lint", [])):
    fail("python.lint must remain empty; system Ruff is executed by target-local jobs")
validator_dependencies = list(python_cfg.get("validator_dependencies", []))
if any(str(item).lower().startswith(("ruff", "pytest-cov", "coverage")) for item in validator_dependencies):
    fail("validator_dependencies must not install ruff/coverage into the Termux venv")
if validator_dependencies != ["build>=1.2"]:
    fail("validator_dependencies must remain ['build>=1.2']")
if python_cfg.get("coverage"):
    fail("python.coverage must stay unset unless pytest-cov is explicitly made Termux-safe")

expected_jobs = {
    "ruff-toolchain": ["python3", "scripts/check_system_ruff.py"],
    "ruff-check": ["ruff", "check", "src", "tests", "scripts"],
    "ruff-format-check": ["ruff", "format", "--check", "src", "tests", "scripts"],
    "ruff-format-fix": ["ruff", "format", "src", "tests", "scripts"],
    "ruff-fix": ["ruff", "check", "--fix", "src", "tests", "scripts"],
}
for name, command in expected_jobs.items():
    actual = jobs.get(name, {}).get("command")
    if list(actual or []) != command:
        fail(f"targets.arc.jobs.{name}.command drifted: {actual!r}")

for workflow in ("lint", "format", "fast", "quality", "release", "final_gate", "final_seal", "r08", "r09a", "r09b"):
    if workflow not in workflows:
        fail(f"required arc workflow missing: {workflow}")

r08 = workflows.get("r08", [])
r08_refs = {str(step.get("ref", "")) for step in r08 if isinstance(step, dict)}
for required_ref in (
    "job:machine-contract",
    "job:command-docs-contract",
    "job:completion-contract",
    "job:devtool-contract",
    "test:arc-r08-machine-capability-verification",
):
    if required_ref not in r08_refs:
        fail(f"r08 workflow missing required ref: {required_ref}")

if list(jobs.get("machine-contract", {}).get("command", []) or []) != ["python3", "scripts/check_machine_contract_r09b.py"]:
    fail("targets.arc.jobs.machine-contract must execute the R09B-compatible machine contract checker")

r09a = workflows.get("r09a", [])
r09a_refs = {str(step.get("ref", "")) for step in r09a if isinstance(step, dict)}
for required_ref in (
    "job:machine-contract",
    "job:provenance-contract",
    "job:command-docs-contract",
    "job:completion-contract",
    "job:devtool-contract",
    "test:arc-r09a-provenance-diff",
):
    if required_ref not in r09a_refs:
        fail(f"r09a workflow missing required ref: {required_ref}")

if list(jobs.get("provenance-contract", {}).get("command", []) or []) != ["python3", "scripts/check_provenance_contract.py"]:
    fail("targets.arc.jobs.provenance-contract must execute the canonical R09A provenance contract checker")

r09b = workflows.get("r09b", [])
r09b_refs = {str(step.get("ref", "")) for step in r09b if isinstance(step, dict)}
for required_ref in (
    "job:machine-contract",
    "job:remote-contract",
    "job:command-docs-contract",
    "job:completion-contract",
    "job:devtool-contract",
    "test:arc-r09b-remote-capability-publication",
):
    if required_ref not in r09b_refs:
        fail(f"r09b workflow missing required ref: {required_ref}")

if list(jobs.get("remote-contract", {}).get("command", []) or []) != ["python3", "scripts/check_remote_contract.py"]:
    fail("targets.arc.jobs.remote-contract must execute the canonical R09B remote contract checker")

declared_tests = data.get("test", [])
if not isinstance(declared_tests, list) or not any(
    isinstance(item, dict) and item.get("id") == "arc-r08-machine-capability-verification"
    for item in declared_tests
):
    fail("missing first-class arc-r08-machine-capability-verification test")

if not any(
    isinstance(item, dict) and item.get("id") == "arc-r09a-provenance-diff"
    for item in declared_tests
):
    fail("missing first-class arc-r09a-provenance-diff test")

if not any(
    isinstance(item, dict) and item.get("id") == "arc-r09b-remote-capability-publication"
    for item in declared_tests
):
    fail("missing first-class arc-r09b-remote-capability-publication test")

if wrapper.get("schema") != 1:
    fail("wrapper schema must remain 1")
if wrapper.get("default_command") != "check":
    fail("wrapper default command must remain check")
required = {
    "setup", "build", "test", "lint", "format", "check", "validate",
    "package", "clean", "fast", "quality", "release", "gate", "seal",
}
missing = sorted(required - set(commands))
if missing:
    fail(f"missing wrapper commands: {', '.join(missing)}")
if commands.get("lint", {}).get("workflow") != "lint":
    fail("wrapper lint command must use workflow='lint'")
if commands.get("format", {}).get("workflow") != "format":
    fail("wrapper format command must use workflow='format'")
if commands.get("check", {}).get("workflow") != "quality":
    fail("wrapper check command must use the authoritative quality workflow")
if commands.get("validate", {}).get("devtool") != "validate":
    fail("wrapper validate command must expose Devtool's native validate action")

if commands.get("gate", {}).get("workflow") != "final_gate":
    fail("wrapper gate command must use workflow='final_gate'")
if commands.get("seal", {}).get("workflow") != "final_seal":
    fail("wrapper seal command must use workflow='final_seal'")
if commands.get("r08", {}).get("workflow") != "r08":
    fail("wrapper r08 command must use workflow='r08'")

if commands.get("r09a", {}).get("workflow") != "r09a":
    fail("wrapper r09a command must use workflow='r09a'")

if commands.get("r09b", {}).get("workflow") != "r09b":
    fail("wrapper r09b command must use workflow='r09b'")

for name in ("devtoolw", "devtoolw.cmd"):
    path = ROOT / name
    if not path.is_file():
        fail(f"missing managed launcher {name}")
    text = path.read_text(encoding="utf-8", errors="replace")
    if "DEVTOOL_WRAPPER_LAUNCHER=1" not in text:
        fail(f"{name} is not a Devtool-managed launcher")
    if "wrapper exec" not in text:
        fail(f"{name} does not delegate to Devtool wrapper exec")

wrapper_doc = ROOT / "docs" / "WRAPPER.md"
if not wrapper_doc.is_file():
    fail("docs/WRAPPER.md is missing")
if not wrapper_doc.read_text(encoding="utf-8").strip():
    fail("docs/WRAPPER.md is empty")

# Detailed wrapper/documentation consistency is owned by `devtool wrapper seal`.
# Keep this repository check structural so harmless prose edits cannot invalidate
# the executable contract.
print("devtool contract: pass")
