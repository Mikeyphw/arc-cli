from __future__ import annotations

import fnmatch
from pathlib import Path, PurePosixPath
from typing import Callable

from .errors import UsageError
from .model import FilterRule, ManifestEntry, Member


def _match(path: str, pattern: str, is_dir: bool) -> bool:
    path = path.replace("\\", "/").lstrip("./")
    pattern = pattern.replace("\\", "/").strip()
    anchored = pattern.startswith("/")
    pattern = pattern.lstrip("/")
    dir_only = pattern.endswith("/")
    pattern = pattern.rstrip("/")
    if dir_only and not is_dir:
        # Directory rules also exclude descendants.
        parts = path.split("/")
        return any(fnmatch.fnmatch(part, pattern) for part in parts[:-1])
    if anchored:
        return fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(path, pattern + "/**")
    if "/" in pattern:
        return fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(path, f"*/{pattern}")
    return fnmatch.fnmatch(PurePosixPath(path).name, pattern) or any(
        fnmatch.fnmatch(part, pattern) for part in path.split("/")
    )


def selected(path: str, is_dir: bool, rules: list[FilterRule]) -> bool:
    keep = True
    for rule in rules:
        if _match(path, rule.pattern, is_dir):
            keep = rule.action == "include"
    return keep


def expand_rule_files(raw_rules: list[tuple[str, str]]) -> list[FilterRule]:
    out: list[FilterRule] = []
    for action, value in raw_rules:
        if action in {"include", "exclude"}:
            out.append(FilterRule(action, value))
            continue
        rule_action = "include" if action == "include-from" else "exclude"
        with Path(value).expanduser().open("r", encoding="utf-8") as fh:
            for line in fh:
                item = line.strip()
                if not item or item.startswith("#"):
                    continue
                if item.startswith("!"):
                    out.append(FilterRule("include", item[1:]))
                else:
                    out.append(FilterRule(rule_action, item))
    return out



def filter_members(members: list[Member], rules: list[FilterRule]) -> list[Member]:
    """Apply the normalized ordered include/exclude rules to archive members."""
    if not rules:
        return list(members)
    return [m for m in members if selected(m.name, m.kind == "dir", rules)]

def build_manifest(
    inputs: list[str],
    rules: list[FilterRule],
    *,
    follow_symlinks: bool = False,
    one_file_system: bool = False,
    on_scan: Callable[[int, int, int], None] | None = None,
) -> list[ManifestEntry]:
    import os
    import stat

    entries: list[ManifestEntry] = []
    seen: set[str] = set()
    cwd = Path.cwd().resolve()
    visited = 0
    selected_bytes = 0

    def member_name(p: Path) -> str:
        absolute = p.absolute()
        try:
            return absolute.relative_to(cwd).as_posix()
        except ValueError:
            return absolute.as_posix().lstrip("/")

    def add_path(p: Path, is_dir: bool, *, force_size: int | None = None) -> None:
        nonlocal visited, selected_bytes
        visited += 1
        name = member_name(p)
        if name in seen or not selected(name, is_dir, rules):
            if on_scan:
                on_scan(visited, len(entries), selected_bytes)
            return
        size = 0
        if force_size is not None:
            size = force_size
        elif not is_dir:
            try:
                st = p.stat() if follow_symlinks else p.lstat()
                if stat.S_ISREG(st.st_mode):
                    size = st.st_size
                elif stat.S_ISLNK(st.st_mode) and not follow_symlinks:
                    size = 0
                else:
                    raise UsageError(f"special input object is not archived by default: {p}")
            except OSError as exc:
                raise UsageError(f"cannot inspect input path {p}: {exc}") from exc
        entries.append(ManifestEntry(p, name, size, is_dir))
        seen.add(name)
        selected_bytes += size
        if on_scan:
            on_scan(visited, len(entries), selected_bytes)

    for raw in inputs:
        p = Path(raw).expanduser()
        if not p.exists() and not p.is_symlink():
            continue
        try:
            root_dev = (p.stat() if follow_symlinks else p.lstat()).st_dev
        except OSError:
            root_dev = None

        if p.is_dir() and (follow_symlinks or not p.is_symlink()):
            add_path(p, True)
            for root, dirs, files in os.walk(p, followlinks=follow_symlinks):
                rootp = Path(root)
                if rootp != p:
                    add_path(rootp, True)
                if one_file_system and root_dev is not None:
                    kept_dirs: list[str] = []
                    for d in dirs:
                        child = rootp / d
                        try:
                            dev = (child.stat() if follow_symlinks else child.lstat()).st_dev
                        except OSError:
                            continue
                        if dev == root_dev:
                            kept_dirs.append(d)
                    dirs[:] = kept_dirs
                if not follow_symlinks:
                    # os.walk already avoids descending symlink dirs with followlinks=False,
                    # but they still appear in dirs; archive them as link entries.
                    for d in list(dirs):
                        child = rootp / d
                        if child.is_symlink():
                            add_path(child, False)
                for f in files:
                    child = rootp / f
                    if one_file_system and root_dev is not None:
                        try:
                            dev = (child.stat() if follow_symlinks else child.lstat()).st_dev
                        except OSError:
                            continue
                        if dev != root_dev:
                            continue
                    add_path(child, False)
        else:
            add_path(p, False)
    return entries
