from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .errors import CorruptArchive, UsageError

SPLIT_MANIFEST_SCHEMA_ID = "arc.split-manifest/v1"
SPLIT_MANIFEST_SCHEMA_VERSION = 1
DEFAULT_CHECKSUM = "sha256"
CHECKSUMS = ("sha256", "sha512", "blake2b")
_PART_RE = re.compile(r"^(?P<prefix>.+)\.part(?P<index>\d+)$")
_SIZE_RE = re.compile(r"^\s*(\d+)\s*([kmgt]?i?b?)?\s*$", re.IGNORECASE)
_SIZE_MULTIPLIERS = {
    "": 1,
    "b": 1,
    "k": 1000,
    "kb": 1000,
    "ki": 1024,
    "kib": 1024,
    "m": 1000**2,
    "mb": 1000**2,
    "mi": 1024**2,
    "mib": 1024**2,
    "g": 1000**3,
    "gb": 1000**3,
    "gi": 1024**3,
    "gib": 1024**3,
    "t": 1000**4,
    "tb": 1000**4,
    "ti": 1024**4,
    "tib": 1024**4,
}


@dataclass(frozen=True, slots=True)
class SplitPart:
    index: int
    name: str
    size: int
    checksum: str


@dataclass(frozen=True, slots=True)
class SplitManifest:
    path: Path
    original_name: str
    byte_length: int
    checksum_algorithm: str
    checksum: str
    parts: tuple[SplitPart, ...]
    split_by: str
    split_value: int
    digits: int


def parse_size(value: str) -> int:
    match = _SIZE_RE.fullmatch(str(value))
    if not match:
        raise UsageError(f"invalid split size: {value!r}; use bytes or units such as 100M, 64MiB, or 2G")
    amount = int(match.group(1))
    suffix = (match.group(2) or "").lower()
    try:
        multiplier = _SIZE_MULTIPLIERS[suffix]
    except KeyError as exc:
        raise UsageError(f"unsupported split size suffix: {suffix}") from exc
    size = amount * multiplier
    if size <= 0:
        raise UsageError("split size must be greater than zero")
    return size


def validate_checksum_algorithm(value: str) -> str:
    algorithm = str(value).lower()
    if algorithm not in CHECKSUMS:
        raise UsageError(f"unsupported checksum algorithm: {value}; choose one of {', '.join(CHECKSUMS)}")
    return algorithm


def new_hasher(algorithm: str):
    algorithm = validate_checksum_algorithm(algorithm)
    return hashlib.new(algorithm)


def checksum_file(path: Path, algorithm: str, *, chunk_size: int = 1024 * 1024) -> str:
    digest = new_hasher(algorithm)
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def checksum_paths(paths: Iterable[Path], algorithm: str, *, chunk_size: int = 1024 * 1024) -> str:
    digest = new_hasher(algorithm)
    for path in paths:
        with path.open("rb") as fh:
            while True:
                block = fh.read(chunk_size)
                if not block:
                    break
                digest.update(block)
    return digest.hexdigest()


def planned_part_sizes(byte_length: int, *, size: int | None = None, parts: int | None = None) -> list[int]:
    if byte_length < 0:
        raise UsageError("source size cannot be negative")
    if (size is None) == (parts is None):
        raise UsageError("choose exactly one of --size or --parts")
    if size is not None:
        if size <= 0:
            raise UsageError("split size must be greater than zero")
        if byte_length == 0:
            return [0]
        count = math.ceil(byte_length / size)
        return [size] * (count - 1) + [byte_length - (size * (count - 1))]
    assert parts is not None
    if parts <= 0:
        raise UsageError("--parts must be greater than zero")
    if byte_length == 0:
        if parts != 1:
            raise UsageError("an empty source can only be split into one empty part")
        return [0]
    if parts > byte_length:
        raise UsageError("--parts cannot exceed the source byte length")
    base, remainder = divmod(byte_length, parts)
    return [base + (1 if index < remainder else 0) for index in range(parts)]


def validate_digits(digits: int, part_count: int) -> int:
    if digits < 1:
        raise UsageError("--digits must be at least 1")
    needed = len(str(max(part_count, 1)))
    if digits < needed:
        raise UsageError(f"--digits={digits} is too small for {part_count} parts; use at least {needed}")
    return digits


def part_name(prefix: str, index: int, digits: int) -> str:
    if not prefix or Path(prefix).name != prefix or prefix in {".", ".."}:
        raise UsageError("split output prefix must be a filename prefix, not a path")
    return f"{prefix}.part{index:0{digits}d}"


def default_manifest_name(prefix: str) -> str:
    if not prefix or Path(prefix).name != prefix:
        raise UsageError("split output prefix must be a filename prefix")
    return f"{prefix}.arc-split.json"


def build_manifest_payload(
    *,
    original_name: str,
    byte_length: int,
    algorithm: str,
    checksum: str,
    parts: Iterable[SplitPart],
    split_by: str,
    split_value: int,
    digits: int,
) -> dict[str, object]:
    algorithm = validate_checksum_algorithm(algorithm)
    rows = list(parts)
    return {
        "schema": SPLIT_MANIFEST_SCHEMA_ID,
        "schema_version": SPLIT_MANIFEST_SCHEMA_VERSION,
        "original_name": original_name,
        "byte_length": byte_length,
        "checksum": {"algorithm": algorithm, "value": checksum},
        "split": {"by": split_by, "value": split_value, "digits": digits},
        "parts": [
            {
                "index": part.index,
                "name": part.name,
                "size": part.size,
                "checksum": {"algorithm": algorithm, "value": part.checksum},
            }
            for part in rows
        ],
    }


