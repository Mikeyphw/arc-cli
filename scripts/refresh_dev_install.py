#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path


def _is_arc_root(path: Path) -> bool:
    try:
        data = tomllib.loads((path / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return False
    return data.get("project", {}).get("name") == "arc-cli"


def _declared_console_scripts(root: Path) -> tuple[str, ...]:
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = data.get("project", {}).get("scripts", {})
    if not isinstance(scripts, dict) or not scripts:
        raise RuntimeError("pyproject.toml does not declare [project.scripts]")
    return tuple(sorted(str(name) for name in scripts))


def main() -> int:
    ap = argparse.ArgumentParser(description="Refresh the active editable arc-cli development install")
    ap.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-generate", action="store_true", help="require generated surfaces to be current without rewriting them")
    ns = ap.parse_args()
    root = ns.source.expanduser().resolve()
    if not _is_arc_root(root):
        print(f"not an arc-cli source checkout: {root}", file=sys.stderr)
        return 2

    generators = [
        [sys.executable, str(root / "scripts" / "sync_alias_registry.py"), "--root", str(root)],
        [sys.executable, str(root / "scripts" / "generate_command_docs.py")],
    ]
    checks = [
        [sys.executable, str(root / "scripts" / "sync_alias_registry.py"), "--check", "--root", str(root)],
        [sys.executable, str(root / "scripts" / "generate_command_docs.py"), "--check"],
        [sys.executable, str(root / "scripts" / "check_completion_contract.py")],
    ]
    install = [sys.executable, "-m", "pip", "install", "--no-build-isolation", "--no-deps", "-e", str(root)]
    if ns.dry_run:
        payload = {"schema_version": 1, "source": str(root), "generators": [] if ns.no_generate else generators, "checks": checks, "install": install, "dry_run": True}
        print(json.dumps(payload, sort_keys=True) if ns.json else " ".join(install))
        return 0

    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if not ns.no_generate:
        for command in generators:
            proc = subprocess.run(command, cwd=root, env=env, check=False)
            if proc.returncode:
                return proc.returncode

        sys.path.insert(0, str(root / "src"))
        from arc_cli.completion import zsh_completion

        (root / "completions" / "_arc").write_text(zsh_completion(), encoding="utf-8")

    for command in checks:
        proc = subprocess.run(command, cwd=root, env=env, check=False)
        if proc.returncode:
            return proc.returncode
    expected_scripts = _declared_console_scripts(root)
    proc = subprocess.run(install, cwd=root, env=env, check=False)
    if proc.returncode:
        return proc.returncode

    missing = sorted(name for name in expected_scripts if not shutil.which(name))
    payload = {
        "schema_version": 1,
        "source": str(root),
        "editable_install_refreshed": True,
        "console_scripts": len(expected_scripts),
        "missing_on_path": missing,
        "ok": not missing,
    }
    if ns.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        if missing:
            print("editable install refreshed, but executables remain missing on PATH: " + ", ".join(missing), file=sys.stderr)
        else:
            print(f"editable install refreshed; {len(expected_scripts)} Arc console scripts resolve on PATH")
    return 0 if not missing else 1


if __name__ == "__main__":
    raise SystemExit(main())
