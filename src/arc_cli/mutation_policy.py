from __future__ import annotations

import filecmp
import os
import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .errors import ConflictError, UsageError


class DestinationPolicy(str, Enum):
    FAIL = "fail"
    REPLACE = "replace"
    RENAME = "rename"
    SKIP_IDENTICAL = "skip-identical"


@dataclass(frozen=True)
class DestinationDecision:
    policy: DestinationPolicy
    destination: Path
    exists: bool
    action: str
    backup: Path | None = None


def policy_from_args(args, *, legacy_replace: bool = False) -> DestinationPolicy:
    raw = getattr(args, "destination_policy", None)
    if raw:
        return DestinationPolicy(raw)
    if legacy_replace or getattr(args, "overwrite", False) or getattr(args, "force", False):
        return DestinationPolicy.REPLACE
    if getattr(args, "rename_existing", False):
        return DestinationPolicy.RENAME
    return DestinationPolicy.FAIL


def next_available(path: Path, *, marker: str = ".old") -> Path:
    index = 1
    while True:
        candidate = path.with_name(path.name + f"{marker}.{index}")
        if not candidate.exists() and not candidate.is_symlink():
            return candidate
        index += 1


def identical_files(left: Path, right: Path) -> bool:
    if not left.is_file() or not right.is_file():
        return False
    if left.stat().st_size != right.stat().st_size:
        return False
    return filecmp.cmp(left, right, shallow=False)


def decide_destination(path: Path, policy: DestinationPolicy, *, candidate: Path | None = None) -> DestinationDecision:
    exists = path.exists() or path.is_symlink()
    if not exists:
        return DestinationDecision(policy, path, False, "publish")
    if policy is DestinationPolicy.FAIL:
        raise ConflictError(f"destination already exists: {path}; choose --destination-policy replace, rename, or skip-identical")
    if policy is DestinationPolicy.REPLACE:
        return DestinationDecision(policy, path, True, "replace")
    if policy is DestinationPolicy.RENAME:
        return DestinationDecision(policy, path, True, "rename", backup=next_available(path))
    if candidate is None:
        return DestinationDecision(policy, path, True, "compare-after-encode")
    if identical_files(candidate, path):
        return DestinationDecision(policy, path, True, "skip-identical")
    raise ConflictError(f"destination exists but is not identical: {path}")




def identical_paths(left: Path, right: Path) -> bool:
    """Return True only when two filesystem objects are safely equivalent.

    Directories are equivalent as containers (their children are evaluated
    separately by callers), symlinks require the same link target, and regular
    files require a full byte comparison.  Mixed object types never compare
    equal.
    """
    if left.is_symlink() or right.is_symlink():
        return left.is_symlink() and right.is_symlink() and os.readlink(left) == os.readlink(right)
    if left.is_dir() or right.is_dir():
        return left.is_dir() and right.is_dir()
    return identical_files(left, right)


def snapshot_existing(path: Path, requested: str | None) -> Path | None:
    """Copy an existing archive before an in-place mutation without clobbering.

    Unlike ``backup_existing`` (which moves the destination out of the way for
    replacement publication), this helper leaves the original in place for a
    backend that mutates it directly.
    """
    if not requested or not (path.exists() or path.is_symlink()):
        return None
    if requested == "auto":
        backup = next_available(path, marker=".bak")
    else:
        backup = Path(requested).expanduser()
        if backup.exists() or backup.is_symlink():
            raise ConflictError(f"backup destination already exists: {backup}")
    backup.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        os.symlink(os.readlink(path), backup)
    else:
        shutil.copy2(path, backup)
    return backup

def backup_existing(path: Path, requested: str | None) -> Path | None:
    if not requested or not (path.exists() or path.is_symlink()):
        return None
    if requested == "auto":
        backup = next_available(path, marker=".bak")
    else:
        backup = Path(requested).expanduser()
        if backup.exists() or backup.is_symlink():
            raise ConflictError(f"backup destination already exists: {backup}")
    backup.parent.mkdir(parents=True, exist_ok=True)
    os.replace(path, backup)
    return backup


def validate_backup_policy(policy: DestinationPolicy, requested: str | None) -> None:
    if requested and policy is not DestinationPolicy.REPLACE:
        raise UsageError("--backup-existing requires --destination-policy replace (or legacy --overwrite/--force)")
