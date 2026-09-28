from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

from .capabilities import BACKEND_PROFILES, BackendCapabilityProfile, VerificationLevel, backend_profile, verification_rank
from .config import DEFAULT_BACKENDS, backend_preferences
from .errors import BackendUnavailable, CorruptArchive, PasswordError, UnsupportedFormat
from .model import ArchiveFormat, BackendInfo, ManifestEntry, Member
from .progress import ProgressReporter, console




BACKEND_CAPABILITIES: dict[str, set[str]] = {
    name: profile.legacy_capabilities() for name, profile in BACKEND_PROFILES.items()
}


@lru_cache(maxsize=32)
def _tar_delete_supported(binary: str) -> bool:
    """Return whether this concrete tar executable advertises --delete.

    Termux commonly provides bsdtar as the preferred tar backend.  bsdtar can
    create/append/update tar archives but does not implement GNU tar's
    --delete operation.  Probe the concrete executable rather than assuming a
    command named ``tar`` is GNU tar (it may itself be a bsdtar shim/symlink).
    """
    path = shutil.which(binary) or binary
    try:
        proc = subprocess.run(
            [path, "--help"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return "--delete" in (proc.stdout or "")


def backend_capability_profile(binary: str) -> BackendCapabilityProfile:
    name = Path(binary).name
    profile = backend_profile(name)
    if name == "tar":
        concrete = shutil.which(binary) or binary
        if _tar_delete_supported(concrete) and "remove" not in profile.operations:
            operations = frozenset({*profile.operations, "remove"})
            mutation = frozenset({*profile.mutation, "remove"})
            profile = replace(profile, operations=operations, mutation=mutation)
    return profile


def backend_capabilities(binary: str) -> set[str]:
    return backend_capability_profile(binary).legacy_capabilities()


def _profile_satisfies(profile: BackendCapabilityProfile, required: set[str]) -> bool:
    return required <= profile.legacy_capabilities()


def _profile_satisfies_verification(profile: BackendCapabilityProfile, requested: VerificationLevel) -> bool:
    return verification_rank(profile.maximum_verification) >= verification_rank(requested)


def _which_capable(
    names: list[str],
    *,
    required: set[str] | None = None,
    required_verification: VerificationLevel | None = None,
    allow_verification_downgrade: bool = False,
    no_fallback: bool = False,
) -> tuple[str, str] | None:
    required = required or set()
    candidates = names[:1] if no_fallback and names else names
    eligible: list[tuple[str, str, BackendCapabilityProfile]] = []
    for name in candidates:
        path = shutil.which(name)
        if not path:
            continue
        profile = backend_capability_profile(name)
        if _profile_satisfies(profile, required):
            eligible.append((name, path, profile))
    if required_verification is not None:
        for name, path, profile in eligible:
            if _profile_satisfies_verification(profile, required_verification):
                return name, path
        if allow_verification_downgrade and eligible:
            return eligible[0][0], eligible[0][1]
        return None
    if eligible:
        return eligible[0][0], eligible[0][1]
    return None


def backend_inventory(config: dict, *, environ: dict[str, str] | None = None) -> list[dict]:
    rows: list[dict] = []
    for role, preferences in (
        (key, backend_preferences(config, key, environ=environ)) for key in DEFAULT_BACKENDS
    ):
        candidates = []
        for name in preferences:
            path = shutil.which(name)
            profile = backend_capability_profile(name)
            candidates.append({
                "binary": name,
                "path": path,
                "installed": bool(path),
                "capabilities": sorted(profile.legacy_capabilities()),
                "capability_profile": profile.to_dict(),
            })
        rows.append({"role": role, "preferences": preferences, "candidates": candidates})
    return rows

class Backend:
    name = "backend"

    def __init__(self, info: BackendInfo):
        self.info = info

    @property
    def binary(self) -> str:
        return self.info.path

    def list_members(self, archive: Path, password: str | None = None) -> list[Member]:
        raise UnsupportedFormat(f"{self.name} cannot list this format")

    def command(self, operation: str, archive: Path, **kwargs) -> tuple[list[str], dict]:
        raise NotImplementedError


def _which_first(names: list[str]) -> tuple[str, str] | None:
    for name in names:
        path = shutil.which(name)
        if path:
            return name, path
    return None


def _manifest_file(entries: list[ManifestEntry], *, dry_run: bool = False, nul: bool = False) -> Path:
    if dry_run:
        return Path("<arc-manifest>")
    fd, name = tempfile.mkstemp(prefix="arc-manifest-")
    os.close(fd)
    p = Path(name)
    if nul:
        with p.open("wb") as fh:
            for e in entries:
                fh.write(os.fsencode(e.source))
                fh.write(b"\0")
    else:
        with p.open("w", encoding="utf-8", errors="surrogateescape", newline="") as fh:
            for e in entries:
                fh.write(os.fspath(e.source) + "\n")
    return p


def _needs_direct_argv(entries: list[ManifestEntry]) -> bool:
    # Native list-file formats used by zip/7z/rar are line framed. Preserve
    # odd filenames by switching to direct argv whenever line framing would be
    # ambiguous. TAR has a real --null list protocol and does not need this.
    return any("\n" in os.fspath(e.source) or "\r" in os.fspath(e.source) for e in entries)


def _looks_like_password_failure(text: str) -> bool:
    low = text.lower()
    return any(marker in low for marker in (
        "wrong password", "incorrect password", "password is incorrect", "bad password",
        "password required", "encrypted file", "can not open encrypted archive",
        "cannot open encrypted archive", "wrong password?",
    ))


def _compression_command(
    compression: str, config: dict, level: int | None, threads: int | None, *, no_fallback: bool = False
) -> list[str]:
    required = {"threads"} if threads is not None else set()
    found = _which_capable(backend_preferences(config, compression), required=required, no_fallback=no_fallback)
    if not found:
        raise BackendUnavailable(f"no {compression} compressor is installed")
    name, path = found
    cmd = [path, "-q", "-c"]
    if compression in {"gzip", "bzip2", "xz"} and level is not None:
        cmd.append(f"-{level}")
    elif compression == "zstd" and level is not None:
        mapped = 1 + round(level * 18 / 9)
        cmd.append(f"-{mapped}")
    if threads is not None:
        n = threads if threads > 0 else 0
        if name in {"pigz"}:
            if n > 0:
                cmd += ["-p", str(n)]
        elif name in {"xz"}:
            cmd += [f"-T{n}"]
        elif name in {"pixz"}:
            if n > 0:
                cmd += ["-p", str(n)]
        elif name == "zstd":
            cmd += [f"-T{n}"]
        elif name == "pzstd" and n > 0:
            cmd += ["-p", str(n)]
        elif name == "pbzip2" and n > 0:
            cmd += [f"-p{n}"]
    return cmd



def _decompression_command(
    compression: str, config: dict, archive: Path, *, no_fallback: bool = False
) -> list[str]:
    found = _which_capable(backend_preferences(config, compression), no_fallback=no_fallback)
    if not found:
        raise BackendUnavailable(f"no {compression} decompressor is installed")
    _, path = found
    return [path, "-q", "-dc", "--", str(archive)]


class TarBackend(Backend):
    name = "tar"

    def list_members(self, archive: Path, password: str | None = None) -> list[Member]:
        from .safety import tar_members
        return tar_members(archive)

    def command(self, operation: str, archive: Path, **kwargs):
        extra = kwargs.get("extra", [])
        output = kwargs.get("output")
        members = kwargs.get("members", [])
        entries = kwargs.get("entries", [])
        fmt: ArchiveFormat = kwargs["fmt"]
        level = kwargs.get("level")
        threads = kwargs.get("threads")
        follow_symlinks = kwargs.get("follow_symlinks", False)
        config = kwargs.get("config", {})
        password = kwargs.get("password")
        preserve_owner = kwargs.get("preserve_owner", False)
        preserve_acls = kwargs.get("preserve_acls", False)
        preserve_xattrs = kwargs.get("preserve_xattrs", False)
        dry_run = kwargs.get("dry_run", False)
        no_fallback = kwargs.get("no_fallback", False)
        if password:
            raise UnsupportedFormat("tar formats do not provide normalized encryption; use an encrypting container such as 7z or zip")
        cleanup: list[Path] = []
        if operation in {"create", "add", "update"}:
            mf = _manifest_file(entries, dry_run=dry_run, nul=True)
            cleanup.append(mf)
            if operation != "create" and fmt.compression:
                raise UnsupportedFormat("updating compressed tar archives is not supported safely; recreate the archive")
            if operation == "create" and fmt.compression:
                tar_cmd = [self.binary, "-cvf", "-", "--no-recursion"]
                if preserve_acls:
                    tar_cmd.append("--acls")
                if preserve_xattrs:
                    tar_cmd.append("--xattrs")
                if follow_symlinks:
                    tar_cmd.append("-h")
                tar_cmd += ["--null", "-T", str(mf), *extra]
                comp_cmd = _compression_command(fmt.compression, config, level, threads, no_fallback=no_fallback)
                return tar_cmd, {"cleanup": cleanup, "pipeline": comp_cmd, "stdout_file": archive}
            mode = "-cf" if operation == "create" else ("-uf" if operation == "update" else "-rf")
            cmd = [self.binary, mode, str(archive), "--no-recursion"]
            if preserve_acls:
                cmd.append("--acls")
            if preserve_xattrs:
                cmd.append("--xattrs")
            if follow_symlinks:
                cmd.append("-h")
            cmd += ["-v", "--null", "-T", str(mf), *extra]
        elif operation in {"extract", "list", "test"}:
            preprocess = _decompression_command(fmt.compression, config, archive, no_fallback=no_fallback) if fmt.compression else None
            archive_arg = "-" if preprocess else str(archive)
            if operation == "extract":
                cmd = [self.binary, "-xvf", archive_arg, "-C", str(output)]
                if not preserve_owner:
                    cmd.append("--no-same-owner")
                if preserve_acls:
                    cmd.append("--acls")
                if preserve_xattrs:
                    cmd.append("--xattrs")
                cmd += [*members, *extra]
            elif operation == "list":
                cmd = [self.binary, "-tvf", archive_arg, *members, *extra]
            else:
                cmd = [self.binary, "-tf", archive_arg, *members, *extra]
            meta = {"cleanup": cleanup}
            if preprocess:
                meta["preprocess"] = preprocess
            return cmd, meta
        elif operation == "remove":
            if fmt.compression:
                raise UnsupportedFormat("removing members from compressed tar archives requires recreating the archive")
            if "remove" not in self.info.capabilities:
                raise UnsupportedFormat(f"{self.info.binary} does not support normalized tar member removal")
            cmd = [self.binary, "--delete", "-f", str(archive), *members, *extra]
            return cmd, {"cleanup": cleanup}
        else:
            raise UnsupportedFormat(f"tar does not support normalized {operation}")
        return cmd, {"cleanup": cleanup}


def parse_7z_slt(text: str, archive: Path | str | None = None) -> list[Member]:
    out: list[Member] = []
    current: dict[str, str] = {}
    archive_text = os.fspath(archive) if archive is not None else None

    def flush() -> None:
        nonlocal current
        if "Path" not in current or (archive_text is not None and current.get("Path") == archive_text):
            current = {}
            return
        attrs = current.get("Attributes", "")
        mode = current.get("Mode", "")
        type_hint = (current.get("Type") or "").lower()
        target = current.get("Symbolic Link") or current.get("Hard Link")
        if current.get("Symbolic Link"):
            kind = "symlink"
        elif current.get("Hard Link"):
            kind = "hardlink"
        elif "directory" in type_hint or "D" in attrs:
            kind = "dir"
        else:
            unix_mode = mode or attrs
            m = re.search(r"(?:^|\s)([bcpdsl-])[rwxStTs-]{9}(?:\s|$)", unix_mode)
            if m:
                lead = m.group(1)
                kind = {"d": "dir", "l": "symlink", "-": "file"}.get(lead, "special")
            elif type_hint:
                # 7z -slt occasionally exposes a textual entry type. Unknown
                # non-empty types must not be silently downgraded to files.
                kind = "special" if any(x in type_hint for x in ("junction", "device", "fifo", "socket", "special")) else "unknown"
            else:
                # Formats such as ZIP may expose only DOS attributes (e.g. A)
                # for ordinary files, with no POSIX mode or Type field.
                kind = "file"
        try:
            size = int(current.get("Size", "0") or 0)
        except ValueError:
            size = 0
        out.append(Member(current["Path"], size, kind, target, current.get("Modified") or current.get("MTime")))
        current = {}

    for line in text.splitlines() + [""]:
        if not line.strip():
            flush()
            continue
        if " = " in line:
            key, value = line.split(" = ", 1)
            current[key] = value
    return out


def parse_rar_lt(text: str) -> list[Member]:
    out: list[Member] = []
    current: dict[str, str] = {}

    def flush() -> None:
        nonlocal current
        name = current.get("Name")
        if not name:
            current = {}
            return
        raw_type = (current.get("Type") or "").strip().lower()
        target = current.get("Target")
        attrs = current.get("Attributes", "").strip()
        if "symbolic link" in raw_type or attrs.startswith("l"):
            kind = "symlink"
        elif "hard link" in raw_type:
            kind = "hardlink"
        elif "directory" in raw_type or attrs.startswith("d"):
            kind = "dir"
        elif raw_type == "file" or attrs.startswith("-") or (attrs and not raw_type):
            kind = "file"
        elif raw_type:
            # Service headers, junctions and other unfamiliar types are not
            # safe to silently downgrade to ordinary files.
            kind = "special" if any(x in raw_type for x in ("junction", "device", "fifo", "socket", "service")) else "unknown"
        else:
            kind = "unknown"
        try:
            size = int((current.get("Size") or "0").replace(",", ""))
        except ValueError:
            size = 0
        out.append(Member(name, size, kind, target, current.get("Modified") or current.get("MTime")))
        current = {}

    for line in text.splitlines() + [""]:
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        canonical_key = "Modified" if key.lower() in {"mtime", "modified"} else key
        if canonical_key in {"Name", "Type", "Target", "Size", "Attributes", "Modified"}:
            if canonical_key == "Name" and current.get("Name"):
                flush()
            current[canonical_key] = value.strip()
    return out


class SevenZipBackend(Backend):
    name = "7z"

    def list_members(self, archive: Path, password: str | None = None) -> list[Member]:
        cmd = [self.binary, "l", "-slt", f"-p{password}" if password else "-p-"]
        cmd += ["--", str(archive)]
        proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if proc.returncode != 0:
            detail = ((proc.stderr or "") + "\n" + (proc.stdout or "")).strip()
            if _looks_like_password_failure(detail):
                raise PasswordError("archive password rejected by 7z")
            raise CorruptArchive(detail or "7z could not read archive")
        return parse_7z_slt(proc.stdout, archive)

    def command(self, operation: str, archive: Path, **kwargs):
        extra = kwargs.get("extra", [])
        output = kwargs.get("output")
        members = kwargs.get("members", [])
        entries = kwargs.get("entries", [])
        level = kwargs.get("level")
        threads = kwargs.get("threads")
        follow_symlinks = kwargs.get("follow_symlinks", False)
        password = kwargs.get("password")
        fmt: ArchiveFormat = kwargs["fmt"]
        dry_run = kwargs.get("dry_run", False)
        cleanup: list[Path] = []
        common = ["-bb1", "-bsp1"]
        if password:
            common.append(f"-p{password}")
        redact = [password] if password else []
        if operation in {"create", "add", "update"}:
            # 7-Zip's @listfile handling can emit "No more files" warnings
            # (exit code 1) when directory entries are supplied through the
            # list file on POSIX/Termux. Preserve directory entries—including
            # empty directories—by using direct argv for directory-bearing
            # manifests. File-only manifests still use @listfile so large
            # archives do not expand argv.
            direct = _needs_direct_argv(entries) or any(e.is_dir for e in entries)
            if direct:
                mf = None
            else:
                mf = _manifest_file(entries, dry_run=dry_run)
                cleanup.append(mf)
            verb = "a" if operation in {"create", "add"} else "u"
            cmd = [self.binary, verb, str(archive), *common, "-r-"]
            if not follow_symlinks:
                cmd.append("-snl")
            if fmt.container in {"zip", "7z"}:
                cmd.append(f"-t{fmt.container}")
            # Normalized add/update/remove are part of arc's advertised 7z
            # contract.  7-Zip cannot reliably mutate solid archives, so arc
            # creates its own 7z containers in non-solid mode.  Keep the flag
            # on later add/update operations as well so new data follows the
            # same mutation-safe layout.
            if fmt.container == "7z":
                cmd.append("-ms=off")
            if level is not None:
                cmd.append(f"-mx={level}")
            if threads is not None:
                cmd.append(f"-mmt={threads if threads > 0 else 'on'}")
            cmd += extra
            extra = []
            if direct:
                cmd += ["--", *[os.fspath(e.source) for e in entries]]
            else:
                cmd.append(f"@{mf}")
        elif operation == "extract":
            read_common = [*common, f"-p{password}" if password else "-p-"] if not any(x.startswith("-p") for x in common) else common
            cmd = [self.binary, "x", str(archive), f"-o{output}", "-y", *read_common, *members]
        elif operation == "list":
            cmd = [self.binary, "l", str(archive), f"-p{password}" if password else "-p-", *members]
        elif operation == "test":
            read_common = [*common, f"-p{password}" if password else "-p-"] if not any(x.startswith("-p") for x in common) else common
            cmd = [self.binary, "t", str(archive), *read_common, *members]
        elif operation == "remove":
            cmd = [self.binary, "d", str(archive), *members, *common]
        else:
            raise UnsupportedFormat(f"7z does not support normalized {operation}")
        cmd += extra
        return cmd, {"cleanup": cleanup, "redact": redact}


class InfoZipBackend(Backend):
    name = "zip"

    def __init__(self, info: BackendInfo, mode: str):
        super().__init__(info)
        self.mode = mode

    def list_members(self, archive: Path, password: str | None = None) -> list[Member]:
        from .safety import zip_members
        return zip_members(archive)

    def command(self, operation: str, archive: Path, **kwargs):
        extra = kwargs.get("extra", [])
        output = kwargs.get("output")
        members = kwargs.get("members", [])
        entries = kwargs.get("entries", [])
        level = kwargs.get("level")
        follow_symlinks = kwargs.get("follow_symlinks", False)
        password = kwargs.get("password")
        overwrite = kwargs.get("overwrite", False)
        skip_existing = kwargs.get("skip_existing", False)
        cleanup: list[Path] = []
        stdin = None
        redact = [password] if password else []
        if self.mode == "unzip":
            if operation == "extract":
                cmd = [self.binary]
                if overwrite:
                    cmd.append("-o")
                elif skip_existing:
                    cmd.append("-n")
                cmd += ["-P", password or ""]
                cmd += [str(archive), *members, "-d", str(output)]
            elif operation == "list":
                cmd = [self.binary, "-l", str(archive), *members]
            elif operation == "test":
                cmd = [self.binary, "-t", "-P", password or "", str(archive), *members]
            elif operation == "print-member":
                cmd = [self.binary, "-p"]
                if password:
                    cmd += ["-P", password]
                cmd += extra
                extra = []
                cmd += [str(archive), *members]
            else:
                raise UnsupportedFormat(f"unzip cannot {operation}")
        else:
            names = [os.fspath(e.source) for e in entries]
            if operation in {"create", "add", "update"}:
                cmd = [self.binary]
                if level is not None:
                    cmd.append(f"-{level}")
                if not follow_symlinks:
                    cmd.append("-y")
                if password:
                    cmd += ["-P", password]
                cmd += extra
                extra = []
                if _needs_direct_argv(entries):
                    cmd += [str(archive), "--", *names]
                else:
                    cmd += [str(archive), "-@"]
                    stdin = "\n".join(names) + "\n"
            elif operation == "remove":
                cmd = [self.binary, "-d", str(archive), *members]
            else:
                raise UnsupportedFormat(f"zip cannot {operation}")
        cmd += extra
        return cmd, {"cleanup": cleanup, "stdin": stdin, "redact": redact}


class RarBackend(Backend):
    name = "rar"

    def __init__(self, info: BackendInfo, mode: str):
        super().__init__(info)
        self.mode = mode

    def list_members(self, archive: Path, password: str | None = None) -> list[Member]:
        cmd = [self.binary, "lt", f"-p{password}" if password else "-p-", "--", str(archive)]
        proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if proc.returncode != 0:
            detail = ((proc.stderr or "") + "\n" + (proc.stdout or "")).strip()
            if _looks_like_password_failure(detail):
                raise PasswordError("archive password rejected by RAR backend")
            raise CorruptArchive(detail or "RAR backend could not read archive")
        return parse_rar_lt(proc.stdout)

    def command(self, operation: str, archive: Path, **kwargs):
        extra = kwargs.get("extra", [])
        output = kwargs.get("output")
        members = kwargs.get("members", [])
        entries = kwargs.get("entries", [])
        level = kwargs.get("level")
        password = kwargs.get("password")
        dry_run = kwargs.get("dry_run", False)
        cleanup: list[Path] = []
        redact = [password] if password else []
        if self.mode == "unrar":
            if operation == "extract":
                cmd = [self.binary, "x", "-y", f"-p{password}" if password else "-p-", str(archive), *members, str(output) + os.sep]
            elif operation == "list":
                cmd = [self.binary, "lt", f"-p{password}" if password else "-p-", str(archive), *members]
            elif operation == "test":
                cmd = [self.binary, "t", f"-p{password}" if password else "-p-", str(archive), *members]
            else:
                raise UnsupportedFormat(f"unrar cannot {operation}")
        else:
            if operation in {"create", "add", "update"}:
                direct = _needs_direct_argv(entries)
                if direct:
                    mf = None
                else:
                    mf = _manifest_file(entries, dry_run=dry_run)
                    cleanup.append(mf)
                cmd = [self.binary, "a", "-y", "-r-"]
                if password:
                    cmd.append(f"-p{password}")
                if level is not None:
                    cmd.append(f"-m{round(level * 5 / 9)}")
                cmd += extra
                extra = []
                cmd.append(str(archive))
                if direct:
                    cmd += ["--", *[os.fspath(e.source) for e in entries]]
                else:
                    cmd.append(f"@{mf}")
            elif operation == "remove":
                cmd = [self.binary, "d", "-y"]
                if password:
                    cmd.append(f"-p{password}")
                cmd += [str(archive), *members]
            elif operation == "extract":
                cmd = [self.binary, "x", "-y", f"-p{password}" if password else "-p-", str(archive), *members, str(output) + os.sep]
            elif operation == "list":
                cmd = [self.binary, "lt", f"-p{password}" if password else "-p-", str(archive), *members]
            elif operation == "test":
                cmd = [self.binary, "t", f"-p{password}" if password else "-p-", str(archive), *members]
            else:
                raise UnsupportedFormat(f"rar cannot {operation}")
        cmd += extra
        return cmd, {"cleanup": cleanup, "redact": redact}


class StreamBackend(Backend):
    name = "stream"

    def __init__(self, info: BackendInfo, compression: str):
        super().__init__(info)
        self.compression = compression

    def command(self, operation: str, archive: Path, **kwargs):
        extra = kwargs.get("extra", [])
        entries = kwargs.get("entries", [])
        level = kwargs.get("level")
        threads = kwargs.get("threads")
        output = kwargs.get("output")
        if kwargs.get("password"):
            raise UnsupportedFormat(f"{self.compression} stream compression does not provide archive encryption")
        if operation == "create":
            if len(entries) != 1 or entries[0].is_dir:
                raise UnsupportedFormat(f"{self.compression} stream creation requires exactly one regular input file")
            cmd = [self.binary, "-q", "-c"]
            if level is not None:
                if self.compression == "zstd":
                    cmd.append(f"-{1 + round(level * 18 / 9)}")
                else:
                    cmd.append(f"-{level}")
            if threads is not None:
                n = threads if threads > 0 else 0
                name = Path(self.binary).name
                if name == "pigz" and n > 0:
                    cmd += ["-p", str(n)]
                elif name == "xz":
                    cmd += [f"-T{n}"]
                elif name == "pixz" and n > 0:
                    cmd += ["-p", str(n)]
                elif name == "zstd":
                    cmd += [f"-T{n}"]
                elif name == "pzstd" and n > 0:
                    cmd += ["-p", str(n)]
                elif name == "pbzip2" and n > 0:
                    cmd += [f"-p{n}"]
            cmd += extra + [str(entries[0].source)]
            return cmd, {"stdout_file": archive, "cleanup": [], "progress_indeterminate": True}
        if operation == "extract":
            stem = archive.name
            for suffix in (".gzip", ".gz", ".bz2", ".xz", ".zst", ".zstd"):
                if stem.lower().endswith(suffix):
                    stem = stem[: -len(suffix)]
                    break
            dest = Path(output) / stem
            cmd = [self.binary, "-q", "-dc", *extra, str(archive)]
            return cmd, {"stdout_file": dest, "cleanup": [], "progress_indeterminate": True}
        if operation == "test":
            test_flag = "-t"
            cmd = [self.binary, "-q", test_flag, *extra, str(archive)]
            return cmd, {"cleanup": [], "progress_indeterminate": True}
        raise UnsupportedFormat(f"{self.compression} stream does not support {operation}")


def _make_backend(name: str, path: str, fmt: ArchiveFormat, operation: str) -> Backend:
    profile = backend_capability_profile(name)
    info = BackendInfo(name, name, path, profile.legacy_capabilities(), profile)
    if name in {"7z", "7zz"}:
        if fmt.container == "rar" and operation in {"create", "add", "update", "remove"}:
            raise UnsupportedFormat("7-Zip can read RAR but cannot create or mutate RAR archives")
        if fmt.container == "tar":
            raise UnsupportedFormat("forced 7-Zip TAR handling is not enabled; use tar/bsdtar")
        return SevenZipBackend(BackendInfo("7z", name, path, profile.legacy_capabilities(), profile))
    if name in {"zip", "unzip"}:
        return InfoZipBackend(info, name)
    if name in {"rar", "unrar"}:
        return RarBackend(info, "rar" if name == "rar" else "unrar")
    if name in {"tar", "bsdtar"}:
        return TarBackend(BackendInfo("tar", name, path, profile.legacy_capabilities(), profile))
    if name in {"gzip", "pigz", "bzip2", "pbzip2", "xz", "pixz", "zstd", "pzstd"}:
        comp = {"gzip": "gzip", "pigz": "gzip", "bzip2": "bzip2", "pbzip2": "bzip2", "xz": "xz", "pixz": "xz", "zstd": "zstd", "pzstd": "zstd"}[name]
        return StreamBackend(BackendInfo(comp, name, path, profile.legacy_capabilities(), profile), comp)
    raise BackendUnavailable(f"unsupported backend: {name}")


def _format_allows_binary(fmt: ArchiveFormat, operation: str, name: str) -> bool:
    if fmt.container == "tar":
        return name in {"tar", "bsdtar"}
    if fmt.container == "zip":
        return name in {"7z", "7zz", "zip", "unzip"}
    if fmt.container == "7z":
        return name in {"7z", "7zz"}
    if fmt.container == "rar":
        if operation in {"create", "add", "update", "remove"}:
            return name == "rar"
        return name in {"7z", "7zz", "rar", "unrar"}
    if fmt.is_stream:
        comp_names = {
            "gzip": {"gzip", "pigz"},
            "bzip2": {"bzip2", "pbzip2"},
            "xz": {"xz", "pixz"},
            "zstd": {"zstd", "pzstd"},
        }
        return name in comp_names.get(fmt.compression or "", set())
    return False


def _role_for(fmt: ArchiveFormat, operation: str) -> str:
    if fmt.container == "tar":
        return "tar"
    if fmt.container == "zip":
        return "zip_create" if operation in {"create", "add", "update", "remove"} else "zip_extract"
    if fmt.container == "7z":
        return "7z"
    if fmt.container == "rar":
        return "rar_create" if operation in {"create", "add", "update", "remove"} else "rar_extract"
    if fmt.is_stream and fmt.compression:
        return fmt.compression
    raise BackendUnavailable(f"no backend role for {fmt.canonical}")


def resolve_backend(
    fmt: ArchiveFormat,
    operation: str,
    config: dict,
    forced: str | None = None,
    *,
    required_capabilities: set[str] | None = None,
    required_verification: VerificationLevel | None = None,
    allow_verification_downgrade: bool = False,
    no_fallback: bool = False,
) -> Backend:
    required = set(required_capabilities or ()) | {operation}
    if forced:
        path = shutil.which(forced)
        if not path:
            raise BackendUnavailable(f"requested backend is not installed: {forced}")
        name = Path(path).name
        if not _format_allows_binary(fmt, operation, name):
            raise UnsupportedFormat(f"backend {forced} cannot {operation} {fmt.canonical}")
        profile = backend_capability_profile(name)
        missing = required - profile.legacy_capabilities()
        if missing:
            raise UnsupportedFormat(
                f"backend {forced} lacks required capability/capabilities: {', '.join(sorted(missing))}"
            )
        if required_verification is not None and not _profile_satisfies_verification(profile, required_verification) and not allow_verification_downgrade:
            raise UnsupportedFormat(
                f"backend {forced} cannot prove verification level {required_verification.value}; maximum={profile.maximum_verification.value}"
            )
        return _make_backend(name, path, fmt, operation)

    role = _role_for(fmt, operation)
    preferences = backend_preferences(config, role)
    candidates = preferences[:1] if no_fallback and preferences else preferences
    reasons: list[str] = []
    eligible: list[tuple[str, str, BackendCapabilityProfile]] = []
    for candidate in candidates:
        path = shutil.which(candidate)
        if not path:
            reasons.append(f"{candidate}: not installed")
            continue
        name = Path(path).name
        if not _format_allows_binary(fmt, operation, name):
            reasons.append(f"{candidate}: incompatible with {fmt.canonical}/{operation}")
            continue
        profile = backend_capability_profile(name)
        missing = required - profile.legacy_capabilities()
        if missing:
            reasons.append(f"{candidate}: missing {','.join(sorted(missing))}")
            continue
        eligible.append((name, path, profile))
        if required_verification is None or _profile_satisfies_verification(profile, required_verification):
            return _make_backend(name, path, fmt, operation)
        reasons.append(f"{candidate}: verification max {profile.maximum_verification.value} < {required_verification.value}")
    if required_verification is not None and allow_verification_downgrade and eligible:
        name, path, _profile = eligible[0]
        return _make_backend(name, path, fmt, operation)
    if required_verification is not None and eligible:
        maxima = ", ".join(f"{name}={profile.maximum_verification.value}" for name, _path, profile in eligible)
        raise UnsupportedFormat(
            f"verification level {required_verification.value} is unavailable for {fmt.canonical}; installed compatible backend maximums: {maxima}; "
            "use --allow-verification-downgrade to accept a weaker proof"
        )
    suffix = f" ({'; '.join(reasons)})" if reasons else ""
    mode = " with fallback disabled" if no_fallback else ""
    verification_requirement = f",verification>={required_verification.value}" if required_verification is not None else ""
    raise BackendUnavailable(
        f"no installed backend can {operation} {fmt.canonical}{mode}; required={','.join(sorted(required))}{verification_requirement}{suffix}"
    )


def run_backend(
    cmd: list[str],
    meta: dict,
    reporter: ProgressReporter | None = None,
    sizes: dict[str, int] | None = None,
    *,
    show_command: bool = False,
    dry_run: bool = False,
    verbose: int = 0,
) -> int:
    import shlex
    from .execution import emit_command, record_backend

    stage = record_backend(cmd, meta)
    redact_values = [x for x in meta.get("redact", []) if x]
    display_cmd = list(cmd)
    for i, item in enumerate(display_cmd):
        for secret in redact_values:
            if secret in item:
                display_cmd[i] = item.replace(secret, "***")
    emit_command(stage, force=show_command or dry_run)
    if dry_run:
        return 0
    cleanup = meta.get("cleanup", [])
    output_tail: list[str] = []
    meta["output_tail"] = output_tail
    children: list[subprocess.Popen] = []
    stdout_file = meta.get("stdout_file")
    stdin_data = meta.get("stdin")
    forward_output = bool(meta.get("forward_output"))
    out_fh = None
    try:
        if meta.get("pipeline"):
            if not stdout_file:
                raise RuntimeError("pipeline backend requires stdout_file")
            Path(stdout_file).parent.mkdir(parents=True, exist_ok=True)
            out_fh = open(stdout_file, "wb")
            first = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=False)
            children.append(first)
            assert first.stdout is not None
            second = subprocess.Popen(meta["pipeline"], stdin=first.stdout, stdout=out_fh, stderr=subprocess.PIPE)
            children.append(second)
            first.stdout.close()
            first_err = first.stderr
            # tar verbose output is normally stderr when the archive itself is stdout.
            if first_err is not None:
                for raw in iter(first_err.readline, b""):
                    line = raw.decode(errors="replace").rstrip()
                    output_tail.append(line)
                    del output_tail[:-50]
                    if verbose:
                        console.print(line)
                    if reporter:
                        normalized = line.strip().replace("\\", "/")
                        for name, size in (sizes or {}).items():
                            if normalized.endswith(name) or name in normalized:
                                reporter.member_done(name, size)
                                break
            first_rc = first.wait()
            _, second_err = second.communicate()
            rc = first_rc or second.returncode
            if second_err:
                err_text = second_err.decode(errors="replace").rstrip()
                if err_text:
                    output_tail.extend(err_text.splitlines()[-50:])
                    del output_tail[:-50]
                    if rc != 0:
                        console.print(err_text)
            if reporter and rc == 0:
                reporter.complete()
            return rc

        if stdout_file:
            Path(stdout_file).parent.mkdir(parents=True, exist_ok=True)
            out_fh = open(stdout_file, "wb")
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE if stdin_data is not None else None, stdout=out_fh, stderr=subprocess.PIPE)
            children.append(proc)
            _, stderr = proc.communicate(stdin_data.encode() if stdin_data is not None else None)
            if stderr:
                err_text = stderr.decode(errors="replace").rstrip()
                if err_text:
                    output_tail.extend(err_text.splitlines()[-50:])
                    del output_tail[:-50]
                    if proc.returncode != 0:
                        console.print(err_text)
            if reporter and proc.returncode == 0:
                reporter.complete()
            return proc.returncode

        preproc = None
        if meta.get("preprocess"):
            preproc = subprocess.Popen(meta["preprocess"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            children.append(preproc)
            assert preproc.stdout is not None
            proc = subprocess.Popen(
                cmd,
                stdin=preproc.stdout,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            children.append(proc)
            preproc.stdout.close()
        else:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE if stdin_data is not None else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            children.append(proc)
        if preproc is None and stdin_data is not None and proc.stdin:
            proc.stdin.write(stdin_data)
            proc.stdin.close()
        assert proc.stdout is not None
        size_map = sizes or {}
        matched: set[str] = set()
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            output_tail.append(line)
            del output_tail[:-50]
            if forward_output:
                print(line)
            elif verbose:
                console.print(line)
            if reporter:
                reporter.observe(line)
                normalized = line.strip().replace("\\", "/")
                for name, size in size_map.items():
                    if name in matched:
                        continue
                    if normalized.endswith(name) or f" {name}" in normalized or name in normalized:
                        matched.add(name)
                        reporter.member_done(name, size)
                        break
        rc = proc.wait()
        if preproc is not None:
            pre_rc = preproc.wait()
            if pre_rc != 0:
                err = preproc.stderr.read().decode(errors="replace") if preproc.stderr else ""
                if err:
                    output_tail.extend(err.rstrip().splitlines()[-50:])
                    del output_tail[:-50]
                    console.print(err.rstrip())
                rc = rc or pre_rc
        if reporter and rc == 0:
            reporter.complete()
        return rc
    finally:
        for child in reversed(children):
            if child.poll() is None:
                try:
                    child.terminate()
                    child.wait(timeout=1)
                except Exception:
                    try:
                        child.kill()
                    except Exception:
                        pass
        if out_fh:
            out_fh.close()
        for p in cleanup:
            try:
                Path(p).unlink(missing_ok=True)
            except OSError:
                pass
