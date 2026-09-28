from __future__ import annotations

import os
import stat
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

from .errors import UnsafeArchive
from .model import Member


def _validate_name(name: str) -> None:
    norm = name.replace("\\", "/")
    pp = PurePosixPath(norm)
    if pp.is_absolute() or (len(norm) >= 2 and norm[1] == ":") or norm.startswith("//"):
        raise UnsafeArchive(f"absolute archive path rejected: {name}")
    depth = 0
    for part in pp.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            depth -= 1
            if depth < 0:
                raise UnsafeArchive(f"path escapes extraction root: {name}")
        else:
            depth += 1


def _validate_link(member_name: str, target: str) -> None:
    target = target.replace("\\", "/")
    if PurePosixPath(target).is_absolute() or (len(target) >= 2 and target[1] == ":"):
        raise UnsafeArchive(f"absolute link target rejected: {member_name} -> {target}")
    base = list(PurePosixPath(member_name).parent.parts)
    for part in PurePosixPath(target).parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not base:
                raise UnsafeArchive(f"link escapes extraction root: {member_name} -> {target}")
            base.pop()
        else:
            base.append(part)


def validate_members(members: list[Member]) -> None:
    symlinks: set[str] = set()
    for member in members:
        _validate_name(member.name)
        if member.kind == "special":
            raise UnsafeArchive(f"special filesystem object rejected: {member.name}")
        if member.kind == "unknown":
            raise UnsafeArchive(f"archive member type cannot be proven safe: {member.name}")
        if member.kind in {"symlink", "hardlink"}:
            if not member.link_target:
                raise UnsafeArchive(f"link without target rejected: {member.name}")
            _validate_link(member.name, member.link_target)
            if member.kind == "symlink":
                symlinks.add(member.name.rstrip("/"))
    for member in members:
        parts = PurePosixPath(member.name).parts[:-1]
        cur: list[str] = []
        for part in parts:
            cur.append(part)
            if "/".join(cur) in symlinks:
                raise UnsafeArchive(f"member descends through archive symlink: {member.name}")


def zip_members(path: Path) -> list[Member]:
    out: list[Member] = []
    try:
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                mode = (info.external_attr >> 16) & 0xFFFF
                kind = "dir" if info.is_dir() else "file"
                target = None
                if stat.S_ISLNK(mode):
                    kind = "symlink"
                    try:
                        target = zf.read(info).decode("utf-8", "surrogateescape")
                    except Exception:
                        target = None
                elif mode:
                    file_type = stat.S_IFMT(mode)
                    # Many ZIP writers store only permission bits and omit the
                    # POSIX file-type bits.  Treat that as a regular file; only
                    # explicit non-regular types are special.
                    if file_type and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                        kind = "special"
                y, m, d, hh, mm, ss = info.date_time
                mtime = f"{y:04d}-{m:02d}-{d:02d} {hh:02d}:{mm:02d}:{ss:02d}"
                out.append(Member(info.filename, info.file_size, kind, target, mtime))
    except zipfile.BadZipFile as exc:
        from .errors import CorruptArchive
        raise CorruptArchive(str(exc)) from exc
    except RuntimeError as exc:
        from .errors import PasswordError
        text = str(exc).lower()
        if "password" in text or "encrypted" in text:
            raise PasswordError("archive password is required or was rejected while indexing ZIP metadata") from exc
        raise
    return out


def _tar_member_from_info(info: tarfile.TarInfo) -> Member:
    if info.isdir():
        kind = "dir"
    elif info.issym():
        kind = "symlink"
    elif info.islnk():
        kind = "hardlink"
    elif info.isreg():
        kind = "file"
    else:
        kind = "special"
    import datetime as _dt
    # TAR stores an epoch timestamp. Render it in UTC rather than the host
    # timezone so provenance/metadata fingerprints are portable across hosts
    # (notably Termux devices and desktop builders in different timezones).
    mtime = (
        _dt.datetime.fromtimestamp(info.mtime, tz=_dt.timezone.utc)
        .replace(tzinfo=None)
        .isoformat(sep=" ", timespec="seconds")
        if info.mtime is not None
        else None
    )
    return Member(info.name, info.size, kind, info.linkname or None, mtime)


def tar_members(path: Path) -> list[Member]:
    import shutil
    import subprocess

    out: list[Member] = []
    try:
        with tarfile.open(path, "r:*") as tf:
            for info in tf.getmembers():
                out.append(_tar_member_from_info(info))
        return out
    except (tarfile.TarError, OSError):
        pass

    # Python 3.11 tarfile does not natively decode zstd. Stream the decompressed
    # tar through tarfile when zstd is available so safety checks still see link metadata.
    exe = shutil.which("zstd") or shutil.which("pzstd")
    if exe:
        proc = subprocess.Popen([exe, "-dc", "--", str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert proc.stdout is not None
        try:
            try:
                with tarfile.open(fileobj=proc.stdout, mode="r|") as tf:
                    for info in tf:
                        out.append(_tar_member_from_info(info))
            except tarfile.TarError:
                out.clear()
            _, stderr = proc.communicate()
            if proc.returncode == 0 and out:
                return out
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
    from .errors import CorruptArchive
    raise CorruptArchive(f"cannot inspect tar metadata safely: {path}")


def validate_extracted_tree(root: Path) -> None:
    root_real = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in [*dirnames, *filenames]:
            p = Path(dirpath) / name
            try:
                st = p.lstat()
            except OSError as exc:
                raise UnsafeArchive(f"cannot inspect extracted path {p}: {exc}") from exc
            if stat.S_ISLNK(st.st_mode):
                target = os.readlink(p)
                resolved = (p.parent / target).resolve(strict=False)
                try:
                    resolved.relative_to(root_real)
                except ValueError as exc:
                    raise UnsafeArchive(f"extracted symlink escapes destination: {p} -> {target}") from exc
            elif not (stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode)):
                raise UnsafeArchive(f"special extracted object rejected: {p}")
