#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import sysconfig
import tomllib
from pathlib import Path
from urllib.parse import quote


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


def _purelib_path() -> Path:
    prefix = os.environ.get("PIP_PREFIX", "").strip()
    if prefix:
        root = str(Path(prefix).expanduser().resolve())
        return Path(sysconfig.get_path("purelib", vars={"base": root, "platbase": root})).resolve()
    return Path(sysconfig.get_path("purelib")).resolve()


def _record_digest(data: bytes) -> str:
    encoded = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode("ascii").rstrip("=")
    return f"sha256={encoded}"


def _rewrite_record(dist_info: Path, changed: dict[Path, bytes]) -> None:
    record = dist_info / "RECORD"
    if not record.is_file():
        return
    rows = list(csv.reader(record.read_text(encoding="utf-8").splitlines()))
    by_rel: dict[str, bytes] = {}
    purelib = dist_info.parent
    for path, data in changed.items():
        try:
            rel = path.resolve().relative_to(purelib.resolve()).as_posix()
        except ValueError:
            continue
        by_rel[rel] = data
    output: list[list[str]] = []
    seen: set[str] = set()
    for row in rows:
        if not row:
            continue
        while len(row) < 3:
            row.append("")
        rel = row[0]
        if rel in by_rel:
            data = by_rel[rel]
            row[1] = _record_digest(data)
            row[2] = str(len(data))
            seen.add(rel)
        output.append(row[:3])
    for rel, data in sorted(by_rel.items()):
        if rel not in seen:
            output.append([rel, _record_digest(data), str(len(data))])
    with record.open("w", encoding="utf-8", newline="") as fh:
        csv.writer(fh, lineterminator="\n").writerows(output)


