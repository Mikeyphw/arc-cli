from __future__ import annotations

import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import tomllib
import urllib.parse
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable

from . import __version__
from .backends import backend_inventory
from .command_docs import alias_specs, console_script_mapping
from .completion import zsh_completion
from .config import config_path, load_config, load_config_result
from .manual import generated_pages


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    id: str
    status: str
    summary: str
    detail: str = ""
    repairable: bool = False

    def as_json(self) -> dict[str, object]:
        return asdict(self)


def _distribution():
    try:
        return importlib.metadata.distribution("arc-cli")
    except importlib.metadata.PackageNotFoundError:
        return None


def _direct_url_source(dist) -> tuple[Path | None, bool | None]:
    if dist is None:
        return None, None
    try:
        raw = dist.read_text("direct_url.json")
        if not raw:
            return None, None
        payload = json.loads(raw)
        url = str(payload.get("url") or "")
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "file":
            return None, bool(payload.get("dir_info", {}).get("editable"))
        path = Path(urllib.parse.unquote(parsed.path)).resolve()
        return path, bool(payload.get("dir_info", {}).get("editable"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None, None


def _looks_like_source_root(path: Path) -> bool:
    pyproject = path / "pyproject.toml"
    if not pyproject.is_file():
        return False
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return False
    return data.get("project", {}).get("name") == "arc-cli"


def discover_source_root(explicit: str | Path | None = None) -> tuple[Path | None, bool | None]:
    if explicit is not None:
        path = Path(explicit).expanduser().resolve()
        return (path, None) if _looks_like_source_root(path) else (None, None)
    dist = _distribution()
    source, editable = _direct_url_source(dist)
    if source is not None and _looks_like_source_root(source):
        return source, editable
    current = Path(__file__).resolve()
    for parent in current.parents:
        if _looks_like_source_root(parent):
            return parent, None
    return None, editable


def installed_entry_points() -> dict[str, str]:
    dist = _distribution()
    if dist is None:
        return {}
    return {
        ep.name: ep.value
        for ep in dist.entry_points
        if ep.group == "console_scripts" and (ep.name == "arc" or ep.name.startswith("arc"))
    }


def alias_status_rows(which: Callable[[str], str | None] = shutil.which) -> list[dict[str, object]]:
    declared = installed_entry_points()
    rows: list[dict[str, object]] = []
    for spec in alias_specs():
        path = which(spec.executable)
        rows.append(
            {
                "executable": spec.executable,
                "command": spec.command,
                "expected_target": "arc_cli.cli:main",
                "metadata_declared": declared.get(spec.executable) == "arc_cli.cli:main",
                "path": path,
                "available": bool(path),
            }
        )
    return rows


def _config_check(result=None) -> DoctorCheck:
    result = result or load_config_result()
    if not result.exists:
        return DoctorCheck("config", "pass", "configuration", f"no config file; defaults active ({result.path})")
    errors = [issue for issue in result.issues if issue.severity == "error"]
    warnings = [issue for issue in result.issues if issue.severity == "warning"]
    if errors:
        detail = "; ".join(issue.message for issue in errors)
        return DoctorCheck("config", "fail", "configuration", f"{result.path}: {detail}")
    if warnings:
        detail = "; ".join(issue.message for issue in warnings)
        return DoctorCheck("config", "warn", "configuration", f"{result.path}: {detail}")
    return DoctorCheck("config", "pass", "configuration", f"parsed and validated {result.path}")


def _generated_surface_checks(root: Path | None) -> list[DoctorCheck]:
    if root is None:
        return [DoctorCheck("generated-surfaces", "pass", "generated surfaces", "source checkout not resolved; package resources only")]
    stale: list[str] = []
    for page, content in generated_pages():
        path = root / "src" / "arc_cli" / "man" / page.filename
        try:
            if path.read_text(encoding="utf-8") != content:
                stale.append(str(path.relative_to(root)))
        except OSError:
            stale.append(str(path.relative_to(root)))
    completion = root / "completions" / "_arc"
    try:
        if completion.read_text(encoding="utf-8") != zsh_completion():
            stale.append("completions/_arc")
    except OSError:
        stale.append("completions/_arc")
    if stale:
        return [DoctorCheck("generated-surfaces", "warn", "generated surfaces", "stale: " + ", ".join(stale), True)]
    return [DoctorCheck("generated-surfaces", "pass", "generated surfaces", "manual pages and completion match source metadata")]


def collect_doctor_report(
    *,
    source: str | Path | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> dict[str, object]:
    checks: list[DoctorCheck] = []
    dist = _distribution()
    source_root, editable = discover_source_root(source)
    dist_version = dist.version if dist is not None else None
    if dist is None:
        checks.append(DoctorCheck("package", "warn", "installed package metadata", "arc-cli distribution metadata not found", True))
    else:
        detail = f"version {dist_version}"
        if editable is not None:
            detail += f"; editable={'yes' if editable else 'no'}"
        checks.append(DoctorCheck("package", "pass", "installed package metadata", detail))

    if source is not None and source_root is None:
        checks.append(DoctorCheck("source", "fail", "source checkout", f"not an arc-cli checkout: {Path(source).expanduser()}"))
    elif source_root is None:
        checks.append(DoctorCheck("source", "warn", "source checkout", "could not resolve an arc-cli checkout", True))
    else:
        checks.append(DoctorCheck("source", "pass", "source checkout", str(source_root)))

    expected = console_script_mapping()
    declared = installed_entry_points()
    missing_metadata = sorted(name for name, target in expected.items() if declared.get(name) != target)
    if missing_metadata:
        checks.append(DoctorCheck("entry-points", "warn", "console-script metadata", "missing/stale: " + ", ".join(missing_metadata), True))
    else:
        checks.append(DoctorCheck("entry-points", "pass", "console-script metadata", f"{len(expected)} expected entry points declared"))

    missing_path = sorted(name for name in expected if not which(name))
    if missing_path:
        checks.append(DoctorCheck("path-aliases", "warn", "executables on PATH", "declared but unavailable: " + ", ".join(missing_path), True))
    else:
        checks.append(DoctorCheck("path-aliases", "pass", "executables on PATH", f"all {len(expected)} console scripts resolve"))

    config_result = load_config_result()
    checks.append(_config_check(config_result))

    # Once an invalid configuration has been reported, downstream doctor
    # checks must remain diagnostic rather than crashing on malformed tables.
    config = config_result.data if config_result.valid else {}
    inventory = backend_inventory(config)
    usable_roles = 0
    for role in inventory:
        if any(bool(candidate.get("installed")) for candidate in role["candidates"]):
            usable_roles += 1
    backend_status = "pass" if usable_roles else "warn"
    checks.append(DoctorCheck("backends", backend_status, "native backends", f"{usable_roles}/{len(inventory)} backend roles have an installed candidate"))

    tools = {name: which(name) for name in ("ssh", "rclone", "fzf", "rg", "yazi", "man")}
    present = sorted(name for name, path in tools.items() if path)
    absent = sorted(name for name, path in tools.items() if not path)
    checks.append(DoctorCheck("optional-tools", "pass", "optional integrations", f"present: {', '.join(present) or 'none'}; absent: {', '.join(absent) or 'none'}"))
    checks.extend(_generated_surface_checks(source_root))

    statuses = [c.status for c in checks]
    summary = {
        "pass": statuses.count("pass"),
        "warn": statuses.count("warn"),
        "fail": statuses.count("fail"),
    }
    return {
        "schema_version": 1,
        "arc_version": __version__,
        "installed_version": dist_version,
        "python": {"executable": sys.executable, "version": sys.version.split()[0]},
        "source_root": str(source_root) if source_root else None,
        "editable": editable,
        "checks": [c.as_json() for c in checks],
        "summary": summary,
        "healthy": summary["fail"] == 0 and summary["warn"] == 0,
    }


def run_refresh(source_root: Path) -> subprocess.CompletedProcess[str]:
    script = source_root / "scripts" / "refresh_dev_install.py"
    if not script.is_file():
        raise FileNotFoundError(f"missing refresh helper: {script}")
    return subprocess.run(
        [sys.executable, str(script), "--source", str(source_root), "--json"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def fix_and_recheck(*, source: str | Path | None = None) -> tuple[dict[str, object], dict[str, object]]:
    source_root, _ = discover_source_root(source)
    before = collect_doctor_report(source=source)
    if source_root is None:
        return before, {"attempted": False, "ok": False, "detail": "no source checkout available"}
    proc = run_refresh(source_root)
    result = {
        "attempted": True,
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }
    after = collect_doctor_report(source=source_root)
    after["fix"] = result
    return after, result
