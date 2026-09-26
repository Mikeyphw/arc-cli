from __future__ import annotations

import bz2
import gzip
import lzma
import shutil
import subprocess
import tarfile
from pathlib import Path

from .errors import UnsupportedFormat
from .model import ArchiveFormat

SUFFIXES: list[tuple[str, ArchiveFormat]] = [
    (".tar.zst", ArchiveFormat("tar", "zstd")),
    (".tar.zstd", ArchiveFormat("tar", "zstd")),
    (".tar.bz2", ArchiveFormat("tar", "bzip2")),
    (".tar.xz", ArchiveFormat("tar", "xz")),
    (".tar.gz", ArchiveFormat("tar", "gzip")),
    (".tbz2", ArchiveFormat("tar", "bzip2")),
    (".tbz", ArchiveFormat("tar", "bzip2")),
    (".tgz", ArchiveFormat("tar", "gzip")),
    (".txz", ArchiveFormat("tar", "xz")),
    (".tzst", ArchiveFormat("tar", "zstd")),
    (".zip", ArchiveFormat("zip")),
    (".7z", ArchiveFormat("7z")),
    (".rar", ArchiveFormat("rar")),
    (".tar", ArchiveFormat("tar")),
    (".gzip", ArchiveFormat(None, "gzip")),
    (".gz", ArchiveFormat(None, "gzip")),
    (".bz2", ArchiveFormat(None, "bzip2")),
    (".xz", ArchiveFormat(None, "xz")),
    (".zst", ArchiveFormat(None, "zstd")),
    (".zstd", ArchiveFormat(None, "zstd")),
]

ALIASES = {
    "tgz": "tar.gz",
    "tbz": "tar.bz2",
    "tbz2": "tar.bz2",
    "txz": "tar.xz",
    "tzst": "tar.zstd",
    "zst": "zstd",
}


def parse_format(value: str) -> ArchiveFormat:
    value = value.strip().lower().lstrip(".")
    value = ALIASES.get(value, value)
    if value.startswith("tar."):
        comp = value.split(".", 1)[1]
        comp = {"gz": "gzip", "bz2": "bzip2", "zst": "zstd"}.get(comp, comp)
        if comp not in {"gzip", "bzip2", "xz", "zstd"}:
            raise UnsupportedFormat(f"unsupported tar compression: {comp}")
        return ArchiveFormat("tar", comp)
    if value in {"tar", "zip", "7z", "rar"}:
        return ArchiveFormat(value)
    value = {"gz": "gzip", "bz2": "bzip2", "zst": "zstd"}.get(value, value)
    if value in {"gzip", "bzip2", "xz", "zstd"}:
        return ArchiveFormat(None, value)
    raise UnsupportedFormat(f"unsupported format: {value}")


def infer_from_name(path: str | Path) -> ArchiveFormat | None:
    lower = str(path).lower()
    for suffix, fmt in SUFFIXES:
        if lower.endswith(suffix):
            return fmt
    return None


def extension_for(fmt: ArchiveFormat) -> str:
    mapping = {
        "tar": ".tar",
        "tar.gzip": ".tar.gz",
        "tar.bzip2": ".tar.bz2",
        "tar.xz": ".tar.xz",
        "tar.zstd": ".tar.zst",
        "zip": ".zip",
        "7z": ".7z",
        "rar": ".rar",
        "gzip": ".gz",
        "bzip2": ".bz2",
        "xz": ".xz",
        "zstd": ".zst",
    }
    return mapping[fmt.canonical]


def _looks_like_tar(block: bytes) -> bool:
    return len(block) >= 512 and block[257:262] == b"ustar"


