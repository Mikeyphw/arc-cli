#!/usr/bin/env python3
from __future__ import annotations

import ast
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def fail(message: str) -> None:
    raise SystemExit(f"COMP-X01 contract failure: {message}")


project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
if project["project"].get("dynamic") != ["version"] or "version" in project["project"]:
    fail("pyproject must use a dynamic version")
if project["tool"]["setuptools"]["dynamic"]["version"].get("attr") != "arc_cli._version.VERSION":
    fail("setuptools version authority is not arc_cli._version.VERSION")

version_mod = ast.parse((ROOT / "src" / "arc_cli" / "_version.py").read_text(encoding="utf-8"))
values = [
    node.value.value
    for node in version_mod.body
    if isinstance(node, ast.Assign)
    for target in node.targets
    if isinstance(target, ast.Name) and target.id == "VERSION"
    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
]
if values != ["0.2.0"]:
    fail(f"expected single VERSION='0.2.0', got {values!r}")

init_text = (ROOT / "src" / "arc_cli" / "__init__.py").read_text(encoding="utf-8")
if "from ._version import VERSION as __version__" not in init_text:
    fail("arc_cli.__version__ does not project the single version authority")
cli_text = (ROOT / "src" / "arc_cli" / "cli.py").read_text(encoding="utf-8")
if 'version=f"arc {__version__}"' not in cli_text or 'version="arc 0.1.0"' in cli_text:
    fail("CLI --version is not derived from __version__")

proc = subprocess.run(
    [sys.executable, "-m", "arc_cli", "--version"],
    cwd=ROOT,
    env={"PYTHONPATH": str(ROOT / "src")},
    text=True,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    check=False,
)
if proc.returncode != 0 or proc.stdout.strip() != "arc 0.2.0":
    fail(f"runtime version mismatch: rc={proc.returncode} stdout={proc.stdout!r} stderr={proc.stderr!r}")

lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
local = [item for item in lock.get("package", []) if item.get("name") == "arc-cli"]
if len(local) != 1 or local[0].get("version") != "0.2.0":
    fail("uv.lock does not mirror the current local package version")

from arc_cli.composition import CompositionSource, resolve_merge_output
from arc_cli.errors import UsageError
from arc_cli.formats import parse_format

same = [CompositionSource(Path("a.zip"), parse_format("zip")), CompositionSource(Path("b.zip"), parse_format("zip"))]
result = resolve_merge_output(same)
if result.path != Path("a.merged.zip") or result.format.canonical != "zip" or result.format_source != "uniform-inputs":
    fail("uniform merge output inference contract drifted")

mixed = [CompositionSource(Path("a.tar"), parse_format("tar")), CompositionSource(Path("b.zip"), parse_format("zip"))]
try:
    resolve_merge_output(mixed)
except UsageError:
    pass
else:
    fail("mixed formats silently selected an output format")

cfg = tomllib.loads((ROOT / ".devtool.toml").read_text(encoding="utf-8"))
if "comp_x01" not in cfg.get("test_profiles", {}):
    fail("missing comp_x01 test profile")
tests = {item["id"]: item for item in cfg.get("test", [])}
if "arc-comp-x01-version-composition-foundation" not in tests:
    fail("missing first-class COMP-X01 test id")
jobs = cfg["targets"]["arc"]["jobs"]
if jobs.get("comp-x01-contract", {}).get("command") != ["python3", "scripts/check_comp_x01_contract.py"]:
    fail("missing COMP-X01 contract job")
workflow = cfg["targets"]["arc"]["workflows"].get("comp_x01", [])
refs = {step.get("ref") for step in workflow}
if "job:comp-x01-contract" not in refs or "test:arc-comp-x01-version-composition-foundation" not in refs:
    fail("COMP-X01 workflow does not own contract + tests")
if cfg["wrapper"]["commands"].get("comp-x01", {}).get("workflow") != "comp_x01":
    fail("missing ./devtoolw comp-x01 ownership")

roadmap = (ROOT / "docs" / "ARC-NEXT-ROADMAP.md").read_text(encoding="utf-8")
for marker in ("COMP-X01", "COMP-X02", "COMP-X03", "COMP-G1"):
    if marker not in roadmap:
        fail(f"roadmap missing {marker}")

print("COMP-X01 contract: PASS")