def _rebind_editable_install(candidate: Path, stable: Path) -> dict[str, object]:
    """Rebind a just-installed candidate editable to the persistent checkout.

    Devtool executes after_commit finalizers before promoting its isolated
    transaction worktree. Installing the candidate is required to materialize
    newly declared console scripts and fresh distribution metadata, but leaving
    the editable mapping pointed at that worktree would break after cleanup.
    Setuptools' current Arc editable layout is a .pth source mapping plus
    direct_url.json; update both to the stable checkout and refresh RECORD.
    """
    candidate = candidate.resolve()
    stable = stable.resolve()
    if candidate == stable:
        return {"rebound": False, "purelib": str(_purelib_path()), "pth_files": 0, "direct_url_files": 0}

    purelib = _purelib_path()
    if not purelib.is_dir():
        raise RuntimeError(f"editable install site-packages directory does not exist: {purelib}")

    candidate_src = str((candidate / "src").resolve())
    stable_src = str((stable / "src").resolve())
    changed: dict[Path, bytes] = {}
    pth_count = 0
    for pth in sorted(purelib.glob("__editable__.arc_cli-*.pth")):
        original = pth.read_text(encoding="utf-8")
        lines = original.splitlines()
        replaced = [stable_src if line.strip() == candidate_src else line for line in lines]
        if replaced != lines:
            data = ("\n".join(replaced) + ("\n" if original.endswith("\n") else "")).encode("utf-8")
            pth.write_bytes(data)
            changed[pth] = data
            pth_count += 1

    if pth_count == 0:
        raise RuntimeError(
            "candidate editable install did not expose a rebindable Arc .pth mapping; "
            "refusing to leave the runtime pointed at a disposable transaction root"
        )

    direct_count = 0
    stable_url = "file://" + quote(str(stable), safe="/")
    dist_infos = sorted(purelib.glob("arc_cli-*.dist-info"))
    if not dist_infos:
        raise RuntimeError("candidate editable install did not produce arc-cli distribution metadata")
    for dist_info in dist_infos:
        direct = dist_info / "direct_url.json"
        if not direct.is_file():
            continue
        try:
            payload = json.loads(direct.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"invalid editable direct_url metadata: {direct}: {exc}") from exc
        info = payload.get("dir_info")
        if not isinstance(info, dict) or not info.get("editable"):
            continue
        payload["url"] = stable_url
        data = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        direct.write_bytes(data)
        changed[direct] = data
        direct_count += 1
        _rewrite_record(dist_info, changed)

    if direct_count == 0:
        raise RuntimeError("candidate editable install did not expose editable direct_url metadata")

    return {
        "rebound": True,
        "purelib": str(purelib),
        "pth_files": pth_count,
        "direct_url_files": direct_count,
        "stable_source": str(stable),
        "candidate_source": str(candidate),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Refresh the active editable arc-cli development install")
    ap.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1], help="persistent checkout that must own the final editable mapping")
    ap.add_argument("--candidate-source", type=Path, help="candidate checkout whose packaging metadata/entry points should be installed before rebinding to --source")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-generate", action="store_true", help="require generated surfaces to be current without rewriting them")
    ns = ap.parse_args()
    stable_root = ns.source.expanduser().resolve()
    candidate_root = (ns.candidate_source or stable_root).expanduser().resolve()
    if not _is_arc_root(stable_root):
        print(f"not an arc-cli persistent source checkout: {stable_root}", file=sys.stderr)
        return 2
    if not _is_arc_root(candidate_root):
        print(f"not an arc-cli candidate source checkout: {candidate_root}", file=sys.stderr)
        return 2

    generators = [
        [sys.executable, str(candidate_root / "scripts" / "sync_alias_registry.py"), "--root", str(candidate_root)],
        [sys.executable, str(candidate_root / "scripts" / "generate_command_docs.py")],
    ]
    checks = [
        [sys.executable, str(candidate_root / "scripts" / "sync_alias_registry.py"), "--check", "--root", str(candidate_root)],
        [sys.executable, str(candidate_root / "scripts" / "generate_command_docs.py"), "--check"],
        [sys.executable, str(candidate_root / "scripts" / "check_completion_contract.py")],
    ]
    install = [sys.executable, "-m", "pip", "install", "--no-build-isolation", "--no-deps", "-e", str(candidate_root)]
    if ns.dry_run:
        payload = {
            "schema_version": 1,
            "source": str(stable_root),
            "candidate_source": str(candidate_root),
            "generators": [] if ns.no_generate else generators,
            "checks": checks,
            "install": install,
            "will_rebind": candidate_root != stable_root,
            "dry_run": True,
        }
        print(json.dumps(payload, sort_keys=True) if ns.json else " ".join(install))
        return 0

    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if not ns.no_generate:
        for command in generators:
            proc = subprocess.run(command, cwd=candidate_root, env=env, check=False)
            if proc.returncode:
                return proc.returncode

        sys.path.insert(0, str(candidate_root / "src"))
        from arc_cli.completion import zsh_completion

        (candidate_root / "completions" / "_arc").write_text(zsh_completion(), encoding="utf-8")

    for command in checks:
        proc = subprocess.run(command, cwd=candidate_root, env=env, check=False)
        if proc.returncode:
            return proc.returncode
    expected_scripts = _declared_console_scripts(candidate_root)
    proc = subprocess.run(install, cwd=candidate_root, env=env, check=False)
    if proc.returncode:
        return proc.returncode

    try:
        rebind = _rebind_editable_install(candidate_root, stable_root)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    missing = sorted(name for name in expected_scripts if not shutil.which(name))
    payload = {
        "schema_version": 1,
        "source": str(stable_root),
        "candidate_source": str(candidate_root),
        "editable_install_refreshed": True,
        "editable_rebind": rebind,
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
            suffix = " and rebound to the stable checkout" if rebind.get("rebound") else ""
            print(f"editable install refreshed{suffix}; {len(expected_scripts)} Arc console scripts resolve on PATH")
    return 0 if not missing else 1


if __name__ == "__main__":
    raise SystemExit(main())