def _probe_nested_tar(path: Path, compression: str) -> bool:
    def probe_stream(fh) -> bool:
        try:
            with tarfile.open(fileobj=fh, mode="r|") as tf:
                # An arbitrary stream of zero bytes is indistinguishable from
                # an empty TAR end marker.  Require a real member for content-
                # based nested-TAR promotion; an explicit .tar.* filename hint
                # below handles genuinely empty compressed TAR archives.
                return tf.next() is not None
        except (tarfile.TarError, OSError, EOFError):
            return False

    try:
        if compression == "gzip":
            with gzip.open(path, "rb") as fh:
                return probe_stream(fh)
        if compression == "bzip2":
            with bz2.open(path, "rb") as fh:
                return probe_stream(fh)
        if compression == "xz":
            with lzma.open(path, "rb") as fh:
                return probe_stream(fh)
        if compression == "zstd":
            exe = shutil.which("zstd") or shutil.which("pzstd")
            if not exe:
                return False
            proc = subprocess.Popen([exe, "-q", "-dc", "--", str(path)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            assert proc.stdout is not None
            try:
                return probe_stream(proc.stdout)
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
    except (OSError, EOFError, subprocess.SubprocessError):
        return False
    return False


def _has_empty_tar_marker(path: Path, compression: str) -> bool:
    """Return whether decompressed data starts with TAR's two zero end blocks.

    This is intentionally only a tie-breaker for a `.tar.*` filename.  It does
    not by itself prove TAR because an arbitrary zero-filled stream is byte-for-
    byte ambiguous with an empty TAR prefix.
    """
    def is_marker(data: bytes) -> bool:
        return len(data) >= 1024 and data[:1024] == b"\0" * 1024

    try:
        if compression == "gzip":
            with gzip.open(path, "rb") as fh:
                return is_marker(fh.read(1024))
        if compression == "bzip2":
            with bz2.open(path, "rb") as fh:
                return is_marker(fh.read(1024))
        if compression == "xz":
            with lzma.open(path, "rb") as fh:
                return is_marker(fh.read(1024))
        if compression == "zstd":
            exe = shutil.which("zstd") or shutil.which("pzstd")
            if not exe:
                return False
            proc = subprocess.Popen([exe, "-q", "-dc", "--", str(path)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            assert proc.stdout is not None
            try:
                data = proc.stdout.read(1024)
                return is_marker(data)
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
    except (OSError, EOFError, subprocess.SubprocessError):
        return False
    return False


def detect(path: str | Path, explicit: str | None = None) -> ArchiveFormat:
    if explicit:
        return parse_format(explicit)
    p = Path(path)
    if not p.is_file():
        raise UnsupportedFormat(f"archive does not exist: {p}")
    try:
        with p.open("rb") as fh:
            head = fh.read(16)
    except OSError as exc:
        raise UnsupportedFormat(str(exc)) from exc

    if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06") or head.startswith(b"PK\x07\x08"):
        return ArchiveFormat("zip")
    if head.startswith(b"7z\xbc\xaf\x27\x1c"):
        return ArchiveFormat("7z")
    if head.startswith(b"Rar!\x1a\x07"):
        return ArchiveFormat("rar")
    hint = infer_from_name(p)

    def compressed_kind(compression: str) -> ArchiveFormat:
        # Content wins when there is strong evidence of a non-empty TAR.  For
        # the inherently ambiguous empty-TAR/all-zero case, the filename hint
        # breaks the tie.  In particular, a wrapper-created foo.gz remains a
        # gzip stream even if its uncompressed bytes happen to be all zero.
        if _probe_nested_tar(p, compression):
            return ArchiveFormat("tar", compression)
        if (
            hint
            and hint.container == "tar"
            and hint.compression == compression
            and _has_empty_tar_marker(p, compression)
        ):
            return ArchiveFormat("tar", compression)
        return ArchiveFormat(None, compression)

    if head.startswith(b"\x1f\x8b"):
        return compressed_kind("gzip")
    if head.startswith(b"BZh"):
        return compressed_kind("bzip2")
    if head.startswith(b"\xfd7zXZ\x00"):
        return compressed_kind("xz")
    zstd_magic = head.startswith(b"\x28\xb5\x2f\xfd")
    if len(head) >= 4:
        magic_le = int.from_bytes(head[:4], "little")
        zstd_magic = zstd_magic or ((magic_le & 0xFFFFFFF0) == 0x184D2A50)
    if zstd_magic:
        return compressed_kind("zstd")
    try:
        if tarfile.is_tarfile(p):
            return ArchiveFormat("tar")
    except OSError:
        pass
    if hint:
        return hint
    raise UnsupportedFormat(f"could not identify archive format: {p}")


def resolve_create_format(path: str | Path, explicit: str | None, add_extension: bool = False) -> tuple[ArchiveFormat, Path]:
    fmt = parse_format(explicit) if explicit else infer_from_name(path)
    if not fmt:
        raise UnsupportedFormat(f"cannot infer archive format from {path!s}; use --format")
    out = Path(path)
    if explicit and add_extension and infer_from_name(out) is None:
        out = Path(str(out) + extension_for(fmt))
    return fmt, out
