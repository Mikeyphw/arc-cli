#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arc_cli.command_docs import console_script_mapping  # noqa: E402


def _render_project_scripts() -> str:
    lines = ["[project.scripts]"]
    lines.extend(f'{name} = "{target}"' for name, target in console_script_mapping().items())
    return "\n".join(lines)


def _sync_pyproject(text: str) -> str:
    rendered = _render_project_scripts()
    pattern = re.compile(r"(?ms)^\[project\.scripts\]\n.*?(?=^\[)")
    if not pattern.search(text):
        raise RuntimeError("pyproject.toml has no [project.scripts] section")
    return pattern.sub(rendered + "\n\n", text, count=1)


def _sync_devtool(text: str) -> str:
    names = ", ".join(f'"{name}"' for name in console_script_mapping())
    replacement = f"console_scripts = [{names}]"
    updated, count = re.subn(r"(?m)^console_scripts\s*=.*$", replacement, text, count=1)
    if count != 1:
        raise RuntimeError(".devtool.toml has no python.package console_scripts field")
    return updated


def main() -> int:
    ap = argparse.ArgumentParser(description="Synchronize Arc console scripts from the canonical alias registry")
    ap.add_argument("--check", action="store_true", help="fail when generated registry consumers drift")
    ap.add_argument("--root", type=Path, default=ROOT)
    ns = ap.parse_args()
    root = ns.root.resolve()
    targets = {
        root / "pyproject.toml": _sync_pyproject,
        root / ".devtool.toml": _sync_devtool,
    }
    stale: list[str] = []
    for path, transform in targets.items():
        current = path.read_text(encoding="utf-8")
        expected = transform(current)
        if current == expected:
            continue
        if ns.check:
            stale.append(str(path.relative_to(root)))
        else:
            path.write_text(expected, encoding="utf-8")
    if stale:
        print("alias registry consumers are stale:", file=sys.stderr)
        for item in stale:
            print(f"  {item}", file=sys.stderr)
        print("regenerate with: python3 scripts/sync_alias_registry.py", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
