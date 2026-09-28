from __future__ import annotations

import hashlib
import json
import os
import stat
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from .model import Member

FINGERPRINT_SCHEMA = "arc.logical-fingerprint/v1"
FINGERPRINT_NORMALIZATION = "arc-logical-members-v1"
DIFF_SCHEMA = "arc.archive-diff/v1"


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_member_name(name: str) -> str:
    raw = str(name).replace("\\", "/")
    parts: list[str] = []
    for part in PurePosixPath(raw).parts:
        if part in {"", ".", "/"}:
            continue
        if part == "..":
            # Safety validation rejects paths that escape the archive root. For
            # paths that remain inside it, collapse filesystem-equivalent parent
            # traversal before hashing so e.g. ``dir/../a`` and ``a`` cannot
            # acquire two logical identities while materializing to one path.
            if parts:
                parts.pop()
            else:
                parts.append(part)
            continue
        parts.append(part)
    normalized = "/".join(parts)
    return unicodedata.normalize("NFC", normalized or "@root")


def _normalized_materialized_name(name: str) -> str:
    """Normalize an extracted filesystem spelling without Unicode folding.

    Logical member identity is NFC-normalized, but an extraction backend on a
    case-sensitive filesystem may materialize the archive's original Unicode
    spelling verbatim.  Looking up the extracted file by the logical NFC name
    therefore fails for decomposed names even though extraction succeeded.

    This helper collapses the same safe ``.``/``..`` and separator spelling as
    logical identity while preserving the on-disk Unicode codepoints.  The
    resulting extracted-tree map is subsequently keyed by
    :func:`normalize_member_name`, so comparisons remain format-independent.
    """

    raw = str(name).replace("\\", "/")
    parts: list[str] = []
    for part in PurePosixPath(raw).parts:
        if part in {"", ".", "/"}:
            continue
        if part == "..":
            if parts:
                parts.pop()
            else:
                parts.append(part)
            continue
        parts.append(part)
    return "/".join(parts) or "@root"


def validate_logical_member_set(members: Iterable[Member]) -> None:
    """Fail before extraction when multiple archive entries collapse to one logical path.

    Archive backends may preserve syntactically distinct spellings such as ``./a``
    and ``a`` or decomposed/composed Unicode.  The logical fingerprint model
    intentionally normalizes those spellings, so allowing both through extraction
    would make the materialized bytes ambiguous before provenance checks run.
    """
    seen: dict[str, str] = {}
    for member in members:
        normalized = normalize_member_name(member.name)
        previous = seen.get(normalized)
        if previous is not None:
            raise ValueError(
                f"archive members collide after logical path normalization: {previous!r} and {member.name!r} -> {normalized!r}"
            )
        seen[normalized] = member.name


def normalize_link_target(value: str | None) -> str | None:
    if value is None:
        return None
    raw = str(value).replace("\\", "/")
    # Preserve relative link semantics (including ..) while normalizing separators
    # and redundant ./ segments. Safety validation happens before this layer.
    parts: list[str] = []
    for part in PurePosixPath(raw).parts:
        if part in {"", ".", "/"}:
            continue
        parts.append(part)
    return unicodedata.normalize("NFC", "/".join(parts) or ".")


