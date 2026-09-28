from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable


class VerificationLevel(str, Enum):
    NONE = "none"
    STRUCTURE = "structure"
    MEMBERS = "members"
    FULL = "full"

    @classmethod
    def parse(cls, value: str | VerificationLevel) -> VerificationLevel:
        if isinstance(value, cls):
            return value
        try:
            return cls(value)
        except ValueError as exc:
            raise ValueError(f"unsupported verification level: {value}") from exc


_VERIFICATION_ORDER = {
    VerificationLevel.NONE: 0,
    VerificationLevel.STRUCTURE: 1,
    VerificationLevel.MEMBERS: 2,
    VerificationLevel.FULL: 3,
}


def verification_rank(level: VerificationLevel | str) -> int:
    return _VERIFICATION_ORDER[VerificationLevel.parse(level)]


def strongest_level(levels: Iterable[VerificationLevel | str]) -> VerificationLevel:
    parsed = [VerificationLevel.parse(level) for level in levels]
    return max(parsed, key=verification_rank, default=VerificationLevel.NONE)


@dataclass(frozen=True, slots=True)
class BackendCapabilityProfile:
    """Typed Arc-normalized capability contract for one backend executable.

    The fields describe behavior Arc has normalized and is prepared to use, not
    every feature the native binary may expose through private passthrough flags.
    """

    binary: str
    operations: frozenset[str]
    stdin: bool = False
    stdout: bool = False
    encryption_read: bool = False
    encryption_write: bool = False
    solid: bool = False
    multipart: bool = False
    random_access: bool = False
    metadata: frozenset[str] = frozenset()
    mutation: frozenset[str] = frozenset()
    verification_levels: frozenset[VerificationLevel] = frozenset({VerificationLevel.NONE})
    remote_suitability: str = "staged"
    threads: bool = False
    safe_index: bool = False

    @property
    def maximum_verification(self) -> VerificationLevel:
        return strongest_level(self.verification_levels)

    def supports_verification(self, level: VerificationLevel | str) -> bool:
        requested = VerificationLevel.parse(level)
        return requested in self.verification_levels

    def legacy_capabilities(self) -> set[str]:
        values = set(self.operations)
        if self.encryption_read or self.encryption_write:
            values.add("password")
        if self.threads:
            values.add("threads")
        if self.safe_index:
            values.add("safe-index")
        if self.metadata:
            values.add("metadata")
        return values

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "binary": self.binary,
            "operations": sorted(self.operations),
            "streams": {"stdin": self.stdin, "stdout": self.stdout},
            "encryption": {"read": self.encryption_read, "write": self.encryption_write},
            "archive_features": {"solid": self.solid, "multipart": self.multipart},
            "metadata": sorted(self.metadata),
            "mutation": sorted(self.mutation),
            "verification": {
                "levels": [level.value for level in sorted(self.verification_levels, key=verification_rank)],
                "maximum": self.maximum_verification.value,
            },
            "random_access": self.random_access,
            "remote_suitability": self.remote_suitability,
            "threads": self.threads,
            "safe_index": self.safe_index,
        }


def _profile(
    binary: str,
    operations: str,
    *,
    stdin: bool = False,
    stdout: bool = False,
    encryption_read: bool = False,
    encryption_write: bool = False,
    solid: bool = False,
    multipart: bool = False,
    random_access: bool = False,
    metadata: str = "",
    verification: tuple[VerificationLevel, ...] = (VerificationLevel.NONE,),
    remote_suitability: str = "staged",
    threads: bool = False,
    safe_index: bool = False,
) -> BackendCapabilityProfile:
    ops = frozenset(operations.split())
    return BackendCapabilityProfile(
        binary=binary,
        operations=ops,
        stdin=stdin,
        stdout=stdout,
        encryption_read=encryption_read,
        encryption_write=encryption_write,
        solid=solid,
        multipart=multipart,
        random_access=random_access,
        metadata=frozenset(metadata.split()),
        mutation=frozenset(op for op in ops if op in {"create", "add", "update", "remove"}),
        verification_levels=frozenset(verification),
        remote_suitability=remote_suitability,
        threads=threads,
        safe_index=safe_index,
    )


V = VerificationLevel

