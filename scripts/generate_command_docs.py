#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from arc_cli.manual import generated_pages, markdown_reference  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate Arc manpages and Markdown command reference")
    ap.add_argument("--check", action="store_true", help="fail when committed generated documentation is stale")
    ns = ap.parse_args()

    expected: dict[Path, str] = {
        ROOT / "src" / "arc_cli" / "man" / page.filename: content
        for page, content in generated_pages()
    }
    expected[ROOT / "docs" / "COMMAND_REFERENCE.md"] = markdown_reference()

    stale: list[str] = []
    for path, content in expected.items():
        if ns.check:
            try:
                current = path.read_text(encoding="utf-8")
            except OSError:
                current = ""
            if current != content:
                stale.append(str(path.relative_to(ROOT)))
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    # Generated manpages are a closed inventory. Remove stale source pages on
    # generation, but never mutate during --check.
    man_root = ROOT / "src" / "arc_cli" / "man"
    expected_man = {path for path in expected if path.parent == man_root}
    if man_root.exists():
        extras = sorted(path for path in man_root.iterdir() if path.is_file() and path not in expected_man)
        if ns.check:
            stale.extend(str(path.relative_to(ROOT)) + " (unexpected generated page)" for path in extras)
        else:
            for path in extras:
                path.unlink()

    if stale:
        print("generated command documentation is stale:", file=sys.stderr)
        for item in stale:
            print(f"  {item}", file=sys.stderr)
        print("regenerate with: python3 scripts/generate_command_docs.py", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