def normalize_mtime(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    candidates = [text]
    if text.endswith("Z"):
        candidates.append(text[:-1] + "+00:00")
    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed.replace(microsecond=0).isoformat(sep="T", timespec="seconds")
    # Native backends may expose locale-independent but non-ISO timestamps.
    # Keep them as explicit metadata rather than pretending they are normalized.
    return text


def materialized_member_map(root: Path) -> dict[str, Path]:
    """Index extracted paths by canonical logical member name.

    Fingerprinting hashes the bytes that were actually materialized by Arc's
    safe extraction path, not a guessed path reconstructed from the logical
    NFC spelling.  This matters for decomposed Unicode names, backslash
    spellings produced by some writers, and safe parent traversal that resolves
    to the same destination path.

    Any two extracted paths that collapse to one logical identity fail closed;
    otherwise Arc could hash an arbitrary one of two ambiguous objects.
    """

    out: dict[str, Path] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(dirpath)
        for name in [*dirnames, *filenames]:
            actual = base / name
            rel = actual.relative_to(root).as_posix()
            # Preserve the actual extracted spelling for filesystem access, but
            # key the map by the same NFC logical normalization used in digests.
            logical = normalize_member_name(_normalized_materialized_name(rel))
            previous = out.get(logical)
            if previous is not None and previous != actual:
                raise ValueError(
                    "extracted archive paths collide after logical path normalization: "
                    f"{str(previous.relative_to(root))!r} and {rel!r} -> {logical!r}"
                )
            out[logical] = actual
    return out


@dataclass(frozen=True, slots=True)
class LogicalMemberRecord:
    path: str
    kind: str
    size: int
    sha256: str | None = None
    link_target: str | None = None
    mtime: str | None = None

    def content_identity(self) -> dict[str, object]:
        return {
            "path": self.path,
            "kind": self.kind,
            "size": self.size,
            "sha256": self.sha256,
            "link_target": self.link_target,
        }

    def metadata_identity(self) -> dict[str, object]:
        return {**self.content_identity(), "mtime": self.mtime}

    def to_dict(self) -> dict[str, object]:
        return self.metadata_identity()


def member_record(
    member: Member,
    extracted_root: Path,
    *,
    stream_path: Path | None = None,
    materialized: dict[str, Path] | None = None,
) -> LogicalMemberRecord:
    path = "@stream" if stream_path is not None else normalize_member_name(member.name)
    kind = "file" if stream_path is not None else str(member.kind)
    if stream_path is not None:
        source = stream_path
    else:
        materialized = materialized if materialized is not None else materialized_member_map(extracted_root)
        try:
            source = materialized[path]
        except KeyError as exc:
            raise FileNotFoundError(f"extracted archive member is missing: {path}") from exc
    checksum: str | None = None
    size = 0
    if kind == "file":
        try:
            mode = source.lstat().st_mode
        except OSError as exc:
            raise FileNotFoundError(f"extracted archive member is missing: {path}") from exc
        if not stat.S_ISREG(mode):
            raise ValueError(f"extracted archive member is not a regular file: {path}")
        actual_size = source.stat().st_size
        # Use materialized bytes as the authority. Some formats/backend listings
        # can expose approximate/32-bit size metadata, while the content hash is exact.
        size = actual_size
        checksum = sha256_file(source)
    elif kind == "dir":
        try:
            mode = source.lstat().st_mode
        except OSError as exc:
            raise FileNotFoundError(f"extracted archive member is missing: {path}") from exc
        if not stat.S_ISDIR(mode):
            raise ValueError(f"extracted archive member is not a directory: {path}")
    elif kind == "symlink":
        try:
            mode = source.lstat().st_mode
        except OSError as exc:
            raise FileNotFoundError(f"extracted archive member is missing: {path}") from exc
        if not stat.S_ISLNK(mode):
            raise ValueError(f"extracted archive member is not a symbolic link: {path}")
    elif kind == "hardlink":
        try:
            mode = source.lstat().st_mode
        except OSError as exc:
            raise FileNotFoundError(f"extracted archive member is missing: {path}") from exc
        if not stat.S_ISREG(mode):
            raise ValueError(f"extracted hard-link member did not materialize as a regular file: {path}")
    return LogicalMemberRecord(
        path=path,
        kind=kind,
        size=size,
        sha256=checksum,
        link_target=normalize_link_target(member.link_target),
        mtime=normalize_mtime(member.mtime),
    )


def build_fingerprint(
    archive_path: Path,
    *,
    format_name: str,
    backend: str | None,
    records: Iterable[LogicalMemberRecord],
    source_label: str | None = None,
    include_members: bool = True,
) -> dict[str, object]:
    materialized = list(records)
    # Non-empty directory entries are representation details: some writers emit
    # them explicitly while others rely on child paths. Keep empty directories
    # because their existence is logical content, but omit implied parents so
    # equivalent ZIP/TAR/7z archives do not differ only on directory records.
    # Every descendant implies all of its parent directories, including another
    # explicit directory entry.  Keep only leaf/meaningful empty directories;
    # this makes ``a/`` + ``a/b/`` equivalent to an archive that stores only
    # ``a/b/`` while still preserving the logical existence of empty ``a/b``.
    all_paths = [item.path for item in materialized]
    ordered = sorted(
        (
            item
            for item in materialized
            if item.kind != "dir"
            or not any(
                path != item.path and path.startswith(item.path.rstrip("/") + "/")
                for path in all_paths
            )
        ),
        key=lambda item: (item.path, item.kind, item.link_target or ""),
    )
    seen: set[str] = set()
    for item in ordered:
        if item.path in seen:
            raise ValueError(f"duplicate normalized archive member path: {item.path}")
        seen.add(item.path)
    content_rows = [item.content_identity() for item in ordered]
    metadata_rows = [item.metadata_identity() for item in ordered]
    content_digest = _sha256_bytes(_canonical_bytes(content_rows))
    metadata_digest = _sha256_bytes(_canonical_bytes(metadata_rows))
    files = [item for item in ordered if item.kind == "file"]
    archive_bytes = archive_path.stat().st_size
    payload: dict[str, object] = {
        "schema": FINGERPRINT_SCHEMA,
        "schema_version": 1,
        "algorithm": "sha256",
        "normalization": FINGERPRINT_NORMALIZATION,
        "digest": content_digest,
        "metadata_digest": metadata_digest,
        "complete": all(item.sha256 is not None for item in files),
        "member_count": len(ordered),
        "file_count": len(files),
        "total_file_bytes": sum(item.size for item in files),
        "archive": {
            "path": source_label or str(archive_path),
            "format": format_name,
            "backend": backend,
            "bytes": archive_bytes,
            "byte_sha256": sha256_file(archive_path),
        },
    }
    if include_members:
        payload["members"] = [item.to_dict() for item in ordered]
    return payload


def fingerprint_summary(value: dict[str, object]) -> dict[str, object]:
    return {key: value[key] for key in (
        "schema", "schema_version", "algorithm", "normalization", "digest", "metadata_digest",
        "complete", "member_count", "file_count", "total_file_bytes", "archive",
    ) if key in value}


def _member_map(fingerprint: dict[str, object]) -> dict[str, dict[str, object]]:
    rows = fingerprint.get("members")
    if not isinstance(rows, list):
        raise ValueError("logical fingerprint does not include member evidence")
    out: dict[str, dict[str, object]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("logical fingerprint member evidence is malformed")
        path = str(row.get("path") or "")
        if not path:
            raise ValueError("logical fingerprint member has no path")
        if path in out:
            raise ValueError(f"logical fingerprint contains duplicate normalized member path: {path}")
        out[path] = row
    return out


def _content_fields(row: dict[str, object]) -> dict[str, object]:
    return {key: row.get(key) for key in ("kind", "size", "sha256", "link_target")}


def compare_fingerprints(left: dict[str, object], right: dict[str, object]) -> dict[str, object]:
    left_rows = _member_map(left)
    right_rows = _member_map(right)
    left_paths = set(left_rows)
    right_paths = set(right_rows)

    added = [right_rows[path] for path in sorted(right_paths - left_paths)]
    removed = [left_rows[path] for path in sorted(left_paths - right_paths)]
    type_changed: list[dict[str, object]] = []
    content_changed: list[dict[str, object]] = []
    metadata_changed: list[dict[str, object]] = []

    for path in sorted(left_paths & right_paths):
        lrow = left_rows[path]
        rrow = right_rows[path]
        if lrow.get("kind") != rrow.get("kind"):
            type_changed.append({"path": path, "left": lrow, "right": rrow})
            continue
        if _content_fields(lrow) != _content_fields(rrow):
            content_changed.append({"path": path, "left": lrow, "right": rrow})
            continue
        if lrow.get("mtime") != rrow.get("mtime"):
            metadata_changed.append({
                "path": path,
                "left": {"mtime": lrow.get("mtime")},
                "right": {"mtime": rrow.get("mtime")},
            })

    logical_equivalent = not (added or removed or type_changed or content_changed)
    metadata_equivalent = logical_equivalent and not metadata_changed
    left_archive = left.get("archive") if isinstance(left.get("archive"), dict) else {}
    right_archive = right.get("archive") if isinstance(right.get("archive"), dict) else {}
    format_changed = left_archive.get("format") != right_archive.get("format")
    byte_identical = (
        left_archive.get("byte_sha256") == right_archive.get("byte_sha256")
        and left_archive.get("bytes") == right_archive.get("bytes")
    )
    return {
        "schema": DIFF_SCHEMA,
        "schema_version": 1,
        "left": fingerprint_summary(left),
        "right": fingerprint_summary(right),
        "equivalence": {
            "logical": logical_equivalent,
            "metadata": metadata_equivalent,
            "byte_identical": byte_identical,
            "format_changed": format_changed,
            "encoding_only": logical_equivalent and metadata_equivalent and not byte_identical,
        },
        "changes": {
            "added": added,
            "removed": removed,
            "type_changed": type_changed,
            "content_changed": content_changed,
            "metadata_changed": metadata_changed,
            "counts": {
                "added": len(added),
                "removed": len(removed),
                "type_changed": len(type_changed),
                "content_changed": len(content_changed),
                "metadata_changed": len(metadata_changed),
            },
        },
    }