# Arc-normalized backend contract. Runtime probes may refine a concrete field
# (notably GNU tar member removal) without mutating this immutable registry.
BACKEND_PROFILES: dict[str, BackendCapabilityProfile] = {
    "tar": _profile(
        "tar", "create list extract test add update", stdin=True, stdout=True,
        metadata="owner mode timestamps acls xattrs links", safe_index=True,
        verification=(V.NONE, V.STRUCTURE, V.MEMBERS), remote_suitability="streamable",
    ),
    "bsdtar": _profile(
        "bsdtar", "create list extract test add update", stdin=True, stdout=True,
        metadata="owner mode timestamps acls xattrs links", safe_index=True,
        verification=(V.NONE, V.STRUCTURE, V.MEMBERS), remote_suitability="streamable",
    ),
    "zip": _profile(
        "zip", "create add update remove", stdin=True, encryption_write=True,
        metadata="timestamps", random_access=True, remote_suitability="staged-random-access",
    ),
    "unzip": _profile(
        "unzip", "list extract test", stdout=True, encryption_read=True, random_access=True,
        metadata="timestamps", safe_index=True,
        verification=(V.NONE, V.STRUCTURE, V.MEMBERS, V.FULL), remote_suitability="staged-random-access",
    ),
    "7z": _profile(
        "7z", "create list extract test add update remove", stdin=True, stdout=True,
        encryption_read=True, encryption_write=True, solid=True, multipart=True, random_access=True,
        metadata="timestamps attributes links", safe_index=True, threads=True,
        verification=(V.NONE, V.STRUCTURE, V.MEMBERS, V.FULL), remote_suitability="staged-random-access",
    ),
    "7zz": _profile(
        "7zz", "create list extract test add update remove", stdin=True, stdout=True,
        encryption_read=True, encryption_write=True, solid=True, multipart=True, random_access=True,
        metadata="timestamps attributes links", safe_index=True, threads=True,
        verification=(V.NONE, V.STRUCTURE, V.MEMBERS, V.FULL), remote_suitability="staged-random-access",
    ),
    "rar": _profile(
        "rar", "create list extract test add update remove", stdout=True,
        encryption_read=True, encryption_write=True, solid=True, multipart=True, random_access=True,
        metadata="timestamps attributes links", safe_index=True,
        verification=(V.NONE, V.STRUCTURE, V.MEMBERS, V.FULL), remote_suitability="staged-random-access",
    ),
    "unrar": _profile(
        "unrar", "list extract test", stdout=True, encryption_read=True, random_access=True,
        metadata="timestamps attributes links", safe_index=True,
        verification=(V.NONE, V.STRUCTURE, V.MEMBERS, V.FULL), remote_suitability="staged-random-access",
    ),
    "gzip": _profile(
        "gzip", "create extract test", stdin=True, stdout=True,
        verification=(V.NONE, V.STRUCTURE, V.FULL), remote_suitability="streamable",
    ),
    "pigz": _profile(
        "pigz", "create extract test", stdin=True, stdout=True, threads=True,
        verification=(V.NONE, V.STRUCTURE, V.FULL), remote_suitability="streamable",
    ),
    "bzip2": _profile(
        "bzip2", "create extract test", stdin=True, stdout=True,
        verification=(V.NONE, V.STRUCTURE, V.FULL), remote_suitability="streamable",
    ),
    "pbzip2": _profile(
        "pbzip2", "create extract test", stdin=True, stdout=True, threads=True,
        verification=(V.NONE, V.STRUCTURE, V.FULL), remote_suitability="streamable",
    ),
    "xz": _profile(
        "xz", "create extract test", stdin=True, stdout=True, threads=True,
        verification=(V.NONE, V.STRUCTURE, V.FULL), remote_suitability="streamable",
    ),
    "pixz": _profile(
        "pixz", "create extract test", stdin=True, stdout=True, threads=True,
        verification=(V.NONE, V.STRUCTURE, V.FULL), remote_suitability="streamable",
    ),
    "zstd": _profile(
        "zstd", "create extract test", stdin=True, stdout=True, threads=True,
        verification=(V.NONE, V.STRUCTURE, V.FULL), remote_suitability="streamable",
    ),
    "pzstd": _profile(
        "pzstd", "create extract test", stdin=True, stdout=True, threads=True,
        verification=(V.NONE, V.STRUCTURE, V.FULL), remote_suitability="streamable",
    ),
}


def backend_profile(binary: str) -> BackendCapabilityProfile:
    from pathlib import Path

    name = Path(binary).name
    return BACKEND_PROFILES.get(name, BackendCapabilityProfile(name, frozenset()))