def _require_int(value: object, *, field: str, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise CorruptArchive(f"invalid split manifest field {field!r}")
    return value


def _require_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise CorruptArchive(f"invalid split manifest field {field!r}")
    return value


def load_split_manifest(path: Path) -> SplitManifest:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise UsageError(f"cannot read split manifest {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise CorruptArchive(f"invalid split manifest JSON: {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise CorruptArchive("split manifest root must be an object")
    if raw.get("schema") != SPLIT_MANIFEST_SCHEMA_ID or raw.get("schema_version") != SPLIT_MANIFEST_SCHEMA_VERSION:
        raise CorruptArchive(
            f"unsupported split manifest schema: {raw.get('schema')!r} version {raw.get('schema_version')!r}"
        )
    original_name = _require_text(raw.get("original_name"), field="original_name")
    if Path(original_name).name != original_name:
        raise CorruptArchive("split manifest original_name must be a basename")
    byte_length = _require_int(raw.get("byte_length"), field="byte_length")
    checksum_obj = raw.get("checksum")
    if not isinstance(checksum_obj, dict):
        raise CorruptArchive("invalid split manifest checksum")
    algorithm = validate_checksum_algorithm(_require_text(checksum_obj.get("algorithm"), field="checksum.algorithm"))
    whole_checksum = _require_text(checksum_obj.get("value"), field="checksum.value")
    split_obj = raw.get("split")
    if not isinstance(split_obj, dict):
        raise CorruptArchive("invalid split manifest split parameters")
    split_by = _require_text(split_obj.get("by"), field="split.by")
    if split_by not in {"size", "parts"}:
        raise CorruptArchive("split manifest split.by must be 'size' or 'parts'")
    split_value = _require_int(split_obj.get("value"), field="split.value", minimum=1)
    digits = _require_int(split_obj.get("digits"), field="split.digits", minimum=1)
    rows = raw.get("parts")
    if not isinstance(rows, list) or not rows:
        raise CorruptArchive("split manifest must contain at least one part")
    parts: list[SplitPart] = []
    total = 0
    for expected, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise CorruptArchive("split manifest part entry must be an object")
        index = _require_int(row.get("index"), field="parts.index", minimum=1)
        if index != expected:
            raise CorruptArchive(f"split manifest parts are not contiguous/in order at index {expected}")
        name = _require_text(row.get("name"), field="parts.name")
        if Path(name).name != name:
            raise CorruptArchive(f"unsafe split part name in manifest: {name!r}")
        match = _PART_RE.fullmatch(name)
        if match is None or int(match.group("index")) != index:
            raise CorruptArchive(f"split manifest part name/index mismatch: {name!r}")
        size = _require_int(row.get("size"), field="parts.size")
        part_checksum_obj = row.get("checksum")
        if not isinstance(part_checksum_obj, dict):
            raise CorruptArchive(f"invalid checksum for split part {name}")
        part_algorithm = validate_checksum_algorithm(
            _require_text(part_checksum_obj.get("algorithm"), field="parts.checksum.algorithm")
        )
        if part_algorithm != algorithm:
            raise CorruptArchive(f"split part {name} uses checksum algorithm {part_algorithm}, expected {algorithm}")
        part_checksum = _require_text(part_checksum_obj.get("value"), field="parts.checksum.value")
        parts.append(SplitPart(index=index, name=name, size=size, checksum=part_checksum))
        total += size
    if total != byte_length:
        raise CorruptArchive(f"split manifest part sizes total {total} bytes, expected {byte_length}")
    return SplitManifest(
        path=path,
        original_name=original_name,
        byte_length=byte_length,
        checksum_algorithm=algorithm,
        checksum=whole_checksum,
        parts=tuple(parts),
        split_by=split_by,
        split_value=split_value,
        digits=digits,
    )


def discover_manifest_from_part(part: Path) -> Path | None:
    match = _PART_RE.fullmatch(part.name)
    if match is None:
        return None
    candidate = part.with_name(default_manifest_name(match.group("prefix")))
    return candidate if candidate.is_file() else None


def discover_parts_without_manifest(part: Path) -> list[Path]:
    match = _PART_RE.fullmatch(part.name)
    if match is None:
        raise UsageError("join input is not an ARC part name (*.partNNN) and no manifest was provided")
    prefix = match.group("prefix")
    rows: list[tuple[int, Path]] = []
    for candidate in part.parent.glob(prefix + ".part*"):
        parsed = _PART_RE.fullmatch(candidate.name)
        if parsed is None or parsed.group("prefix") != prefix or not candidate.is_file():
            continue
        rows.append((int(parsed.group("index")), candidate))
    rows.sort(key=lambda item: item[0])
    if not rows or rows[0][0] != 1:
        raise CorruptArchive("split parts are incomplete: part 1 is missing")
    for expected, (index, _candidate) in enumerate(rows, 1):
        if index != expected:
            raise CorruptArchive(f"split parts are incomplete or reordered: expected part {expected}, found {index}")
    return [path for _index, path in rows]


def output_name_from_part(part: Path) -> str:
    match = _PART_RE.fullmatch(part.name)
    if match is None:
        raise UsageError("cannot infer join output name without a manifest")
    return match.group("prefix")
